#!/usr/bin/env python3
"""Create/resume a standard single-node kind cluster, then attach the base via Flux."""
import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import config


def run(*args, input=None, capture=False):
    result = subprocess.run([str(a) for a in args], cwd=ROOT, input=input,
                            text=True, check=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else ""


def apply(kube, obj):
    run(*kube, "apply", "-f", "-", input=json.dumps(obj))


def get(kube, *args):
    text = run(*kube, "get", *args, "-o", "json", capture=True)
    return json.loads(text) if text.strip() else None


def wait(kube, *resources, timeout="15m", namespace="flux-system"):
    # Creation and reconciliation are asynchronous; kubectl wait alone races
    # NotFound and can accept a Ready condition from an older generation.
    deadline = time.monotonic() + int(timeout.removesuffix("m")) * 60
    latest = {}
    print("Waiting for " + ", ".join(resources), flush=True)
    while time.monotonic() < deadline:
        ready = True
        for resource in resources:
            obj = get(kube, resource, "-n", namespace, "--ignore-not-found")
            latest[resource] = [c.get("message", "") for c in (obj or {}).get("status", {}).get("conditions", [])
                                if c.get("type") == "Ready"]
            ready = ready and bool(obj and any(
                c.get("type") == "Ready" and c.get("status") == "True" and
                c.get("observedGeneration") == obj["metadata"]["generation"]
                for c in obj.get("status", {}).get("conditions", [])))
        if ready:
            return
        time.sleep(5)
    raise RuntimeError("Timed out waiting for " + ", ".join(resources) + ": " + json.dumps(latest))


def bootstrap_dns(kube, c):
    # kind's initial resolver may contain a Docker-only loopback nameserver.
    # Configure upstreams before Flux must resolve GitHub to fetch this repo.
    obj = json.loads((ROOT / "profiles/kind/dns/coredns.yaml").read_text())
    for key, value in {"DOMAIN": c["domain"], "LAN_DNS_SERVICE_IP": c["lan_dns_service_ip"],
                       "UPSTREAM_DNS": " ".join(c["upstream_dns"])}.items():
        obj["data"]["Corefile"] = obj["data"]["Corefile"].replace("${" + key + "}", value)
    apply(kube, obj)
    run(*kube, "-n", "kube-system", "rollout", "restart", "deployment/coredns")
    run(*kube, "-n", "kube-system", "rollout", "status", "deployment/coredns", "--timeout=300s")
    name = "base-dns-" + uuid.uuid4().hex[:8]
    apply(kube, {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": "kube-system"},
        "spec": {"backoffLimit": 1, "activeDeadlineSeconds": 120, "ttlSecondsAfterFinished": 300,
                 "template": {"spec": {"restartPolicy": "Never", "containers": [{"name": "dns",
                 "image": "busybox:1.37.0", "command": ["nslookup", "github.com"]}]}}}})
    try:
        run(*kube, "-n", "kube-system", "wait", "job/" + name, "--for=condition=Complete", "--timeout=150s")
    finally:
        subprocess.run([*kube, "-n", "kube-system", "logs", "job/" + name], check=False)
        subprocess.run([*kube, "-n", "kube-system", "delete", "job/" + name, "--wait=false"], check=False)


def initialize_ca(kube, state, cluster_name):
    for name in ("cert-manager", "gateway-system"):
        apply(kube, {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name}})
    existing = get(kube, "secret/internal-ca", "-n", "cert-manager", "--ignore-not-found")
    cert, key = state / "ca.crt", state / "ca.key"
    with tempfile.TemporaryDirectory(dir=state) as tmp:
        crt, private = Path(tmp) / "ca.crt", Path(tmp) / "ca.key"
        if existing:
            for path, field in ((crt, "tls.crt"), (private, "tls.key")):
                path.write_bytes(base64.b64decode(existing["data"][field], validate=True))
        elif cert.exists() and key.exists():
            shutil.copy2(cert, crt)
            shutil.copy2(key, private)
        elif cert.exists() or key.exists():
            raise RuntimeError("Incomplete local CA: restore both ca.crt and ca.key.")
        else:
            run("openssl", "req", "-x509", "-newkey", "rsa:4096", "-sha256", "-nodes",
                "-days", "3650", "-subj", f"/CN={cluster_name} internal root CA",
                "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-keyout", private, "-out", crt, capture=True)
        run("openssl", "x509", "-in", crt, "-checkend", "0", "-noout", capture=True)
        if run("openssl", "x509", "-in", crt, "-pubkey", "-noout", capture=True) != run(
                "openssl", "pkey", "-in", private, "-pubout", capture=True):
            raise RuntimeError("CA key/certificate mismatch; backups preserved.")
        if not existing:
            run(*kube, "-n", "cert-manager", "create", "secret", "tls", "internal-ca",
                f"--cert={crt}", f"--key={private}")
        for source, target in ((crt, cert), (private, key)):
            os.chmod(source, 0o600)
            os.replace(source, target)


def cluster_config(c, settings, state):
    return {"kind": "Cluster", "apiVersion": "kind.x-k8s.io/v1alpha4",
        "networking": {"podSubnet": c["pod_cidr"], "serviceSubnet": c["service_cidr"],
                       "apiServerAddress": "127.0.0.1"},
        "nodes": [{"role": "control-plane", "labels": {
            "elektro.internal/role": "hybrid", "elektro.internal/edge": "true",
            "elektro.internal/profile": "kind"},
            "extraPortMappings": [
                {"containerPort": port, "hostPort": host, "listenAddress": "127.0.0.1", "protocol": proto}
                for port, host, proto in [(80, settings["http_port"], "TCP"),
                    (443, settings["https_port"], "TCP"), (53, settings["dns_port"], "UDP"),
                    (53, settings["dns_port"], "TCP")]],
            "extraMounts": [{"hostPath": str(state / "data"),
                             "containerPath": "/var/local-path-provisioner"}]}]}


def check_local_storage(kube):
    standard = get(kube, "storageclass/standard")
    expected = {"provisioner": "rancher.io/local-path", "volumeBindingMode": "WaitForFirstConsumer",
                "reclaimPolicy": "Delete", "parameters": {}, "allowVolumeExpansion": False}
    for key, value in expected.items():
        if standard.get(key, {} if key == "parameters" else False) != value:
            raise RuntimeError("kind's standard StorageClass changed: " + key + "; review the aliases before proceeding.")
    provisioner = get(kube, "configmap/local-path-config", "-n", "local-path-storage")
    paths = json.loads(provisioner["data"]["config.json"])["nodePathMap"]
    if paths != [{"node": "DEFAULT_PATH_FOR_NON_LISTED_NODES", "paths": ["/var/local-path-provisioner"]}]:
        raise RuntimeError("Unexpected kind local-path data directory; refusing to leave test volumes outside the state mount.")


def check_host_ports(mappings):
    try:
        privileged_start = int(Path("/proc/sys/net/ipv4/ip_unprivileged_port_start").read_text())
    except FileNotFoundError:
        privileged_start = 1024  # Docker Desktop hosts do not have Linux /proc.
    for mapping in mappings:
        kind = socket.SOCK_STREAM if mapping["protocol"] == "TCP" else socket.SOCK_DGRAM
        with socket.socket(socket.AF_INET, kind) as sock:
            try:
                sock.bind(("127.0.0.1", mapping["hostPort"]))
            except PermissionError:
                if os.geteuid() == 0 or mapping["hostPort"] >= privileged_start:
                    raise
                # A rootful Docker daemon publishes these ports. Its create
                # operation performs the authoritative conflict check.
                print(f"Docker will check privileged host port {mapping['hostPort']}/{mapping['protocol']}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="Independent test cluster name (default from config/kind.json)")
    args = parser.parse_args()
    c = config.load()
    settings = json.loads((ROOT / "config/kind.json").read_text())
    name = args.name or settings["name"]
    import re
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        parser.error("Use a lowercase alphanumeric cluster name, separated by hyphens.")
    for tool in ("docker", "kind", "kubectl", "openssl", "git"):
        if not shutil.which(tool):
            raise RuntimeError(f"Install {tool} before running this script.")
    docker = json.loads(run("docker", "info", "--format", "{{json .}}", capture=True))
    if docker["OSType"] != "linux" or any("rootless" in s for s in docker.get("SecurityOptions", [])):
        raise RuntimeError("Use local Docker with Linux containers (rootful Docker or Docker Desktop).")
    if settings["kind_version"] not in run("kind", "version", capture=True):
        raise RuntimeError("Install kind " + settings["kind_version"])
    run(sys.executable, "scripts/config.py", "check")
    run(sys.executable, "scripts/generate-kind.py", "--check")
    # Flux must fetch the exact configuration used to create this cluster.
    if run("git", "status", "--porcelain", capture=True).strip():
        raise RuntimeError("Commit and push changes before bootstrap.")
    remote = run("git", "ls-remote", c["git_url"], "refs/heads/" + c["git_branch"], capture=True).split()
    if not remote or remote[0] != run("git", "rev-parse", "HEAD", capture=True).strip():
        raise RuntimeError("Checkout must match the configured remote branch before bootstrap.")
    os.umask(0o077)
    state = ROOT / ".state" / name
    state.mkdir(parents=True, exist_ok=True)
    (state / "data").mkdir(exist_ok=True)
    configuration = cluster_config(c, settings, state)
    config_path = state / "kind.json"
    existing = name in run("kind", "get", "clusters", capture=True).splitlines()
    if existing:
        if not config_path.exists() or json.loads(config_path.read_text()) != configuration:
            raise RuntimeError("Existing cluster does not match this checkout's kind configuration; refusing to adopt it.")
    else:
        if any((state / "data").iterdir()) or (state / "ca.key").exists() or (state / "ca.crt").exists():
            raise RuntimeError("Old cluster state remains. Archive it and use a fresh state directory; see docs/kind.md.")
        check_host_ports(configuration["nodes"][0]["extraPortMappings"])
        config_path.write_text(json.dumps(configuration, indent=2) + "\n")
        run("kind", "create", "cluster", "--name", name, "--image", settings["node_image"], "--config", config_path,
            "--kubeconfig", state / "config.kubeconfig", "--wait", "5m")
    # A dedicated kubeconfig prevents changing the user's current context.
    run("kind", "export", "kubeconfig", "--name", name, "--kubeconfig", state / "config.kubeconfig")
    os.environ["KUBECONFIG"] = str(state / "config.kubeconfig")
    kube = ["kubectl", "--context", "kind-" + name]
    node = name + "-control-plane"
    if get(kube, "node", node)["metadata"]["labels"].get("elektro.internal/profile") != "kind":
        raise RuntimeError("Refusing to modify a cluster without the kind profile label.")
    run(*kube, "wait", "node", "--all", "--for=condition=Ready", "--timeout=300s")
    run(*kube, "-n", "local-path-storage", "rollout", "status", "deployment/local-path-provisioner", "--timeout=300s")
    check_local_storage(kube)
    # The compatibility class named longhorn becomes the sole default.
    run(*kube, "annotate", "storageclass", "standard", "storageclass.kubernetes.io/is-default-class=false", "--overwrite")
    classes = get(kube, "storageclasses")["items"]
    for sc in classes:
        a = sc["metadata"].get("annotations", {})
        if sc["metadata"]["name"] != "longhorn" and any(a.get(k) == "true" for k in (
                "storageclass.kubernetes.io/is-default-class", "storageclass.beta.kubernetes.io/is-default-class")):
            raise RuntimeError("Unexpected default StorageClass: " + sc["metadata"]["name"])
    inspect = json.loads(run("docker", "inspect", node, capture=True))[0]
    node_ip = inspect["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    ipaddress.IPv4Address(node_ip)
    network = json.loads(run("docker", "network", "inspect", "kind", capture=True))[0]
    cidr = next(x["Subnet"] for x in network["IPAM"]["Config"]
                if ipaddress.ip_network(x["Subnet"]).version == 4 and
                ipaddress.ip_address(node_ip) in ipaddress.ip_network(x["Subnet"]))
    run(*kube, "-n", "kube-system", "rollout", "status", "deployment/coredns", "--timeout=300s")
    bootstrap_dns(kube, c)
    initialize_ca(kube, state, name)
    run(*kube, "apply", "--server-side", "-k", "infrastructure/flux")
    run(*kube, "-n", "flux-system", "wait", "deployment", "--all", "--for=condition=Available", "--timeout=300s")
    apply(kube, {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
        "name": "kind-runtime", "namespace": "flux-system", "labels": {"reconcile.fluxcd.io/watch": "Enabled"}},
        "data": {"KIND_NODE_IP": node_ip, "KIND_NODE_CIDR": cidr}})
    initial = (ROOT / "kind/bootstrap/cluster-settings.yaml").read_text()
    apply(kube, json.loads(initial.replace("${KIND_NODE_IP}", node_ip).replace("${KIND_NODE_CIDR}", cidr)))
    run(*kube, "apply", "-f", "clusters/lan/flux-system/source.yaml", "-f", "kind/bootstrap/sync.yaml")
    wait(kube, "gitrepository/flux-system", "kustomization/flux-system", timeout="5m")
    wait(kube, *["kustomization/" + n for n in ("cilium", "dns", "pki", "gateway", "apps")])
    run(*kube, "apply", "-k", "kind/bootstrap/storage")
    wait(kube, "gitrepository/storage-addons", "kustomization/storage-addons")
    wait(kube, *["kustomization/storage-" + n for n in (
        "namespaces", "sources", "longhorn", "classes", "cnpg", "barman", "garage", "velero")])
    run(sys.executable, "scripts/check-base.py", "--profile", "kind")
    print(f"Ready. Export KUBECONFIG={state / 'config.kubeconfig'} before attaching add-ons.")
    print(f"Public test CA: {state / 'ca.crt'}; test data: {state / 'data'}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))

#!/usr/bin/env python3
"""Create/resume the single-node Linux Docker test cluster, then attach Flux."""
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
    while time.monotonic() < deadline:
        ready = True
        for resource in resources:
            obj = get(kube, resource, "-n", namespace, "--ignore-not-found")
            ready = ready and bool(obj and any(
                c.get("type") == "Ready" and c.get("status") == "True" and
                c.get("observedGeneration") == obj["metadata"]["generation"]
                for c in obj.get("status", {}).get("conditions", [])))
        if ready:
            return
        time.sleep(5)
    raise RuntimeError("Timed out waiting for " + ", ".join(resources))


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
        "networking": {"disableDefaultCNI": True, "kubeProxyMode": "none",
                       "podSubnet": c["pod_cidr"], "serviceSubnet": c["service_cidr"],
                       "apiServerAddress": "127.0.0.1"},
        "nodes": [{"role": "control-plane", "labels": {
            "elektro.internal/role": "hybrid", "elektro.internal/edge": "true",
            "elektro.internal/profile": "kind"},
            "extraPortMappings": [
                {"containerPort": port, "hostPort": host, "listenAddress": "127.0.0.1", "protocol": proto}
                for port, host, proto in [(80, settings["http_port"], "TCP"),
                    (443, settings["https_port"], "TCP"), (53, settings["dns_port"], "UDP"),
                    (53, settings["dns_port"], "TCP")]],
            "extraMounts": [{"hostPath": "/lib/modules", "containerPath": "/lib/modules", "readOnly": True},
                            {"hostPath": str(state / "data"), "containerPath": "/var/lib/longhorn"}]}]}


def check_host_ports(mappings):
    privileged_start = int(Path("/proc/sys/net/ipv4/ip_unprivileged_port_start").read_text())
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
    for tool in ("docker", "kind", "kubectl", "helm", "openssl", "git"):
        if not shutil.which(tool):
            raise RuntimeError(f"Install {tool} before running this script.")
    if sys.platform != "linux" or not Path("/sys/fs/cgroup/cgroup.controllers").exists():
        raise RuntimeError("Use a native Linux Docker host with cgroup v2; see docs/kind.md.")
    docker = json.loads(run("docker", "info", "--format", "{{json .}}", capture=True))
    if docker["OSType"] != "linux" or any("rootless" in s for s in docker.get("SecurityOptions", [])):
        raise RuntimeError("Rootful Linux Docker is required for Cilium and Longhorn.")
    if settings["kind_version"] not in run("kind", "version", capture=True):
        raise RuntimeError("Install kind " + settings["kind_version"])
    run(sys.executable, "scripts/config.py", "check")
    run(sys.executable, "scripts/generate-kind.py", "--check")
    # Flux must fetch the exact checkout used to install Cilium.
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
        image = "elektro-kind-node:v1.36.4"
        run("docker", "build", "--build-arg", "NODE_IMAGE=" + settings["node_image"],
            "-t", image, "kind")
        # Do not wait for Node Ready here: Cilium has not been installed yet.
        run("kind", "create", "cluster", "--name", name, "--image", image, "--config", config_path,
            "--kubeconfig", state / "config.kubeconfig")
    # A dedicated kubeconfig prevents changing the user's current context.
    run("kind", "export", "kubeconfig", "--name", name, "--kubeconfig", state / "config.kubeconfig")
    os.environ["KUBECONFIG"] = str(state / "config.kubeconfig")
    kube = ["kubectl", "--context", "kind-" + name]
    node = name + "-control-plane"
    if run("docker", "exec", node, "readlink", "/proc/self/ns/cgroup", capture=True).strip() == os.readlink("/proc/self/ns/cgroup"):
        raise RuntimeError("Docker nodes must use private cgroup namespaces for Cilium.")
    if get(kube, "node", node)["metadata"]["labels"].get("elektro.internal/profile") != "kind":
        raise RuntimeError("Refusing to modify a cluster without the kind profile label.")
    run("docker", "exec", node, "sh", "-ec",
        "for module in iscsi_tcp nfs dm_crypt xt_socket xt_TPROXY xt_mark xt_CT; do modprobe \"$module\"; done; "
        "systemctl enable --now iscsid; systemctl is-active --quiet iscsid; "
        "test -f /etc/iscsi/initiatorname.iscsi; "
        "fs=$(findmnt -n -o FSTYPE -T /var/lib/longhorn); "
        "case \"$fs\" in ext4|xfs) ;; *) echo 'Longhorn data directory requires ext4 or XFS' >&2; exit 1;; esac")
    # kind installs this additional default even when its CNI is disabled.
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
    installed = run(*kube, "apply", "--server-side", "-k", "infrastructure/gateway-api", "-o", "name", capture=True)
    crds = [r for r in installed.splitlines() if r.startswith("customresourcedefinition.")]
    if not crds:
        raise RuntimeError("Gateway API bundle did not contain any CRDs.")
    run(*kube, "wait", "--for=condition=Established", "--timeout=120s", *crds)
    values = json.loads((ROOT / "profiles/kind/cilium/values.yaml").read_text())
    values["k8sServiceHost"] = node_ip
    (state / "cilium-values.json").write_text(json.dumps(values))
    # If already adopted, Flux is the sole Helm release manager.
    crd = get(kube, "crd/helmreleases.helm.toolkit.fluxcd.io", "--ignore-not-found")
    release = get(kube, "helmrelease/cilium", "-n", "kube-system", "--ignore-not-found") if crd else None
    if not release:
        run("helm", "repo", "add", "cilium", "https://helm.cilium.io", "--force-update")
        run("helm", "repo", "update", "cilium")
        run("helm", "upgrade", "--install", "cilium", "cilium/cilium", "--namespace", "kube-system",
            "--version", c["cilium_version"], "--values", state / "cilium-values.json", "--wait", "--timeout", "15m")
    run(*kube, "wait", "node", "--all", "--for=condition=Ready", "--timeout=300s")
    run(*kube, "-n", "kube-system", "rollout", "status", "deployment/coredns", "--timeout=300s")
    initialize_ca(kube, state, name)
    run(*kube, "apply", "--server-side", "-k", "infrastructure/flux")
    run(*kube, "-n", "flux-system", "wait", "deployment", "--all", "--for=condition=Available", "--timeout=300s")
    apply(kube, {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
        "name": "kind-runtime", "namespace": "flux-system", "labels": {"reconcile.fluxcd.io/watch": "Enabled"}},
        "data": {"KIND_NODE_IP": node_ip, "KIND_NODE_CIDR": cidr}})
    initial = (ROOT / "kind/bootstrap/cluster-settings.yaml").read_text()
    apply(kube, json.loads(initial.replace("${KIND_NODE_IP}", node_ip).replace("${KIND_NODE_CIDR}", cidr)))
    run(*kube, "apply", "-f", "clusters/lan/flux-system/source.yaml", "-f", "kind/bootstrap/sync.yaml")
    wait(kube, "gitrepository/flux-system", "kustomization/flux-system")
    wait(kube, *["kustomization/" + n for n in ("cilium", "dns", "pki", "gateway", "apps")])
    run(*kube, "apply", "-k", "kind/bootstrap/storage")
    wait(kube, "gitrepository/storage-addons", "kustomization/storage-addons")
    wait(kube, *["kustomization/storage-" + n for n in (
        "namespaces", "sources", "longhorn", "classes", "cnpg", "barman", "garage", "velero")], timeout="30m")
    run(sys.executable, "scripts/check-base.py", "--profile", "kind")
    print(f"Ready. Export KUBECONFIG={state / 'config.kubeconfig'} before attaching add-ons.")
    print(f"Public test CA: {state / 'ca.crt'}; test data: {state / 'data'}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))

#!/usr/bin/env python3
"""Exercise real storage, private DNS, HTTPS routing and CNPG on a kind base."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def run(*args, input=None):
    return subprocess.check_output(args, input=input, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    ports = json.loads((ROOT / "config/kind.json").read_text())
    parser.add_argument("--http-port", type=int, default=ports["http_port"])
    parser.add_argument("--https-port", type=int, default=ports["https_port"])
    args = parser.parse_args()
    run(sys.executable, str(ROOT / "scripts/check-base.py"), "--profile", "kind")
    settings = json.loads(run("kubectl", "-n", "flux-system", "get", "cm/cluster-settings", "-o", "json"))["data"]
    if settings.get("CLUSTER_PROFILE") != "kind":
        raise RuntimeError("Smoke test is restricted to the kind profile.")
    namespace = "base-smoke-" + secrets.token_hex(3)
    token = secrets.token_hex(16)
    def apply(obj):
        run("kubectl", "apply", "-f", "-", input=json.dumps(obj))
    def resource(api, kind, name, **fields):
        return dict(apiVersion=api, kind=kind, metadata={"name": name, "namespace": namespace}, **fields)
    apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace,
          "labels": {"elektro.internal/route-scope": "applications"}}})
    try:
        classes = ("longhorn", "longhorn-cnpg", "longhorn-garage", "longhorn-replicated")
        for name in classes:
            apply(resource("v1", "PersistentVolumeClaim", name, spec={"accessModes": ["ReadWriteOnce"],
                "storageClassName": name, "resources": {"requests": {"storage": "1Gi"}}}))
        def pod(name, command):
            return resource("v1", "Pod", name, spec={"restartPolicy": "Never", "containers": [{
                "name": "test", "image": "busybox:1.37.0", "command": ["sh", "-ec", command],
                "volumeMounts": [{"name": c, "mountPath": "/data/" + c} for c in classes]}],
                "volumes": [{"name": c, "persistentVolumeClaim": {"claimName": c}} for c in classes]})
        apply(pod("write", f"for p in /data/*; do echo {token} > \"$p/probe\"; done; sync; sleep 3600"))
        run("kubectl", "-n", namespace, "wait", "pod/write", "--for=condition=Ready", "--timeout=10m")
        run("kubectl", "-n", namespace, "exec", "write", "--", "sh", "-ec",
            f"for p in /data/*; do test \"$(cat \"$p/probe\")\" = {token}; done")
        run("kubectl", "-n", namespace, "delete", "pod/write", "--wait=true", "--timeout=180s")
        apply(pod("read", f"for p in /data/*; do test \"$(cat \"$p/probe\")\" = {token}; done; sleep 3600"))
        run("kubectl", "-n", namespace, "wait", "pod/read", "--for=condition=Ready", "--timeout=10m")
        hostname = namespace + "." + settings["TESTING_DOMAIN"]
        dns = run("kubectl", "-n", namespace, "exec", "read", "--", "nslookup", hostname)
        if settings["API_IP"] not in dns:
            raise RuntimeError("Private DNS returned an unexpected address")
        run("kubectl", "-n", namespace, "exec", "read", "--", "nslookup", "kubernetes.default.svc.cluster.local")
        apply(resource("apps/v1", "Deployment", "echo", spec={"replicas": 1,
            "selector": {"matchLabels": {"app": "echo"}}, "template": {"metadata": {"labels": {"app": "echo"}},
            "spec": {"containers": [{"name": "echo", "image": "traefik/whoami:v1.11.0", "args": ["--port=8080"]}]}}}))
        apply(resource("v1", "Service", "echo", spec={"selector": {"app": "echo"}, "ports": [{"port": 80, "targetPort": 8080}]}))
        apply(resource("cilium.io/v2", "CiliumNetworkPolicy", "echo", spec={"endpointSelector": {"matchLabels": {"app": "echo"}},
            "ingress": [{"fromEntities": ["ingress"], "toPorts": [{"ports": [{"port": "8080", "protocol": "TCP"}]}]}]}))
        apply(resource("gateway.networking.k8s.io/v1", "HTTPRoute", "echo", spec={"parentRefs": [{"name": "internal",
            "namespace": "gateway-system", "sectionName": "testing-https"}], "hostnames": [hostname],
            "rules": [{"backendRefs": [{"name": "echo", "port": 80}]}]}))
        run("kubectl", "-n", namespace, "rollout", "status", "deployment/echo", "--timeout=180s")
        import base64
        secret = json.loads(run("kubectl", "-n", "cert-manager", "get", "secret/internal-ca", "-o", "json"))
        with tempfile.TemporaryDirectory() as tmp:
            ca = Path(tmp) / "ca.crt"
            ca.write_bytes(base64.b64decode(secret["data"]["tls.crt"]))
            # Curl validates both the certificate and the hostname. Applying the
            # Cilium policy above tests schema compatibility, not enforcement.
            body = run("curl", "--noproxy", "*", "--fail", "--silent", "--show-error",
                "--retry", "30", "--retry-delay", "2", "--retry-all-errors", "--max-time", "10",
                "--cacert", str(ca), "--resolve", f"{hostname}:{args.https_port}:127.0.0.1",
                f"https://{hostname}:{args.https_port}/")
            if "Hostname:" not in body:
                raise RuntimeError("Gateway response did not reach the echo backend")
            headers = run("curl", "--noproxy", "*", "--silent", "--show-error", "--max-time", "10",
                "--dump-header", "-", "--output", "/dev/null", "--resolve", f"{hostname}:{args.http_port}:127.0.0.1",
                f"http://{hostname}:{args.http_port}/")
            if "location: https://" + hostname not in headers.lower():
                raise RuntimeError("HTTP did not redirect to the HTTPS Gateway")
            apply(resource("v1", "ConfigMap", "test-ca", data={"ca.crt": ca.read_text()}))
        # Exercise the URL that pods use for SSO callbacks and service access,
        # including private DNS, node hostPorts and certificate verification.
        apply(resource("batch/v1", "Job", "pod-https", spec={"backoffLimit": 1, "activeDeadlineSeconds": 120,
            "template": {"spec": {"restartPolicy": "Never", "containers": [{"name": "curl",
                "image": "curlimages/curl:8.16.0", "args": ["--fail", "--silent", "--show-error", "--max-time", "30",
                    "--cacert", "/ca/ca.crt", "https://" + hostname + "/"],
                "volumeMounts": [{"name": "ca", "mountPath": "/ca", "readOnly": True}]}],
                "volumes": [{"name": "ca", "configMap": {"name": "test-ca"}}]}}}))
        run("kubectl", "-n", namespace, "wait", "job/pod-https", "--for=condition=Complete", "--timeout=180s")
        if "Hostname:" not in run("kubectl", "-n", namespace, "logs", "job/pod-https"):
            raise RuntimeError("Pod HTTPS did not reach the echo backend")
        apply(resource("postgresql.cnpg.io/v1", "Cluster", "database", spec={"instances": 1,
            "storage": {"storageClass": "longhorn-cnpg", "size": "1Gi"},
            "bootstrap": {"initdb": {"database": "app", "owner": "app"}}}))
        run("kubectl", "-n", namespace, "wait", "cluster.postgresql.cnpg.io/database",
            "--for=condition=Ready", "--timeout=10m")
        result = run("kubectl", "-n", namespace, "exec", "database-1", "-c", "postgres", "--",
                     "psql", "-U", "postgres", "-tAc", "SELECT 1")
        if result.strip() != "1":
            raise RuntimeError("CNPG SQL probe failed")
        print("Smoke passed: four PVC write/remount checks, private/cluster DNS, host/pod HTTPS, HTTP redirect, policy API compatibility, and CNPG SQL.")
    finally:
        # Only this invocation's randomly named disposable namespace is removed.
        run("kubectl", "delete", "namespace", namespace, "--wait=false")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))

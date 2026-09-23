#!/usr/bin/env python3
"""Read-only compatibility check before attaching downstream Flux repositories."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("namespaces", "gateway-api", "cilium", "network", "dns", "cert-manager", "pki", "gateway", "apps",
          "storage-namespaces", "storage-sources", "storage-longhorn", "storage-classes",
          "storage-cnpg", "storage-barman", "storage-garage", "storage-velero")


def get(*args):
    return json.loads(subprocess.check_output(["kubectl", "get", *args, "-o", "json"], text=True))


def check(profile):
    errors = []
    for name in STAGES:
        obj = get("kustomization.kustomize.toolkit.fluxcd.io", name, "-n", "flux-system")
        if not any(c.get("type") == "Ready" and c.get("status") == "True" and
                   c.get("observedGeneration") == obj["metadata"]["generation"]
                   for c in obj.get("status", {}).get("conditions", [])):
            errors.append(f"Flux stage {name} is not currently Ready")
    settings = get("configmap/cluster-settings", "-n", "flux-system")["data"]
    for key in ("DOMAIN", "ADMIN_DOMAIN", "MANAGEMENT_DOMAIN", "TESTING_DOMAIN", "STAGING_DOMAIN",
                "API_IP", "CLUSTER_DNS_IP", "LAN_DNS_SERVICE_IP"):
        if not settings.get(key) or "${" in settings[key]:
            errors.append("Unresolved cluster setting " + key)
    classes = get("storageclasses")["items"]
    defaults = [sc["metadata"]["name"] for sc in classes if any(
        sc["metadata"].get("annotations", {}).get(key) == "true" for key in (
            "storageclass.kubernetes.io/is-default-class", "storageclass.beta.kubernetes.io/is-default-class"))]
    if defaults != ["longhorn"]:
        errors.append(f"Expected sole default longhorn, got {defaults}")
    for name in ("longhorn", "longhorn-cnpg", "longhorn-garage", "longhorn-replicated"):
        sc = next((x for x in classes if x["metadata"]["name"] == name), {})
        replicas = "3" if profile == "k3s" and name == "longhorn-replicated" else "1"
        if sc.get("provisioner") != "driver.longhorn.io" or sc.get("parameters", {}).get("numberOfReplicas") != replicas:
            errors.append(f"{name} must use the Longhorn driver with {replicas} replicas")
    for namespace, service in (("kube-system", "hubble-ui"), ("longhorn-system", "longhorn-frontend"), ("garage", "garage")):
        get("service", service, "-n", namespace)
        endpoints = get("endpointslices", "-n", namespace, "-l", "kubernetes.io/service-name=" + service)
        if not any(e.get("conditions", {}).get("ready") is True for s in endpoints["items"] for e in s.get("endpoints", [])):
            errors.append(f"No ready backend for {namespace}/{service}")
    get("secret/internal-ca", "-n", "cert-manager")
    get("secret/internal-wildcard-tls", "-n", "gateway-system")
    spec = importlib.util.spec_from_file_location("gateway_check", ROOT / "scripts/check-gateway.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    snapshot = {
        "ingress": get("ingress,ingressclass", "-A"),
        "cilium": get("configmap/cilium-config", "-n", "kube-system"),
        "class": get("gatewayclass/cilium"),
        "gateway": get("gateway/internal", "-n", "gateway-system"),
        "routes": get("httproutes", "-A"),
        "edge": get("nodes", "-l", "elektro.internal/edge=true"),
    }
    errors.extend(module.check(snapshot, settings["API_IP"]))
    if profile == "kind":
        nodes = get("nodes")["items"]
        if len(nodes) != 1 or nodes[0]["spec"].get("unschedulable") or any(
                t["effect"] in ("NoSchedule", "NoExecute") for t in nodes[0]["spec"].get("taints", [])):
            errors.append("kind needs exactly one schedulable node")
    if errors:
        raise RuntimeError("\n".join(errors))
    print(f"{profile} base is Ready: Gateway, DNS, TLS, Flux dependencies, services and four storage classes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("k3s", "kind"), required=True)
    try:
        check(parser.parse_args().profile)
    except (KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))

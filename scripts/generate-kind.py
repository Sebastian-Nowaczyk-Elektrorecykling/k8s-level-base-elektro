#!/usr/bin/env python3
"""Generate the kind overlay without changing the golden k3s configuration."""
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import config


def kustomization(resources, **extra):
    return dict(apiVersion="kustomize.config.k8s.io/v1beta1", kind="Kustomization",
                resources=resources, **extra)


def patch(kind, name, operations, namespace=None):
    target = dict(kind=kind, name=name)
    if namespace:
        target["namespace"] = namespace
    return dict(target=target, patch=json.dumps(operations))


def replace(path, value):
    return dict(op="replace", path=path, value=value)


def outputs():
    c = config.load()
    values = copy.deepcopy(config.cilium_values(c))
    values["cluster"]["name"] = "elektro-test"
    values["k8sServiceHost"] = "${API_IP}"
    # The container's own cgroup namespace avoids attaching BPF to the host root.
    values["cgroup"] = {"autoMount": {"enabled": False}, "hostRoot": "/sys/fs/cgroup"}
    values["image"] = {"pullPolicy": "IfNotPresent"}
    values["devices"] = ["eth0"]
    values["operator"]["replicas"] = 1
    settings = config.outputs(c)["clusters/lan/cluster-settings.yaml"]
    settings["data"].update(API_IP="${KIND_NODE_IP}", LAN_CIDR="${KIND_NODE_CIDR}",
                            CLUSTER_NAME="elektro-test", CLUSTER_PROFILE="kind")
    # This ConfigMap is deliberately runtime-owned, outside the Flux inventory.
    runtime = {"substituteFrom": [{"kind": "ConfigMap", "name": "kind-runtime"}]}
    sync = copy.deepcopy(config.outputs(c)["clusters/lan/flux-system/sync.yaml"])
    sync["spec"].update(path="./clusters/kind", postBuild=runtime)
    patches = [
        patch("Kustomization", "flux-system", [replace("/spec/path", "./clusters/kind"),
              {"op": "add", "path": "/spec/postBuild", "value": runtime}]),
        patch("ConfigMap", "cluster-settings", [replace("/data", settings["data"])]),
        patch("Kustomization", "cilium", [replace("/spec/path", "./profiles/kind/cilium")]),
        patch("Kustomization", "dns", [replace("/spec/path", "./profiles/kind/dns")]),
    ]
    corefile = """${DOMAIN}:53 {
    errors
    cache 30
    forward . ${LAN_DNS_SERVICE_IP}
}
.:53 {
    errors
    health { lameduck 5s }
    ready
    kubernetes cluster.local in-addr.arpa ip6.arpa {
        pods insecure
        fallthrough in-addr.arpa ip6.arpa
        ttl 30
    }
    prometheus :9153
    forward . ${UPSTREAM_DNS} { max_concurrent 1000 }
    cache 30
    loop
    reload
    loadbalance
}
"""
    return {
        "kind/bootstrap/cluster-settings.yaml": settings,
        "kind/bootstrap/sync.yaml": sync,
        "kind/bootstrap/storage/kustomization.yaml": kustomization(
            ["../../../storage/bootstrap"], patches=[patch("Kustomization", "storage-addons",
                [replace("/spec/path", "./storage/clusters/kind")])]),
        "storage/bootstrap/source.yaml": {
            "apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "GitRepository",
            "metadata": {"name": "storage-addons", "namespace": "flux-system"},
            "spec": {"interval": "1m", "url": c["git_url"], "ref": {"branch": c["git_branch"]}}},
        "clusters/kind/kustomization.yaml": kustomization(["../lan"], patches=patches),
        "profiles/kind/cilium/values.yaml": values,
        "profiles/kind/cilium/kustomization.yaml": kustomization(
            ["../../../infrastructure/cilium"], configMapGenerator=[{
                "name": "cilium-values", "namespace": "kube-system", "behavior": "replace",
                "files": ["values.yaml"]}], generatorOptions={"disableNameSuffixHash": True,
                "labels": {"reconcile.fluxcd.io/watch": "Enabled"}}),
        "profiles/kind/dns/kustomization.yaml": kustomization(
            ["../../../infrastructure/dns", "coredns.yaml"]),
        "profiles/kind/dns/coredns.yaml": {
            "apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": "coredns", "namespace": "kube-system"},
            "data": {"Corefile": corefile}},
        "profiles/kind/storage-classes/kustomization.yaml": kustomization(
            ["../../../storage/infrastructure/storage-classes"], patches=[
                patch("StorageClass", "longhorn-replicated",
                      [replace("/parameters/numberOfReplicas", "1")])]),
        "profiles/kind/longhorn/kustomization.yaml": kustomization(
            ["../../../storage/infrastructure/longhorn"], patches=[{
                "target": {"kind": "HelmRelease", "name": "longhorn"},
                "patch": json.dumps({"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
                    "metadata": {"name": "longhorn", "namespace": "longhorn-system"},
                    "spec": {"values": {"longhornUI": {"replicas": 1}, "csi": {
                        "attacherReplicaCount": 1, "provisionerReplicaCount": 1,
                        "resizerReplicaCount": 1, "snapshotterReplicaCount": 1}}}})}]),
        "storage/clusters/kind/kustomization.yaml": kustomization(["../lan"], patches=[
            patch("Kustomization", "storage-addons", [replace("/spec/path", "./storage/clusters/kind")]),
            patch("Kustomization", "storage-classes", [replace("/spec/path", "./profiles/kind/storage-classes")]),
            patch("Kustomization", "storage-longhorn", [replace("/spec/path", "./profiles/kind/longhorn")]),
        ]),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    stale = []
    for relative, obj in outputs().items():
        path = ROOT / relative
        text = json.dumps(obj, indent=2) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        sys.exit("Run python3 scripts/generate-kind.py; stale files: " + ", ".join(stale))


if __name__ == "__main__":
    main()

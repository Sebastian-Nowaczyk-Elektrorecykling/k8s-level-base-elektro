#!/usr/bin/env python3
"""Generate application-compatible kind overlays; never change the golden k3s base."""
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import config

CONTROLLER = "traefik.io/gateway-controller"


def kustomization(resources, **extra):
    return dict(apiVersion="kustomize.config.k8s.io/v1beta1", kind="Kustomization",
                resources=resources, **extra)


def patch(kind, name, operations):
    return dict(target=dict(kind=kind, name=name), patch=json.dumps(operations))


def replace(path, value):
    return dict(op="replace", path=path, value=value)


def storage_class(name, default=False):
    # Copy the fields of kind v0.33.0's standard StorageClass. Bootstrap checks
    # that the installed standard class still matches this pinned contract.
    return {"apiVersion": "storage.k8s.io/v1", "kind": "StorageClass",
        "metadata": {"name": name, "annotations": {
            "storageclass.kubernetes.io/is-default-class": str(default).lower()}},
        "provisioner": "rancher.io/local-path", "volumeBindingMode": "WaitForFirstConsumer",
        "reclaimPolicy": "Delete"}


def outputs():
    c = config.load()
    kind = json.loads((ROOT / "config/kind.json").read_text())
    settings = config.outputs(c)["clusters/lan/cluster-settings.yaml"]
    settings["data"].update(API_IP="${KIND_NODE_IP}", LAN_CIDR="${KIND_NODE_CIDR}",
                            CLUSTER_NAME="elektro-test", CLUSTER_PROFILE="kind")
    runtime = {"substituteFrom": [{"kind": "ConfigMap", "name": "kind-runtime"}]}
    sync = copy.deepcopy(config.outputs(c)["clusters/lan/flux-system/sync.yaml"])
    sync["spec"].update(path="./clusters/kind", postBuild=runtime)
    patches = [
        patch("CustomResourceDefinition", ".*", [{"op": "add",
              "path": "/metadata/labels/kustomize.toolkit.fluxcd.io~1substitute", "value": "disabled"}]),
        patch("Kustomization", "flux-system", [replace("/spec/path", "./clusters/kind"),
              {"op": "add", "path": "/spec/postBuild", "value": runtime}]),
        patch("ConfigMap", "cluster-settings", [replace("/data", settings["data"])]),
        # Keep dependency names used by application repositories. These stages
        # reconcile actual kind capabilities, without installing Cilium/Longhorn.
        patch("Kustomization", "cilium", [replace("/spec/path", "./profiles/kind/gateway-controller"),
              replace("/spec/dependsOn", [{"name": "gateway-api"}, {"name": "namespaces"}])]),
        patch("Kustomization", "network", [replace("/spec/path", "./profiles/kind/network")]),
        patch("Kustomization", "dns", [replace("/spec/path", "./profiles/kind/dns")]),
        {"target": {"kind": "Kustomization", "name": "(network|gateway|apps)"},
         "path": "gateway-health.yaml"},
    ]
    corefile = """${DOMAIN}:53 {
    errors
    cache 30
    forward . ${LAN_DNS_SERVICE_IP}
}
.:53 {
    errors
    health {
        lameduck 5s
    }
    ready
    kubernetes cluster.local in-addr.arpa ip6.arpa {
        pods insecure
        fallthrough in-addr.arpa ip6.arpa
        ttl 30
    }
    prometheus :9153
    forward . ${UPSTREAM_DNS} {
        max_concurrent 1000
    }
    cache 30
    loop
    reload
    loadbalance
}
"""
    traefik = {
        "fullnameOverride": "kind-gateway",
        "deployment": {"replicas": 1},
        "updateStrategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1}},
        "nodeSelector": {"elektro.internal/edge": "true"},
        "providers": {"kubernetesCRD": {"enabled": False}, "kubernetesIngress": {"enabled": False},
                      "kubernetesGateway": {"enabled": True, "statusAddress": {
                          "ip": "${API_IP}", "service": {"enabled": False}}}},
        "gateway": {"enabled": False}, "gatewayClass": {"enabled": False},
        "ingressClass": {"enabled": False}, "api": {"dashboard": False},
        "service": {"spec": {"type": "ClusterIP"}},
        "ports": {"web": {"port": 80, "hostPort": 80, "exposedPort": 80},
                  "websecure": {"port": 443, "hostPort": 443, "exposedPort": 443}},
        "securityContext": {"capabilities": {"drop": ["ALL"], "add": ["NET_BIND_SERVICE"]}},
        "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"memory": "512Mi"}},
    }
    release = {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
        "metadata": {"name": "kind-gateway", "namespace": "gateway-system"},
        "spec": {"interval": "10m", "timeout": "10m", "releaseName": "kind-gateway",
                 "chart": {"spec": {"chart": "traefik", "version": kind["gateway_chart_version"],
                     "sourceRef": {"kind": "HelmRepository", "name": "kind-traefik"}}},
                 "install": {"crds": "Skip", "remediation": {"retries": 2}},
                 "upgrade": {"crds": "Skip", "remediation": {"retries": 2}},
                 "driftDetection": {"mode": "enabled"}, "values": traefik}}
    crd_base = f"https://raw.githubusercontent.com/cilium/cilium/v{c['cilium_version']}/pkg/k8s/apis/cilium.io/client/crds/v2/"
    result = {
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
        "clusters/kind/gateway-health.yaml": (ROOT / "clusters/lan/gateway-health.yaml").read_text().replace(
            "io.cilium/gateway-controller", CONTROLLER),
        "profiles/kind/gateway-controller/kustomization.yaml": kustomization(
            ["release.yaml", "source.yaml", "../policy-api"]),
        "profiles/kind/gateway-controller/release.yaml": release,
        "profiles/kind/gateway-controller/source.yaml": {
            "apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "HelmRepository",
            "metadata": {"name": "kind-traefik", "namespace": "gateway-system"},
            "spec": {"interval": "1h", "url": "https://traefik.github.io/charts"}},
        # Real schemas allow unmodified app repositories containing Cilium policy
        # objects to reconcile. There is intentionally no policy controller.
        "profiles/kind/policy-api/kustomization.yaml": kustomization([
            crd_base + "ciliumnetworkpolicies.yaml", crd_base + "ciliumclusterwidenetworkpolicies.yaml"],
            commonAnnotations={"kustomize.toolkit.fluxcd.io/substitute": "disabled"}),
        "profiles/kind/network/kustomization.yaml": kustomization(["../../../infrastructure/network"],
            patches=[patch("GatewayClass", "cilium", [replace("/spec/controllerName", CONTROLLER)])]),
        "profiles/kind/dns/kustomization.yaml": kustomization(["../../../infrastructure/dns", "coredns.yaml"]),
        "profiles/kind/dns/coredns.yaml": {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": "coredns", "namespace": "kube-system"}, "data": {"Corefile": corefile}},
        "profiles/kind/local-storage/kustomization.yaml": kustomization(["standard.yaml"]),
        "profiles/kind/local-storage/standard.yaml": storage_class("standard"),
        "profiles/kind/storage-classes/kustomization.yaml": kustomization([
            name + ".yaml" for name in ("longhorn", "longhorn-cnpg", "longhorn-garage", "longhorn-replicated")]),
        "storage/clusters/kind/kustomization.yaml": kustomization(["../lan"], patches=[
            patch("Kustomization", "storage-addons", [replace("/spec/path", "./storage/clusters/kind")]),
            patch("Kustomization", "storage-classes", [replace("/spec/path", "./profiles/kind/storage-classes")]),
            patch("Kustomization", "storage-longhorn", [replace("/spec/path", "./profiles/kind/local-storage"),
                replace("/spec/wait", False), {"op": "add", "path": "/spec/healthChecks", "value": [
                    {"apiVersion": "apps/v1", "kind": "Deployment", "name": "local-path-provisioner",
                     "namespace": "local-path-storage"}]}]),
        ]),
    }
    for name in ("longhorn", "longhorn-cnpg", "longhorn-garage", "longhorn-replicated"):
        result[f"profiles/kind/storage-classes/{name}.yaml"] = storage_class(name, default=name == "longhorn")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    stale = []
    for relative, obj in outputs().items():
        path = ROOT / relative
        text = obj if isinstance(obj, str) else json.dumps(obj, indent=2) + "\n"
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

#!/usr/bin/env python3
"""Check golden-file parity and build both complete Flux dependency graphs."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import config


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True)


def docs(text):
    return [x for x in yaml.safe_load_all(text) if x]


def build(path):
    return docs(run("kustomize", "build", path))


def one(objects, kind, name):
    matches = [x for x in objects if x["kind"] == kind and x["metadata"]["name"] == name]
    assert len(matches) == 1, (kind, name, len(matches))
    return matches[0]


def parity():
    lock = json.loads((ROOT / "config/upstream-lock.json").read_text())
    adaptations = {
        "network": {"README.md", ".gitignore", "config/cluster.json", "clusters/lan/cluster-settings.yaml"},
        "storage": {"bootstrap/source.yaml", "bootstrap/sync.yaml", "clusters/lan/infrastructure.yaml", "scripts/validate.py"},
    }
    checked = 0
    for name, pin in lock.items():
        base = ROOT if name == "network" else ROOT / "storage"
        for path, expected in pin["files"].items():
            if path in adaptations[name]:
                continue
            assert hashlib.sha256((base / path).read_bytes()).hexdigest() == expected, f"Golden file changed: {name}/{path}"
            checked += 1
    golden_path = ROOT / "config/golden-cluster.json"
    assert hashlib.sha256(golden_path.read_bytes()).hexdigest() == lock["network"]["files"]["config/cluster.json"]
    actual, expected = config.load(), json.loads(golden_path.read_text())
    for key in ("git_url", "git_branch"):
        actual.pop(key)
        expected.pop(key)
    assert actual == expected, "k3s configuration must match the golden baseline except for Git identity"
    print(f"Golden parity: {checked} imported files are byte-identical; k3s settings match.")


def substitute(objects, settings):
    def replacement(match):
        assert match[1] in settings, "Missing substitution: " + match[1]
        return settings[match[1]]
    result = []
    for obj in objects:
        meta = obj.get("metadata", {})
        if any(meta.get(key, {}).get("kustomize.toolkit.fluxcd.io/substitute") == "disabled"
               for key in ("labels", "annotations")):
            result.append(obj)
            continue
        text = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replacement, yaml.safe_dump(obj))
        assert "${" not in text, f"Unresolved substitution expression in {obj['kind']}/{meta.get('name')}"
        result.extend(docs(text))
    return result


def profile(name):
    network_root = build("clusters/" + ("lan" if name == "k3s" else "kind"))
    if name == "kind":
        for obj in network_root:
            if obj["kind"] == "CustomResourceDefinition":
                assert obj["metadata"]["labels"]["kustomize.toolkit.fluxcd.io/substitute"] == "disabled"
        network_root = substitute(network_root, {"KIND_NODE_IP": "172.18.0.2", "KIND_NODE_CIDR": "172.18.0.0/16"})
    settings = one(network_root, "ConfigMap", "cluster-settings")["data"]
    storage_root = build("storage/clusters/" + ("lan" if name == "k3s" else "kind"))
    roots = network_root + storage_root
    stages = {x["metadata"]["name"]: x for x in roots if x["kind"] == "Kustomization" and
              x["apiVersion"].startswith("kustomize.toolkit")}
    assert set(stages) == {"flux-system", "storage-addons", "namespaces", "gateway-api", "cilium", "network",
        "dns", "cert-manager", "pki", "gateway", "apps", "storage-namespaces", "storage-sources",
        "storage-longhorn", "storage-classes", "storage-cnpg", "storage-barman", "storage-garage", "storage-velero"}
    def visit(key, chain=()):
        assert key not in chain, f"Dependency cycle: {chain} -> {key}"
        for dep in stages[key]["spec"].get("dependsOn", []):
            assert dep["name"] in stages, f"Missing dependency: {dep}"
            visit(dep["name"], chain + (key,))
    leaves = {}
    for key, stage in stages.items():
        visit(key)
        path = stage["spec"]["path"]
        assert (ROOT / path / "kustomization.yaml").exists(), path
        expected_source = "storage-addons" if key.startswith("storage-") else "flux-system"
        assert stage["spec"]["sourceRef"]["name"] == expected_source
        if key not in ("flux-system", "storage-addons"):
            leaves[key] = substitute(build(path), settings)
    objects = [x for group in leaves.values() for x in group]
    identities = [(o["apiVersion"], o["kind"], o["metadata"].get("namespace"), o["metadata"]["name"]) for o in objects]
    assert len(set(identities)) == len(identities), "Overlapping Flux ownership"
    assert not any(x["kind"] in ("Ingress", "IngressClass") for x in objects)
    assert "${" not in yaml.safe_dump_all([x for x in objects if x["kind"] != "CustomResourceDefinition"])
    gateway = one(objects, "Gateway", "internal")
    assert gateway["metadata"]["namespace"] == "gateway-system"
    assert gateway["spec"]["gatewayClassName"] == "cilium"
    assert {l["name"] for l in gateway["spec"]["listeners"]} == {
        "http", "apps-https", "admin-https", "management-https", "testing-https", "staging-https"}
    for n in ("longhorn", "longhorn-cnpg", "longhorn-garage", "longhorn-replicated"):
        sc = one(objects, "StorageClass", n)
        if name == "k3s":
            assert sc["provisioner"] == "driver.longhorn.io"
            assert sc["parameters"]["numberOfReplicas"] == ("3" if n == "longhorn-replicated" else "1")
        else:
            standard = one(objects, "StorageClass", "standard")
            assert {k: v for k, v in sc.items() if k != "metadata"} == {
                k: v for k, v in standard.items() if k != "metadata"}
            assert sc["provisioner"] == "rancher.io/local-path"
            assert sc["volumeBindingMode"] == "WaitForFirstConsumer"
            assert sc["reclaimPolicy"] == "Delete"
            assert not sc.get("allowVolumeExpansion", False)
        assert sc["metadata"]["annotations"]["storageclass.kubernetes.io/is-default-class"] == ("true" if n == "longhorn" else "false")
    common_releases = {"cert-manager", "cloudnative-pg", "plugin-barman-cloud", "garage", "velero"}
    assert {x["metadata"]["name"] for x in objects if x["kind"] == "HelmRelease"} == common_releases | (
        {"cilium", "longhorn"} if name == "k3s" else {"kind-gateway"})
    controller = "io.cilium/gateway-controller" if name == "k3s" else "traefik.io/gateway-controller"
    assert one(objects, "GatewayClass", "cilium")["spec"]["controllerName"] == controller
    for n in ("network", "gateway", "apps"):
        health = yaml.safe_dump(stages[n]["spec"]["healthCheckExprs"])
        assert controller in health
    if name == "kind":
        gateway_values = one(objects, "HelmRelease", "kind-gateway")["spec"]["values"]
        assert gateway_values["providers"]["kubernetesGateway"]["enabled"] is True
        assert gateway_values["providers"]["kubernetesCRD"]["enabled"] is False
        assert gateway_values["providers"]["kubernetesIngress"]["enabled"] is False
        assert gateway_values["providers"]["kubernetesGateway"]["statusAddress"]["ip"] == settings["API_IP"]
        for port, name_port in ((80, "web"), (443, "websecure")):
            assert gateway_values["ports"][name_port]["port"] == port
            assert gateway_values["ports"][name_port]["hostPort"] == port
        assert stages["storage-longhorn"]["spec"]["wait"] is False
        assert stages["storage-longhorn"]["spec"]["healthChecks"] == [{
            "apiVersion": "apps/v1", "kind": "Deployment", "name": "local-path-provisioner", "namespace": "local-path-storage"}]
        for n in ("ciliumnetworkpolicies.cilium.io", "ciliumclusterwidenetworkpolicies.cilium.io"):
            crd = one(objects, "CustomResourceDefinition", n)
            assert crd["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/substitute"] == "disabled"
        assert settings["CLUSTER_DNS_IP"] == "10.43.0.10"
        corefile = one(objects, "ConfigMap", "coredns")["data"]["Corefile"]
        assert settings["DOMAIN"] + ":53" in corefile
        assert "forward . " + settings["LAN_DNS_SERVICE_IP"] in corefile
        assert "cluster.local" in corefile
    else:
        values = json.loads(one(objects, "ConfigMap", "cilium-values")["data"]["values.yaml"])
        assert values["k8sServiceHost"] == settings["API_IP"]
        assert values["kubeProxyReplacement"] is True
        assert values["gatewayAPI"]["hostNetwork"]["enabled"] is True
    print(f"{name}: built {len(leaves)} Flux targets, verified dependencies, Gateway, DNS and storage.")
    return leaves


def main():
    run(sys.executable, "scripts/config.py", "check")
    run(sys.executable, "scripts/generate-kind.py", "--check")
    parity()
    golden, kind = profile("k3s"), profile("kind")
    # Run the controller's actual substitution implementation too. In dry-run
    # mode use explicit fixture values instead of reading a live ConfigMap.
    fixture = json.loads((ROOT / "kind/bootstrap/sync.yaml").read_text())
    fixture["spec"]["postBuild"] = {"substitute": {"KIND_NODE_IP": "172.18.0.2", "KIND_NODE_CIDR": "172.18.0.0/16"}}
    flux_root = ROOT / ".cache/kind-flux-root.json"
    flux_root.parent.mkdir(exist_ok=True)
    flux_root.write_text(json.dumps(fixture))
    run("flux", "build", "kustomization", "flux-system", "--path", "./clusters/kind",
        "--kustomization-file", str(flux_root), "--dry-run", "--strict-substitute", "--in-memory-build")
    # The only kind differences at the resource layer are deliberate overlays.
    for name in golden.keys() - {"cilium", "network", "dns", "storage-classes", "storage-longhorn"}:
        assert golden[name] == kind[name], f"Unexpected profile difference: {name}"
    # Validate the actual kind chart inputs, not only the HelmRelease wrappers.
    cache = ROOT / ".cache/kind-rendered"
    cache.mkdir(parents=True, exist_ok=True)
    release = one(kind["cilium"], "HelmRelease", "kind-gateway")["spec"]
    value_path = cache / "traefik-values.yaml"
    value_path.write_text(yaml.safe_dump(release["values"]))
    manifest = cache / "traefik.yaml"
    manifest.write_text(run("helm", "template", "kind-gateway", "traefik", "--repo", "https://traefik.github.io/charts",
        "--version", release["chart"]["spec"]["version"], "--namespace", "gateway-system",
        "--kube-version", "1.36.4", "--values", str(value_path)))
    rendered = docs(manifest.read_text())
    assert not any(o["kind"] in ("Ingress", "IngressClass", "Gateway", "GatewayClass") for o in rendered)
    deployment = one(rendered, "Deployment", "kind-gateway")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert {p["hostPort"] for p in container["ports"] if "hostPort" in p} == {80, 443}
    assert "--providers.kubernetesgateway" in container["args"]
    assert one(rendered, "Service", "kind-gateway")["spec"]["type"] == "ClusterIP"
    print(run("kubeconform", "-strict", "-summary", "-kubernetes-version", "1.36.4", str(manifest)), end="")
    print("Profile compatibility validation passed.")


if __name__ == "__main__":
    main()

#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ ${1:-} == --help ]]; then
  echo 'Usage: scripts/bootstrap.sh (uses kubectl and the current kubeconfig context)'
  exit 0
fi
[[ $# == 0 ]] || { echo 'Unexpected argument; use --help.' >&2; exit 1; }
command -v kubectl >/dev/null
command -v python3 >/dev/null
echo "Installing storage add-ons into context: $(kubectl config current-context)"
kubectl -n flux-system wait kustomization/cilium kustomization/cert-manager \
  --for=condition=Ready --timeout=5m

# The base repository disables k3s local-storage. Never silently compete with a
# default class added by the administrator afterward.
kubectl get storageclasses -o json | python3 -c '
import json, sys
for sc in json.load(sys.stdin)["items"]:
    annotations = sc["metadata"].get("annotations", {})
    default = any(annotations.get(key) == "true" for key in (
        "storageclass.kubernetes.io/is-default-class",
        "storageclass.beta.kubernetes.io/is-default-class"))
    if default and sc["metadata"]["name"] != "longhorn":
        sys.exit("Another default StorageClass exists: " + sc["metadata"]["name"] +
                 ". Review and remove its default annotation before installing.")
'
kubectl apply -k bootstrap
kubectl -n flux-system wait gitrepository/storage-addons --for=condition=Ready --timeout=5m
kubectl -n flux-system wait kustomization/storage-addons --for=condition=Ready --timeout=5m
kubectl -n flux-system wait kustomizations \
  -l app.kubernetes.io/part-of=storage-addons --for=condition=Ready --timeout=30m
kubectl get storageclasses
kubectl get helmreleases -A
echo 'Storage add-ons reconciled. Backup destinations and schedules are not configured.'

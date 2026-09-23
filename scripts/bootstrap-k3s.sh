#!/usr/bin/env bash
# Run the golden network bootstrap first, followed by the golden storage stack.
set -Eeuo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ ${1:-} == --help ]]; then
  echo 'Usage: sudo scripts/bootstrap-k3s.sh --interface IFACE [golden node options]'
  echo 'Resume an installed server with: sudo scripts/bootstrap-k3s.sh --resume'
  exit 0
fi
if [[ ${1:-} == --resume ]]; then
  [[ $# == 1 ]] || { echo '--resume takes no node options.' >&2; exit 1; }
  "$root/scripts/bootstrap-cluster.sh"
else
  "$root/scripts/bootstrap-hybrid.sh" "$@"
fi
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
bash "$root/storage/scripts/bootstrap.sh"

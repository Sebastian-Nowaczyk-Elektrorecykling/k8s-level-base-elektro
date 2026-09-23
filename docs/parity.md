# Golden baseline and ownership

Network revision: `0552794de323b35f339a41fd5632ccbed9097410`.
Storage revision: `f11ddf0910058ff7c336e52fabb18ea9e838d988`.

The network tree is imported at the root; storage is under `storage/`. The k3s
path remains `clusters/lan`. Network scripts are unchanged, including node roles,
embedded etcd, host prerequisites, disabled k3s components, power policy, NVIDIA
preparation, CA preservation and Cilium Helm adoption. Storage Helm values,
versions and StorageClasses are unchanged in the k3s path.

| Imported file | Adaptation |
| --- | --- |
| Network `README.md` | Combined usage; original retained in `docs/network-baseline.md` |
| Network `.gitignore` | Added `.state/` for kind runtime state |
| `config/cluster.json` | New Git URL; non-Git settings checked against golden JSON |
| `clusters/lan/cluster-settings.yaml` | Regenerated Git identity and configuration hash |
| `storage/bootstrap/source.yaml` | Same `storage-addons` identity; new repository and configured branch |
| `storage/bootstrap/sync.yaml` | Root path relocated to `./storage/clusters/lan` |
| `storage/clusters/lan/infrastructure.yaml` | Child targets relocated under `./storage/infrastructure` |
| `storage/scripts/validate.py` | Resolve combined repository paths; validate kind separately |

Generated `clusters/lan/flux-system` files are included so a fresh checkout can
bootstrap immediately. The Git source identities remain `flux-system` and
`storage-addons`, with independent roots in the same repository. All `dependsOn`
identities remain as before. Storage attaches only after the network is healthy.
Stateful prune/deletion settings are unchanged.

Kind changes are isolated in `clusters/kind`, `storage/clusters/kind`,
`profiles/kind` and `kind/bootstrap`. They replace the Cilium Helm target with a
Traefik Gateway controller and policy CRDs, change GatewayClass's controller,
configure kubeadm CoreDNS, and replace the Longhorn target with kind's built-in
local-path storage. The four application StorageClasses copy `standard`, which
becomes non-default. No k3s leaf resources are changed by these overlays.

The Flux stage named `cilium` checks the kind Gateway HelmRelease and policy
CRDs. The stage named `storage-longhorn` checks the actual local-path provisioner
Deployment. These names preserve application `dependsOn` references. Gateway
health expressions use Traefik's controller name only in kind.

The bootstrap-owned `kind-runtime` supplies the Docker node address to the root
Flux Kustomization. Flux owns `cluster-settings` and passes it to child stages.
Flux and policy CRDs disable substitution of shell-expression examples in
schema descriptions. No production address is used by the kind Gateway.

To upgrade, inspect new upstream commits, update the lock and golden files,
regenerate both profiles, run validation/live tests, and review the k3s diff.
Do not change hashes just to silence parity failures. The original network and
storage documentation is retained as upstream reference; use this README and
the new guides for this repository's entrypoints and paths.

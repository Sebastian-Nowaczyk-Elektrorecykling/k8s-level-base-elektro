# Elektro cluster base: k3s and kind

One repository for the network baseline followed by the storage baseline:

1. [minimum-k8s-net-elektro](https://github.com/Sebastian-Nowaczyk-Elektrorecykling/minimum-k8s-net-elektro/tree/0552794de323b35f339a41fd5632ccbed9097410)
2. [k8s-addon-storage](https://github.com/Sebastian-Nowaczyk-Elektrorecykling/k8s-addon-storage/tree/f11ddf0910058ff7c336e52fabb18ea9e838d988)

**k3s is the golden configuration.** Its host provisioning, network settings,
versions, Gateway listeners, DNS, PKI, Cilium/Hubble and storage releases are
preserved. Only Git identity and storage repository paths change. Imported files
and revisions are recorded in [the lock file](config/upstream-lock.json), with
[automated parity checks](docs/parity.md).

**kind uses its built-in networking and local-path storage on one node.**
Traefik implements Gateway API while retaining the Gateway names used by add-ons.
The four storage names are copies of kind's `standard` class, with `longhorn` as
the sole default. No Cilium or Longhorn controller is installed on kind.

| Contract | k3s | kind |
| --- | --- | --- |
| Kubernetes | `v1.36.4+k3s1` | `v1.36.4`, kind `v0.33.0`, digest-pinned node image |
| Networking | Cilium `1.20.2` | kindnet and kube-proxy |
| Gateway API / Flux | `v1.6.1` / `v2.9.5` | Same |
| Gateway controller | Cilium | Traefik, chart `41.6.0` |
| Gateway identity | `gateway-system/internal`, class `cilium` | Same names; class controller is Traefik |
| HTTPS listeners | `apps-https`, `admin-https`, `management-https`, `testing-https`, `staging-https` | Same |
| Settings | `flux-system/cluster-settings` | Same keys; runtime container IP and network |
| TLS | `internal-ca` issuer; `internal-wildcard-tls` Secret | Same names; separate test CA |
| StorageClasses | `longhorn` (default), `longhorn-cnpg`, `longhorn-garage`, `longhorn-replicated` | Same names, using `rancher.io/local-path` |
| Storage copies | 1 / 1 / 1 / **3** | One local copy for every class |
| Database / S3 / backup controllers | CNPG, Barman, Garage, Velero | Same releases |
| Flux dependencies | `cilium`, `dns`, `pki`, `gateway`, `storage-*` | Same names; check the corresponding kind capabilities |
| Policy / admin UIs | Cilium enforcement, Hubble, Longhorn UI | Policy schemas only; no enforcement or these UIs |
| Edge address | `192.168.2.153`, ports 53/80/443 | Docker node IP inside cluster; loopback ports 1053/80/443 on host |

## Create the golden k3s cluster

Use fresh Debian 13 hosts as described in [host operations](docs/operations.md).
The first node must already have `192.168.2.153/20`, with 53 TCP/UDP and 80/443
free. Replace `eno1` with its LAN interface:

```bash
git clone https://github.com/Sebastian-Nowaczyk-Elektrorecykling/k8s-level-base-elektro.git
cd k8s-level-base-elektro
sudo ./scripts/bootstrap-k3s.sh --interface eno1
```

This calls the original network bootstrap, waits for it, then attaches storage.
It preserves the original GPU and host-power preparation; use
`sudo GPU_VENDOR=none ./scripts/bootstrap-k3s.sh --interface eno1` when GPU
preparation is not wanted. Resume after an interruption with
`sudo ./scripts/bootstrap-k3s.sh --resume`.

For a dedicated first controller, run
`sudo ./scripts/install-controller.sh --bootstrap --interface eno1`, then
`sudo ./scripts/bootstrap-k3s.sh --resume`. Ordinary services require a worker or
hybrid. The original `install-controller.sh`, `install-hybrid.sh` and
`install-worker.sh` remain the node-join entrypoints, with `--interface` and
`--token-file` as documented in [operations](docs/operations.md).

Configure LAN DNS and client trust exactly as before. The public CA is
`/etc/elektro/secrets/ca.crt`. Three eligible storage nodes are still required for
`longhorn-replicated` on k3s. No backup destination or schedule is created.

## Create the kind test cluster

Use local Docker with Linux containers: rootful Docker on Linux or Docker
Desktop on macOS/Windows (run these commands in a Linux/WSL shell on Windows).
See [kind prerequisites and access](docs/kind.md).

Install Python 3.11+, Git, OpenSSL, Docker, kind `v0.33.0` and kubectl `v1.36.4`.
From a clean, published checkout:

```bash
python3 scripts/bootstrap-kind.py
export KUBECONFIG="$PWD/.state/elektro-test/config.kubeconfig"
python3 scripts/check-base.py --profile kind
python3 scripts/smoke-kind.py
```

The script creates one schedulable control-plane node using the standard kind
image, prepares DNS and a test CA, then installs Flux and waits for the network
and storage layers in order. Rerunning resumes the same cluster. It uses a
dedicated kubeconfig without changing your default context.

Keep default ports 80/443 when testing SSO redirects. Host access can use explicit
loopback hosts entries, for example `127.0.0.1 auth.internal home.internal`.
Cluster DNS resolves all five private wildcard families to the kind node IP;
pods can reach the HTTPS Gateway at the normal URLs. Trust only the public
`.state/elektro-test/ca.crt` in the browser used for this test. See the kind guide
for wildcard DNS, other application names, port changes and cleanup.

## Attach other Flux repositories

Use each downstream repository's normal setup command with this cluster's
kubeconfig. Do not install either old baseline again: this repository already
owns their resource identities.

```bash
# k3s server: export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
# kind: export KUBECONFIG=/absolute/path/to/.state/elektro-test/config.kubeconfig
python3 scripts/check-base.py --profile kind   # or --profile k3s
# Then run the add-on's own bootstrap/setup script in its checkout.
```

The inspected `k8s-addon-authn`, `k8s-addon-ai` and `k8s-addon-github-runner`
dependencies are documented in [compatibility](docs/compatibility.md). Their
paths such as `clusters/lan` remain valid on kind: that is a path in the add-on's
source, not a restriction to a k3s cluster. Add-ons still need credentials and
application configuration. GPU profiles need hardware; multi-node workloads
need their own single-node application configuration.

## Configuration and validation

The golden settings are in `config/cluster.json`; the kind name, image and host
ports are in `config/kind.json`. To change Git URL/branch for a fork:

```bash
python3 scripts/config.py generate
python3 scripts/generate-kind.py
# Commit and push before either bootstrap. Flux deploys that exact branch.
```

Changing golden network/runtime settings is an intentional divergence and fails
the parity check until the documented baseline and its lock are updated. Kind
overlays never rewrite the k3s configuration. Runtime IPs, kubeconfigs, private
keys and test data live in ignored `.state/`, outside Git reconciliation.

See [validation and live acceptance](docs/testing.md). Static validation renders
both profiles, charts, schemas and dependency graphs. A separate kind smoke
workflow tests PVC remounts, DNS, HTTPS, policy API compatibility and CNPG SQL on Linux Docker.
Physical k3s provisioning and LAN/GPU behavior still require host acceptance.

This repository bootstraps **new clusters**. It does not automatically migrate
ownership or data from an existing cluster managed by the original two sources.

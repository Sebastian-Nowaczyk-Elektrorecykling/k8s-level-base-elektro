# Single-node kind test cluster

The node uses kindnet, kube-proxy and kind's local-path provisioner. Traefik
provides Gateway API. cert-manager, Flux, CNPG, Barman, Garage and Velero keep
the baseline releases. There is no custom node image, Cilium agent, Longhorn,
iSCSI setup or host kernel modification.

Requirements:

- Local Docker running Linux containers: rootful Docker on Linux or Docker
  Desktop. On Windows, run the bootstrap in WSL with Docker Desktop integration.
  Remote daemons and rootless Docker are outside this bootstrap's support.
- Python 3.11+, Git, OpenSSL, kubectl `v1.36.4`, kind `v0.33.0`.
- Outbound access to GitHub, chart repositories, image registries and the
  configured upstream DNS servers.
- Loopback 80/443 TCP and 1053 TCP/UDP free by default.
- Plan for 4 CPUs, 8 GiB available RAM and 30 GiB free disk for the base and
  smoke tests; applications need additional capacity. These are estimates.

CI exercises Linux Docker. Docker Desktop uses the same standard kind image and
port mappings, but its host setup has not been tested by this repository's CI.
Allow Docker Desktop to share the checkout directory. `.state/<name>/data` is
mounted at `/var/local-path-provisioner` inside the node, where all alias classes
store their data. Bootstrap verifies the standard provisioner and its path
before proceeding.

Before installing Flux, bootstrap configures CoreDNS and checks public DNS from
a pod. This avoids making GitHub resolution depend on fetching the repository
that configures DNS.

```bash
python3 scripts/bootstrap-kind.py
export KUBECONFIG="$PWD/.state/elektro-test/config.kubeconfig"
kubectl get nodes,storageclasses
kubectl -n flux-system get kustomizations
python3 scripts/smoke-kind.py
```

Pass `--name another-test` for a separate cluster/state directory. Change ports
in `config/kind.json`, regenerate with `python3 scripts/generate-kind.py`, commit
and push before creating a second simultaneous cluster. Existing clusters with
different configuration are not adopted. Keep 80/443 for authentication add-ons'
unmodified SSO redirects. With a different HTTPS port, pass it to
`scripts/smoke-kind.py --https-port PORT` too.

## Browser, DNS and TLS

Explicit host entries map the applications under test to loopback:

```text
127.0.0.1 auth.internal home.internal s3.internal
127.0.0.1 authentik.admin.internal openfga.admin.internal
127.0.0.1 chat.internal llm.internal flows.internal notebook.internal mlflow.internal
```

Hosts files do not support wildcards. A local resolver can instead answer the
private suffix with `127.0.0.1`. On native Linux, forwarding `internal` to
`127.0.0.1:1053` also works: that DNS server returns the Docker node IP. Docker
Desktop does not normally expose that node IP to the host, so use loopback host
entries or a resolver returning loopback there. Keep test DNS local to your
machine. Pods use Kubernetes DNS, which forwards the private suffix to
`lan-dns`; their normal add-on URLs resolve to the node and reach the Gateway.

```bash
dig @127.0.0.1 -p 1053 auth.internal
curl --noproxy '*' --cacert .state/elektro-test/ca.crt \
  --resolve auth.internal:443:127.0.0.1 https://auth.internal/
```

That URL responds after the authentication add-on is installed. Import the
public `.state/elektro-test/ca.crt` into the test browser's trust store. Keep the
private `ca.key` out of applications and Git. Reruns preserve the in-cluster CA;
an incomplete local CA or API read failure stops without rotating it.

## What applications can expect

Ordinary filesystem `ReadWriteOnce` PVCs and HTTPRoutes use the same names as
k3s. `longhorn`, `longhorn-cnpg`, `longhorn-garage` and `longhorn-replicated` all
use `rancher.io/local-path`, `WaitForFirstConsumer` and `Delete`. `longhorn` is
the sole default; `standard` remains available as non-default. A PVC binds when
its first consumer is scheduled. kind's provisioner is local-path, not a
Longhorn CSI driver: it does not emulate replication, RWX across nodes, volume
expansion, block volumes, CSI snapshots, or Longhorn APIs. Requested capacity
is not a disk quota.

The GatewayClass remains named `cilium` for app compatibility, with controller
`traefik.io/gateway-controller`. The Gateway, six listeners, namespace selectors,
TLS names and private DNS zones are unchanged. Traefik-specific CRDs and the
legacy Ingress provider are disabled.

Real Cilium policy CRDs are registered so existing app repositories containing
`CiliumNetworkPolicy` or `CiliumClusterwideNetworkPolicy` can reconcile.
**Policies are not enforced.** This profile tests application behavior, not
Cilium security or network isolation. Hubble and Longhorn UIs are absent, so
admin links/proxies to those services will not work. See the complete
[compatibility contract](compatibility.md).

CNPG, Barman, Garage and Velero are real deployments. Backup destinations and
schedules remain unconfigured, as in the golden baseline. GPU resources and
multiple failure domains are not emulated.

## Cleanup

```bash
kind get clusters
kind delete cluster --name elektro-test
```

This deletes the control plane and its metadata. Host data remains under
`.state/<name>/data`, but is not an automatic recovery mechanism. Archive or
remove the old state directory deliberately before creating a new cluster with
the same name. Removing it also removes test volumes and the CA key. Remove the
test CA from your trust store when retiring the environment. A cluster created
with the earlier Cilium/Longhorn kind profile must be recreated; in-place CNI
and storage migration is not supported.

References: [kind configuration](https://kind.sigs.k8s.io/docs/user/configuration/),
[kind's built-in storage](https://github.com/kubernetes-sigs/kind/blob/v0.33.0/pkg/build/nodeimage/const_storage.go),
[Traefik Gateway API](https://doc.traefik.io/traefik/reference/install-configuration/providers/kubernetes/kubernetes-gateway/).

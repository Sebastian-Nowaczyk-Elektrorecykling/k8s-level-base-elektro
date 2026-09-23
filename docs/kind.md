# kind on a Linux test host

The single node runs real Cilium, cert-manager, Longhorn, CNPG, Barman, Garage
and Velero. Budget at least 4 CPUs, 12–16 GiB available RAM and 60 GiB free disk
for the base plus smoke tests; applications need additional capacity. These are
planning estimates, not measured limits.

Requirements:

- Local, rootful Docker on native Linux with cgroup v2 and private container
  cgroup namespaces. Do not use a production host already running Cilium.
- Kernel modules `iscsi_tcp`, `nfs`, `dm_crypt`, `xt_socket`, `xt_TPROXY`, `xt_mark`
  and `xt_CT`. Install the modules package matching `uname -r` if required;
  on Ubuntu this can be `linux-modules-extra-$(uname -r)`.
- `.state/<name>/data` on ext4 or XFS. It is bind-mounted into the node at
  `/var/lib/longhorn`; bootstrap checks the filesystem. Longhorn's storage reserve
  still applies, so keep ample free space.
- Python 3.11+, Git, OpenSSL, kubectl `v1.36.4`, Helm `v3.19.0`, kind `v0.33.0`.
- Outbound access to apt, GitHub, chart repositories and image registries.
- Loopback 80/443 TCP and 1053 TCP/UDP free by default.

The Dockerfile extends a digest-pinned node image with iSCSI, NFS and filesystem
tools. Bootstrap loads host kernel modules through the privileged node, starts
iscsid inside it, and checks Cilium cgroup isolation. It does not run k3s host
preparation or the GPU/power-policy scripts. Docker Desktop, rootless Docker and
remote Docker daemons are outside this profile's supported setup.

```bash
python3 scripts/bootstrap-kind.py
export KUBECONFIG="$PWD/.state/elektro-test/config.kubeconfig"
kubectl get nodes,storageclasses
kubectl -n flux-system get kustomizations
```

Pass `--name another-test` for a separate cluster/state directory. Change ports
in `config/kind.json`, commit and push before creating a second simultaneous
cluster. Existing clusters with different configuration are not adopted. Keep
80/443 for the authentication add-on's unmodified SSO redirects. With a different
HTTPS port, pass it to `scripts/smoke-kind.py --https-port PORT` as well.

## Browser, DNS and TLS

Explicit host entries can map tested names to loopback:

```text
127.0.0.1 auth.internal home.internal s3.internal
127.0.0.1 authentik.admin.internal hubble.admin.internal longhorn.admin.internal openfga.admin.internal
127.0.0.1 chat.internal llm.internal flows.internal notebook.internal mlflow.internal
```

Hosts files do not support wildcards. For arbitrary names, use a local DNS
resolver forwarding `internal` to `127.0.0.1:1053`, or to the Docker node IP on
port 53. DNS returns the node IP, reachable from a native Linux Docker host.
Do not forward the production LAN's `internal` suffix to the test cluster.
Pods use Kubernetes DNS, which forwards the private suffix to `lan-dns`; their
normal add-on URLs work without host entries.

```bash
dig @127.0.0.1 -p 1053 auth.internal
kubectl -n flux-system get cm cluster-settings -o jsonpath='{.data.API_IP}'
curl --noproxy '*' --cacert .state/elektro-test/ca.crt \
  --resolve auth.internal:443:127.0.0.1 https://auth.internal/
```

That URL responds after the authentication add-on is installed. Import the
public `.state/elektro-test/ca.crt` into your test browser's trust store. Never
copy `ca.key` into an application or Git. Reruns preserve the in-cluster CA;
an incomplete local CA or API read failure stops without rotating it.

## Lifecycle and limits

There is one storage copy and failure domain. `longhorn-replicated` has one
replica on kind; it is not a redundancy test. CSI, PVC expansion and Longhorn
APIs are real. Backups are unconfigured, as in the golden storage repository.
GPU resources are not emulated.

To destroy a disposable cluster, verify the name and run:

```bash
kind get clusters
kind delete cluster --name elektro-test
```

This deletes the control plane and its metadata. Host data remains under
`.state/<name>/data`, but is not an automatic recovery mechanism. Archive or
remove the old state directory deliberately before starting a fresh cluster
with the same name. Removing it also removes test volumes and the CA key.
Remove the test CA from your trust store when retiring the environment.

References: [kind configuration](https://kind.sigs.k8s.io/docs/user/configuration/),
[node image digests](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0),
[Cilium on kind](https://docs.cilium.io/en/stable/installation/kind/),
[Gateway prerequisites](https://docs.cilium.io/en/stable/network/servicemesh/gateway-api/gateway-api/),
[Longhorn requirements](https://longhorn.io/docs/1.12.1/deploy/install/).

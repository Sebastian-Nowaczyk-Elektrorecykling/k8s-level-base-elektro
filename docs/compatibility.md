# Downstream compatibility

Both profiles preserve application names. Their implementations differ:

| Contract | Both profiles | kind implementation / limit |
| --- | --- | --- |
| Flux dependencies | `flux-system/{cilium,cert-manager,network,dns,pki,gateway,apps,storage-namespaces,storage-sources,storage-longhorn,storage-classes,storage-cnpg,storage-barman,storage-garage,storage-velero}` | Same names; `cilium` reconciles Gateway/policy schemas and `storage-longhorn` checks local-path readiness |
| Domains / DNS IP | `flux-system/cluster-settings`: `DOMAIN`, `ADMIN_DOMAIN`, `MANAGEMENT_DOMAIN`, `TESTING_DOMAIN`, `STAGING_DOMAIN`, `CLUSTER_DNS_IP` | Same keys, runtime node IP |
| Gateway | `gateway-system/internal`, `GatewayClass/cilium`, all six listeners including `http` | Traefik Gateway controller |
| Route selectors | `elektro.internal/route-scope`: `applications`, `administration`, `management` | Same |
| TLS | `ClusterIssuer/internal-ca`, `cert-manager/internal-ca`, `gateway-system/internal-wildcard-tls` | Same names, independent test CA |
| Storage names | `longhorn` (sole default), `longhorn-cnpg`, `longhorn-garage`, `longhorn-replicated` | Copies of kind's `standard` local-path class; one local copy, filesystem RWO |
| Garage S3 | `garage.garage.svc.cluster.local:3900`, region `garage` | Same real deployment |
| Database / backup APIs | CNPG, Barman Cloud, Velero | Same real controllers; configure credentials/destinations as usual |
| Cilium policies | `CiliumNetworkPolicy`, `CiliumClusterwideNetworkPolicy` schemas | Accepted for Flux compatibility, **not enforced** |

Hubble and Longhorn services/UIs, Longhorn CRDs, Cilium observability, policy
enforcement, CSI-specific functionality and multi-node storage are available
only on k3s. Admin links or proxy targets to Hubble/Longhorn are consequently
unavailable on kind. Standard HTTPRoutes and filesystem RWO PVCs need no base
name changes. Applications inspecting a StorageClass's provisioner or relying
on these implementation-specific features can distinguish the profiles.

Inspected add-on revisions:

- `k8s-addon-authn`: `854cf2647003cd29e82a54db42985987ef80920f`: preflight,
  DNS/TLS, CNPG, storage names and Garage contracts retained. Its Hubble and
  Longhorn admin proxy targets are absent on kind.
- `k8s-addon-ai`: `3ebf8f1c2f845170af9b28114e083d6be49a8f03`: base dependencies,
  CPU profile, Gateway routes, policy schemas and storage names retained; policy
  enforcement is not tested on kind.
- `k8s-addon-github-runner`: `b85726c44efbca7f47e687ab2f1c55ebd5637925`:
  `longhorn` class and generated Gateway references retained.

This is interface inspection, not a claim of end-to-end validation of each app,
SSO flow, model download or runner job. Use each repository's setup script for
credentials and Flux attachment. Do not install the old network/storage sources
over this base. `clusters/lan` in an add-on is a source path and can run on kind.

Applications with hard anti-affinity, multiple required nodes, GPU resources,
specific node selectors or excessive requests need their own test profile.
Backup tests also need real provider credentials, buckets and schedules.

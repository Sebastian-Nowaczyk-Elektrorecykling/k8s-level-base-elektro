# Downstream compatibility

Both profiles expose these identities:

| Contract | Resource |
| --- | --- |
| Flux dependencies | `flux-system/{cilium,cert-manager,network,dns,pki,gateway,apps,storage-namespaces,storage-sources,storage-longhorn,storage-classes,storage-cnpg,storage-barman,storage-garage,storage-velero}` |
| Domains/DNS IP | `flux-system/cluster-settings`: `DOMAIN`, `ADMIN_DOMAIN`, `MANAGEMENT_DOMAIN`, `TESTING_DOMAIN`, `STAGING_DOMAIN`, `CLUSTER_DNS_IP` |
| Gateway | `gateway-system/internal`, `GatewayClass/cilium`, all six listeners including `http` |
| Route selectors | `elektro.internal/route-scope`: `applications`, `administration`, `management` |
| TLS | `ClusterIssuer/internal-ca`, `cert-manager/internal-ca`, `gateway-system/internal-wildcard-tls` |
| Storage | `longhorn`, `longhorn-cnpg`, `longhorn-garage`, `longhorn-replicated`; `driver.longhorn.io` |
| Longhorn UI | `longhorn-frontend.longhorn-system.svc.cluster.local:80` |
| Hubble UI | `hubble-ui.kube-system.svc.cluster.local:80` |
| Garage S3 | `garage.garage.svc.cluster.local:3900`, region `garage` |
| Database/backup APIs | CNPG, Barman Cloud, Velero and Longhorn CRDs |
| Policy | Actual Cilium and the Gateway's reserved `ingress` identity |

Inspected add-on revisions:

- `k8s-addon-authn`: `854cf2647003cd29e82a54db42985987ef80920f`: preflight,
  DNS/TLS, CNPG, Longhorn and Garage upstream contracts retained.
- `k8s-addon-ai`: `3ebf8f1c2f845170af9b28114e083d6be49a8f03`: base dependencies,
  CPU profile, Gateway routes, policies and storage classes retained.
- `k8s-addon-github-runner`: `b85726c44efbca7f47e687ab2f1c55ebd5637925`:
  `longhorn` class and generated Gateway references retained.

This is interface inspection, not a claim of end-to-end validation of each app,
SSO flow, model download or runner job. Use each repository's setup script for
credentials and Flux attachment. Do not install the old network/storage sources
over this base. `clusters/lan` in an add-on is a source path and can run on kind.

Applications with hard anti-affinity, multiple required nodes, GPU resources,
specific node selectors or excessive requests need their own test profile.
Backup tests also need real provider credentials, buckets and schedules.

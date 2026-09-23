# Kubernetes storage add-ons

FluxCD storage and backup operators for a cluster created by
[minimum-k8s-net-elektro](https://github.com/Sebastian-Nowaczyk-Elektrorecykling/minimum-k8s-net-elektro).
This repository attaches to the existing Flux installation as a second Git
source. No changes to the base repository are required.

| Component | Pinned version | Configuration |
| --- | --- | --- |
| Longhorn | chart/application `1.12.1` | V1 engine; default `longhorn` class with **1 replica** |
| Additional StorageClass | `longhorn-replicated` | Same volume settings with **3 replicas**, non-default |
| CNPG StorageClass | `longhorn-cnpg` | Same single-replica settings as `longhorn`; selected by convention |
| Garage StorageClass | `longhorn-garage` | Same single-replica settings as `longhorn`; selected in Garage Helm values |
| CloudNativePG | chart `0.29.0`, operator `1.30.0` | Cluster-wide operator in `cnpg-system` |
| Barman Cloud plugin | chart `0.8.0`, plugin `0.15.0` | Beside CNPG; TLS issued by existing cert-manager |
| Garage | chart `0.10.2`, application `2.4.1` | One node, automatic layout, persistent metadata and S3 data |
| Velero | chart `12.2.0`, application `1.18.2` | Server and CRDs; backup configuration deferred |

**No backups run after installation.** Backup destinations, credentials, buckets,
schedules, retention policies, CNPG ObjectStores, and Longhorn backup targets
are not configured. CNPG installs the operator, not an application PostgreSQL
cluster. An optional [example](examples/postgres.yaml) is outside Flux's paths.

## Prerequisites

- A healthy base cluster with `cilium` and `cert-manager` Flux Kustomizations
  ready. Inspected base revision:
  [`ed17d4a`](https://github.com/Sebastian-Nowaczyk-Elektrorecykling/minimum-k8s-net-elektro/commit/ed17d4a7bf55f766c112d7867bbc064e4d4060e2),
  with k3s `v1.36.4+k3s1`, Flux `v2.9.5`, cert-manager `v1.21.2`.
- At least one schedulable worker or hybrid with capacity for these services.
  Dedicated controllers keep their base `NoSchedule` taint; add-ons do not add
  tolerations to place ordinary workloads on them.
- Longhorn prerequisites on every workload/storage node: active `iscsid`,
  `iscsi_tcp`, NFS clients, mount propagation, and an ext4/XFS filesystem at
  `/var/lib/longhorn`. The base host-preparation script supplies the software.
  Check free space and any `multipathd` exclusions before installation.
- **Three eligible storage nodes with free space** before using
  `longhorn-replicated`. Replica node anti-affinity is enforced; initial volume
  creation with fewer replicas is disabled.
- `kubectl`, Python 3, and a cluster-admin kubeconfig. On a base k3s host, use
  `KUBECONFIG=/etc/rancher/k3s/k3s.yaml` with permission to read that file.
- Outbound access to GitHub, the chart repositories, and upstream registries.
  The current public Git repository needs no credentials.

The base disables k3s local-storage. If another default StorageClass was added
afterward, deliberately remove its default annotation first. The bootstrap
script refuses to create two defaults.

## Install

From the published `main` branch:

```bash
git clone https://github.com/Sebastian-Nowaczyk-Elektrorecykling/k8s-addon-storage.git
cd k8s-addon-storage
kubectl config current-context
bash scripts/bootstrap.sh
```

The script checks dependencies, applies the source and root Kustomization, and
waits for all add-ons. It can be rerun. It does not bootstrap Flux again or change
the base `flux-system` Git source. To apply only the attachment after checking
the prerequisites, use `kubectl apply -k bootstrap`.

Flux reconciles `clusters/lan`, including the bootstrap source and sync files.
Commit and push changes to `main` before expecting them on the cluster. For a
fork/private repository, change `bootstrap/source.yaml`, commit it, and add a
`secretRef` pointing to a read-only Git credential where needed.

## Reconciliation order

| Flux Kustomization | Prerequisites |
| --- | --- |
| `storage-namespaces`, `storage-sources` | Existing Flux |
| `storage-longhorn` | Namespaces, sources, base `cilium` |
| `storage-classes` | Ready Longhorn Helm release |
| `storage-cnpg` | Namespaces and sources |
| `storage-barman` | Ready CNPG and base `cert-manager` |
| `storage-garage` | StorageClasses, namespaces, sources |
| `storage-velero` | Namespaces and sources |

Children wait for their resources. Helm releases use retries and drift
correction, with CRD upgrade handling where applicable. Garage's upstream chart
comes from the official GitHub mirror at commit
`268334bd2530fa99f8b06c7383b2e9f776691edd` (`v2.4.1`). CNPG and Barman share a
namespace as required by plugin discovery.

## Storage and access

All four classes use `driver.longhorn.io`, ext4, V1, `Immediate` binding, `Delete`
reclaim policy, volume expansion, and disabled data locality. The purpose-specific
classes have the same volume settings as `longhorn`, including one replica;
`longhorn-replicated` has three replicas. Flux owns all four; chart-created
classes are disabled. Longhorn's global replica default is also one.

| StorageClass | Replicas | Default usage |
| --- | --- | --- |
| `longhorn` | 1 | Sole Kubernetes cluster-wide default |
| `longhorn-cnpg` | 1 | CNPG convention; explicitly set on each database Cluster |
| `longhorn-garage` | 1 | Configured for Garage metadata and object-data PVCs |
| `longhorn-replicated` | 3 | Explicit opt-in for replicated volumes |

The StorageClass default annotation applies cluster-wide, so both new classes
set it to `"false"`. Garage selects `longhorn-garage` through
`persistence.meta.storageClass` and `persistence.data.storageClass` in its Helm
values. The pinned CNPG operator has no operator-wide storage-class default:
follow the convention by setting `spec.storage.storageClass: longhorn-cnpg` on
each CNPG `Cluster`, as in [the example](examples/postgres.yaml). If you configure
separate WAL or tablespace volumes, explicitly select `longhorn-cnpg` in their
storage settings too. Omitting a class uses the Kubernetes default `longhorn`.
See the pinned [CNPG storage documentation](https://cloudnative-pg.io/docs/1.30/storage/)
and [operator configuration options](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.0/docs/src/operator_conf.md).

Set `spec.storageClassName: longhorn` on a PVC, or omit it to use the default.
Set `spec.storageClassName: longhorn-replicated` to request three replicas.
**One replica does not protect against storage-node/disk loss.** Three replicas
are not backups. Deleting a PVC deletes its volume with any of these classes. Changing
defaults does not migrate existing PVCs or change existing volume replicas.

Longhorn uses `/var/lib/longhorn` and the base kubelet path `/var/lib/kubelet`.
Review its disks and scheduling before storing data. Access the internal UI:

```bash
kubectl -n longhorn-system port-forward svc/longhorn-frontend 8080:80
# Open http://localhost:8080
```

Garage's internal path-style S3 endpoint is
**`http://garage.garage.svc.cluster.local:3900`**, region **`garage`**.
Clients require an explicitly provisioned bucket and access key; none are
created here. No Ingress, Gateway route, or external administrative endpoint is
installed. Garage's `--single-node` flag automatically initializes its layout.
Metadata uses a `1Gi` PVC and objects a `20Gi` PVC, both on `longhorn-garage`. Helm
generates and preserves the RPC Secret; no secret values are committed.

This Garage baseline has no node or volume redundancy. Multi-node Garage
requires a planned layout/replication migration, not just a replica-count change.
In-cluster Garage shares the cluster's failure domain; future disaster recovery
should account for an independent data copy. See [operations](docs/operations.md)
for capacity changes, acceptance checks, and maintenance.

## Verify

```bash
kubectl -n flux-system get kustomizations -l app.kubernetes.io/part-of=storage-addons
kubectl get helmreleases -A
kubectl get storageclasses
kubectl -n cnpg-system get deployments,certificates
kubectl -n garage get pods,pvc
kubectl -n garage exec garage-0 -c garage -- /garage status
kubectl -n velero get pods
kubectl -n velero get backupstoragelocations,volumesnapshotlocations,schedules
kubectl get objectstores.barmancloud.cnpg.io -A
```

All storage Kustomizations and Helm releases should be ready. `longhorn` should
be the only default class, and Garage should have a healthy one-node layout and
bound PVCs. On a fresh install the final two commands should list no backup
configuration. Velero checks expecting a default BackupStorageLocation will not
pass until backups are configured; use Deployment readiness at this stage.

## Validate and upgrade

Use Python 3.11+, Helm 3.19.0, Kustomize 5.7.1, and kubeconform 0.7.0:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python3 scripts/validate.py
```

Validation builds every Flux target, renders all pinned charts, checks deployment
invariants, and validates Kubernetes/Flux/custom resources against schemas. It
downloads charts and schemas into `.cache/` and never contacts a cluster. The
same checks run in GitHub Actions. Live storage attachment and failure recovery
still require the [acceptance checks](docs/operations.md).

For upgrades, edit the pinned chart versions; update Garage's source commit and
image together. Read upstream release/upgrade notes, validate, commit, and push.
Stateful infrastructure uses `prune: false`, and deleting the root or child Flux
Kustomizations orphans resources. Deliberate removal needs a data-removal plan.

## Reserved for later

Velero provider plugins, credentials, storage/snapshot locations, node agent, CSI
snapshot integration, schedules, retention, and restore testing remain to be
configured. CNPG's Barman ObjectStore, per-cluster plugin configuration, WAL
archiving, and backup schedules remain unconfigured, as do Longhorn backup
targets and recurring jobs. Installed operators are not yet a recovery solution.

Upstream: [Longhorn](https://longhorn.io/docs/1.12.1/),
[CNPG charts](https://github.com/cloudnative-pg/charts),
[Barman installation](https://cloudnative-pg.io/plugin-barman-cloud/docs/installation/),
[Garage Kubernetes](https://garagehq.deuxfleurs.fr/documentation/cookbook/kubernetes/),
[Velero chart](https://github.com/vmware-tanzu/helm-charts/tree/velero-12.2.0/charts/velero).

# Operations

## First-run acceptance

Run these checks on the target cluster before relying on the storage. Manifest
rendering does not test iSCSI, mount propagation, physical disks, or real I/O.

1. Confirm all eight child Flux Kustomizations and all five Helm releases are
   ready. Inspect a failing dependency before its dependants:

   ```bash
   kubectl -n flux-system get kustomizations -l app.kubernetes.io/part-of=storage-addons
   kubectl -n flux-system describe kustomization storage-garage
   kubectl -n longhorn-system describe helmrelease longhorn
   ```

2. Check all four StorageClasses and Longhorn nodes/disks:

   ```bash
   kubectl get sc longhorn longhorn-cnpg longhorn-garage longhorn-replicated -o yaml
   kubectl -n longhorn-system get nodes.longhorn.io
   kubectl -n longhorn-system get volumes.longhorn.io
   ```

   Create a disposable PVC and pod using `longhorn`, write a known value,
   restart the pod, and confirm persistence. Delete the pod/PVC afterward.
   Repeat with `longhorn-replicated` after three nodes are eligible and confirm
   three healthy replicas on separate nodes. Do not delete real application PVCs.
   `longhorn` must be the only cluster-wide default; `longhorn-cnpg` and
   `longhorn-garage` must have the same single-replica settings.

3. Verify Garage's automatic layout and storage:

   ```bash
   kubectl -n garage rollout status statefulset/garage
   kubectl -n garage get pvc
   kubectl -n garage exec garage-0 -c garage -- /garage status
   kubectl -n garage exec garage-0 -c garage -- /garage layout show
   kubectl -n garage port-forward svc/garage 3900:3900
   ```

   Both Garage PVCs should show `longhorn-garage` as their StorageClass.

   Provision a disposable bucket/key using the
   [Garage CLI](https://garagehq.deuxfleurs.fr/documentation/quick-start/).
   Test an object PUT/GET/DELETE with an S3 client at `http://localhost:3900`,
   path-style addressing, region `garage`. Then remove the disposable bucket
   and key. Key-creation output contains credentials; keep it out of Git and logs.
   Preserve the `garage-rpc-secret` Secret along with the data for future recovery.

4. Confirm the CNPG and Barman Deployments, plugin Certificates, and endpoint:

   ```bash
   kubectl -n cnpg-system get deployments,certificates
   kubectl -n cnpg-system get endpointslices -l kubernetes.io/service-name=barman-cloud
   kubectl -n cnpg-system logs deploy/cnpg-controller-manager --tail=100
   ```

   Optionally test CNPG with the disposable example (it is not part of Flux):

   ```bash
   kubectl apply -f examples/postgres.yaml
   kubectl -n databases wait cluster/example --for=condition=Ready --timeout=10m
   kubectl -n databases get cluster,pods,pvc
   # Only when there is no example data you need:
   kubectl delete -f examples/postgres.yaml
   ```

   The example PVC should use `longhorn-cnpg`. This class is selected by
   convention in each CNPG Cluster; the operator does not apply it automatically.

5. Check the Velero Deployment is available and no backup location or schedule
   resources exist. No backup/restore success is claimed before configuration.

## Capacity and replication

Garage starts with `1Gi` metadata and `20Gi` object data. Change the sizes in
`infrastructure/garage/release.yaml` before first installation if needed.
Longhorn thin provisioning does not guarantee physical capacity; monitor free
space and leave room for metadata, snapshots, and replica rebuilding.

After installation, growing Garage also requires expanding its existing PVCs.
StatefulSet volumeClaimTemplates cannot be updated in place. Follow the upstream
[PVC expansion procedure](https://garagehq.deuxfleurs.fr/documentation/cookbook/kubernetes/#increase-pvc-size-on-running-garage-instances)
with a maintenance plan. Do not force Helm replacement or delete PVCs to bypass
this restriction. PVCs cannot be shrunk.

For multi-node Garage, plan layout, failure domains, replication, capacity, and
data movement before disabling single-node mode. Increasing replicaCount alone
has no effect while singleNode is enabled. Three Garage replicas on
three-replica Longhorn volumes can require nine physical object copies.
StorageClass parameter changes cannot be applied in place; plan migrations for
existing PVCs. Global replica defaults also do not alter existing volumes.

## Pause or remove

Suspend the root and its children to pause Git reconciliation:

```bash
kubectl -n flux-system get kustomizations \
  -l app.kubernetes.io/part-of=storage-addons -o name |
  xargs -r -n 1 kubectl -n flux-system patch --type=merge -p '{"spec":{"suspend":true}}'
```

Suspend the five HelmReleases separately if Helm reconciliation must also stop.
The base repository continues to reconcile independently. Commit any intended
long-term suspension to Git so it survives resuming the parent.

`deletionPolicy: Orphan` prevents deleting the root/children from uninstalling
their inventories. Most stateful components also use `prune: false`, protecting
against removal from Git. Orphaned children and HelmReleases can still reconcile.
For deliberate uninstallation, stop writers, secure data, then follow upstream
removal procedures. Longhorn's deletion-confirmation safeguard stays at its
upstream default. Deleting a namespace or PVC can destroy data even with Flux
pruning disabled; there is no destructive uninstall script here.

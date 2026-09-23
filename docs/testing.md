# Validation and acceptance

Install Helm `3.19.0`, Kustomize `5.7.1`, kubeconform `0.7.0` and ShellCheck:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r tests/requirements.txt
python3 -m unittest discover -s tests -v
shellcheck -x -P SCRIPTDIR scripts/*.sh scripts/lib/*.sh storage/scripts/*.sh
python3 scripts/validate.py
python3 storage/scripts/validate.py
python3 scripts/validate-profiles.py
```

Inherited validators render pinned charts and check schemas. Profile validation
checks file hashes, golden settings, all Flux paths/dependencies, substitutions,
disjoint ownership, Gateway names/listeners and actual CSI classes. Bootstrap
tests cover CA error handling and stale readiness. Set `COREDNS` to the pinned
CoreDNS binary for the inherited DNS protocol tests.

After kind bootstrap on Linux Docker:

```bash
export KUBECONFIG="$PWD/.state/elektro-test/config.kubeconfig"
python3 scripts/check-base.py --profile kind
python3 scripts/smoke-kind.py
```

The smoke test creates a random disposable namespace and removes only that
namespace afterward. It writes to all four StorageClasses, deletes the writer
pod, remounts and verifies the data. It checks private/cluster DNS, HTTPS with
CA verification through `testing-https` and Cilium ingress policy, then creates
a CNPG database and executes SQL. It requires free storage for the test PVCs.

Static CI runs on pushes and pull requests. Kind smoke runs on `main` pushes
and can be dispatched against `main`. It uses a fresh Linux runner, not a user
cluster. A run must pass before claiming live kind validation. Bootstrap requires
the checkout to match the configured branch, so PR branches get static checks.

To test a separate branch on your own host, change `git_branch` in
`config/cluster.json`, run both generators, commit and push before bootstrap.
Restore `main` and regenerate before merging. Keep the clean-checkout guard.

For k3s, perform the original [host checks](operations.md#first-run-acceptance)
and [storage checks](../storage/docs/operations.md), followed by:

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
python3 scripts/check-base.py --profile k3s
```

Check physical LAN DNS/TLS, host reboot/join behavior, GPU prerequisites where
used, and three-node placement for `longhorn-replicated`. Container tests cannot
establish these host-specific properties.

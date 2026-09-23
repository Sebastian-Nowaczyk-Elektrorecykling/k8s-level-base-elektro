import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap_kind", ROOT / "scripts/bootstrap-kind.py")
kind = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kind)


class KindBootstrapTests(unittest.TestCase):
    def test_nonroot_can_delegate_privileged_port_check_to_docker(self):
        with patch.object(kind.socket, "socket") as socket, patch.object(kind.os, "geteuid", return_value=1000), \
                patch.object(kind.Path, "read_text", return_value="1024"):
            socket.return_value.__enter__.return_value.bind.side_effect = PermissionError()
            kind.check_host_ports([{"protocol": "TCP", "hostPort": 443}])
            with self.assertRaises(PermissionError):
                kind.check_host_ports([{"protocol": "TCP", "hostPort": 8443}])

    def test_port_in_use_is_not_ignored(self):
        with patch.object(kind.socket, "socket") as socket, patch.object(kind.Path, "read_text", return_value="1024"):
            socket.return_value.__enter__.return_value.bind.side_effect = OSError("Address already in use")
            with self.assertRaisesRegex(OSError, "Address already in use"):
                kind.check_host_ports([{"protocol": "TCP", "hostPort": 443}])

    def test_single_node_uses_golden_networks_and_isolates_host_ports(self):
        c = json.loads((ROOT / "config/cluster.json").read_text())
        settings = json.loads((ROOT / "config/kind.json").read_text())
        obj = kind.cluster_config(c, settings, Path("/tmp/test-state"))
        self.assertEqual(len(obj["nodes"]), 1)
        self.assertNotIn("disableDefaultCNI", obj["networking"])
        self.assertNotIn("kubeProxyMode", obj["networking"])
        self.assertEqual(obj["networking"]["serviceSubnet"], c["service_cidr"])
        self.assertEqual(obj["networking"]["podSubnet"], c["pod_cidr"])
        self.assertEqual(obj["nodes"][0]["labels"]["elektro.internal/edge"], "true")
        self.assertTrue(all(p["listenAddress"] == "127.0.0.1" for p in obj["nodes"][0]["extraPortMappings"]))
        self.assertEqual(obj["nodes"][0]["extraMounts"], [{
            "hostPath": "/tmp/test-state/data", "containerPath": "/var/local-path-provisioner"}])

    def test_storage_aliases_refuse_a_changed_default_provisioner(self):
        standard = {"provisioner": "rancher.io/local-path", "reclaimPolicy": "Delete",
                    "volumeBindingMode": "WaitForFirstConsumer"}
        paths = {"data": {"config.json": json.dumps({"nodePathMap": [{
            "node": "DEFAULT_PATH_FOR_NON_LISTED_NODES", "paths": ["/var/local-path-provisioner"]}]})}}
        with patch.object(kind, "get", side_effect=[standard, paths]):
            kind.check_local_storage([])
        standard["provisioner"] = "another.driver"
        with patch.object(kind, "get", return_value=standard):
            with self.assertRaisesRegex(RuntimeError, "standard StorageClass changed"):
                kind.check_local_storage([])

    def test_wait_rejects_old_ready_generation_and_waits_for_creation(self):
        old = {"metadata": {"generation": 2}, "status": {"conditions": [
            {"type": "Ready", "status": "True", "observedGeneration": 1}]}}
        current = {"metadata": {"generation": 2}, "status": {"conditions": [
            {"type": "Ready", "status": "True", "observedGeneration": 2}]}}
        with patch.object(kind, "get", side_effect=[None, old, current]) as get, patch.object(kind.time, "sleep"):
            kind.wait(["kubectl"], "kustomization/gateway")
            self.assertEqual(get.call_count, 3)

    def test_ca_read_error_is_not_interpreted_as_absence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(kind, "apply"), patch.object(
                kind, "get", side_effect=RuntimeError("API unavailable")), patch.object(kind, "run") as run:
            state = Path(tmp)
            (state / "ca.key").write_text("preserve")
            with self.assertRaisesRegex(RuntimeError, "API unavailable"):
                kind.initialize_ca([], state, "test")
            self.assertEqual((state / "ca.key").read_text(), "preserve")
            run.assert_not_called()

    def test_incomplete_local_ca_does_not_create_new_trust_root(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(kind, "apply"), patch.object(
                kind, "get", return_value=None), patch.object(kind, "run") as run:
            state = Path(tmp)
            (state / "ca.crt").write_text("preserve")
            with self.assertRaisesRegex(RuntimeError, "Incomplete local CA"):
                kind.initialize_ca([], state, "test")
            self.assertEqual((state / "ca.crt").read_text(), "preserve")
            run.assert_not_called()

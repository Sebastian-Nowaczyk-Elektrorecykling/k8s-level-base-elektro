import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class KindCorefileTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("COREDNS"), "Set COREDNS to check the kind Corefile")
    def test_corefile_starts_with_the_pinned_dns_binary(self):
        text = json.loads((ROOT / "profiles/kind/dns/coredns.yaml").read_text())["data"]["Corefile"]
        text = text.replace("${DOMAIN}", "internal").replace("${LAN_DNS_SERVICE_IP}", "127.0.0.2")
        text = text.replace("${UPSTREAM_DNS}", "127.0.0.3")
        # Only API discovery needs a real cluster; parse and start all other
        # directives exactly as deployed, including nested plugin blocks.
        text, count = re.subn(r"    kubernetes cluster.local[^\n]*\{\n.*?    }\n", "", text, flags=re.S)
        self.assertEqual(count, 1)
        for directive in ("    health {\n        lameduck 5s\n    }\n", "    ready\n", "    prometheus :9153\n"):
            # Allocate separate ports instead of competing with other tests.
            if directive.startswith("    health"):
                text = text.replace("health {", "health 127.0.0.1:0 {")
            elif directive.startswith("    ready"):
                text = text.replace(directive, "    ready 127.0.0.1:0\n")
            else:
                text = text.replace(directive, "    prometheus 127.0.0.1:0\n")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        text = text.replace(":53 {", f":{port} {{")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Corefile"
            path.write_text(text)
            process = subprocess.Popen([os.environ["COREDNS"], "-conf", str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(process.stdout.read())
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=.1):
                            break
                    except OSError:
                        time.sleep(.05)
                else:
                    self.fail("CoreDNS did not bind its DNS listener")
            finally:
                process.terminate()
                process.communicate(timeout=10)

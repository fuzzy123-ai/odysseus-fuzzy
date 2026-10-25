import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from ops.homeserver import redacted_predeploy_backup_root_helper_preexec_stage_diagnostic as d
from ops.homeserver import redacted_predeploy_backup_root_helper_preexec_stage_diagnostic_transport as t

class TransportTests(unittest.TestCase):
    def test_source_pin_matches_actual_bytes(self):
        self.assertEqual(hashlib.sha256(t.SOURCE_PATH.read_bytes()).hexdigest(),t.SOURCE_SHA256)
    def test_explicit_gate(self):
        def forbidden(*args):raise AssertionError("network called")
        self.assertEqual(t.collect(runner=forbidden)["error_code"],"execution_disabled")
    def test_fixed_sandbox_and_no_runtime_directory_management(self):
        props=dict(p.split("=",1) for p in t.PROPERTIES)
        self.assertEqual(props["ProtectSystem"],"strict")
        self.assertEqual(props["ProtectHome"],"read-only")
        for key in ("PrivateTmp","PrivateNetwork","PrivateDevices","NoNewPrivileges"):
            self.assertEqual(props[key],"yes")
        self.assertNotIn("RuntimeDirectory",props)
        self.assertNotIn("CAP_DAC_OVERRIDE",props["CapabilityBoundingSet"])
        self.assertNotIn("CAP_FOWNER",props["CapabilityBoundingSet"])
        self.assertEqual(props["TimeoutStartSec"],"40s")
        self.assertIn("--collect",t.REMOTE_ARGV)
        self.assertIn("--pipe",t.REMOTE_ARGV)
        self.assertEqual(t.SSH_COMMAND[:4],("ssh","-F","ops/homeserver/ssh_config","odysseus-homeserver"))
    def test_valid_result_boundary(self):
        value=d.envelope("observed","stage_failed","credential_directory_owner_mode",ready=True,bound=True)
        def fake(command,source,timeout):
            self.assertEqual(command,t.SSH_COMMAND);self.assertEqual(timeout,70)
            self.assertEqual(hashlib.sha256(source).hexdigest(),t.SOURCE_SHA256)
            return SimpleNamespace(returncode=0,stdout=json.dumps(value).encode()+b"\n",oversized=False)
        self.assertEqual(t.collect(execute=True,runner=fake),value)
    def test_source_drift_prevents_dispatch(self):
        with patch.object(t,"SOURCE_SHA256","0"*64):
            self.assertEqual(t.collect(execute=True,runner=lambda *a:self.fail("called"))["status"],"blocked")
    def test_raw_or_extra_output_not_forwarded(self):
        for raw in (b"private raw diagnostic\n",b"{}\n{}\n"):
            result=t.collect(execute=True,runner=lambda *a:SimpleNamespace(returncode=0,stdout=raw,oversized=False))
            self.assertEqual(result["error_code"],"transport_ambiguous")
            self.assertFalse(result["raw_output_visible"])

if __name__=="__main__":unittest.main()

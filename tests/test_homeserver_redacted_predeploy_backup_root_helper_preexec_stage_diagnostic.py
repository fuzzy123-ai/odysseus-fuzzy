import os
import time
import unittest
from unittest.mock import patch
from ops.homeserver import redacted_predeploy_backup_root_helper_preexec_stage_diagnostic as d

def prefix(stage="execveat"):
    rows=[]
    for name in d.STAGES:
        rows.append("B:"+name)
        if name==stage:break
        rows.append("D:"+name)
    return ("\n".join(rows)+"\n").encode("ascii")

class ProtocolTests(unittest.TestCase):
    def test_each_exact_stage_failure(self):
        for stage in d.STAGES:
            with self.subTest(stage=stage):
                raw=prefix(stage)+("F:"+stage+"\n").encode()
                self.assertEqual(d._interpret(125,raw),("observed","stage_failed",stage,False))
    def test_exec_return_is_distinct_from_restic_exit_125(self):
        self.assertEqual(d._interpret(125,prefix()+b"F:exec_returned\n"),
                         ("observed","stage_failed","exec_returned",False))
        self.assertEqual(d._interpret(125,prefix()),("observed","restic_read_failed","restic_read",True))
    def test_success_requires_complete_sequence(self):
        self.assertEqual(d._interpret(0,prefix()),("observed","none","none",True))
        bad=[b"B:execveat\n",prefix()[:-1],prefix()+b"D:execveat\n",
             prefix("mount_namespace")+b"F:mount_namespace\nB:private_propagation\n",
             prefix().replace(b"D:private_propagation\n",b"",1),b"private data\n"]
        for raw in bad:self.assertEqual(d._interpret(0,raw)[1],"protocol_invalid")
    def test_timeout_and_signal_override_progress(self):
        self.assertEqual(d._interpret(0,prefix(),timed_out=True)[1],"timeout")
        self.assertEqual(d._interpret(-9,prefix(),signaled=True)[1],"child_signal")
    def test_envelope_rejects_wrong_types_extra_fields_and_visibility(self):
        good=d.envelope("observed","none",ready=True,bound=True,invoked=True)
        self.assertTrue(d.validate_envelope(good))
        for key,value in (("identity_bound",1),("secret_values_visible",True),("extra","private")):
            bad=dict(good);bad[key]=value;bad["evidence_sha256"]=d._digest(bad)
            self.assertFalse(d.validate_envelope(bad))
    def test_disabled_never_calls_runner(self):
        def forbidden():raise AssertionError("effect called")
        self.assertEqual(d.collect(runner=forbidden)["error_code"],"execution_disabled")
    def test_helper_pin_mismatch(self):
        with patch.object(d,"_read_fixed",return_value=b"not the helper"):
            self.assertIsNone(d._load_helper())
    def test_foreign_incident_rejected(self):
        self.assertFalse(d._validate_incident({}, {}, {}, b"used\n",0))
    @unittest.skipUnless(hasattr(os,"fork"),"requires local POSIX fork")
    def test_timeout_survives_exec_pipe_eof(self):
        def fake_child(helper,bound,fd):
            os.write(fd,prefix());os.close(fd);time.sleep(5);os._exit(0)
        started=time.monotonic()
        with patch.object(d,"_child",fake_child):
            value=d._run_child({},None,timeout=0.15)
        self.assertEqual(value[1],"timeout")
        self.assertLess(time.monotonic()-started,2)
    @unittest.skipUnless(hasattr(os,"fork"),"requires local POSIX fork")
    def test_completed_child_is_reaped_once(self):
        def fake_child(helper,bound,fd):
            os.write(fd,prefix());os.close(fd);os._exit(0)
        with patch.object(d,"_child",fake_child):
            self.assertEqual(d._run_child({},None,timeout=2),("observed","none","none",True))

if __name__=="__main__":unittest.main()

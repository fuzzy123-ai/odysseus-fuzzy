import importlib.util
import json
from pathlib import Path
import sys
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("updater_safety_under_test", ROOT / "ops/homeserver/updater_safety.py")
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)
REV = "a" * 40


class SafetyTests(unittest.TestCase):
    def shell_probe(self, body):
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
        if not bash:
            self.skipTest("bash unavailable")
        installer = (ROOT / "ops/homeserver/install-auto-update-timer.sh").read_text()
        wrapper = installer.split("cat >\"$WRAPPER_PATH\" <<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]
        definitions = wrapper.split('\nUPDATE_STAGE="lock"\nrun_locked\n', 1)[0]
        # Every called executable is a shell stub; no production helper runs.
        return subprocess.run([bash, "-s"], input=definitions + "\n" + body,
                              capture_output=True, text=True, timeout=10)

    def test_candidate_failure_prevents_switch(self):
        result = self.shell_probe(
            "trap - EXIT\n"
            "compose() { return 42; }\n"
            "switch_to_built_app() { echo forbidden_switch; }\n"
            'UPDATE_STAGE="candidate_configuration"\n'
            "validate_candidate_configuration\nswitch_to_built_app\n"
        )
        self.assertEqual(result.returncode, 42)
        self.assertNotIn("forbidden_switch", result.stdout)
        self.assertIn("stage=candidate_configuration", result.stderr)

    def test_notice_failure_does_not_rewrite_success(self):
        result = self.shell_probe(
            'python3() { printf "%s\\n" "$*"; return 1; }\n'
            'UPDATE_RESULT="completed"\nUPDATE_STAGE="runtime_stability"\nexit 0\n'
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--result completed", result.stdout)
        self.assertIn("delivery is not confirmed", result.stdout)

    def test_explicit_failure_also_emits_failure_outcome(self):
        result = self.shell_probe(
            'python3() { printf "%s\\n" "$*"; return 0; }\n'
            'UPDATE_STAGE="candidate_configuration"\ndie "synthetic failure"\n'
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("--result failed", result.stdout)

    def test_configuration_defaults(self):
        self.assertTrue(safety.configuration_check({})["ok"])

    def test_configuration_identifies_bad_flag_without_echo(self):
        for value in ("", '"false"', "not-a-boolean-private-canary"):
            result = safety.configuration_check({"ODYSSEUS_TRANSCRIPTION_ENABLED": value})
            self.assertEqual(result["reason"], "invalid_boolean")
            self.assertEqual(result["setting"], "ODYSSEUS_TRANSCRIPTION_ENABLED")
            if value:
                self.assertNotIn(value, json.dumps(result))

    def test_does_not_silently_disable_requested_feature(self):
        result = safety.configuration_check({"ODYSSEUS_TRANSCRIPTION_ENABLED": "true"})
        self.assertEqual(result["reason"], "transcription_legacy_conflict")

    def enabled_values(self):
        return {"ODYSSEUS_TRANSCRIPTION_ENABLED": "true",
                "ODYSSEUS_LEGACY_STT_ENABLED": "false"}

    def test_active_pipeline_requires_model_directory(self):
        result = safety.configuration_check(self.enabled_values())
        self.assertEqual(result["reason"], "model_directory_missing")

    def test_active_pipeline_dependency_failure_is_redacted(self):
        def check(values):
            raise RuntimeError("private-model-path-canary")
        values = {**self.enabled_values(), "ODYSSEUS_TRANSCRIPTION_MODEL_DIR": "/synthetic"}
        result = safety.configuration_check(values, dependency_check=check)
        self.assertEqual(result["reason"], "transcription_dependencies_unavailable")
        self.assertNotIn("private-model-path-canary", json.dumps(result))

    def test_active_pipeline_preserves_explicit_enable(self):
        checked = []
        values = {**self.enabled_values(), "ODYSSEUS_TRANSCRIPTION_MODEL_DIR": "/synthetic"}
        result = safety.configuration_check(values, dependency_check=lambda v: checked.append(v))
        self.assertTrue(result["ok"])
        self.assertEqual(checked[0]["ODYSSEUS_TRANSCRIPTION_ENABLED"], "true")

    def test_numeric_constraints_still_enforced(self):
        result = safety.configuration_check({"ODYSSEUS_TRANSCRIPTION_REQUEST_TIMEOUT_SECONDS": "900"})
        self.assertFalse(result["ok"])

    def stable(self, samples):
        ticks = [0]
        iterator = iter(samples)
        def sample():
            return next(iterator)
        def sleep(n):
            ticks[0] += n
        return safety.stable_runtime(REV, sample=sample, clock=lambda: ticks[0],
                                     sleep=sleep, duration=10, interval=5)

    def good(self, **changes):
        return {"identity": ("container", "start"), "healthy": True,
                "restarts": 0, "commit": REV[:8], **changes}

    def test_requires_entire_observation_window(self):
        self.assertTrue(self.stable([self.good()] * 3)["ok"])

    def test_detects_crash_after_first_good_sample(self):
        result = self.stable([self.good(), self.good(restarts=1)])
        self.assertEqual(result["reason"], "runtime_restarted")

    def test_detects_container_replacement(self):
        result = self.stable([self.good(), self.good(identity=("other", "start"))])
        self.assertEqual(result["reason"], "runtime_replaced")

    def test_wrong_revision_never_succeeds(self):
        self.assertEqual(self.stable([self.good(commit="b" * 8)])["reason"], "revision_mismatch")

    def test_open_port_without_readiness_is_failure(self):
        self.assertFalse(self.stable([self.good(healthy=False)])["ok"])

    def settings(self):
        return {"ODYSSEUS_UPDATE_NOTIFICATIONS_ENABLED": "true",
                "TELEGRAM_AGENT_REPLY_ENABLED": "true",
                "TELEGRAM_BOT_TOKEN": "123:synthetic_canary",
                "TELEGRAM_NOTIFICATION_CHAT_ID": "456"}

    def test_notifications_need_explicit_enable(self):
        result = safety.notify("completed", "runtime_stability", REV, settings={},
                               send=lambda *args: self.fail("must not send"))
        self.assertEqual(result["reason"], "notification_disabled")

    def test_host_sender_success(self):
        sent = []
        def send(*args):
            sent.append(args)
            return True
        result = safety.notify("failed", "candidate_configuration", REV,
                               settings=self.settings(), send=send)
        self.assertTrue(result["ok"])
        self.assertIn("fehlgeschlagen", sent[0][2])
        self.assertIn("Kein automatischer Rollback", sent[0][2])
        self.assertNotIn("synthetic_canary", json.dumps(result))

    def test_provider_exception_never_leaks(self):
        def send(*args):
            raise RuntimeError("private-canary-provider-response")
        result = safety.notify("failed", "runtime_stability", REV,
                               settings=self.settings(), send=send)
        self.assertEqual(result["reason"], "delivery_failed")
        self.assertNotIn("private-canary", json.dumps(result))

    def test_no_update_is_silent(self):
        result = safety.notify("unchanged", "git_resolution", REV, settings={},
                               send=lambda *args: self.fail("must not send"))
        self.assertEqual(result["reason"], "unchanged_silent")

    def test_untrusted_stage_cannot_enter_notice(self):
        result = safety.notify("failed", "private-canary", REV,
                               settings=self.settings(), send=lambda *args: self.fail())
        self.assertEqual(result["reason"], "invalid_event")


if __name__ == "__main__":
    unittest.main()

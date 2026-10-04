from __future__ import annotations

import hashlib
import io
import json
import os
import base64
import builtins
import pytest
import subprocess
import sys
from pathlib import Path

from ops.homeserver import redacted_predeploy_backup_root_helper_install as installer
from ops.homeserver import redacted_predeploy_backup_root_helper_install_transport as subject


class _Input:
    def __init__(self, returns: list[object] | None = None) -> None: self.value = b""; self.closed = False; self.returns = list(returns or [])
    def write(self, value):
        data = bytes(value)
        result = self.returns.pop(0) if self.returns else len(data)
        if type(result) is int and result > 0: self.value += data[:result]
        return result
    def flush(self): pass
    def close(self): self.closed = True
    def fileno(self) -> int: return 98


class _Output:
    def __init__(self, value: bytes) -> None: self.value = value; self.closed = False
    def read(self, count: int) -> bytes:
        value, self.value = self.value[:count], self.value[count:]
        return value
    def close(self): self.closed = True
    def fileno(self) -> int: return 99


class _Process:
    def __init__(self, output: bytes = b"", returncode: int = 0, timeout: bool = False, wait_error: bool = False, kill_raises: bool = False, write_returns: list[object] | None = None, poll_returns: list[object] | None = None, poll_error: bool = False) -> None:
        self.stdin, self.stdout = _Input(write_returns), _Output(output)
        self.returncode, self.timeout, self.wait_error, self.kill_raises, self.killed, self.kill_calls, self.wait_calls = returncode, timeout, wait_error, kill_raises, False, 0, 0
        self.poll_returns = list(poll_returns) if poll_returns is not None else None
        self.poll_error = poll_error
    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_error: raise OSError("wait failed")
        if self.timeout and not self.killed: raise subprocess.TimeoutExpired("ssh", timeout)
        return self.returncode
    def kill(self):
        self.kill_calls += 1
        if self.kill_raises: raise OSError("controlled kill failure")
        self.killed = True; self.returncode = -9
    def poll(self):
        if self.poll_error: raise OSError("poll failed")
        if self.poll_returns is not None:
            return self.poll_returns.pop(0) if self.poll_returns else self.returncode
        return None if self.timeout or not self.stdin.closed else self.returncode


def _popen(process: _Process, seen: list[tuple[str, ...]]):
    def call(argv, **kwargs):
        assert kwargs["shell"] is False and kwargs["stderr"] is subprocess.DEVNULL and kwargs["bufsize"] == 0
        seen.append(tuple(argv)); return process
    return call


@pytest.fixture(autouse=True)
def _nonblocking_fd(monkeypatch):
    monkeypatch.setattr(subject.os, "set_blocking", lambda descriptor, enabled: None)


def _bundle_runner(command, **kwargs):
    path = command[-1].split(":", 1)[1]
    return type("Result", (), {"returncode": 0, "stdout": Path(path).read_bytes()})()


def _install_response() -> bytes:
    receipt = {"schema_id": "odysseus.predeploy_backup_root_helper_install.v1", "status": "installed", "error_code": "execution_disabled", "helper_installed": True, "unit_installed": True, "sudo_policy_installed": True, "rollback_attempted": False, "rollback_succeeded": False, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    receipt["evidence_sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    readback = {"schema_id": "odysseus.predeploy_backup_root_helper_install_diagnostic_readback.v1", "status": "available", "assets_valid": True, "safe_parents": True, "state_dir_safe": True, "unit_disabled": True, "arm_present": False, "unit_terminal_state": "inactive", "main_pid_present": False, "control_pid_present": False, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    readback["evidence_sha256"] = hashlib.sha256(json.dumps(readback, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    return json.dumps({"mode": "install", "receipt": receipt, "readback": readback}, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def test_default_is_inert_and_probe_and_candidate_install_use_pinned_argv() -> None:
    assert subject.validate_envelope(subject.probe_authority())
    assert subject.validate_envelope(subject.request_installation())
    probe, install, seen = _Process(b'{"euid_root":true,"mode":"probe","ssh_argv_authority":true}\n'), _Process(_install_response()), []
    assert subject.probe_authority(execute=True, popen=_popen(probe, seen))["status"] == "probe_ok"
    assert subject.request_candidate_installation(execute=True, popen=_popen(install, seen))["status"] == "installed"
    assert seen == [subject.SSH_COMMAND, subject.CANDIDATE_SSH_COMMAND]
    assert probe.stdin.value == b"mode=probe\n"
    assert install.stdin.value.startswith(b"mode=install\n")
    assert subject.SSH_COMMAND[:4] == ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
    assert subject.SSH_COMMAND == subject.SSH_PREFIX + (subject.REMOTE_COMMAND,)
    assert subject.CANDIDATE_SSH_COMMAND == subject.SSH_PREFIX + (subject.CANDIDATE_REMOTE_COMMAND,)
    assert subject.REMOTE_COMMAND == "/usr/bin/timeout 35s /usr/bin/sudo -n /usr/bin/python3 -I -c " + subject.shlex.quote(subject.STATIC_BOOTSTRAP)


def test_pins_and_bundle_are_exact_base64_and_pre_dispatch_failures_have_no_effect(monkeypatch) -> None:
    assert subject.prepare_published_install_bundle(runner=_bundle_runner) is None
    bundle = subject.prepare_candidate_install_bundle()
    assert bundle and set(bundle) == {"installer_sha256", "installer_source", "helper_sha256", "helper_source", "readback_sha256", "readback_source", "install_readback_sha256", "install_readback_source"}
    assert bundle["installer_sha256"] == subject.CANDIDATE_INSTALLER_SHA256
    calls = []
    monkeypatch.setattr(subject, "CANDIDATE_HELPER_SHA256", "0" * 64)
    value = subject.request_candidate_installation(execute=True, popen=lambda *a, **k: calls.append(a))
    assert value["status"] == "blocked" and value["installation_invoked"] is False and not calls


def test_candidate_bundle_and_explicit_candidate_route_are_separate_from_published(monkeypatch) -> None:
    candidate = subject.prepare_candidate_install_bundle()
    assert candidate and set(candidate) == {"installer_sha256", "installer_source", "helper_sha256", "helper_source", "readback_sha256", "readback_source", "install_readback_sha256", "install_readback_source"}
    assert candidate["installer_sha256"] == subject.CANDIDATE_INSTALLER_SHA256
    assert candidate["install_readback_sha256"] == subject.CANDIDATE_INSTALL_READBACK_SHA256
    assert subject.CANDIDATE_STATIC_BOOTSTRAP != subject.STATIC_BOOTSTRAP
    assert subject.CANDIDATE_INSTALLER_SHA256 in subject.CANDIDATE_STATIC_BOOTSTRAP
    assert subject.PUBLISHED_INSTALLER_SHA256 in subject.STATIC_BOOTSTRAP
    assert subject.request_candidate_installation()["status"] == "blocked"
    seen = []
    result = subject.request_candidate_installation(execute=True, popen=_popen(_Process(_install_response()), seen))
    assert result["status"] == "installed" and seen == [subject.CANDIDATE_SSH_COMMAND]
    monkeypatch.setattr(subject, "CANDIDATE_INSTALLER_SHA256", "0" * 64)
    assert subject.request_candidate_installation(execute=True, popen=lambda *a, **k: pytest.fail("must not dispatch"))["error_code"] == "candidate_blob_mismatch"


def test_probe_bootstrap_has_no_installer_execution_path_and_no_checkout_import(monkeypatch) -> None:
    text = subject.STATIC_BOOTSTRAP
    probe_branch = text.split("elif line==b\"mode=install", 1)[0]
    assert 'receipt=ns["install"]' not in probe_branch and "importlib" not in text and "chdir" not in text and "sys.path" not in text
    assert "base64.b64decode(bundle[k],validate=True)" in text and "types.ModuleType" in text and "sys.modules[installer_name]" in text and "SecureHostOperations(root=\"/\")" in text
    # Execute the probe branch in-process: an installer call would fail this
    # test because no installer symbols exist in the isolated globals.
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"buffer": io.BytesIO(b"mode=probe\n")})())
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    exec(compile(text, "<fixed-bootstrap>", "exec"), {"__name__": "__bootstrap_test__"})
    assert json.loads(stdout.getvalue()) == {"euid_root": True, "mode": "probe", "ssh_argv_authority": True}


def test_install_bootstrap_uses_registered_modules_and_fake_operations_without_host_effect(monkeypatch) -> None:
    installer = b'''from dataclasses import dataclass\nimport builtins,hashlib,json\n@dataclass\nclass Marker: value:int=1\nclass SecureHostOperations:\n def __init__(self,*,root): self.root=root\ndef install(*,execute,helper_source,readback_source,operations):\n assert execute and operations.root=='/' and helper_source==b'h' and readback_source==b'r' and Marker().value==1\n builtins._odysseus_bootstrap_install_calls+=1\n v={'schema_id':'odysseus.predeploy_backup_root_helper_install.v1','status':'installed','error_code':'execution_disabled','helper_installed':True,'unit_installed':True,'sudo_policy_installed':True,'rollback_attempted':False,'rollback_succeeded':False,'raw_output_visible':False,'environment_visible':False,'paths_visible':False,'secret_values_visible':False};v['evidence_sha256']=hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest();return v\n'''
    readback = b'''import hashlib,json\ndef collect_diagnostic_readback_v2():\n v={'schema_id':'odysseus.predeploy_backup_root_helper_install_diagnostic_readback.v1','status':'available','assets_valid':True,'safe_parents':True,'state_dir_safe':True,'unit_disabled':True,'arm_present':False,'unit_terminal_state':'inactive','main_pid_present':False,'control_pid_present':False,'raw_output_visible':False,'environment_visible':False,'paths_visible':False,'secret_values_visible':False};v['evidence_sha256']=hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest();return v\ndef validate_diagnostic(v): return v.get('status')=='available'\n'''
    blobs = {"installer": installer, "helper": b"h", "readback": b"r", "install_readback": readback}
    text = subject.STATIC_BOOTSTRAP
    for name, blob in blobs.items():
        text = text.replace(getattr(subject, "PUBLISHED_" + ("INSTALLER" if name == "installer" else "HELPER" if name == "helper" else "READBACK" if name == "readback" else "INSTALL_READBACK") + "_SHA256"), hashlib.sha256(blob).hexdigest())
    bundle = {key + "_sha256": hashlib.sha256(value).hexdigest() for key, value in blobs.items()} | {key + "_source": base64.b64encode(value).decode("ascii") for key, value in blobs.items()}
    stdout = io.StringIO()
    monkeypatch.setattr(builtins, "_odysseus_bootstrap_install_calls", 0, raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"buffer": io.BytesIO(b"mode=install\n" + json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode() + b"\n")})())
    monkeypatch.setattr(sys, "stdout", stdout)
    exec(compile(text, "<fake-fixed-bootstrap>", "exec"), {"__name__": "__bootstrap_test__"})
    value = json.loads(stdout.getvalue())
    assert builtins._odysseus_bootstrap_install_calls == 1
    assert value["mode"] == "install" and subject._valid_install_receipt(value["receipt"]) and subject._valid_install_readback(value["readback"])


def test_bootstrap_rejects_any_trailing_stdin_after_a_valid_line(monkeypatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"buffer": io.BytesIO(b"mode=probe\ntrailing")})())
    with pytest.raises(SystemExit):
        exec(compile(subject.STATIC_BOOTSTRAP, "<fixed-bootstrap>", "exec"), {"__name__": "__bootstrap_test__"})


def test_all_post_dispatch_ambiguity_is_unknown_with_manual_recovery(monkeypatch) -> None:
    cases = [(_Process(b"x\n", 1), "transport_nonzero"), (_Process(b"{}\n"), "transport_invalid"), (_Process(b"x" * (subject.MAX_STDOUT_BYTES + 1)), "transport_oversize")]
    for process, code in cases:
        result = subject.probe_authority(execute=True, popen=_popen(process, []))
        assert result["status"] == "probe_unknown" and result["error_code"] == code and result["effect_may_have_occurred"] is False and result["installation_invoked"] is False and result["retry_permitted"] is False
    monkeypatch.setattr(subject, "TRANSPORT_TIMEOUT_SECONDS", 0.001)
    timeout = _Process(timeout=True)
    result = subject.probe_authority(execute=True, popen=_popen(timeout, []))
    assert result["error_code"] == "transport_timeout" and timeout.killed
    broken = _Process(poll_error=True)
    result = subject.probe_authority(execute=True, popen=_popen(broken, []))
    assert result["status"] == "probe_unknown" and result["retry_permitted"] is False
    assert broken.killed and broken.stdin.closed and broken.stdout.closed


def test_malformed_multiline_or_forged_install_receipt_never_becomes_success() -> None:
    multiline = _Process(b'{"euid_root":true}\n{}\n')
    assert subject.probe_authority(execute=True, popen=_popen(multiline, []))["status"] == "probe_unknown"
    forged = json.loads(_install_response()); forged["receipt"]["raw_output_visible"] = True
    process = _Process(json.dumps(forged, separators=(",", ":")).encode() + b"\n")
    assert subject.request_candidate_installation(execute=True, popen=_popen(process, []))["status"] == "unknown"


def test_transport_never_uses_shell_or_unbounded_communicate() -> None:
    text = Path(subject.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in text and ".communicate(" not in text and "stderr=subprocess.DEVNULL" in text


def test_transport_rejects_resigned_forged_diagnostic_projections() -> None:
    valid = json.loads(_install_response())["readback"]
    assert subject._valid_install_readback(valid)
    for change in (
        {"status": "unknown"},
        {"arm_present": True},
        {"main_pid_present": True},
        {"control_pid_present": True},
        {"main_pid_present": 0},
        {"control_pid_present": 0},
        {"unit_terminal_state": "nonterminal"},
        {"unit_terminal_state": "unavailable"},
        {"unit_terminal_state": "active"},
    ):
        forged = dict(valid)
        forged.update(change)
        forged["evidence_sha256"] = hashlib.sha256(json.dumps({key: value for key, value in forged.items() if key != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
        assert not subject._valid_install_readback(forged)


def test_transport_preserves_historical_published_pins_and_binds_current_candidate_assets() -> None:
    root = Path(subject.__file__).parents[2]
    assert subject.PUBLISHED_INSTALLER_SHA256 == "35a05876b90addbc6102aee45995d334fc0e04ce68a7a1bef8e0633f41dd0941"
    assert subject.PUBLISHED_INSTALL_READBACK_SHA256 == "c11ac244ebccfb54520b0e152e24a7f9cfd7002d668dd8901e5d6af0f2e93f60"
    candidate_paths = {
        "CANDIDATE_INSTALLER_SHA256": root / "ops/homeserver/redacted_predeploy_backup_root_helper_install.py",
        "CANDIDATE_HELPER_SHA256": root / "ops/homeserver/redacted_predeploy_backup_root_helper.py",
        "CANDIDATE_READBACK_SHA256": root / "ops/homeserver/redacted_predeploy_backup_root_helper_readback.py",
        "CANDIDATE_INSTALL_READBACK_SHA256": root / "ops/homeserver/redacted_predeploy_backup_root_helper_install_readback.py",
    }
    for pin, path in candidate_paths.items():
        assert getattr(subject, pin) == hashlib.sha256(path.read_bytes()).hexdigest()
    assert installer.SERVICE_TEXT == (root / "ops/homeserver/root-helper/odysseus-predeploy-backup-root-helper.service").read_text(encoding="utf-8")
    assert "TimeoutStartSec=1860s" in installer.SERVICE_TEXT and "RuntimeMaxSec=" not in installer.SERVICE_TEXT
    sudoers = (root / "ops/homeserver/root-helper/odysseus-predeploy-backup-root-helper.sudoers").read_text(encoding="ascii")
    assert installer.SUDOERS_TEXT == sudoers
    assert "NOSETENV" in sudoers and "/usr/bin/python3 -I /usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py --diagnostic-v2" in sudoers


def test_transport_handles_partial_and_invalid_unbuffered_writes_conservatively() -> None:
    partial = _Process(b"ok\n", write_returns=[2, 3], poll_returns=[None, 0])
    state, output = subject._bounded_popen(("ssh",), b"abcde", popen=_popen(partial, []))
    assert state == "ok" and output == b"ok\n" and partial.stdin.value == b"abcde"
    fragmented = _Process(b"ok\n", write_returns=[None, 2, 1, 2], poll_returns=[None, None, None, 0])
    state, output = subject._bounded_popen(("ssh",), b"abcde", popen=_popen(fragmented, []))
    assert state == "ok" and output == b"ok\n" and fragmented.stdin.value == b"abcde"
    early = _Process(b"ok\n", poll_returns=[0])
    state, output = subject._bounded_popen(("ssh",), b"x" * 4097, popen=_popen(early, []))
    assert state == "ambiguous" and output is None and early.stdin.value == b"x" * 4096
    invalid = _Process(b"ok\n", write_returns=[0])
    state, output = subject._bounded_popen(("ssh",), b"abcde", popen=_popen(invalid, []))
    assert state == "ambiguous" and output is None
    assert invalid.kill_calls >= 1 and invalid.wait_calls >= 1 and invalid.stdin.closed and invalid.stdout.closed


def test_transport_cap_plus_one_never_requests_a_zero_length_read() -> None:
    class CountOutput(_Output):
        def __init__(self, value: bytes) -> None: super().__init__(value); self.counts: list[int] = []
        def read(self, count: int) -> bytes:
            self.counts.append(count)
            assert count > 0
            return super().read(count)
    process = _Process()
    process.stdout = CountOutput(b"x" * (subject.MAX_STDOUT_BYTES + 1))
    state, output = subject._bounded_popen(("ssh",), b"payload", popen=_popen(process, []))
    assert state == "oversize" and output is None and process.stdout.counts == [4096, 4096, 1]


def test_transport_bounded_cleanup_handles_blocked_workers_and_kill_failure(monkeypatch) -> None:
    class WouldBlockOutput(_Output):
        def read(self, count: int): return None
    monkeypatch.setattr(subject, "TRANSPORT_TIMEOUT_SECONDS", 0)
    blocked = _Process(timeout=True, write_returns=[None])
    blocked.stdout = WouldBlockOutput(b"")
    state, output = subject._bounded_popen(("ssh",), b"payload", popen=_popen(blocked, []))
    assert state == "timeout" and output is None and blocked.stdin.closed and blocked.stdout.closed
    timeout = _Process(timeout=True, kill_raises=True)
    state, output = subject._bounded_popen(("ssh",), b"payload", popen=_popen(timeout, []))
    assert state == "timeout" and output is None
    assert timeout.kill_calls >= 1 and timeout.wait_calls >= 1 and timeout.stdin.closed and timeout.stdout.closed
    assert "threading.Thread" not in Path(subject.__file__).read_text(encoding="utf-8")


def test_transport_rejects_signed_hostile_diagnostic_types_after_dispatch(monkeypatch) -> None:
    valid = json.loads(_install_response())
    for terminal in ([], {}):
        forged = json.loads(json.dumps(valid))
        forged["readback"]["unit_terminal_state"] = terminal
        forged["readback"]["evidence_sha256"] = hashlib.sha256(json.dumps({key: value for key, value in forged["readback"].items() if key != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
        result = subject.request_candidate_installation(execute=True, popen=_popen(_Process(json.dumps(forged, separators=(",", ":")).encode("ascii") + b"\n"), []))
        assert result["status"] == "unknown" and result["error_code"] == "transport_invalid" and result["manual_recovery_required"] is True and result["retry_permitted"] is False
    malformed = valid["readback"]
    malformed["unit_terminal_state"] = object()
    assert not subject._valid_install_readback(malformed)
    monkeypatch.setattr(subject.json, "loads", lambda value: {"mode": "install", "receipt": valid["receipt"], "readback": malformed})
    result = subject.request_candidate_installation(execute=True, popen=_popen(_Process(b"{}\n"), []))
    assert result["status"] == "unknown" and result["error_code"] == "transport_invalid" and result["manual_recovery_required"] is True and result["retry_permitted"] is False


def test_transport_envelope_validator_is_total_and_requires_exact_json_booleans() -> None:
    value = subject.blocked("invalid_invocation")
    for change in ({"error_code": []}, {"status": {}}, {"retry_permitted": 0}, {"installation_invoked": []}):
        forged = dict(value); forged.update(change)
        forged["evidence_sha256"] = subject._digest(forged)
        assert not subject.validate_envelope(forged)
    forged = dict(value); forged["error_code"] = object()
    assert not subject.validate_envelope(forged)
    assert not subject.validate_envelope(object())

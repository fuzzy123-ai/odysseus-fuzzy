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

from ops.homeserver import redacted_predeploy_backup_root_helper_install_transport as subject


class _Input:
    def __init__(self) -> None: self.value = b""; self.closed = False
    def write(self, value): self.value += bytes(value); return len(value)
    def flush(self): pass
    def close(self): self.closed = True


class _Output:
    def __init__(self, value: bytes) -> None: self.value = value; self.closed = False
    def read(self, count: int) -> bytes:
        value, self.value = self.value[:count], self.value[count:]
        return value
    def close(self): self.closed = True


class _Process:
    def __init__(self, output: bytes = b"", returncode: int = 0, timeout: bool = False, wait_error: bool = False) -> None:
        self.stdin, self.stdout = _Input(), _Output(output)
        self.returncode, self.timeout, self.wait_error, self.killed = returncode, timeout, wait_error, False
    def wait(self, timeout=None):
        if self.wait_error: raise OSError("wait failed")
        if self.timeout and not self.killed: raise subprocess.TimeoutExpired("ssh", timeout)
        return self.returncode
    def kill(self): self.killed = True; self.returncode = -9


def _popen(process: _Process, seen: list[tuple[str, ...]]):
    def call(argv, **kwargs):
        assert kwargs["shell"] is False and kwargs["stderr"] is subprocess.DEVNULL
        seen.append(tuple(argv)); return process
    return call


def _bundle_runner(command, **kwargs):
    path = command[-1].split(":", 1)[1]
    return type("Result", (), {"returncode": 0, "stdout": Path(path).read_bytes()})()


def _install_response() -> bytes:
    receipt = {"schema_id": "odysseus.predeploy_backup_root_helper_install.v1", "status": "installed", "error_code": "execution_disabled", "helper_installed": True, "unit_installed": True, "sudo_policy_installed": True, "rollback_attempted": False, "rollback_succeeded": False, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    receipt["evidence_sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    readback = {"schema_id": "odysseus.predeploy_backup_root_helper_install_readback.v2", "status": "available", "assets_valid": True, "safe_parents": True, "state_dir_safe": True, "unit_disabled": True, "unit_inactive": True, "arm_present": False, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    readback["evidence_sha256"] = hashlib.sha256(json.dumps(readback, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    return json.dumps({"mode": "install", "receipt": receipt, "readback": readback}, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def test_default_is_inert_and_probe_and_install_use_identical_immutable_argv() -> None:
    assert subject.validate_envelope(subject.probe_authority())
    assert subject.validate_envelope(subject.request_installation())
    probe, install, seen = _Process(b'{"euid_root":true,"mode":"probe","ssh_argv_authority":true}\n'), _Process(_install_response()), []
    assert subject.probe_authority(execute=True, popen=_popen(probe, seen))["status"] == "probe_ok"
    assert subject.request_installation(execute=True, runner=_bundle_runner, popen=_popen(install, seen))["status"] == "installed"
    assert seen == [subject.SSH_COMMAND, subject.SSH_COMMAND]
    assert probe.stdin.value == b"mode=probe\n"
    assert install.stdin.value.startswith(b"mode=install\n")
    assert subject.SSH_COMMAND[:4] == ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
    assert subject.SSH_COMMAND == subject.SSH_PREFIX + (subject.REMOTE_COMMAND,)
    assert subject.REMOTE_COMMAND == "/usr/bin/timeout 35s /usr/bin/sudo -n /usr/bin/python3 -I -c " + subject.shlex.quote(subject.STATIC_BOOTSTRAP)


def test_pins_and_bundle_are_exact_base64_and_pre_dispatch_failures_have_no_effect(monkeypatch) -> None:
    bundle = subject.prepare_published_install_bundle(runner=_bundle_runner)
    assert bundle and set(bundle) == {"installer_sha256", "installer_source", "helper_sha256", "helper_source", "readback_sha256", "readback_source", "install_readback_sha256", "install_readback_source"}
    assert bundle["installer_sha256"] == subject.PUBLISHED_INSTALLER_SHA256
    calls = []
    monkeypatch.setattr(subject, "PUBLISHED_HELPER_SHA256", "0" * 64)
    value = subject.request_installation(execute=True, runner=_bundle_runner, popen=lambda *a, **k: calls.append(a))
    assert value["status"] == "blocked" and value["installation_invoked"] is False and not calls


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
    readback = b'''import hashlib,json\ndef collect():\n v={'schema_id':'odysseus.predeploy_backup_root_helper_install_readback.v2','status':'available','assets_valid':True,'safe_parents':True,'state_dir_safe':True,'unit_disabled':True,'unit_inactive':True,'arm_present':False,'raw_output_visible':False,'environment_visible':False,'paths_visible':False,'secret_values_visible':False};v['evidence_sha256']=hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest();return v\n'''
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
    broken = _Process(wait_error=True)
    result = subject.probe_authority(execute=True, popen=_popen(broken, []))
    assert result["status"] == "probe_unknown" and result["retry_permitted"] is False
    assert broken.killed and broken.stdin.closed and broken.stdout.closed


def test_malformed_multiline_or_forged_install_receipt_never_becomes_success() -> None:
    multiline = _Process(b'{"euid_root":true}\n{}\n')
    assert subject.probe_authority(execute=True, popen=_popen(multiline, []))["status"] == "probe_unknown"
    forged = json.loads(_install_response()); forged["receipt"]["raw_output_visible"] = True
    process = _Process(json.dumps(forged, separators=(",", ":")).encode() + b"\n")
    assert subject.request_installation(execute=True, runner=_bundle_runner, popen=_popen(process, []))["status"] == "unknown"


def test_transport_never_uses_shell_or_unbounded_communicate() -> None:
    text = Path(subject.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in text and ".communicate(" not in text and "stderr=subprocess.DEVNULL" in text

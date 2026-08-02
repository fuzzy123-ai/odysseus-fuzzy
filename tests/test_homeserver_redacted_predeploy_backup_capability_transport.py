from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import time
from types import SimpleNamespace

from ops.homeserver import redacted_predeploy_backup_capability as capability
from ops.homeserver import redacted_predeploy_backup_capability_transport as transport


def _indexed_source() -> bytes:
    return subprocess.run(
        ["git", "cat-file", "blob", f":{transport.CAPABILITY_PATH}"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ).stdout


def test_default_is_inert_and_published_pin_is_consistent(capsys):
    source = _indexed_source()
    calls = []
    payload = transport.collect_published_predeploy_backup_capability(execute=False, runner=lambda *a, **k: calls.append(a))
    assert payload["status"] == "blocked" and payload["error_code"] == "invalid_invocation"
    assert calls == [] and transport.validate_transport_envelope(payload)
    assert transport.PUBLISHED_CAPABILITY_SHA256 == hashlib.sha256(source).hexdigest()
    assert f"expected='{transport.PUBLISHED_CAPABILITY_SHA256}'" in transport._BOOTSTRAP
    assert f"read({transport.MAX_BUNDLE_BYTES + 1})" in transport._BOOTSTRAP
    maximum_bundle = json.dumps({
        "execute": True, "sha256": transport.PUBLISHED_CAPABILITY_SHA256,
        "source": base64.b64encode(b"x" * transport.MAX_SOURCE_BYTES).decode("ascii"),
    }, separators=(",", ":")).encode("ascii")
    assert len(maximum_bundle) <= transport.MAX_BUNDLE_BYTES
    assert transport.main() == 1 and json.loads(capsys.readouterr().out)["error_code"] == "invalid_invocation"


def test_exact_blob_bundle_fixed_ssh_and_valid_core_envelope(monkeypatch):
    source = b"fixed published source"
    monkeypatch.setattr(transport, "_published_blob", lambda runner: source)
    calls = []
    core = capability._packet("supported", "none", invoked=True, ready=True)
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=(json.dumps(core, separators=(",", ":")) + "\n").encode())
    payload = transport.collect_published_predeploy_backup_capability(execute=True, runner=runner)
    assert payload == core and capability.validate_envelope(payload)
    command, kwargs = calls[0]
    assert tuple(command) == transport.SSH_COMMAND
    assert tuple(command[:4]) == transport.SSH_COMMAND_PREFIX
    assert kwargs["stderr"] is subprocess.DEVNULL and kwargs["stdout"] is subprocess.PIPE
    assert kwargs["timeout"] == 30 and kwargs["shell"] is False and kwargs["text"] is False
    bundle = json.loads(kwargs["input"].decode("ascii"))
    assert bundle == {"execute": True, "sha256": transport.PUBLISHED_CAPABILITY_SHA256, "source": base64.b64encode(source).decode("ascii")}


def test_predispatch_blob_mismatch_is_blocked_but_every_postdispatch_failure_is_unknown(monkeypatch):
    mismatch = transport.collect_published_predeploy_backup_capability(
        execute=True, runner=lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=b"wrong")
    )
    assert mismatch["status"] == "blocked" and mismatch["effect_may_have_occurred"] is False

    monkeypatch.setattr(transport, "_published_blob", lambda runner: b"source")
    cases = (
        subprocess.TimeoutExpired(transport.SSH_COMMAND, 30),
        SimpleNamespace(returncode=1, stdout=(json.dumps(capability._packet("unsupported", "capability_unavailable", invoked=True, ready=False)) + "\n").encode()),
        SimpleNamespace(returncode=2, stdout=b""),
        SimpleNamespace(returncode=0, stdout=b"{}\n"),
        SimpleNamespace(returncode=0, stdout=b"x" * 8193 + b"\n"),
    )
    for response in cases:
        def runner(*args, **kwargs):
            if isinstance(response, BaseException): raise response
            return response
        payload = transport.collect_published_predeploy_backup_capability(execute=True, runner=runner)
        assert payload["status"] == "unknown" and payload["effect_may_have_occurred"] is True
        assert payload["retry_permitted"] is False and transport.validate_transport_envelope(payload)


def test_transport_source_has_no_live_or_secret_bypass():
    text = open(transport.__file__, encoding="utf-8").read()
    assert "odysseus-homeserver" in text and "RESTIC_PASSWORD" not in text
    assert "retry_permitted\": False" in text or '"retry_permitted": False' in text


def test_transport_validator_rejects_status_error_cross_product_even_with_valid_digest():
    payload = transport._packet("blocked", "invalid_invocation", False)
    payload["error_code"] = "transport_ambiguous"; payload["evidence_sha256"] = transport._digest(payload)
    assert not transport.validate_transport_envelope(payload)


def test_bounded_process_exact_overflow_timeout_and_kill_failure_close_all_pipes(monkeypatch):
    class Input:
        def __init__(self): self.closed, self.written = False, bytearray()
        def write(self, value): self.written.extend(value); return len(value)
        def close(self): self.closed = True
    class Output:
        def __init__(self, chunks): self.closed, self.chunks = False, iter(chunks)
        def read(self, amount):
            try: return next(self.chunks)
            except StopIteration: return b""
        def close(self): self.closed = True
    class Process:
        def __init__(self, chunks, *, timeout=False, kill_fails=False):
            self.stdin, self.stdout = Input(), Output(chunks)
            self.timeout, self.kill_fails, self.waits, self.kills = timeout, kill_fails, [], 0
        def wait(self, timeout=None):
            self.waits.append(timeout)
            if self.timeout and len(self.waits) == 1: raise subprocess.TimeoutExpired(("probe",), timeout)
            time.sleep(0.05)
            return 0
        def kill(self):
            self.kills += 1
            if self.kill_fails: raise OSError("kill denied")

    exact = Process((b"1234", b""))
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: exact)
    result = transport._bounded_process(("probe",), b"input", 5, 4)
    assert result.stdout == b"1234" and result.stdout_oversized is False
    assert exact.stdin.closed and exact.stdout.closed and exact.stdin.written == b"input"

    overflow = Process((b"12345", b""))
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: overflow)
    result = transport._bounded_process(("probe",), b"input", 5, 4)
    assert result.stdout_oversized is True and overflow.kills >= 1
    assert overflow.waits and overflow.stdin.closed and overflow.stdout.closed

    timeout = Process((b"",), timeout=True, kill_fails=True)
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: timeout)
    try:
        transport._bounded_process(("probe",), b"input", 5, 4)
        assert False, "timeout must remain ambiguous"
    except subprocess.TimeoutExpired:
        pass
    assert timeout.kills >= 1 and timeout.waits == [5, 1]
    assert timeout.stdin.closed and timeout.stdout.closed

    class FirstWaitFailureThenExit(Process):
        def wait(self, timeout=None):
            self.waits.append(timeout)
            if len(self.waits) == 1: raise RuntimeError("first wait failed")
            return 0
        def poll(self): return None
    later_exit = FirstWaitFailureThenExit((b"",), kill_fails=True)
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: later_exit)
    try:
        transport._bounded_process(("probe",), b"input", 5, 4)
        assert False, "initial wait failure remains ambiguous"
    except RuntimeError as exc:
        assert not isinstance(exc, transport.TransportProcessUnreaped)
    assert later_exit.waits == [5, 1] and later_exit.kills >= 1
    assert later_exit.stdin.closed and later_exit.stdout.closed

    class PermanentlyUnreaped(Process):
        def wait(self, timeout=None): self.waits.append(timeout); raise subprocess.TimeoutExpired(("probe",), timeout)
        def poll(self): return None
    unreaped = PermanentlyUnreaped((b"",), kill_fails=True)
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *args, **kwargs: unreaped)
    try:
        transport._bounded_process(("probe",), b"input", 5, 4)
        assert False, "unreaped child needs distinct ambiguity"
    except transport.TransportProcessUnreaped:
        pass
    assert unreaped.kills >= 1 and len(unreaped.waits) >= 3
    assert unreaped.stdin.closed and unreaped.stdout.closed
    payload = transport._packet("unknown", "transport_ambiguous", True)
    payload["error_code"] = "published_blob_mismatch"; payload["evidence_sha256"] = transport._digest(payload)
    assert not transport.validate_transport_envelope(payload)

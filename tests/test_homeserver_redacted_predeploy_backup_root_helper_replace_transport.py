from __future__ import annotations

import io
import json
import subprocess
import threading
from itertools import product
from pathlib import Path
from ops.homeserver import redacted_predeploy_backup_root_helper_replace_transport as subject
from ops.homeserver import redacted_predeploy_backup_root_helper_replace as replacement


def test_transport_default_is_inert_and_redacted() -> None:
    value = subject.request_replacement()
    assert value["status"] == "blocked"
    assert subject.validate_envelope(value)
    assert subject.SSH_COMMAND[:4] == ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
    assert "timeout 35s" in subject.SSH_COMMAND[-1]


def test_candidate_requires_published_pin_match() -> None:
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        class Result:
            returncode = 1
            stdout = b""
        return Result()

    assert subject.prepare_candidate(runner=runner) is None
    assert len(calls) == 2


def test_transport_failure_is_unknown_and_never_retryable() -> None:
    def runner(argv, **kwargs):
        class Result:
            returncode = 1
            stdout = b""
        return Result()

    value = subject.request_replacement(execute=True, runner=runner)
    assert value["status"] == "blocked"
    assert value["retry_permitted"] is False


def test_validator_rejects_raw_fields_and_tampering() -> None:
    value = subject.envelope("blocked", "execution_disabled")
    value["raw_output_visible"] = True
    value["evidence_sha256"] = subject._digest(value)
    assert not subject.validate_envelope(value)


def test_independent_validator_state_table_is_exhaustive() -> None:
    statuses = ("applied", "blocked", "rolled_back", "unknown")
    known_codes = {
        "none", "execution_disabled", "invalid_source", "preflight_failed", "lock_busy",
        "backup_conflict", "write_failed", "publish_ambiguous", "postflight_failed",
        "rollback_failed", "foreign_target", "published_blob_mismatch", "remote_blocked",
        "remote_rolled_back", "transport_ambiguous", "transport_invalid", "transport_timeout",
        "transport_oversize", "transport_nonzero",
    }
    flag_names = (
        "backup_created", "replacement_published", "postflight_ok", "rollback_attempted",
        "rollback_succeeded", "effect_may_have_occurred", "manual_recovery_required",
    )

    def digest(value):
        encoded = json.dumps({key: item for key, item in value.items() if key != "evidence_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return __import__("hashlib").sha256(encoded).hexdigest()

    def expected(status, code, flags):
        backup, published, postflight, attempted, succeeded, effect, recovery = flags
        if status == "blocked":
            return code in {"execution_disabled", "invalid_source", "preflight_failed", "lock_busy", "backup_conflict", "foreign_target", "write_failed"} and not any(flags)
        if status == "applied":
            return code == "none" and backup and published and postflight and not attempted and not succeeded and effect and not recovery
        if status == "rolled_back":
            return code in {"write_failed", "publish_ambiguous", "postflight_failed"} and backup and published and not postflight and attempted and succeeded and effect and not recovery
        if not effect or not recovery or postflight or succeeded:
            return False
        if code == "rollback_failed":
            return attempted and backup and published
        if attempted:
            return False
        if code == "publish_ambiguous":
            return backup and published
        if code == "foreign_target":
            return backup
        return code == "write_failed" and not published

    checks = 0
    for status, code, flags in product(statuses, sorted(known_codes), product((False, True), repeat=len(flag_names))):
        value = {
            "schema_id": replacement.SCHEMA_ID,
            "status": status,
            "error_code": code,
            **dict(zip(flag_names, flags)),
            "retry_permitted": False,
            "raw_output_visible": False,
            "environment_visible": False,
            "paths_visible": False,
            "secret_values_visible": False,
        }
        value["evidence_sha256"] = digest(value)
        want = expected(status, code, flags)
        assert replacement.validate_envelope(value) is want, (status, code, flags, "replacement")
        assert subject._valid_result(value) is want, (status, code, flags, "transport")
        checks += 1
    assert checks == len(statuses) * len(known_codes) * (2 ** len(flag_names))


def test_status_and_error_code_types_fail_closed_and_malformed_remote_is_unknown() -> None:
    for field, hostile in (("status", []), ("status", {}), ("error_code", []), ("error_code", {})):
        remote = replacement.envelope("unknown", "write_failed", effect=True, recovery=True)
        remote[field] = hostile
        remote["evidence_sha256"] = replacement._digest(remote)
        assert not replacement.validate_envelope(remote)
        assert not subject._valid_result(remote)

    repo = Path(__file__).resolve().parents[1]

    def runner(argv, **_kwargs):
        relative = argv[-1].split(":", 1)[1]
        class Result:
            returncode = 0
            stdout = (repo / relative).read_bytes()
        return Result()

    malformed = replacement.envelope("unknown", "write_failed", effect=True, recovery=True)
    malformed["error_code"] = []
    malformed["evidence_sha256"] = replacement._digest(malformed)
    class Process:
        def __init__(self, *_args, **_kwargs):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO((json.dumps(malformed, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii"))
            self.returncode = 0
        def wait(self, timeout=None): return 0
        def poll(self): return self.returncode
        def kill(self): pass

    result = subject.request_replacement(execute=True, runner=runner, popen=Process)
    assert subject.validate_envelope(result)
    assert result["status"] == "unknown"
    assert result["error_code"] == "transport_invalid"
    assert result["dispatch_invoked"] is True
    assert result["effect_may_have_occurred"] is True
    assert result["manual_recovery_required"] is True
    assert result["retry_permitted"] is False


def test_local_json_parser_rejects_duplicates_and_extra_output() -> None:
    assert subject._one_json_line(b'{"status":"blocked","status":"applied"}\n') is None
    assert subject._one_json_line(b'{"status":"blocked"}\nextra\n') is None
    assert subject._one_json_line(b'{"status":"blocked"}\r\n') is None


def test_remote_result_validator_preserves_causal_lifecycle_states() -> None:
    blocked = replacement.envelope("blocked", "preflight_failed")
    assert subject._valid_result(blocked)
    applied = replacement.envelope("applied", "none", backup=True, published=True, postflight=True, effect=True)
    assert subject._valid_result(applied)
    rolled_back = replacement.envelope("rolled_back", "postflight_failed", backup=True, published=True, rollback_attempted=True, rollback_succeeded=True, effect=True)
    assert subject._valid_result(rolled_back)
    rolled_back["postflight_ok"] = True
    rolled_back["evidence_sha256"] = replacement._digest(rolled_back)
    assert not subject._valid_result(rolled_back)
    blocked = replacement.envelope("blocked", "preflight_failed")
    blocked["backup_created"] = 1
    blocked["evidence_sha256"] = replacement._digest(blocked)
    assert not subject._valid_result(blocked)

    rollback_failed = replacement.envelope("unknown", "rollback_failed", backup=True, published=True, rollback_attempted=True, effect=True, recovery=True)
    assert replacement.validate_envelope(rollback_failed)
    assert subject._valid_result(rollback_failed)
    impossible_success = replacement.envelope("unknown", "rollback_failed", backup=True, published=True, rollback_succeeded=True, effect=True, recovery=True)
    assert not replacement.validate_envelope(impossible_success)
    assert not subject._valid_result(impossible_success)
    success_without_attempt = replacement.envelope("unknown", "write_failed", rollback_succeeded=True, effect=True, recovery=True)
    assert not replacement.validate_envelope(success_without_attempt)
    assert not subject._valid_result(success_without_attempt)


def test_transport_keeps_remote_blocked_and_rolled_back_dispatch_truth() -> None:
    remote_blocked = subject.envelope("blocked", "remote_blocked", invoked=True)
    falsely_uninvoked = subject.envelope("blocked", "remote_blocked", invoked=False)
    remote_rolled_back = subject.envelope("rolled_back", "remote_rolled_back", invoked=True, effect=True)
    assert subject.validate_envelope(remote_blocked)
    assert remote_blocked["dispatch_invoked"] is True
    assert not subject.validate_envelope(falsely_uninvoked)
    assert subject.validate_envelope(remote_rolled_back)


def test_bounded_dispatch_closes_streams_and_joins_process() -> None:
    class Process:
        def __init__(self, *_args, **_kwargs):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b'{"status":"blocked"}\n')
            self.returncode = 0
            self.killed = False
            self.waited = False

        def wait(self, timeout=None):
            self.waited = True
            return 0

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True

    spawned = []
    def popen(*args, **kwargs):
        process = Process()
        spawned.append(process)
        return process

    state, output = subject._bounded_dispatch(b"payload\n", popen=popen)
    assert state == "ok"
    assert output == b'{"status":"blocked"}\n'
    assert spawned[0].waited is True
    assert spawned[0].stdin.closed and spawned[0].stdout.closed


def test_bounded_dispatch_timeout_kills_waits_and_closes_streams(monkeypatch) -> None:
    class Process:
        def __init__(self, *_args, **_kwargs):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"")
            self.returncode = None
            self.killed = False
            self.wait_count = 0

        def wait(self, timeout=None):
            self.wait_count += 1
            if not self.killed:
                raise subprocess.TimeoutExpired("isolated", timeout)
            self.returncode = -9
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True

    process = Process()
    monkeypatch.setattr(subject, "TIMEOUT_SECONDS", 0.01)
    state, output = subject._bounded_dispatch(b"payload\n", popen=lambda *_a, **_k: process)
    assert state == "timeout"
    assert output is None
    assert process.killed and process.wait_count >= 2
    assert process.stdin.closed and process.stdout.closed


def test_dispatch_startup_failures_are_unknown_and_cleanup_spawned_processes(monkeypatch) -> None:
    class BrokenPipe:
        def __init__(self, fail_on):
            self.fail_on = fail_on
            self.closed = False

        def write(self, _payload):
            if self.fail_on == "write": raise OSError("injected write failure")
            return 1

        def flush(self): pass

        def read(self, _size=-1):
            if self.fail_on == "read": raise OSError("injected read failure")
            return b""

        def close(self): self.closed = True

    class Process:
        def __init__(self, fail_on=None):
            self.stdin = BrokenPipe(fail_on)
            self.stdout = BrokenPipe(fail_on)
            self.returncode = None
            self.killed = False
            self.wait_count = 0

        def wait(self, timeout=None):
            self.wait_count += 1
            if self.killed:
                self.returncode = -9
                return self.returncode
            raise subprocess.TimeoutExpired("synthetic-child", timeout)

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True

    repo = Path(__file__).resolve().parents[1]

    def runner(argv, **_kwargs):
        relative = argv[-1].split(":", 1)[1]
        class Result:
            returncode = 0
            stdout = (repo / relative).read_bytes()
        return Result()

    real_thread = threading.Thread
    for failure_kind, failure_index in (("popen", 1), ("construct", 1), ("construct", 2), ("start", 1), ("start", 2), ("start_after", 1), ("start_after", 2), ("read", 1), ("write", 1)):
        process = Process(failure_kind if failure_kind in {"read", "write"} else None)
        started_threads = []
        constructed = 0
        start_count = 0

        class TrackedThread:
            def __init__(self, *args, **kwargs):
                nonlocal constructed
                constructed += 1
                if failure_kind == "construct" and constructed == failure_index:
                    raise RuntimeError("injected constructor failure")
                self.inner = real_thread(*args, **kwargs)
                self.start_attempt = None

            def start(self):
                nonlocal start_count
                start_count += 1
                self.start_attempt = start_count
                if failure_kind == "start" and start_count == failure_index:
                    raise RuntimeError("injected start failure")
                self.inner.start()
                started_threads.append(self)
                if failure_kind == "start_after" and start_count == failure_index:
                    raise RuntimeError("injected post-start failure")

            def join(self, timeout=None):
                self.inner.join(timeout)
                self.joined = True

            def is_alive(self):
                return self.inner.is_alive()

            @property
            def ident(self):
                return self.inner.ident

        monkeypatch.setattr(subject.threading, "Thread", TrackedThread)

        def popen(*_args, **_kwargs):
            if failure_kind == "popen":
                raise RuntimeError("injected Popen failure")
            return process

        value = subject.request_replacement(execute=True, runner=runner, popen=popen)
        assert subject.validate_envelope(value), (failure_kind, failure_index, value)
        assert value["status"] == "unknown"
        assert value["error_code"] == "transport_ambiguous"
        assert value["dispatch_invoked"] is True
        assert value["effect_may_have_occurred"] is True
        assert value["manual_recovery_required"] is True
        assert value["retry_permitted"] is False
        if failure_kind == "popen":
            assert process.wait_count == 0
        else:
            assert process.killed and process.wait_count >= 1
            assert process.stdin.closed and process.stdout.closed
            assert all(not thread.is_alive() for thread in started_threads)
            assert all(getattr(thread, "joined", False) for thread in started_threads)
        monkeypatch.setattr(subject.threading, "Thread", real_thread)

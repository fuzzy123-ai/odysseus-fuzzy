from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ops.homeserver import redacted_runtime_probe_transport as transport


MARKER = "SYNTHETIC_SECRET_MARKER_MUST_NOT_ESCAPE"


class Result:
    def __init__(self, stdout: object, returncode: object = 0, **extra: object) -> None:
        self.stdout = stdout
        self.returncode = returncode
        for key, value in extra.items():
            setattr(self, key, value)


def _telegram() -> dict[str, bool]:
    return {
        "opaque_target_configured": True,
        "agent_reply_enabled": True,
        "send_ready": True,
        "raw_target_visible": False,
        "secret_values_visible": False,
    }


def _pc() -> dict[str, bool]:
    return {
        "enabled": True,
        "wake_enabled": True,
        "wake_target_configured": True,
        "agent_secret_configured": True,
        "wake_ready": True,
        "agent_ready": True,
        "raw_target_visible": False,
        "secret_values_visible": False,
    }


def _presence(*, extended: bool) -> dict[str, bool]:
    names = set(transport._CREDENTIAL_KEYS)
    if not extended:
        names.remove("TELEGRAM_PC_AGENT_SECRET")
    result = {name: False for name in names}
    result["TELEGRAM_BOT_TOKEN"] = True
    if extended:
        result["TELEGRAM_PC_AGENT_SECRET"] = True
    return result


def _host(*, extended: bool = False, status: str = "ok") -> dict[str, object]:
    if status == "ok":
        result: dict[str, object] = {
            "schema_id": transport.HOST_SCHEMA_ID,
            "status": "ok",
            "container": transport.DEFAULT_CONTAINER,
            "container_running": True,
            "environment_entry_count": 24,
            "credential_presence": _presence(extended=extended),
            "unknown_sensitive_key_count": 1,
            "raw_environment_visible": False,
            "secret_values_visible": False,
            "telegram_delivery_readiness": _telegram(),
        }
    else:
        result = {
            "schema_id": transport.HOST_SCHEMA_ID,
            "status": "blocked",
            "error_code": "invalid_probe_payload",
            "raw_environment_visible": False,
            "secret_values_visible": False,
            "telegram_delivery_readiness": {
                key: False for key in transport._TELEGRAM_KEYS
            },
        }
    if extended:
        result["pc_control_readiness"] = _pc() if status == "ok" else {
            key: False for key in transport._PC_KEYS
        }
    return result


def _container(*, extended: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_id": transport.CONTAINER_SCHEMA_ID,
        "environment_entry_count": 24,
        "credential_presence": _presence(extended=extended),
        "unknown_sensitive_key_count": 1,
        "telegram_delivery_readiness": _telegram(),
    }
    if extended:
        result["pc_control_readiness"] = _pc()
    return result


def _wire(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def _collect(response: object, calls: list[tuple[object, dict[str, object]]] | None = None) -> dict[str, object]:
    def runner(argv: object, **kwargs: object) -> Result:
        if calls is not None:
            calls.append((argv, kwargs))
        return response if isinstance(response, Result) else response  # type: ignore[return-value]
    return transport.collect_runtime_probe_schema_diagnostic(runner=runner)


def _assert_clean(payload: dict[str, object]) -> None:
    assert transport.validate_diagnostic_envelope(payload) is True
    assert transport._reserialize_diagnostic_envelope(payload) == payload
    assert set(payload) == transport._OUTPUT_KEYS
    encoded = json.dumps(payload, sort_keys=True)
    assert MARKER not in encoded
    for key in transport._BOOL_FIELDS[-7:]:
        assert payload[key] is False


@pytest.mark.parametrize(
    ("extended", "profile"),
    [(False, "host_v1_base"), (True, "host_v1_pc_extended")],
)
def test_exact_host_profiles_and_returncode_matrix(extended: bool, profile: str) -> None:
    for status, rc in (("ok", 0), ("blocked", 1)):
        payload = _collect(Result(_wire(_host(extended=extended, status=status)), rc))
        assert payload["diagnostic_code"] == "host_projection_valid"
        assert payload["projection_kind"] == "host"
        assert payload["shape_profile"] == profile
        assert payload["fixed_alias_contract_valid"] is True
        assert payload["host_projection_valid"] is True
        _assert_clean(payload)

    wrong_rc = _collect(Result(_wire(_host(extended=extended)), 1))
    assert wrong_rc["diagnostic_code"] == "returncode_mismatch"
    assert wrong_rc["fixed_alias_contract_valid"] is False
    _assert_clean(wrong_rc)


@pytest.mark.parametrize(
    ("extended", "profile"),
    [(False, "container_v1_base"), (True, "container_v1_pc_extended")],
)
def test_container_profiles_are_diagnosed_as_unexpected_alias_output(extended: bool, profile: str) -> None:
    payload = _collect(Result(_wire(_container(extended=extended)), 0))
    assert payload["diagnostic_code"] == "unexpected_container_projection"
    assert payload["projection_kind"] == "container"
    assert payload["shape_profile"] == profile
    assert payload["container_projection_valid"] is True
    assert payload["fixed_alias_contract_valid"] is False
    _assert_clean(payload)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"{}", "invalid_framing"),
        (b"{}\r\n", "invalid_framing"),
        (b"{}\n\n", "invalid_framing"),
        (b"{}\n{}\n", "invalid_framing"),
        (b"\xff\n", "invalid_utf8"),
        (b"{\n", "invalid_json"),
        (b'{"x":1,"x":2}\n', "duplicate_json_key"),
        (b"[]\n", "top_level_not_object"),
        (b'{"unknown":1}\n', "schema_discriminator_missing"),
        (b'{"schema_id":1}\n', "schema_discriminator_type_invalid"),
        (b'{"schema_id":"private-schema"}\n', "schema_discriminator_unknown"),
    ],
)
def test_hostile_framing_and_schema_failures_are_fixed_and_redacted(raw: bytes, code: str) -> None:
    payload = _collect(Result(raw, 0))
    assert payload["diagnostic_code"] == code
    _assert_clean(payload)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_constants_are_invalid_json(constant: str) -> None:
    payload = _collect(Result((f'{{"schema_id":"{transport.HOST_SCHEMA_ID}","value":{constant}}}\n').encode(), 0))
    assert payload["diagnostic_code"] == "invalid_json"
    assert payload["json_valid"] is False
    _assert_clean(payload)


@pytest.mark.parametrize("extended", [False, True])
def test_host_nested_leaf_count_and_invariant_failures_never_echo_marker(extended: bool) -> None:
    variants: list[tuple[dict[str, object], str]] = []
    unknown = _host(extended=extended)
    unknown["unrecognized"] = MARKER
    variants.append((unknown, "host_exact_shape_invalid"))
    nested = _host(extended=extended)
    nested["telegram_delivery_readiness"] = {"secret": MARKER}
    variants.append((nested, "host_nested_shape_invalid"))
    leaf = _host(extended=extended)
    leaf["telegram_delivery_readiness"]["send_ready"] = MARKER  # type: ignore[index]
    variants.append((leaf, "host_leaf_type_invalid"))
    count = _host(extended=extended)
    count["environment_entry_count"] = True
    variants.append((count, "host_count_invalid"))
    invariant = _host(extended=extended)
    invariant["telegram_delivery_readiness"]["send_ready"] = False  # type: ignore[index]
    variants.append((invariant, "host_invariant_invalid"))
    for value, code in variants:
        payload = _collect(Result(_wire(value), 0))
        assert payload["diagnostic_code"] == code
        _assert_clean(payload)


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("kind", ["host", "container"])
def test_unknown_sensitive_count_cannot_exceed_environment_count(kind: str, extended: bool) -> None:
    payload = _host(extended=extended) if kind == "host" else _container(extended=extended)
    payload["environment_entry_count"] = 0
    payload["unknown_sensitive_key_count"] = 1

    result = transport.classify_runtime_probe_payload(_wire(payload), returncode=0)

    _assert_clean(result)
    assert result["diagnostic_code"] == f"{kind}_invariant_invalid"
    assert result["cross_field_invariants_valid"] is False
    assert result["host_projection_valid"] is False
    assert result["container_projection_valid"] is False
    assert result["fixed_alias_contract_valid"] is False


def test_container_nested_leaf_count_and_invariant_failures_are_classified() -> None:
    variants: list[tuple[dict[str, object], str]] = []
    nested = _container()
    nested["credential_presence"] = {"unknown": MARKER}
    variants.append((nested, "container_nested_shape_invalid"))
    leaf = _container()
    leaf["credential_presence"]["GH_TOKEN"] = MARKER  # type: ignore[index]
    variants.append((leaf, "container_leaf_type_invalid"))
    count = _container()
    count["unknown_sensitive_key_count"] = -1
    variants.append((count, "container_count_invalid"))
    invariant = _container()
    invariant["telegram_delivery_readiness"]["send_ready"] = False  # type: ignore[index]
    variants.append((invariant, "container_invariant_invalid"))
    for value, code in variants:
        payload = _collect(Result(_wire(value), 0))
        assert payload["diagnostic_code"] == code
        _assert_clean(payload)


def test_fixed_alias_argv_and_runner_hostile_outputs_never_leak() -> None:
    calls: list[tuple[object, dict[str, object]]] = []
    payload = _collect(Result(_wire(_host()), 0), calls)
    assert payload["diagnostic_code"] == "host_projection_valid"
    argv, kwargs = calls[0]
    assert argv == transport.SSH_COMMAND
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "timeout": transport.TIMEOUT_SECONDS,
        "shell": False,
        "cwd": str(transport.REPOSITORY_ROOT),
    }

    hostile: list[object] = [
        Result(MARKER.encode(), 0),
        Result(_wire(_host()), True),
        Result(None, 255),
        Result(b"x" * (transport.MAX_STDOUT_BYTES + 1), 0),
        Result(_wire(_host()), 255, stderr=MARKER),
    ]
    results = [_collect(item) for item in hostile]
    assert [item["diagnostic_code"] for item in results] == [
        "invalid_framing", "transport_invocation_failed", "stdout_unavailable",
        "output_too_large", "ssh_transport_failed",
    ]
    def exception_runner(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(MARKER)

    exception_result = transport.collect_runtime_probe_schema_diagnostic(runner=exception_runner)
    assert exception_result["diagnostic_code"] == "transport_invocation_failed"
    results.append(exception_result)
    def timeout_runner(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(transport.SSH_COMMAND, 1, output=MARKER, stderr=MARKER)

    timeout_result = transport.collect_runtime_probe_schema_diagnostic(runner=timeout_runner)
    assert timeout_result["diagnostic_code"] == "transport_timeout"
    results.append(timeout_result)
    assert MARKER not in json.dumps(results, sort_keys=True)
    for item in results:
        _assert_clean(item)


@pytest.mark.parametrize("attribute", ["timed_out", "too_large", "stdout", "returncode"])
def test_raising_result_attribute_getters_fail_closed_without_leaking(attribute: str) -> None:
    class HostileResult:
        @property
        def timed_out(self) -> bool:
            if attribute == "timed_out":
                raise RuntimeError(MARKER)
            return False

        @property
        def too_large(self) -> bool:
            if attribute == "too_large":
                raise RuntimeError(MARKER)
            return False

        @property
        def stdout(self) -> bytes:
            if attribute == "stdout":
                raise RuntimeError(MARKER)
            return _wire(_host())

        @property
        def returncode(self) -> int:
            if attribute == "returncode":
                raise RuntimeError(MARKER)
            return 0

    payload = _collect(HostileResult())
    assert payload["diagnostic_code"] == "transport_invocation_failed"
    _assert_clean(payload)
    assert MARKER not in json.dumps(payload)


@pytest.mark.parametrize("attribute", ["timed_out", "too_large"])
def test_nonboolean_runner_flags_are_rejected_without_calling_hostile_bool(attribute: str) -> None:
    class HostileBool:
        def __bool__(self) -> bool:
            raise RuntimeError(MARKER)

    class HostileFlags:
        timed_out = HostileBool() if attribute == "timed_out" else False
        too_large = HostileBool() if attribute == "too_large" else False
        stdout = _wire(_host())
        returncode = 0

    payload = _collect(HostileFlags())
    assert payload["diagnostic_code"] == "transport_invocation_failed"
    _assert_clean(payload)
    assert MARKER not in json.dumps(payload)


def test_bounded_runner_reads_cap_sentinel_and_reaps_real_synthetic_child() -> None:
    child = "import sys; sys.stdout.buffer.write(b'x' * (32768 + 300)); sys.stdout.flush()"
    result = transport._bounded_process([sys.executable, "-c", child], 5)
    assert result.too_large is True
    assert len(result.stdout) == transport.MAX_STDOUT_BYTES + 1
    assert result.returncode is not None


def test_bounded_runner_timeout_kills_and_reaps_real_synthetic_child(monkeypatch: pytest.MonkeyPatch) -> None:
    real_popen = transport.subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def retaining_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(transport.subprocess, "Popen", retaining_popen)
    result = transport._bounded_process([sys.executable, "-c", "import time; time.sleep(60)"], 0.2)
    assert result.timed_out is True
    assert result.returncode is not None
    assert len(children) == 1
    assert type(children[0].poll()) is int
    assert children[0].wait(timeout=0) == children[0].returncode


def test_bounded_runner_double_timeout_raises_fixed_failure_and_closes_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePipe:
        closed = False

        def close(self) -> None:
            self.closed = True

    class DoubleTimeoutProcess:
        def __init__(self) -> None:
            self.stdout = FakePipe()
            self.returncode = None
            self.kill_count = 0
            self.wait_count = 0

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.kill_count += 1

        def wait(self, *, timeout: float) -> int:
            self.wait_count += 1
            raise subprocess.TimeoutExpired("synthetic-child", timeout, output=MARKER)

    process = DoubleTimeoutProcess()
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(transport, "_pipe_available", lambda _stream: None)
    with pytest.raises(transport._BoundedProcessFailure) as caught:
        transport._bounded_process(["synthetic-child"], 1)
    assert caught.value.args == ("bounded_process_failed",)
    assert caught.value.child_reaped is False
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert process.wait_count == 2 and process.kill_count == 1
    assert process.stdout.closed is True

    def fail_runner(*_args: object, **_kwargs: object) -> object:
        raise transport._BoundedProcessFailure(child_reaped=False)

    payload = transport.collect_runtime_probe_schema_diagnostic(runner=fail_runner)
    assert payload["diagnostic_code"] == "transport_invocation_failed"
    _assert_clean(payload)
    assert MARKER not in json.dumps(payload)


@pytest.mark.parametrize(
    ("fault", "child_reaped", "expected_kills", "expected_waits"),
    [
        ("stdout_getter", True, 0, 0),
        ("deadline", True, 0, 0),
        ("pipe_available", True, 0, 0),
        ("pipe_read", True, 0, 0),
        ("poll", True, 0, 1),
        ("kill", True, 1, 2),
        ("wait", True, 1, 2),
        ("cleanup_wait_timeout", False, 1, 2),
        ("close_after_reap", True, 0, 1),
        ("invalid_poll_bool", True, 0, 1),
        ("invalid_poll_string", True, 0, 1),
        ("invalid_wait_bool", True, 0, 1),
        ("invalid_wait_string", True, 0, 1),
        ("final_poll", True, 0, 1),
    ],
)
def test_bounded_runner_operation_faults_raise_fixed_failure(
    fault: str, child_reaped: bool, expected_kills: int, expected_waits: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePipe:
        closed = False

        def fileno(self) -> int:
            return 123

        def close(self) -> None:
            self.closed = True
            if fault == "close_after_reap":
                raise RuntimeError(MARKER)

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = FakePipe()
            self.kill_count = 0
            self.wait_count = 0
            self.poll_count = 0
            self.reaped = fault in {"stdout_getter", "deadline", "pipe_available", "pipe_read", "close_after_reap", "invalid_wait_bool"}
            self.reaped = self.reaped or fault in {"invalid_wait_string", "final_poll"}

        def poll(self) -> object:
            self.poll_count += 1
            if fault == "poll" and self.poll_count == 1:
                raise RuntimeError(MARKER)
            if fault == "final_poll" and self.poll_count == 2:
                raise RuntimeError(MARKER)
            if fault == "invalid_poll_bool":
                return True
            if fault == "invalid_poll_string":
                return "0"
            return 0 if self.reaped else None

        def kill(self) -> None:
            self.kill_count += 1
            if fault == "kill":
                raise RuntimeError(MARKER)

        def wait(self, *, timeout: float) -> object:
            self.wait_count += 1
            if fault == "cleanup_wait_timeout" and self.wait_count == 1:
                raise subprocess.TimeoutExpired("synthetic-child", timeout, output=MARKER)
            if fault == "cleanup_wait_timeout":
                raise subprocess.TimeoutExpired("synthetic-child", timeout, output=MARKER)
            if fault == "kill" and self.wait_count == 1:
                raise subprocess.TimeoutExpired("synthetic-child", timeout, output=MARKER)
            if fault == "wait" and self.wait_count == 1:
                raise RuntimeError(MARKER)
            self.reaped = True
            if fault == "invalid_wait_bool":
                return True
            if fault == "invalid_wait_string":
                return "0"
            return 0

    process = FakeProcess()
    if fault == "stdout_getter":
        class HostileStdoutProcess(FakeProcess):
            @property
            def stdout(self) -> object:
                raise RuntimeError(MARKER)

            @stdout.setter
            def stdout(self, value: object) -> None:
                self._stdout = value
        process = HostileStdoutProcess()

    monkeypatch.setattr(transport.subprocess, "Popen", lambda *_args, **_kwargs: process)
    if fault == "deadline":
        calls = 0

        def failing_clock() -> float:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError(MARKER)
            return 1.0

        monkeypatch.setattr(transport.time, "monotonic", failing_clock)
    elif fault == "pipe_available":
        def fail_available(_stream: object) -> int:
            raise RuntimeError(MARKER)
        monkeypatch.setattr(transport, "_pipe_available", fail_available)
    elif fault == "pipe_read":
        monkeypatch.setattr(transport, "_pipe_available", lambda _stream: 1)
        monkeypatch.setattr(transport.os, "read", lambda *_args: (_ for _ in ()).throw(RuntimeError(MARKER)))
    else:
        monkeypatch.setattr(transport, "_pipe_available", lambda _stream: None)

    with pytest.raises(transport._BoundedProcessFailure) as caught:
        transport._bounded_process(["synthetic-child"], 1)
    assert caught.value.child_reaped is child_reaped
    assert caught.value.args == ("bounded_process_failed",)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert MARKER not in str(caught.value)
    assert process.kill_count == expected_kills
    assert process.wait_count == expected_waits
    if fault == "stdout_getter":
        assert process._stdout.closed is True  # type: ignore[attr-defined]
    else:
        assert process.stdout.closed is True


def test_collector_converts_bounded_failure_to_fixed_invocation_failure() -> None:
    def runner(*_args: object, **_kwargs: object) -> object:
        raise transport._BoundedProcessFailure(child_reaped=False)

    result = transport.collect_runtime_probe_schema_diagnostic(runner=runner)
    assert result["diagnostic_code"] == "transport_invocation_failed"
    _assert_clean(result)
    assert MARKER not in json.dumps(result)


def test_cli_converts_bounded_failure_to_fixed_invocation_failure_without_marker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_bounded_process(*_args: object, **_kwargs: object) -> object:
        raise transport._BoundedProcessFailure(child_reaped=False)

    monkeypatch.setattr(transport, "_bounded_process", fail_bounded_process)
    assert transport.main([]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["diagnostic_code"] == "transport_invocation_failed"
    _assert_clean(payload)
    assert MARKER not in captured.out and MARKER not in captured.err
    assert captured.err == ""


def test_child_exit_with_buffered_stdout_drains_to_eof_without_a_reader_thread() -> None:
    child = "import sys; sys.stdout.buffer.write(b'x' * 20000); sys.stdout.flush()"
    result = transport._bounded_process([sys.executable, "-c", child], 5)
    assert result.timed_out is False
    assert result.too_large is False
    assert result.stdout == b"x" * 20000
    assert result.returncode == 0


def test_child_closes_stdout_before_exit_and_is_killed_at_the_original_deadline() -> None:
    child = "import os, time; os.close(1); time.sleep(60)"
    start = transport.time.monotonic()
    result = transport._bounded_process([sys.executable, "-c", child], 0.2)
    elapsed = transport.time.monotonic() - start
    assert result.timed_out is True
    assert result.returncode is not None
    assert elapsed < 2.0


@pytest.mark.parametrize(
    ("raw", "returncode", "code"),
    [
        (b"malformed", 255, "ssh_transport_failed"),
        (_wire(_host()), 255, "ssh_transport_failed"),
        (b"malformed", 2, "ssh_transport_failed"),
        (_wire(_host()), True, "ssh_transport_failed"),
        (b"x" * (transport.MAX_STDOUT_BYTES + 1), 255, "output_too_large"),
    ],
    ids=["malformed-255", "valid-host-255", "other-rc", "bool-rc", "oversized-255"],
)
def test_public_classifier_transport_precedence_is_fixed_and_bounded(raw: bytes, returncode: int, code: str) -> None:
    payload = transport.classify_runtime_probe_payload(raw, returncode=returncode)
    assert payload["diagnostic_code"] == code
    _assert_clean(payload)


def test_unknown_cli_arguments_are_not_echoed_and_emit_only_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    assert transport.main([MARKER]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    _assert_clean(payload)
    assert payload["diagnostic_code"] == "transport_invocation_failed"
    assert MARKER not in captured.out and MARKER not in captured.err
    assert captured.err == ""


def test_envelope_validator_rejects_extra_types_and_true_visibility() -> None:
    base = transport._envelope("host_projection_valid", ssh_exit_class="zero", projection_kind="host",
                               shape_profile="host_v1_base")
    variants = []
    extra = dict(base); extra["private"] = MARKER; variants.append(extra)
    bad_bool = dict(base); bad_bool["json_valid"] = 1; variants.append(bad_bool)
    visible = dict(base); visible["secret_values_visible"] = True; variants.append(visible)
    bad_enum = dict(base); bad_enum["diagnostic_code"] = MARKER; variants.append(bad_enum)
    assert all(transport.validate_diagnostic_envelope(item) is False for item in variants)
    forged = dict(base)
    forged["fixed_alias_contract_valid"] = True
    assert transport.validate_diagnostic_envelope(forged) is False


@pytest.mark.parametrize(
    ("code", "status"),
    [("host_projection_valid", "blocked"), ("transport_timeout", "diagnosed")],
)
def test_envelope_status_code_mismatches_return_literal_false(code: str, status: str) -> None:
    payload = transport._envelope(code)
    payload["status"] = status
    result = transport.validate_diagnostic_envelope(payload)
    assert result is False
    assert type(result) is bool


def test_cli_reserializes_hostile_runner_failure_without_marker_or_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Hostile:
        @property
        def stdout(self) -> bytes:
            raise RuntimeError(MARKER)

    safe = transport.collect_runtime_probe_schema_diagnostic(runner=lambda *_a, **_k: Hostile())
    monkeypatch.setattr(transport, "collect_runtime_probe_schema_diagnostic", lambda: safe)
    assert transport.main([]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == safe
    assert MARKER not in captured.out and MARKER not in captured.err
    assert captured.err == ""

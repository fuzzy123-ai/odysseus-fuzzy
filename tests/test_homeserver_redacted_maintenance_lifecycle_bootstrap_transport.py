from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ops.homeserver import redacted_maintenance_lifecycle_bootstrap_transport as transport
from ops.homeserver import redacted_transactional_app_deploy_transport as deploy_transport


def test_transport_is_inert_without_explicit_execute_and_cli_stays_disabled(capsys: object) -> None:
    result = transport.collect_published_lifecycle_bootstrap()
    assert transport.validate_envelope(result)
    assert result["status"] == "blocked"
    assert transport.main(["--execute"]) == 1
    assert '"effect_may_have_occurred":false' in capsys.readouterr().out


def test_transport_uses_fixed_sudo_start_and_readback_without_python_c() -> None:
    command = " ".join(transport.SSH_COMMAND)
    remote = transport._REMOTE_PARENT
    assert "['/usr/bin/sudo','-n','/usr/bin/systemctl','start','--wait',UNIT]" in remote
    assert "['/usr/bin/sudo','-n','/usr/bin/python3','-I',READBACK]" in remote
    assert "sudo -n /usr/bin/python3 -I -c" not in command
    assert "sudo -n /usr/bin/python3 -I -c" not in remote
    assert remote.count("systemctl','start','--wait'") == 1
    assert remote.index("before=readback()") < remote.index("systemctl','start','--wait'") < remote.index("after=readback()")


def test_transport_returns_only_validated_bounded_envelope() -> None:
    expected = transport._envelope("ready", "none")
    calls: list[tuple[str, ...]] = []

    def run(command: list[str], **kwargs: object) -> object:
        calls.append(tuple(command))
        assert kwargs["input"] == b""
        assert kwargs["stderr"] == subprocess.DEVNULL
        return type("Result", (), {"stdout": json.dumps(expected, sort_keys=True, separators=(",", ":")).encode() + b"\n", "returncode": 0})()

    value = transport.collect_published_lifecycle_bootstrap(execute=True, runner=run)
    assert transport.validate_envelope(value)
    assert value["status"] == "ready"
    assert len(calls) == 1
    assert "ssh" in calls[0][0]


def test_transport_connection_loss_is_unknown_without_automatic_retry() -> None:
    calls = 0

    def run(_command: list[str], **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired("ssh", 50)

    value = transport.collect_published_lifecycle_bootstrap(execute=True, runner=run)
    assert transport.validate_envelope(value)
    assert value["status"] == "unknown"
    assert value["error_code"] == "transport_timeout"
    assert calls == 1


def test_local_timeout_kills_and_reaps_the_entire_process_group() -> None:
    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory(prefix="odysseus-bootstrap-transport-") as directory:
        marker = str(Path(directory) / "escaped-child-marker")
        child = "import pathlib,time; time.sleep(1); pathlib.Path(" + repr(marker) + ").write_text('alive')"
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(10)"
        command = (sys.executable, "-c", parent)
        result = transport._run_local_process_group(command, input_bytes=b"", timeout=0.1)
        assert getattr(result, "timed_out", False) is True
        assert result.returncode is not None
        time.sleep(1.1)
        assert not Path(marker).exists()


def test_local_transport_bounds_stdout_while_reading() -> None:
    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory(prefix="odysseus-overflow-group-") as directory:
        marker = str(Path(directory) / "descendant-survived")
        child = "import pathlib,time; time.sleep(.4); pathlib.Path(" + repr(marker) + ").write_text('alive')"
        parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-I','-c'," + repr(child) + "]); sys.stdout.write('x' * 9000)"
        try:
            transport._run_local_process_group((sys.executable, "-c", parent), input_bytes=b"", timeout=2)
        except ValueError as exc:
            assert str(exc) == "transport child output exceeded fixed bound"
        else:
            raise AssertionError("oversized transport output was accepted")
        time.sleep(0.6)
        assert not Path(marker).exists()


def test_local_transport_overflow_precedes_early_stdin_close_after_group_cleanup() -> None:
    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory(prefix="odysseus-overflow-stdin-") as directory:
        marker = str(Path(directory) / "descendant-survived")
        delayed = "import pathlib,time; time.sleep(.4); pathlib.Path(" + repr(marker) + ").write_text('alive')"
        child = (
            "import os,subprocess,sys; os.close(0); "
            "subprocess.Popen([sys.executable,'-I','-c'," + repr(delayed) + "]); "
            "sys.stdout.write('x'*9000); sys.stdout.flush()"
        )
        try:
            transport._run_local_process_group(
                (sys.executable, "-I", "-c", child), input_bytes=b"i" * 200_000, timeout=3
            )
        except ValueError as exc:
            assert str(exc) == "transport child output exceeded fixed bound"
        else:
            raise AssertionError("overflow was hidden by the early stdin close")
        time.sleep(0.6)
        assert not Path(marker).exists()


def test_local_transport_nonblocking_full_duplex_handles_pipe_pressure() -> None:
    if os.name != "posix":
        return
    payload = b"i" * 200_000
    child = (
        "import os,sys; os.write(1,b'o'*4096); "
        "data=sys.stdin.buffer.read(); os.write(1,b'\\n'+str(len(data)).encode())"
    )
    result = transport._run_local_process_group(
        (sys.executable, "-I", "-c", child), input_bytes=payload, timeout=3
    )
    assert result.returncode == 0
    assert result.stdout == b"o" * 4096 + b"\n200000"


def test_local_transport_blocked_large_stdin_times_out_and_kills_group() -> None:
    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory(prefix="odysseus-bootstrap-blocked-stdin-") as directory:
        marker = str(Path(directory) / "survivor-marker")
        descendant = "import pathlib,time; time.sleep(.5); pathlib.Path(" + repr(marker) + ").write_text('alive')"
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(descendant) + "]); time.sleep(10)"
        started = time.monotonic()
        result = transport._run_local_process_group(
            (sys.executable, "-I", "-c", parent), input_bytes=b"x" * 200_000, timeout=0.1
        )
        assert getattr(result, "timed_out", False) is True
        assert result.returncode is not None
        assert time.monotonic() - started < 2.5
        time.sleep(0.6)
        assert not Path(marker).exists()


def test_local_transport_rejects_nonfinite_and_nonpositive_timeouts() -> None:
    if os.name != "posix":
        return
    for timeout in (float("nan"), float("inf"), float("-inf"), 0, -1):
        try:
            transport._run_local_process_group((sys.executable, "-I", "-c", "pass"), input_bytes=b"", timeout=timeout)
        except ValueError as exc:
            assert str(exc) == "invalid bounded child timeout"
        else:
            raise AssertionError("invalid timeout was accepted")


def test_local_transport_times_out_when_child_closes_stdout_early() -> None:
    if os.name != "posix":
        return
    command = (sys.executable, "-c", "import os,time; os.close(1); time.sleep(10)")
    started = time.monotonic()
    result = transport._run_local_process_group(command, input_bytes=b"", timeout=0.1)
    assert getattr(result, "timed_out", False) is True
    assert result.returncode is not None and time.monotonic() - started < 3


def test_local_transport_accepts_early_stdout_close_when_child_finishes_in_budget() -> None:
    if os.name != "posix":
        return
    command = (sys.executable, "-c", "import os,time; os.close(1); time.sleep(0.05)")
    result = transport._run_local_process_group(command, input_bytes=b"", timeout=2)
    assert result.returncode == 0 and result.stdout == b""
    assert getattr(result, "timed_out", False) is False


def test_local_transport_kills_descendant_after_leader_exits_with_pipe_open() -> None:
    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory(prefix="odysseus-bootstrap-pipe-holder-") as directory:
        marker = str(Path(directory) / "child-survived")
        child = "import pathlib,time; time.sleep(1); pathlib.Path(" + repr(marker) + ").write_text('alive')"
        parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "])"
        result = transport._run_local_process_group((sys.executable, "-c", parent), input_bytes=b"", timeout=3)
        assert result.returncode == 0
        time.sleep(1.1)
        assert not Path(marker).exists()


def test_local_transport_kills_residual_group_after_successful_leader_exit() -> None:
    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory(prefix="odysseus-bootstrap-residual-pipe-") as directory:
        marker = str(Path(directory) / "child-survived")
        child = "import os,pathlib,time; os.close(1); time.sleep(.4); pathlib.Path(" + repr(marker) + ").write_text('alive')"
        parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "])"
        command = (sys.executable, "-c", parent)
        result = transport._run_local_process_group(command, input_bytes=b"", timeout=2)
        assert result.returncode == 0 and getattr(result, "timed_out", False) is False
        time.sleep(0.6)
        assert not Path(marker).exists()


def test_windows_job_transport_bounds_output_timeout_and_owns_descendants(monkeypatch: object) -> None:
    if os.name != "nt":
        return
    run = transport._run_windows_job_process_group
    successful = run((sys.executable, "-I", "-c", "print('bounded')"), input_bytes=b"", timeout=3)
    assert successful.returncode == 0 and successful.stdout == b"bounded\r\n"

    original_start = threading.Thread.start

    def delay_reader_start(thread: threading.Thread) -> None:
        if thread.name == "lifecycle-stdout-reader":
            original_run = thread.run

            def delayed_run() -> None:
                time.sleep(0.4)
                original_run()

            thread.run = delayed_run  # type: ignore[method-assign]
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", delay_reader_start)
    try:
        try:
            run(
                (sys.executable, "-I", "-c", "import os; os.write(1, b'xx')"),
                input_bytes=b"",
                timeout=3,
                output_limit=1,
            )
        except ValueError as exc:
            assert str(exc) == "transport child output exceeded fixed bound"
        else:
            raise AssertionError("late-drained oversized Windows output was accepted")
    finally:
        monkeypatch.setattr(threading.Thread, "start", original_start)

    for invalid_timeout in (float("nan"), float("inf"), float("-inf"), 1231):
        try:
            run((sys.executable, "-I", "-c", "pass"), input_bytes=b"", timeout=invalid_timeout)
        except ValueError as exc:
            assert str(exc) == "invalid bounded child timeout"
        else:
            raise AssertionError("invalid Windows child timeout was accepted")

    with tempfile.TemporaryDirectory(prefix="odysseus-winjob-timeout-") as directory:
        marker = str(Path(directory) / "timeout-survived")
        command = (sys.executable, "-I", "-c", "import pathlib,time; time.sleep(1); pathlib.Path(" + repr(marker) + ").write_text('alive')")
        timed = run(command, input_bytes=b"", timeout=0.1)
        assert getattr(timed, "timed_out", False) is True
        time.sleep(1.1)
        assert not Path(marker).exists()

    for close_stdout in (False, True):
        with tempfile.TemporaryDirectory(prefix="odysseus-winjob-descendant-") as directory:
            marker = str(Path(directory) / "descendant-survived")
            close = "os.close(1); " if close_stdout else ""
            child = "import os,pathlib,time; " + close + "time.sleep(.4); pathlib.Path(" + repr(marker) + ").write_text('alive')"
            leader = "import subprocess,sys; subprocess.Popen([sys.executable,'-I','-c'," + repr(child) + "])"
            result = deploy_transport._run_local_process_group(
                (sys.executable, "-I", "-c", leader), input_bytes=b"", timeout=3
            )
            assert result.returncode == 0 and getattr(result, "timed_out", False) is False
            time.sleep(0.6)
            assert not Path(marker).exists()

    with tempfile.TemporaryDirectory(prefix="odysseus-winjob-no-handshake-") as directory:
        marker = str(Path(directory) / "assignment-failure-child")

        class AssignmentFailure:
            def __init__(self) -> None:
                pass

            def assign(self, _process_handle: int) -> None:
                raise RuntimeError("injected assignment failure")

            def close(self) -> None:
                pass

        monkeypatch.setattr(transport, "_WindowsKillOnCloseJob", AssignmentFailure)
        child = "import pathlib; pathlib.Path(" + repr(marker) + ").write_text('alive')"
        try:
            run((sys.executable, "-I", "-c", child), input_bytes=b"", timeout=3)
        except RuntimeError as exc:
            assert str(exc) == "Windows child execution failed"
        else:
            raise AssertionError("job assignment failure was accepted")
        assert not Path(marker).exists()


def test_transport_unknown_envelope_and_type_checks() -> None:
    payload = transport.unknown("root_child_timeout")
    assert transport.validate_envelope(payload)
    payload["status"] = []
    payload["evidence_sha256"] = transport._digest(payload)
    assert transport.validate_envelope(payload) is False


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))

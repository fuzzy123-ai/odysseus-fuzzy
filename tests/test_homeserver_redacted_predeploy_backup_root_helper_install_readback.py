from __future__ import annotations

import hashlib
import io
from pathlib import Path
import subprocess
from types import SimpleNamespace
import stat
import pytest

from ops.homeserver import redacted_predeploy_backup_root_helper_install_readback as subject


class _ShowReader(io.BytesIO):
    def __init__(self, value: bytes, *, fail: bool = False) -> None:
        super().__init__(value)
        self.fail = fail

    def read(self, count: int = -1) -> bytes:
        if self.fail:
            raise OSError("controlled reader failure")
        return super().read(count)

    def fileno(self) -> int: return 97


class _ShowProcess:
    def __init__(self, value: bytes, *, returncode: int = 0, timeout: bool = False, kill_raises: bool = False, reader_fails: bool = False) -> None:
        self.stdin = io.BytesIO()
        self.stdout = _ShowReader(value, fail=reader_fails)
        self.returncode = returncode
        self.timeout = timeout
        self.kill_raises = kill_raises
        self.kill_calls = 0
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.timeout and self.wait_calls == 1:
            raise subprocess.TimeoutExpired("systemctl", timeout)
        return self.returncode

    def poll(self): return None if self.timeout else self.returncode

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_raises:
            raise OSError("controlled kill failure")


def _show_popen(process: _ShowProcess, seen: list[tuple[object, object]]) -> object:
    def call(argv, **kwargs):
        seen.append((argv, kwargs))
        return process
    return call


def _show_output(*, active: str = "inactive", sub: str = "dead", main: str = "0", control: str = "0", load: str = "loaded", unit: str = "disabled") -> bytes:
    return (f"LoadState={load}\nUnitFileState={unit}\nActiveState={active}\nSubState={sub}\nMainPID={main}\nControlPID={control}\n").encode("ascii")


@pytest.fixture(autouse=True)
def _nonblocking_fd(monkeypatch):
    monkeypatch.setattr(subject.os, "set_blocking", lambda descriptor, enabled: None)


def _good(monkeypatch) -> None:
    monkeypatch.setattr(subject, "_asset_valid", lambda *args, **kwargs: True)
    monkeypatch.setattr(subject, "_parents_valid", lambda **kwargs: True)
    monkeypatch.setattr(subject, "_state_safe_and_arm_absent", lambda **kwargs: (True, False))
    monkeypatch.setattr(subject, "_systemctl_state", lambda *args, **kwargs: True)


def test_positive_readback_is_strictly_redacted(monkeypatch) -> None:
    _good(monkeypatch)
    value = subject.collect()
    assert subject.validate(value) and value["status"] == "available" and value["arm_present"] is False


def test_every_asset_or_state_failure_is_unknown(monkeypatch) -> None:
    _good(monkeypatch)
    monkeypatch.setattr(subject, "_asset_valid", lambda *args, **kwargs: False)
    assert subject.collect()["status"] == "unknown"  # symlink, hardlink, owner, mode, or hash rejection
    _good(monkeypatch); monkeypatch.setattr(subject, "_parents_valid", lambda **kwargs: False)
    assert subject.collect()["status"] == "unknown"
    _good(monkeypatch); monkeypatch.setattr(subject, "_state_safe_and_arm_absent", lambda **kwargs: (False, True))
    value = subject.collect(); assert value["status"] == "unknown" and value["arm_present"] is True
    _good(monkeypatch); monkeypatch.setattr(subject, "_systemctl_state", lambda argument, **kwargs: argument == "is-enabled")
    assert subject.collect()["status"] == "unknown"


def test_readback_has_descriptor_walk_exact_four_asset_pins_and_bounded_systemctl() -> None:
    assert len(subject.ASSETS) == 4
    text = Path(subject.__file__).read_text(encoding="utf-8")
    assert "O_NOFOLLOW" in text and "dir_fd=current" in text and "before.st_nlink == 1" in text
    assert all(field in text for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "api.read(fd, 1) == b\"\""))
    assert "hashlib.sha256" in text and '"/usr/bin/systemctl"' in text and "stderr=subprocess.DEVNULL" in text
    assert "disabled\\n" in text and "static\\n" in text and "inactive\\n" in text and "arm.json" in text


def test_validator_rejects_tampering(monkeypatch) -> None:
    _good(monkeypatch)
    value = subject.collect(); value["unit_inactive"] = False; value["evidence_sha256"] = subject._digest(value)
    assert not subject.validate(value)


def test_diagnostic_readback_is_closed_about_terminal_state_and_pids(monkeypatch) -> None:
    _good(monkeypatch)
    monkeypatch.setattr(subject, "_show_unit_state", lambda **kwargs: ("inactive", True, False, False))
    value = subject.collect_diagnostic_readback_v2()
    assert subject.validate_diagnostic(value) and value["status"] == "available"
    monkeypatch.setattr(subject, "_show_unit_state", lambda **kwargs: ("failed", True, False, False))
    assert subject.collect_diagnostic_readback_v2()["unit_terminal_state"] == "failed"
    monkeypatch.setattr(subject, "_show_unit_state", lambda **kwargs: ("inactive", True, True, False))
    assert subject.collect_diagnostic_readback_v2()["status"] == "unknown"
    bad = dict(value); bad["unit_terminal_state"] = "active"; bad["evidence_sha256"] = subject._digest(bad)
    assert not subject.validate_diagnostic(bad)


def test_show_unit_state_uses_fixed_systemctl_argv_and_recognizes_terminal_rows() -> None:
    seen: list[tuple[object, object]] = []
    process = _ShowProcess(_show_output())
    assert subject._show_unit_state(popen=_show_popen(process, seen)) == ("inactive", True, False, False)
    assert seen == [(
        ["/usr/bin/systemctl", "show", "--property=LoadState,UnitFileState,ActiveState,SubState,MainPID,ControlPID", subject.UNIT],
        {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "shell": False, "bufsize": 0},
    )]
    assert process.stdin.closed and process.stdout.closed
    assert subject._show_unit_state(popen=_show_popen(_ShowProcess(_show_output(active="failed", sub="failed", unit="static")), [])) == ("failed", True, False, False)


def test_show_unit_state_recognizes_all_pid_combinations_and_nonterminal_systemd_rows() -> None:
    for terminal, active, sub in (("inactive", "inactive", "dead"), ("failed", "failed", "failed")):
        for main in ("0", "73"):
            for control in ("0", "74"):
                actual = subject._show_unit_state(popen=_show_popen(_ShowProcess(_show_output(active=active, sub=sub, main=main, control=control)), []))
                assert actual == (terminal, True, main != "0", control != "0")
    for active, sub in (("activating", "start-pre"), ("activating", "start-post"), ("deactivating", "stop-sigterm"), ("deactivating", "stop-sigkill")):
        assert subject._show_unit_state(popen=_show_popen(_ShowProcess(_show_output(active=active, sub=sub)), [])) == ("nonterminal", True, False, False)


def test_show_unit_state_rejects_malformed_unloaded_and_nonterminal_contradictions() -> None:
    good = _show_output()
    malformed = (
        good.replace(b"ControlPID=0\n", b"MainPID=0\n"),
        b"LoadState=loaded\nUnitFileState=disabled\nActiveState=inactive\nSubState=dead\nMainPID=0\n",
        good + b"Extra=x\n",
        good.replace(b"LoadState=loaded", b"LoadState=load\xffed"),
        _show_output(load="not-found"),
        _show_output(main="+1"),
        _show_output(active="inactive", sub="running"),
        _show_output(active="active", sub="start-pre"),
        _show_output(active="activating", sub="bogus"),
        _show_output(active="deactivating", sub="start-post"),
    )
    for value in malformed:
        assert subject._show_unit_state(popen=_show_popen(_ShowProcess(value), [])) == ("unavailable", False, False, False)
    assert subject._show_unit_state(popen=_show_popen(_ShowProcess(good, returncode=1), [])) == ("unavailable", False, False, False)


def test_show_unit_state_bounds_timeout_overflow_reader_failure_and_blocked_reader(monkeypatch) -> None:
    monkeypatch.setattr(subject, "SYSTEMCTL_TIMEOUT_SECONDS", 0)
    timeout = _ShowProcess(_show_output(), timeout=True)
    assert subject._show_unit_state(popen=_show_popen(timeout, [])) == ("unavailable", False, False, False)
    assert timeout.kill_calls == 1 and timeout.wait_calls >= 1 and timeout.stdin.closed and timeout.stdout.closed
    overflow = _ShowProcess(b"x" * 257 + b"\n")
    assert subject._show_unit_state(popen=_show_popen(overflow, [])) == ("unavailable", False, False, False)
    failed = _ShowProcess(_show_output(), reader_fails=True)
    assert subject._show_unit_state(popen=_show_popen(failed, [])) == ("unavailable", False, False, False)

    class WouldBlock(_ShowReader):
        def read(self, count: int = -1): return None
    blocked = _ShowProcess(_show_output(), timeout=True)
    blocked.stdout = WouldBlock(b"")
    assert subject._show_unit_state(popen=_show_popen(blocked, [])) == ("unavailable", False, False, False)
    assert blocked.kill_calls == 1 and blocked.wait_calls >= 1 and blocked.stdin.closed and blocked.stdout.closed


def test_systemctl_paths_are_unbuffered_and_asset_pins_match_frozen_bytes() -> None:
    seen: list[tuple[object, object]] = []
    assert subject._systemctl_state("is-enabled", popen=_show_popen(_ShowProcess(b"disabled\n"), seen))
    assert seen[0][1]["bufsize"] == 0
    assert "threading.Thread" not in Path(subject.__file__).read_text(encoding="utf-8")
    root = Path(subject.__file__).parents[2]
    local_assets = {
        "/usr/local/libexec/odysseus-predeploy-backup-root-helper.py": root / "ops/homeserver/redacted_predeploy_backup_root_helper.py",
        "/usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py": root / "ops/homeserver/redacted_predeploy_backup_root_helper_readback.py",
        "/etc/systemd/system/odysseus-predeploy-backup-root-helper.service": root / "ops/homeserver/root-helper/odysseus-predeploy-backup-root-helper.service",
        "/etc/sudoers.d/odysseus-predeploy-backup-root-helper": root / "ops/homeserver/root-helper/odysseus-predeploy-backup-root-helper.sudoers",
    }
    assert {remote: digest for remote, digest, _ in subject.ASSETS} == {remote: hashlib.sha256(path.read_bytes()).hexdigest() for remote, path in local_assets.items()}


def test_show_unit_state_attempts_reap_even_if_kill_throws(monkeypatch) -> None:
    monkeypatch.setattr(subject, "SYSTEMCTL_TIMEOUT_SECONDS", 0)
    process = _ShowProcess(_show_output(), timeout=True, kill_raises=True)
    assert subject._show_unit_state(popen=_show_popen(process, [])) == ("unavailable", False, False, False)
    assert process.kill_calls == 1 and process.wait_calls >= 1 and process.stdin.closed and process.stdout.closed


def test_nonblocking_entrypoints_reap_all_failed_reader_paths(monkeypatch) -> None:
    class InvalidFd(_ShowReader):
        def fileno(self): return "bad"
    class NonBytes(_ShowReader):
        def read(self, count: int = -1): return "bad"
    class ReadFailure(_ShowReader):
        def read(self, count: int = -1): raise OSError("read")
    def invoke(entry, process):
        value = subject._systemctl_state("is-enabled", popen=_show_popen(process, [])) if entry == "legacy" else subject._show_unit_state(popen=_show_popen(process, []))
        assert value is False if entry == "legacy" else value == ("unavailable", False, False, False)
        assert process.kill_calls == 1 and process.wait_calls >= 1 and process.stdin.closed and process.stdout.closed
    for entry in ("legacy", "diagnostic"):
        process = _ShowProcess(b""); process.stdout = InvalidFd(b""); invoke(entry, process)
        process = _ShowProcess(b""); process.stdout = NonBytes(b""); invoke(entry, process)
        process = _ShowProcess(b""); process.stdout = ReadFailure(b""); invoke(entry, process)
        process = _ShowProcess(b""); process.poll = lambda: (_ for _ in ()).throw(OSError("poll")); invoke(entry, process)
        process = _ShowProcess(b"x" * 257, timeout=True); invoke(entry, process)
    monkeypatch.setattr(subject.os, "set_blocking", lambda descriptor, enabled: (_ for _ in ()).throw(OSError("setup")))
    for entry in ("legacy", "diagnostic"):
        process = _ShowProcess(b""); invoke(entry, process)


def test_legacy_validator_is_total_and_requires_exact_json_booleans(monkeypatch) -> None:
    _good(monkeypatch)
    value = subject.collect()
    for change in ({"assets_valid": []}, {"arm_present": 0}, {"status": {}}):
        forged = dict(value); forged.update(change)
        forged["evidence_sha256"] = subject._digest(forged)
        assert not subject.validate(forged)
    forged = dict(value); forged["evidence_sha256"] = object()
    assert not subject.validate(forged)
    assert not subject.validate(object())


def test_readback_cli_has_closed_flag_and_15_field_diagnostic_projection(monkeypatch, capsys) -> None:
    calls: list[str] = []
    legacy = {"legacy": True}
    diagnostic = subject._diagnostic_result(assets=True, parents=True, state=True, disabled=True, arm=False, terminal="inactive", main=False, control=False)
    monkeypatch.setattr(subject, "collect", lambda: calls.append("legacy") or legacy)
    monkeypatch.setattr(subject, "collect_diagnostic_readback_v2", lambda: calls.append("diagnostic") or diagnostic)
    assert subject.main([]) == 0 and calls == ["legacy"] and capsys.readouterr().out
    assert subject.main(["--diagnostic-v2"]) == 0 and calls == ["legacy", "diagnostic"]
    assert set(diagnostic) == subject._DIAGNOSTIC_KEYS and len(diagnostic) == 15 and subject.validate_diagnostic(diagnostic)
    assert subject.main(["--unknown"]) == 2 and calls == ["legacy", "diagnostic"]


def test_state_directory_is_validated_after_nofollow_open_and_rejects_opened_unsafe_dir(monkeypatch) -> None:
    monkeypatch.setattr(subject, "_open_parent", lambda *args, **kwargs: (41, "state"))
    class Unsafe:
        def open(self, *args, **kwargs): return 42
        def fstat(self, descriptor): return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0, st_gid=99, st_nlink=2, st_dev=1, st_ino=2, st_mtime_ns=3, st_ctime_ns=4)
        def close(self, descriptor): pass
    assert subject._state_safe_and_arm_absent(api=Unsafe()) == (False, True)

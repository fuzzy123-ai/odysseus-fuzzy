from __future__ import annotations

import errno
import types

import pytest

from ops.homeserver import redacted_predeploy_backup_podman_unshare_stage_diagnostic as subject


def test_default_is_inert_and_visibility_is_always_false() -> None:
    value = subject.collect_podman_unshare_stage_diagnostic()
    assert subject.validate_envelope(value)
    assert value["status"] == "blocked" and value["probe_invoked"] is False
    assert not any(value[key] for key in subject.VISIBILITY_KEYS)


def test_validator_rejects_cross_products_digest_and_visibility() -> None:
    good = subject._packet("unsupported", "tmpfs", True)
    assert subject.validate_envelope(good)
    good["errno_visible"] = True
    assert not subject.validate_envelope(good)
    bad = subject._packet("unsupported", "tmpfs", True)
    bad["dispatch"] = True
    bad["evidence_sha256"] = subject._digest(bad)
    assert not subject.validate_envelope(bad)


def _chain(*, failed: str | None = None) -> tuple[str, list[tuple], dict[int, int]]:
    calls: list[tuple] = []
    flags = {22: 0}
    def fail(stage: str) -> None:
        if failed == stage:
            raise OSError()
    def unshare(flag: int) -> int:
        calls.append(("unshare", flag))
        return 1 if failed == "mount_namespace" else 0
    def mount(*args):
        calls.append(args)
        stages = {0x44000: "private_propagation", 6: "tmpfs", 0x5000: "descriptor_bind_identity", 0x1021: "read_only_remount"}
        fail(stages[args[3]])
    def reader(path: str, maximum: int) -> str:
        return "21 20 0:1 / / rw,relatime shared:7 - tmpfs tmpfs rw\n" if failed == "private_propagation" else "21 20 0:1 / / rw,relatime - tmpfs tmpfs rw\n"
    def fstat(fd: int): return types.SimpleNamespace(st_dev=1, st_ino=2)
    def stat_path(path: str): return types.SimpleNamespace(st_dev=1, st_ino=3 if failed == "descriptor_bind_identity" else 2)
    def statvfs(path: str): return types.SimpleNamespace(f_flag=0 if failed == "read_only_remount" else getattr(subject.os, "ST_RDONLY", 1))
    def close(fd: int) -> bool: return failed != "source_fd_ebadf"
    def getfd(fd: int, command: int) -> int: return flags[fd]
    def setfd(fd: int, command: int, value: int) -> None:
        fail("executable_cloexec")
        flags[fd] = value
    return subject._perform_chain(11, 22, unshare=unshare, mount_call=mount, reader=reader, mkdir=lambda path, mode: fail("tmpfs"),
                                  fstat_call=fstat, stat_call=stat_path, statvfs_call=statvfs, close_verified=close,
                                  getfd=getfd, setfd=setfd, cloexec=1, getfd_flag=1), calls, flags


def test_real_chain_injection_proves_every_stage_and_full_positive_path() -> None:
    stage, calls, flags = _chain()
    assert stage == "none" and flags[22] & 1
    assert calls[0] == ("unshare", 0x00020000)
    assert calls[1][3] == 0x44000 and calls[2][:4] == ("tmpfs", "/tmp", "tmpfs", 6)
    assert calls[3][0] == "/proc/self/fd/11" and calls[4][3] == 0x1021
    for expected in subject.STAGES[1:-1]:
        assert _chain(failed=expected)[0] == expected


def test_preflight_failure_never_invokes_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_safe_identity", lambda *args, **kwargs: False)
    invoked = False
    def runner(*args, **kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError()
    value = subject.collect_podman_unshare_stage_diagnostic(execute=True, runner=runner)
    assert subject.validate_envelope(value)
    assert value["first_failed_stage"] == "preflight" and invoked is False


def test_dispatch_failure_is_invoked_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "_safe_identity", lambda *args, **kwargs: True)
    monkeypatch.setattr(subject, "_podman_user_namespace", lambda: True)
    monkeypatch.setattr(subject.os, "open", lambda *args, **kwargs: 51 if args[0] == subject.SOURCE else 52)
    monkeypatch.setattr(subject.os, "pipe", lambda: (53, 54))
    monkeypatch.setattr(subject.os, "read", lambda *args: b"")
    monkeypatch.setattr(subject.os, "close", lambda fd: None)
    value = subject.collect_podman_unshare_stage_diagnostic(execute=True, runner=lambda *args, **kwargs: types.SimpleNamespace(returncode=125))
    assert subject.validate_envelope(value)
    assert value["status"] == "unsupported" and value["first_failed_stage"] == "dispatch"


def test_mapping_and_fd_close_are_fail_closed() -> None:
    maps = {"/proc/self/uid_map": "0 1000 1\n", "/proc/self/gid_map": "0 1000 1\n"}
    assert subject._podman_user_namespace(lambda path, size: maps[path], lambda: 0)
    maps["/proc/self/uid_map"] = "0 0 4294967295\n"
    assert not subject._podman_user_namespace(lambda path, size: maps[path], lambda: 0)
    error = OSError(); error.errno = errno.EBADF
    assert subject._close_verified(7, lambda fd: None, lambda fd, flag: (_ for _ in ()).throw(error))
    assert not subject._close_verified(7, lambda fd: None, lambda fd, flag: 0)

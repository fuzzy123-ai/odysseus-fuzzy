from __future__ import annotations

import types

import pytest

from ops.homeserver import redacted_predeploy_backup_podman_unshare_capability as subject


def test_default_is_inert_and_redacted() -> None:
    value = subject.collect_podman_unshare_capability()
    assert subject.validate_envelope(value)
    assert value["status"] == "blocked"
    assert value["probe_invoked"] is False
    assert not any(value[key] for key in subject._BOOLS if key.endswith("_visible"))


def test_validator_rejects_shape_and_digest_tampering() -> None:
    value = subject._packet("supported", "none", True, True)
    assert subject.validate_envelope(value)
    value["paths_visible"] = True
    assert not subject.validate_envelope(value)


def test_podman_mapping_accepts_only_one_id_root_mapping() -> None:
    values = {"/proc/self/uid_map": "0 1000 1\n", "/proc/self/gid_map": "0 1000 1\n"}
    assert subject._podman_user_namespace(lambda path, maximum: values[path], lambda: 0)
    values["/proc/self/uid_map"] = "0 0 4294967295\n"
    assert not subject._podman_user_namespace(lambda path, maximum: values[path], lambda: 0)


def test_fixed_system_paths_accept_rootless_overflow_owner_but_reject_unsafe_mode() -> None:
    directory = types.SimpleNamespace(st_mode=0o40755, st_uid=65534)
    executable = types.SimpleNamespace(st_mode=0o100555, st_uid=65534)
    unsafe = types.SimpleNamespace(st_mode=0o40777, st_uid=65534)
    assert subject._safe_identity(subject.SOURCE, True, lambda path: directory)
    assert subject._safe_identity(subject.EXECUTABLE, False, lambda path: executable)
    assert not subject._safe_identity(subject.SOURCE, True, lambda path: unsafe)


def test_private_mount_parser_rejects_shared_root() -> None:
    private = "21 20 0:1 / / rw,relatime - tmpfs tmpfs rw\n"
    shared = "21 20 0:1 / / rw,relatime shared:7 - tmpfs tmpfs rw\n"
    assert subject._root_private(private)
    assert not subject._root_private(shared)


def test_fd_close_requires_ebadf_proof() -> None:
    closed: list[int] = []
    def close(fd: int) -> None: closed.append(fd)
    def getfd(fd: int, flag: int) -> int:
        raise OSError(9, "")
    assert subject._close_verified(77, close, getfd)
    assert closed == [77]
    assert not subject._close_verified(78, close, lambda fd, flag: 0)


def test_preflight_failure_does_not_invoke_runner(monkeypatch) -> None:
    monkeypatch.setattr(subject, "_safe_identity", lambda *args, **kwargs: False)
    called = False
    def runner(*args, **kwargs):
        nonlocal called
        called = True
        return types.SimpleNamespace(returncode=0)
    value = subject.collect_podman_unshare_capability(execute=True, runner=runner)
    assert subject.validate_envelope(value)
    assert value["status"] == "blocked" and not called


def test_runner_failure_is_terminal_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(subject, "_safe_identity", lambda *args, **kwargs: True)
    monkeypatch.setattr(subject, "_podman_user_namespace", lambda: True)
    monkeypatch.setattr(subject.os, "open", lambda *args: 81 if args[0] == subject.SOURCE else 82)
    monkeypatch.setattr(subject.os, "close", lambda fd: None)
    value = subject.collect_podman_unshare_capability(execute=True, runner=lambda *args, **kwargs: types.SimpleNamespace(returncode=125))
    assert subject.validate_envelope(value)
    assert value["status"] == "unsupported" and value["retry_permitted"] is False


def _probe(*, mismatch: bool = False, readonly: bool = True, close: bool = True, private: bool = True):
    calls: list[tuple] = []; fd_flags = {22: 0}
    def mount(*args): calls.append(args)
    def unshare(flag): calls.append(("unshare", flag)); return 0
    def reader(path, maximum): return "21 20 0:1 / / rw,relatime - tmpfs tmpfs rw\n" if private else "21 20 0:1 / / rw,relatime shared:7 - tmpfs tmpfs rw\n"
    def stat_fd(fd): return types.SimpleNamespace(st_dev=1, st_ino=2)
    def stat_path(path): return types.SimpleNamespace(st_dev=1, st_ino=3 if mismatch else 2)
    def getfd(fd, command): return fd_flags[fd]
    def setfd(fd, command, flags): fd_flags[fd] = flags
    result = subject._perform_probe(11, 22, unshare=unshare, mount_call=mount, reader=reader, mkdir=lambda path, mode: None,
                                    fstat_call=stat_fd, stat_call=stat_path,
                                    statvfs_call=lambda path: types.SimpleNamespace(f_flag=getattr(subject.os, "ST_RDONLY", 1) if readonly else 0),
                                    close_verified=lambda fd: close, getfd=getfd, setfd=setfd, cloexec=1, getfd_flag=1)
    return result, calls, fd_flags


def test_mount_fd_probe_proves_full_positive_chain() -> None:
    result, calls, flags = _probe()
    assert result and flags[22] & 1
    assert calls[0] == ("unshare", 0x00020000)
    assert calls[1][3] == 0x44000  # private propagation transition
    assert calls[2][:4] == ("tmpfs", "/tmp", "tmpfs", 6)
    assert calls[3][0] == "/proc/self/fd/11" and calls[4][3] == 0x1021


@pytest.mark.parametrize("kwargs", [
    {"mismatch": True}, {"readonly": False}, {"close": False}, {"private": False},
])
def test_mount_fd_probe_rejects_adversarial_invariants(kwargs) -> None:
    result, _, _ = _probe(**kwargs)
    assert not result

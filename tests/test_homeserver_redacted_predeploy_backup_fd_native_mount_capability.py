from __future__ import annotations

import errno
import json
import os
from types import SimpleNamespace

import pytest

from ops.homeserver import redacted_predeploy_backup_fd_native_mount_capability as subject


def _mountinfo_private(_: str, __: int) -> str:
    return "36 25 0:32 / / rw - tmpfs tmpfs rw\n"


def test_default_is_inert_and_redacted() -> None:
    value = subject.collect_fd_native_mount_capability()
    assert subject.validate_envelope(value)
    assert value["status"] == "blocked"
    assert not any(value[key] for key in subject.VISIBILITY_KEYS)


def test_validator_rejects_cross_product_and_visibility() -> None:
    value = subject._packet("unsupported", "source_open_tree", True)
    assert subject.validate_envelope(value)
    value["repository_open_tree"] = True
    value["evidence_sha256"] = subject._digest(value)
    assert not subject.validate_envelope(value)
    value = subject._packet("supported", "none", True)
    value["path_visible"] = True
    value["evidence_sha256"] = subject._digest(value)
    assert not subject.validate_envelope(value)


def test_chain_uses_fd_native_flags_and_closes_every_binding_fd() -> None:
    opened: list[tuple[int, str, int]] = []; moved: list[tuple[int, str, int, str, int]] = []; mounts = []
    closed: list[int] = []; sealed: list[int] = []
    infos = {10: SimpleNamespace(st_dev=1, st_ino=2), 11: SimpleNamespace(st_dev=3, st_ino=4)}
    def open_tree(fd: int, empty: str, flags: int) -> int:
        opened.append((fd, empty, flags)); return 20 + len(opened)
    def move_mount(*args): moved.append(args)
    def mount(*args): mounts.append(args)
    def stat(path: str):
        return infos[10] if path == subject.SOURCE_TARGET else infos[11]
    def statvfs(path: str): return SimpleNamespace(f_flag=1 if path != subject.REPOSITORY_TARGET else 0)
    fd_flags = {"value": 0}
    stage = subject._perform_chain(10, 11, 12, unshare=lambda flag: 0, mount_call=mount, reader=_mountinfo_private,
        mkdir=lambda *_: None, create_file=lambda *_: None, open_tree=open_tree, move_mount=move_mount, fstat_call=lambda fd: infos[fd], stat_call=stat,
        statvfs_call=statvfs, memfd_create=lambda *_: 30, truncate=lambda *_: None, seal_memfd=lambda fd: sealed.append(fd),
        close_verified=lambda fd: closed.append(fd) is None, getfd=lambda *_: fd_flags["value"], setfd=lambda _, __, value: fd_flags.update(value=value),
        cloexec=1, getfd_flag=1)
    assert stage == "none"
    assert all(entry[1] == "" and entry[2] == subject.AT_EMPTY_PATH | subject.OPEN_TREE_CLONE | subject.OPEN_TREE_RECURSIVE | subject.OPEN_TREE_CLOEXEC for entry in opened)
    assert [(entry[0], entry[2], entry[4]) for entry in moved] == [(21, subject.AT_FDCWD, subject.MOVE_MOUNT_F_EMPTY_PATH), (22, subject.AT_FDCWD, subject.MOVE_MOUNT_F_EMPTY_PATH)]
    assert closed == [10, 11, 21, 22, 30]
    assert sealed == [30]
    assert any(call[0] == "/proc/self/fd/30" and call[1] == subject.MEMFD_TARGET for call in mounts)


def test_each_fd_native_stage_fails_closed() -> None:
    base = dict(unshare=lambda _: 0, mount_call=lambda *_: None, reader=_mountinfo_private, mkdir=lambda *_: None, create_file=lambda *_: None,
        open_tree=lambda *_: 21, move_mount=lambda *_: None, fstat_call=lambda fd: SimpleNamespace(st_dev=fd, st_ino=fd),
        stat_call=lambda path: SimpleNamespace(st_dev=10 if path == subject.SOURCE_TARGET else 11, st_ino=10 if path == subject.SOURCE_TARGET else 11),
        statvfs_call=lambda path: SimpleNamespace(f_flag=1 if path != subject.REPOSITORY_TARGET else 0), memfd_create=lambda *_: 30,
        truncate=lambda *_: None, seal_memfd=lambda *_: None, close_verified=lambda _: True, getfd=lambda *_: 1, setfd=lambda *_: None, cloexec=1, getfd_flag=1)
    assert subject._perform_chain(10, 11, 12, **{**base, "open_tree": lambda *_: (_ for _ in ()).throw(OSError())}) == "source_open_tree"
    assert subject._perform_chain(10, 11, 12, **{**base, "move_mount": lambda *_: (_ for _ in ()).throw(OSError())}) == "source_move_mount_identity"
    assert subject._perform_chain(10, 11, 12, **{**base, "close_verified": lambda _: False}) == "binding_fds_ebadf"
    assert subject._perform_chain(10, 11, 12, **{**base, "seal_memfd": lambda _: (_ for _ in ()).throw(OSError())}) == "memfd_bind_read_only"
    assert subject._perform_chain(10, 11, 12, **{**base, "getfd": lambda *_: 0}) == "executable_cloexec"


def test_close_verified_requires_ebadf() -> None:
    assert subject._close_verified(9, closer=lambda _: None, getfd=lambda *_: (_ for _ in ()).throw(OSError(errno.EBADF, "x")))
    assert not subject._close_verified(9, closer=lambda _: None, getfd=lambda *_: 1)


def test_source_never_uses_repository_content_api() -> None:
    source = open(subject.__file__, encoding="utf-8").read()
    assert "os.listdir(REPOSITORY" not in source
    assert "os.read(repository_fd" not in source


def _chain(**changed):
    """A successful injected chain, with one bounded point changed per test."""
    fd_flags = {"value": 0}; tree = iter((21, 22)); stats = {10: SimpleNamespace(st_dev=10, st_ino=10), 11: SimpleNamespace(st_dev=11, st_ino=11)}
    values = dict(unshare=lambda _: 0, mount_call=lambda *_: None, reader=_mountinfo_private, mkdir=lambda *_: None,
        create_file=lambda *_: None, open_tree=lambda *_: next(tree), move_mount=lambda *_: None,
        fstat_call=lambda fd: stats[fd], stat_call=lambda path: stats[10] if path == subject.SOURCE_TARGET else stats[11],
        statvfs_call=lambda path: SimpleNamespace(f_flag=1 if path != subject.REPOSITORY_TARGET else 0),
        memfd_create=lambda *_: 30, truncate=lambda *_: None, seal_memfd=lambda *_: None, close_verified=lambda _: True,
        getfd=lambda *_: fd_flags["value"], setfd=lambda _, __, value: fd_flags.update(value=value), cloexec=1, getfd_flag=1)
    values.update(changed)
    return subject._perform_chain(10, 11, 12, **values)


@pytest.mark.parametrize(("change", "stage"), [
    ({"unshare": lambda _: -1}, "mount_namespace"),
    ({"reader": lambda *_: "bad"}, "private_propagation"),
    ({"mkdir": lambda *_: (_ for _ in ()).throw(OSError())}, "tmpfs"),
    ({"open_tree": lambda *_: (_ for _ in ()).throw(OSError())}, "source_open_tree"),
    ({"move_mount": lambda *_: (_ for _ in ()).throw(OSError())}, "source_move_mount_identity"),
    ({"stat_call": lambda _: SimpleNamespace(st_dev=0, st_ino=0)}, "source_move_mount_identity"),
    ({"statvfs_call": lambda _: SimpleNamespace(f_flag=0)}, "source_read_only"),
    ({"memfd_create": lambda *_: (_ for _ in ()).throw(OSError())}, "memfd_bind_read_only"),
    ({"truncate": lambda *_: (_ for _ in ()).throw(OSError())}, "memfd_bind_read_only"),
    ({"seal_memfd": lambda *_: (_ for _ in ()).throw(OSError())}, "memfd_bind_read_only"),
    ({"close_verified": lambda _: False}, "binding_fds_ebadf"),
    ({"getfd": lambda *_: 0}, "executable_cloexec"),
])
def test_all_individual_boundary_failures_are_fixed(change, stage) -> None:
    # The second open_tree requires a sequence: a single constant fd is a
    # deliberately malformed mount-FD identity and must still fail closed.
    assert _chain(**change) == stage


def test_repository_specific_boundaries_and_exact_syscall_arguments() -> None:
    calls = []; move = []; flags = []; tree = iter((21, 22)); fd_flags = {"value": 0}
    def open_tree(fd, empty, value): calls.append((fd, empty, value)); return next(tree)
    def move_mount(*args): move.append(args)
    def memfd(name, value): flags.append((name, value)); return 30
    stage = subject._perform_chain(10, 11, 12, unshare=lambda _: 0, mount_call=lambda *_: None, reader=_mountinfo_private,
        mkdir=lambda *_: None, create_file=lambda *_: None, open_tree=open_tree, move_mount=move_mount,
        fstat_call=lambda fd: SimpleNamespace(st_dev=fd, st_ino=fd), stat_call=lambda path: SimpleNamespace(st_dev=10 if path == subject.SOURCE_TARGET else 11, st_ino=10 if path == subject.SOURCE_TARGET else 11),
        statvfs_call=lambda path: SimpleNamespace(f_flag=1 if path != subject.REPOSITORY_TARGET else 0), memfd_create=memfd,
        truncate=lambda fd, size: flags.append((fd, size)), seal_memfd=lambda fd: flags.append(("seal", fd)), close_verified=lambda _: True,
        getfd=lambda *_: fd_flags["value"], setfd=lambda _, __, value: fd_flags.update(value=value), cloexec=1, getfd_flag=1)
    assert stage == "none"
    native = subject.AT_EMPTY_PATH | subject.OPEN_TREE_CLONE | subject.OPEN_TREE_RECURSIVE | subject.OPEN_TREE_CLOEXEC
    assert calls == [(10, "", native), (11, "", native)]
    assert move == [(21, "", subject.AT_FDCWD, subject.SOURCE_TARGET, subject.MOVE_MOUNT_F_EMPTY_PATH), (22, "", subject.AT_FDCWD, subject.REPOSITORY_TARGET, subject.MOVE_MOUNT_F_EMPTY_PATH)]
    assert flags[0] == ("odysseus-nonsecret", getattr(os, "MFD_CLOEXEC", 1) | getattr(os, "MFD_ALLOW_SEALING", 2))
    assert (30, 1) in flags and ("seal", 30) in flags
    assert subject._linux_syscalls() is None or len(subject._linux_syscalls()) == 2


def test_repository_specific_failed_stages() -> None:
    calls_open = {"n": 0}
    def second_open_fails(*_):
        calls_open["n"] += 1
        if calls_open["n"] == 2: raise OSError()
        return 21
    assert _chain(open_tree=second_open_fails) == "repository_open_tree"
    calls = {"n": 0}
    def move(*_):
        calls["n"] += 1
        if calls["n"] == 2: raise OSError()
    assert _chain(move_mount=move) == "repository_move_mount_identity"
    assert _chain(stat_call=lambda path: SimpleNamespace(st_dev=10 if path == subject.SOURCE_TARGET else 0, st_ino=10 if path == subject.SOURCE_TARGET else 0)) == "repository_move_mount_identity"
    assert _chain(statvfs_call=lambda _: SimpleNamespace(f_flag=1)) == "repository_remains_read_write"


def test_collect_preflight_never_invokes_runner_and_validator_is_complete() -> None:
    assert subject.collect_fd_native_mount_capability(execute=True, runner=lambda *_: (_ for _ in ()).throw(AssertionError()), lstat=lambda _: (_ for _ in ()).throw(OSError()))["first_failed_stage"] == "preflight"
    valid = subject._packet("supported", "none", True)
    for key in ("status", "error_code", "probe_invoked", *subject.STAGES, *subject.VISIBILITY_KEYS):
        changed = dict(valid)
        changed[key] = False if changed[key] is True else (True if isinstance(changed[key], bool) else "bad")
        changed["evidence_sha256"] = subject._digest(changed)
        assert not subject.validate_envelope(changed), key
    bad_digest = dict(valid); bad_digest["evidence_sha256"] = "0" * 64
    assert not subject.validate_envelope(bad_digest)


def test_collect_nonzero_timeout_and_stage_pipe_propagate(monkeypatch) -> None:
    monkeypatch.setattr(subject, "_safe_identity", lambda *_: True); monkeypatch.setattr(subject, "_podman_user_namespace", lambda: True)
    monkeypatch.setattr(subject.os, "open", lambda *_: 10); monkeypatch.setattr(subject.os, "pipe", lambda: (20, 21)); monkeypatch.setattr(subject.os, "close", lambda *_: None)
    monkeypatch.setattr(subject, "_read_stage", lambda _: "repository_open_tree")
    value = subject.collect_fd_native_mount_capability(execute=True, runner=lambda *a, **k: SimpleNamespace(returncode=125))
    # The outer collector never exposes the pipe bytes; a fixed stage token is
    # accepted only through its parser, while any local setup ambiguity maps to
    # the fixed dispatch stage.
    assert value["status"] == "unsupported" and value["first_failed_stage"] in {"repository_open_tree", "dispatch"}
    monkeypatch.setattr(subject, "_read_stage", lambda _: None)
    timeout = subject.collect_fd_native_mount_capability(execute=True, runner=lambda *a, **k: (_ for _ in ()).throw(__import__("subprocess").TimeoutExpired("x", 1)))
    assert timeout["first_failed_stage"] == "dispatch"


def test_stage_pipe_accepts_only_one_fixed_token(monkeypatch) -> None:
    chunks = iter((b"repository_open_tree\n", b""))
    monkeypatch.setattr(subject.os, "read", lambda *_: next(chunks))
    assert subject._read_stage(9) == "repository_open_tree"
    chunks = iter((b"not-a-stage\n", b""))
    assert subject._read_stage(9) is None

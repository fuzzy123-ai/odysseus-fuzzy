#!/usr/bin/env python3
"""Inert, redacted proof for the FD-native rootless backup mount chain.

This is deliberately a capability diagnostic, not a backup runner.  It has no
caller supplied paths or command and is only useful when the pinned transport
executes it inside ``podman unshare``.  Every failure is reduced to a fixed
stage token before it can leave the process.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import platform
import stat
import subprocess
from typing import Any, Callable, Mapping

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - import stays inert on Windows
    _fcntl = None

SCHEMA_ID = "odysseus.redacted_predeploy_backup_fd_native_mount_capability.v1"
SOURCE = "/usr/share"
REPOSITORY = "/mnt/backup/restic/homeserver"
EXECUTABLE = "/usr/bin/true"
TMPFS_ROOT = "/tmp/odysseus-fd-native-mount-capability"
SOURCE_TARGET = TMPFS_ROOT + "/source"
REPOSITORY_TARGET = TMPFS_ROOT + "/repository"
MEMFD_TARGET = TMPFS_ROOT + "/sealed-nonsecret"
TIMEOUT_SECONDS = 15
CLONE_NEWNS, MS_RDONLY, MS_PRIVATE, MS_REC, MS_BIND, MS_REMOUNT = 0x00020000, 1, 1 << 18, 16384, 4096, 32
AT_FDCWD, AT_EMPTY_PATH = -100, 0x1000
OPEN_TREE_CLONE, OPEN_TREE_CLOEXEC, OPEN_TREE_RECURSIVE = 1, 0x80000, 0x8000
MOVE_MOUNT_F_EMPTY_PATH = 0x00000004
F_ADD_SEALS, F_SEAL_ALL = 1033, 0x000F
STAGES = ("preflight", "mount_namespace", "private_propagation", "tmpfs", "source_open_tree", "source_move_mount_identity", "source_read_only", "repository_open_tree", "repository_move_mount_identity", "repository_remains_read_write", "memfd_bind_read_only", "binding_fds_ebadf", "executable_cloexec", "dispatch")
VISIBILITY_KEYS = frozenset({"raw_stdout_visible", "raw_stderr_visible", "errno_visible", "exception_text_visible", "path_visible", "environment_visible", "hostname_visible", "file_contents_visible", "repository_entries_visible", "secret_values_visible"})
_KEYS = frozenset({"schema_id", "status", "error_code", "first_failed_stage", "probe_invoked", "retry_permitted", "evidence_sha256", *STAGES, *VISIBILITY_KEYS})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def _packet(status: str, failed: str, invoked: bool) -> dict[str, Any]:
    index = STAGES.index(failed) if failed in STAGES else len(STAGES)
    value: dict[str, Any] = {"schema_id": SCHEMA_ID, "status": status, "error_code": "none" if status == "supported" else ("preflight_failed" if status == "blocked" else "stage_failed"), "first_failed_stage": "none" if status == "supported" else failed, "probe_invoked": invoked, "retry_permitted": False}
    value.update({stage: status == "supported" or (status == "unsupported" and n < index) for n, stage in enumerate(STAGES)})
    value.update({key: False for key in VISIBILITY_KEYS})
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    if type(value) is not dict or set(value) != _KEYS or value.get("schema_id") != SCHEMA_ID or value.get("retry_permitted") is not False:
        return False
    status, failed = value.get("status"), value.get("first_failed_stage")
    if status == "supported":
        accepted = failed == "none" and value.get("error_code") == "none" and value.get("probe_invoked") is True and all(value.get(s) is True for s in STAGES)
    elif status == "blocked":
        accepted = failed == "preflight" and value.get("error_code") in {"invalid_invocation", "preflight_failed"} and value.get("probe_invoked") is False and all(value.get(s) is False for s in STAGES)
    elif status == "unsupported" and failed in STAGES[1:]:
        index = STAGES.index(failed); accepted = value.get("error_code") == "stage_failed" and value.get("probe_invoked") is True and all(value.get(s) is (n < index) for n, s in enumerate(STAGES))
    else:
        accepted = False
    return bool(accepted and all(value.get(k) is False for k in VISIBILITY_KEYS) and isinstance(value.get("evidence_sha256"), str) and value["evidence_sha256"] == _digest(value))


def _safe_identity(path: str, directory: bool, lstat: Callable[[str], Any] = os.lstat) -> bool:
    try:
        mode = lstat(path).st_mode
        return bool((stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)) and not (stat.S_IMODE(mode) & 0o022) and (directory or stat.S_IMODE(mode) & 0o100))
    except Exception:
        return False


def _bounded_text(path: str, maximum: int = 256) -> str:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)); data = bytearray()
    try:
        while len(data) <= maximum:
            chunk = os.read(fd, min(128, maximum + 1 - len(data)))
            if not chunk: break
            data.extend(chunk)
        if not data or len(data) > maximum: raise OSError()
        return data.decode("ascii")
    finally:
        os.close(fd)


def _podman_user_namespace(reader: Callable[[str, int], str] = _bounded_text, euid: Callable[[], int] = getattr(os, "geteuid", lambda: -1)) -> bool:
    try:
        def valid(text: str) -> bool:
            rows = [x.split() for x in text.splitlines() if x.strip()]
            return bool(rows and all(len(x) == 3 and all(y.isdecimal() for y in x) for x in rows) and rows[0][0] == "0" and int(rows[0][2]) > 0 and (rows[0][1], rows[0][2]) != ("0", "4294967295"))
        return bool(euid() == 0 and valid(reader("/proc/self/uid_map", 256)) and valid(reader("/proc/self/gid_map", 256)))
    except Exception:
        return False


def _root_private(raw: str) -> bool:
    try:
        roots = [line for line in raw.splitlines() if len(line.split()) > 6 and line.split()[4] == "/"]
        return bool(len(roots) == 1 and " - " in roots[0] and not any(x.startswith(("shared:", "master:", "propagate_from:")) for x in roots[0].split(" - ", 1)[0].split()[6:]))
    except Exception:
        return False


def _close_verified(fd: int, closer: Callable[[int], None] = os.close, getfd: Callable[[int, int], int] | None = None) -> bool:
    try:
        if getfd is None:
            if _fcntl is None: return False
            getfd = _fcntl.fcntl
        closer(fd)
        try: getfd(fd, _fcntl.F_GETFD if _fcntl else 1)
        except OSError as error: return error.errno == errno.EBADF
        return False
    except Exception:
        return False


def _linux_syscalls() -> tuple[Callable[[int, int, str, int], int], Callable[[int, str, int, str, int], int]] | None:
    """Return fixed ABI wrappers only on explicitly known Linux ABIs."""
    if os.name != "posix" or platform.machine().lower() not in {"x86_64", "amd64", "aarch64"}: return None
    try:
        libc = ctypes.CDLL(None, use_errno=True); syscall = libc.syscall; syscall.restype = ctypes.c_long
        def open_tree(fd: int, empty: str, flags: int) -> int:
            result = syscall(428, ctypes.c_int(fd), ctypes.c_char_p(empty.encode("ascii")), ctypes.c_uint(flags))
            if result < 0: raise OSError()
            return int(result)
        def move_mount(from_fd: int, empty: str, to_fd: int, target: str, flags: int) -> int:
            result = syscall(429, ctypes.c_int(from_fd), ctypes.c_char_p(empty.encode("ascii")), ctypes.c_int(to_fd), ctypes.c_char_p(target.encode("ascii")), ctypes.c_uint(flags))
            if result < 0: raise OSError()
            return int(result)
        return open_tree, move_mount
    except Exception:
        return None


def _perform_chain(source_fd: int, repository_fd: int, executable_fd: int, *, unshare: Callable[[int], int], mount_call: Callable[[str | None, str, str | None, int, str | None], None], reader: Callable[[str, int], str], mkdir: Callable[[str, int], None], create_file: Callable[[str], None], open_tree: Callable[[int, str, int], int], move_mount: Callable[[int, str, int, str, int], int], fstat_call: Callable[[int], Any], stat_call: Callable[[str], Any], statvfs_call: Callable[[str], Any], memfd_create: Callable[[str, int], int], truncate: Callable[[int, int], None], seal_memfd: Callable[[int], None], close_verified: Callable[[int], bool], getfd: Callable[[int, int], int], setfd: Callable[[int, int, int], Any], cloexec: int, getfd_flag: int) -> str:
    """Injectable fixed stage chain; none of its targets are caller controlled."""
    mount_fds: list[int] = []; memfd: int | None = None
    try:
        try:
            if unshare(CLONE_NEWNS) != 0: return "mount_namespace"
        except Exception: return "mount_namespace"
        try:
            mount_call(None, "/", None, MS_REC | MS_PRIVATE, None)
            if not _root_private(reader("/proc/self/mountinfo", 1_048_576)): return "private_propagation"
        except Exception: return "private_propagation"
        try:
            mount_call("tmpfs", "/tmp", "tmpfs", 6, "mode=0755,size=1048576")  # MS_NOSUID | MS_NODEV; deliberately writable.
            mkdir(TMPFS_ROOT, 0o700); mkdir(SOURCE_TARGET, 0o700); mkdir(REPOSITORY_TARGET, 0o700)
            # Fixed empty mount point only; no repository content is read/listed.
            create_file(MEMFD_TARGET)
        except Exception: return "tmpfs"
        try:
            src_tree = open_tree(source_fd, "", AT_EMPTY_PATH | OPEN_TREE_CLONE | OPEN_TREE_RECURSIVE | OPEN_TREE_CLOEXEC); mount_fds.append(src_tree)
        except Exception: return "source_open_tree"
        try:
            move_mount(src_tree, "", AT_FDCWD, SOURCE_TARGET, MOVE_MOUNT_F_EMPTY_PATH)
            if (fstat_call(source_fd).st_dev, fstat_call(source_fd).st_ino) != (stat_call(SOURCE_TARGET).st_dev, stat_call(SOURCE_TARGET).st_ino): return "source_move_mount_identity"
        except Exception: return "source_move_mount_identity"
        try:
            mount_call(None, SOURCE_TARGET, None, MS_BIND | MS_REMOUNT | MS_RDONLY, None)
            if not (statvfs_call(SOURCE_TARGET).f_flag & getattr(os, "ST_RDONLY", 1)): return "source_read_only"
        except Exception: return "source_read_only"
        try:
            repo_tree = open_tree(repository_fd, "", AT_EMPTY_PATH | OPEN_TREE_CLONE | OPEN_TREE_RECURSIVE | OPEN_TREE_CLOEXEC); mount_fds.append(repo_tree)
        except Exception: return "repository_open_tree"
        try:
            move_mount(repo_tree, "", AT_FDCWD, REPOSITORY_TARGET, MOVE_MOUNT_F_EMPTY_PATH)
            if (fstat_call(repository_fd).st_dev, fstat_call(repository_fd).st_ino) != (stat_call(REPOSITORY_TARGET).st_dev, stat_call(REPOSITORY_TARGET).st_ino): return "repository_move_mount_identity"
        except Exception: return "repository_move_mount_identity"
        try:
            if statvfs_call(REPOSITORY_TARGET).f_flag & getattr(os, "ST_RDONLY", 1): return "repository_remains_read_write"
        except Exception: return "repository_remains_read_write"
        try:
            memfd = memfd_create("odysseus-nonsecret", getattr(os, "MFD_CLOEXEC", 1) | getattr(os, "MFD_ALLOW_SEALING", 2)); truncate(memfd, 1); seal_memfd(memfd)
            mount_call(f"/proc/self/fd/{memfd}", MEMFD_TARGET, None, MS_BIND, None)
            mount_call(None, MEMFD_TARGET, None, MS_BIND | MS_REMOUNT | MS_RDONLY, None)
            if not (statvfs_call(MEMFD_TARGET).f_flag & getattr(os, "ST_RDONLY", 1)): return "memfd_bind_read_only"
        except Exception: return "memfd_bind_read_only"
        try:
            if not all(close_verified(fd) for fd in (source_fd, repository_fd, *mount_fds, memfd)): return "binding_fds_ebadf"
            mount_fds.clear(); memfd = None
        except Exception: return "binding_fds_ebadf"
        try:
            flags = getfd(executable_fd, getfd_flag); setfd(executable_fd, 2, flags | cloexec)
            if not (getfd(executable_fd, getfd_flag) & cloexec): return "executable_cloexec"
        except Exception: return "executable_cloexec"
        return "none"
    finally:
        # The child is short lived; this covers failure paths without changing the stage result.
        for fd in mount_fds + ([memfd] if isinstance(memfd, int) else []):
            try: os.close(fd)
            except Exception: pass


def _child(source_fd: int, repository_fd: int, executable_fd: int, stage_fd: int) -> Callable[[], None]:
    def setup() -> None:
        stage = "mount_namespace"
        try:
            if _fcntl is None or not hasattr(os, "memfd_create"): raise OSError()
            syscalls = _linux_syscalls()
            if syscalls is None: raise OSError()
            libc = ctypes.CDLL(None, use_errno=True); mount = libc.mount
            mount.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p); mount.restype = ctypes.c_int
            def call(source: str | None, target: str, fstype: str | None, flags: int, data: str | None) -> None:
                if mount(None if source is None else source.encode("ascii"), target.encode("ascii"), None if fstype is None else fstype.encode("ascii"), flags, None if data is None else data.encode("ascii")) != 0: raise OSError()
            def create_file(path: str) -> None:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                os.close(fd)
            def seal_memfd(fd: int) -> None: _fcntl.fcntl(fd, F_ADD_SEALS, F_SEAL_ALL)
            stage = _perform_chain(source_fd, repository_fd, executable_fd, unshare=libc.unshare, mount_call=call, reader=_bounded_text, mkdir=os.mkdir, create_file=create_file, open_tree=syscalls[0], move_mount=syscalls[1], fstat_call=os.fstat, stat_call=os.stat, statvfs_call=os.statvfs, memfd_create=os.memfd_create, truncate=os.ftruncate, seal_memfd=seal_memfd, close_verified=_close_verified, getfd=_fcntl.fcntl, setfd=_fcntl.fcntl, cloexec=_fcntl.FD_CLOEXEC, getfd_flag=_fcntl.F_GETFD)
        except Exception: pass
        if stage != "none":
            try: os.write(stage_fd, stage.encode("ascii") + b"\n")
            except Exception: pass
            os._exit(125)
    return setup


def _read_stage(fd: int) -> str | None:
    try:
        raw = os.read(fd, 64)
        return raw.decode("ascii").strip() if not os.read(fd, 1) and raw.decode("ascii").strip() in STAGES[1:] else None
    except Exception: return None


def collect_fd_native_mount_capability(*, execute: bool = False, runner: Callable[..., Any] = subprocess.run, lstat: Callable[[str], Any] = os.lstat) -> dict[str, Any]:
    if execute is not True:
        value = _packet("blocked", "preflight", False); value["error_code"] = "invalid_invocation"; value["evidence_sha256"] = _digest(value); return value
    if not all((_safe_identity(SOURCE, True, lstat), _safe_identity(REPOSITORY, True, lstat), _safe_identity(EXECUTABLE, False, lstat), _podman_user_namespace())):
        return _packet("blocked", "preflight", False)
    source_fd = repository_fd = executable_fd = read_fd = write_fd = None
    try:
        flags = getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        source_fd, repository_fd = os.open(SOURCE, flags), os.open(REPOSITORY, flags)
        executable_fd = os.open(EXECUTABLE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)); read_fd, write_fd = os.pipe()
        result = runner((f"/proc/self/fd/{executable_fd}",), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS, shell=False, close_fds=True, pass_fds=(source_fd, repository_fd, executable_fd, write_fd), preexec_fn=_child(source_fd, repository_fd, executable_fd, write_fd), env={"PATH": "/usr/bin:/bin"})
        os.close(write_fd); write_fd = None; stage = _read_stage(read_fd)
        return _packet("supported", "none", True) if getattr(result, "returncode", None) == 0 and stage is None else _packet("unsupported", stage or "dispatch", True)
    except subprocess.TimeoutExpired: return _packet("unsupported", "dispatch", True)
    except Exception: return _packet("unsupported", "dispatch", True)
    finally:
        for fd in (source_fd, repository_fd, executable_fd, read_fd, write_fd):
            if isinstance(fd, int):
                try: os.close(fd)
                except Exception: pass


def main() -> int:
    print(json.dumps(collect_fd_native_mount_capability(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)); return 1

if __name__ == "__main__": raise SystemExit(main())

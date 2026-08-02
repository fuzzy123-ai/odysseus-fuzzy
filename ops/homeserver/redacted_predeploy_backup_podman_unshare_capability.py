#!/usr/bin/env python3
"""Inert proof that the descriptor-bound backup setup works under podman unshare.

This is deliberately a capability probe, not a backup implementation.  It is
normally executed only from the immutable-blob transport and never receives a
credential, repository location, environment, or caller supplied path.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import stat
import subprocess
from typing import Any, Callable, Mapping

try:  # The probe itself is Linux-only; import must remain testable on Windows.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - selected only by the Windows test host
    _fcntl = None

SCHEMA_ID = "odysseus.redacted_predeploy_backup_podman_unshare_capability.v1"
SOURCE = "/usr/share"
EXECUTABLE = "/usr/bin/true"
TARGET = "/tmp/odysseus-podman-unshare-capability-source"
TIMEOUT_SECONDS = 15
_BOOLS = frozenset({
    "private_user_namespace", "private_mount_namespace", "private_mount_propagation",
    "descriptor_directory_bound", "source_remounted_read_only", "binding_fds_closed",
    "executable_cloexec", "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible",
    "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible",
})
_KEYS = frozenset({"schema_id", "status", "error_code", "probe_invoked", "retry_permitted", "evidence_sha256", *_BOOLS})

def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()

def _packet(status: str, code: str, invoked: bool, ready: bool) -> dict[str, Any]:
    value: dict[str, Any] = {"schema_id": SCHEMA_ID, "status": status, "error_code": code, "probe_invoked": invoked, "retry_permitted": False}
    for key in _BOOLS:
        value[key] = False if key.endswith("_visible") else ready
    value["evidence_sha256"] = _digest(value)
    return value

def validate_envelope(value: Any) -> bool:
    if type(value) is not dict or set(value) != _KEYS or value.get("schema_id") != SCHEMA_ID:
        return False
    status, code = value.get("status"), value.get("error_code")
    ready = status == "supported"
    permitted = ((status == "supported" and code == "none") or
                 (status == "unsupported" and code in {"capability_unavailable", "timeout", "internal_error"}) or
                 (status == "blocked" and code in {"invalid_invocation", "preflight_failed", "internal_error"}))
    return bool(permitted and value.get("probe_invoked") is (status != "blocked") and value.get("retry_permitted") is False
                and all(value.get(k) is (False if k.endswith("_visible") else ready) for k in _BOOLS)
                and isinstance(value.get("evidence_sha256"), str) and value["evidence_sha256"] == _digest(value))

def _safe_identity(path: str, directory: bool, lstat: Callable[[str], Any] = os.lstat) -> bool:
    try:
        info = lstat(path); mode = info.st_mode
        # In a rootless user namespace host-root system files can deliberately
        # appear as overflow uid.  These are fixed O_NOFOLLOW paths, so type,
        # safe mode and (for true) execute permission are the fail-closed proof.
        return bool((stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)) and not (stat.S_IMODE(mode) & 0o022) and (directory or stat.S_IMODE(mode) & 0o100))
    except Exception:
        return False

def _bounded_text(path: str, maximum: int = 1_048_576) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)); output = bytearray()
    try:
        while len(output) <= maximum:
            block = os.read(descriptor, min(4096, maximum + 1 - len(output)))
            if not block: break
            output.extend(block)
        if not output or len(output) > maximum: raise OSError()
        return output.decode("ascii")
    finally:
        os.close(descriptor)

def _podman_user_namespace(reader: Callable[[str, int], str] = _bounded_text, euid: Callable[[], int] = getattr(os, "geteuid", lambda: -1)) -> bool:
    """Require a non-initial rootless user mapping without emitting host ids."""
    try:
        uid_lines = [line.split() for line in reader("/proc/self/uid_map", 256).splitlines() if line.strip()]
        gid_lines = [line.split() for line in reader("/proc/self/gid_map", 256).splitlines() if line.strip()]
        def rootless(lines: list[list[str]]) -> bool:
            if not lines or any(len(line) != 3 or not all(part.isdecimal() for part in line) for line in lines): return False
            root = lines[0]
            # The initial namespace's full identity mapping is not a Podman proof.
            return root[0] == "0" and int(root[2]) > 0 and (root[1], root[2]) != ("0", "4294967295")
        return bool(euid() == 0 and rootless(uid_lines) and rootless(gid_lines))
    except Exception:
        return False

def _root_private(raw: str) -> bool:
    try:
        candidates = [line for line in raw.splitlines() if len(line.split()) > 5 and line.split()[4] == "/"]
        if len(candidates) != 1 or " - " not in candidates[0]: return False
        optional = candidates[0].split(" - ", 1)[0].split()[6:]
        return not any(item.startswith(("shared:", "master:", "propagate_from:")) for item in optional)
    except Exception:
        return False

def _close_verified(descriptor: int, closer: Callable[[int], None] = os.close, getfd: Callable[[int, int], int] | None = None) -> bool:
    try:
        if getfd is None:
            if _fcntl is None: return False
            getfd = _fcntl.fcntl
        getfd_flag = _fcntl.F_GETFD if _fcntl is not None else 1
        closer(descriptor)
        try:
            getfd(descriptor, getfd_flag)
            return False
        except OSError as exc:
            return exc.errno == errno.EBADF
    except Exception:
        return False

def _perform_probe(
    source_fd: int, executable_fd: int, *, unshare: Callable[[int], int], mount_call: Callable[[str | None, str, str | None, int, str | None], None],
    reader: Callable[[str, int], str], mkdir: Callable[[str, int], None], fstat_call: Callable[[int], Any], stat_call: Callable[[str], Any],
    statvfs_call: Callable[[str], Any], close_verified: Callable[[int], bool], getfd: Callable[[int, int], int],
    setfd: Callable[[int, int, int], Any], cloexec: int, getfd_flag: int,
) -> bool:
    """Purely injectable mount/FD chain used by the child setup and offline tests."""
    try:
        if unshare(0x00020000) != 0: return False  # CLONE_NEWNS only: never a second user namespace.
        mount_call(None, "/", None, 16384 | (1 << 18), None)  # MS_REC|MS_PRIVATE
        if not _root_private(reader("/proc/self/mountinfo", 1_048_576)): return False
        mount_call("tmpfs", "/tmp", "tmpfs", 2 | 4, "mode=0755,size=1048576")
        mkdir(TARGET, 0o700)
        mount_call(f"/proc/self/fd/{source_fd}", TARGET, None, 4096 | 16384, None)  # MS_BIND|MS_REC
        source_info, target_info = fstat_call(source_fd), stat_call(TARGET)
        if (source_info.st_dev, source_info.st_ino) != (target_info.st_dev, target_info.st_ino): return False
        mount_call(None, TARGET, None, 4096 | 32 | 1, None)  # bind remount, MS_RDONLY
        if not (statvfs_call(TARGET).f_flag & getattr(os, "ST_RDONLY", 1)): return False
        if not close_verified(source_fd): return False
        flags = getfd(executable_fd, getfd_flag)
        setfd(executable_fd, 2, flags | cloexec)  # F_SETFD is 2 on Linux.
        return bool(getfd(executable_fd, getfd_flag) & cloexec)
    except Exception:
        return False

def _child(value: tuple[int, int]) -> Callable[[], None]:
    source_fd, executable_fd = value
    def setup() -> None:
        try:
            if _fcntl is None: raise OSError()
            libc = ctypes.CDLL(None, use_errno=True)
            mount = libc.mount; mount.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p); mount.restype = ctypes.c_int
            def call(source: str | None, target: str, fstype: str | None, flags: int, data: str | None = None) -> None:
                if mount(None if source is None else source.encode(), target.encode(), None if fstype is None else fstype.encode(), flags, None if data is None else data.encode()) != 0: raise OSError()
            if not _perform_probe(source_fd, executable_fd, unshare=libc.unshare, mount_call=call, reader=_bounded_text, mkdir=os.mkdir,
                                  fstat_call=os.fstat, stat_call=os.stat, statvfs_call=os.statvfs, close_verified=_close_verified,
                                  getfd=_fcntl.fcntl, setfd=_fcntl.fcntl, cloexec=_fcntl.FD_CLOEXEC, getfd_flag=_fcntl.F_GETFD): raise OSError()
        except Exception:
            os._exit(125)
    return setup

def collect_podman_unshare_capability(*, execute: bool = False, runner: Callable[..., Any] = subprocess.run, lstat: Callable[[str], Any] = os.lstat) -> dict[str, Any]:
    if execute is not True: return _packet("blocked", "invalid_invocation", False, False)
    if not _safe_identity(SOURCE, True, lstat) or not _safe_identity(EXECUTABLE, False, lstat) or not _podman_user_namespace():
        return _packet("blocked", "preflight_failed", False, False)
    source_fd = executable_fd = None
    try:
        source_fd = os.open(SOURCE, getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        executable_fd = os.open(EXECUTABLE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        result = runner((f"/proc/self/fd/{executable_fd}",), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=TIMEOUT_SECONDS, shell=False, close_fds=True, pass_fds=(source_fd, executable_fd), preexec_fn=_child((source_fd, executable_fd)), env={"PATH": "/usr/bin:/bin"})
        return _packet("supported", "none", True, True) if getattr(result, "returncode", None) == 0 else _packet("unsupported", "capability_unavailable", True, False)
    except subprocess.TimeoutExpired:
        return _packet("unsupported", "timeout", True, False)
    except Exception:
        return _packet("unsupported", "capability_unavailable", True, False)
    finally:
        for descriptor in (source_fd, executable_fd):
            if isinstance(descriptor, int):
                try: os.close(descriptor)
                except Exception: pass

def main() -> int:
    print(json.dumps(collect_podman_unshare_capability(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)); return 1

if __name__ == "__main__": raise SystemExit(main())

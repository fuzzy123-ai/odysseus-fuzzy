#!/usr/bin/env python3
"""Redacted, inert stage diagnostic for the fixed Podman-unshare proof.

The diagnostic is deliberately not a backup runner.  When explicitly invoked
from its immutable-blob transport it performs the same mount/descriptor chain
as the published capability proof and returns only a fixed stage name and
booleans.  No caller supplied path, environment, command output, errno, or
exception text can enter the envelope.
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

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows imports remain inert
    _fcntl = None

SCHEMA_ID = "odysseus.redacted_predeploy_backup_podman_unshare_stage_diagnostic.v1"
SOURCE = "/usr/share"
EXECUTABLE = "/usr/bin/true"
TARGET = "/tmp/odysseus-podman-unshare-stage-source"
TIMEOUT_SECONDS = 15
STAGES = (
    "preflight", "mount_namespace", "private_propagation", "tmpfs",
    "descriptor_bind_identity", "read_only_remount", "source_fd_ebadf",
    "executable_cloexec", "dispatch",
)
VISIBILITY_KEYS = frozenset({
    "raw_stdout_visible", "raw_stderr_visible", "errno_visible",
    "exception_text_visible", "path_visible", "environment_visible",
    "hostname_visible", "file_contents_visible", "secret_values_visible",
})
_KEYS = frozenset({"schema_id", "status", "error_code", "first_failed_stage",
                   "probe_invoked", "retry_permitted", "evidence_sha256", *STAGES,
                   *VISIBILITY_KEYS})


def _digest(value: Mapping[str, Any]) -> str:
    safe = {key: item for key, item in value.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(safe, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("ascii")).hexdigest()


def _packet(status: str, failed: str, invoked: bool) -> dict[str, Any]:
    """Create one of the three exact redacted outcome cross-products."""
    index = STAGES.index(failed) if failed in STAGES else len(STAGES)
    value: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "status": status,
        "error_code": "none" if status == "supported" else (
            "preflight_failed" if status == "blocked" else "stage_failed"),
        "first_failed_stage": "none" if status == "supported" else failed,
        "probe_invoked": invoked,
        "retry_permitted": False,
    }
    for position, stage in enumerate(STAGES):
        value[stage] = status == "supported" or (status == "unsupported" and position < index)
    for key in VISIBILITY_KEYS:
        value[key] = False
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    if (type(value) is not dict or set(value) != _KEYS or
            value.get("schema_id") != SCHEMA_ID or value.get("retry_permitted") is not False):
        return False
    status, failed = value.get("status"), value.get("first_failed_stage")
    if status == "supported":
        accepted = (failed == "none" and value.get("error_code") == "none" and
                    value.get("probe_invoked") is True and all(value.get(stage) is True for stage in STAGES))
    elif status == "blocked":
        accepted = (failed == "preflight" and value.get("error_code") in {"invalid_invocation", "preflight_failed"} and
                    value.get("probe_invoked") is False and all(value.get(stage) is False for stage in STAGES))
    elif status == "unsupported" and failed in STAGES[1:]:
        index = STAGES.index(failed)
        accepted = (value.get("error_code") == "stage_failed" and value.get("probe_invoked") is True and
                    all(value.get(stage) is (position < index) for position, stage in enumerate(STAGES)))
    else:
        accepted = False
    return bool(accepted and all(value.get(key) is False for key in VISIBILITY_KEYS) and
                isinstance(value.get("evidence_sha256"), str) and value["evidence_sha256"] == _digest(value))


def _safe_identity(path: str, directory: bool, lstat: Callable[[str], Any] = os.lstat) -> bool:
    try:
        info = lstat(path)
        mode = info.st_mode
        return bool((stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)) and
                    not (stat.S_IMODE(mode) & 0o022) and
                    (directory or stat.S_IMODE(mode) & 0o100))
    except Exception:
        return False


def _bounded_text(path: str, maximum: int = 1_048_576) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    output = bytearray()
    try:
        while len(output) <= maximum:
            block = os.read(descriptor, min(4096, maximum + 1 - len(output)))
            if not block:
                break
            output.extend(block)
        if not output or len(output) > maximum:
            raise OSError()
        return output.decode("ascii")
    finally:
        os.close(descriptor)


def _podman_user_namespace(reader: Callable[[str, int], str] = _bounded_text,
                           euid: Callable[[], int] = getattr(os, "geteuid", lambda: -1)) -> bool:
    try:
        def mapped(raw: str) -> bool:
            rows = [line.split() for line in raw.splitlines() if line.strip()]
            return bool(rows and all(len(row) == 3 and all(piece.isdecimal() for piece in row) for row in rows) and
                        rows[0][0] == "0" and int(rows[0][2]) > 0 and
                        (rows[0][1], rows[0][2]) != ("0", "4294967295"))
        return bool(euid() == 0 and mapped(reader("/proc/self/uid_map", 256)) and
                    mapped(reader("/proc/self/gid_map", 256)))
    except Exception:
        return False


def _root_private(raw: str) -> bool:
    try:
        roots = [line for line in raw.splitlines() if len(line.split()) > 6 and line.split()[4] == "/"]
        if len(roots) != 1 or " - " not in roots[0]:
            return False
        optional = roots[0].split(" - ", 1)[0].split()[6:]
        return not any(item.startswith(("shared:", "master:", "propagate_from:")) for item in optional)
    except Exception:
        return False


def _close_verified(descriptor: int, closer: Callable[[int], None] = os.close,
                    getfd: Callable[[int, int], int] | None = None) -> bool:
    try:
        if getfd is None:
            if _fcntl is None:
                return False
            getfd = _fcntl.fcntl
        getfd_flag = _fcntl.F_GETFD if _fcntl is not None else 1
        closer(descriptor)
        try:
            getfd(descriptor, getfd_flag)
        except OSError as error:
            return error.errno == errno.EBADF
        return False
    except Exception:
        return False


def _perform_chain(source_fd: int, executable_fd: int, *,
                   unshare: Callable[[int], int], mount_call: Callable[[str | None, str, str | None, int, str | None], None],
                   reader: Callable[[str, int], str], mkdir: Callable[[str, int], None],
                   fstat_call: Callable[[int], Any], stat_call: Callable[[str], Any],
                   statvfs_call: Callable[[str], Any], close_verified: Callable[[int], bool],
                   getfd: Callable[[int, int], int], setfd: Callable[[int, int, int], Any],
                   cloexec: int, getfd_flag: int) -> str:
    """Return only the first fixed failed stage; never a raw exception."""
    try:
        if unshare(0x00020000) != 0:  # CLONE_NEWNS only; never a second user ns.
            return "mount_namespace"
    except Exception:
        return "mount_namespace"
    try:
        mount_call(None, "/", None, 0x44000, None)  # MS_REC | MS_PRIVATE
        if not _root_private(reader("/proc/self/mountinfo", 1_048_576)):
            return "private_propagation"
    except Exception:
        return "private_propagation"
    try:
        mount_call("tmpfs", "/tmp", "tmpfs", 6, "mode=0755,size=1048576")
        mkdir(TARGET, 0o700)
    except Exception:
        return "tmpfs"
    try:
        mount_call(f"/proc/self/fd/{source_fd}", TARGET, None, 0x5000, None)  # MS_BIND|MS_REC
        source_info, target_info = fstat_call(source_fd), stat_call(TARGET)
        if (source_info.st_dev, source_info.st_ino) != (target_info.st_dev, target_info.st_ino):
            return "descriptor_bind_identity"
    except Exception:
        return "descriptor_bind_identity"
    try:
        mount_call(None, TARGET, None, 0x1021, None)  # bind remount + MS_RDONLY
        if not (statvfs_call(TARGET).f_flag & getattr(os, "ST_RDONLY", 1)):
            return "read_only_remount"
    except Exception:
        return "read_only_remount"
    if not close_verified(source_fd):
        return "source_fd_ebadf"
    try:
        flags = getfd(executable_fd, getfd_flag)
        setfd(executable_fd, 2, flags | cloexec)  # F_SETFD is fixed at 2 on Linux.
        if not (getfd(executable_fd, getfd_flag) & cloexec):
            return "executable_cloexec"
    except Exception:
        return "executable_cloexec"
    return "none"


def _write_stage(descriptor: int, stage: str) -> None:
    # The pipe carries a fixed short token only; errors deliberately disappear.
    try:
        os.write(descriptor, stage.encode("ascii") + b"\n")
    except Exception:
        pass


def _child(source_fd: int, executable_fd: int, stage_fd: int) -> Callable[[], None]:
    def setup() -> None:
        try:
            if _fcntl is None:
                raise OSError()
            libc = ctypes.CDLL(None, use_errno=True)
            mount = libc.mount
            mount.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p)
            mount.restype = ctypes.c_int
            def call(source: str | None, target: str, fstype: str | None, flags: int, data: str | None) -> None:
                if mount(None if source is None else source.encode("ascii"), target.encode("ascii"),
                         None if fstype is None else fstype.encode("ascii"), flags,
                         None if data is None else data.encode("ascii")) != 0:
                    raise OSError()
            stage = _perform_chain(source_fd, executable_fd, unshare=libc.unshare, mount_call=call,
                                   reader=_bounded_text, mkdir=os.mkdir, fstat_call=os.fstat, stat_call=os.stat,
                                   statvfs_call=os.statvfs, close_verified=_close_verified, getfd=_fcntl.fcntl,
                                   setfd=_fcntl.fcntl, cloexec=_fcntl.FD_CLOEXEC, getfd_flag=_fcntl.F_GETFD)
        except Exception:
            stage = "mount_namespace"
        if stage != "none":
            _write_stage(stage_fd, stage)
            os._exit(125)
    return setup


def _read_stage(descriptor: int) -> str | None:
    try:
        raw = os.read(descriptor, 64)
        if os.read(descriptor, 1):
            return None
        token = raw.decode("ascii").strip()
        return token if token in STAGES[1:] else None
    except Exception:
        return None


def collect_podman_unshare_stage_diagnostic(*, execute: bool = False,
                                            runner: Callable[..., Any] = subprocess.run,
                                            lstat: Callable[[str], Any] = os.lstat) -> dict[str, Any]:
    if execute is not True:
        value = _packet("blocked", "preflight", False)
        value["error_code"] = "invalid_invocation"; value["evidence_sha256"] = _digest(value)
        return value
    if not _safe_identity(SOURCE, True, lstat) or not _safe_identity(EXECUTABLE, False, lstat) or not _podman_user_namespace():
        return _packet("blocked", "preflight", False)
    source_fd = executable_fd = read_fd = write_fd = None
    try:
        source_fd = os.open(SOURCE, getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        executable_fd = os.open(EXECUTABLE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        read_fd, write_fd = os.pipe()
        result = runner((f"/proc/self/fd/{executable_fd}",), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS, shell=False, close_fds=True,
                        pass_fds=(source_fd, executable_fd, write_fd), preexec_fn=_child(source_fd, executable_fd, write_fd),
                        env={"PATH": "/usr/bin:/bin"})
        os.close(write_fd); write_fd = None
        stage = _read_stage(read_fd)
        if getattr(result, "returncode", None) == 0 and stage is None:
            return _packet("supported", "none", True)
        return _packet("unsupported", stage or "dispatch", True)
    except subprocess.TimeoutExpired:
        return _packet("unsupported", "dispatch", True)
    except Exception:
        return _packet("unsupported", "dispatch", True)
    finally:
        for descriptor in (source_fd, executable_fd, read_fd, write_fd):
            if isinstance(descriptor, int):
                try:
                    os.close(descriptor)
                except Exception:
                    pass


def main() -> int:
    print(json.dumps(collect_podman_unshare_stage_diagnostic(), sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

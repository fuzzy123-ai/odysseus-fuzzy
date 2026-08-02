#!/usr/bin/env python3
"""Probe the fixed private-namespace backup prerequisites without host effects."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.redacted_predeploy_backup_capability.v1"
SOURCE = "/usr/share"
EXECUTABLE = "/usr/bin/true"
TARGET = "/tmp/odysseus-capability-source"
TIMEOUT_SECONDS = 15
_HEX = re.compile(r"^[0-9a-f]{64}$")
_ERRORS = frozenset({"none", "invalid_invocation", "preflight_failed", "capability_unavailable", "timeout", "internal_error"})
_BOOL_KEYS = frozenset({
    "private_user_namespace", "private_mount_namespace", "private_mount_propagation",
    "descriptor_directory_bound", "source_remounted_read_only", "binding_fds_closed",
    "executable_cloexec", "raw_stdout_visible", "raw_stderr_visible",
    "exception_text_visible", "environment_visible", "file_contents_visible",
    "paths_visible", "hostnames_visible", "secret_values_visible",
})
_KEYS = frozenset({"schema_id", "status", "error_code", "probe_invoked", "retry_permitted", *_BOOL_KEYS, "evidence_sha256"})


def _digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _packet(status: str, error: str, *, invoked: bool, ready: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_id": SCHEMA_ID, "status": status,
        "error_code": error if error in _ERRORS else "internal_error",
        "probe_invoked": invoked, "retry_permitted": False,
    }
    for key in _BOOL_KEYS:
        payload[key] = ready if not key.endswith("_visible") else False
    payload["evidence_sha256"] = _digest(payload)
    return payload


def validate_envelope(value: Any) -> bool:
    if type(value) is not dict or set(value) != _KEYS or value.get("schema_id") != SCHEMA_ID:
        return False
    status = value.get("status")
    ready = status == "supported"
    return bool(
        status in {"supported", "unsupported", "blocked"}
        and (
            (status == "supported" and value.get("error_code") == "none")
            or (status == "unsupported" and value.get("error_code") in {"capability_unavailable", "timeout", "internal_error"})
            or (status == "blocked" and value.get("error_code") in {"invalid_invocation", "preflight_failed", "internal_error"})
        )
        and value.get("probe_invoked") is (status != "blocked")
        and value.get("retry_permitted") is False
        and all(value.get(key) is (ready if not key.endswith("_visible") else False) for key in _BOOL_KEYS)
        and type(value.get("evidence_sha256")) is str and _HEX.fullmatch(value["evidence_sha256"])
        and value["evidence_sha256"] == _digest(value)
    )


class Identities:
    __slots__ = ("source_fd", "executable_fd")
    def __init__(self, source_fd: int, executable_fd: int) -> None:
        self.source_fd, self.executable_fd = source_fd, executable_fd
    @property
    def pass_fds(self) -> tuple[int, int]:
        return (self.source_fd, self.executable_fd)


def _safe(path: str, *, directory: bool, uid: int, lstat: Callable[[str], Any]) -> bool:
    try:
        info = lstat(path); mode = int(info.st_mode); permissions = stat.S_IMODE(mode)
        return bool(
            (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode))
            and int(info.st_uid) == uid and (permissions & 0o022) == 0
            and (directory or bool(permissions & 0o100))
        )
    except Exception:
        return False


def _open_identities() -> Identities:
    opened: list[int] = []
    try:
        nofollow = getattr(os, "O_NOFOLLOW")
        source = os.open(SOURCE, getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | nofollow); opened.append(source)
        executable = os.open(EXECUTABLE, os.O_RDONLY | nofollow); opened.append(executable)
        source_info, executable_info = os.fstat(source), os.fstat(executable)
        if not (
            stat.S_ISDIR(source_info.st_mode) and source_info.st_uid == 0
            and (stat.S_IMODE(source_info.st_mode) & 0o022) == 0
            and stat.S_ISREG(executable_info.st_mode) and executable_info.st_uid == 0
            and bool(stat.S_IMODE(executable_info.st_mode) & 0o100)
            and (stat.S_IMODE(executable_info.st_mode) & 0o022) == 0
        ):
            raise OSError
        return Identities(source, executable)
    except Exception:
        for descriptor in opened:
            try: os.close(descriptor)
            except Exception: pass
        raise


def _release(value: Identities) -> None:
    for descriptor in value.pass_fds:
        try: os.close(descriptor)
        except Exception: pass


def _write_map(path: str, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
    try:
        encoded = value.encode("ascii")
        if os.write(descriptor, encoded) != len(encoded): raise OSError
    finally:
        os.close(descriptor)


def _enter_user_mount_namespace(
    uid: int, gid: int, *, unshare: Callable[[int], int], writer: Callable[[str, str], None] = _write_map,
    setgid: Callable[[int, int, int], None] | None = None,
    setuid: Callable[[int, int, int], None] | None = None,
) -> bool:
    try:
        if unshare(0x10000000 | 0x00020000) != 0: return False
        writer("/proc/self/setgroups", "deny\n")
        writer("/proc/self/uid_map", f"0 {uid} 1\n")
        writer("/proc/self/gid_map", f"0 {gid} 1\n")
        (getattr(os, "setresgid") if setgid is None else setgid)(0, 0, 0)
        (getattr(os, "setresuid") if setuid is None else setuid)(0, 0, 0)
        return True
    except Exception:
        return False


def _bounded_read(path: str, maximum: int = 1_048_576) -> str:
    descriptor = os.open(path, os.O_RDONLY)
    output = bytearray()
    try:
        while len(output) <= maximum:
            chunk = os.read(descriptor, min(4096, maximum + 1 - len(output)))
            if not chunk: break
            output.extend(chunk)
        if not output or len(output) > maximum: raise OSError
        return output.decode("ascii")
    finally:
        os.close(descriptor)


def _finalize_child_fds(
    value: Identities, *, closer: Callable[[int], None] = os.close,
    getfd: Callable[[int], int] | None = None, setfd: Callable[[int, int], None] | None = None,
    cloexec: int | None = None,
) -> bool:
    try:
        if getfd is None or setfd is None or cloexec is None:
            import fcntl
            selected_getfd = lambda descriptor: fcntl.fcntl(descriptor, fcntl.F_GETFD)
            selected_setfd = lambda descriptor, flags: fcntl.fcntl(descriptor, fcntl.F_SETFD, flags)
            selected_cloexec = fcntl.FD_CLOEXEC
        else:
            selected_getfd, selected_setfd, selected_cloexec = getfd, setfd, cloexec
        closer(value.source_fd)
        try:
            selected_getfd(value.source_fd)
            return False
        except OSError as exc:
            if exc.errno != errno.EBADF: return False
        flags = selected_getfd(value.executable_fd)
        selected_setfd(value.executable_fd, flags | selected_cloexec)
        return bool(selected_getfd(value.executable_fd) & selected_cloexec)
    except Exception:
        return False


def _root_mount_is_private(raw: str) -> bool:
    try:
        candidates = []
        for line in raw.splitlines():
            fields = line.split(" ")
            if len(fields) > 4 and fields[4] == "/": candidates.append(line)
        if len(candidates) != 1 or candidates[0].count(" - ") != 1: return False
        before, after = candidates[0].split(" - ", 1)
        prefix, suffix = before.split(" "), after.split(" ")
        if len(prefix) < 6 or len(suffix) < 3 or prefix[4] != "/": return False
        optional = prefix[6:]
        return not any(item.startswith(("shared:", "master:", "propagate_from:")) for item in optional)
    except Exception:
        return False


def _perform_child_probe(
    value: Identities, *, uid: int, gid: int, unshare: Callable[[int], int],
    map_writer: Callable[[str, str], None], setgid: Callable[[int, int, int], None],
    setuid: Callable[[int, int, int], None], mount_call: Callable[[str | None, str, str | None, int, str | None], None],
    bounded_reader: Callable[[str, int], str], mkdir: Callable[[str, int], None],
    stat_call: Callable[[str], Any], fstat_call: Callable[[int], Any],
    statvfs_call: Callable[[str], Any], finalize: Callable[[Identities], bool],
) -> bool:
    try:
        if not _enter_user_mount_namespace(uid, gid, unshare=unshare, writer=map_writer, setgid=setgid, setuid=setuid): return False
        if bounded_reader("/proc/self/uid_map", 256).split() != ["0", str(uid), "1"]: return False
        if bounded_reader("/proc/self/gid_map", 256).split() != ["0", str(gid), "1"]: return False
        if bounded_reader("/proc/self/setgroups", 32) != "deny\n": return False
        mount_call(None, "/", None, 16384 | (1 << 18), None)
        if not _root_mount_is_private(bounded_reader("/proc/self/mountinfo", 1_048_576)): return False
        mount_call("tmpfs", "/tmp", "tmpfs", 2 | 4, "mode=0755,size=1048576")
        mkdir(TARGET, 0o700)
        mount_call(f"/proc/self/fd/{value.source_fd}", TARGET, None, 4096 | 16384, None)
        target_info, source_info = stat_call(TARGET), fstat_call(value.source_fd)
        if target_info.st_ino != source_info.st_ino or target_info.st_dev != source_info.st_dev: return False
        mount_call(None, TARGET, None, 4096 | 32 | 1, None)
        if not (statvfs_call(TARGET).f_flag & getattr(os, "ST_RDONLY", 1)): return False
        return finalize(value)
    except Exception:
        return False


def _child_setup(value: Identities) -> Callable[[], None]:
    uid = getattr(os, "geteuid", lambda: 0)(); gid = getattr(os, "getegid", lambda: 0)()
    def setup() -> None:
        libc = ctypes.CDLL(None, use_errno=True); mount = libc.mount
        mount.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p)
        mount.restype = ctypes.c_int
        def call(source: str | None, target: str, fs: str | None, flags: int, data: str | None = None) -> None:
            if mount(None if source is None else source.encode(), target.encode(), None if fs is None else fs.encode(), flags,
                     None if data is None else data.encode()) != 0: os._exit(125)
        if not _perform_child_probe(
            value, uid=uid, gid=gid, unshare=libc.unshare, map_writer=_write_map,
            setgid=getattr(os, "setresgid"), setuid=getattr(os, "setresuid"), mount_call=call,
            bounded_reader=_bounded_read, mkdir=os.mkdir, stat_call=os.stat, fstat_call=os.fstat,
            statvfs_call=os.statvfs, finalize=_finalize_child_fds,
        ): os._exit(125)
    return setup


def collect_predeploy_backup_capability(
    *, execute: bool = False, runner: Callable[..., Any] = subprocess.run,
    lstat: Callable[[str], Any] = os.lstat, identity_open: Callable[[], Identities] = _open_identities,
    identity_release: Callable[[Identities], None] = _release,
) -> dict[str, Any]:
    if execute is not True: return _packet("blocked", "invalid_invocation", invoked=False, ready=False)
    if not _safe(SOURCE, directory=True, uid=0, lstat=lstat) or not _safe(EXECUTABLE, directory=False, uid=0, lstat=lstat):
        return _packet("blocked", "preflight_failed", invoked=False, ready=False)
    identities: Identities | None = None
    try:
        identities = identity_open()
        command = (f"/proc/self/fd/{identities.executable_fd}",)
        try:
            result = runner(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=TIMEOUT_SECONDS, env={"PATH": "/usr/bin:/bin"}, shell=False,
                            pass_fds=identities.pass_fds, preexec_fn=_child_setup(identities), close_fds=True)
        except subprocess.TimeoutExpired:
            return _packet("unsupported", "timeout", invoked=True, ready=False)
        except Exception:
            return _packet("unsupported", "capability_unavailable", invoked=True, ready=False)
        return _packet("supported", "none", invoked=True, ready=True) if getattr(result, "returncode", None) == 0 else _packet("unsupported", "capability_unavailable", invoked=True, ready=False)
    except Exception:
        return _packet("blocked", "preflight_failed", invoked=False, ready=False)
    finally:
        if identities is not None: identity_release(identities)


def main() -> int:
    payload = collect_predeploy_backup_capability()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

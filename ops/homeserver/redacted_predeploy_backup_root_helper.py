#!/usr/bin/env python3
"""Root-owned, argv-free executor for one descriptor-bound Restic snapshot.

This module is deliberately self-contained.  The installed copy is owned by
root and is launched by one fixed systemd unit with ``python3 -I``; it never
imports code from the checkout.  The repository copy exists solely so it can
be reviewed, hashed and tested before a separately authorised installation.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import posixpath
import select
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.redacted_predeploy_backup_creation.v1"
ARM_SCHEMA_ID = "odysseus.predeploy_backup_root_helper_arm.v1"
RESTIC_BINARY = "/usr/bin/restic"
SOURCE = "/opt/odysseus"
BACKUP_MOUNT = "/mnt/backup"
REPOSITORY = "/mnt/backup/restic/homeserver"
PASSWORD_FILE = "/home/homebase/.config/odysseus-backup/restic-password"
OWNER = "homebase"
STATE_DIR = "/var/lib/odysseus-predeploy-backup-root-helper"
ARM_NAME = "arm.json"
USED_PREFIX = "used-"
PUBLIC_RECEIPT_PATH = "/run/odysseus-predeploy-backup-root-helper/receipt.json"
PUBLIC_DIAGNOSTIC_PATH = "/run/odysseus-predeploy-backup-root-helper/diagnostic.json"
VIEW_ROOT = "/run/odysseus-predeploy-backup-root-helper/view"
VIEW_CREDENTIAL = VIEW_ROOT + "/credential/restic-password"
VIEW_REPOSITORY = VIEW_ROOT + "/repository"
BACKUP_TIMEOUT_SECONDS = 1800
READBACK_TIMEOUT_SECONDS = 20
TERMINAL_DRAIN_SECONDS = 1
MAX_OUTPUT_BYTES = 65536
MAX_CREDENTIAL_BYTES = 16384
MAX_ARM_BYTES = 1024
MAX_ARM_FUTURE_SECONDS = 600
CLONE_NEWNS = 0x00020000
MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_REMOUNT, MS_BIND, MS_REC, MS_PRIVATE = 32, 4096, 16384, 1 << 18
EXECVEAT_EMPTY_PATH = 0x1000
_O_RDONLY = os.O_RDONLY
_O_WRONLY = os.O_WRONLY
_O_CREAT = os.O_CREAT
_O_EXCL = os.O_EXCL
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0x20000)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0x80000)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0x10000)


def _os_flag(name: str, fixed: int) -> int:
    """Use a fixed safe fallback for controlled non-POSIX test namespaces."""
    value = getattr(os, name, fixed)
    return value if type(value) is int else fixed
_HEX = __import__("re").compile(r"^[0-9a-f]{64}$")
_BLOCKED = frozenset({"not_armed", "arm_invalid", "arm_expired", "arm_replayed", "arm_contended", "identity_unavailable", "preflight_failed"})
_UNKNOWN = frozenset({"backup_timeout", "backup_failed", "readback_timeout", "readback_failed", "readback_invalid", "execution_ambiguous"})
_OK_KEYS = frozenset({"schema_id", "status", "repository_identity", "protected_source_identity", "backup_effect", "action_provenance_ref", "snapshot_id", "source_included", "snapshot_created_after_start", "snapshot_age_seconds", "snapshot_fresh", "concurrent_lock_held", "partial_snapshot_detected", "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "evidence_sha256"})
_BLOCKED_KEYS = frozenset({"schema_id", "status", "error_code", "backup_invoked", "retry_permitted", "evidence_sha256"})
_UNKNOWN_KEYS = frozenset({"schema_id", "status", "error_code", "effect_may_have_occurred", "retry_permitted", "manual_recovery_required", "action_provenance_ref", "evidence_sha256"})
DIAGNOSTIC_SCHEMA_ID = "odysseus.predeploy_backup_root_helper_diagnostic.v1"
_DIAGNOSTIC_KEYS = frozenset({"schema_id", "result_status", "result_error_code", "result_evidence_sha256", "action_provenance_ref", "failed_phase", "restic_exec_state", "exit_class", "exit_code_available", "exit_code", "child_started", "child_reaped", "cleanup_terminal", "arm_consumed", "retry_permitted", "manual_recovery_required", "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "evidence_sha256"})
_PHASES = frozenset({"none", "pre_arm", "post_arm_pre_backup", "backup_start", "backup_wait", "readback_start", "readback_wait", "readback_validate", "unexpected"})
_EXEC_STATES = frozenset({"not_attempted", "preexec_failed", "confirmed_exec", "unknown"})
_EXIT_CLASSES = frozenset({"not_available", "exited_zero", "exited_nonzero", "signaled", "timed_out", "output_limit", "wait_failed", "handshake_ambiguous"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _blocked(code: str) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": "blocked", "error_code": code if code in _BLOCKED else "preflight_failed", "backup_invoked": False, "retry_permitted": False}
    value["evidence_sha256"] = _digest(value)
    return value


def _unknown(code: str, reference: str) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": "unknown", "error_code": code if code in _UNKNOWN else "execution_ambiguous", "effect_may_have_occurred": True, "retry_permitted": False, "manual_recovery_required": True, "action_provenance_ref": reference}
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    try:
        if type(value) is not dict or value.get("schema_id") != SCHEMA_ID or not isinstance(value.get("evidence_sha256"), str) or value["evidence_sha256"] != _digest(value):
            return False
        if value.get("status") == "blocked":
            return set(value) == _BLOCKED_KEYS and value.get("error_code") in _BLOCKED and value.get("backup_invoked") is False and value.get("retry_permitted") is False
        if value.get("status") == "unknown":
            return set(value) == _UNKNOWN_KEYS and value.get("error_code") in _UNKNOWN and value.get("effect_may_have_occurred") is True and value.get("retry_permitted") is False and value.get("manual_recovery_required") is True and isinstance(value.get("action_provenance_ref"), str) and __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", value["action_provenance_ref"])
        if value.get("status") != "ok" or set(value) != _OK_KEYS:
            return False
        return bool(value.get("repository_identity") == "restic_homeserver_backup_v1" and value.get("protected_source_identity") == "odysseus_protected_source_v1" and value.get("backup_effect") == "created" and isinstance(value.get("action_provenance_ref"), str) and __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", value["action_provenance_ref"]) and isinstance(value.get("snapshot_id"), str) and _HEX.fullmatch(value["snapshot_id"]) and type(value.get("snapshot_age_seconds")) is int and 0 <= value["snapshot_age_seconds"] <= BACKUP_TIMEOUT_SECONDS + READBACK_TIMEOUT_SECONDS and all(value.get(k) is False for k in _OK_KEYS if k.endswith("_visible")) and value.get("source_included") is True and value.get("snapshot_created_after_start") is True and value.get("snapshot_fresh") is True and value.get("concurrent_lock_held") is True and value.get("partial_snapshot_detected") is False)
    except Exception:
        return False


def _diagnostic(result: Mapping[str, Any], *, failed_phase: str, outcome: "ChildOutcome | None", arm_consumed: bool) -> dict[str, Any]:
    """Build the separate, non-secret post-attempt diagnostic sidecar."""
    status = result.get("status")
    error_code = result.get("error_code", "none")
    reference = result.get("action_provenance_ref", "none")
    if status not in {"ok", "blocked", "unknown"} or not isinstance(error_code, str) or not isinstance(reference, str):
        raise Failure("preflight_failed")
    child = outcome or ChildOutcome("not_attempted", "not_available", False, 0, False, False, True, b"")
    value = {
        "schema_id": DIAGNOSTIC_SCHEMA_ID,
        "result_status": status,
        "result_error_code": error_code,
        "result_evidence_sha256": result.get("evidence_sha256"),
        "action_provenance_ref": reference,
        "failed_phase": failed_phase,
        "restic_exec_state": child.restic_exec_state,
        "exit_class": child.exit_class,
        "exit_code_available": child.exit_code_available,
        "exit_code": child.exit_code,
        "child_started": child.child_started,
        "child_reaped": child.child_reaped,
        "cleanup_terminal": child.cleanup_terminal,
        "arm_consumed": arm_consumed,
        "retry_permitted": False,
        "manual_recovery_required": status == "unknown",
        "raw_stdout_visible": False,
        "raw_stderr_visible": False,
        "exception_text_visible": False,
        "environment_visible": False,
        "file_contents_visible": False,
        "paths_visible": False,
        "hostnames_visible": False,
        "secret_values_visible": False,
    }
    value["evidence_sha256"] = _digest(value)
    if not validate_diagnostic(value):
        raise Failure("preflight_failed")
    return value


def validate_diagnostic(value: Any) -> bool:
    try:
        if type(value) is not dict or set(value) != _DIAGNOSTIC_KEYS or value.get("schema_id") != DIAGNOSTIC_SCHEMA_ID or type(value.get("evidence_sha256")) is not str or value["evidence_sha256"] != _digest(value): return False
        status, code, reference = value.get("result_status"), value.get("result_error_code"), value.get("action_provenance_ref")
        if status not in {"ok", "blocked", "unknown"} or not isinstance(code, str): return False
        if (status == "ok" and code != "none") or (status == "blocked" and code not in _BLOCKED) or (status == "unknown" and code not in _UNKNOWN): return False
        if (status == "blocked" and reference != "none") or (status != "blocked" and (not isinstance(reference, str) or not __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", reference))): return False
        if not isinstance(value.get("result_evidence_sha256"), str) or not _HEX.fullmatch(value["result_evidence_sha256"]): return False
        if value.get("failed_phase") not in _PHASES or value.get("restic_exec_state") not in _EXEC_STATES or value.get("exit_class") not in _EXIT_CLASSES: return False
        state, exit_class = value["restic_exec_state"], value["exit_class"]
        available, code_value = value.get("exit_code_available"), value.get("exit_code")
        if type(available) is not bool or type(code_value) is not int or not 0 <= code_value <= 255 or (not available and code_value != 0): return False
        if (value["exit_class"] in {"exited_zero", "exited_nonzero"}) != available: return False
        if any(type(value.get(key)) is not bool for key in ("child_started", "child_reaped", "cleanup_terminal", "arm_consumed", "retry_permitted", "manual_recovery_required")): return False
        if value["child_reaped"] and not value["child_started"] or value["cleanup_terminal"] != (not value["child_started"] or value["child_reaped"]) or value["retry_permitted"] is not False or value["manual_recovery_required"] != (status == "unknown"): return False
        if exit_class == "not_available" and (value["child_started"] and state != "preexec_failed"): return False
        if status == "blocked":
            if not (value["failed_phase"] == "pre_arm" and value["restic_exec_state"] == "not_attempted" and value["exit_class"] == "not_available" and not value["child_started"] and not value["child_reaped"] and value["cleanup_terminal"] and not value["arm_consumed"]): return False
        elif status == "ok":
            if not (value["arm_consumed"] and value["failed_phase"] == "none" and value["restic_exec_state"] == "confirmed_exec" and value["exit_class"] == "exited_zero" and code_value == 0 and value["child_started"] and value["child_reaped"] and value["cleanup_terminal"]): return False
        else:
            phase = value["failed_phase"]
            if not value["arm_consumed"] or phase == "none": return False
            if not value["child_started"]:
                if not (state == "not_attempted" and exit_class == "not_available" and not available and code_value == 0 and not value["child_reaped"] and value["cleanup_terminal"] and code == "execution_ambiguous" and phase in {"pre_arm", "post_arm_pre_backup", "backup_start", "readback_start"}): return False
            else:
                if state == "preexec_failed":
                    if not (phase in {"backup_start", "readback_start"} and exit_class == "not_available" and not available and code_value == 0 and value["child_reaped"]): return False
                else:
                    if state == "not_attempted" or phase not in {"backup_wait", "readback_wait", "readback_validate", "unexpected"}: return False
                    if exit_class in {"exited_zero", "exited_nonzero"} and not (state == "confirmed_exec" and value["child_reaped"] and available and (code_value == 0 if exit_class == "exited_zero" else 1 <= code_value <= 255)): return False
                    if exit_class in {"signaled", "handshake_ambiguous"} and not value["child_reaped"]: return False
                    if exit_class not in {"exited_zero", "exited_nonzero"} and (available or code_value != 0): return False
            if code == "backup_timeout" and not (phase == "backup_wait" and exit_class == "timed_out"): return False
            if code == "readback_timeout" and not (phase == "readback_wait" and exit_class == "timed_out"): return False
            if code == "backup_failed" and not ((phase == "backup_start" and state == "preexec_failed") or (phase == "backup_wait" and exit_class != "timed_out" and (state != "confirmed_exec" or exit_class != "exited_zero"))): return False
            if code == "readback_failed" and not ((phase == "readback_start" and state == "preexec_failed") or (phase == "readback_wait" and exit_class != "timed_out" and (state != "confirmed_exec" or exit_class != "exited_zero"))): return False
            if code == "readback_invalid" and not (phase == "readback_validate" and state == "confirmed_exec" and exit_class == "exited_zero" and available and code_value == 0): return False
            if code == "execution_ambiguous" and not ((not value["child_started"] and phase in {"pre_arm", "post_arm_pre_backup", "backup_start", "readback_start"}) or (value["child_started"] and phase in {"backup_wait", "readback_wait", "readback_validate", "unexpected"})): return False
        return all(value.get(key) is False for key in ("raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible"))
    except Exception:
        return False


@dataclass(frozen=True)
class Bound:
    source_parent_fd: int
    source_fd: int
    repository_fd: int
    credential_fd: int
    restic_fd: int
    uid: int
    gid: int
    grant_ref: str


@dataclass(frozen=True)
class ChildOutcome:
    restic_exec_state: str
    exit_class: str
    exit_code_available: bool
    exit_code: int
    child_started: bool
    child_reaped: bool
    cleanup_terminal: bool
    output: bytes


class Failure(Exception):
    def __init__(self, code: str, *, arm_consumed: bool = False, reference: str | None = None) -> None:
        self.code = code
        self.arm_consumed = arm_consumed
        self.reference = reference


def _safe_dir(info: Any, uid: int | None = None, mode: int | None = None) -> bool:
    try:
        return stat.S_ISDIR(info.st_mode) and (uid is None or info.st_uid == uid) and (mode is None or stat.S_IMODE(info.st_mode) == mode) and info.st_nlink >= 1
    except Exception: return False


def _safe_file(info: Any, uid: int, mode: int, maximum: int | None = None) -> bool:
    try:
        return stat.S_ISREG(info.st_mode) and info.st_uid == uid and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1 and (maximum is None or 0 < info.st_size <= maximum)
    except Exception: return False


def _safe_root_parent(info: Any) -> bool:
    try:
        mode = stat.S_IMODE(info.st_mode)
        return _safe_dir(info, 0) and info.st_gid == 0 and mode & 0o500 == 0o500 and mode & 0o022 == 0
    except Exception:
        return False


def _safe_bound_root(info: Any, uid: int, gid: int, *, writable: bool = False) -> bool:
    try:
        mode = stat.S_IMODE(info.st_mode)
        return bool(
            _safe_dir(info, uid)
            and info.st_gid == gid
            and mode & (0o700 if writable else 0o500) == (0o700 if writable else 0o500)
            and mode & 0o022 == 0
        )
    except Exception:
        return False


def _open_components(path: str) -> int:
    if not path.startswith("/") or "//" in path or "/../" in path or "/./" in path: raise Failure("identity_unavailable")
    flags = getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    current = os.open("/", flags)
    try:
        for part in path.split("/")[1:]:
            if not part or part in {".", ".."}: raise OSError()
            following = os.open(part, flags, dir_fd=current)
            os.close(current); current = following
        return current
    except Exception:
        try: os.close(current)
        except Exception: pass
        raise Failure("identity_unavailable") from None


def _read_exact(fd: int, size: int) -> bytearray:
    if not isinstance(size, int) or size <= 0 or size > MAX_CREDENTIAL_BYTES: raise Failure("identity_unavailable")
    chunks = bytearray(size); view = memoryview(chunks); offset = 0
    while offset < len(view):
        count = os.readv(fd, [view[offset:]])
        if not isinstance(count, int) or count <= 0: raise Failure("identity_unavailable")
        offset += count
    probe = bytearray(1)
    if os.readv(fd, [memoryview(probe)]):
        probe[:] = b"\x00"; chunks[:] = b"\x00" * len(chunks); raise Failure("identity_unavailable")
    probe[:] = b"\x00"
    return chunks


def _seal_credential(credential: bytearray) -> int:
    fd: int | None = None
    try:
        import fcntl
        fd = os.memfd_create("odysseus-root-helper-credential", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.fchmod(fd, 0o400)
        written = 0
        while written < len(credential):
            count = os.write(fd, memoryview(credential)[written:])
            if not isinstance(count, int) or count <= 0: raise OSError()
            written += count
        os.lseek(fd, 0, os.SEEK_SET)
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
        if fcntl.fcntl(fd, fcntl.F_GET_SEALS) != seals: raise OSError()
        return fd
    except Exception:
        try:
            if isinstance(fd, int): os.close(fd)
        except Exception: pass
        raise Failure("identity_unavailable") from None
    finally:
        # This is the only mutable copy of the credential in this process.
        # Do not merely rebind its name: overwrite the actual buffer on every
        # success and failure path before it can be released.
        credential[:] = b"\x00" * len(credential)


def _close_owned_descriptor(descriptor: int | None, *, closer: Callable[[int], Any] = os.close) -> None:
    if isinstance(descriptor, int): closer(descriptor)
    return None


def _bind_identities() -> Bound:
    source_parent = source = repository = credential_source = restic = credential_fd = mount = config = raw = None
    success = False
    try:
        import pwd
        owner = pwd.getpwnam(OWNER); uid, gid = int(owner.pw_uid), int(owner.pw_gid)
        source_parent = _open_components(os.path.dirname(SOURCE))
        source = os.open(os.path.basename(SOURCE), getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=source_parent)
        mount = _open_components(BACKUP_MOUNT)
        _prove_fixed_mount(mount)
        repository = os.open("restic", getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=mount)
        next_repository = os.open("homeserver", getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=repository); os.close(repository); repository = next_repository
        config = _open_components(os.path.dirname(PASSWORD_FILE))
        credential_source = os.open(os.path.basename(PASSWORD_FILE), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=config); config = _close_owned_descriptor(config)
        restic = os.open(RESTIC_BINARY, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        if not (_safe_root_parent(os.fstat(source_parent)) and _safe_bound_root(os.fstat(source), uid, gid) and _safe_bound_root(os.fstat(repository), uid, gid, writable=True) and _safe_file(os.fstat(credential_source), uid, 0o600, MAX_CREDENTIAL_BYTES)):
            raise Failure("identity_unavailable")
        rinfo = os.fstat(restic)
        if not (stat.S_ISREG(rinfo.st_mode) and rinfo.st_uid == 0 and stat.S_IMODE(rinfo.st_mode) & 0o100 and not stat.S_IMODE(rinfo.st_mode) & 0o022): raise Failure("identity_unavailable")
        before = os.fstat(credential_source); raw = _read_exact(credential_source, before.st_size); after = os.fstat(credential_source)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns): raise Failure("identity_unavailable")
        credential_fd = _seal_credential(raw)
        os.close(credential_source); credential_source = None
        result = Bound(source_parent, source, repository, credential_fd, restic, uid, gid, "")
        success = True
        return result
    except Failure: raise
    except Exception: raise Failure("identity_unavailable") from None
    finally:
        if isinstance(raw, bytearray):
            raw[:] = b"\x00" * len(raw)
        if not success:
            for fd in (source_parent, source, repository, credential_fd, restic):
                if isinstance(fd, int):
                    try: os.close(fd)
                    except Exception: pass
        for fd in (credential_source,):
            if isinstance(fd, int):
                try: os.close(fd)
                except Exception: pass
        # `mount` has no further role after repository was opened.
        for fd in (config, mount):
            if isinstance(fd, int):
                try: os.close(fd)
                except Exception: pass


def _read_proc_bounded(path: str, maximum: int) -> bytes:
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        value = bytearray()
        while len(value) <= maximum:
            chunk = os.read(fd, min(4096, maximum + 1 - len(value)))
            if not chunk: break
            value.extend(chunk)
        if not value or len(value) > maximum: raise OSError()
        return bytes(value)
    except Exception: raise Failure("identity_unavailable") from None
    finally:
        if isinstance(fd, int):
            try: os.close(fd)
            except Exception: pass


def _prove_fixed_mount(mount_fd: int) -> None:
    """Prove the retained mount descriptor is the one fixed backup mount."""
    try:
        fdinfo = _read_proc_bounded(f"/proc/self/fdinfo/{mount_fd}", 16384).decode("ascii")
        mountinfo = _read_proc_bounded("/proc/self/mountinfo", 1048576).decode("ascii")
        ids = [line.split("\t", 1)[1] for line in fdinfo.splitlines() if line.startswith("mnt_id:\t")]
        if len(ids) != 1 or not ids[0].isdigit(): raise ValueError
        rows = [line.split(" ") for line in mountinfo.splitlines() if line.split(" ")[0] == ids[0]]
        # A descriptor can be valid yet name a different mount namespace row.
        # Require exactly the fixed, non-root mountpoint and its own mount root.
        if len(rows) != 1 or len(rows[0]) < 10 or rows[0][3] != "/" or rows[0][4] != BACKUP_MOUNT or rows[0][4] == "/" or "-" not in rows[0]: raise ValueError
    except Exception: raise Failure("identity_unavailable") from None


def _execveat_syscall() -> tuple[Callable[..., int], int] | None:
    if os.name != "posix" or __import__("platform").machine().lower() not in {"x86_64", "amd64"}: return None
    libc = ctypes.CDLL(None, use_errno=True)
    return libc.syscall, 322  # execveat on x86_64


def _open_reusable_view_anchor(*, opener: Callable[..., int] = os.open, statter: Callable[[int], Any] = os.fstat, closer: Callable[[int], Any] = os.close) -> None:
    """Validate the root-owned nofollow anchor before mounting below it."""
    descriptor = None
    try:
        descriptor = opener(VIEW_ROOT, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        if not _safe_dir(statter(descriptor), 0, 0o700): raise Failure("preflight_failed")
    except Failure:
        raise
    except Exception:
        raise Failure("preflight_failed") from None
    finally:
        if isinstance(descriptor, int): closer(descriptor)


def _same_identity(descriptor_info: Any, path_info: Any) -> bool:
    try:
        return (descriptor_info.st_dev, descriptor_info.st_ino) == (path_info.st_dev, path_info.st_ino)
    except Exception:
        return False


def _reject_nested_mounts(*roots: str, reader: Callable[[str, int], bytes] = _read_proc_bounded) -> None:
    try:
        mountinfo = reader("/proc/self/mountinfo", 1048576).decode("ascii")
        if not mountinfo or any(not root.startswith("/") or root.endswith("/") for root in roots): raise ValueError
        for line in mountinfo.splitlines():
            fields = line.split(" ")
            if len(fields) < 10 or "-" not in fields or not fields[4].startswith("/"): raise ValueError
            mountpoint = fields[4]
            if any(mountpoint.startswith(root + "/") for root in roots): raise ValueError
    except Exception:
        raise Failure("preflight_failed") from None


def _bind_private_views(
    bound: Bound,
    invoke_mount: Callable[[bytes | None, str, bytes | None, int, bytes | None], None],
    *,
    statter: Callable[[str], Any] = os.stat,
    fstatter: Callable[[int], Any] = os.fstat,
    statvfs: Callable[[str], Any] | None = None,
    reject_nested: Callable[..., None] = _reject_nested_mounts,
) -> None:
    filesystem_stat = getattr(os, "statvfs", None) if statvfs is None else statvfs
    if filesystem_stat is None: raise Failure("preflight_failed")
    parent_info = fstatter(bound.source_parent_fd)
    source_info = fstatter(bound.source_fd)
    repository_info = fstatter(bound.repository_fd)
    parent_path_info = statter(os.path.dirname(SOURCE))
    source_path_info = statter(SOURCE)
    repository_path_info = statter(REPOSITORY)
    if not (
        _safe_root_parent(parent_info)
        and _safe_root_parent(parent_path_info)
        and _safe_bound_root(source_info, bound.uid, bound.gid)
        and _safe_bound_root(source_path_info, bound.uid, bound.gid)
        and _safe_bound_root(repository_info, bound.uid, bound.gid, writable=True)
        and _safe_bound_root(repository_path_info, bound.uid, bound.gid, writable=True)
        and _same_identity(parent_info, parent_path_info)
        and _same_identity(source_info, source_path_info)
        and _same_identity(repository_info, repository_path_info)
    ):
        raise Failure("preflight_failed")
    reject_nested(SOURCE, REPOSITORY)

    # Classic non-recursive bind mounts keep the canonical source pathname for
    # snapshot identity while the repository is exposed only through one
    # fixed private tmpfs view.  Post-bind descriptor comparisons reject a
    # pathname swap between descriptor binding and mount(2).
    invoke_mount(SOURCE.encode("ascii"), SOURCE, None, MS_BIND, None)
    if not _same_identity(source_info, statter(SOURCE)): raise Failure("preflight_failed")
    invoke_mount(None, SOURCE, None, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC, None)
    if not (_same_identity(source_info, statter(SOURCE)) and _filesystem_readonly(filesystem_stat(SOURCE))): raise Failure("preflight_failed")

    invoke_mount(REPOSITORY.encode("ascii"), VIEW_REPOSITORY, None, MS_BIND, None)
    repository_view_info = statter(VIEW_REPOSITORY)
    if not (
        _same_identity(repository_info, repository_view_info)
        and _safe_bound_root(repository_view_info, bound.uid, bound.gid, writable=True)
        and not _filesystem_readonly(filesystem_stat(VIEW_REPOSITORY))
    ):
        raise Failure("preflight_failed")
    reject_nested(SOURCE, REPOSITORY, VIEW_REPOSITORY)


def _mount_setup(bound: Bound, *, mount_call: Callable[..., int] | None = None, unshare_call: Callable[[int], int] | None = None) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    mount = libc.mount if mount_call is None else mount_call
    def invoke_mount(source: bytes | None, target: str, fs: bytes | None, flags: int, data: bytes | None = None) -> None:
        result = mount(source, target.encode("ascii"), fs, flags, data)
        if result != 0: raise Failure("preflight_failed")
    unshare = libc.unshare if unshare_call is None else unshare_call
    if unshare(CLONE_NEWNS) != 0: raise Failure("preflight_failed")
    invoke_mount(None, "/", None, MS_REC | MS_PRIVATE)
    try:
        os.mkdir(VIEW_ROOT, 0o700)
    except FileExistsError:
        pass
    # This run directory is deliberately reusable across one-shot runs, but
    # never accepted through a symlink or with relaxed ownership.
    _open_reusable_view_anchor()
    # The helper drops to ``homebase`` before exec.  The private tmpfs root
    # therefore needs traverse-only access, while the credential directory is
    # transferred to that exact uid and remains owner-only.
    invoke_mount(b"tmpfs", VIEW_ROOT, b"tmpfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, b"mode=0711,size=1048576")
    os.mkdir(VIEW_REPOSITORY, 0o700)
    credential_directory = os.path.dirname(VIEW_CREDENTIAL)
    os.mkdir(credential_directory, 0o700)
    credential_directory_fd = credential_fd = None
    raw = None
    try:
        credential_directory_fd = os.open(
            credential_directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        os.fchmod(credential_directory_fd, 0o700)
        credential_directory_info = os.fstat(credential_directory_fd)
        if not (
            _safe_dir(credential_directory_info, 0, 0o700)
            and credential_directory_info.st_gid == 0
        ):
            raise Failure("preflight_failed")

        _bind_private_views(bound, invoke_mount)
        raw = _read_credential_from_start(
            bound.credential_fd, os.fstat(bound.credential_fd).st_size
        )
        credential_fd = os.open(
            os.path.basename(VIEW_CREDENTIAL),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o400,
            dir_fd=credential_directory_fd,
        )
        os.fchmod(credential_fd, 0o400)
        offset = 0
        while offset < len(raw):
            count = os.write(credential_fd, memoryview(raw)[offset:])
            if not isinstance(count, int) or count <= 0: raise OSError()
            offset += count
        os.fsync(credential_fd)

        credential_info = os.fstat(credential_fd)
        if not (
            _safe_file(credential_info, 0, 0o400, MAX_CREDENTIAL_BYTES)
            and credential_info.st_gid == 0
            and credential_info.st_size == len(raw)
        ):
            raise Failure("preflight_failed")

        os.fchown(credential_fd, bound.uid, bound.gid)
        credential_info = os.fstat(credential_fd)
        if not (
            _safe_file(credential_info, bound.uid, 0o400, MAX_CREDENTIAL_BYTES)
            and credential_info.st_gid == bound.gid
            and credential_info.st_size == len(raw)
        ):
            raise Failure("preflight_failed")

        os.fchown(credential_directory_fd, bound.uid, bound.gid)
        credential_directory_info = os.fstat(credential_directory_fd)
        if not (
            _safe_dir(credential_directory_info, bound.uid, 0o700)
            and credential_directory_info.st_gid == bound.gid
        ):
            raise Failure("preflight_failed")

        _make_credential_directory_readonly(credential_directory, invoke_mount)
    except Failure:
        raise
    except Exception:
        raise Failure("preflight_failed") from None
    finally:
        if isinstance(raw, bytearray):
            raw[:] = b"\x00" * len(raw)
        for descriptor in (credential_fd, credential_directory_fd):
            if isinstance(descriptor, int):
                try: os.close(descriptor)
                except Exception: pass


def _filesystem_readonly(info: Any) -> bool:
    return bool(getattr(info, "f_flag", 0) & getattr(os, "ST_RDONLY", 1))


def _make_credential_directory_readonly(
    credential_directory: str,
    invoke_mount: Callable[[bytes | None, str, bytes | None, int, bytes | None], None],
    *,
    statvfs: Callable[[str], Any] | None = None,
) -> None:
    filesystem_stat = getattr(os, "statvfs", None) if statvfs is None else statvfs
    if filesystem_stat is None: raise Failure("preflight_failed")
    # MS_REMOUNT|MS_BIND is valid only after the directory is its own mount.
    # Keep the self-bind and read-only remount adjacent and fail closed on
    # either syscall before Restic can be dispatched.
    invoke_mount(credential_directory.encode("ascii"), credential_directory, None, MS_BIND, None)
    invoke_mount(None, credential_directory, None, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC, None)
    if not _filesystem_readonly(filesystem_stat(credential_directory)): raise Failure("preflight_failed")


def _prove_repository_access_after_drop(*, accessor: Callable[[str, int], bool] = os.access) -> None:
    try:
        if accessor(VIEW_REPOSITORY, os.R_OK | os.W_OK | os.X_OK) is not True: raise OSError()
    except Exception:
        raise Failure("preflight_failed") from None


def _close_verified(fd: int) -> None:
    os.close(fd)
    try: os.fstat(fd)
    except OSError as exc:
        if exc.errno == errno.EBADF: return
    raise Failure("preflight_failed")


def _read_credential_from_start(descriptor: int, size: int) -> bytearray:
    try: os.lseek(descriptor, 0, os.SEEK_SET)
    except Exception: raise Failure("preflight_failed") from None
    return _read_exact(descriptor, size)


def _drop_identity(bound: Bound) -> None:
    try:
        os.setgroups([]); os.setresgid(bound.gid, bound.gid, bound.gid); os.setresuid(bound.uid, bound.uid, bound.uid)
        if os.geteuid() != bound.uid or os.getegid() != bound.gid: raise OSError()
        # A full uid transition clears Linux effective/permitted capabilities
        # unless KEEP_CAPS was set.  Query capget directly rather than using a
        # procfs pathname after descriptor binding.
        class Header(ctypes.Structure): _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]
        class Data(ctypes.Structure): _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]
        header, data = Header(0x20080522, 0), (Data * 2)()
        if ctypes.CDLL(None, use_errno=True).syscall(125, ctypes.byref(header), ctypes.byref(data)) != 0 or not _capability_sets_clear(data): raise OSError()
    except Exception: raise Failure("preflight_failed") from None


def _capability_sets_clear(words: Any) -> bool:
    return not any(item.effective or item.permitted or item.inheritable for item in words)


def _arm_reference(grant_id: str, helper_sha256: str) -> str:
    return "predeploy_backup_root_helper_v1:" + hashlib.sha256((grant_id + helper_sha256).encode("ascii")).hexdigest()


def _validate_arm_record(record: Any, now: float, helper_sha256: str) -> str:
    if type(record) is not dict or set(record) != {"schema_id", "grant_id", "expires_at_epoch", "helper_sha256"} or record.get("schema_id") != ARM_SCHEMA_ID or not isinstance(record.get("grant_id"), str) or not _HEX.fullmatch(record["grant_id"]) or type(record.get("expires_at_epoch")) is not int or not now < record["expires_at_epoch"] <= now + MAX_ARM_FUTURE_SECONDS or record.get("helper_sha256") != helper_sha256:
        raise Failure("arm_expired")
    return record["grant_id"]


def _consume_arm(now: float, helper_sha256: str) -> str:
    """Atomically consume only a root-authored, current arm immediately before dispatch."""
    import fcntl
    directory = fd = used = None; published = False; reference: str | None = None
    try:
        directory = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        if not _safe_dir(os.fstat(directory), 0, 0o700): raise Failure("arm_invalid")
        fd = os.open(ARM_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        info = os.fstat(fd)
        if not _safe_file(info, 0, 0o600, MAX_ARM_BYTES): raise Failure("arm_invalid")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = json.loads(_read_exact(fd, info.st_size).decode("ascii"))
        grant_id = _validate_arm_record(record, now, helper_sha256)
        reference = _arm_reference(grant_id, helper_sha256)
        used_name = USED_PREFIX + grant_id
        used = os.open(used_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        written = 0
        while written < 5:
            amount = os.write(used, b"used\n"[written:])
            if not isinstance(amount, int) or amount <= 0: raise OSError()
            written += amount
        os.fsync(used); published = True; os.fsync(directory)
        return reference
    except BlockingIOError: raise Failure("arm_contended") from None
    except FileExistsError: raise Failure("arm_replayed") from None
    except Failure: raise
    except Exception: raise Failure("not_armed", arm_consumed=published, reference=reference) from None
    finally:
        for item in (used, fd, directory):
            if isinstance(item, int):
                try: os.close(item)
                except Exception: pass


def _acquire_run_lock() -> int:
    """Return a root-owned lock held through receipt publication."""
    try: import fcntl
    except Exception: raise Failure("preflight_failed") from None
    directory = descriptor = None
    success = False
    try:
        directory = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        if not _safe_dir(os.fstat(directory), 0, 0o700): raise Failure("arm_invalid")
        descriptor = os.open("execution.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        info = os.fstat(descriptor)
        if not _safe_file(info, 0, 0o600): raise Failure("arm_invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        success = True
        return descriptor
    except BlockingIOError: raise Failure("arm_contended") from None
    except Failure: raise
    except Exception: raise Failure("not_armed") from None
    finally:
        if isinstance(directory, int):
            try: os.close(directory)
            except Exception: pass
        # Return ownership only after successful flock.
        if isinstance(descriptor, int) and not success:
            try: os.close(descriptor)
            except Exception: pass


def _invalidate_public_receipts() -> None:
    directory = None
    try:
        directory = os.open(posixpath.dirname(PUBLIC_RECEIPT_PATH), _os_flag("O_RDONLY", _O_RDONLY) | _os_flag("O_DIRECTORY", _O_DIRECTORY) | _os_flag("O_NOFOLLOW", _O_NOFOLLOW) | _os_flag("O_CLOEXEC", _O_CLOEXEC))
        if not _safe_dir(os.fstat(directory), 0, 0o755): raise Failure("preflight_failed")
        # The legacy result must never outlive a new diagnostic sidecar.
        for path in (PUBLIC_RECEIPT_PATH, PUBLIC_DIAGNOSTIC_PATH):
            try: os.unlink(os.path.basename(path), dir_fd=directory)
            except FileNotFoundError: pass
        os.fsync(directory)
    except Exception: raise Failure("preflight_failed") from None
    finally:
        if isinstance(directory, int):
            try: os.close(directory)
            except Exception: pass


def _same_descriptor_identity(left: Any, right: Any) -> bool:
    try: return left.st_dev == right.st_dev and left.st_ino == right.st_ino
    except Exception: return False


def _prove_live_exec(pid: int, bound: Bound) -> bool:
    descriptor = None
    try:
        descriptor = os.open(f"/proc/{pid}/exe", getattr(os, "O_PATH", os.O_RDONLY) | os.O_CLOEXEC)
        return _same_descriptor_identity(os.fstat(descriptor), os.fstat(bound.restic_fd))
    except Exception:
        return False
    finally:
        if isinstance(descriptor, int):
            try: os.close(descriptor)
            except Exception: pass


def _waitpid_nointr(pid: int, options: int) -> tuple[int, int]:
    for _ in range(32):
        try: return os.waitpid(pid, options)
        except InterruptedError: continue
    raise OSError()


def _kill_and_reap(pid: int) -> tuple[bool, int | None]:
    try:
        for _ in range(32):
            try:
                os.kill(pid, 9)
                break
            except InterruptedError: continue
        else: return False, None
        done, status = _waitpid_nointr(pid, 0)
        return done == pid, status if done == pid else None
    except Exception:
        return False, None


def _drain_terminal_output(descriptor: int, output: bytearray) -> bool | None:
    """Drain a reaped child's pipe without waiting on an inherited writer."""
    deadline = time.monotonic() + TERMINAL_DRAIN_SECONDS; interrupted = 0
    while len(output) <= MAX_OUTPUT_BYTES:
        try:
            ready, _, _ = select.select([descriptor], [], [], max(0.0, min(0.05, deadline - time.monotonic())))
        except InterruptedError:
            interrupted += 1
            if interrupted >= 32: return None
            continue
        except Exception:
            return None
        if not ready:
            if time.monotonic() >= deadline: return None
            continue
        try: piece = os.read(descriptor, min(4096, MAX_OUTPUT_BYTES + 1 - len(output)))
        except Exception: return None
        if not piece: return len(output) <= MAX_OUTPUT_BYTES
        output.extend(piece)
    return False


def _run_child(bound: Bound, command: tuple[str, ...], timeout: int, capture: bool) -> ChildOutcome:
    read_fd = write_fd = marker_read = marker_write = pid = None
    live_exec = False; terminal = False
    try:
        if capture: read_fd, write_fd = os.pipe()
        marker_read, marker_write = os.pipe()
        os.set_inheritable(marker_read, False); os.set_inheritable(marker_write, False)
        pid = os.fork()
        if pid == 0:
            try:
                os.close(marker_read)
                if capture:
                    os.close(read_fd); os.dup2(write_fd, 1); os.close(write_fd)
                else:
                    null = os.open(os.devnull, os.O_WRONLY); os.dup2(null, 1); os.close(null)
                null = os.open(os.devnull, os.O_WRONLY); os.dup2(null, 2); os.close(null)
                _mount_setup(bound)
                for descriptor in (bound.source_parent_fd, bound.source_fd, bound.repository_fd, bound.credential_fd): _close_verified(descriptor)
                _drop_identity(bound)
                _prove_repository_access_after_drop()
                native = _execveat_syscall()
                assert native is not None
                argv = (ctypes.c_char_p * (len(command) + 1))(*[part.encode("ascii") for part in command], None)
                env = (ctypes.c_char_p * 3)(b"PATH=/usr/bin:/bin", ("RESTIC_PASSWORD_FILE=" + VIEW_CREDENTIAL).encode("ascii"), None)
                # A successful execveat(2) never returns.  Any return from the
                # ctypes syscall boundary, including a (malformed) zero return
                # supplied by a test double, therefore proves pre-exec failure.
                native[0](native[1], bound.restic_fd, ctypes.c_char_p(b""), argv, env, EXECVEAT_EMPTY_PATH)
                raise OSError()
            except Exception:
                try: os.write(marker_write, b"P")
                except Exception: pass
            os._exit(125)
        if capture: os.close(write_fd); write_fd = None
        os.close(marker_write); marker_write = None
        output = bytearray(); deadline = time.monotonic() + timeout; marker = b""; marker_eof = False; live_exec = False
        while True:
            try:
                done, status = _waitpid_nointr(pid, os.WNOHANG)
            except Exception:
                reaped, _ = _kill_and_reap(pid)
                terminal = reaped
                return ChildOutcome("confirmed_exec" if live_exec else "unknown", "wait_failed", False, 0, True, reaped, reaped, b"")
            if done == pid:
                terminal = True
                try:
                    while not marker_eof:
                        piece = os.read(marker_read, 1)
                        if not piece: marker_eof = True; break
                        marker += piece
                        if marker != b"P": break
                except Exception:
                    marker = b"X"
                if capture:
                    drained = _drain_terminal_output(read_fd, output)
                    if drained is None:
                        return ChildOutcome("confirmed_exec" if live_exec else "unknown", "wait_failed", False, 0, True, True, True, b"")
                    if drained is False:
                        return ChildOutcome("confirmed_exec" if live_exec else "unknown", "output_limit", False, 0, True, True, True, b"")
                try: code = os.waitstatus_to_exitcode(status)
                except Exception:
                    return ChildOutcome("confirmed_exec" if live_exec else "unknown", "wait_failed", False, 0, True, True, True, bytes(output))
                if marker == b"P": return ChildOutcome("preexec_failed", "not_available", False, 0, True, True, True, bytes(output))
                if marker_eof and code >= 0 and (code != 125 or live_exec):
                    return ChildOutcome("confirmed_exec", "exited_zero" if code == 0 else "exited_nonzero", True, code, True, True, True, bytes(output))
                return ChildOutcome("confirmed_exec" if live_exec else "unknown", "signaled" if code < 0 else "handshake_ambiguous", False, 0, True, True, True, bytes(output))
            if time.monotonic() >= deadline:
                reaped, _ = _kill_and_reap(pid)
                terminal = reaped
                return ChildOutcome("confirmed_exec" if live_exec else "unknown", "timed_out", False, 0, True, reaped, reaped, b"")
            wait_for = min(0.05, max(0.0, deadline - time.monotonic()))
            readers = [marker_read] + ([read_fd] if capture else [])
            try: ready, _, _ = select.select(readers, [], [], wait_for)
            except InterruptedError: continue
            except Exception:
                reaped, _ = _kill_and_reap(pid)
                terminal = reaped
                return ChildOutcome("confirmed_exec" if live_exec else "unknown", "wait_failed", False, 0, True, reaped, reaped, b"")
            if marker_read in ready:
                try: piece = os.read(marker_read, 1)
                except Exception: piece = b"X"
                if not piece:
                    marker_eof = True
                    live_exec = _prove_live_exec(pid, bound) or live_exec
                else: marker += piece
            if capture and read_fd in ready:
                try: piece = os.read(read_fd, min(4096, MAX_OUTPUT_BYTES + 1 - len(output)))
                except Exception: raise OSError() from None
                output.extend(piece)
                if len(output) > MAX_OUTPUT_BYTES:
                    reaped, _ = _kill_and_reap(pid)
                    terminal = reaped
                    return ChildOutcome("confirmed_exec" if live_exec else "unknown", "output_limit", False, 0, True, reaped, reaped, b"")
    except Exception:
        if isinstance(pid, int) and pid > 0 and not terminal:
            reaped, _ = _kill_and_reap(pid)
            return ChildOutcome("confirmed_exec" if live_exec else "unknown", "wait_failed", False, 0, True, reaped, reaped, b"")
        raise
    finally:
        for item in (read_fd, write_fd, marker_read, marker_write):
            if isinstance(item, int):
                try: os.close(item)
                except Exception: pass


def _parse_snapshot(raw: bytes, started: float) -> tuple[str, int] | None:
    try:
        if len(raw) > MAX_OUTPUT_BYTES: return None
        rows = json.loads(raw.decode("utf-8"))
        if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict: return None
        row = rows[0]; snapshot = row.get("id"); stamp = row.get("time")
        if not isinstance(snapshot, str) or not _HEX.fullmatch(snapshot) or "odysseus-pre-update" not in row.get("tags", []) or SOURCE not in row.get("paths", []): return None
        value = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
        age = time.time() - value
        if value < started or age < 0 or age > BACKUP_TIMEOUT_SECONDS + READBACK_TIMEOUT_SECONDS: return None
        return snapshot, int(age)
    except Exception: return None


def _execute_under_lock(digest: str, now: Callable[[], float]) -> tuple[dict[str, Any], dict[str, Any]]:
    bound: Bound | None = None
    reference: str | None = None; phase = "pre_arm"; outcome: ChildOutcome | None = None; arm_consumed = False
    try:
        started = float(now())
        reference = _consume_arm(started, digest)
        arm_consumed = True; phase = "post_arm_pre_backup"
        _invalidate_public_receipts()
        bound = _bind_identities()
        bound = Bound(bound.source_parent_fd, bound.source_fd, bound.repository_fd, bound.credential_fd, bound.restic_fd, bound.uid, bound.gid, reference)
        phase = "backup_start"; outcome = None
        outcome = _run_child(bound, (RESTIC_BINARY, "-r", VIEW_REPOSITORY, "backup", SOURCE, "--exclude", "**/.git", "--exclude", "**/__pycache__", "--exclude", "**/.pytest_cache", "--exclude", "**/node_modules", "--exclude", "**/backups", "--exclude", "**/logs/*.log", "--exclude", "**/tmp", "--exclude", "**/.cache", "--exclude-caches", "--tag", "homeserver", "--tag", "pre-update", "--tag", "odysseus-pre-update"), BACKUP_TIMEOUT_SECONDS, False)
        phase = "backup_wait"
        if outcome.exit_class == "timed_out":
            result = _unknown("backup_timeout", reference)
            return result, _diagnostic(result, failed_phase=phase, outcome=outcome, arm_consumed=arm_consumed)
        if outcome.restic_exec_state != "confirmed_exec" or outcome.exit_class != "exited_zero":
            result = _unknown("backup_failed", reference)
            return result, _diagnostic(result, failed_phase="backup_start" if outcome.restic_exec_state == "preexec_failed" else phase, outcome=outcome, arm_consumed=arm_consumed)
        phase = "readback_start"; outcome = None
        outcome = _run_child(bound, (RESTIC_BINARY, "-r", VIEW_REPOSITORY, "--no-lock", "snapshots", "--tag", "odysseus-pre-update", "--latest", "1", "--json"), READBACK_TIMEOUT_SECONDS, True)
        phase = "readback_wait"
        if outcome.exit_class == "timed_out":
            result = _unknown("readback_timeout", reference)
            return result, _diagnostic(result, failed_phase=phase, outcome=outcome, arm_consumed=arm_consumed)
        if outcome.restic_exec_state != "confirmed_exec" or outcome.exit_class != "exited_zero":
            result = _unknown("readback_failed", reference)
            return result, _diagnostic(result, failed_phase="readback_start" if outcome.restic_exec_state == "preexec_failed" else phase, outcome=outcome, arm_consumed=arm_consumed)
        phase = "readback_validate"
        snapshot = _parse_snapshot(outcome.output, started)
        if snapshot is None:
            result = _unknown("readback_invalid", reference)
            return result, _diagnostic(result, failed_phase=phase, outcome=outcome, arm_consumed=arm_consumed)
        value = {"schema_id": SCHEMA_ID, "status": "ok", "repository_identity": "restic_homeserver_backup_v1", "protected_source_identity": "odysseus_protected_source_v1", "backup_effect": "created", "action_provenance_ref": reference, "snapshot_id": snapshot[0], "source_included": True, "snapshot_created_after_start": True, "snapshot_age_seconds": snapshot[1], "snapshot_fresh": True, "concurrent_lock_held": True, "partial_snapshot_detected": False, "raw_stdout_visible": False, "raw_stderr_visible": False, "exception_text_visible": False, "environment_visible": False, "file_contents_visible": False, "paths_visible": False, "hostnames_visible": False, "secret_values_visible": False}
        value["evidence_sha256"] = _digest(value)
        return value, _diagnostic(value, failed_phase="none", outcome=outcome, arm_consumed=arm_consumed)
    except Failure as failure:
        arm_consumed = arm_consumed or failure.arm_consumed
        if reference is None and failure.arm_consumed and isinstance(failure.reference, str): reference = failure.reference
        result = _blocked(failure.code) if reference is None else _unknown("execution_ambiguous", reference)
        return result, _diagnostic(result, failed_phase="pre_arm" if reference is None else phase, outcome=outcome, arm_consumed=arm_consumed)
    except Exception:
        result = _blocked("preflight_failed") if reference is None else _unknown("execution_ambiguous", reference)
        return result, _diagnostic(result, failed_phase="pre_arm" if reference is None else phase, outcome=outcome, arm_consumed=arm_consumed)
    finally:
        if bound is not None:
            for descriptor in (bound.source_parent_fd, bound.source_fd, bound.repository_fd, bound.credential_fd, bound.restic_fd):
                try: os.close(descriptor)
                except Exception: pass


def run_root_helper(*, helper_sha256: str | None = None, now: Callable[[], float] = time.time, publish: bool = False) -> dict[str, Any]:
    """The only effectful entrypoint; it accepts no caller command or path."""
    if getattr(os, "geteuid", lambda: -1)() != 0: return _blocked("preflight_failed")
    digest = helper_sha256 or hashlib.sha256(open(__file__, "rb").read()).hexdigest()
    if not _HEX.fullmatch(digest): return _blocked("preflight_failed")
    lock_fd = None
    try:
        lock_fd = _acquire_run_lock()
        value, diagnostic = _execute_under_lock(digest, now)
        if publish and (not _write_public_diagnostic(diagnostic) or not _write_public_receipt(value)):
            reference = value.get("action_provenance_ref")
            return _unknown("execution_ambiguous", reference) if isinstance(reference, str) else _blocked("preflight_failed")
        return value
    except Failure as failure:
        return _blocked(failure.code)
    finally:
        if isinstance(lock_fd, int):
            try: os.close(lock_fd)
            except Exception: pass


def _safe_public_file(info: Any) -> bool:
    try: return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o644 and info.st_nlink == 1
    except Exception: return False


def _write_public_document(path: str, value: Mapping[str, Any], validator: Callable[[Any], bool]) -> bool:
    """Publish only an already validated redacted envelope, atomically.

    The runtime directory is created by systemd.  It is intentionally not a
    caller-selected path and never receives stdout, stderr or exception text.
    """
    if not validator(value): return False
    directory = descriptor = None; temporary: str | None = None; owned: tuple[int, int] | None = None
    target = posixpath.basename(path); replace_attempted = False; success = False
    try:
        directory = os.open(posixpath.dirname(path), _os_flag("O_RDONLY", _O_RDONLY) | _os_flag("O_DIRECTORY", _O_DIRECTORY) | _os_flag("O_NOFOLLOW", _O_NOFOLLOW) | _os_flag("O_CLOEXEC", _O_CLOEXEC))
        info = os.fstat(directory)
        if not _safe_dir(info, 0, 0o755): return False
        temporary = "." + os.path.basename(path) + "." + os.urandom(16).hex() + ".tmp"
        descriptor = os.open(temporary, _os_flag("O_WRONLY", _O_WRONLY) | _os_flag("O_CREAT", _O_CREAT) | _os_flag("O_EXCL", _O_EXCL) | _os_flag("O_NOFOLLOW", _O_NOFOLLOW) | _os_flag("O_CLOEXEC", _O_CLOEXEC), 0o600, dir_fd=directory)
        initial = os.fstat(descriptor); owned = (initial.st_dev, initial.st_ino)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1: return False
        os.fchown(descriptor, 0, 0); os.fchmod(descriptor, 0o644)
        raw = json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        offset = 0
        while offset < len(raw):
            count = os.write(descriptor, raw[offset:])
            if not isinstance(count, int) or count <= 0: return False
            offset += count
        os.fsync(descriptor)
        if not _safe_public_file(os.fstat(descriptor)): return False
        os.close(descriptor); descriptor = None
        replace_attempted = True
        os.replace(temporary, target, src_dir_fd=directory, dst_dir_fd=directory); temporary = None
        os.fsync(directory)
        descriptor = os.open(target, _os_flag("O_RDONLY", _O_RDONLY) | _os_flag("O_NOFOLLOW", _O_NOFOLLOW) | _os_flag("O_CLOEXEC", _O_CLOEXEC), dir_fd=directory)
        if not _safe_public_file(os.fstat(descriptor)): return False
        observed = os.read(descriptor, len(raw) + 1)
        if observed != raw: return False
        success = True
        return True
    except Exception:
        return False
    finally:
        if isinstance(descriptor, int):
            try: os.close(descriptor)
            except Exception: pass
        if not success and isinstance(directory, int) and owned is not None:
            for name in (temporary, target if replace_attempted else None):
                if not isinstance(name, str): continue
                try:
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if (info.st_dev, info.st_ino) == owned:
                        os.unlink(name, dir_fd=directory)
                        try: os.fsync(directory)
                        except Exception: pass
                except Exception: pass
        if isinstance(directory, int):
            try: os.close(directory)
            except Exception: pass


def _write_public_receipt(value: Mapping[str, Any]) -> bool:
    return _write_public_document(PUBLIC_RECEIPT_PATH, value, validate_envelope)


def _write_public_diagnostic(value: Mapping[str, Any]) -> bool:
    return _write_public_document(PUBLIC_DIAGNOSTIC_PATH, value, validate_diagnostic)


def main() -> int:
    value = run_root_helper(publish=True)
    # The service has StandardOutput=null.  This exists only for a local root
    # console; it remains one canonical redacted line.
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if value.get("status") == "ok" else 1


if __name__ == "__main__": raise SystemExit(main())

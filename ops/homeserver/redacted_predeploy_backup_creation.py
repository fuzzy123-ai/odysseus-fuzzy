#!/usr/bin/env python3
"""Create one fixed, redacted pre-update backup snapshot.

This wrapper intentionally exposes no options.  It opens the protected source,
repository and credential without following links, retains those descriptors
through backup and readback, and gives each Restic child a private mount
namespace in which the retained objects are bound at the fixed recorded paths.
It never copies process output, exception text, environment, or filesystem
details into its packet.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
import ctypes
import select
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.redacted_predeploy_backup_creation.v1"
RESTIC_BINARY = "/usr/bin/restic"
BACKUP_MOUNT = "/mnt/backup"
REPOSITORY = "/mnt/backup/restic/homeserver"
SOURCE = "/opt/odysseus"
NEXTCLOUD_ROOT = "/opt/nextcloud"
HOMEBASE_HOME = "/home/homebase"
DB_DUMP_ROOT = ""
DB_DUMP_STAGING = "/home/homebase/.cache/odysseus-backup/db-dumps"
CONFIG_PATH = "/home/homebase/.config/odysseus-backup/restic-observation.env"
PASSWORD_FILE = "/home/homebase/.config/odysseus-backup/restic-password"
LOCK_PATH = "/home/homebase/.local/state/odysseus-predeploy-backup.lock"
LOCK_PARENT = "/home/homebase/.local/state"
EXPECTED_OWNER = "homebase"
BACKUP_TIMEOUT_SECONDS = 1_800
READBACK_TIMEOUT_SECONDS = 20
OUTER_PACKET_TIMEOUT_SECONDS = 1_860
MAX_READBACK_BYTES = 65_536
MAX_CREDENTIAL_BYTES = 16_384
MAX_FDINFO_BYTES = 16_384
MAX_MOUNTINFO_BYTES = 1_048_576
MAX_SNAPSHOT_AGE_SECONDS = OUTER_PACKET_TIMEOUT_SECONDS

BACKUP_COMMAND = (
    RESTIC_BINARY, "-r", REPOSITORY, "backup", SOURCE,
    "--exclude", "**/.git", "--exclude", "**/__pycache__",
    "--exclude", "**/.pytest_cache", "--exclude", "**/node_modules",
    "--exclude", "**/backups", "--exclude", "**/logs/*.log",
    "--exclude", "**/tmp", "--exclude", "**/.cache", "--exclude-caches",
    "--tag", "homeserver", "--tag", "pre-update", "--tag", "odysseus-pre-update",
)
READBACK_COMMAND = (
    RESTIC_BINARY, "-r", REPOSITORY, "--no-lock", "snapshots",
    "--tag", "odysseus-pre-update", "--latest", "1", "--json",
)
FIXED_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "RESTIC_PASSWORD_FILE": PASSWORD_FILE,
}

_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{64}$")
_PROVENANCE_REF = re.compile(r"^predeploy_backup_creation_v1:[0-9a-f]{64}$")
_BLOCKED_ERRORS = frozenset({
    "config_unavailable", "config_invalid", "mount_unavailable",
    "repository_unsafe", "password_file_unsafe", "restic_unavailable",
    "source_path_missing", "identity_bind_unavailable", "lock_contended",
    "lock_unavailable", "internal_error",
})
_UNKNOWN_ERRORS = frozenset({
    "backup_timeout", "backup_failed", "backup_exception", "backup_result_invalid",
    "readback_timeout", "readback_failed", "readback_exception",
    "readback_output_too_large", "readback_malformed", "snapshot_missing",
    "snapshot_id_invalid", "snapshot_invalid", "snapshot_stale", "snapshot_not_new",
    "internal_error",
})
_OK_KEYS = frozenset({
    "schema_id", "status", "repository_identity", "protected_source_identity",
    "backup_effect", "action_provenance_ref", "snapshot_id", "source_included",
    "snapshot_created_after_start", "snapshot_age_seconds", "snapshot_fresh",
    "concurrent_lock_held", "partial_snapshot_detected", "raw_stdout_visible",
    "raw_stderr_visible", "exception_text_visible", "environment_visible",
    "file_contents_visible", "paths_visible", "hostnames_visible",
    "secret_values_visible", "evidence_sha256",
})
_BLOCKED_KEYS = frozenset({
    "schema_id", "status", "error_code", "backup_invoked", "retry_permitted", "evidence_sha256",
})
_UNKNOWN_KEYS = frozenset({
    "schema_id", "status", "error_code", "effect_may_have_occurred",
    "retry_permitted", "manual_recovery_required", "action_provenance_ref", "evidence_sha256",
})


class ContractFailure(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code if code in _BLOCKED_ERRORS else "internal_error"


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def blocked(code: str) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID, "status": "blocked",
        "error_code": code if code in _BLOCKED_ERRORS else "internal_error",
        "backup_invoked": False, "retry_permitted": False,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def _action_provenance_ref(started_at: float) -> str:
    """Return a fixed-grammar opaque reference without emitting the start time."""
    canonical = json.dumps({
        "action": "predeploy_backup_creation_v1",
        "started_at_epoch_millis": int(started_at * 1_000),
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "predeploy_backup_creation_v1:" + hashlib.sha256(canonical).hexdigest()


def unknown(code: str, *, action_provenance_ref: str) -> dict[str, Any]:
    assert _PROVENANCE_REF.fullmatch(action_provenance_ref)
    payload = {
        "schema_id": SCHEMA_ID, "status": "unknown",
        "error_code": code if code in _UNKNOWN_ERRORS else "internal_error",
        "effect_may_have_occurred": True, "retry_permitted": False,
        "manual_recovery_required": True, "action_provenance_ref": action_provenance_ref,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def _read_fixed_config() -> str:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            return handle.read(4097)
    except Exception:
        raise ContractFailure("config_unavailable") from None


def _validate_config(value: Any) -> None:
    expected = "RESTIC_PASSWORD_FILE=" + PASSWORD_FILE + "\n"
    if not isinstance(value, str) or len(value.encode("utf-8")) > 4096 or value != expected:
        raise ContractFailure("config_invalid")


def _validate_process_environment(value: Any) -> None:
    """Reject inherited write/auth/path controls without inspecting their values."""
    if not isinstance(value, Mapping):
        raise ContractFailure("config_invalid")
    for key in ("RESTIC_PASSWORD", "RESTIC_PASSWORD_COMMAND"):
        if key in value:
            raise ContractFailure("config_invalid")
    for key in (
        "RESTIC_REPOSITORY", "RESTIC_BINARY", "RESTIC_BIN", "BACKUP_MOUNT", "ODYSSEUS_ROOT", "SOURCE",
        "NEXTCLOUD_ROOT", "HOMEBASE_HOME", "DB_DUMP_ROOT", "DB_DUMP_STAGING", "RESTIC_USE_SUDO",
        "RESTIC_REPAIR_REPO_OWNER",
    ):
        if key in value:
            raise ContractFailure("config_invalid")
    password_file = value.get("RESTIC_PASSWORD_FILE")
    if password_file is not None and password_file != PASSWORD_FILE:
        raise ContractFailure("config_invalid")


def _production_owner_lookup(owner: str) -> Any:
    try:
        import pwd
        return pwd.getpwnam(owner)
    except Exception:
        raise ContractFailure("internal_error") from None


def _expected_uid(owner_lookup: Callable[[str], Any]) -> int:
    try:
        uid = owner_lookup(EXPECTED_OWNER).pw_uid
    except Exception:
        raise ContractFailure("internal_error") from None
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise ContractFailure("internal_error")
    return uid


def _safe_regular(path: str, *, expected_uid: int, lstat: Callable[[str], Any], exact_mode: int | None = None,
                  executable: bool = False) -> bool:
    try:
        info = lstat(path)
        mode = int(info.st_mode)
        permissions = stat.S_IMODE(mode)
        return (
            stat.S_ISREG(mode) and int(info.st_uid) == expected_uid
            and (permissions == exact_mode if exact_mode is not None else (permissions & 0o022) == 0)
            and (not executable or bool(permissions & 0o100))
        )
    except Exception:
        return False


def _safe_binary(*, lstat: Callable[[str], Any]) -> bool:
    try:
        info = lstat(RESTIC_BINARY)
        mode = int(info.st_mode)
        permissions = stat.S_IMODE(mode)
        return stat.S_ISREG(mode) and int(info.st_uid) == 0 and bool(permissions & 0o100) and (permissions & 0o022) == 0
    except Exception:
        return False


def _safe_directory(path: str, *, expected_uid: int, lstat: Callable[[str], Any], required_permissions: int = 0o100) -> bool:
    try:
        info = lstat(path)
        mode = int(info.st_mode)
        permissions = stat.S_IMODE(mode)
        return stat.S_ISDIR(mode) and int(info.st_uid) == expected_uid and (permissions & required_permissions) == required_permissions and (permissions & 0o022) == 0
    except Exception:
        return False


def _production_mount_checker(path: str) -> bool:
    try:
        return os.path.ismount(path)
    except Exception:
        return False


def _production_lock_acquire(expected_uid: int) -> int:
    """Create/validate and take the fixed local advisory lock without following links."""
    descriptor: int | None = None
    try:
        import fcntl
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if not isinstance(nofollow, int):
            raise ContractFailure("lock_unavailable")
        flags = os.O_RDWR | os.O_CREAT | nofollow
        descriptor = os.open(LOCK_PATH, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or int(info.st_uid) != expected_uid or stat.S_IMODE(info.st_mode) != 0o600:
            os.close(descriptor)
            raise OSError("unsafe lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError:
        raise ContractFailure("lock_contended") from None
    except ContractFailure:
        raise
    except Exception:
        raise ContractFailure("lock_unavailable") from None
    finally:
        if descriptor is not None and sys.exc_info()[0] is not None:
            try:
                os.close(descriptor)
            except Exception:
                pass


def _production_lock_release(descriptor: Any) -> None:
    try:
        import fcntl
        fcntl.flock(int(descriptor), fcntl.LOCK_UN)
    except Exception:
        # The invocation is already represented by a canonical result; release
        # errors must neither disclose nor transform that outcome.
        pass
    finally:
        try:
            os.close(int(descriptor))
        except Exception:
            pass


class BoundIdentities:
    """Descriptors whose inodes, rather than their original names, are authoritative."""

    __slots__ = ("source_fd", "mount_fd", "repository_fd", "config_fd", "password_fd", "restic_fd")

    def __init__(
        self, source_fd: int, mount_fd: int, repository_fd: int,
        config_fd: int, password_fd: int, restic_fd: int,
    ) -> None:
        self.source_fd = source_fd
        self.mount_fd = mount_fd
        self.repository_fd = repository_fd
        self.config_fd = config_fd
        self.password_fd = password_fd
        self.restic_fd = restic_fd

    @property
    def pass_fds(self) -> tuple[int, int, int, int]:
        return (self.source_fd, self.repository_fd, self.password_fd, self.restic_fd)

    @property
    def owned_fds(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.source_fd, self.mount_fd, self.repository_fd,
            self.config_fd, self.password_fd, self.restic_fd,
        )


def _open_nofollow(path: str, *, directory: bool) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        raise ContractFailure("identity_bind_unavailable")
    flags = (getattr(os, "O_PATH", os.O_RDONLY) if directory else os.O_RDONLY) | nofollow
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        return os.open(path, flags)
    except Exception:
        raise ContractFailure("identity_bind_unavailable") from None


def _directory_open_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        raise ContractFailure("identity_bind_unavailable")
    return getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_DIRECTORY", 0) | nofollow


def _open_directory_components(path: str) -> int:
    if not path.startswith("/") or path == "/" or "//" in path or "/../" in path or "/./" in path:
        raise ContractFailure("identity_bind_unavailable")
    current: int | None = None
    try:
        current = os.open("/", _directory_open_flags())
        for component in path.split("/")[1:]:
            if not component or component in {".", ".."}:
                raise ContractFailure("identity_bind_unavailable")
            following = os.open(component, _directory_open_flags(), dir_fd=current)
            os.close(current)
            current = following
        return current
    except ContractFailure:
        if current is not None:
            try: os.close(current)
            except Exception: pass
        raise
    except Exception:
        if current is not None:
            try: os.close(current)
            except Exception: pass
        raise ContractFailure("identity_bind_unavailable") from None


def _open_relative_components(parent_fd: int, components: tuple[str, ...]) -> int:
    current = os.dup(parent_fd)
    try:
        for component in components:
            if not component or component in {".", ".."} or "/" in component:
                raise ContractFailure("identity_bind_unavailable")
            following = os.open(component, _directory_open_flags(), dir_fd=current)
            os.close(current)
            current = following
        return current
    except ContractFailure:
        try: os.close(current)
        except Exception: pass
        raise
    except Exception:
        try: os.close(current)
        except Exception: pass
        raise ContractFailure("identity_bind_unavailable") from None


def _read_proc_bounded(path: str, maximum: int) -> bytes:
    descriptor: int | None = None
    output = bytearray()
    try:
        descriptor = os.open(path, os.O_RDONLY)
        while len(output) <= maximum:
            chunk = os.read(descriptor, min(4096, maximum + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
        if not output or len(output) > maximum:
            raise ContractFailure("identity_bind_unavailable")
        return bytes(output)
    except ContractFailure:
        raise
    except Exception:
        raise ContractFailure("identity_bind_unavailable") from None
    finally:
        if descriptor is not None:
            try: os.close(descriptor)
            except Exception: pass


def _prove_fixed_backup_mount(mount_fd: int) -> None:
    fdinfo = _read_proc_bounded(f"/proc/self/fdinfo/{mount_fd}", MAX_FDINFO_BYTES)
    mountinfo = _read_proc_bounded("/proc/self/mountinfo", MAX_MOUNTINFO_BYTES)
    try:
        identifiers = [line.split("\t", 1)[1] for line in fdinfo.decode("ascii").splitlines() if line.startswith("mnt_id:\t")]
        if len(identifiers) != 1 or not identifiers[0].isdigit() or len(identifiers[0]) > 20:
            raise ValueError
        mount_id = identifiers[0]
        matches = []
        for line in mountinfo.decode("ascii").splitlines():
            fields = line.split(" ")
            if len(fields) >= 10 and fields[0] == mount_id and fields[4] == BACKUP_MOUNT and "-" in fields:
                matches.append(fields)
        if len(matches) != 1:
            raise ValueError
    except Exception:
        raise ContractFailure("identity_bind_unavailable") from None


def _production_identity_bind(expected_uid: int) -> BoundIdentities:
    descriptors: list[int] = []
    credential_source_fd: int | None = None
    credential = bytearray(MAX_CREDENTIAL_BYTES)
    eof_probe = bytearray(1)
    try:
        restic_fd = _open_nofollow(RESTIC_BINARY, directory=False); descriptors.append(restic_fd)
        source_fd = _open_nofollow(SOURCE, directory=True); descriptors.append(source_fd)
        mount_fd = _open_directory_components(BACKUP_MOUNT); descriptors.append(mount_fd)
        _prove_fixed_backup_mount(mount_fd)
        repository_fd = _open_relative_components(mount_fd, ("restic", "homeserver")); descriptors.append(repository_fd)
        config_fd = _open_directory_components(os.path.dirname(PASSWORD_FILE)); descriptors.append(config_fd)
        credential_source_fd = os.open(
            os.path.basename(PASSWORD_FILE), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=config_fd,
        )
        restic_info = os.fstat(restic_fd)
        source_info = os.fstat(source_fd)
        mount_info = os.fstat(mount_fd)
        repository_info = os.fstat(repository_fd)
        config_info = os.fstat(config_fd)
        password_before = os.fstat(credential_source_fd)
        if not (
            stat.S_ISREG(restic_info.st_mode) and restic_info.st_uid == 0
            and bool(stat.S_IMODE(restic_info.st_mode) & 0o100)
            and (stat.S_IMODE(restic_info.st_mode) & 0o022) == 0
            and stat.S_ISDIR(source_info.st_mode) and source_info.st_uid == expected_uid
            and (stat.S_IMODE(source_info.st_mode) & 0o500) == 0o500
            and (stat.S_IMODE(source_info.st_mode) & 0o022) == 0
            and stat.S_ISDIR(mount_info.st_mode)
            and stat.S_ISDIR(repository_info.st_mode) and repository_info.st_uid == expected_uid
            and (stat.S_IMODE(repository_info.st_mode) & 0o022) == 0
            and stat.S_ISDIR(config_info.st_mode) and config_info.st_uid == expected_uid
            and stat.S_IMODE(config_info.st_mode) == 0o700
            and stat.S_ISREG(password_before.st_mode) and password_before.st_uid == expected_uid
            and stat.S_IMODE(password_before.st_mode) == 0o600
            and password_before.st_nlink == 1 and 0 < password_before.st_size <= MAX_CREDENTIAL_BYTES
        ):
            raise ContractFailure("identity_bind_unavailable")
        credential_size = int(password_before.st_size)
        read_total = 0
        while read_total < credential_size:
            count = os.readv(credential_source_fd, [memoryview(credential)[read_total:credential_size]])
            if not isinstance(count, int) or count <= 0:
                raise ContractFailure("identity_bind_unavailable")
            read_total += count
        if os.readv(credential_source_fd, [memoryview(eof_probe)]) != 0:
            raise ContractFailure("identity_bind_unavailable")
        password_after = os.fstat(credential_source_fd)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (
            read_total != credential_size
            or any(getattr(password_before, key) != getattr(password_after, key) for key in stable_fields)
            or credential_size != password_after.st_size
        ):
            raise ContractFailure("identity_bind_unavailable")
        allow_sealing = getattr(os, "MFD_ALLOW_SEALING", None)
        cloexec = getattr(os, "MFD_CLOEXEC", None)
        if not isinstance(allow_sealing, int) or not isinstance(cloexec, int) or not hasattr(os, "memfd_create"):
            raise ContractFailure("identity_bind_unavailable")
        password_fd = os.memfd_create("odysseus-restic-credential", cloexec | allow_sealing)
        descriptors.append(password_fd)
        os.fchmod(password_fd, 0o400)
        view = memoryview(credential)[:credential_size]
        written = 0
        while written < len(view):
            count = os.write(password_fd, view[written:])
            if not isinstance(count, int) or count <= 0:
                raise ContractFailure("identity_bind_unavailable")
            written += count
        os.lseek(password_fd, 0, os.SEEK_SET)
        import fcntl
        seals = (
            getattr(fcntl, "F_SEAL_WRITE", 0x0008) | getattr(fcntl, "F_SEAL_GROW", 0x0004)
            | getattr(fcntl, "F_SEAL_SHRINK", 0x0002) | getattr(fcntl, "F_SEAL_SEAL", 0x0001)
        )
        add_seals, get_seals = getattr(fcntl, "F_ADD_SEALS", 1033), getattr(fcntl, "F_GET_SEALS", 1034)
        fcntl.fcntl(password_fd, add_seals, seals)
        if fcntl.fcntl(password_fd, get_seals) != seals:
            raise ContractFailure("identity_bind_unavailable")
        os.close(credential_source_fd); credential_source_fd = None
        return BoundIdentities(source_fd, mount_fd, repository_fd, config_fd, password_fd, restic_fd)
    except ContractFailure:
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except Exception:
                pass
        raise
    except Exception:
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except Exception:
                pass
        raise ContractFailure("identity_bind_unavailable") from None
    finally:
        if credential_source_fd is not None:
            try:
                os.close(credential_source_fd)
            except Exception:
                pass
        credential[:] = b"\x00" * MAX_CREDENTIAL_BYTES
        eof_probe[0] = 0


def _production_identity_release(bound: BoundIdentities) -> None:
    for descriptor in bound.owned_fds:
        try:
            os.close(descriptor)
        except Exception:
            pass


def _close_child_binding_fds(
    bound: BoundIdentities, *, closer: Callable[[int], None] = os.close,
    cloexec_setter: Callable[[int], None] | None = None,
) -> bool:
    """Remove every mount-capable bypass descriptor before Restic executes."""
    try:
        for descriptor in (bound.source_fd, bound.repository_fd, bound.password_fd):
            closer(descriptor)
        if cloexec_setter is None:
            import fcntl
            flags = fcntl.fcntl(bound.restic_fd, fcntl.F_GETFD)
            fcntl.fcntl(bound.restic_fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
        else:
            cloexec_setter(bound.restic_fd)
        return True
    except Exception:
        return False


def _write_namespace_map(path: str, value: str) -> None:
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        encoded = value.encode("ascii")
        if os.write(descriptor, encoded) != len(encoded):
            raise OSError("short namespace map write")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _enter_private_user_mount_namespace(
    *, uid: int, gid: int, unshare_call: Callable[[int], int],
    map_writer: Callable[[str, str], None] = _write_namespace_map,
    setresgid: Callable[[int, int, int], None] | None = None,
    setresuid: Callable[[int, int, int], None] | None = None,
) -> bool:
    """Enter one tightly mapped user+mount namespace without broad host privilege."""
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (uid, gid)):
        return False
    clone_newuser, clone_newns = 0x10000000, 0x00020000
    try:
        selected_setresgid = getattr(os, "setresgid") if setresgid is None else setresgid
        selected_setresuid = getattr(os, "setresuid") if setresuid is None else setresuid
        if unshare_call(clone_newuser | clone_newns) != 0:
            return False
        map_writer("/proc/self/setgroups", "deny\n")
        map_writer("/proc/self/uid_map", f"0 {uid} 1\n")
        map_writer("/proc/self/gid_map", f"0 {gid} 1\n")
        selected_setresgid(0, 0, 0)
        selected_setresuid(0, 0, 0)
        return True
    except Exception:
        return False


def _mount_namespace_setup(bound: BoundIdentities) -> Callable[[], None]:
    """Build a private fixed-name view from retained FDs in the Restic child."""
    uid = getattr(os, "geteuid", lambda: 0)()
    gid = getattr(os, "getegid", lambda: 0)()

    def setup() -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        mount = libc.mount
        mount.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p)
        mount.restype = ctypes.c_int

        def call(source: str | None, target: str, fs_type: str | None, flags: int, data: str | None = None) -> None:
            encoded_data = None if data is None else data.encode("ascii")
            if mount(
                None if source is None else source.encode("ascii"), target.encode("ascii"),
                None if fs_type is None else fs_type.encode("ascii"), flags, encoded_data,
            ) != 0:
                os._exit(125)

        if not _enter_private_user_mount_namespace(uid=uid, gid=gid, unshare_call=libc.unshare):
            os._exit(125)
        ms_rec, ms_private, ms_bind = 16384, 1 << 18, 4096
        ms_remount, ms_rdonly, ms_nodev, ms_nosuid = 32, 1, 4, 2
        call(None, "/", None, ms_rec | ms_private)
        for root in ("/opt", "/mnt", "/home"):
            call("tmpfs", root, "tmpfs", ms_nodev | ms_nosuid, "mode=0755,size=1048576")
        os.makedirs(os.path.dirname(REPOSITORY), mode=0o755, exist_ok=True)
        os.makedirs(os.path.dirname(PASSWORD_FILE), mode=0o700, exist_ok=True)
        os.mkdir(SOURCE, 0o700)
        os.mkdir(REPOSITORY, 0o700)
        credential_target = os.open(PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(credential_target)
        call(f"/proc/self/fd/{bound.source_fd}", SOURCE, None, ms_bind | ms_rec)
        call(None, SOURCE, None, ms_bind | ms_remount | ms_rdonly)
        call(f"/proc/self/fd/{bound.repository_fd}", REPOSITORY, None, ms_bind | ms_rec)
        call(f"/proc/self/fd/{bound.password_fd}", PASSWORD_FILE, None, ms_bind)
        call(None, PASSWORD_FILE, None, ms_bind | ms_remount | ms_rdonly)
        if not _close_child_binding_fds(bound):
            os._exit(125)

    return setup


def _parse_snapshot_time(value: Any) -> float:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid") from None
    if parsed.tzinfo is None:
        raise ValueError("invalid")
    return parsed.astimezone(timezone.utc).timestamp()


def _readback_snapshot(raw: Any, *, started_at: float, now: float) -> tuple[str, int]:
    if not isinstance(raw, str):
        raise ValueError("malformed")
    if len(raw.encode("utf-8")) > MAX_READBACK_BYTES:
        raise ValueError("too_large")
    try:
        snapshots = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("malformed") from None
    if not isinstance(snapshots, list) or len(snapshots) != 1:
        raise ValueError("missing" if snapshots == [] else "malformed")
    snapshot = snapshots[0]
    if not isinstance(snapshot, Mapping):
        raise ValueError("malformed")
    snapshot_id, tags, paths = snapshot.get("id"), snapshot.get("tags"), snapshot.get("paths")
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError("id")
    if not isinstance(tags, list) or any(not isinstance(tag, str) or len(tag) > 64 for tag in tags) or "odysseus-pre-update" not in tags:
        raise ValueError("invalid")
    if not isinstance(paths, list) or any(not isinstance(path, str) or len(path) > 4096 for path in paths) or SOURCE not in paths:
        raise ValueError("invalid")
    try:
        created_at = _parse_snapshot_time(snapshot.get("time"))
    except ValueError:
        raise ValueError("invalid") from None
    if created_at < started_at:
        raise ValueError("not_new")
    age = now - created_at
    if age < 0 or age > MAX_SNAPSHOT_AGE_SECONDS:
        raise ValueError("stale")
    return snapshot_id, int(age)


def _bound_command(command: tuple[str, ...], bound: BoundIdentities) -> tuple[str, ...]:
    return (f"/proc/self/fd/{bound.restic_fd}", *command[1:])


def _run_backup(runner: Callable[..., Any], bound: BoundIdentities) -> str | None:
    try:
        result = runner(_bound_command(BACKUP_COMMAND, bound), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
                        timeout=BACKUP_TIMEOUT_SECONDS, env=dict(FIXED_ENVIRONMENT), shell=False,
                        pass_fds=bound.pass_fds, preexec_fn=_mount_namespace_setup(bound), close_fds=True)
    except subprocess.TimeoutExpired:
        return "backup_timeout"
    except Exception:
        return "backup_exception"
    if not isinstance(getattr(result, "returncode", None), int):
        return "backup_result_invalid"
    return None if result.returncode == 0 else "backup_failed"


def _bounded_readback_subprocess(
    command: tuple[str, ...], *, bound: BoundIdentities, timeout: int, maximum_stdout: int,
    popen: Callable[..., Any] = subprocess.Popen, wait_for_read: Callable[..., Any] = select.select,
    reader: Callable[[int, int], bytes] = os.read, monotonic: Callable[[], float] = time.monotonic,
) -> Any:
    process: Any = None
    stdout: Any = None
    output = bytearray()
    deadline = monotonic() + timeout
    reaped = False

    def terminate() -> None:
        nonlocal reaped
        if process is None:
            return
        try: process.kill()
        except Exception: pass
        try: process.wait()
        except Exception: pass
        reaped = True

    try:
        process = popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=False, env=dict(FIXED_ENVIRONMENT), shell=False, pass_fds=bound.pass_fds,
            preexec_fn=_mount_namespace_setup(bound), close_fds=True,
        )
        stdout = getattr(process, "stdout", None)
        if stdout is None or not callable(getattr(stdout, "fileno", None)):
            raise OSError("invalid pipe")
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            ready, _, _ = wait_for_read([stdout], [], [], remaining)
            if not ready:
                raise subprocess.TimeoutExpired(command, timeout)
            capacity = min(4096, maximum_stdout + 1 - len(output))
            chunk = reader(stdout.fileno(), capacity)
            if not isinstance(chunk, bytes):
                raise OSError("invalid pipe read")
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > maximum_stdout:
                terminate()
                result = subprocess.CompletedProcess(command, -1, stdout=b"")
                result.stdout_oversized = True
                return result
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        returncode = process.wait(timeout=remaining)
        reaped = True
        result = subprocess.CompletedProcess(command, returncode, stdout=bytes(output).decode("utf-8"))
        result.stdout_oversized = False
        return result
    except subprocess.TimeoutExpired:
        if not reaped:
            terminate()
        raise
    except Exception:
        terminate()
        raise
    finally:
        if stdout is not None:
            try: stdout.close()
            except Exception: pass


def _run_readback(runner: Callable[..., Any], bound: BoundIdentities, *, started_at: float, now: float) -> tuple[str, int] | str:
    try:
        command = _bound_command(READBACK_COMMAND, bound)
        result = (
            _bounded_readback_subprocess(
                command, bound=bound, timeout=READBACK_TIMEOUT_SECONDS, maximum_stdout=MAX_READBACK_BYTES,
            )
            if runner is subprocess.run
            else runner(
                command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                timeout=READBACK_TIMEOUT_SECONDS, env=dict(FIXED_ENVIRONMENT), shell=False,
                pass_fds=bound.pass_fds, preexec_fn=_mount_namespace_setup(bound), close_fds=True,
            )
        )
    except subprocess.TimeoutExpired:
        return "readback_timeout"
    except Exception:
        return "readback_exception"
    if getattr(result, "stdout_oversized", False) is True:
        return "readback_output_too_large"
    if getattr(result, "returncode", None) != 0:
        return "readback_failed"
    try:
        return _readback_snapshot(getattr(result, "stdout", None), started_at=started_at, now=now)
    except ValueError as exc:
        return {
            "too_large": "readback_output_too_large", "missing": "snapshot_missing",
            "id": "snapshot_id_invalid", "not_new": "snapshot_not_new", "stale": "snapshot_stale",
        }.get(str(exc), "readback_malformed")


def _ok(snapshot_id: str, age: int, *, action_provenance_ref: str) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID, "status": "ok",
        "repository_identity": "restic_homeserver_backup_v1",
        "protected_source_identity": "odysseus_protected_source_v1",
        "backup_effect": "created", "action_provenance_ref": action_provenance_ref,
        "snapshot_id": snapshot_id, "source_included": True,
        "snapshot_created_after_start": True, "snapshot_age_seconds": age, "snapshot_fresh": True,
        "concurrent_lock_held": True, "partial_snapshot_detected": False,
        "raw_stdout_visible": False, "raw_stderr_visible": False, "exception_text_visible": False,
        "environment_visible": False, "file_contents_visible": False, "paths_visible": False,
        "hostnames_visible": False, "secret_values_visible": False,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def validate_envelope(value: Any) -> bool:
    """Accept only one fully redacted creation result from the fixed runner."""
    if type(value) is not dict or type(value.get("status")) is not str:
        return False
    status = value["status"]
    if status == "blocked":
        return bool(
            set(value) == _BLOCKED_KEYS
            and value.get("error_code") in _BLOCKED_ERRORS
            and value.get("backup_invoked") is False
            and value.get("retry_permitted") is False
            and type(value.get("evidence_sha256")) is str
            and _SNAPSHOT_ID.fullmatch(value["evidence_sha256"])
            and _digest(value) == value["evidence_sha256"]
        )
    if status == "unknown":
        return bool(
            set(value) == _UNKNOWN_KEYS
            and value.get("error_code") in _UNKNOWN_ERRORS
            and value.get("effect_may_have_occurred") is True
            and value.get("retry_permitted") is False
            and value.get("manual_recovery_required") is True
            and type(value.get("action_provenance_ref")) is str
            and _PROVENANCE_REF.fullmatch(value["action_provenance_ref"])
            and type(value.get("evidence_sha256")) is str
            and _SNAPSHOT_ID.fullmatch(value["evidence_sha256"])
            and _digest(value) == value["evidence_sha256"]
        )
    if status != "ok" or set(value) != _OK_KEYS:
        return False
    required = {
        "repository_identity": "restic_homeserver_backup_v1", "protected_source_identity": "odysseus_protected_source_v1",
        "backup_effect": "created", "source_included": True, "snapshot_created_after_start": True,
        "snapshot_fresh": True, "concurrent_lock_held": True, "partial_snapshot_detected": False,
    }
    visible = {key for key in _OK_KEYS if key.endswith("_visible")}
    return bool(
        all(value.get(key) == expected for key, expected in required.items())
        and type(value.get("snapshot_id")) is str
        and _SNAPSHOT_ID.fullmatch(value["snapshot_id"])
        and type(value.get("snapshot_age_seconds")) is int
        and 0 <= value["snapshot_age_seconds"] <= MAX_SNAPSHOT_AGE_SECONDS
        and type(value.get("action_provenance_ref")) is str
        and _PROVENANCE_REF.fullmatch(value["action_provenance_ref"])
        and all(value.get(key) is False for key in visible)
        and type(value.get("evidence_sha256")) is str
        and _SNAPSHOT_ID.fullmatch(value["evidence_sha256"])
        and _digest(value) == value["evidence_sha256"]
    )


def collect_predeploy_backup_creation(
    *, runner: Callable[..., Any] = subprocess.run, lstat: Callable[[str], Any] = os.lstat,
    mount_checker: Callable[[str], bool] = _production_mount_checker,
    owner_lookup: Callable[[str], Any] = _production_owner_lookup,
    config_reader: Callable[[], str] = _read_fixed_config,
    process_environment: Mapping[str, Any] | None = None,
    lock_acquire: Callable[[int], Any] = _production_lock_acquire,
    lock_release: Callable[[Any], None] = _production_lock_release,
    identity_bind: Callable[[int], BoundIdentities] = _production_identity_bind,
    identity_release: Callable[[BoundIdentities], None] = _production_identity_release,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run the one-shot fixed contract; all dependencies are injectable for offline tests."""
    lock_handle: Any = None
    bound: BoundIdentities | None = None
    invoked = False
    action_provenance_ref: str | None = None
    try:
        _validate_process_environment(os.environ if process_environment is None else process_environment)
        expected_uid = _expected_uid(owner_lookup)
        if not _safe_binary(lstat=lstat):
            return blocked("restic_unavailable")
        if not _safe_directory(SOURCE, expected_uid=expected_uid, lstat=lstat, required_permissions=0o500):
            return blocked("source_path_missing")
        if not _safe_directory(REPOSITORY, expected_uid=expected_uid, lstat=lstat):
            return blocked("repository_unsafe")
        if not _safe_regular(CONFIG_PATH, expected_uid=expected_uid, lstat=lstat, exact_mode=0o600):
            return blocked("config_invalid")
        if not _safe_regular(PASSWORD_FILE, expected_uid=expected_uid, lstat=lstat, exact_mode=0o600):
            return blocked("password_file_unsafe")
        if not _safe_directory(LOCK_PARENT, expected_uid=expected_uid, lstat=lstat, required_permissions=0o700):
            return blocked("lock_unavailable")
        _validate_config(config_reader())
        if not bool(mount_checker(BACKUP_MOUNT)):
            return blocked("mount_unavailable")
        lock_handle = lock_acquire(expected_uid)
        bound = identity_bind(expected_uid)
        # This is deliberately the final operation before dispatch, so its
        # provenance cannot be confused with preflight time.
        started_value = clock()
        if isinstance(started_value, bool) or not isinstance(started_value, (int, float)):
            raise ContractFailure("internal_error")
        started_at = float(started_value)
        if not math.isfinite(started_at) or started_at < 0:
            raise ContractFailure("internal_error")
        action_provenance_ref = _action_provenance_ref(started_at)
        invoked = True
        backup_problem = _run_backup(runner, bound)
        if backup_problem is not None:
            return unknown(backup_problem, action_provenance_ref=action_provenance_ref)
        readback = _run_readback(runner, bound, started_at=started_at, now=float(clock()))
        if isinstance(readback, str):
            return unknown(readback, action_provenance_ref=action_provenance_ref)
        return _ok(*readback, action_provenance_ref=action_provenance_ref)
    except ContractFailure as failure:
        if not invoked:
            return blocked(failure.code)
        assert action_provenance_ref is not None
        return unknown("internal_error", action_provenance_ref=action_provenance_ref)
    except Exception:
        if not invoked:
            return blocked("internal_error")
        assert action_provenance_ref is not None
        return unknown("internal_error", action_provenance_ref=action_provenance_ref)
    finally:
        if bound is not None:
            try:
                identity_release(bound)
            except Exception:
                pass
        if lock_handle is not None:
            lock_release(lock_handle)


def main() -> int:
    payload = collect_predeploy_backup_creation()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

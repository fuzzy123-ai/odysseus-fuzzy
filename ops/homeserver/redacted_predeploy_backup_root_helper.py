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
VIEW_ROOT = "/run/odysseus-predeploy-backup-root-helper/view"
VIEW_CREDENTIAL = VIEW_ROOT + "/credential/restic-password"
BACKUP_TIMEOUT_SECONDS = 1800
READBACK_TIMEOUT_SECONDS = 20
MAX_OUTPUT_BYTES = 65536
MAX_CREDENTIAL_BYTES = 16384
MAX_ARM_BYTES = 1024
MAX_ARM_FUTURE_SECONDS = 600
CLONE_NEWNS = 0x00020000
MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_REMOUNT, MS_BIND, MS_REC, MS_PRIVATE = 32, 4096, 16384, 1 << 18
AT_EMPTY_PATH, AT_FDCWD = 0x1000, -100
OPEN_TREE_CLONE, OPEN_TREE_CLOEXEC, OPEN_TREE_RECURSIVE = 1, 0x80000, 0x8000
MOVE_MOUNT_F_EMPTY_PATH = 0x00000004
EXECVEAT_EMPTY_PATH = 0x1000
_HEX = __import__("re").compile(r"^[0-9a-f]{64}$")
_BLOCKED = frozenset({"not_armed", "arm_invalid", "arm_expired", "arm_replayed", "arm_contended", "identity_unavailable", "preflight_failed"})
_UNKNOWN = frozenset({"backup_timeout", "backup_failed", "readback_timeout", "readback_failed", "readback_invalid", "execution_ambiguous"})
_OK_KEYS = frozenset({"schema_id", "status", "repository_identity", "protected_source_identity", "backup_effect", "action_provenance_ref", "snapshot_id", "source_included", "snapshot_created_after_start", "snapshot_age_seconds", "snapshot_fresh", "concurrent_lock_held", "partial_snapshot_detected", "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "evidence_sha256"})
_BLOCKED_KEYS = frozenset({"schema_id", "status", "error_code", "backup_invoked", "retry_permitted", "evidence_sha256"})
_UNKNOWN_KEYS = frozenset({"schema_id", "status", "error_code", "effect_may_have_occurred", "retry_permitted", "manual_recovery_required", "action_provenance_ref", "evidence_sha256"})


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
    if type(value) is not dict or not isinstance(value.get("evidence_sha256"), str) or value["evidence_sha256"] != _digest(value):
        return False
    if value.get("status") == "blocked":
        return set(value) == _BLOCKED_KEYS and value.get("error_code") in _BLOCKED and value.get("backup_invoked") is False and value.get("retry_permitted") is False
    if value.get("status") == "unknown":
        return set(value) == _UNKNOWN_KEYS and value.get("error_code") in _UNKNOWN and value.get("effect_may_have_occurred") is True and value.get("retry_permitted") is False and value.get("manual_recovery_required") is True and isinstance(value.get("action_provenance_ref"), str) and __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", value["action_provenance_ref"])
    if value.get("status") != "ok" or set(value) != _OK_KEYS:
        return False
    return bool(value.get("repository_identity") == "restic_homeserver_backup_v1" and value.get("protected_source_identity") == "odysseus_protected_source_v1" and value.get("backup_effect") == "created" and isinstance(value.get("snapshot_id"), str) and _HEX.fullmatch(value["snapshot_id"]) and isinstance(value.get("snapshot_age_seconds"), int) and 0 <= value["snapshot_age_seconds"] <= BACKUP_TIMEOUT_SECONDS + READBACK_TIMEOUT_SECONDS and all(value.get(k) is False for k in _OK_KEYS if k.endswith("_visible")) and value.get("source_included") is True and value.get("snapshot_created_after_start") is True and value.get("snapshot_fresh") is True and value.get("concurrent_lock_held") is True and value.get("partial_snapshot_detected") is False)


@dataclass(frozen=True)
class Bound:
    source_fd: int
    repository_fd: int
    credential_fd: int
    restic_fd: int
    uid: int
    gid: int
    grant_ref: str


class Failure(Exception):
    def __init__(self, code: str) -> None: self.code = code


def _safe_dir(info: Any, uid: int | None = None, mode: int | None = None) -> bool:
    try:
        return stat.S_ISDIR(info.st_mode) and (uid is None or info.st_uid == uid) and (mode is None or stat.S_IMODE(info.st_mode) == mode) and info.st_nlink >= 1
    except Exception: return False


def _safe_file(info: Any, uid: int, mode: int, maximum: int | None = None) -> bool:
    try:
        return stat.S_ISREG(info.st_mode) and info.st_uid == uid and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1 and (maximum is None or 0 < info.st_size <= maximum)
    except Exception: return False


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
    source = repository = credential_source = restic = credential_fd = mount = config = raw = None
    success = False
    try:
        import pwd
        owner = pwd.getpwnam(OWNER); uid, gid = int(owner.pw_uid), int(owner.pw_gid)
        source = _open_components(SOURCE)
        mount = _open_components(BACKUP_MOUNT)
        _prove_fixed_mount(mount)
        repository = os.open("restic", getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=mount)
        next_repository = os.open("homeserver", getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=repository); os.close(repository); repository = next_repository
        config = _open_components(os.path.dirname(PASSWORD_FILE))
        credential_source = os.open(os.path.basename(PASSWORD_FILE), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=config); config = _close_owned_descriptor(config)
        restic = os.open(RESTIC_BINARY, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        if not (_safe_dir(os.fstat(source), uid) and _safe_dir(os.fstat(repository), uid) and _safe_file(os.fstat(credential_source), uid, 0o600, MAX_CREDENTIAL_BYTES)):
            raise Failure("identity_unavailable")
        rinfo = os.fstat(restic)
        if not (stat.S_ISREG(rinfo.st_mode) and rinfo.st_uid == 0 and stat.S_IMODE(rinfo.st_mode) & 0o100 and not stat.S_IMODE(rinfo.st_mode) & 0o022): raise Failure("identity_unavailable")
        before = os.fstat(credential_source); raw = _read_exact(credential_source, before.st_size); after = os.fstat(credential_source)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns): raise Failure("identity_unavailable")
        credential_fd = _seal_credential(raw)
        os.close(credential_source); credential_source = None
        result = Bound(source, repository, credential_fd, restic, uid, gid, "")
        success = True
        return result
    except Failure: raise
    except Exception: raise Failure("identity_unavailable") from None
    finally:
        if isinstance(raw, bytearray):
            raw[:] = b"\x00" * len(raw)
        if not success:
            for fd in (source, repository, credential_fd, restic):
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


def _syscalls() -> tuple[Callable[..., int], int, int, int] | None:
    if os.name != "posix" or __import__("platform").machine().lower() not in {"x86_64", "amd64"}: return None
    libc = ctypes.CDLL(None, use_errno=True)
    return libc.syscall, 428, 429, 322  # open_tree, move_mount, execveat on x86_64


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


def _mount_setup(bound: Bound, *, syscall: Callable[..., int] | None = None, mount_call: Callable[..., int] | None = None) -> None:
    native = _syscalls()
    if native is None: raise Failure("preflight_failed")
    call, open_tree_nr, move_mount_nr, _ = native
    if syscall is not None: call = syscall
    libc = ctypes.CDLL(None, use_errno=True)
    mount = libc.mount if mount_call is None else mount_call
    def invoke_mount(source: bytes | None, target: str, fs: bytes | None, flags: int, data: bytes | None = None) -> None:
        result = mount(source, target.encode("ascii"), fs, flags, data)
        if result != 0: raise Failure("preflight_failed")
    if libc.unshare(CLONE_NEWNS) != 0: raise Failure("preflight_failed")
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
    # transferred to that exact uid and remains owner-only.  The previous
    # 0700/root layout made every Restic invocation fail after the uid drop.
    invoke_mount(b"tmpfs", VIEW_ROOT, b"tmpfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, b"mode=0711,size=1048576")
    credential_directory = os.path.dirname(VIEW_CREDENTIAL)
    os.mkdir(credential_directory, 0o700)
    credential_directory_fd = None
    try:
        credential_directory_fd = os.open(credential_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        os.fchown(credential_directory_fd, bound.uid, bound.gid); os.fchmod(credential_directory_fd, 0o700)
        credential_directory_info = os.fstat(credential_directory_fd)
        if not (_safe_dir(credential_directory_info, bound.uid, 0o700) and credential_directory_info.st_gid == bound.gid): raise Failure("preflight_failed")
    except Failure:
        raise
    except Exception:
        raise Failure("preflight_failed") from None
    finally:
        if isinstance(credential_directory_fd, int): os.close(credential_directory_fd)
    def move(fd: int, target: str) -> None:
        tree = call(open_tree_nr, fd, ctypes.c_char_p(b""), AT_EMPTY_PATH | OPEN_TREE_CLONE | OPEN_TREE_CLOEXEC | OPEN_TREE_RECURSIVE)
        if not isinstance(tree, int) or tree < 0: raise Failure("preflight_failed")
        try:
            if call(move_mount_nr, tree, ctypes.c_char_p(b""), AT_FDCWD, ctypes.c_char_p(target.encode("ascii")), MOVE_MOUNT_F_EMPTY_PATH) != 0: raise Failure("preflight_failed")
        finally: os.close(tree)
    # Attach retained descriptor identities at their canonical paths inside
    # this child-only mount namespace.  Restic consequently records
    # ``/opt/odysseus`` rather than an internal staging pathname, preserving
    # the independent snapshot-observation contract.
    move(bound.source_fd, SOURCE); move(bound.repository_fd, REPOSITORY)
    source_before, source_after = os.fstat(bound.source_fd), os.stat(SOURCE)
    repository_before, repository_after = os.fstat(bound.repository_fd), os.stat(REPOSITORY)
    if (source_before.st_dev, source_before.st_ino) != (source_after.st_dev, source_after.st_ino) or (repository_before.st_dev, repository_before.st_ino) != (repository_after.st_dev, repository_after.st_ino): raise Failure("preflight_failed")
    if _filesystem_readonly(os.statvfs(REPOSITORY)): raise Failure("preflight_failed")
    invoke_mount(None, SOURCE, None, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC)
    if not _filesystem_readonly(os.statvfs(SOURCE)): raise Failure("preflight_failed")
    raw = _read_credential_from_start(bound.credential_fd, os.fstat(bound.credential_fd).st_size)
    fd = None
    try:
        fd = os.open(VIEW_CREDENTIAL, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        offset = 0
        while offset < len(raw):
            count = os.write(fd, memoryview(raw)[offset:])
            if not isinstance(count, int) or count <= 0: raise OSError()
            offset += count
        os.fsync(fd); os.fchown(fd, bound.uid, bound.gid)
    finally:
        raw[:] = b"\x00" * len(raw)
        if isinstance(fd, int): os.close(fd)
    invoke_mount(None, credential_directory, None, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC)
    if not _filesystem_readonly(os.statvfs(credential_directory)): raise Failure("preflight_failed")


def _filesystem_readonly(info: Any) -> bool:
    return bool(getattr(info, "f_flag", 0) & getattr(os, "ST_RDONLY", 1))


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
    directory = fd = used = None
    try:
        directory = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        if not _safe_dir(os.fstat(directory), 0, 0o700): raise Failure("arm_invalid")
        fd = os.open(ARM_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        info = os.fstat(fd)
        if not _safe_file(info, 0, 0o600, MAX_ARM_BYTES): raise Failure("arm_invalid")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = json.loads(_read_exact(fd, info.st_size).decode("ascii"))
        grant_id = _validate_arm_record(record, now, helper_sha256)
        used_name = USED_PREFIX + grant_id
        used = os.open(used_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        written = 0
        while written < 5:
            amount = os.write(used, b"used\n"[written:])
            if not isinstance(amount, int) or amount <= 0: raise OSError()
            written += amount
        os.fsync(used); os.fsync(directory)
        return _arm_reference(grant_id, helper_sha256)
    except BlockingIOError: raise Failure("arm_contended") from None
    except FileExistsError: raise Failure("arm_replayed") from None
    except Failure: raise
    except Exception: raise Failure("not_armed") from None
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


def _invalidate_public_receipt() -> None:
    directory = None
    try:
        directory = os.open(os.path.dirname(PUBLIC_RECEIPT_PATH), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        if _safe_dir(os.fstat(directory), 0, 0o755):
            try: os.unlink(os.path.basename(PUBLIC_RECEIPT_PATH), dir_fd=directory)
            except FileNotFoundError: pass
            os.fsync(directory)
    except Exception: raise Failure("preflight_failed") from None
    finally:
        if isinstance(directory, int):
            try: os.close(directory)
            except Exception: pass


def _run_child(bound: Bound, command: tuple[str, ...], timeout: int, capture: bool) -> tuple[int | None, bytes, bool]:
    read_fd = write_fd = None
    try:
        if capture: read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                if capture:
                    os.close(read_fd); os.dup2(write_fd, 1); os.close(write_fd)
                else:
                    null = os.open(os.devnull, os.O_WRONLY); os.dup2(null, 1); os.close(null)
                null = os.open(os.devnull, os.O_WRONLY); os.dup2(null, 2); os.close(null)
                _mount_setup(bound)
                for descriptor in (bound.source_fd, bound.repository_fd, bound.credential_fd): _close_verified(descriptor)
                _drop_identity(bound)
                native = _syscalls()
                assert native is not None
                argv = (ctypes.c_char_p * (len(command) + 1))(*[part.encode("ascii") for part in command], None)
                env = (ctypes.c_char_p * 3)(b"PATH=/usr/bin:/bin", ("RESTIC_PASSWORD_FILE=" + VIEW_CREDENTIAL).encode("ascii"), None)
                native[0](native[3], bound.restic_fd, ctypes.c_char_p(b""), argv, env, EXECVEAT_EMPTY_PATH)
            except Exception: pass
            os._exit(125)
        if capture: os.close(write_fd); write_fd = None
        output = bytearray(); deadline = time.monotonic() + timeout
        while capture and time.monotonic() < deadline:
            ready, _, _ = select.select([read_fd], [], [], max(0, deadline - time.monotonic()))
            if not ready: break
            piece = os.read(read_fd, min(4096, MAX_OUTPUT_BYTES + 1 - len(output)))
            if not piece: break
            output.extend(piece)
            if len(output) > MAX_OUTPUT_BYTES:
                os.kill(pid, 9); os.waitpid(pid, 0); return None, b"", True
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done: return (os.waitstatus_to_exitcode(status), bytes(output), False)
            if time.monotonic() >= deadline:
                os.kill(pid, 9); os.waitpid(pid, 0); return None, b"", False
            time.sleep(0.01)
    finally:
        for item in (read_fd, write_fd):
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


def _execute_under_lock(digest: str, now: Callable[[], float]) -> dict[str, Any]:
    bound: Bound | None = None
    reference: str | None = None
    try:
        started = float(now())
        reference = _consume_arm(started, digest)
        _invalidate_public_receipt()
        bound = _bind_identities()
        bound = Bound(bound.source_fd, bound.repository_fd, bound.credential_fd, bound.restic_fd, bound.uid, bound.gid, reference)
        backup = _run_child(bound, (RESTIC_BINARY, "-r", REPOSITORY, "backup", SOURCE, "--exclude", "**/.git", "--exclude", "**/__pycache__", "--exclude", "**/.pytest_cache", "--exclude", "**/node_modules", "--exclude", "**/backups", "--exclude", "**/logs/*.log", "--exclude", "**/tmp", "--exclude", "**/.cache", "--exclude-caches", "--tag", "homeserver", "--tag", "pre-update", "--tag", "odysseus-pre-update"), BACKUP_TIMEOUT_SECONDS, False)
        if backup[0] is None: return _unknown("backup_timeout", reference)
        if backup[0] != 0: return _unknown("backup_failed", reference)
        result, raw, overflow = _run_child(bound, (RESTIC_BINARY, "-r", REPOSITORY, "--no-lock", "snapshots", "--tag", "odysseus-pre-update", "--latest", "1", "--json"), READBACK_TIMEOUT_SECONDS, True)
        snapshot = None if overflow or result != 0 else _parse_snapshot(raw, started)
        if result is None: return _unknown("readback_timeout", reference)
        if snapshot is None: return _unknown("readback_invalid" if result == 0 else "readback_failed", reference)
        value = {"schema_id": SCHEMA_ID, "status": "ok", "repository_identity": "restic_homeserver_backup_v1", "protected_source_identity": "odysseus_protected_source_v1", "backup_effect": "created", "action_provenance_ref": reference, "snapshot_id": snapshot[0], "source_included": True, "snapshot_created_after_start": True, "snapshot_age_seconds": snapshot[1], "snapshot_fresh": True, "concurrent_lock_held": True, "partial_snapshot_detected": False, "raw_stdout_visible": False, "raw_stderr_visible": False, "exception_text_visible": False, "environment_visible": False, "file_contents_visible": False, "paths_visible": False, "hostnames_visible": False, "secret_values_visible": False}
        value["evidence_sha256"] = _digest(value)
        return value
    except Failure as failure:
        return _blocked(failure.code) if reference is None else _unknown("execution_ambiguous", reference)
    except Exception:
        return _blocked("preflight_failed") if reference is None else _unknown("execution_ambiguous", reference)
    finally:
        if bound is not None:
            for descriptor in (bound.source_fd, bound.repository_fd, bound.credential_fd, bound.restic_fd):
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
        value = _execute_under_lock(digest, now)
        if publish and not _write_public_receipt(value):
            reference = value.get("action_provenance_ref")
            return _unknown("execution_ambiguous", reference) if isinstance(reference, str) else _blocked("preflight_failed")
        return value
    except Failure as failure:
        return _blocked(failure.code)
    finally:
        if isinstance(lock_fd, int):
            try: os.close(lock_fd)
            except Exception: pass


def _write_public_receipt(value: Mapping[str, Any]) -> bool:
    """Publish only an already validated redacted envelope, atomically.

    The runtime directory is created by systemd.  It is intentionally not a
    caller-selected path and never receives stdout, stderr or exception text.
    """
    if not validate_envelope(value): return False
    directory = descriptor = None
    temporary = ".receipt.tmp"
    try:
        directory = os.open(os.path.dirname(PUBLIC_RECEIPT_PATH), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(directory)
        if not _safe_dir(info, 0, 0o755): return False
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=directory)
        raw = json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        offset = 0
        while offset < len(raw):
            count = os.write(descriptor, raw[offset:])
            if not isinstance(count, int) or count <= 0: return False
            offset += count
        os.fsync(descriptor); os.close(descriptor); descriptor = None
        os.replace(temporary, os.path.basename(PUBLIC_RECEIPT_PATH), src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        return True
    except Exception:
        return False
    finally:
        if isinstance(descriptor, int):
            try: os.close(descriptor)
            except Exception: pass
        if isinstance(directory, int):
            try: os.close(directory)
            except Exception: pass


def main() -> int:
    value = run_root_helper(publish=True)
    # The service has StandardOutput=null.  This exists only for a local root
    # console; it remains one canonical redacted line.
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if value.get("status") == "ok" else 1


if __name__ == "__main__": raise SystemExit(main())

#!/usr/bin/env python3
"""Root-only, no-clobber successor replacement for the backup helper.

This module is a preparation packet.  Its normal import and entry point are
inert; a future separately authorised host action may call ``replace`` with a
descriptor rooted operation adapter.  Every result is a fixed redacted
envelope and an ambiguous publication is never retried automatically.
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover - the host target is Debian/Linux
    fcntl = None  # type: ignore[assignment]
import hashlib
import json
import os
import select
import stat
import subprocess
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Mapping

SCHEMA_ID = "odysseus.predeploy_backup_root_helper_replace.v1"
HELPER_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper.py"
BACKUP_PATH = HELPER_PATH + ".backup"
STATE_DIR = "/var/lib/odysseus-predeploy-backup-root-helper"
LIFECYCLE_LOCK = STATE_DIR + "/lifecycle.lock"
EXECUTION_LOCK = STATE_DIR + "/execution.lock"
UNIT_PATH = "/etc/systemd/system/odysseus-predeploy-backup-root-helper.service"
SUDOERS_PATH = "/etc/sudoers.d/odysseus-predeploy-backup-root-helper"
ARM_PATH = STATE_DIR + "/arm.json"
OLD_HELPER_SHA256 = "abf8f859384a9ab21d2c5fb682aabaaff522464eef5d035126065021de373d31"
NEW_HELPER_SHA256 = "f5e6f90169c59daee07dbb82880912b8c959d2bf991748e3363b6dbbc7b57952"
READBACK_SHA256 = "e647b6f1faa409f42cbeb80c74826b730695d9e0fad14cf47aa22c1a59a0a046"
UNIT_SHA256 = "466de2f889a00ee2759bd06380ddb213f8c0f4cee5644e3a2e27083863c1ab98"
SUDOERS_SHA256 = "1a6a7f1ec4d328c9fed20b758a87bb9905b68145122f6751fd5eeea748d5847d"
MAX_BYTES = 400_000
_HEX = __import__("re").compile(r"^[0-9a-f]{64}$")
_CODES = frozenset({"none", "execution_disabled", "invalid_source", "preflight_failed", "lock_busy", "backup_conflict", "write_failed", "publish_ambiguous", "postflight_failed", "rollback_failed", "foreign_target"})
_KEYS = frozenset({"schema_id", "status", "error_code", "backup_created", "replacement_published", "postflight_ok", "rollback_attempted", "rollback_succeeded", "effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


ASSET_PINS = {"helper": OLD_HELPER_SHA256, "readback": READBACK_SHA256, "unit": UNIT_SHA256, "sudoers": SUDOERS_SHA256}
ASSET_PATHS = ((HELPER_PATH, OLD_HELPER_SHA256, 0o700), ("/usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py", READBACK_SHA256, 0o700), (UNIT_PATH, UNIT_SHA256, 0o644), (SUDOERS_PATH, SUDOERS_SHA256, 0o440))
UNIT = "odysseus-predeploy-backup-root-helper.service"
UNIT_STATE_COMMAND = ("/usr/bin/systemctl", "show", "--no-pager", "--property=LoadState", "--property=ActiveState", "--property=SubState", "--property=UnitFileState", UNIT)


def envelope(status: str, code: str, *, backup: bool = False, published: bool = False, postflight: bool = False, rollback_attempted: bool = False, rollback_succeeded: bool = False, effect: bool = False, recovery: bool = False) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": status if status in {"applied", "blocked", "rolled_back", "unknown"} else "unknown", "error_code": code if code in _CODES else "preflight_failed", "backup_created": backup is True, "replacement_published": published is True, "postflight_ok": postflight is True, "rollback_attempted": rollback_attempted is True, "rollback_succeeded": rollback_succeeded is True, "effect_may_have_occurred": effect is True, "manual_recovery_required": recovery is True, "retry_permitted": False, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    try:
        if type(value) is not dict or set(value) != _KEYS or value.get("schema_id") != SCHEMA_ID or type(value.get("status")) is not str or type(value.get("error_code")) is not str or value.get("retry_permitted") is not False or any(value.get(k) is not False for k in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) or value.get("evidence_sha256") != _digest(value): return False
    except Exception: return False
    if value["error_code"] not in _CODES: return False
    if any(type(value.get(key)) is not bool for key in ("backup_created", "replacement_published", "postflight_ok", "rollback_attempted", "rollback_succeeded", "effect_may_have_occurred", "manual_recovery_required")): return False
    status, code = value.get("status"), value.get("error_code")
    if status == "blocked": return code in {"execution_disabled", "invalid_source", "preflight_failed", "lock_busy", "backup_conflict", "foreign_target", "write_failed"} and not value["effect_may_have_occurred"] and not value["manual_recovery_required"] and not any(value[k] for k in ("backup_created", "replacement_published", "postflight_ok", "rollback_attempted", "rollback_succeeded"))
    if status == "applied": return code == "none" and all(value[k] for k in ("backup_created", "replacement_published", "postflight_ok", "effect_may_have_occurred")) and not value["rollback_attempted"] and not value["rollback_succeeded"] and not value["manual_recovery_required"]
    if status == "rolled_back": return code in {"write_failed", "publish_ambiguous", "postflight_failed"} and value["backup_created"] and value["replacement_published"] and not value["postflight_ok"] and value["rollback_attempted"] and value["rollback_succeeded"] and value["effect_may_have_occurred"] and not value["manual_recovery_required"]
    if status != "unknown" or code not in {"write_failed", "publish_ambiguous", "rollback_failed", "foreign_target"} or not value["effect_may_have_occurred"] or not value["manual_recovery_required"] or value["postflight_ok"]: return False
    if code == "rollback_failed": return value["rollback_attempted"] and not value["rollback_succeeded"] and value["backup_created"] and value["replacement_published"]
    if value["rollback_attempted"] or value["rollback_succeeded"]: return False
    if code == "publish_ambiguous": return value["backup_created"] and value["replacement_published"]
    if code == "foreign_target": return value["backup_created"]
    return not value["replacement_published"]


def _safe_file(info: Any, mode: int, digest: str | None = None, *, uid: int = 0, gid: int = 0) -> bool:
    return bool(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_gid == gid and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1 and 0 < info.st_size <= MAX_BYTES)


def _asset_valid(path: str, expected: str, mode: int, *, api: Any = os, root_fd: int | None = None, uid: int = 0, gid: int = 0) -> bool:
    parent = fd = None
    try:
        parent, name = _open_parent(path, api=api, root_fd=root_fd, uid=uid, gid=gid)
        fd = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        info = api.fstat(fd)
        if not _safe_file(info, mode, uid=uid, gid=gid): return False
        digest, (_, after) = _read_hash(fd, api=api)
        return digest == expected and _same_identity(info, after)
    except Exception: return False
    finally:
        for item in (fd, parent):
            if isinstance(item, int):
                try: api.close(item)
                except Exception: pass


def _safe_dir(info: Any, *, uid: int = 0, gid: int = 0) -> bool:
    return bool(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o700 and info.st_nlink >= 1)


def _run_unit_state(*_args: Any, **_kwargs: Any) -> Any:
    process = None
    output = bytearray()
    try:
        process = subprocess.Popen(UNIT_STATE_COMMAND, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False, close_fds=True)
        if process.stdout is None: raise OSError("missing output pipe")
        monotonic = __import__("time").monotonic
        deadline = monotonic() + 5.0
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0: raise TimeoutError("unit probe timeout")
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            if not ready: raise TimeoutError("unit probe timeout")
            chunk = os.read(process.stdout.fileno(), min(64, 257 - len(output)))
            if not chunk: break
            output.extend(chunk)
            if len(output) > 256: raise OSError("unit probe too large")
        returncode = process.wait(timeout=max(0.01, deadline - monotonic()))
        return SimpleNamespace(returncode=returncode, stdout=bytes(output))
    except Exception:
        if process is not None:
            try: process.kill()
            except Exception: pass
            try: process.wait(timeout=1)
            except Exception: pass
        raise
    finally:
        if process is not None and process.stdout is not None:
            try: process.stdout.close()
            except Exception: pass


def _open_parent(path: str, *, api: Any = os, root_fd: int | None = None, uid: int = 0, gid: int = 0) -> tuple[int, str]:
    parts = path.split("/")[1:]
    if not path.startswith("/") or not parts or any(not p or p in {".", ".."} for p in parts): raise OSError("unsafe path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current = api.open("/", flags) if root_fd is None else api.dup(root_fd)
    try:
        root_info = api.fstat(current)
        if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != uid or root_info.st_gid != gid or stat.S_IMODE(root_info.st_mode) & 0o022: raise OSError("unsafe root")
        for part in parts[:-1]:
            nxt = api.open(part, flags, dir_fd=current); api.close(current); current = nxt
            info = api.fstat(current)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_gid != gid or stat.S_IMODE(info.st_mode) & 0o022: raise OSError("unsafe parent")
        return current, parts[-1]
    except Exception:
        try: api.close(current)
        except Exception: pass
        raise


def _read_hash(fd: int, *, api: Any = os) -> tuple[str, Any]:
    before = api.fstat(fd); hasher = hashlib.sha256(); remaining = before.st_size
    if remaining <= 0 or remaining > MAX_BYTES: raise OSError("invalid size")
    api.lseek(fd, 0, os.SEEK_SET)
    while remaining:
        piece = api.read(fd, min(8192, remaining))
        if not piece or len(piece) > remaining: raise OSError("short read")
        hasher.update(piece); remaining -= len(piece)
    if api.read(fd, 1): raise OSError("file grew")
    after = api.fstat(fd)
    if not _same_identity(before, after): raise OSError("file changed")
    identity = tuple(getattr(before, k, None) for k in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))
    return hasher.hexdigest(), (identity, after)


def _same_identity(before: Any, after: Any) -> bool:
    return tuple(getattr(before, k, None) for k in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")) == tuple(getattr(after, k, None) for k in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))


def _same_inode(before: Any, after: Any) -> bool:
    return getattr(before, "st_dev", None) == getattr(after, "st_dev", None) and getattr(before, "st_ino", None) == getattr(after, "st_ino", None)


def _acquire_lock(path: str, *, api: Any = os, root_fd: int | None = None, state_fd: int | None = None, uid: int = 0, gid: int = 0) -> int:
    if fcntl is None: raise OSError("flock unavailable")
    if state_fd is None:
        parent, name = _open_parent(path, api=api, root_fd=root_fd, uid=uid, gid=gid)
    else:
        if path not in {LIFECYCLE_LOCK, EXECUTION_LOCK}: raise OSError("unexpected lock path")
        parent = api.dup(state_fd); name = path.rsplit("/", 1)[-1]
    fd = None
    try:
        fd = api.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=parent)
        info = api.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1: raise OSError("unsafe lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except Exception:
        if isinstance(fd, int):
            try: api.close(fd)
            except Exception: pass
        raise
    finally: api.close(parent)


def validate_postflight(*, assets_unchanged: bool, unit_inactive: bool, unit_disabled_or_static: bool, arm_absent: bool) -> bool:
    """Return true only for the required quiescent successor state."""
    return bool(assets_unchanged is True and unit_inactive is True and unit_disabled_or_static is True and arm_absent is True)


def validate_preflight(*, assets_exact: bool, unit_inactive: bool, unit_disabled_or_static: bool, arm_absent: bool, state_dir_safe: bool = True) -> bool:
    """Validate all host facts required before consuming replacement authority."""
    return bool(assets_exact is True and unit_inactive is True and unit_disabled_or_static is True and arm_absent is True and state_dir_safe is True)


@dataclass(frozen=True)
class Operations:
    api: Any = os
    root_path: str | None = None
    owner_uid: int = 0
    owner_gid: int = 0
    unit_runner: Any = _run_unit_state
    old_helper_digest: str = OLD_HELPER_SHA256

    def quiescent(self, *, helper_digest: str | None = None, root_fd: int | None = None, state_fd: int | None = None) -> bool:
        helper_digest = self.old_helper_digest if helper_digest is None else helper_digest
        try:
            state_parent, state_name = _open_parent(STATE_DIR, api=self.api, root_fd=root_fd, uid=self.owner_uid, gid=self.owner_gid)
            try: state_info = self.api.stat(state_name, dir_fd=state_parent, follow_symlinks=False)
            finally: self.api.close(state_parent)
            if not _safe_dir(state_info, uid=self.owner_uid, gid=self.owner_gid): return False
            if state_fd is None or not _same_inode(state_info, self.api.fstat(state_fd)): return False
        except Exception: return False
        try:
            if state_fd is None: return False
            self.api.stat("arm.json", dir_fd=state_fd, follow_symlinks=False)
            return False
        except FileNotFoundError:
            pass
        except Exception:
            return False
        if not _asset_valid(HELPER_PATH, helper_digest, 0o700, api=self.api, root_fd=root_fd, uid=self.owner_uid, gid=self.owner_gid) or any(not _asset_valid(path, digest, mode, api=self.api, root_fd=root_fd, uid=self.owner_uid, gid=self.owner_gid) for path, digest, mode in ASSET_PATHS[1:]): return False
        try:
            result = self.unit_runner(UNIT_STATE_COMMAND, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False, timeout=5, check=False)
            raw = result.stdout
            if result.returncode != 0 or type(raw) is not bytes or len(raw) > 256 or not raw.isascii() or raw.count(b"\n") != 4: return False
            lines = raw.decode("ascii").splitlines()
            if any(line.count("=") != 1 for line in lines): return False
            pairs = [line.split("=", 1) for line in lines]
            if len({key for key, _ in pairs}) != 4: return False
            values = dict(pairs)
            return values == {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "UnitFileState": "disabled"} or values == {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "UnitFileState": "static"}
        except Exception: return False


class SecureHostOperations(Operations):
    """Concrete fixed-root operation surface used by the host bootstrap."""

    def __init__(self, *, api: Any = os, root_path: str | None = None, owner_uid: int = 0, owner_gid: int = 0, unit_runner: Any = _run_unit_state, old_helper_digest: str = OLD_HELPER_SHA256) -> None:
        super().__init__(api=api, root_path=root_path, owner_uid=owner_uid, owner_gid=owner_gid, unit_runner=unit_runner, old_helper_digest=old_helper_digest)


def _copy_fd(source: int, destination: int, *, api: Any = os) -> None:
    api.lseek(source, 0, os.SEEK_SET); remaining = api.fstat(source).st_size
    while remaining:
        chunk = api.read(source, min(8192, remaining))
        if not chunk: raise OSError("short source")
        offset = 0
        while offset < len(chunk):
            count = api.write(destination, chunk[offset:])
            if count <= 0: raise OSError("short write")
            offset += count
        remaining -= len(chunk)


def _unlink_if_owned(*, api: Any, parent: int, name: str, descriptor: int | None) -> bool:
    if not isinstance(descriptor, int): return True
    try:
        held = api.fstat(descriptor)
        named = api.stat(name, dir_fd=parent, follow_symlinks=False)
        if not _same_inode(held, named): return False
        api.unlink(name, dir_fd=parent)
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


def _exclusive_copy(path: str, content: bytes, *, mode: int = 0o700, api: Any = os) -> tuple[int, str]:
    parent, name = _open_parent(path, api=api); temp = "." + name + ".odysseus-replace-new"; fd = None
    try:
        fd = api.open(temp, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=parent)
        api.fchown(fd, 0, 0)
        offset = 0
        while offset < len(content):
            count = api.write(fd, content[offset:])
            if not isinstance(count, int) or count <= 0: raise OSError("short write")
            offset += count
        api.fsync(fd)
        digest, (_, info) = _read_hash(fd, api=api)
        if digest != hashlib.sha256(content).hexdigest() or not _same_identity(info, api.fstat(fd)): raise OSError("write verification")
        api.fsync(parent)
        return fd, temp
    except Exception:
        if isinstance(fd, int): api.close(fd)
        try: api.unlink(temp, dir_fd=parent)
        except Exception: pass
        raise
    finally: api.close(parent)


def _rollback_under_locks(*, api: Any, parent: int, name: str, candidate_info: Any, operations: Operations, root_fd: int, state_fd: int) -> bool:
    """Restore only the exact candidate inode while both locks remain held."""
    backup = temp = current = None; temp_name = "." + name + ".odysseus-rollback-new"
    try:
        backup = api.open(BACKUP_PATH.rsplit("/", 1)[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        old_hash, old_meta = _read_hash(backup, api=api)
        if old_hash != operations.old_helper_digest or not _safe_file(old_meta[1], 0o700, uid=operations.owner_uid, gid=operations.owner_gid): return False
        current = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        current_hash, current_meta = _read_hash(current, api=api)
        if current_hash != NEW_HELPER_SHA256 or not _same_inode(candidate_info, current_meta[1]) or not _safe_file(current_meta[1], 0o700, uid=operations.owner_uid, gid=operations.owner_gid): return False
        temp = api.open(temp_name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o700, dir_fd=parent); api.fchown(temp, operations.owner_uid, operations.owner_gid)
        _copy_fd(backup, temp, api=api); api.fsync(temp)
        restored_hash, restored_meta = _read_hash(temp, api=api)
        rollback_info = api.fstat(temp)
        if restored_hash != operations.old_helper_digest or not _safe_file(restored_meta[1], 0o700, uid=operations.owner_uid, gid=operations.owner_gid) or not _same_inode(rollback_info, restored_meta[1]): return False
        api.fsync(parent)
        current_check = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            check_hash, check_meta = _read_hash(current_check, api=api)
            if check_hash != NEW_HELPER_SHA256 or not _same_inode(candidate_info, check_meta[1]) or not _safe_file(check_meta[1], 0o700, uid=operations.owner_uid, gid=operations.owner_gid): return False
        finally: api.close(current_check)
        api.replace(temp_name, name, src_dir_fd=parent, dst_dir_fd=parent); api.fsync(parent)
        reopened = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            final_hash, final_meta = _read_hash(reopened, api=api)
            return final_hash == operations.old_helper_digest and _safe_file(final_meta[1], 0o700, uid=operations.owner_uid, gid=operations.owner_gid) and _same_inode(rollback_info, final_meta[1]) and operations.quiescent(helper_digest=operations.old_helper_digest, root_fd=root_fd, state_fd=state_fd)
        finally: api.close(reopened)
    except Exception: return False
    finally:
        if isinstance(temp, int):
            try:
                held = api.fstat(temp)
                named = api.stat(temp_name, dir_fd=parent, follow_symlinks=False)
                if _same_identity(held, named): api.unlink(temp_name, dir_fd=parent)
            except FileNotFoundError: pass
            except Exception: pass
        for fd in (temp, current, backup):
            if isinstance(fd, int):
                try: api.close(fd)
                except Exception: pass


def replace(*, execute: bool = False, new_source: bytes | None = None, operations: Operations | None = None) -> dict[str, Any]:
    if execute is not True: return envelope("blocked", "execution_disabled")
    if type(new_source) is not bytes or len(new_source) == 0 or len(new_source) > MAX_BYTES or hashlib.sha256(new_source).hexdigest() != NEW_HELPER_SHA256: return envelope("blocked", "invalid_source")
    ops = operations or SecureHostOperations(); api = ops.api
    if os.name != "posix" or (os.geteuid() != 0 and ops.root_path is None): return envelope("blocked", "preflight_failed")
    locks: list[int] = []; old_fd = None; temp_fd = None; parent = None; temp_name = None; root_fd = None
    state_fd = None; candidate_info = None; publish_attempted = False; backup_publish_attempted = False; backup_created = False; backup_tmp = None; backup_fd = None; cleanup_ok = True
    try:
        root_fd = api.open(ops.root_path or "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        root_info = api.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != ops.owner_uid or root_info.st_gid != ops.owner_gid or stat.S_IMODE(root_info.st_mode) & 0o022: return envelope("blocked", "preflight_failed")
        state_parent, state_name = _open_parent(STATE_DIR, api=api, root_fd=root_fd, uid=ops.owner_uid, gid=ops.owner_gid)
        try:
            state_info = api.stat(state_name, dir_fd=state_parent, follow_symlinks=False)
            state_fd = api.open(state_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=state_parent)
        finally: api.close(state_parent)
        if not _safe_dir(state_info, uid=ops.owner_uid, gid=ops.owner_gid) or not _safe_dir(api.fstat(state_fd), uid=ops.owner_uid, gid=ops.owner_gid) or not _same_inode(state_info, api.fstat(state_fd)): return envelope("blocked", "preflight_failed")
        locks.append(_acquire_lock(LIFECYCLE_LOCK, api=api, root_fd=root_fd, state_fd=state_fd, uid=ops.owner_uid, gid=ops.owner_gid))
        locks.append(_acquire_lock(EXECUTION_LOCK, api=api, root_fd=root_fd, state_fd=state_fd, uid=ops.owner_uid, gid=ops.owner_gid))
        if not ops.quiescent(helper_digest=ops.old_helper_digest, root_fd=root_fd, state_fd=state_fd): return envelope("blocked", "preflight_failed")
        parent, name = _open_parent(HELPER_PATH, api=api, root_fd=root_fd, uid=ops.owner_uid, gid=ops.owner_gid)
        old_fd = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent); old_info = api.fstat(old_fd); old_hash, _ = _read_hash(old_fd, api=api)
        if old_hash != OLD_HELPER_SHA256 or not _safe_file(old_info, 0o700, uid=ops.owner_uid, gid=ops.owner_gid): return envelope("blocked", "foreign_target")
        # Backup publication is exclusive; any existing target is preserved and blocks.
        backup_name = BACKUP_PATH.rsplit("/", 1)[-1]
        try:
            api.stat(backup_name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return envelope("blocked", "backup_conflict")
        backup_tmp = "." + backup_name + ".tmp"; bfd = api.open(backup_tmp, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o700, dir_fd=parent); backup_fd = bfd
        try:
            api.fchown(bfd, ops.owner_uid, ops.owner_gid)
            _copy_fd(old_fd, bfd, api=api); api.fsync(bfd)
            backup_hash, (_, backup_info) = _read_hash(bfd, api=api)
            if backup_hash != OLD_HELPER_SHA256 or not _safe_file(backup_info, 0o700, uid=ops.owner_uid, gid=ops.owner_gid): raise OSError("backup verification")
            api.fsync(parent); backup_publish_attempted = True; api.link(backup_tmp, backup_name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            backup_final = api.open(backup_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            try:
                backup_final_hash, backup_final_meta = _read_hash(backup_final, api=api)
                info = backup_final_meta[1]
                if backup_final_hash != OLD_HELPER_SHA256 or not stat.S_ISREG(info.st_mode) or info.st_uid != ops.owner_uid or info.st_gid != ops.owner_gid or stat.S_IMODE(info.st_mode) != 0o700 or info.st_nlink != 2 or not _same_inode(backup_info, info): raise OSError("backup publish verification")
            finally: api.close(backup_final)
            api.unlink(backup_tmp, dir_fd=parent); api.fsync(parent)
            backup_final = api.open(backup_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            try:
                backup_final_hash, backup_final_meta = _read_hash(backup_final, api=api)
                if backup_final_hash != OLD_HELPER_SHA256 or not _safe_file(backup_final_meta[1], 0o700, uid=ops.owner_uid, gid=ops.owner_gid) or not _same_inode(backup_info, backup_final_meta[1]): raise OSError("backup final verification")
            finally: api.close(backup_final)
            backup_created = True
        finally:
            if not backup_created: cleanup_ok = _unlink_if_owned(api=api, parent=parent, name=backup_tmp, descriptor=bfd) and cleanup_ok
            api.close(bfd); backup_fd = None
        reopened = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            if not _same_identity(old_info, api.fstat(reopened)): return envelope("unknown", "foreign_target", backup=backup_created, effect=True, recovery=True)
        finally: api.close(reopened)
        temp_name = "." + name + ".odysseus-replace-new"; temp_fd = api.open(temp_name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o700, dir_fd=parent); api.fchown(temp_fd, ops.owner_uid, ops.owner_gid)
        offset = 0
        while offset < len(new_source):
            count = api.write(temp_fd, new_source[offset:])
            if not isinstance(count, int) or count <= 0: raise OSError("short write")
            offset += count
        api.fsync(temp_fd)
        candidate_hash, (_, candidate_info) = _read_hash(temp_fd, api=api)
        if candidate_hash != NEW_HELPER_SHA256 or not _safe_file(candidate_info, 0o700, uid=ops.owner_uid, gid=ops.owner_gid): raise OSError("candidate verification")
        api.fsync(parent)
        reopened = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            if not _same_identity(old_info, api.fstat(reopened)): return envelope("unknown", "foreign_target", backup=backup_created, effect=True, recovery=True)
        finally: api.close(reopened)
        publish_attempted = True
        api.replace(temp_name, name, src_dir_fd=parent, dst_dir_fd=parent); api.fsync(parent)
        final_fd = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            final_hash, final_meta = _read_hash(final_fd, api=api)
            if final_hash != NEW_HELPER_SHA256: return envelope("unknown", "publish_ambiguous", backup=backup_created, published=True, effect=True, recovery=True)
            if not _same_inode(candidate_info, final_meta[1]) or not _safe_file(final_meta[1], 0o700, uid=ops.owner_uid, gid=ops.owner_gid): return envelope("unknown", "foreign_target", backup=backup_created, published=True, effect=True, recovery=True)
        finally: api.close(final_fd)
        postflight_ok = ops.quiescent(helper_digest=NEW_HELPER_SHA256, root_fd=root_fd, state_fd=state_fd)
        if not postflight_ok:
            restored = _rollback_under_locks(api=api, parent=parent, name=name, candidate_info=final_meta[1], operations=ops, root_fd=root_fd, state_fd=state_fd)
            return envelope("rolled_back" if restored else "unknown", "postflight_failed" if restored else "rollback_failed", backup=backup_created, published=True, postflight=False, rollback_attempted=True, rollback_succeeded=restored, effect=True, recovery=not restored)
        return envelope("applied", "none", backup=backup_created, published=True, postflight=True, effect=True)
    except BlockingIOError: return envelope("blocked", "lock_busy")
    except Exception:
        if publish_attempted and parent is not None and candidate_info is not None and backup_created:
            restored = _rollback_under_locks(api=api, parent=parent, name=HELPER_PATH.rsplit("/", 1)[-1], candidate_info=candidate_info, operations=ops, root_fd=root_fd, state_fd=state_fd)
            return envelope("rolled_back" if restored else "unknown", "write_failed" if restored else "rollback_failed", backup=True, published=True, rollback_attempted=True, rollback_succeeded=restored, effect=True, recovery=not restored)
        return envelope("unknown", "write_failed", backup=backup_created, published=publish_attempted, effect=True, recovery=True) if backup_created or backup_publish_attempted or publish_attempted or not cleanup_ok else envelope("blocked", "write_failed")
    finally:
        if parent is not None and temp_name is not None:
            cleanup_descriptor = temp_fd if isinstance(temp_fd, int) else None
            if cleanup_descriptor is None and candidate_info is not None:
                try:
                    current_temp = api.open(temp_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                    try:
                        if _same_identity(candidate_info, api.fstat(current_temp)):
                            api.unlink(temp_name, dir_fd=parent)
                    finally: api.close(current_temp)
                except FileNotFoundError: pass
                except Exception: cleanup_ok = False
            elif cleanup_descriptor is not None:
                cleanup_ok = _unlink_if_owned(api=api, parent=parent, name=temp_name, descriptor=cleanup_descriptor) and cleanup_ok
        for fd in (temp_fd, backup_fd, old_fd, parent, state_fd, root_fd):
            if isinstance(fd, int):
                try: api.close(fd)
                except Exception: pass
        for fd in reversed(locks):
            try: api.close(fd)
            except Exception: pass


def rollback(*, execute: bool = False, operations: Operations | None = None) -> dict[str, Any]:
    # Standalone rollback has no durable candidate inode authority. Recovery is
    # available only inside replace(), while both lifecycle locks remain held.
    return envelope("blocked", "execution_disabled")


def main() -> int:
    print(json.dumps(envelope("blocked", "execution_disabled"), sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__": raise SystemExit(main())

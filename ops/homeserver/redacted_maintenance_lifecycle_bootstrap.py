#!/usr/bin/env python3
"""Root-only, no-repair publication of the shared maintenance lock."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ops.homeserver import redacted_maintenance_lifecycle_lock as lifecycle


SCHEMA_ID = "odysseus.redacted_maintenance_lifecycle_bootstrap.v1"
UNIT = "odysseus-predeploy-backup-root-helper.service"
STATE_DIRECTORY = lifecycle.BACKUP_STATE_DIRECTORY
ARM_NAME = "arm.json"
_BLOCKED_CODES = frozenset({"execution_disabled", "identity_unavailable", "lock_busy", "unit_not_quiescent", "unsafe_existing_object", "publication_failed_before_exposure"})
_UNKNOWN_CODES = frozenset({"publication_ambiguous", "postflight_not_quiescent"})
_KEYS = frozenset({"schema_id", "status", "error_code", "publication_attempted", "published", "manual_recovery_required", "retry_permitted", "raw_output_visible", "secret_values_visible", "evidence_sha256"})


def _digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _envelope(status: str, code: str, *, attempted: bool = False, published: bool = False) -> dict[str, Any]:
    value = {
        "schema_id": SCHEMA_ID,
        "status": status,
        "error_code": code,
        "publication_attempted": attempted,
        "published": published,
        "manual_recovery_required": status == "unknown",
        "retry_permitted": False,
        "raw_output_visible": False,
        "secret_values_visible": False,
    }
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    if type(value) is not dict or set(value) != _KEYS:
        return False
    if value.get("schema_id") != SCHEMA_ID:
        return False
    status = value.get("status")
    code = value.get("error_code")
    if type(status) is not str or type(code) is not str:
        return False
    if type(value.get("publication_attempted")) is not bool or type(value.get("published")) is not bool:
        return False
    if type(value.get("manual_recovery_required")) is not bool or type(value.get("retry_permitted")) is not bool:
        return False
    if type(value.get("raw_output_visible")) is not bool or type(value.get("secret_values_visible")) is not bool:
        return False
    if type(value.get("evidence_sha256")) is not str or value["evidence_sha256"] != _digest(value):
        return False
    if status == "ready":
        return code == "none" and value["published"] and not value["manual_recovery_required"] and not value["retry_permitted"]
    if status == "blocked":
        return code in _BLOCKED_CODES and not value["publication_attempted"] and not value["published"] and not value["manual_recovery_required"] and not value["retry_permitted"]
    if status == "unknown":
        if code == "publication_ambiguous":
            return value["publication_attempted"] and value["manual_recovery_required"] and not value["retry_permitted"]
        return code == "postflight_not_quiescent" and value["published"] and value["manual_recovery_required"] and not value["retry_permitted"]
    return False


def _same_inode(left: Any, right: Any) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _directory_matches(info: Any, *, uid: int, gid: int, mode: int) -> bool:
    return stat.S_ISDIR(info.st_mode) and info.st_uid == uid and info.st_gid == gid and stat.S_IMODE(info.st_mode) == mode and info.st_nlink >= 1


def _lock_matches(info: Any, *, gid: int) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == gid and stat.S_IMODE(info.st_mode) == lifecycle.LOCK_MODE and info.st_nlink == 1


def _open_var_lib(*, root: str, api: Any) -> int:
    return lifecycle._open_components("/var/lib", root=root, api=api)


def _open_directory_at(parent: int, name: str, *, api: Any) -> tuple[int, Any]:
    path_info = api.stat(name, dir_fd=parent, follow_symlinks=False)
    descriptor = api.open(name, api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW | api.O_CLOEXEC, dir_fd=parent)
    held_info = api.fstat(descriptor)
    if not _same_inode(path_info, held_info):
        api.close(descriptor)
        raise OSError(errno.ESTALE, "directory changed")
    return descriptor, held_info


def _arm_absent(*, root: str, api: Any) -> bool:
    state_fd = lifecycle._open_components(STATE_DIRECTORY, root=root, api=api)
    try:
        state = api.fstat(state_fd)
        if not _directory_matches(state, uid=0, gid=0, mode=0o700):
            return False
        try:
            api.stat(ARM_NAME, dir_fd=state_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        return False
    finally:
        api.close(state_fd)


def _unit_is_quiescent(runner: Callable[..., Any]) -> bool:
    command = (
        "/usr/bin/systemctl",
        "show",
        "--no-pager",
        "--property=LoadState",
        "--property=ActiveState",
        "--property=SubState",
        "--property=UnitFileState",
        UNIT,
    )
    try:
        result = runner(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=5,
            check=False,
        )
        raw = getattr(result, "stdout", None)
        if getattr(result, "returncode", None) != 0 or type(raw) is not bytes or len(raw) > 256:
            return False
        if not raw.isascii() or raw.count(b"\n") != 4:
            return False
        lines = raw.decode("ascii").splitlines()
        pairs = [line.split("=", 1) for line in lines if line.count("=") == 1]
        if len(pairs) != 4 or len({key for key, _ in pairs}) != 4:
            return False
        values = dict(pairs)
        return values in (
            {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "UnitFileState": "disabled"},
            {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "UnitFileState": "static"},
        )
    except Exception:
        return False


def _safe_unpublish_hidden(parent: int, directory_name: str, directory_fd: int | None, file_fd: int | None, *, api: Any) -> bool:
    """Remove only this invocation's still-hidden objects before publication."""
    if directory_fd is None:
        return True
    clean = True
    if file_fd is not None:
        try:
            held = api.fstat(file_fd)
            named = api.stat("lifecycle.lock", dir_fd=directory_fd, follow_symlinks=False)
            if _same_inode(held, named):
                api.unlink("lifecycle.lock", dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except Exception:
            clean = False
    try:
        held_dir = api.fstat(directory_fd)
        named_dir = api.stat(directory_name, dir_fd=parent, follow_symlinks=False)
        if _same_inode(held_dir, named_dir):
            api.rmdir(directory_name, dir_fd=parent)
    except FileNotFoundError:
        pass
    except Exception:
        clean = False
    return clean


def _publish_shared(*, root: str, gid: int, api: Any) -> tuple[bool, bool]:
    """Return (published, publication_attempted), preserving ambiguity."""
    parent = _open_var_lib(root=root, api=api)
    directory_fd: int | None = None
    lock_fd: int | None = None
    directory_name = "odysseus-maintenance"
    created = False
    attempted = False
    try:
        try:
            api.stat(directory_name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            api.mkdir(directory_name, 0o700, dir_fd=parent)
            created = True
            api.fsync(parent)
        directory_fd, directory_info = _open_directory_at(parent, directory_name, api=api)
        if created:
            if not _directory_matches(directory_info, uid=0, gid=0, mode=0o700):
                raise OSError(errno.EPERM, "unsafe new directory")
            lock_fd = api.open(
                "lifecycle.lock",
                api.O_RDWR | api.O_CREAT | api.O_EXCL | api.O_NOFOLLOW | api.O_CLOEXEC,
                0o600,
                dir_fd=directory_fd,
            )
            api.fchown(lock_fd, 0, gid)
            api.fchmod(lock_fd, lifecycle.LOCK_MODE)
            lock_info = api.fstat(lock_fd)
            if not _lock_matches(lock_info, gid=gid):
                raise OSError(errno.EPERM, "unsafe new lock")
            api.fsync(lock_fd)
            api.fsync(directory_fd)
            api.fsync(parent)
            api.fchown(directory_fd, 0, gid)
            api.fsync(directory_fd)
            attempted = True
            api.fchmod(directory_fd, 0o750)
            api.fsync(directory_fd)
            api.fsync(parent)
        else:
            if not _directory_matches(directory_info, uid=0, gid=gid, mode=0o750):
                raise OSError(errno.EPERM, "unsafe existing directory")
            lock_before = api.stat("lifecycle.lock", dir_fd=directory_fd, follow_symlinks=False)
            if not _lock_matches(lock_before, gid=gid):
                raise OSError(errno.EPERM, "unsafe existing lock")
        if not _directory_matches(api.fstat(directory_fd), uid=0, gid=gid, mode=0o750):
            raise OSError(errno.EPERM, "published directory mismatch")
        if lock_fd is None:
            lock_fd = api.open("lifecycle.lock", api.O_RDWR | api.O_NOFOLLOW | api.O_CLOEXEC, dir_fd=directory_fd)
        lock_named = api.stat("lifecycle.lock", dir_fd=directory_fd, follow_symlinks=False)
        lock_held = api.fstat(lock_fd)
        if not _lock_matches(lock_named, gid=gid) or not _lock_matches(lock_held, gid=gid) or not _same_inode(lock_named, lock_held):
            raise OSError(errno.ESTALE, "published lock mismatch")
        import fcntl
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_after = api.stat("lifecycle.lock", dir_fd=directory_fd, follow_symlinks=False)
        if not _lock_matches(lock_after, gid=gid) or not _same_inode(lock_after, api.fstat(lock_fd)):
            raise OSError(errno.ESTALE, "published lock changed")
        return True, attempted
    except Exception:
        if created and not attempted:
            _safe_unpublish_hidden(parent, directory_name, directory_fd, lock_fd, api=api)
        return False, attempted
    finally:
        for descriptor in (lock_fd, directory_fd, parent):
            if isinstance(descriptor, int):
                try:
                    api.close(descriptor)
                except Exception:
                    pass


@dataclass
class BootstrapOperations:
    api: Any = os
    root_path: str = "/"
    euid: int | None = None
    group_identity: Callable[[], tuple[int, int]] = lifecycle.homebase_private_gid
    unit_runner: Callable[..., Any] = subprocess.run

    def current_euid(self) -> int:
        return int(self.api.geteuid() if self.euid is None else self.euid)


def bootstrap(*, execute: bool = False, operations: BootstrapOperations | None = None) -> dict[str, Any]:
    """Create and validate the fixed shared lock; ordinary calls are inert."""
    if execute is not True:
        return _envelope("blocked", "execution_disabled")
    ops = operations or BootstrapOperations()
    api = ops.api
    lifecycle_fd: int | None = None
    execution_fd: int | None = None
    published = False
    attempted = False
    try:
        if os.name != "posix" or ops.current_euid() != 0:
            return _envelope("blocked", "identity_unavailable")
        _, gid = ops.group_identity()
        lifecycle_fd = lifecycle.acquire_root_state_lock(lifecycle.BACKUP_LIFECYCLE_LOCK, root=ops.root_path, api=api)
        execution_fd = lifecycle.acquire_root_state_lock(lifecycle.BACKUP_EXECUTION_LOCK, root=ops.root_path, api=api)
        if not _unit_is_quiescent(ops.unit_runner) or not _arm_absent(root=ops.root_path, api=api):
            return _envelope("blocked", "unit_not_quiescent")
        published, attempted = _publish_shared(root=ops.root_path, gid=gid, api=api)
        if not published:
            if attempted:
                return _envelope("unknown", "publication_ambiguous", attempted=True, published=True)
            return _envelope("blocked", "unsafe_existing_object")
        if not _unit_is_quiescent(ops.unit_runner) or not _arm_absent(root=ops.root_path, api=api):
            return _envelope("unknown", "postflight_not_quiescent", attempted=attempted, published=True)
        return _envelope("ready", "none", attempted=attempted, published=True)
    except BlockingIOError:
        return _envelope("blocked", "lock_busy")
    except Exception:
        if attempted:
            return _envelope("unknown", "publication_ambiguous", attempted=True, published=True)
        return _envelope("blocked", "identity_unavailable")
    finally:
        for descriptor in (execution_fd, lifecycle_fd):
            if isinstance(descriptor, int):
                try:
                    lifecycle.close_lock(descriptor, api=api)
                except Exception:
                    pass


def main() -> int:
    value = _envelope("blocked", "execution_disabled")
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

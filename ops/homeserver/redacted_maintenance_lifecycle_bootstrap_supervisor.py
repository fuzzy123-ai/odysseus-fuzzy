#!/usr/bin/env python3
"""One-use, fixed root supervisor for the maintenance bootstrap service."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import sys
import time
import types
from typing import Any, Mapping


SCHEMA_ID = "odysseus.redacted_maintenance_lifecycle_bootstrap_supervisor.v1"
ARM_SCHEMA_ID = "odysseus.redacted_maintenance_lifecycle_bootstrap_arm.v1"
RECEIPT_SCHEMA_ID = "odysseus.redacted_maintenance_lifecycle_bootstrap_receipt.v1"
ASSET_DIRECTORY = "/usr/local/libexec/odysseus-maintenance-bootstrap"
STATE_DIRECTORY = "/var/lib/odysseus-maintenance-bootstrap"
ARM_NAME = "arm.json"
CONSUMED_NAME = "consumed.json"
RECEIPT_NAME = "receipt.json"
ASSET_NAMES = ("lifecycle.py", "bootstrap.py", "supervisor.py", "readback.py")
ASSET_PATHS = {name: ASSET_DIRECTORY + "/" + name for name in ASSET_NAMES}
SHARED_DIRECTORY = "/var/lib/odysseus-maintenance"
SHARED_LOCK = SHARED_DIRECTORY + "/lifecycle.lock"
BACKUP_UNIT = "odysseus-predeploy-backup-root-helper.service"
MAX_ARM_BYTES = 4096
MAX_ASSET_BYTES = 350_000
MAX_RECEIPT_BYTES = 8192


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_inode(a: Any, b: Any) -> bool:
    return a.st_dev == b.st_dev and a.st_ino == b.st_ino


def _safe_dir(info: Any, mode: int | None = None) -> bool:
    return stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and info.st_nlink >= 1 and (stat.S_IMODE(info.st_mode) == mode if mode is not None else stat.S_IMODE(info.st_mode) & 0o022 == 0)


def _safe_file(info: Any, mode: int) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1


def _acquire_existing_legacy_lock(
    lifecycle: Any,
    *,
    root: str = "/",
    api: Any = os,
    effective_uid: int | None = None,
    users: Any = None,
    groups: Any = None,
) -> int:
    """Root bridge for the already-published homebase-owned legacy lock."""
    caller = int(api.geteuid() if effective_uid is None else effective_uid)
    if caller != 0:
        raise OSError(errno.EPERM, "legacy lock bridge requires root")
    account_args = {} if users is None and groups is None else {"users": users, "groups": groups}
    uid, gid = lifecycle.homebase_private_gid(**account_args)
    parent = lifecycle._open_tmp_parent(root=root, api=api)
    descriptor: int | None = None
    try:
        descriptor = lifecycle._acquire_existing(
            parent,
            "odysseus-auto-update.lock",
            uid=uid,
            gid=gid,
            mode=lifecycle.LEGACY_LOCK_MODE,
            api=api,
        )
        owned_parent = parent
        parent = None
        api.close(owned_parent)
        result = descriptor
        descriptor = None
        return result
    finally:
        if descriptor is not None:
            try:
                api.close(descriptor)
            except BaseException:
                pass
        if parent is not None:
            api.close(parent)


def _open_absolute_directory(path: str) -> int:
    if type(path) is not str or not path.startswith("/") or "//" in path or ".." in path.split("/"):
        raise OSError(errno.EINVAL, "invalid fixed directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for name in filter(None, path.split("/")):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
            if not _safe_dir(os.fstat(fd)):
                raise OSError(errno.EPERM, "unsafe fixed directory")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_named(directory_fd: int, name: str, *, limit: int, mode: int = 0o600) -> tuple[bytes, Any]:
    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if not _safe_file(before, mode):
        raise OSError(errno.EPERM, "unsafe fixed file")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
    try:
        held = os.fstat(fd)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _same_inode(before, held) or not _same_inode(held, named) or not _safe_file(held, mode):
            raise OSError(errno.ESTALE, "fixed file changed")
        chunks = bytearray()
        while len(chunks) <= limit:
            part = os.read(fd, min(65536, limit + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        if len(chunks) > limit:
            raise OSError(errno.EFBIG, "fixed file exceeds bound")
        return bytes(chunks), held
    finally:
        os.close(fd)


def _load_assets(expected: Mapping[str, str]) -> tuple[dict[str, bytes], dict[str, types.ModuleType]]:
    package_fd = _open_absolute_directory(ASSET_DIRECTORY)
    data: dict[str, bytes] = {}
    try:
        if not _safe_dir(os.fstat(package_fd), 0o755):
            raise OSError(errno.EPERM, "unsafe asset package")
        for name in ASSET_NAMES:
            raw, _ = _read_named(package_fd, name, limit=MAX_ASSET_BYTES, mode=0o700 if name.endswith(".py") else 0o644)
            if type(expected.get(name)) is not str or len(expected[name]) != 64 or _digest(raw) != expected[name]:
                raise OSError(errno.EBADMSG, "asset pin mismatch")
            data[name] = raw
    finally:
        os.close(package_fd)

    packages = {"ops": types.ModuleType("ops"), "ops.homeserver": types.ModuleType("ops.homeserver")}
    packages["ops"].__path__ = []
    packages["ops.homeserver"].__path__ = []
    packages["ops"].__package__ = "ops"
    packages["ops.homeserver"].__package__ = "ops.homeserver"
    sys.modules.update(packages)
    loaded: dict[str, types.ModuleType] = {}
    for name, module_name in (("lifecycle.py", "ops.homeserver.redacted_maintenance_lifecycle_lock"), ("bootstrap.py", "ops.homeserver.redacted_maintenance_lifecycle_bootstrap")):
        module = types.ModuleType(module_name)
        module.__file__ = ASSET_PATHS[name]
        module.__package__ = "ops.homeserver"
        sys.modules[module_name] = module
        setattr(packages["ops.homeserver"], module_name.rsplit(".", 1)[1], module)
        exec(compile(data[name], module.__file__, "exec"), module.__dict__)
        loaded[name] = module
    return data, loaded


def _strict_json(raw: bytes, *, limit: int) -> Any:
    if type(raw) is not bytes or not raw or len(raw) > limit:
        raise ValueError
    value = json.loads(raw.decode("ascii"), object_pairs_hook=_unique_pairs)
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _valid_id(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def validate_arm(value: Any, *, now: int | None = None) -> bool:
    if type(value) is not dict or set(value) != {"schema_id", "grant_id", "invocation_id", "expires_at", "asset_pins"}:
        return False
    if value["schema_id"] != ARM_SCHEMA_ID or not _valid_id(value["grant_id"]) or not _valid_id(value["invocation_id"]):
        return False
    if type(value["expires_at"]) is not int:
        return False
    current = int(time.time()) if now is None else now
    if value["expires_at"] < current or value["expires_at"] > current + 600:
        return False
    pins = value["asset_pins"]
    return type(pins) is dict and set(pins) == set(ASSET_NAMES) and all(_valid_id(pin) for pin in pins.values())


def _entry_absent(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        return False
    except FileNotFoundError:
        return True


def _consume(state_fd: int, arm_raw: bytes, arm_info: Any) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(CONSUMED_NAME, flags, 0o600, dir_fd=state_fd)
    try:
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o600)
        _write_all(fd, arm_raw)
        os.fsync(fd)
        if not _safe_file(os.fstat(fd), 0o600):
            raise OSError(errno.EPERM, "unsafe consumed marker")
        named = os.stat(CONSUMED_NAME, dir_fd=state_fd, follow_symlinks=False)
        if not _same_inode(os.fstat(fd), named):
            raise OSError(errno.ESTALE, "consumed marker changed")
    finally:
        os.close(fd)
    named_arm = os.stat(ARM_NAME, dir_fd=state_fd, follow_symlinks=False)
    if not _same_inode(arm_info, named_arm):
        raise OSError(errno.ESTALE, "arm changed during consume")
    # Persist the consumed name before removing the arm. If this directory
    # sync fails, leave both names in place and fail closed on the marker.
    os.fsync(state_fd)
    os.unlink(ARM_NAME, dir_fd=state_fd)
    os.fsync(state_fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "short fixed write")
        view = view[written:]


def _atomic_receipt(state_fd: int, value: Mapping[str, Any]) -> None:
    data = _canonical(value)
    fd = os.open(RECEIPT_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=state_fd)
    try:
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o600)
        _write_all(fd, data)
        os.fsync(fd)
        if not _safe_file(os.fstat(fd), 0o600):
            raise OSError(errno.EPERM, "unsafe receipt")
    finally:
        os.close(fd)
    os.fsync(state_fd)


def _shared_metadata() -> dict[str, Any]:
    shared_fd = _open_absolute_directory(SHARED_DIRECTORY)
    try:
        info = os.fstat(shared_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid <= 0 or stat.S_IMODE(info.st_mode) != 0o750:
            raise OSError(errno.EPERM, "unsafe shared directory")
        lock_info = os.stat("lifecycle.lock", dir_fd=shared_fd, follow_symlinks=False)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != 0 or lock_info.st_gid != info.st_gid or lock_info.st_nlink != 1 or stat.S_IMODE(lock_info.st_mode) != 0o660:
            raise OSError(errno.EPERM, "unsafe shared lock")
        return {"directory_uid": 0, "directory_gid": info.st_gid, "directory_mode": stat.S_IMODE(info.st_mode), "lock_uid": 0, "lock_gid": lock_info.st_gid, "lock_mode": stat.S_IMODE(lock_info.st_mode), "lock_nlink": lock_info.st_nlink}
    finally:
        os.close(shared_fd)


def _receipt(value: Mapping[str, Any], result: Mapping[str, Any], metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    receipt = {
        "schema_id": RECEIPT_SCHEMA_ID,
        "grant_id": value["grant_id"],
        "invocation_id": value["invocation_id"],
        "asset_pins": value["asset_pins"],
        "outcome": result["status"],
        "error_code": result["error_code"],
        "shared_metadata": metadata,
        "recorded_at": int(time.time()),
    }
    receipt["receipt_sha256"] = _digest(_canonical(receipt))
    return receipt


def run_supervisor() -> int:
    """Consume a root-created arm and run the fixed bootstrap exactly once."""
    if os.name != "posix" or os.geteuid() != 0:
        return 1
    state_fd = lifecycle_fd = backup_lifecycle_fd = backup_execution_fd = None
    consumed = False
    try:
        state_fd = _open_absolute_directory(STATE_DIRECTORY)
        if not _safe_dir(os.fstat(state_fd), 0o700):
            return 1
        if not _entry_absent(state_fd, CONSUMED_NAME) or not _entry_absent(state_fd, RECEIPT_NAME):
            return 1
        arm_raw, arm_info = _read_named(state_fd, ARM_NAME, limit=MAX_ARM_BYTES)
        arm = _strict_json(arm_raw, limit=MAX_ARM_BYTES)
        if not validate_arm(arm):
            return 1
        if not _entry_absent(state_fd, CONSUMED_NAME) or not _entry_absent(state_fd, RECEIPT_NAME):
            return 1
        assets, modules = _load_assets(arm["asset_pins"])
        if set(assets) != set(ASSET_NAMES):
            return 1
        lifecycle = modules["lifecycle.py"]
        bootstrap = modules["bootstrap.py"]
        # The legacy lock is the only lock held by this supervisor. The fixed
        # bootstrap acquires the backup lifecycle then execution locks itself.
        lifecycle_fd = _acquire_existing_legacy_lock(lifecycle)
        backup_lifecycle_fd = None
        _consume(state_fd, arm_raw, arm_info)
        consumed = True
        try:
            result = bootstrap.bootstrap(execute=True)
            if type(result) is not dict or bootstrap.validate_envelope(result) is not True:
                raise ValueError
            metadata = _shared_metadata() if result.get("status") == "ready" else None
            receipt = _receipt(arm, result, metadata)
            _atomic_receipt(state_fd, receipt)
            return 0
        except BaseException:
            # No raw exception, traceback, or subprocess output reaches the
            # service's null stdio or a caller-visible channel.
            return 1
    except BaseException:
        return 1
    finally:
        for descriptor in (backup_execution_fd, backup_lifecycle_fd, lifecycle_fd, state_fd):
            if type(descriptor) is int:
                try:
                    os.close(descriptor)
                except BaseException:
                    pass


def main() -> int:
    return run_supervisor()


if __name__ == "__main__":
    raise SystemExit(main())

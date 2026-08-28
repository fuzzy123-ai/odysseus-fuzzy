#!/usr/bin/env python3
"""Exact-version, fail-closed upgrade of the installed root-helper scripts."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_upgrade.v1"
HELPER_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper.py"
READBACK_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py"
UNIT_PATH = "/etc/systemd/system/odysseus-predeploy-backup-root-helper.service"
SUDOERS_PATH = "/etc/sudoers.d/odysseus-predeploy-backup-root-helper"
STATE_DIR = "/var/lib/odysseus-predeploy-backup-root-helper"
ARM_NAME = "arm.json"
UNIT = "odysseus-predeploy-backup-root-helper.service"
OLD_HELPER_SHA256 = "dbcbac4c5a4b65edcc4d4facd9204674a8e9114179f406c88d067c7f96185a97"
OLD_READBACK_SHA256 = "8201653d392d1556a81f6ca236e9f5dd94b6d425dc03ae699f74280b4ae9b722"
NEW_HELPER_SHA256 = "9c9e6632be23a04d6c8d284b868b227b5576f35bb480208c1b4f7f0635f21032"
NEW_READBACK_SHA256 = "e647b6f1faa409f42cbeb80c74826b730695d9e0fad14cf47aa22c1a59a0a046"
UNIT_SHA256 = "466de2f889a00ee2759bd06380ddb213f8c0f4cee5644e3a2e27083863c1ab98"
SUDOERS_SHA256 = "1a6a7f1ec4d328c9fed20b758a87bb9905b68145122f6751fd5eeea748d5847d"
MAX_SOURCE_BYTES = 400_000
MINIMAL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
ACTIVE_COMMAND = ("/usr/bin/systemctl", "is-active", UNIT)
ENABLED_COMMAND = ("/usr/bin/systemctl", "is-enabled", UNIT)
_CODES = frozenset({"none", "execution_disabled", "source_mismatch", "preflight_failed", "write_failed", "rollback_failed", "postflight_failed"})
_KEYS = frozenset({"schema_id", "status", "error_code", "upgrade_invoked", "helper_upgraded", "readback_upgraded", "rollback_attempted", "rollback_succeeded", "effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})


class PublicationUncertain(Exception):
    """The target may have been atomically replaced; do not guess."""


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def receipt(status: str, code: str, *, invoked: bool = False, helper: bool = False, readback: bool = False, rollback_attempted: bool = False, rollback_succeeded: bool = False, effect: bool = False, recovery: bool = False) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": status if status in {"blocked", "upgraded", "rolled_back", "unknown"} else "unknown", "error_code": code if code in _CODES else "preflight_failed", "upgrade_invoked": invoked is True, "helper_upgraded": helper is True, "readback_upgraded": readback is True, "rollback_attempted": rollback_attempted is True, "rollback_succeeded": rollback_succeeded is True, "effect_may_have_occurred": effect is True, "manual_recovery_required": recovery is True, "retry_permitted": False, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate_receipt(value: Any) -> bool:
    if not (type(value) is dict and set(value) == _KEYS and value.get("schema_id") == SCHEMA_ID and value.get("error_code") in _CODES and value.get("retry_permitted") is False and all(value.get(key) is False for key in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) and value.get("evidence_sha256") == _digest(value)): return False
    if value["status"] == "blocked": return bool(value["error_code"] in {"execution_disabled", "source_mismatch", "preflight_failed", "write_failed"} and all(value[key] is False for key in ("upgrade_invoked", "helper_upgraded", "readback_upgraded", "rollback_attempted", "rollback_succeeded", "effect_may_have_occurred", "manual_recovery_required")))
    if value["status"] == "upgraded": return bool(value["error_code"] == "none" and all(value[key] is True for key in ("upgrade_invoked", "helper_upgraded", "readback_upgraded", "effect_may_have_occurred")) and all(value[key] is False for key in ("rollback_attempted", "rollback_succeeded", "manual_recovery_required")))
    if value["status"] == "rolled_back": return bool(value["error_code"] == "write_failed" and value["upgrade_invoked"] is True and value["helper_upgraded"] is False and value["readback_upgraded"] is False and value["rollback_attempted"] is True and value["rollback_succeeded"] is True and value["effect_may_have_occurred"] is True and value["manual_recovery_required"] is False)
    return bool(value["status"] == "unknown" and value["error_code"] in {"write_failed", "rollback_failed", "postflight_failed"} and value["upgrade_invoked"] is True and value["effect_may_have_occurred"] is True and value["manual_recovery_required"] is True)


def _safe_parent(info: Any) -> bool:
    return bool(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) in {0o755, 0o700} and info.st_nlink >= 1)


def _safe_file(info: Any, mode: int, maximum: int = MAX_SOURCE_BYTES) -> bool:
    return bool(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1 and 0 < info.st_size <= maximum)


def _open_parent(path: str, *, api: Any = os) -> tuple[int, str]:
    parts = path.split("/")[1:]
    if not parts or any(not part or part in {".", ".."} for part in parts): raise OSError()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current = api.open("/", flags)
    try:
        root_info = api.fstat(current)
        if not _safe_parent(root_info) or stat.S_IMODE(root_info.st_mode) != 0o755: raise OSError()
        for part in parts[:-1]:
            following = api.open(part, flags, dir_fd=current); api.close(current); current = following
            if not _safe_parent(api.fstat(current)): raise OSError()
        return current, parts[-1]
    except Exception:
        try: api.close(current)
        except Exception: pass
        raise


def _read_exact(path: str, expected: str, mode: int, *, api: Any = os) -> bytes:
    parent = descriptor = None
    try:
        parent, name = _open_parent(path, api=api)
        descriptor = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        before = api.fstat(descriptor)
        identity = tuple(getattr(before, key, None) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))
        if not _safe_file(before, mode): raise OSError()
        raw = bytearray()
        while len(raw) <= MAX_SOURCE_BYTES:
            piece = api.read(descriptor, min(8192, MAX_SOURCE_BYTES + 1 - len(raw)))
            if not piece: break
            raw.extend(piece)
        after = api.fstat(descriptor)
        if len(raw) != before.st_size or len(raw) > MAX_SOURCE_BYTES or api.read(descriptor, 1) != b"" or tuple(getattr(after, key, None) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")) != identity or after.st_nlink != 1 or hashlib.sha256(raw).hexdigest() != expected: raise OSError()
        return bytes(raw)
    finally:
        for item in (descriptor, parent):
            if isinstance(item, int):
                try: api.close(item)
                except Exception: pass


def _replace_exact(path: str, expected: str, replacement: bytes, replacement_digest: str, mode: int, *, api: Any = os) -> None:
    parent = descriptor = None; temporary_created = False; replaced = False; temporary_token = None
    name = path.rsplit("/", 1)[-1]; temporary = "." + name + ".odysseus-upgrade"
    try:
        _read_exact(path, expected, mode, api=api)
        parent, observed_name = _open_parent(path, api=api)
        if observed_name != name: raise OSError()
        descriptor = api.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=parent); temporary_created = True
        api.fchown(descriptor, 0, 0); api.fchmod(descriptor, mode)
        created = api.fstat(descriptor)
        if not (stat.S_ISREG(created.st_mode) and created.st_uid == 0 and created.st_gid == 0 and stat.S_IMODE(created.st_mode) == mode and created.st_nlink == 1 and created.st_size == 0): raise OSError()
        temporary_token = (int(created.st_dev), int(created.st_ino))
        offset = 0
        while offset < len(replacement):
            written = api.write(descriptor, replacement[offset:])
            if not isinstance(written, int) or written <= 0: raise OSError()
            offset += written
        api.fsync(descriptor)
        info = api.fstat(descriptor)
        if not (_safe_file(info, mode) and info.st_size == len(replacement)): raise OSError()
        if (int(info.st_dev), int(info.st_ino)) != temporary_token: raise OSError()
        api.lseek(descriptor, 0, os.SEEK_SET); observed = bytearray()
        while len(observed) < len(replacement):
            piece = api.read(descriptor, len(replacement) - len(observed))
            if not piece: raise OSError()
            observed.extend(piece)
        if bytes(observed) != replacement or api.read(descriptor, 1) != b"" or hashlib.sha256(observed).hexdigest() != replacement_digest: raise OSError()
        api.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent); temporary_created = False; replaced = True
        api.fsync(parent)
        _read_exact(path, replacement_digest, mode, api=api)
    except Exception:
        if replaced: raise PublicationUncertain() from None
        raise
    finally:
        if isinstance(descriptor, int):
            try: api.close(descriptor)
            except Exception: pass
        if temporary_created and isinstance(parent, int):
            try:
                info = api.stat(temporary, dir_fd=parent, follow_symlinks=False)
                if temporary_token is not None and (int(info.st_dev), int(info.st_ino)) == temporary_token: api.unlink(temporary, dir_fd=parent); api.fsync(parent)
            except Exception: pass
        if isinstance(parent, int):
            try: api.close(parent)
            except Exception: pass


@dataclass(frozen=True)
class Operations:
    read_exact: Callable[[str, str, int], bytes]
    replace_exact: Callable[[str, str, bytes, str, int], None]


class SecureHostOperations:
    def read_exact(self, path: str, expected: str, mode: int) -> bytes: return _read_exact(path, expected, mode)
    def replace_exact(self, path: str, expected: str, replacement: bytes, replacement_digest: str, mode: int) -> None: _replace_exact(path, expected, replacement, replacement_digest, mode)


def _unit_safe() -> bool:
    try:
        active = subprocess.run(list(ACTIVE_COMMAND), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=MINIMAL_ENV, close_fds=True, timeout=5, check=False)
        enabled = subprocess.run(list(ENABLED_COMMAND), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=MINIMAL_ENV, close_fds=True, timeout=5, check=False)
        return bool(active.returncode in {0, 1, 3} and active.stdout == b"inactive\n" and enabled.returncode in {0, 1} and enabled.stdout in {b"disabled\n", b"static\n"})
    except Exception: return False


def _arm_absent(*, api: Any = os) -> bool:
    parent = directory = None
    try:
        parent, name = _open_parent(STATE_DIR, api=api)
        directory = api.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        info = api.fstat(directory)
        if not (stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700 and info.st_nlink >= 1): return False
        try: api.stat(ARM_NAME, dir_fd=directory, follow_symlinks=False); return False
        except FileNotFoundError: return True
    except Exception: return False
    finally:
        for item in (directory, parent):
            if isinstance(item, int):
                try: api.close(item)
                except Exception: pass


def upgrade(*, execute: bool = False, helper_source: bytes | None = None, readback_source: bytes | None = None, operations: Operations | SecureHostOperations | None = None, authority: Callable[[], bool] = lambda: os.geteuid() == 0, unit_safe: Callable[[], bool] = _unit_safe, arm_absent: Callable[[], bool] = _arm_absent) -> dict[str, Any]:
    if execute is not True: return receipt("blocked", "execution_disabled")
    if not (type(helper_source) is bytes and type(readback_source) is bytes and 0 < len(helper_source) <= MAX_SOURCE_BYTES and 0 < len(readback_source) <= MAX_SOURCE_BYTES and hashlib.sha256(helper_source).hexdigest() == NEW_HELPER_SHA256 and hashlib.sha256(readback_source).hexdigest() == NEW_READBACK_SHA256): return receipt("blocked", "source_mismatch")
    ops = operations
    if ops is None or not authority() or not unit_safe() or not arm_absent(): return receipt("blocked", "preflight_failed")
    try:
        old_helper = ops.read_exact(HELPER_PATH, OLD_HELPER_SHA256, 0o700)
        old_readback = ops.read_exact(READBACK_PATH, OLD_READBACK_SHA256, 0o700)
        ops.read_exact(UNIT_PATH, UNIT_SHA256, 0o644); ops.read_exact(SUDOERS_PATH, SUDOERS_SHA256, 0o440)
    except Exception:
        return receipt("blocked", "preflight_failed")
    try:
        ops.replace_exact(READBACK_PATH, OLD_READBACK_SHA256, readback_source, NEW_READBACK_SHA256, 0o700)
    except PublicationUncertain:
        return receipt("unknown", "write_failed", invoked=True, effect=True, recovery=True)
    except Exception:
        return receipt("blocked", "write_failed")
    try:
        ops.replace_exact(HELPER_PATH, OLD_HELPER_SHA256, helper_source, NEW_HELPER_SHA256, 0o700)
    except PublicationUncertain:
        return receipt("unknown", "write_failed", invoked=True, readback=True, effect=True, recovery=True)
    except Exception:
        try:
            ops.replace_exact(READBACK_PATH, NEW_READBACK_SHA256, old_readback, OLD_READBACK_SHA256, 0o700)
            return receipt("rolled_back", "write_failed", invoked=True, rollback_attempted=True, rollback_succeeded=True, effect=True)
        except Exception:
            return receipt("unknown", "rollback_failed", invoked=True, readback=True, rollback_attempted=True, effect=True, recovery=True)
    try:
        if not unit_safe() or not arm_absent(): raise OSError()
        ops.read_exact(HELPER_PATH, NEW_HELPER_SHA256, 0o700); ops.read_exact(READBACK_PATH, NEW_READBACK_SHA256, 0o700)
        ops.read_exact(UNIT_PATH, UNIT_SHA256, 0o644); ops.read_exact(SUDOERS_PATH, SUDOERS_SHA256, 0o440)
    except Exception:
        return receipt("unknown", "postflight_failed", invoked=True, helper=True, readback=True, effect=True, recovery=True)
    return receipt("upgraded", "none", invoked=True, helper=True, readback=True, effect=True)


def main() -> int:
    print(json.dumps(receipt("blocked", "execution_disabled"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

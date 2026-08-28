#!/usr/bin/env python3
"""Incident-bound recovery for one failed root-helper backup attempt.

The recovery is deliberately narrower than the action which created the arm.
It accepts only a terminal failed unit, an expired arm whose provenance matches
the preserved redacted ``backup_failed`` receipt, and a matching one-use marker.
Only the proven arm inode is removed; the receipt and marker remain as audit
evidence.  A successful recovery still does not authorize a backup retry.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_recovery.v1"
PACKET_SCHEMA_ID = "odysseus.predeploy_backup_root_helper_recovery_packet.v1"
ARM_SCHEMA_ID = "odysseus.predeploy_backup_root_helper_arm.v1"
RESULT_SCHEMA_ID = "odysseus.redacted_predeploy_backup_creation.v1"
SNAPSHOT_SCHEMA_ID = "odysseus.redacted_backup_snapshot_observation.v1"
STATE_DIR = "/var/lib/odysseus-predeploy-backup-root-helper"
ARM_NAME = "arm.json"
USED_PREFIX = "used-"
RECEIPT_PATH = "/run/odysseus-predeploy-backup-root-helper/receipt.json"
UNIT = "odysseus-predeploy-backup-root-helper.service"
HELPER_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper.py"
READBACK_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py"
UNIT_PATH = "/etc/systemd/system/odysseus-predeploy-backup-root-helper.service"
SUDOERS_PATH = "/etc/sudoers.d/odysseus-predeploy-backup-root-helper"
HELPER_SHA256 = "dbcbac4c5a4b65edcc4d4facd9204674a8e9114179f406c88d067c7f96185a97"
READBACK_SHA256 = "8201653d392d1556a81f6ca236e9f5dd94b6d425dc03ae699f74280b4ae9b722"
UNIT_SHA256 = "466de2f889a00ee2759bd06380ddb213f8c0f4cee5644e3a2e27083863c1ab98"
SUDOERS_SHA256 = "1a6a7f1ec4d328c9fed20b758a87bb9905b68145122f6751fd5eeea748d5847d"
MAX_ASSET_BYTES = 400_000
MAX_ARM_BYTES = 1_024
MAX_RECEIPT_BYTES = 8_192
MAX_AUTHORIZATION_FUTURE_SECONDS = 600
MINIMAL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
RESET_COMMAND = ("/usr/bin/systemctl", "reset-failed", UNIT)
ACTIVE_COMMAND = ("/usr/bin/systemctl", "is-active", UNIT)
SHOW_COMMAND = (
    "/usr/bin/systemctl", "show", UNIT,
    "--property=ActiveState", "--property=SubState", "--property=Result",
    "--property=ExecMainCode", "--property=ExecMainStatus",
    "--property=MainPID", "--property=ControlPID", "--no-pager",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ACTION_REF = re.compile(r"^predeploy_backup_root_helper_v1:[0-9a-f]{64}$")
_VISIBILITY = frozenset({"raw_stdout_visible", "raw_stderr_visible", "environment_visible", "paths_visible", "hostnames_visible", "secret_values_visible"})
_CODES = frozenset({"none", "execution_disabled", "invalid_packet", "preflight_failed", "cleanup_failed", "reset_failed", "postflight_failed"})
_KEYS = frozenset({
    "schema_id", "status", "error_code", "recovery_invoked", "arm_removed",
    "unit_reset", "unit_inactive", "evidence_preserved", "effect_may_have_occurred",
    "manual_recovery_required", "retry_permitted", "raw_stdout_visible",
    "raw_stderr_visible", "environment_visible", "paths_visible", "hostnames_visible",
    "secret_values_visible", "evidence_sha256",
})
_PACKET_KEYS = frozenset({
    "schema_id", "authorization_id", "expires_at_epoch", "action_provenance_ref",
    "result_evidence_sha256", "snapshot_status", "snapshot_error_code",
    "snapshot_evidence_sha256",
})
_RESULT_KEYS = frozenset({
    "schema_id", "status", "error_code", "effect_may_have_occurred",
    "retry_permitted", "manual_recovery_required", "action_provenance_ref",
    "evidence_sha256",
})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def envelope(
    status: str,
    code: str,
    *,
    invoked: bool = False,
    arm_removed: bool = False,
    unit_reset: bool = False,
    unit_inactive: bool = False,
    evidence_preserved: bool = False,
    effect: bool = False,
    recovery: bool = False,
) -> dict[str, Any]:
    value = {
        "schema_id": SCHEMA_ID,
        "status": status if status in {"blocked", "recovered", "unknown"} else "unknown",
        "error_code": code if code in _CODES else "preflight_failed",
        "recovery_invoked": invoked is True,
        "arm_removed": arm_removed is True,
        "unit_reset": unit_reset is True,
        "unit_inactive": unit_inactive is True,
        "evidence_preserved": evidence_preserved is True,
        "effect_may_have_occurred": effect is True,
        "manual_recovery_required": recovery is True,
        "retry_permitted": False,
        "raw_stdout_visible": False,
        "raw_stderr_visible": False,
        "environment_visible": False,
        "paths_visible": False,
        "hostnames_visible": False,
        "secret_values_visible": False,
    }
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    if not (
        type(value) is dict
        and set(value) == _KEYS
        and value.get("schema_id") == SCHEMA_ID
        and value.get("error_code") in _CODES
        and value.get("retry_permitted") is False
        and all(value.get(key) is False for key in _VISIBILITY)
        and value.get("evidence_sha256") == _digest(value)
    ):
        return False
    if value["status"] == "blocked":
        return bool(
            value["error_code"] in {"execution_disabled", "invalid_packet", "preflight_failed"}
            and all(value[key] is False for key in ("recovery_invoked", "arm_removed", "unit_reset", "unit_inactive", "evidence_preserved", "effect_may_have_occurred", "manual_recovery_required"))
        )
    if value["status"] == "recovered":
        return bool(
            value["error_code"] == "none"
            and all(value[key] is True for key in ("recovery_invoked", "arm_removed", "unit_reset", "unit_inactive", "evidence_preserved", "effect_may_have_occurred"))
            and value["manual_recovery_required"] is False
        )
    return bool(
        value["status"] == "unknown"
        and value["error_code"] in {"cleanup_failed", "reset_failed", "postflight_failed"}
        and value["recovery_invoked"] is True
        and value["effect_may_have_occurred"] is True
        and value["manual_recovery_required"] is True
    )


def _packet_valid(value: Any, now_epoch: int) -> bool:
    return bool(
        type(value) is dict
        and set(value) == _PACKET_KEYS
        and value.get("schema_id") == PACKET_SCHEMA_ID
        and isinstance(value.get("authorization_id"), str)
        and _HEX64.fullmatch(value["authorization_id"])
        and type(value.get("expires_at_epoch")) is int
        and now_epoch < value["expires_at_epoch"] <= now_epoch + MAX_AUTHORIZATION_FUTURE_SECONDS
        and isinstance(value.get("action_provenance_ref"), str)
        and _ACTION_REF.fullmatch(value["action_provenance_ref"])
        and isinstance(value.get("result_evidence_sha256"), str)
        and _HEX64.fullmatch(value["result_evidence_sha256"])
        and value.get("snapshot_status") == "blocked"
        and value.get("snapshot_error_code") == "snapshot_stale"
        and isinstance(value.get("snapshot_evidence_sha256"), str)
        and _HEX64.fullmatch(value["snapshot_evidence_sha256"])
    )


def _safe_dir(info: Any) -> bool:
    return bool(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700 and info.st_nlink >= 1)


def _safe_parent_dir(info: Any) -> bool:
    return bool(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) in {0o755, 0o700} and info.st_nlink >= 1)


def _safe_file(info: Any, mode: int, maximum: int) -> bool:
    return bool(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1 and 0 < info.st_size <= maximum)


def _open_parent(path: str, *, api: Any = os) -> tuple[int, str]:
    parts = path.split("/")[1:]
    if not parts or any(not part or part in {".", ".."} for part in parts): raise OSError()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current = api.open("/", flags)
    try:
        if not _safe_parent_dir(api.fstat(current)) or stat.S_IMODE(api.fstat(current).st_mode) != 0o755: raise OSError()
        for part in parts[:-1]:
            following = api.open(part, flags, dir_fd=current)
            api.close(current); current = following
            if not _safe_parent_dir(api.fstat(current)): raise OSError()
        return current, parts[-1]
    except Exception:
        try: api.close(current)
        except Exception: pass
        raise


def _open_state_dir(*, api: Any = os) -> int:
    parent = directory = None
    try:
        parent, name = _open_parent(STATE_DIR, api=api)
        directory = api.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        if not _safe_dir(api.fstat(directory)): raise OSError()
        result = directory; directory = None
        return result
    finally:
        for descriptor in (directory, parent):
            if isinstance(descriptor, int):
                try: api.close(descriptor)
                except Exception: pass


def _asset_valid(path: str, expected: str, mode: int, *, api: Any = os) -> bool:
    parent = descriptor = None
    try:
        parent, name = _open_parent(path, api=api)
        descriptor = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        before = api.fstat(descriptor)
        identity = tuple(getattr(before, name, None) for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))
        if not _safe_file(before, mode, MAX_ASSET_BYTES): return False
        remaining = before.st_size; hasher = hashlib.sha256()
        while remaining:
            piece = api.read(descriptor, min(8192, remaining))
            if not piece or len(piece) > remaining: return False
            hasher.update(piece); remaining -= len(piece)
        after = api.fstat(descriptor)
        return bool(api.read(descriptor, 1) == b"" and tuple(getattr(after, name, None) for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")) == identity and after.st_nlink == 1 and hasher.hexdigest() == expected)
    except Exception:
        return False
    finally:
        for item in (descriptor, parent):
            if isinstance(item, int):
                try: api.close(item)
                except Exception: pass


def _read_exact_file(name: str, *, directory: int, mode: int, maximum: int, api: Any = os) -> tuple[dict[str, Any], Any]:
    descriptor = None
    try:
        descriptor = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        info = api.fstat(descriptor)
        if not _safe_file(info, mode, maximum): raise OSError()
        raw = bytearray()
        while len(raw) <= maximum:
            piece = api.read(descriptor, min(4096, maximum + 1 - len(raw)))
            if not piece: break
            raw.extend(piece)
        if len(raw) != info.st_size or len(raw) > maximum or api.read(descriptor, 1) != b"": raise OSError()
        return json.loads(bytes(raw).decode("ascii")), info
    finally:
        if isinstance(descriptor, int): api.close(descriptor)


def _result_valid(value: Any, packet: Mapping[str, Any]) -> bool:
    return bool(
        type(value) is dict
        and set(value) == _RESULT_KEYS
        and value.get("schema_id") == RESULT_SCHEMA_ID
        and value.get("status") == "unknown"
        and value.get("error_code") == "backup_failed"
        and value.get("effect_may_have_occurred") is True
        and value.get("retry_permitted") is False
        and value.get("manual_recovery_required") is True
        and value.get("action_provenance_ref") == packet["action_provenance_ref"]
        and value.get("evidence_sha256") == packet["result_evidence_sha256"]
        and value.get("evidence_sha256") == _digest(value)
    )


def _systemctl(command: tuple[str, ...], maximum: int = 512) -> tuple[int | None, bytes]:
    try:
        result = subprocess.run(list(command), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=MINIMAL_ENV, close_fds=True, timeout=5, check=False)
        output = result.stdout
        if type(output) is not bytes or len(output) > maximum: return None, b""
        return result.returncode if type(result.returncode) is int else None, output
    except Exception:
        return None, b""


def _unit_terminal_failed() -> bool:
    code, raw = _systemctl(SHOW_COMMAND)
    if code != 0 or b"\r" in raw: return False
    try:
        rows = raw.decode("ascii").splitlines()
        fields = dict(row.split("=", 1) for row in rows if row.count("=") == 1)
    except Exception:
        return False
    return fields == {"Result": "exit-code", "ExecMainCode": "1", "ExecMainStatus": "1", "MainPID": "0", "ControlPID": "0", "ActiveState": "failed", "SubState": "failed"}


def _unit_inactive() -> bool:
    code, raw = _systemctl(ACTIVE_COMMAND, 32)
    return code in {0, 1, 3} and raw == b"inactive\n"


def _reset_unit() -> bool:
    code, raw = _systemctl(RESET_COMMAND, 32)
    return code == 0 and raw == b""


@dataclass(frozen=True)
class RecoveryToken:
    device: int
    inode: int
    grant_id: str


def _read_used(directory: int, grant_id: str, *, api: Any = os) -> bool:
    descriptor = None
    try:
        descriptor = api.open(USED_PREFIX + grant_id, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        info = api.fstat(descriptor)
        return bool(_safe_file(info, 0o600, 16) and info.st_size == 5 and api.read(descriptor, 6) == b"used\n" and api.read(descriptor, 1) == b"")
    except Exception:
        return False
    finally:
        if isinstance(descriptor, int):
            try: api.close(descriptor)
            except Exception: pass


def _read_receipt(packet: Mapping[str, Any], *, api: Any = os) -> bool:
    parent = descriptor = None
    try:
        parent, name = _open_parent(RECEIPT_PATH, api=api)
        descriptor = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        info = api.fstat(descriptor)
        if not _safe_file(info, 0o600, MAX_RECEIPT_BYTES): return False
        raw = bytearray()
        while len(raw) <= MAX_RECEIPT_BYTES:
            piece = api.read(descriptor, min(4096, MAX_RECEIPT_BYTES + 1 - len(raw)))
            if not piece: break
            raw.extend(piece)
        return bool(len(raw) == info.st_size and len(raw) <= MAX_RECEIPT_BYTES and api.read(descriptor, 1) == b"" and _result_valid(json.loads(bytes(raw).decode("ascii")), packet))
    except Exception:
        return False
    finally:
        for item in (descriptor, parent):
            if isinstance(item, int):
                try: api.close(item)
                except Exception: pass


def _incident_preflight(packet: Mapping[str, Any], now_epoch: int, *, api: Any = os) -> RecoveryToken | None:
    if getattr(api, "geteuid", lambda: -1)() != 0: return None
    assets = ((HELPER_PATH, HELPER_SHA256, 0o700), (READBACK_PATH, READBACK_SHA256, 0o700), (UNIT_PATH, UNIT_SHA256, 0o644), (SUDOERS_PATH, SUDOERS_SHA256, 0o440))
    if not all(_asset_valid(path, digest, mode, api=api) for path, digest, mode in assets): return None
    directory = None
    try:
        directory = _open_state_dir(api=api)
        arm, info = _read_exact_file(ARM_NAME, directory=directory, mode=0o600, maximum=MAX_ARM_BYTES, api=api)
        if not (type(arm) is dict and set(arm) == {"schema_id", "grant_id", "expires_at_epoch", "helper_sha256"} and arm.get("schema_id") == ARM_SCHEMA_ID and isinstance(arm.get("grant_id"), str) and _HEX64.fullmatch(arm["grant_id"]) and type(arm.get("expires_at_epoch")) is int and arm["expires_at_epoch"] <= now_epoch and arm.get("helper_sha256") == HELPER_SHA256): return None
        reference = "predeploy_backup_root_helper_v1:" + hashlib.sha256((arm["grant_id"] + HELPER_SHA256).encode("ascii")).hexdigest()
        if reference != packet["action_provenance_ref"] or not _read_used(directory, arm["grant_id"], api=api): return None
        token = RecoveryToken(int(info.st_dev), int(info.st_ino), arm["grant_id"])
    except Exception:
        return None
    finally:
        if isinstance(directory, int):
            try: api.close(directory)
            except Exception: pass
    return token if _read_receipt(packet, api=api) and _unit_terminal_failed() else None


def _remove_arm(token: RecoveryToken, *, api: Any = os) -> bool:
    directory = None
    try:
        directory = _open_state_dir(api=api)
        info = api.stat(ARM_NAME, dir_fd=directory, follow_symlinks=False)
        if not (_safe_file(info, 0o600, MAX_ARM_BYTES) and (int(info.st_dev), int(info.st_ino)) == (token.device, token.inode)): return False
        api.unlink(ARM_NAME, dir_fd=directory); api.fsync(directory)
        try: api.stat(ARM_NAME, dir_fd=directory, follow_symlinks=False); return False
        except FileNotFoundError: return True
    except Exception:
        return False
    finally:
        if isinstance(directory, int):
            try: api.close(directory)
            except Exception: pass


def _evidence_preserved(packet: Mapping[str, Any], token: RecoveryToken, *, api: Any = os) -> bool:
    directory = None
    try:
        directory = _open_state_dir(api=api)
        if not _read_used(directory, token.grant_id, api=api): return False
        try: api.stat(ARM_NAME, dir_fd=directory, follow_symlinks=False); return False
        except FileNotFoundError: pass
        return _read_receipt(packet, api=api)
    except Exception:
        return False
    finally:
        if isinstance(directory, int):
            try: api.close(directory)
            except Exception: pass


def perform(
    packet: Any = None,
    *,
    execute: bool = False,
    now: Callable[[], float] = time.time,
    preflight: Callable[[Mapping[str, Any], int], RecoveryToken | None] = _incident_preflight,
    remove_arm: Callable[[RecoveryToken], bool] = _remove_arm,
    reset_unit: Callable[[], bool] = _reset_unit,
    unit_inactive: Callable[[], bool] = _unit_inactive,
    evidence_preserved: Callable[[Mapping[str, Any], RecoveryToken], bool] = _evidence_preserved,
) -> dict[str, Any]:
    if execute is not True: return envelope("blocked", "execution_disabled")
    current = int(now())
    if not _packet_valid(packet, current): return envelope("blocked", "invalid_packet")
    token = preflight(packet, current)
    if token is None: return envelope("blocked", "preflight_failed")
    if not remove_arm(token): return envelope("unknown", "cleanup_failed", invoked=True, effect=True, recovery=True)
    if not reset_unit(): return envelope("unknown", "reset_failed", invoked=True, arm_removed=True, effect=True, recovery=True)
    inactive = unit_inactive()
    preserved = evidence_preserved(packet, token)
    if not inactive or not preserved:
        return envelope("unknown", "postflight_failed", invoked=True, arm_removed=True, unit_reset=True, unit_inactive=inactive, evidence_preserved=preserved, effect=True, recovery=True)
    return envelope("recovered", "none", invoked=True, arm_removed=True, unit_reset=True, unit_inactive=True, evidence_preserved=True, effect=True)


def main() -> int:
    print(json.dumps(envelope("blocked", "execution_disabled"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

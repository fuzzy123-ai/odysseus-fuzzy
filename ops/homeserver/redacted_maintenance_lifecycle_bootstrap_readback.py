#!/usr/bin/env python3
"""Fixed bounded readback for the one-use maintenance bootstrap service."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import time
from typing import Any, Callable, Mapping

try:
    import grp
except ImportError:
    grp = None
try:
    import pwd
except ImportError:
    pwd = None


SCHEMA_ID = "odysseus.redacted_maintenance_lifecycle_bootstrap_readback.v1"
ARM_SCHEMA_ID = "odysseus.redacted_maintenance_lifecycle_bootstrap_arm.v1"
RECEIPT_SCHEMA_ID = "odysseus.redacted_maintenance_lifecycle_bootstrap_receipt.v1"
ASSET_DIRECTORY = "/usr/local/libexec/odysseus-maintenance-bootstrap"
STATE_DIRECTORY = "/var/lib/odysseus-maintenance-bootstrap"
SHARED_DIRECTORY = "/var/lib/odysseus-maintenance"
BACKUP_STATE_DIRECTORY = "/var/lib/odysseus-predeploy-backup-root-helper"
SERVICE_UNIT = "odysseus-maintenance-lifecycle-bootstrap.service"
BACKUP_UNIT = "odysseus-predeploy-backup-root-helper.service"
ASSET_NAMES = ("lifecycle.py", "bootstrap.py", "supervisor.py", "readback.py")
ARM_NAME = "arm.json"
CONSUMED_NAME = "consumed.json"
RECEIPT_NAME = "receipt.json"
MAX_JSON_BYTES = 8192
MAX_ASSET_BYTES = 350_000
_KEYS = frozenset({"schema_id", "status", "error_code", "manual_recovery_required", "retry_permitted", "units", "asset_pins_valid", "arm", "consumed", "receipt", "state_validity", "shared_metadata", "temporary_absent", "raw_output_visible", "secret_values_visible", "evidence_sha256"})
_ENVELOPE_CODES = frozenset({"ready_to_start", "bootstrap_completed", "unit_not_quiescent", "state_mismatch_or_consumed", "not_armed"})
_ACTIVE_STATES = frozenset({"active", "reloading", "inactive", "failed", "activating", "deactivating"})
_SUB_STATES = frozenset({"dead", "running", "exited", "failed", "start-pre", "start", "start-post", "stop", "stop-sigterm", "stop-sigkill", "stop-post", "final-sigterm", "final-sigkill", "auto-restart", "condition", "reload", "maintenance", "refreshing"})
_RECEIPT_CODES = frozenset({"none", "execution_disabled", "identity_unavailable", "lock_busy", "unit_not_quiescent", "unsafe_existing_object", "publication_failed_before_exposure", "publication_ambiguous", "postflight_not_quiescent"})
_METADATA_FIELDS = frozenset({"directory_uid", "directory_gid", "directory_mode", "lock_uid", "lock_gid", "lock_mode", "lock_nlink"})


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "evidence_sha256"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError
        result[key] = item
    return result


def _json(raw: bytes, limit: int = MAX_JSON_BYTES) -> Any:
    if type(raw) is not bytes or not raw or len(raw) > limit:
        raise ValueError
    return json.loads(raw.decode("ascii"), object_pairs_hook=_unique_pairs)


def _id(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _safe_dir(info: Any, mode: int | None = None, *, uid: int = 0, gid: int = 0) -> bool:
    return stat.S_ISDIR(info.st_mode) and info.st_uid == uid and info.st_gid == gid and info.st_nlink >= 1 and (stat.S_IMODE(info.st_mode) == mode if mode is not None else stat.S_IMODE(info.st_mode) & 0o022 == 0)


def _safe_file(info: Any, mode: int) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1


def _homebase_private_gid(*, users: Any = pwd, groups: Any = grp) -> tuple[int, int]:
    if users is None or groups is None:
        raise OSError("POSIX account databases unavailable")
    account = users.getpwnam("homebase")
    uid, gid = int(account.pw_uid), int(account.pw_gid)
    if uid <= 0 or gid <= 0:
        raise OSError("unsafe shared account identity")
    group = groups.getgrgid(gid)
    if any(member != "homebase" for member in tuple(group.gr_mem)):
        raise OSError("shared account group is not private")
    if any(other.pw_name != "homebase" and int(other.pw_gid) == gid for other in users.getpwall()):
        raise OSError("shared account primary group is not private")
    return uid, gid


def _open_dir(path: str, *, root: str = "/", api: Any = os, final_uid: int = 0, final_gid: int = 0) -> int:
    if type(path) is not str or not path.startswith("/") or "//" in path or ".." in path.split("/"):
        raise OSError("unsafe fixed path")
    fd = api.open(root, api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW | api.O_CLOEXEC)
    try:
        components = tuple(filter(None, path.split("/")))
        for index, part in enumerate(components):
            child = api.open(part, api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW | api.O_CLOEXEC, dir_fd=fd)
            api.close(fd)
            fd = child
            expected_uid, expected_gid = (final_uid, final_gid) if index == len(components) - 1 else (0, 0)
            if not _safe_dir(api.fstat(fd), uid=expected_uid, gid=expected_gid):
                raise OSError("unsafe fixed directory")
        return fd
    except BaseException:
        api.close(fd)
        raise


def _read(fd: int, name: str, *, mode: int = 0o600, limit: int = MAX_JSON_BYTES) -> bytes:
    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not _safe_file(before, mode):
        raise OSError("unsafe fixed file")
    opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
    try:
        held = os.fstat(opened)
        named = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino) or (held.st_dev, held.st_ino) != (before.st_dev, before.st_ino) or not _safe_file(held, mode):
            raise OSError("fixed file changed")
        output = bytearray()
        while len(output) <= limit:
            part = os.read(opened, min(65536, limit + 1 - len(output)))
            if not part:
                break
            output.extend(part)
        if len(output) > limit:
            raise OSError("fixed file too large")
        return bytes(output)
    finally:
        os.close(opened)


def _asset_pins() -> dict[str, str]:
    fd = _open_dir(ASSET_DIRECTORY)
    try:
        if not _safe_dir(os.fstat(fd), 0o755):
            raise OSError("unsafe asset package")
        result: dict[str, str] = {}
        for name in ASSET_NAMES:
            result[name] = _sha(_read(fd, name, mode=0o700, limit=MAX_ASSET_BYTES))
        return result
    finally:
        os.close(fd)


def _parse_unit(raw: Any, returncode: Any) -> dict[str, Any] | None:
    if type(returncode) is not int or returncode != 0 or type(raw) is not bytes or len(raw) > 512 or not raw.isascii():
        return None
    lines = raw.splitlines()
    expected = {"LoadState", "ActiveState", "SubState", "MainPID", "ControlPID"}
    pairs: dict[str, str] = {}
    for line in lines:
        if line.count(b"=") != 1:
            return None
        key, value = line.decode("ascii").split("=", 1)
        if key in pairs:
            return None
        pairs[key] = value
    if set(pairs) != expected or pairs["LoadState"] != "loaded" or pairs["ActiveState"] not in _ACTIVE_STATES or pairs["SubState"] not in _SUB_STATES or not pairs["MainPID"].isdigit() or not pairs["ControlPID"].isdigit():
        return None
    pid = int(pairs["MainPID"])
    control_pid = int(pairs["ControlPID"])
    if pid > 2_147_483_647 or control_pid > 2_147_483_647:
        return None
    return {"loaded": True, "active_state": pairs["ActiveState"], "sub_state": pairs["SubState"], "main_pid": pid, "control_pid": control_pid, "inactive_dead": pairs["ActiveState"] == "inactive" and pairs["SubState"] == "dead" and pid == 0 and control_pid == 0}


def _unit(unit: str, runner: Callable[..., Any]) -> dict[str, Any] | None:
    try:
        result = runner(("/usr/bin/systemctl", "show", "--no-pager", "--property=LoadState", "--property=ActiveState", "--property=SubState", "--property=MainPID", "--property=ControlPID", unit), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False, timeout=5, check=False)
        return _parse_unit(getattr(result, "stdout", None), getattr(result, "returncode", None))
    except BaseException:
        return None


def _metadata(*, root: str = "/", api: Any = os, users: Any = None, groups: Any = None) -> dict[str, Any] | None:
    account_args = {} if users is None and groups is None else {"users": users, "groups": groups}
    try:
        _uid, shared_gid = _homebase_private_gid(**account_args)
        directory_fd = _open_dir(SHARED_DIRECTORY, root=root, api=api, final_uid=0, final_gid=shared_gid)
    except FileNotFoundError:
        return None
    except BaseException:
        return {"valid": False}
    try:
        info = api.fstat(directory_fd)
        lock = api.stat("lifecycle.lock", dir_fd=directory_fd, follow_symlinks=False)
        if not _safe_dir(info, 0o750, uid=0, gid=shared_gid) or shared_gid <= 0 or not stat.S_ISREG(lock.st_mode) or lock.st_uid != 0 or lock.st_gid != shared_gid or stat.S_IMODE(lock.st_mode) != 0o660 or lock.st_nlink != 1:
            return {"valid": False}
        return {"valid": True, "directory_uid": info.st_uid, "directory_gid": info.st_gid, "directory_mode": stat.S_IMODE(info.st_mode), "lock_uid": lock.st_uid, "lock_gid": lock.st_gid, "lock_mode": stat.S_IMODE(lock.st_mode), "lock_nlink": lock.st_nlink}
    except BaseException:
        return {"valid": False}
    finally:
        api.close(directory_fd)


def _state() -> tuple[dict[str, Any], bool]:
    fd = _open_dir(STATE_DIRECTORY)
    try:
        if not _safe_dir(os.fstat(fd), 0o700):
            raise OSError("unsafe state directory")
        names = set(os.listdir(fd))
        temporary_absent = not any(name.startswith((".stage-", ".arm-", ".receipt-", "tmp-")) for name in names)
        allowed = {"lifecycle.lock", "arm.json", "consumed.json", "receipt.json"}
        if not names <= allowed:
            temporary_absent = False
        result: dict[str, Any] = {}
        for key, name in (("arm", ARM_NAME), ("consumed", CONSUMED_NAME), ("receipt", RECEIPT_NAME)):
            if name not in names:
                result[key] = None
                continue
            raw = _read(fd, name)
            result[key] = _json(raw)
        return result, temporary_absent
    finally:
        os.close(fd)


def _valid_arm(value: Any) -> bool:
    if type(value) is not dict or set(value) != {"schema_id", "grant_id", "invocation_id", "expires_at", "asset_pins"}:
        return False
    if value["schema_id"] != ARM_SCHEMA_ID or not _id(value["grant_id"]) or not _id(value["invocation_id"]):
        return False
    if type(value["expires_at"]) is not int or not int(time.time()) <= value["expires_at"] <= int(time.time()) + 600:
        return False
    pins = value["asset_pins"]
    return type(pins) is dict and set(pins) == set(ASSET_NAMES) and all(_id(pin) for pin in pins.values())


def _valid_consumed(value: Any) -> bool:
    if type(value) is not dict or set(value) != {"schema_id", "grant_id", "invocation_id", "expires_at", "asset_pins"}:
        return False
    return value["schema_id"] == ARM_SCHEMA_ID and _id(value["grant_id"]) and _id(value["invocation_id"]) and type(value["expires_at"]) is int and 0 <= value["expires_at"] <= 4_102_444_800 and type(value["asset_pins"]) is dict and set(value["asset_pins"]) == set(ASSET_NAMES) and all(_id(pin) for pin in value["asset_pins"].values())


def _valid_metadata(value: Any, *, include_valid: bool) -> bool:
    if type(value) is not dict:
        return False
    expected = _METADATA_FIELDS | ({"valid"} if include_valid else set())
    if set(value) != expected:
        return False
    if include_valid and value["valid"] is not True:
        return False
    limits = {"directory_uid": 2_147_483_647, "directory_gid": 2_147_483_647, "directory_mode": 0o777, "lock_uid": 2_147_483_647, "lock_gid": 2_147_483_647, "lock_mode": 0o777, "lock_nlink": 1}
    return all(type(value[key]) is int and 0 <= value[key] <= maximum for key, maximum in limits.items()) and value["directory_uid"] == value["lock_uid"] == 0 and value["directory_mode"] == 0o750 and value["lock_gid"] == value["directory_gid"] and value["lock_mode"] == 0o660 and value["lock_nlink"] == 1 and value["directory_gid"] > 0


def _valid_receipt(value: Any, consumed: Any) -> bool:
    if type(value) is not dict or set(value) != {"schema_id", "grant_id", "invocation_id", "asset_pins", "outcome", "error_code", "shared_metadata", "recorded_at", "receipt_sha256"}:
        return False
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    return value["schema_id"] == RECEIPT_SCHEMA_ID and _id(value["grant_id"]) and _id(value["invocation_id"]) and type(value["asset_pins"]) is dict and set(value["asset_pins"]) == set(ASSET_NAMES) and all(_id(pin) for pin in value["asset_pins"].values()) and type(value["outcome"]) is str and value["outcome"] in {"ready", "blocked", "unknown"} and type(value["error_code"]) is str and value["error_code"] in _RECEIPT_CODES and type(value["recorded_at"]) is int and 0 <= value["recorded_at"] <= 4_102_444_800 and value["receipt_sha256"] == _sha(_canonical(body)) and type(consumed) is dict and value["grant_id"] == consumed.get("grant_id") and value["invocation_id"] == consumed.get("invocation_id") and value["asset_pins"] == consumed.get("asset_pins") and ((value["outcome"] == "ready" and value["error_code"] == "none" and _valid_metadata(value["shared_metadata"], include_valid=False)) or (value["outcome"] != "ready" and value["shared_metadata"] is None))


def _envelope(status: str, code: str, *, units: Mapping[str, Any], pins_valid: bool, state: Mapping[str, Any], metadata: Any, temporary_absent: bool) -> dict[str, Any]:
    arm = state.get("arm")
    consumed = state.get("consumed")
    receipt = state.get("receipt")
    projected_units = {unit: _project_unit(units.get(unit)) for unit in (SERVICE_UNIT, BACKUP_UNIT)}
    projected_metadata = _project_metadata(metadata)
    projected_arm = _project_arm(arm)
    projected_consumed = _project_consumed(consumed)
    projected_receipt = _project_receipt(receipt, consumed)
    validity = {"arm_present": arm is not None, "arm_valid": arm is None or _valid_arm(arm), "consumed_present": consumed is not None, "consumed_valid": consumed is None or _valid_consumed(consumed), "receipt_present": receipt is not None, "receipt_valid": receipt is None or _valid_receipt(receipt, consumed)}
    value = {"schema_id": SCHEMA_ID, "status": status, "error_code": code, "manual_recovery_required": status == "manual_recovery_required", "retry_permitted": False, "units": projected_units, "asset_pins_valid": pins_valid, "arm": projected_arm, "consumed": projected_consumed, "receipt": projected_receipt, "state_validity": validity, "shared_metadata": projected_metadata, "temporary_absent": temporary_absent, "raw_output_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def _project_unit(value: Any) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != {"loaded", "active_state", "sub_state", "main_pid", "control_pid", "inactive_dead"}:
        return None
    if value["loaded"] is not True or type(value["active_state"]) is not str or value["active_state"] not in _ACTIVE_STATES or type(value["sub_state"]) is not str or value["sub_state"] not in _SUB_STATES or type(value["main_pid"]) is not int or not 0 <= value["main_pid"] <= 2_147_483_647 or type(value["control_pid"]) is not int or not 0 <= value["control_pid"] <= 2_147_483_647 or type(value["inactive_dead"]) is not bool:
        return None
    expected_quiet = value["active_state"] == "inactive" and value["sub_state"] == "dead" and value["main_pid"] == 0 and value["control_pid"] == 0
    if value["inactive_dead"] != expected_quiet:
        return None
    return {key: value[key] for key in ("loaded", "active_state", "sub_state", "main_pid", "control_pid", "inactive_dead")}


def _project_arm(value: Any) -> dict[str, Any] | None:
    if not _valid_arm(value):
        return None
    return {key: value[key] for key in ("schema_id", "grant_id", "invocation_id", "expires_at", "asset_pins")}


def _project_consumed(value: Any) -> dict[str, Any] | None:
    if not _valid_consumed(value):
        return None
    return {key: value[key] for key in ("schema_id", "grant_id", "invocation_id", "expires_at", "asset_pins")}


def _project_receipt(value: Any, consumed: Any) -> dict[str, Any] | None:
    if not _valid_receipt(value, consumed):
        return None
    return {key: value[key] for key in ("schema_id", "grant_id", "invocation_id", "asset_pins", "outcome", "error_code", "shared_metadata", "recorded_at", "receipt_sha256")}


def _project_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not _valid_metadata(value, include_valid=True):
        return {"valid": False}
    return {key: value[key] for key in ("valid", *_METADATA_FIELDS)}


def validate_envelope(value: Any) -> bool:
    if type(value) is not dict or set(value) != _KEYS or value.get("schema_id") != SCHEMA_ID:
        return False
    if type(value.get("status")) is not str or value["status"] not in {"armed", "completed", "manual_recovery_required", "blocked"} or type(value.get("error_code")) is not str or value["error_code"] not in _ENVELOPE_CODES:
        return False
    for key in ("manual_recovery_required", "retry_permitted", "asset_pins_valid", "temporary_absent", "raw_output_visible", "secret_values_visible"):
        if type(value.get(key)) is not bool:
            return False
    if value["manual_recovery_required"] != (value["status"] == "manual_recovery_required") or value["retry_permitted"] is not False or value["raw_output_visible"] or value["secret_values_visible"]:
        return False
    if type(value.get("units")) is not dict or set(value["units"]) != {SERVICE_UNIT, BACKUP_UNIT}:
        return False
    if any(unit is not None and _project_unit(unit) != unit for unit in value["units"].values()):
        return False
    if value["arm"] is not None and _project_arm(value["arm"]) != value["arm"]:
        return False
    if value["consumed"] is not None and _project_consumed(value["consumed"]) != value["consumed"]:
        return False
    if value["receipt"] is not None and _project_receipt(value["receipt"], value["consumed"]) != value["receipt"]:
        return False
    validity = value.get("state_validity")
    validity_keys = {"arm_present", "arm_valid", "consumed_present", "consumed_valid", "receipt_present", "receipt_valid"}
    if type(validity) is not dict or set(validity) != validity_keys or any(type(item) is not bool for item in validity.values()):
        return False
    for name in ("arm", "consumed", "receipt"):
        if validity[name + "_present"] != (value[name] is not None) and validity[name + "_valid"]:
            return False
        if validity[name + "_valid"] != (not validity[name + "_present"] or value[name] is not None):
            return False
    if value["shared_metadata"] is not None and _project_metadata(value["shared_metadata"]) != value["shared_metadata"]:
        return False
    quiet = all(type(unit) is dict and unit.get("inactive_dead") is True and unit.get("main_pid") == 0 and unit.get("control_pid") == 0 for unit in value["units"].values())
    if value["status"] == "armed":
        expected_validity = {"arm_present": True, "arm_valid": True, "consumed_present": False, "consumed_valid": True, "receipt_present": False, "receipt_valid": True}
        if value["error_code"] != "ready_to_start" or not quiet or value["asset_pins_valid"] is not True or value["arm"] is None or value["consumed"] is not None or value["receipt"] is not None or validity != expected_validity or value["shared_metadata"] is not None or value["temporary_absent"] is not True:
            return False
    elif value["status"] == "completed":
        receipt = value["receipt"]
        metadata = value["shared_metadata"]
        expected_validity = {"arm_present": False, "arm_valid": True, "consumed_present": True, "consumed_valid": True, "receipt_present": True, "receipt_valid": True}
        if value["error_code"] != "bootstrap_completed" or not quiet or value["asset_pins_valid"] is not True or value["arm"] is not None or value["consumed"] is None or validity != expected_validity or type(receipt) is not dict or receipt.get("outcome") != "ready" or receipt.get("error_code") != "none" or type(metadata) is not dict or metadata.get("valid") is not True or receipt.get("shared_metadata") != {key: item for key, item in metadata.items() if key != "valid"} or value["temporary_absent"] is not True:
            return False
    elif value["status"] == "manual_recovery_required":
        if value["error_code"] == "unit_not_quiescent":
            if quiet:
                return False
        elif value["error_code"] == "state_mismatch_or_consumed":
            if not quiet or not (
                any(validity[key] for key in ("arm_present", "consumed_present", "receipt_present"))
                or value["temporary_absent"] is False
                or value["shared_metadata"] is not None
            ):
                return False
        else:
            return False
    else:
        absent_validity = {
            "arm_present": False,
            "arm_valid": True,
            "consumed_present": False,
            "consumed_valid": True,
            "receipt_present": False,
            "receipt_valid": True,
        }
        if (
            value["error_code"] != "not_armed"
            or not quiet
            or value["asset_pins_valid"] is not False
            or value["arm"] is not None
            or value["consumed"] is not None
            or value["receipt"] is not None
            or validity != absent_validity
            or value["shared_metadata"] is not None
            or value["temporary_absent"] is not True
        ):
            return False
    if type(value.get("evidence_sha256")) is not str or value["evidence_sha256"] != _digest(value):
        return False
    return True


def readback(*, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    units = {SERVICE_UNIT: _unit(SERVICE_UNIT, runner), BACKUP_UNIT: _unit(BACKUP_UNIT, runner)}
    try:
        state, temporary_absent = _state()
    except BaseException:
        state, temporary_absent = {"arm": None, "consumed": None, "receipt": None}, False
    arm, consumed, receipt = state.get("arm"), state.get("consumed"), state.get("receipt")
    metadata = _metadata()
    all_quiet = all(type(unit) is dict and unit.get("inactive_dead") is True for unit in units.values())
    pins: Any = arm.get("asset_pins") if type(arm) is dict else consumed.get("asset_pins") if type(consumed) is dict else receipt.get("asset_pins") if type(receipt) is dict else None
    try:
        pins_valid = type(pins) is dict and set(pins) == set(ASSET_NAMES) and pins == _asset_pins()
    except BaseException:
        pins_valid = False
    arm_valid = _valid_arm(arm)
    consumed_valid = _valid_consumed(consumed)
    receipt_valid = _valid_receipt(receipt, consumed)
    if all_quiet and pins_valid and arm_valid and consumed is None and receipt is None and metadata is None and temporary_absent:
        value = _envelope("armed", "ready_to_start", units=units, pins_valid=True, state=state, metadata=metadata, temporary_absent=True)
    elif all_quiet and pins_valid and arm is None and consumed_valid and receipt_valid and type(receipt) is dict and receipt.get("outcome") == "ready" and receipt.get("error_code") == "none" and type(metadata) is dict and metadata.get("valid") is True and receipt.get("shared_metadata") == {key: metadata[key] for key in metadata if key != "valid"} and temporary_absent:
        value = _envelope("completed", "bootstrap_completed", units=units, pins_valid=True, state=state, metadata=metadata, temporary_absent=True)
    elif not all_quiet:
        value = _envelope("manual_recovery_required", "unit_not_quiescent", units=units, pins_valid=pins_valid, state=state, metadata=metadata, temporary_absent=temporary_absent)
    elif arm is not None or consumed is not None or receipt is not None or not temporary_absent or metadata is not None:
        value = _envelope("manual_recovery_required", "state_mismatch_or_consumed", units=units, pins_valid=pins_valid, state=state, metadata=metadata, temporary_absent=temporary_absent)
    else:
        value = _envelope("blocked", "not_armed", units=units, pins_valid=pins_valid, state=state, metadata=metadata, temporary_absent=temporary_absent)
    return value


def main() -> int:
    value = readback()
    print(_canonical(value).decode("ascii"))
    return 0 if validate_envelope(value) and value["status"] in {"armed", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

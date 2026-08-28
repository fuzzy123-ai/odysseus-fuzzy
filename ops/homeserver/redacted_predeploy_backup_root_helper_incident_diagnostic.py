#!/usr/bin/env python3
"""Read-only, fixed-incident diagnostic for a blocked root-helper recovery.

The module is deliberately tied to one existing abf8 root-helper incident.  It
does not accept paths, commands, credentials, or incident identifiers from a
caller, and it never mutates the host.  Its result exposes only the minimal
boolean preflight classes needed to decide whether a later recovery contract
must be corrected; raw receipt and systemd output stay on the host.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_incident_diagnostic.v1"
ARM_SCHEMA_ID = "odysseus.predeploy_backup_root_helper_arm.v1"
RESULT_SCHEMA_ID = "odysseus.redacted_predeploy_backup_creation.v1"
STATE_DIR = "/var/lib/odysseus-predeploy-backup-root-helper"
ARM_PATH = STATE_DIR + "/arm.json"
RECEIPT_PATH = "/run/odysseus-predeploy-backup-root-helper/receipt.json"
UNIT = "odysseus-predeploy-backup-root-helper.service"
HELPER_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper.py"
READBACK_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py"
UNIT_PATH = "/etc/systemd/system/odysseus-predeploy-backup-root-helper.service"
SUDOERS_PATH = "/etc/sudoers.d/odysseus-predeploy-backup-root-helper"
HELPER_SHA256 = "abf8f859384a9ab21d2c5fb682aabaaff522464eef5d035126065021de373d31"
READBACK_SHA256 = "e647b6f1faa409f42cbeb80c74826b730695d9e0fad14cf47aa22c1a59a0a046"
UNIT_SHA256 = "466de2f889a00ee2759bd06380ddb213f8c0f4cee5644e3a2e27083863c1ab98"
SUDOERS_SHA256 = "1a6a7f1ec4d328c9fed20b758a87bb9905b68145122f6751fd5eeea748d5847d"
ACTION_PROVENANCE_REF = "predeploy_backup_root_helper_v1:d9c73a2cebba7209eb3342cf9ef749d0feed3e11f7c3c9d305ccc739cdf17a33"
RESULT_EVIDENCE_SHA256 = "76a2f37841b779b25b3e27b3862f5ebdcba20358e18d026edef28503c4abc05e"
MAX_ASSET_BYTES = 400_000
MAX_ARM_BYTES = 1_024
MAX_RECEIPT_BYTES = 8_192
MINIMAL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
SHOW_COMMAND = (
    "/usr/bin/systemctl", "show", UNIT,
    "--property=ActiveState", "--property=SubState", "--property=Result",
    "--property=ExecMainCode", "--property=ExecMainStatus",
    "--property=MainPID", "--property=ControlPID", "--no-pager",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_VISIBILITY = frozenset({"raw_stdout_visible", "raw_stderr_visible", "environment_visible", "paths_visible", "hostnames_visible", "secret_values_visible"})
_FLAGS = (
    "privilege_root", "assets_intact", "state_directory_accessible",
    "arm_present", "arm_shape_valid", "arm_expired", "arm_helper_matches",
    "action_provenance_matches", "used_marker_present", "receipt_matches",
    "unit_terminal_failed", "recovery_preflight_ready",
)
_KEYS = frozenset({"schema_id", "status", "error_code", *_FLAGS, *_VISIBILITY, "evidence_sha256"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({key: value[key] for key in value if key != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _envelope(status: str, code: str, flags: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "schema_id": SCHEMA_ID,
        "status": status if status in {"ok", "blocked"} else "blocked",
        "error_code": code if code in {"none", "execution_disabled", "diagnostic_failed"} else "diagnostic_failed",
        **{key: False for key in _FLAGS},
        **{key: False for key in _VISIBILITY},
    }
    if status == "ok" and isinstance(flags, Mapping):
        for key in _FLAGS:
            result[key] = flags.get(key) is True
    result["evidence_sha256"] = _digest(result)
    return result


def validate_envelope(value: Any) -> bool:
    if not (type(value) is dict and set(value) == _KEYS and value.get("schema_id") == SCHEMA_ID and value.get("status") in {"ok", "blocked"} and value.get("error_code") in {"none", "execution_disabled", "diagnostic_failed"} and all(value.get(key) is False for key in _VISIBILITY) and value.get("evidence_sha256") == _digest(value)):
        return False
    if value["status"] == "blocked":
        return value["error_code"] in {"execution_disabled", "diagnostic_failed"} and all(value[key] is False for key in _FLAGS)
    return value["error_code"] == "none" and all(type(value[key]) is bool for key in _FLAGS) and value["recovery_preflight_ready"] is (all(value[key] is True for key in _FLAGS[:-1]))


def _safe_file(info: Any, mode: int, maximum: int) -> bool:
    return bool(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1 and 0 < info.st_size <= maximum)


def _safe_directory(info: Any) -> bool:
    return bool(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700 and info.st_nlink >= 1)


def _read_regular(path: str, mode: int, maximum: int, *, api: Any = os) -> bytes | None:
    descriptor = None
    try:
        descriptor = api.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = api.fstat(descriptor)
        if not _safe_file(info, mode, maximum):
            return None
        raw = bytearray()
        while len(raw) <= maximum:
            piece = api.read(descriptor, min(4096, maximum + 1 - len(raw)))
            if not piece:
                break
            raw.extend(piece)
        return bytes(raw) if len(raw) == info.st_size and len(raw) <= maximum and api.read(descriptor, 1) == b"" else None
    except Exception:
        return None
    finally:
        if isinstance(descriptor, int):
            try:
                api.close(descriptor)
            except Exception:
                pass


def _asset_valid(path: str, expected: str, mode: int, *, api: Any = os) -> bool:
    raw = _read_regular(path, mode, MAX_ASSET_BYTES, api=api)
    return raw is not None and hashlib.sha256(raw).hexdigest() == expected


def _state_directory_accessible(*, api: Any = os) -> bool:
    descriptor = None
    try:
        descriptor = api.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        return _safe_directory(api.fstat(descriptor))
    except Exception:
        return False
    finally:
        if isinstance(descriptor, int):
            try:
                api.close(descriptor)
            except Exception:
                pass


def _used_marker(grant_id: str, *, api: Any = os) -> bool:
    if not _HEX64.fullmatch(grant_id):
        return False
    raw = _read_regular(STATE_DIR + "/used-" + grant_id, 0o600, 16, api=api)
    return raw == b"used\n"


def _receipt_matches(*, api: Any = os) -> bool:
    raw = _read_regular(RECEIPT_PATH, 0o600, MAX_RECEIPT_BYTES, api=api)
    try:
        value = json.loads(raw.decode("ascii")) if raw is not None else None
    except Exception:
        return False
    return bool(type(value) is dict and set(value) == {"schema_id", "status", "error_code", "effect_may_have_occurred", "retry_permitted", "manual_recovery_required", "action_provenance_ref", "evidence_sha256"} and value.get("schema_id") == RESULT_SCHEMA_ID and value.get("status") == "unknown" and value.get("error_code") == "backup_failed" and value.get("effect_may_have_occurred") is True and value.get("retry_permitted") is False and value.get("manual_recovery_required") is True and value.get("action_provenance_ref") == ACTION_PROVENANCE_REF and value.get("evidence_sha256") == RESULT_EVIDENCE_SHA256 and value.get("evidence_sha256") == _digest(value))


def _unit_terminal_failed(runner: Callable[..., Any] = subprocess.run) -> bool:
    try:
        result = runner(list(SHOW_COMMAND), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=MINIMAL_ENV, close_fds=True, timeout=5, check=False)
        raw = result.stdout
    except Exception:
        return False
    if getattr(result, "returncode", None) != 0 or type(raw) is not bytes or len(raw) > 512 or b"\r" in raw:
        return False
    try:
        fields = dict(row.split("=", 1) for row in raw.decode("ascii").splitlines() if row.count("=") == 1)
    except Exception:
        return False
    return fields == {"Result": "exit-code", "ExecMainCode": "1", "ExecMainStatus": "1", "MainPID": "0", "ControlPID": "0", "ActiveState": "failed", "SubState": "failed"}


def probe(*, now_epoch: int, api: Any = os, runner: Callable[..., Any] = subprocess.run) -> dict[str, bool]:
    flags = {key: False for key in _FLAGS}
    flags["privilege_root"] = getattr(api, "geteuid", lambda: -1)() == 0
    flags["assets_intact"] = all(_asset_valid(path, digest, mode, api=api) for path, digest, mode in ((HELPER_PATH, HELPER_SHA256, 0o700), (READBACK_PATH, READBACK_SHA256, 0o700), (UNIT_PATH, UNIT_SHA256, 0o644), (SUDOERS_PATH, SUDOERS_SHA256, 0o440)))
    flags["state_directory_accessible"] = _state_directory_accessible(api=api)
    raw = _read_regular(ARM_PATH, 0o600, MAX_ARM_BYTES, api=api)
    flags["arm_present"] = raw is not None
    arm: Any = None
    try:
        arm = json.loads(raw.decode("ascii")) if raw is not None else None
    except Exception:
        arm = None
    if type(arm) is dict and set(arm) == {"schema_id", "grant_id", "expires_at_epoch", "helper_sha256"} and arm.get("schema_id") == ARM_SCHEMA_ID and isinstance(arm.get("grant_id"), str) and _HEX64.fullmatch(arm["grant_id"]) and type(arm.get("expires_at_epoch")) is int:
        flags["arm_shape_valid"] = True
        flags["arm_expired"] = arm["expires_at_epoch"] <= now_epoch
        flags["arm_helper_matches"] = arm.get("helper_sha256") == HELPER_SHA256
        reference = "predeploy_backup_root_helper_v1:" + hashlib.sha256((arm["grant_id"] + HELPER_SHA256).encode("ascii")).hexdigest()
        flags["action_provenance_matches"] = reference == ACTION_PROVENANCE_REF
        flags["used_marker_present"] = _used_marker(arm["grant_id"], api=api)
    flags["receipt_matches"] = _receipt_matches(api=api)
    flags["unit_terminal_failed"] = _unit_terminal_failed(runner)
    flags["recovery_preflight_ready"] = all(flags[key] is True for key in _FLAGS[:-1])
    return flags


def collect(*, execute: bool = False, now_epoch: int | None = None, probe_fn: Callable[..., Mapping[str, Any]] = probe) -> dict[str, Any]:
    if execute is not True:
        return _envelope("blocked", "execution_disabled")
    try:
        values = probe_fn(now_epoch=int(__import__("time").time()) if now_epoch is None else now_epoch)
        result = _envelope("ok", "none", values)
    except Exception:
        result = _envelope("blocked", "diagnostic_failed")
    return result if validate_envelope(result) else _envelope("blocked", "diagnostic_failed")


def main() -> int:
    print(json.dumps(collect(), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

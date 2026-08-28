#!/usr/bin/python3
"""Independent, fixed-schema readback of a redacted root-helper receipt."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from typing import Any, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_readback.v1"
RECEIPT_PATH = "/run/odysseus-predeploy-backup-root-helper/receipt.json"
MAX_RECEIPT_BYTES = 8192
_KEYS = frozenset({"schema_id", "status", "receipt_available", "result_status", "result_evidence_sha256", "action_provenance_ref", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})
_RESULT_SCHEMA = "odysseus.redacted_predeploy_backup_creation.v1"
_RESULT_KEYS = {
    "blocked": frozenset({"schema_id", "status", "error_code", "backup_invoked", "retry_permitted", "evidence_sha256"}),
    "unknown": frozenset({"schema_id", "status", "error_code", "effect_may_have_occurred", "retry_permitted", "manual_recovery_required", "action_provenance_ref", "evidence_sha256"}),
}
_OK_RESULT_KEYS = frozenset({"schema_id", "status", "repository_identity", "protected_source_identity", "backup_effect", "action_provenance_ref", "snapshot_id", "source_included", "snapshot_created_after_start", "snapshot_age_seconds", "snapshot_fresh", "concurrent_lock_held", "partial_snapshot_detected", "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "evidence_sha256"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _result(status: str, available: bool, result_status: str, result_digest: str, action_ref: str) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": status, "receipt_available": available, "result_status": result_status, "result_evidence_sha256": result_digest, "action_provenance_ref": action_ref, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def _result_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _validate_result(value: Any) -> bool:
    if type(value) is not dict or value.get("schema_id") != _RESULT_SCHEMA or type(value.get("evidence_sha256")) is not str or value["evidence_sha256"] != _result_digest(value): return False
    status = value.get("status")
    if status == "blocked": return set(value) == _RESULT_KEYS["blocked"] and value.get("error_code") in {"not_armed", "arm_invalid", "arm_expired", "arm_replayed", "arm_contended", "identity_unavailable", "preflight_failed"} and value.get("backup_invoked") is False and value.get("retry_permitted") is False
    if status == "unknown": return set(value) == _RESULT_KEYS["unknown"] and value.get("error_code") in {"backup_timeout", "backup_failed", "readback_timeout", "readback_failed", "readback_invalid", "execution_ambiguous"} and value.get("effect_may_have_occurred") is True and value.get("retry_permitted") is False and value.get("manual_recovery_required") is True and isinstance(value.get("action_provenance_ref"), str) and __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", value["action_provenance_ref"])
    return bool(status == "ok" and set(value) == _OK_RESULT_KEYS and value.get("repository_identity") == "restic_homeserver_backup_v1" and value.get("protected_source_identity") == "odysseus_protected_source_v1" and value.get("backup_effect") == "created" and value.get("source_included") is True and value.get("snapshot_created_after_start") is True and type(value.get("snapshot_age_seconds")) is int and value["snapshot_age_seconds"] >= 0 and value.get("snapshot_fresh") is True and value.get("concurrent_lock_held") is True and value.get("partial_snapshot_detected") is False and all(value.get(key) is False for key in ("raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible")) and isinstance(value.get("snapshot_id"), str) and __import__("re").fullmatch(r"[0-9a-f]{64}", value["snapshot_id"]) and isinstance(value.get("action_provenance_ref"), str) and __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", value["action_provenance_ref"]))


def validate_envelope(value: Any) -> bool:
    unavailable = value.get("status") == "unavailable" and value.get("receipt_available") is False and value.get("result_status") == "none" and value.get("result_evidence_sha256") == "0" * 64 and value.get("action_provenance_ref") == "none"
    available = value.get("status") == "available" and value.get("receipt_available") is True and value.get("result_status") in {"ok", "blocked", "unknown"} and type(value.get("result_evidence_sha256")) is str and __import__("re").fullmatch(r"[0-9a-f]{64}", value["result_evidence_sha256"]) and ((value.get("result_status") == "blocked" and value.get("action_provenance_ref") == "none") or (type(value.get("action_provenance_ref")) is str and __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", value["action_provenance_ref"])))
    return bool(type(value) is dict and set(value) == _KEYS and value.get("schema_id") == SCHEMA_ID and (available or unavailable) and all(value.get(key) is False for key in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) and value.get("evidence_sha256") == _digest(value))


def _read_fixed(path: str = RECEIPT_PATH) -> dict[str, Any] | None:
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(fd)
        # The systemd unit deliberately runs with UMask=0077, so the helper's
        # requested 0644 receipt is published as root-only 0600.  The reader is
        # itself a fixed root command and must validate the effective mode.
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= MAX_RECEIPT_BYTES: return None
        raw = bytearray()
        while len(raw) <= MAX_RECEIPT_BYTES:
            item = os.read(fd, min(4096, MAX_RECEIPT_BYTES + 1 - len(raw)))
            if not item: break
            raw.extend(item)
        if not raw or len(raw) > MAX_RECEIPT_BYTES: return None
        value = json.loads(bytes(raw).decode("ascii"))
        return value if _validate_result(value) else None
    except Exception: return None
    finally:
        if isinstance(fd, int):
            try: os.close(fd)
            except Exception: pass


def collect_readback(*, reader: Any = _read_fixed) -> dict[str, Any]:
    value = reader()
    if value is None: return _result("unavailable", False, "none", "0" * 64, "none")
    action_ref = "none" if value["status"] == "blocked" else value.get("action_provenance_ref")
    if not isinstance(action_ref, str): return _result("unavailable", False, "none", "0" * 64, "none")
    return _result("available", True, value["status"], value["evidence_sha256"], action_ref)


def main() -> int:
    print(json.dumps(collect_readback(), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__": raise SystemExit(main())

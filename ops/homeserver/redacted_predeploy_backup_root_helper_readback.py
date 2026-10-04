#!/usr/bin/python3
"""Independent, fixed-schema readback of a redacted root-helper receipt."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from typing import Any, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_readback.v1"
RECEIPT_PATH = "/run/odysseus-predeploy-backup-root-helper/receipt.json"
DIAGNOSTIC_PATH = "/run/odysseus-predeploy-backup-root-helper/diagnostic.json"
MAX_RECEIPT_BYTES = 8192
BACKUP_TIMEOUT_SECONDS = 1800
READBACK_TIMEOUT_SECONDS = 20
_KEYS = frozenset({"schema_id", "status", "receipt_available", "result_status", "result_evidence_sha256", "action_provenance_ref", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})
_RESULT_SCHEMA = "odysseus.redacted_predeploy_backup_creation.v1"
_RESULT_KEYS = {
    "blocked": frozenset({"schema_id", "status", "error_code", "backup_invoked", "retry_permitted", "evidence_sha256"}),
    "unknown": frozenset({"schema_id", "status", "error_code", "effect_may_have_occurred", "retry_permitted", "manual_recovery_required", "action_provenance_ref", "evidence_sha256"}),
}
_OK_RESULT_KEYS = frozenset({"schema_id", "status", "repository_identity", "protected_source_identity", "backup_effect", "action_provenance_ref", "snapshot_id", "source_included", "snapshot_created_after_start", "snapshot_age_seconds", "snapshot_fresh", "concurrent_lock_held", "partial_snapshot_detected", "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "evidence_sha256"})
DIAGNOSTIC_SCHEMA_ID = "odysseus.predeploy_backup_root_helper_diagnostic.v1"
DIAGNOSTIC_READBACK_SCHEMA_ID = "odysseus.predeploy_backup_root_helper_diagnostic_readback.v2"
_DIAGNOSTIC_KEYS = frozenset({"schema_id", "result_status", "result_error_code", "result_evidence_sha256", "action_provenance_ref", "failed_phase", "restic_exec_state", "exit_class", "exit_code_available", "exit_code", "child_started", "child_reaped", "cleanup_terminal", "arm_consumed", "retry_permitted", "manual_recovery_required", "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "evidence_sha256"})
_DIAGNOSTIC_READBACK_KEYS = frozenset({"schema_id", "status", "diagnostic_available", "result_status", "result_error_code", "result_evidence_sha256", "action_provenance_ref", "failed_phase", "restic_exec_state", "exit_class", "exit_code_available", "exit_code", "child_started", "child_reaped", "cleanup_terminal", "arm_consumed", "retry_permitted", "manual_recovery_required", "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "evidence_sha256"})
_PHASES = frozenset({"none", "pre_arm", "post_arm_pre_backup", "backup_start", "backup_wait", "readback_start", "readback_wait", "readback_validate", "unexpected"})
_EXEC_STATES = frozenset({"not_attempted", "preexec_failed", "confirmed_exec", "unknown"})
_EXIT_CLASSES = frozenset({"not_available", "exited_zero", "exited_nonzero", "signaled", "timed_out", "output_limit", "wait_failed", "handshake_ambiguous"})
_BLOCKED_CODES = frozenset({"not_armed", "arm_invalid", "arm_expired", "arm_replayed", "arm_contended", "identity_unavailable", "preflight_failed"})
_UNKNOWN_CODES = frozenset({"backup_timeout", "backup_failed", "readback_timeout", "readback_failed", "readback_invalid", "execution_ambiguous"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _result(status: str, available: bool, result_status: str, result_digest: str, action_ref: str) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": status, "receipt_available": available, "result_status": result_status, "result_evidence_sha256": result_digest, "action_provenance_ref": action_ref, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def _result_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _strict_json(raw: bytes) -> Any:
    """Reject duplicate members and non-finite values before schema validation."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value
    return json.loads(raw.decode("ascii"), object_pairs_hook=pairs,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def _validate_result(value: Any) -> bool:
    try:
        if type(value) is not dict or value.get("schema_id") != _RESULT_SCHEMA or type(value.get("evidence_sha256")) is not str or value["evidence_sha256"] != _result_digest(value): return False
        status = value.get("status")
        if status == "blocked": return set(value) == _RESULT_KEYS["blocked"] and value.get("error_code") in _BLOCKED_CODES and value.get("backup_invoked") is False and value.get("retry_permitted") is False
        if status == "unknown": return set(value) == _RESULT_KEYS["unknown"] and value.get("error_code") in _UNKNOWN_CODES and value.get("effect_may_have_occurred") is True and value.get("retry_permitted") is False and value.get("manual_recovery_required") is True and isinstance(value.get("action_provenance_ref"), str) and __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", value["action_provenance_ref"])
        return bool(status == "ok" and set(value) == _OK_RESULT_KEYS and value.get("repository_identity") == "restic_homeserver_backup_v1" and value.get("protected_source_identity") == "odysseus_protected_source_v1" and value.get("backup_effect") == "created" and value.get("source_included") is True and value.get("snapshot_created_after_start") is True and type(value.get("snapshot_age_seconds")) is int and 0 <= value["snapshot_age_seconds"] <= BACKUP_TIMEOUT_SECONDS + READBACK_TIMEOUT_SECONDS and value.get("snapshot_fresh") is True and value.get("concurrent_lock_held") is True and value.get("partial_snapshot_detected") is False and all(value.get(key) is False for key in ("raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible")) and isinstance(value.get("snapshot_id"), str) and __import__("re").fullmatch(r"[0-9a-f]{64}", value["snapshot_id"]) and isinstance(value.get("action_provenance_ref"), str) and __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", value["action_provenance_ref"]))
    except Exception:
        return False


def validate_envelope(value: Any) -> bool:
    try:
        if type(value) is not dict or set(value) != _KEYS or value.get("schema_id") != SCHEMA_ID: return False
        unavailable = value.get("status") == "unavailable" and value.get("receipt_available") is False and value.get("result_status") == "none" and value.get("result_evidence_sha256") == "0" * 64 and value.get("action_provenance_ref") == "none"
        result_status, action_ref = value.get("result_status"), value.get("action_provenance_ref")
        available = value.get("status") == "available" and value.get("receipt_available") is True and result_status in {"ok", "blocked", "unknown"} and type(value.get("result_evidence_sha256")) is str and __import__("re").fullmatch(r"[0-9a-f]{64}", value["result_evidence_sha256"]) and ((result_status == "blocked" and action_ref == "none") or (result_status in {"ok", "unknown"} and type(action_ref) is str and __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", action_ref)))
        return bool((available or unavailable) and all(value.get(key) is False for key in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) and value.get("evidence_sha256") == _digest(value))
    except Exception:
        return False


def _read_fixed(path: str = RECEIPT_PATH) -> dict[str, Any] | None:
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o644 or info.st_nlink != 1 or not 0 < info.st_size <= MAX_RECEIPT_BYTES: return None
        raw = bytearray()
        while len(raw) <= MAX_RECEIPT_BYTES:
            item = os.read(fd, min(4096, MAX_RECEIPT_BYTES + 1 - len(raw)))
            if not item: break
            raw.extend(item)
        if not raw or len(raw) > MAX_RECEIPT_BYTES: return None
        value = _strict_json(bytes(raw))
        return value if _validate_result(value) else None
    except Exception: return None
    finally:
        if isinstance(fd, int):
            try: os.close(fd)
            except Exception: pass


def _validate_diagnostic(value: Any) -> bool:
    try:
        if type(value) is not dict or set(value) != _DIAGNOSTIC_KEYS or value.get("schema_id") != DIAGNOSTIC_SCHEMA_ID or type(value.get("evidence_sha256")) is not str or value["evidence_sha256"] != _digest(value): return False
        status, code, reference = value.get("result_status"), value.get("result_error_code"), value.get("action_provenance_ref")
        if status not in {"ok", "blocked", "unknown"} or not isinstance(code, str): return False
        if (status == "ok" and code != "none") or (status == "blocked" and code not in _BLOCKED_CODES) or (status == "unknown" and code not in _UNKNOWN_CODES): return False
        if (status == "blocked" and reference != "none") or (status != "blocked" and (not isinstance(reference, str) or __import__("re").fullmatch(r"predeploy_backup_root_helper_v1:[0-9a-f]{64}", reference) is None)): return False
        if type(value.get("result_evidence_sha256")) is not str or __import__("re").fullmatch(r"[0-9a-f]{64}", value["result_evidence_sha256"]) is None: return False
        if value.get("failed_phase") not in _PHASES or value.get("restic_exec_state") not in _EXEC_STATES or value.get("exit_class") not in _EXIT_CLASSES: return False
        state, exit_class = value["restic_exec_state"], value["exit_class"]
        available, exit_code = value.get("exit_code_available"), value.get("exit_code")
        if type(available) is not bool or type(exit_code) is not int or not 0 <= exit_code <= 255 or (not available and exit_code != 0): return False
        if (value["exit_class"] in {"exited_zero", "exited_nonzero"}) != available: return False
        if any(type(value.get(key)) is not bool for key in ("child_started", "child_reaped", "cleanup_terminal", "arm_consumed", "retry_permitted", "manual_recovery_required")): return False
        if value["child_reaped"] and not value["child_started"]: return False
        if value["cleanup_terminal"] != (not value["child_started"] or value["child_reaped"]) or value["retry_permitted"] is not False or value["manual_recovery_required"] != (status == "unknown"): return False
        if exit_class == "not_available" and (value["child_started"] and state != "preexec_failed"): return False
        if status == "blocked":
            if not (value["failed_phase"] == "pre_arm" and value["restic_exec_state"] == "not_attempted" and value["exit_class"] == "not_available" and not value["child_started"] and not value["child_reaped"] and value["cleanup_terminal"] and not value["arm_consumed"]): return False
        elif status == "ok":
            if not (value["arm_consumed"] and value["failed_phase"] == "none" and value["restic_exec_state"] == "confirmed_exec" and value["exit_class"] == "exited_zero" and value["exit_code_available"] is True and value["exit_code"] == 0 and value["child_started"] and value["child_reaped"] and value["cleanup_terminal"]): return False
        else:
            phase = value["failed_phase"]
            if not value["arm_consumed"] or phase == "none": return False
            if not value["child_started"]:
                if not (state == "not_attempted" and exit_class == "not_available" and not available and exit_code == 0 and not value["child_reaped"] and value["cleanup_terminal"] and code == "execution_ambiguous" and phase in {"pre_arm", "post_arm_pre_backup", "backup_start", "readback_start"}): return False
            else:
                if state == "preexec_failed":
                    if not (phase in {"backup_start", "readback_start"} and exit_class == "not_available" and not available and exit_code == 0 and value["child_reaped"]): return False
                else:
                    if state == "not_attempted" or phase not in {"backup_wait", "readback_wait", "readback_validate", "unexpected"}: return False
                    if exit_class in {"exited_zero", "exited_nonzero"} and not (state == "confirmed_exec" and value["child_reaped"] and available and (exit_code == 0 if exit_class == "exited_zero" else 1 <= exit_code <= 255)): return False
                    if exit_class in {"signaled", "handshake_ambiguous"} and not value["child_reaped"]: return False
                    if exit_class not in {"exited_zero", "exited_nonzero"} and (available or exit_code != 0): return False
            if code == "backup_timeout" and not (phase == "backup_wait" and exit_class == "timed_out"): return False
            if code == "readback_timeout" and not (phase == "readback_wait" and exit_class == "timed_out"): return False
            if code == "backup_failed" and not ((phase == "backup_start" and state == "preexec_failed") or (phase == "backup_wait" and exit_class != "timed_out" and (state != "confirmed_exec" or exit_class != "exited_zero"))): return False
            if code == "readback_failed" and not ((phase == "readback_start" and state == "preexec_failed") or (phase == "readback_wait" and exit_class != "timed_out" and (state != "confirmed_exec" or exit_class != "exited_zero"))): return False
            if code == "readback_invalid" and not (phase == "readback_validate" and state == "confirmed_exec" and exit_class == "exited_zero" and available and exit_code == 0): return False
            if code == "execution_ambiguous" and not ((not value["child_started"] and phase in {"pre_arm", "post_arm_pre_backup", "backup_start", "readback_start"}) or (value["child_started"] and phase in {"backup_wait", "readback_wait", "readback_validate", "unexpected"})): return False
        return all(value.get(key) is False for key in ("raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible"))
    except Exception:
        return False


def _read_diagnostic_fixed(path: str = DIAGNOSTIC_PATH) -> dict[str, Any] | None:
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o644 or info.st_nlink != 1 or not 0 < info.st_size <= MAX_RECEIPT_BYTES: return None
        raw = os.read(fd, MAX_RECEIPT_BYTES + 1)
        if not raw or len(raw) > MAX_RECEIPT_BYTES: return None
        value = _strict_json(raw)
        return value if _validate_diagnostic(value) else None
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


def _diagnostic_readback(status: str, available: bool, diagnostic: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if diagnostic is None:
        value = {"schema_id": DIAGNOSTIC_READBACK_SCHEMA_ID, "status": status, "diagnostic_available": available, "result_status": "none", "result_error_code": "none", "result_evidence_sha256": "0" * 64, "action_provenance_ref": "none", "failed_phase": "none", "restic_exec_state": "not_attempted", "exit_class": "not_available", "exit_code_available": False, "exit_code": 0, "child_started": False, "child_reaped": False, "cleanup_terminal": True, "arm_consumed": False, "retry_permitted": False, "manual_recovery_required": False, "raw_stdout_visible": False, "raw_stderr_visible": False, "exception_text_visible": False, "environment_visible": False, "file_contents_visible": False, "paths_visible": False, "hostnames_visible": False, "secret_values_visible": False}
    else:
        value = {key: diagnostic[key] for key in _DIAGNOSTIC_KEYS if key != "evidence_sha256"}
        value.update({"schema_id": DIAGNOSTIC_READBACK_SCHEMA_ID, "status": status, "diagnostic_available": available})
    value["evidence_sha256"] = _digest(value)
    return value


def validate_diagnostic_readback_v2(value: Any) -> bool:
    try:
        if type(value) is not dict or set(value) != _DIAGNOSTIC_READBACK_KEYS or value.get("schema_id") != DIAGNOSTIC_READBACK_SCHEMA_ID or value.get("evidence_sha256") != _digest(value): return False
        if value.get("status") == "unavailable":
            return value.get("diagnostic_available") is False and value.get("result_status") == "none" and value.get("result_error_code") == "none" and value.get("result_evidence_sha256") == "0" * 64 and value.get("action_provenance_ref") == "none" and value.get("failed_phase") == "none" and value.get("restic_exec_state") == "not_attempted" and value.get("exit_class") == "not_available" and value.get("exit_code_available") is False and type(value.get("exit_code")) is int and value.get("exit_code") == 0 and value.get("child_started") is False and value.get("child_reaped") is False and value.get("cleanup_terminal") is True and value.get("arm_consumed") is False and value.get("retry_permitted") is False and value.get("manual_recovery_required") is False and all(value.get(key) is False for key in ("raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible", "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible"))
        if value.get("status") != "available" or value.get("diagnostic_available") is not True: return False
        candidate = {key: value[key] for key in _DIAGNOSTIC_KEYS}
        candidate["schema_id"] = DIAGNOSTIC_SCHEMA_ID
        candidate["evidence_sha256"] = _digest(candidate)
        return _validate_diagnostic(candidate)
    except Exception:
        return False


def collect_diagnostic_readback_v2(*, receipt_reader: Any = _read_fixed, diagnostic_reader: Any = _read_diagnostic_fixed) -> dict[str, Any]:
    try:
        receipt, diagnostic = receipt_reader(), diagnostic_reader()
    except Exception:
        return _diagnostic_readback("unavailable", False)
    if not _validate_result(receipt) or not _validate_diagnostic(diagnostic): return _diagnostic_readback("unavailable", False)
    action_ref = "none" if receipt["status"] == "blocked" else receipt.get("action_provenance_ref")
    if diagnostic["result_status"] != receipt["status"] or diagnostic["result_evidence_sha256"] != receipt["evidence_sha256"] or diagnostic["action_provenance_ref"] != action_ref or diagnostic["result_error_code"] != receipt.get("error_code", "none"):
        return _diagnostic_readback("unavailable", False)
    value = _diagnostic_readback("available", True, diagnostic)
    return value if validate_diagnostic_readback_v2(value) else _diagnostic_readback("unavailable", False)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == []: value = collect_readback()
    elif args == ["--diagnostic-v2"]: value = collect_diagnostic_readback_v2()
    else: return 2
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__": raise SystemExit(main())

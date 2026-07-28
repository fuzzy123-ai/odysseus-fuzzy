#!/usr/bin/env python3
"""Create one fixed, redacted pre-update backup snapshot.

This wrapper intentionally exposes no options.  It validates a fixed local
backup contract, takes one non-blocking host-local lock, invokes the reviewed
backup script once, then proves the resulting snapshot with one read-only
Restic query.  It never copies process output, exception text, environment,
or filesystem details into its packet.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.redacted_predeploy_backup_creation.v1"
RESTIC_BINARY = "/usr/bin/restic"
BACKUP_MOUNT = "/mnt/backup"
REPOSITORY = "/mnt/backup/restic/homeserver"
SOURCE = "/opt/odysseus"
NEXTCLOUD_ROOT = "/opt/nextcloud"
HOMEBASE_HOME = "/home/homebase"
DB_DUMP_ROOT = ""
DB_DUMP_STAGING = "/home/homebase/.cache/odysseus-backup/db-dumps"
BACKUP_SCRIPT = "/opt/odysseus/ops/homeserver/backup-homeserver.sh"
CONFIG_PATH = "/home/homebase/.config/odysseus-backup/restic-observation.env"
PASSWORD_FILE = "/home/homebase/.config/odysseus-backup/restic-password"
LOCK_PATH = "/home/homebase/.local/state/odysseus-predeploy-backup.lock"
LOCK_PARENT = "/home/homebase/.local/state"
EXPECTED_OWNER = "homebase"
BACKUP_TIMEOUT_SECONDS = 1_800
READBACK_TIMEOUT_SECONDS = 20
OUTER_PACKET_TIMEOUT_SECONDS = 1_860
MAX_READBACK_BYTES = 65_536
MAX_SNAPSHOT_AGE_SECONDS = OUTER_PACKET_TIMEOUT_SECONDS

BACKUP_COMMAND = (BACKUP_SCRIPT, "--mode", "pre-update")
READBACK_COMMAND = (
    RESTIC_BINARY, "-r", REPOSITORY, "--no-lock", "snapshots",
    "--tag", "odysseus-pre-update", "--latest", "1", "--json",
)
FIXED_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "RESTIC_PASSWORD_FILE": PASSWORD_FILE,
    "RESTIC_REPOSITORY": REPOSITORY,
    "BACKUP_MOUNT": BACKUP_MOUNT,
    "RESTIC_BIN": RESTIC_BINARY,
    "RESTIC_USE_SUDO": "0",
    "RESTIC_REPAIR_REPO_OWNER": "0",
    "ODYSSEUS_ROOT": SOURCE,
    "NEXTCLOUD_ROOT": NEXTCLOUD_ROOT,
    "HOMEBASE_HOME": HOMEBASE_HOME,
    "DB_DUMP_ROOT": DB_DUMP_ROOT,
    "DB_DUMP_STAGING": DB_DUMP_STAGING,
}

_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{64}$")
_PROVENANCE_REF = re.compile(r"^predeploy_backup_creation_v1:[0-9a-f]{64}$")
_BLOCKED_ERRORS = frozenset({
    "config_unavailable", "config_invalid", "mount_unavailable",
    "repository_unsafe", "password_file_unsafe", "restic_unavailable",
    "backup_script_unsafe", "source_path_missing", "lock_contended",
    "lock_unavailable", "internal_error",
})
_UNKNOWN_ERRORS = frozenset({
    "backup_timeout", "backup_failed", "backup_exception", "backup_result_invalid",
    "readback_timeout", "readback_failed", "readback_exception",
    "readback_output_too_large", "readback_malformed", "snapshot_missing",
    "snapshot_id_invalid", "snapshot_invalid", "snapshot_stale", "snapshot_not_new",
    "internal_error",
})
_OK_KEYS = frozenset({
    "schema_id", "status", "repository_identity", "protected_source_identity",
    "backup_effect", "action_provenance_ref", "snapshot_id", "source_included",
    "snapshot_created_after_start", "snapshot_age_seconds", "snapshot_fresh",
    "concurrent_lock_held", "partial_snapshot_detected", "raw_stdout_visible",
    "raw_stderr_visible", "exception_text_visible", "environment_visible",
    "file_contents_visible", "paths_visible", "hostnames_visible",
    "secret_values_visible", "evidence_sha256",
})
_BLOCKED_KEYS = frozenset({
    "schema_id", "status", "error_code", "backup_invoked", "retry_permitted", "evidence_sha256",
})
_UNKNOWN_KEYS = frozenset({
    "schema_id", "status", "error_code", "effect_may_have_occurred",
    "retry_permitted", "manual_recovery_required", "action_provenance_ref", "evidence_sha256",
})


class ContractFailure(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code if code in _BLOCKED_ERRORS else "internal_error"


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def blocked(code: str) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID, "status": "blocked",
        "error_code": code if code in _BLOCKED_ERRORS else "internal_error",
        "backup_invoked": False, "retry_permitted": False,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def _action_provenance_ref(started_at: float) -> str:
    """Return a fixed-grammar opaque reference without emitting the start time."""
    canonical = json.dumps({
        "action": "predeploy_backup_creation_v1",
        "started_at_epoch_millis": int(started_at * 1_000),
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "predeploy_backup_creation_v1:" + hashlib.sha256(canonical).hexdigest()


def unknown(code: str, *, action_provenance_ref: str) -> dict[str, Any]:
    assert _PROVENANCE_REF.fullmatch(action_provenance_ref)
    payload = {
        "schema_id": SCHEMA_ID, "status": "unknown",
        "error_code": code if code in _UNKNOWN_ERRORS else "internal_error",
        "effect_may_have_occurred": True, "retry_permitted": False,
        "manual_recovery_required": True, "action_provenance_ref": action_provenance_ref,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def _read_fixed_config() -> str:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            return handle.read(4097)
    except Exception:
        raise ContractFailure("config_unavailable") from None


def _validate_config(value: Any) -> None:
    expected = "RESTIC_PASSWORD_FILE=" + PASSWORD_FILE + "\n"
    if not isinstance(value, str) or len(value.encode("utf-8")) > 4096 or value != expected:
        raise ContractFailure("config_invalid")


def _validate_process_environment(value: Any) -> None:
    """Reject inherited write/auth/path controls without inspecting their values."""
    if not isinstance(value, Mapping):
        raise ContractFailure("config_invalid")
    for key in ("RESTIC_PASSWORD", "RESTIC_PASSWORD_COMMAND"):
        candidate = value.get(key)
        if candidate is not None and (not isinstance(candidate, str) or bool(candidate)):
            raise ContractFailure("config_invalid")
    for key in (
        "RESTIC_REPOSITORY", "RESTIC_BINARY", "RESTIC_BIN", "BACKUP_MOUNT", "ODYSSEUS_ROOT", "SOURCE",
        "NEXTCLOUD_ROOT", "HOMEBASE_HOME", "DB_DUMP_ROOT", "DB_DUMP_STAGING", "RESTIC_USE_SUDO",
        "RESTIC_REPAIR_REPO_OWNER",
    ):
        if key in value:
            raise ContractFailure("config_invalid")
    password_file = value.get("RESTIC_PASSWORD_FILE")
    if password_file is not None and password_file != PASSWORD_FILE:
        raise ContractFailure("config_invalid")


def _production_owner_lookup(owner: str) -> Any:
    try:
        import pwd
        return pwd.getpwnam(owner)
    except Exception:
        raise ContractFailure("internal_error") from None


def _expected_uid(owner_lookup: Callable[[str], Any]) -> int:
    try:
        uid = owner_lookup(EXPECTED_OWNER).pw_uid
    except Exception:
        raise ContractFailure("internal_error") from None
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise ContractFailure("internal_error")
    return uid


def _safe_regular(path: str, *, expected_uid: int, lstat: Callable[[str], Any], exact_mode: int | None = None,
                  executable: bool = False) -> bool:
    try:
        info = lstat(path)
        mode = int(info.st_mode)
        permissions = stat.S_IMODE(mode)
        return (
            stat.S_ISREG(mode) and int(info.st_uid) == expected_uid
            and (permissions == exact_mode if exact_mode is not None else (permissions & 0o022) == 0)
            and (not executable or bool(permissions & 0o100))
        )
    except Exception:
        return False


def _safe_binary(*, lstat: Callable[[str], Any]) -> bool:
    try:
        info = lstat(RESTIC_BINARY)
        mode = int(info.st_mode)
        permissions = stat.S_IMODE(mode)
        return stat.S_ISREG(mode) and int(info.st_uid) == 0 and bool(permissions & 0o100) and (permissions & 0o022) == 0
    except Exception:
        return False


def _safe_directory(path: str, *, expected_uid: int, lstat: Callable[[str], Any], required_permissions: int = 0o100) -> bool:
    try:
        info = lstat(path)
        mode = int(info.st_mode)
        permissions = stat.S_IMODE(mode)
        return stat.S_ISDIR(mode) and int(info.st_uid) == expected_uid and (permissions & required_permissions) == required_permissions and (permissions & 0o022) == 0
    except Exception:
        return False


def _production_mount_checker(path: str) -> bool:
    try:
        return os.path.ismount(path)
    except Exception:
        return False


def _production_lock_acquire(expected_uid: int) -> int:
    """Create/validate and take the fixed local advisory lock without following links."""
    try:
        import fcntl
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if not isinstance(nofollow, int):
            raise ContractFailure("lock_unavailable")
        flags = os.O_RDWR | os.O_CREAT | nofollow
        descriptor = os.open(LOCK_PATH, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or int(info.st_uid) != expected_uid or stat.S_IMODE(info.st_mode) != 0o600:
            os.close(descriptor)
            raise OSError("unsafe lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError:
        raise ContractFailure("lock_contended") from None
    except ContractFailure:
        raise
    except Exception:
        raise ContractFailure("lock_unavailable") from None


def _production_lock_release(descriptor: Any) -> None:
    try:
        import fcntl
        fcntl.flock(int(descriptor), fcntl.LOCK_UN)
        os.close(int(descriptor))
    except Exception:
        # The invocation is already represented by a canonical result; release
        # errors must neither disclose nor transform that outcome.
        pass


def _parse_snapshot_time(value: Any) -> float:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid") from None
    if parsed.tzinfo is None:
        raise ValueError("invalid")
    return parsed.astimezone(timezone.utc).timestamp()


def _readback_snapshot(raw: Any, *, started_at: float, now: float) -> tuple[str, int]:
    if not isinstance(raw, str):
        raise ValueError("malformed")
    if len(raw.encode("utf-8")) > MAX_READBACK_BYTES:
        raise ValueError("too_large")
    try:
        snapshots = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("malformed") from None
    if not isinstance(snapshots, list) or len(snapshots) != 1:
        raise ValueError("missing" if snapshots == [] else "malformed")
    snapshot = snapshots[0]
    if not isinstance(snapshot, Mapping):
        raise ValueError("malformed")
    snapshot_id, tags, paths = snapshot.get("id"), snapshot.get("tags"), snapshot.get("paths")
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError("id")
    if not isinstance(tags, list) or any(not isinstance(tag, str) or len(tag) > 64 for tag in tags) or "odysseus-pre-update" not in tags:
        raise ValueError("invalid")
    if not isinstance(paths, list) or any(not isinstance(path, str) or len(path) > 4096 for path in paths) or SOURCE not in paths:
        raise ValueError("invalid")
    try:
        created_at = _parse_snapshot_time(snapshot.get("time"))
    except ValueError:
        raise ValueError("invalid") from None
    if created_at < started_at:
        raise ValueError("not_new")
    age = now - created_at
    if age < 0 or age > MAX_SNAPSHOT_AGE_SECONDS:
        raise ValueError("stale")
    return snapshot_id, int(age)


def _run_backup(runner: Callable[..., Any]) -> str | None:
    try:
        result = runner(BACKUP_COMMAND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
                        timeout=BACKUP_TIMEOUT_SECONDS, env=dict(FIXED_ENVIRONMENT))
    except subprocess.TimeoutExpired:
        return "backup_timeout"
    except Exception:
        return "backup_exception"
    if not isinstance(getattr(result, "returncode", None), int):
        return "backup_result_invalid"
    return None if result.returncode == 0 else "backup_failed"


def _run_readback(runner: Callable[..., Any], *, started_at: float, now: float) -> tuple[str, int] | str:
    try:
        result = runner(READBACK_COMMAND, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                        timeout=READBACK_TIMEOUT_SECONDS, env=dict(FIXED_ENVIRONMENT))
    except subprocess.TimeoutExpired:
        return "readback_timeout"
    except Exception:
        return "readback_exception"
    if getattr(result, "returncode", None) != 0:
        return "readback_failed"
    try:
        return _readback_snapshot(getattr(result, "stdout", None), started_at=started_at, now=now)
    except ValueError as exc:
        return {
            "too_large": "readback_output_too_large", "missing": "snapshot_missing",
            "id": "snapshot_id_invalid", "not_new": "snapshot_not_new", "stale": "snapshot_stale",
        }.get(str(exc), "readback_malformed")


def _ok(snapshot_id: str, age: int, *, action_provenance_ref: str) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID, "status": "ok",
        "repository_identity": "restic_homeserver_backup_v1",
        "protected_source_identity": "odysseus_protected_source_v1",
        "backup_effect": "created", "action_provenance_ref": action_provenance_ref,
        "snapshot_id": snapshot_id, "source_included": True,
        "snapshot_created_after_start": True, "snapshot_age_seconds": age, "snapshot_fresh": True,
        "concurrent_lock_held": True, "partial_snapshot_detected": False,
        "raw_stdout_visible": False, "raw_stderr_visible": False, "exception_text_visible": False,
        "environment_visible": False, "file_contents_visible": False, "paths_visible": False,
        "hostnames_visible": False, "secret_values_visible": False,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def collect_predeploy_backup_creation(
    *, runner: Callable[..., Any] = subprocess.run, lstat: Callable[[str], Any] = os.lstat,
    mount_checker: Callable[[str], bool] = _production_mount_checker,
    owner_lookup: Callable[[str], Any] = _production_owner_lookup,
    config_reader: Callable[[], str] = _read_fixed_config,
    process_environment: Mapping[str, Any] | None = None,
    lock_acquire: Callable[[int], Any] = _production_lock_acquire,
    lock_release: Callable[[Any], None] = _production_lock_release,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run the one-shot fixed contract; all dependencies are injectable for offline tests."""
    lock_handle: Any = None
    invoked = False
    action_provenance_ref: str | None = None
    try:
        _validate_process_environment(os.environ if process_environment is None else process_environment)
        expected_uid = _expected_uid(owner_lookup)
        if not _safe_binary(lstat=lstat):
            return blocked("restic_unavailable")
        if not _safe_regular(BACKUP_SCRIPT, expected_uid=expected_uid, lstat=lstat, executable=True):
            return blocked("backup_script_unsafe")
        if not _safe_directory(SOURCE, expected_uid=expected_uid, lstat=lstat, required_permissions=0o500):
            return blocked("source_path_missing")
        if not _safe_directory(REPOSITORY, expected_uid=expected_uid, lstat=lstat):
            return blocked("repository_unsafe")
        if not _safe_regular(CONFIG_PATH, expected_uid=expected_uid, lstat=lstat, exact_mode=0o600):
            return blocked("config_invalid")
        if not _safe_regular(PASSWORD_FILE, expected_uid=expected_uid, lstat=lstat, exact_mode=0o600):
            return blocked("password_file_unsafe")
        if not _safe_directory(LOCK_PARENT, expected_uid=expected_uid, lstat=lstat, required_permissions=0o700):
            return blocked("lock_unavailable")
        _validate_config(config_reader())
        if not bool(mount_checker(BACKUP_MOUNT)):
            return blocked("mount_unavailable")
        lock_handle = lock_acquire(expected_uid)
        # This is deliberately the final operation before dispatch, so its
        # provenance cannot be confused with preflight time.
        started_value = clock()
        if isinstance(started_value, bool) or not isinstance(started_value, (int, float)):
            raise ContractFailure("internal_error")
        started_at = float(started_value)
        if not math.isfinite(started_at) or started_at < 0:
            raise ContractFailure("internal_error")
        action_provenance_ref = _action_provenance_ref(started_at)
        invoked = True
        backup_problem = _run_backup(runner)
        if backup_problem is not None:
            return unknown(backup_problem, action_provenance_ref=action_provenance_ref)
        readback = _run_readback(runner, started_at=started_at, now=float(clock()))
        if isinstance(readback, str):
            return unknown(readback, action_provenance_ref=action_provenance_ref)
        return _ok(*readback, action_provenance_ref=action_provenance_ref)
    except ContractFailure as failure:
        if not invoked:
            return blocked(failure.code)
        assert action_provenance_ref is not None
        return unknown("internal_error", action_provenance_ref=action_provenance_ref)
    except Exception:
        if not invoked:
            return blocked("internal_error")
        assert action_provenance_ref is not None
        return unknown("internal_error", action_provenance_ref=action_provenance_ref)
    finally:
        if lock_handle is not None:
            lock_release(lock_handle)


def main() -> int:
    payload = collect_predeploy_backup_creation()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

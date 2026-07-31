#!/usr/bin/env python3
"""Read one redacted pre-update Restic snapshot observation.

This host-local wrapper has no command-line options and never invokes SSH.  Its
only executable is the reviewed absolute ``/usr/bin/restic`` binary, invoked
once with the fixed repository and ``snapshots --json --no-lock``.  It never
runs check, backup, restore, forget, lock, unlock, or another write-capable
Restic command.  Raw process/configuration data stays inside this module.
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
from typing import Any, Callable, Mapping, Sequence


SCHEMA_ID = "odysseus.redacted_backup_snapshot_observation.v1"
RESTIC_BINARY = "/usr/bin/restic"
BACKUP_MOUNT = "/mnt/backup"
REPOSITORY = "/mnt/backup/restic/homeserver"
SOURCE = "/opt/odysseus"
CONFIG_PATH = "/home/homebase/.config/odysseus-backup/restic-observation.env"
PASSWORD_FILE = "/home/homebase/.config/odysseus-backup/restic-password"
EXPECTED_OWNER = "homebase"
TIMEOUT_SECONDS = 20
MAX_OUTPUT_BYTES = 65_536
MAX_SNAPSHOTS = 4096
MAX_SNAPSHOT_AGE_SECONDS = 86_400

RESTIC_SNAPSHOTS_COMMAND = (
    RESTIC_BINARY, "-r", REPOSITORY, "--no-lock", "snapshots",
    "--tag", "odysseus-pre-update", "--latest", "1", "--json",
)

_SNAPSHOT_ID = re.compile(r"^[0-9a-f]{64}$")
_OK_KEYS = frozenset({
    "schema_id", "status", "repository_identity", "protected_source_identity",
    "snapshot_id", "source_included", "snapshot_age_seconds", "snapshot_fresh",
    "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible",
    "environment_visible", "file_contents_visible", "paths_visible",
    "hostnames_visible", "secret_values_visible", "evidence_sha256",
})
_BLOCKED_KEYS = frozenset({"schema_id", "status", "error_code", "evidence_sha256"})
_ERRORS = frozenset({
    "config_unavailable", "config_invalid", "mount_unavailable", "repository_unsafe",
    "password_file_unsafe", "restic_unavailable", "snapshot_query_failed", "timeout",
    "output_too_large", "malformed_output", "snapshot_missing", "snapshot_id_invalid", "snapshot_invalid",
    "snapshot_stale", "source_path_missing", "internal_error",
})


class ObservationFailure(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code if code in _ERRORS else "internal_error"


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    encoded = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def blocked(code: str) -> dict[str, Any]:
    payload = {"schema_id": SCHEMA_ID, "status": "blocked", "error_code": code if code in _ERRORS else "internal_error"}
    payload["evidence_sha256"] = _digest(payload)
    return payload


def validate_envelope(value: Any) -> bool:
    if type(value) is not dict or type(value.get("status")) is not str:
        return False
    if value["status"] == "blocked":
        return bool(
            set(value) == _BLOCKED_KEYS
            and value.get("schema_id") == SCHEMA_ID
            and value.get("error_code") in _ERRORS
            and type(value.get("evidence_sha256")) is str
            and _SNAPSHOT_ID.fullmatch(value["evidence_sha256"])
            and value["evidence_sha256"] == _digest(value)
        )
    visibility = {key for key in _OK_KEYS if key.endswith("_visible")}
    return bool(
        value["status"] == "ok"
        and set(value) == _OK_KEYS
        and value.get("schema_id") == SCHEMA_ID
        and value.get("repository_identity") == "restic_homeserver_backup_v1"
        and value.get("protected_source_identity")
        == "odysseus_protected_source_v1"
        and value.get("source_included") is True
        and value.get("snapshot_fresh") is True
        and type(value.get("snapshot_id")) is str
        and _SNAPSHOT_ID.fullmatch(value["snapshot_id"])
        and type(value.get("snapshot_age_seconds")) is int
        and 0 <= value["snapshot_age_seconds"] <= MAX_SNAPSHOT_AGE_SECONDS
        and all(value.get(key) is False for key in visibility)
        and type(value.get("evidence_sha256")) is str
        and _SNAPSHOT_ID.fullmatch(value["evidence_sha256"])
        and value["evidence_sha256"] == _digest(value)
    )


def _read_fixed_config() -> str:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            return handle.read(4097)
    except Exception:
        raise ObservationFailure("config_unavailable") from None


def _parse_config(value: Any) -> None:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 4096:
        raise ObservationFailure("config_invalid")
    # A fixed one-line source prevents command substitution, arbitrary exports,
    # password-command use, and path/binary/repository/source overrides.
    expected = "RESTIC_PASSWORD_FILE=" + PASSWORD_FILE + "\n"
    if value != expected:
        raise ObservationFailure("config_invalid")


def _validate_process_environment(value: Any) -> None:
    """Reject all caller-controlled Restic/path overrides without emitting them."""
    if not isinstance(value, Mapping):
        raise ObservationFailure("config_invalid")
    for key in ("RESTIC_PASSWORD_COMMAND", "RESTIC_PASSWORD"):
        if key in value:
            raise ObservationFailure("config_invalid")
    for key in ("RESTIC_REPOSITORY", "RESTIC_BINARY", "BACKUP_MOUNT", "ODYSSEUS_ROOT"):
        if key in value:
            raise ObservationFailure("config_invalid")
    password_file = value.get("RESTIC_PASSWORD_FILE")
    if password_file is not None and password_file != PASSWORD_FILE:
        raise ObservationFailure("config_invalid")


def _expected_uid(owner_lookup: Callable[[str], Any]) -> int:
    try:
        uid = owner_lookup(EXPECTED_OWNER).pw_uid
    except Exception:
        raise ObservationFailure("internal_error") from None
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise ObservationFailure("internal_error")
    return uid


def _production_owner_lookup(owner: str) -> Any:
    # ``pwd`` is Unix-only; import it only in the actual Debian-host path so
    # offline Windows tests can inject a safe owner projection.
    try:
        import pwd
        return pwd.getpwnam(owner)
    except Exception:
        raise ObservationFailure("internal_error") from None


def _safe_regular_file(path: str, *, expected_uid: int, lstat: Callable[[str], Any]) -> bool:
    try:
        info = lstat(path)
        mode = int(info.st_mode)
        return (
            stat.S_ISREG(mode) and int(info.st_uid) == expected_uid
            and stat.S_IMODE(mode) == 0o600
        )
    except Exception:
        return False


def _safe_repository(*, expected_uid: int, lstat: Callable[[str], Any]) -> bool:
    try:
        info = lstat(REPOSITORY)
        mode = int(info.st_mode)
        return (
            stat.S_ISDIR(mode) and int(info.st_uid) == expected_uid
            and (stat.S_IMODE(mode) & 0o022) == 0
        )
    except Exception:
        return False


def _safe_binary(*, lstat: Callable[[str], Any]) -> bool:
    try:
        info = lstat(RESTIC_BINARY)
        mode = int(info.st_mode)
        return (
            stat.S_ISREG(mode) and int(info.st_uid) == 0
            and bool(stat.S_IMODE(mode) & 0o100)
            and (stat.S_IMODE(mode) & 0o022) == 0
        )
    except Exception:
        return False


def _safe_source(*, expected_uid: int, lstat: Callable[[str], Any]) -> bool:
    try:
        info = lstat(SOURCE)
        mode = int(info.st_mode)
        permissions = stat.S_IMODE(mode)
        return (
            stat.S_ISDIR(mode) and int(info.st_uid) == expected_uid
            and (permissions & 0o500) == 0o500 and (permissions & 0o022) == 0
        )
    except Exception:
        return False


def _parse_time(value: Any) -> float:
    if not isinstance(value, str) or len(value) > 64:
        raise ObservationFailure("snapshot_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ObservationFailure("snapshot_invalid") from None
    if parsed.tzinfo is None:
        raise ObservationFailure("snapshot_invalid")
    return parsed.astimezone(timezone.utc).timestamp()


def _latest_pre_update_snapshot(raw: str, *, now: float) -> tuple[str, int]:
    if not isinstance(raw, str):
        raise ObservationFailure("malformed_output")
    if len(raw.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ObservationFailure("output_too_large")
    try:
        snapshots = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise ObservationFailure("malformed_output") from None
    if not isinstance(snapshots, list):
        raise ObservationFailure("malformed_output")
    if not snapshots:
        raise ObservationFailure("snapshot_missing")
    if len(snapshots) != 1:
        raise ObservationFailure("malformed_output")
    snapshot = snapshots[0]
    if not isinstance(snapshot, Mapping):
        raise ObservationFailure("malformed_output")
    snapshot_id = snapshot.get("id")
    tags = snapshot.get("tags")
    paths = snapshot.get("paths")
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ObservationFailure("snapshot_id_invalid")
    if not isinstance(tags, list) or any(not isinstance(tag, str) or len(tag) > 64 for tag in tags):
        raise ObservationFailure("snapshot_invalid")
    if "odysseus-pre-update" not in tags:
        raise ObservationFailure("snapshot_missing")
    if not isinstance(paths, list) or any(not isinstance(path, str) or len(path) > 256 for path in paths):
        raise ObservationFailure("snapshot_invalid")
    if SOURCE not in paths:
        raise ObservationFailure("source_path_missing")
    latest_time = _parse_time(snapshot.get("time"))
    if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(float(now)) or now < latest_time:
        raise ObservationFailure("snapshot_stale")
    age_seconds = int(float(now) - latest_time)
    if not 0 <= age_seconds <= MAX_SNAPSHOT_AGE_SECONDS:
        raise ObservationFailure("snapshot_stale")
    return snapshot_id, age_seconds


def _run_restic(runner: Callable[..., Any]) -> str:
    try:
        result = runner(
            list(RESTIC_SNAPSHOTS_COMMAND), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=TIMEOUT_SECONDS, check=False, encoding="utf-8", errors="replace",
            env={"RESTIC_PASSWORD_FILE": PASSWORD_FILE, "PATH": "/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired:
        raise ObservationFailure("timeout") from None
    except FileNotFoundError:
        raise ObservationFailure("restic_unavailable") from None
    except Exception:
        raise ObservationFailure("internal_error") from None
    if getattr(result, "returncode", None) != 0:
        raise ObservationFailure("snapshot_query_failed")
    stdout = getattr(result, "stdout", None)
    if not isinstance(stdout, str):
        raise ObservationFailure("malformed_output")
    return stdout


def collect_backup_snapshot_observation(
    *,
    runner: Callable[..., Any] = subprocess.run,
    read_config: Callable[[], str] = _read_fixed_config,
    lstat: Callable[[str], Any] = os.lstat,
    mount_checker: Callable[[str], bool] = os.path.ismount,
    owner_lookup: Callable[[str], Any] = _production_owner_lookup,
    clock: Callable[[], float] = time.time,
    process_environment: Mapping[str, Any] = os.environ,
) -> dict[str, Any]:
    """Return the fixed redacted observation; dependencies exist for offline tests."""
    try:
        _validate_process_environment(process_environment)
        expected_uid = _expected_uid(owner_lookup)
        if not bool(mount_checker(BACKUP_MOUNT)):
            raise ObservationFailure("mount_unavailable")
        if not _safe_binary(lstat=lstat):
            raise ObservationFailure("restic_unavailable")
        if not _safe_source(expected_uid=expected_uid, lstat=lstat):
            raise ObservationFailure("source_path_missing")
        if not _safe_repository(expected_uid=expected_uid, lstat=lstat):
            raise ObservationFailure("repository_unsafe")
        if not _safe_regular_file(CONFIG_PATH, expected_uid=expected_uid, lstat=lstat):
            raise ObservationFailure("config_invalid")
        _parse_config(read_config())
        if not _safe_regular_file(PASSWORD_FILE, expected_uid=expected_uid, lstat=lstat):
            raise ObservationFailure("password_file_unsafe")
        snapshot_id, snapshot_age_seconds = _latest_pre_update_snapshot(_run_restic(runner), now=clock())
        payload = {
            "schema_id": SCHEMA_ID, "status": "ok",
            "repository_identity": "restic_homeserver_backup_v1",
            "protected_source_identity": "odysseus_protected_source_v1",
            "snapshot_id": snapshot_id, "source_included": True,
            "snapshot_age_seconds": snapshot_age_seconds, "snapshot_fresh": True,
            "raw_stdout_visible": False, "raw_stderr_visible": False,
            "exception_text_visible": False, "environment_visible": False,
            "file_contents_visible": False, "paths_visible": False,
            "hostnames_visible": False, "secret_values_visible": False,
        }
        payload["evidence_sha256"] = _digest(payload)
        if set(payload) != _OK_KEYS:
            raise ObservationFailure("internal_error")
        return payload
    except ObservationFailure as exc:
        return blocked(exc.code)
    except Exception:
        return blocked("internal_error")


def main() -> int:
    try:
        payload = collect_backup_snapshot_observation()
    except Exception:
        payload = blocked("internal_error")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

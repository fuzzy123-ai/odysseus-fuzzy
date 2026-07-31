#!/usr/bin/env python3
"""Fixed-key, value-free diagnosis of the Restic observation contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.redacted_backup_configuration_diagnostic.v1"
RESTIC_BINARY = "/usr/bin/restic"
BACKUP_MOUNT = "/mnt/backup"
REPOSITORY = "/mnt/backup/restic/homeserver"
SOURCE = "/opt/odysseus"
CONFIG_PATH = "/home/homebase/.config/odysseus-backup/restic-observation.env"
PASSWORD_FILE = "/home/homebase/.config/odysseus-backup/restic-password"
EXPECTED_OWNER = "homebase"
_PROOFS = frozenset(
    {
        "process_environment_safe",
        "owner_resolved",
        "backup_mount_present",
        "restic_binary_safe",
        "source_directory_safe",
        "repository_directory_safe",
        "configuration_metadata_safe",
        "configuration_content_exact",
        "password_file_safe",
    }
)
_VISIBILITY = frozenset(
    {
        "configuration_value_visible",
        "credential_value_visible",
        "value_length_visible",
        "value_hash_visible",
        "raw_stdout_visible",
        "raw_stderr_visible",
        "exception_text_visible",
        "environment_visible",
        "paths_visible",
        "hostnames_visible",
        "secret_values_visible",
    }
)
_ERRORS = frozenset(
    {
        "none",
        "internal_error",
        "published_blob_unavailable",
        "published_blob_mismatch",
        "transport_timeout",
        "transport_failed",
        "transport_invalid",
        "invalid_invocation",
    }
)
_KEYS = frozenset(
    {
        "schema_id",
        "status",
        "error_code",
        "contract_ready",
        "retry_permitted",
        *_PROOFS,
        *_VISIBILITY,
        "evidence_sha256",
    }
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _digest(payload: Mapping[str, Any]) -> str:
    projected = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(
        json.dumps(projected, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def envelope(
    status: str,
    error_code: str,
    proofs: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    flags = {key: False for key in _PROOFS | _VISIBILITY}
    if proofs is not None:
        flags.update(
            {
                key: value if type(value) is bool else False
                for key, value in proofs.items()
                if key in _PROOFS
            }
        )
    contract_ready = all(flags[key] for key in _PROOFS)
    payload = {
        "schema_id": SCHEMA_ID,
        "status": status if status in {"observed", "blocked"} else "blocked",
        "error_code": error_code if error_code in _ERRORS else "internal_error",
        "contract_ready": contract_ready,
        "retry_permitted": False,
        **flags,
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def validate_envelope(value: Any) -> bool:
    if (
        type(value) is not dict
        or set(value) != _KEYS
        or value.get("schema_id") != SCHEMA_ID
        or value.get("status") not in {"observed", "blocked"}
        or value.get("error_code") not in _ERRORS
        or value.get("retry_permitted") is not False
        or any(type(value.get(key)) is not bool for key in _PROOFS | _VISIBILITY | {"contract_ready"})
        or any(value[key] is not False for key in _VISIBILITY)
    ):
        return False
    if value["contract_ready"] != all(value[key] for key in _PROOFS):
        return False
    if (value["status"] == "observed") != (value["error_code"] == "none"):
        return False
    digest = value.get("evidence_sha256")
    return bool(
        isinstance(digest, str)
        and _HEX64.fullmatch(digest)
        and digest == _digest(value)
    )


def _production_owner_lookup(owner: str) -> Any:
    import pwd

    return pwd.getpwnam(owner)


def _safe_environment(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    for key in ("RESTIC_PASSWORD", "RESTIC_PASSWORD_COMMAND"):
        if key in value:
            return False
    for key in (
        "RESTIC_REPOSITORY",
        "RESTIC_BINARY",
        "RESTIC_BIN",
        "BACKUP_MOUNT",
        "ODYSSEUS_ROOT",
        "SOURCE",
        "NEXTCLOUD_ROOT",
        "HOMEBASE_HOME",
        "DB_DUMP_ROOT",
        "DB_DUMP_STAGING",
        "RESTIC_USE_SUDO",
        "RESTIC_REPAIR_REPO_OWNER",
    ):
        if key in value:
            return False
    configured = value.get("RESTIC_PASSWORD_FILE")
    return configured is None or configured == PASSWORD_FILE


def _uid(owner_lookup: Callable[[str], Any]) -> int | None:
    try:
        value = owner_lookup(EXPECTED_OWNER).pw_uid
    except Exception:
        return None
    return value if type(value) is int and value >= 0 else None


def _safe_file(
    path: str,
    *,
    uid: int | None,
    lstat: Callable[[str], Any],
    owner: int,
    mode: int | None = None,
    executable: bool = False,
) -> bool:
    if uid is None:
        return False
    try:
        info = lstat(path)
        permissions = stat.S_IMODE(int(info.st_mode))
        return bool(
            stat.S_ISREG(int(info.st_mode))
            and int(info.st_uid) == owner
            and (permissions == mode if mode is not None else (permissions & 0o022) == 0)
            and (not executable or bool(permissions & 0o100))
        )
    except Exception:
        return False


def _safe_directory(
    path: str,
    *,
    uid: int | None,
    lstat: Callable[[str], Any],
    required: int,
) -> bool:
    if uid is None:
        return False
    try:
        info = lstat(path)
        permissions = stat.S_IMODE(int(info.st_mode))
        return bool(
            stat.S_ISDIR(int(info.st_mode))
            and int(info.st_uid) == uid
            and permissions & required == required
            and permissions & 0o022 == 0
        )
    except Exception:
        return False


def _read_config() -> str:
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        return handle.read(4097)


def collect_backup_configuration_diagnostic(
    *,
    process_environment: Mapping[str, Any] = os.environ,
    owner_lookup: Callable[[str], Any] = _production_owner_lookup,
    lstat: Callable[[str], Any] = os.lstat,
    mount_checker: Callable[[str], bool] = os.path.ismount,
    config_reader: Callable[[], str] = _read_config,
) -> dict[str, Any]:
    try:
        uid = _uid(owner_lookup)
        try:
            mounted = bool(mount_checker(BACKUP_MOUNT))
        except Exception:
            mounted = False
        try:
            content = config_reader()
            content_exact = (
                isinstance(content, str)
                and content
                == "RESTIC_PASSWORD_FILE=" + PASSWORD_FILE + "\n"
            )
        except Exception:
            content_exact = False
        proofs = {
            "process_environment_safe": _safe_environment(process_environment),
            "owner_resolved": uid is not None,
            "backup_mount_present": mounted,
            "restic_binary_safe": _safe_file(
                RESTIC_BINARY,
                uid=uid,
                lstat=lstat,
                owner=0,
                executable=True,
            ),
            "source_directory_safe": _safe_directory(
                SOURCE,
                uid=uid,
                lstat=lstat,
                required=0o500,
            ),
            "repository_directory_safe": _safe_directory(
                REPOSITORY,
                uid=uid,
                lstat=lstat,
                required=0o100,
            ),
            "configuration_metadata_safe": _safe_file(
                CONFIG_PATH,
                uid=uid,
                lstat=lstat,
                owner=uid if uid is not None else -1,
                mode=0o600,
            ),
            "configuration_content_exact": content_exact,
            "password_file_safe": _safe_file(
                PASSWORD_FILE,
                uid=uid,
                lstat=lstat,
                owner=uid if uid is not None else -1,
                mode=0o600,
            ),
        }
        return envelope("observed", "none", proofs)
    except Exception:
        return envelope("blocked", "internal_error")


def main() -> int:
    payload = collect_backup_configuration_diagnostic()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload["status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

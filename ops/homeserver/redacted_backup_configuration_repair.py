#!/usr/bin/env python3
"""One-shot, fixed-path repair of the Restic observation contract.

The credential bytes are never read or changed.  The only credential-side
effect is hardening the already-existing regular file's owner and mode through
its open file descriptor.  The non-secret pointer configuration is replaced
atomically in the same validated directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from typing import Any, Mapping


SCHEMA_ID = "odysseus.redacted_backup_configuration_repair.v1"
CONFIG_DIRECTORY = "/home/homebase/.config/odysseus-backup"
CONFIG_NAME = "restic-observation.env"
PASSWORD_NAME = "restic-password"
TEMPORARY_NAME = ".restic-observation.env.ops-alert-repair"
EXPECTED_OWNER = "homebase"
MAX_SECRET_BYTES = 16_384
CONFIG_BYTES = (
    "RESTIC_PASSWORD_FILE="
    + CONFIG_DIRECTORY
    + "/"
    + PASSWORD_NAME
    + "\n"
).encode("ascii")
_O_CLOEXEC = int(getattr(os, "O_CLOEXEC", 0))
_O_DIRECTORY = int(getattr(os, "O_DIRECTORY", 0))
_O_NOFOLLOW = int(getattr(os, "O_NOFOLLOW", 0))

_VISIBILITY = frozenset(
    {
        "raw_stdout_visible",
        "raw_stderr_visible",
        "exception_text_visible",
        "environment_visible",
        "paths_visible",
        "secret_values_visible",
        "file_contents_visible",
        "value_length_visible",
        "value_hash_visible",
    }
)
_ERRORS = frozenset(
    {
        "none",
        "execution_disabled",
        "invalid_invocation",
        "preflight_failed",
        "execution_failed",
        "mutation_ambiguous",
        "published_blob_mismatch",
        "transport_timeout",
        "transport_failed",
        "transport_invalid",
    }
)
_KEYS = frozenset(
    {
        "schema_id",
        "status",
        "error_code",
        "effect_may_have_occurred",
        "password_metadata_repaired",
        "configuration_replaced",
        "automatic_rollback_attempted",
        "automatic_rollback_succeeded",
        "retry_permitted",
        *_VISIBILITY,
        "evidence_sha256",
    }
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _digest(payload: Mapping[str, Any]) -> str:
    projected = {
        key: value for key, value in payload.items() if key != "evidence_sha256"
    }
    return hashlib.sha256(
        json.dumps(
            projected,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


def envelope(
    status: str,
    error_code: str,
    *,
    effect: bool = False,
    password_repaired: bool = False,
    configuration_replaced: bool = False,
    rollback_attempted: bool = False,
    rollback_succeeded: bool = False,
) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID,
        "status": status
        if status in {"blocked", "succeeded", "rolled_back", "unknown"}
        else "unknown",
        "error_code": error_code if error_code in _ERRORS else "mutation_ambiguous",
        "effect_may_have_occurred": effect is True,
        "password_metadata_repaired": password_repaired is True,
        "configuration_replaced": configuration_replaced is True,
        "automatic_rollback_attempted": rollback_attempted is True,
        "automatic_rollback_succeeded": rollback_succeeded is True,
        "retry_permitted": False,
        **{key: False for key in _VISIBILITY},
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def validate_envelope(value: Any) -> bool:
    if (
        type(value) is not dict
        or set(value) != _KEYS
        or value.get("schema_id") != SCHEMA_ID
        or value.get("status")
        not in {"blocked", "succeeded", "rolled_back", "unknown"}
        or value.get("error_code") not in _ERRORS
        or value.get("retry_permitted") is not False
        or any(
            type(value.get(key)) is not bool
            for key in {
                "effect_may_have_occurred",
                "password_metadata_repaired",
                "configuration_replaced",
                "automatic_rollback_attempted",
                "automatic_rollback_succeeded",
                *_VISIBILITY,
            }
        )
        or any(value[key] is not False for key in _VISIBILITY)
    ):
        return False
    state = (
        value["status"],
        value["error_code"],
        value["effect_may_have_occurred"],
        value["password_metadata_repaired"],
        value["configuration_replaced"],
        value["automatic_rollback_attempted"],
        value["automatic_rollback_succeeded"],
    )
    valid_state = (
        (
            state[0] == "blocked"
            and state[1]
            in {
                "execution_disabled",
                "invalid_invocation",
                "preflight_failed",
                "published_blob_mismatch",
                "transport_timeout",
                "transport_failed",
                "transport_invalid",
            }
            and state[2:] == (False, False, False, False, False)
        )
        or state == ("succeeded", "none", True, True, True, False, False)
        or state
        == ("rolled_back", "execution_failed", True, False, False, True, True)
        or (
            state[0] == "unknown"
            and state[1] == "mutation_ambiguous"
            and state[2] is True
            and state[6] is False
        )
    )
    digest = value.get("evidence_sha256")
    return bool(
        valid_state
        and isinstance(digest, str)
        and _HEX64.fullmatch(digest)
        and digest == _digest(value)
    )


class _ProductionOperations:
    def owner(self) -> tuple[int, int]:
        import pwd

        record = pwd.getpwnam(EXPECTED_OWNER)
        return record.pw_uid, record.pw_gid

    def open_directory(self) -> int:
        if not _O_DIRECTORY or not _O_NOFOLLOW:
            raise OSError("unsupported_platform")
        return os.open(
            CONFIG_DIRECTORY,
            os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC,
        )

    def stat_fd(self, descriptor: int) -> Any:
        return os.fstat(descriptor)

    def stat_at(self, directory_fd: int, name: str) -> Any:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)

    def open_at(
        self,
        directory_fd: int,
        name: str,
        flags: int,
        mode: int = 0o600,
    ) -> int:
        return os.open(name, flags, mode, dir_fd=directory_fd)

    def fchown(self, descriptor: int, uid: int, gid: int) -> None:
        os.fchown(descriptor, uid, gid)

    def fchmod(self, descriptor: int, mode: int) -> None:
        os.fchmod(descriptor, mode)

    def write(self, descriptor: int, value: bytes) -> int:
        return os.write(descriptor, value)

    def read(self, descriptor: int, maximum: int) -> bytes:
        return os.read(descriptor, maximum)

    def fsync(self, descriptor: int) -> None:
        os.fsync(descriptor)

    def replace_at(self, directory_fd: int, source: str, target: str) -> None:
        os.replace(
            source,
            target,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )

    def unlink_at(self, directory_fd: int, name: str) -> None:
        os.unlink(name, dir_fd=directory_fd)

    def close(self, descriptor: int) -> None:
        os.close(descriptor)


def _valid_owner(value: Any) -> tuple[int, int] | None:
    try:
        uid, gid = value
    except Exception:
        return None
    if (
        type(uid) is not int
        or type(gid) is not int
        or uid < 0
        or gid < 0
    ):
        return None
    return uid, gid


def _safe_directory(info: Any, uid: int) -> bool:
    try:
        permissions = stat.S_IMODE(int(info.st_mode))
        return bool(
            stat.S_ISDIR(int(info.st_mode))
            and int(info.st_uid) == uid
            and permissions == 0o700
        )
    except Exception:
        return False


def _safe_password(info: Any, uid: int) -> bool:
    try:
        return bool(
            stat.S_ISREG(int(info.st_mode))
            and int(info.st_nlink) == 1
            and 0 < int(info.st_size) <= MAX_SECRET_BYTES
            and int(info.st_uid) in {0, uid}
        )
    except Exception:
        return False


def _safe_configuration(info: Any, uid: int) -> bool:
    try:
        return bool(
            stat.S_ISREG(int(info.st_mode))
            and int(info.st_nlink) == 1
            and int(info.st_uid) == uid
            and stat.S_IMODE(int(info.st_mode)) == 0o600
        )
    except Exception:
        return False


def _write_all(operations: Any, descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = operations.write(descriptor, value[offset:])
        if type(written) is not int or written <= 0 or written > len(value) - offset:
            raise OSError("write_failed")
        offset += written


def _configuration_is_exact(operations: Any, directory_fd: int, uid: int) -> bool:
    descriptor: int | None = None
    try:
        descriptor = operations.open_at(
            directory_fd,
            CONFIG_NAME,
            os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC,
        )
        if not _safe_configuration(operations.stat_fd(descriptor), uid):
            return False
        value = operations.read(descriptor, len(CONFIG_BYTES) + 1)
        return type(value) is bytes and value == CONFIG_BYTES
    except Exception:
        return False
    finally:
        if descriptor is not None:
            try:
                operations.close(descriptor)
            except Exception:
                pass


def repair_backup_configuration(
    *,
    execute: bool = False,
    operations: Any = None,
) -> dict[str, Any]:
    if execute is not True:
        return envelope("blocked", "execution_disabled")
    selected = _ProductionOperations() if operations is None else operations
    directory_fd: int | None = None
    password_fd: int | None = None
    temporary_fd: int | None = None
    temporary_created = False
    password_mutated = False
    configuration_replaced = False
    old_metadata: tuple[int, int, int] | None = None
    try:
        owner = _valid_owner(selected.owner())
        if owner is None:
            return envelope("blocked", "preflight_failed")
        uid, gid = owner
        directory_fd = selected.open_directory()
        if not _safe_directory(selected.stat_fd(directory_fd), uid):
            return envelope("blocked", "preflight_failed")
        try:
            selected.stat_at(directory_fd, TEMPORARY_NAME)
        except FileNotFoundError:
            pass
        else:
            return envelope("blocked", "preflight_failed")
        password_fd = selected.open_at(
            directory_fd,
            PASSWORD_NAME,
            os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC,
        )
        password_info = selected.stat_fd(password_fd)
        if not _safe_password(password_info, uid):
            return envelope("blocked", "preflight_failed")
        configuration_info = selected.stat_at(directory_fd, CONFIG_NAME)
        if not _safe_configuration(configuration_info, uid):
            return envelope("blocked", "preflight_failed")
        old_metadata = (
            int(password_info.st_uid),
            int(password_info.st_gid),
            stat.S_IMODE(int(password_info.st_mode)),
        )

        temporary_fd = selected.open_at(
            directory_fd,
            TEMPORARY_NAME,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | _O_NOFOLLOW
            | _O_CLOEXEC,
            0o600,
        )
        temporary_created = True
        selected.fchown(temporary_fd, uid, gid)
        selected.fchmod(temporary_fd, 0o600)
        _write_all(selected, temporary_fd, CONFIG_BYTES)
        selected.fsync(temporary_fd)
        selected.close(temporary_fd)
        temporary_fd = None

        selected.fchown(password_fd, uid, gid)
        password_mutated = True
        selected.fchmod(password_fd, 0o600)
        selected.fsync(password_fd)
        hardened = selected.stat_fd(password_fd)
        if (
            int(hardened.st_uid) != uid
            or int(hardened.st_gid) != gid
            or stat.S_IMODE(int(hardened.st_mode)) != 0o600
        ):
            raise OSError("metadata_not_hardened")

        selected.replace_at(directory_fd, TEMPORARY_NAME, CONFIG_NAME)
        temporary_created = False
        configuration_replaced = True
        selected.fsync(directory_fd)
        final_configuration = selected.stat_at(directory_fd, CONFIG_NAME)
        if not _safe_configuration(
            final_configuration,
            uid,
        ) or not _configuration_is_exact(selected, directory_fd, uid):
            raise OSError("configuration_not_safe")
        return envelope(
            "succeeded",
            "none",
            effect=True,
            password_repaired=True,
            configuration_replaced=True,
        )
    except Exception:
        if configuration_replaced:
            return envelope(
                "unknown",
                "mutation_ambiguous",
                effect=True,
                password_repaired=password_mutated,
                configuration_replaced=True,
            )
        rollback_attempted = temporary_created or password_mutated
        rollback_succeeded = True
        if password_mutated and password_fd is not None and old_metadata is not None:
            try:
                selected.fchown(password_fd, old_metadata[0], old_metadata[1])
                selected.fchmod(password_fd, old_metadata[2])
                selected.fsync(password_fd)
                restored = selected.stat_fd(password_fd)
                rollback_succeeded = bool(
                    int(restored.st_uid) == old_metadata[0]
                    and int(restored.st_gid) == old_metadata[1]
                    and stat.S_IMODE(int(restored.st_mode)) == old_metadata[2]
                )
            except Exception:
                rollback_succeeded = False
        if temporary_created and directory_fd is not None:
            try:
                selected.unlink_at(directory_fd, TEMPORARY_NAME)
                selected.fsync(directory_fd)
            except Exception:
                rollback_succeeded = False
        if rollback_attempted and rollback_succeeded:
            return envelope(
                "rolled_back",
                "execution_failed",
                effect=True,
                rollback_attempted=True,
                rollback_succeeded=True,
            )
        if rollback_attempted:
            return envelope(
                "unknown",
                "mutation_ambiguous",
                effect=True,
                password_repaired=password_mutated,
                rollback_attempted=True,
            )
        return envelope("blocked", "preflight_failed")
    finally:
        for descriptor in (temporary_fd, password_fd, directory_fd):
            if descriptor is not None:
                try:
                    selected.close(descriptor)
                except Exception:
                    pass


def main() -> int:
    payload = envelope("blocked", "invalid_invocation")
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

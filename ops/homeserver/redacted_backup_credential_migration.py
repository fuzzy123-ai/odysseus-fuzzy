#!/usr/bin/env python3
"""Rollback-safe, redacted migration of the Restic password-file contract.

The only secret input is the file named by ``RESTIC_PASSWORD_FILE``.  Secret
bytes are copied internally between file descriptors and are never projected
into the result.  All destination operations are bound to one validated
directory descriptor and use fixed entry names.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
from typing import Any, Mapping


SCHEMA_ID = "odysseus.redacted_backup_credential_migration.v1"
EXPECTED_OWNER = "homebase"
CONFIG_DIRECTORY = "/home/homebase/.config/odysseus-backup"
DESTINATION_NAME = "restic-password"
CONFIGURATION_NAME = "restic-observation.env"
PASSWORD_TEMPORARY_NAME = ".restic-password.ops-alert-migration"
CONFIGURATION_TEMPORARY_NAME = ".restic-observation.env.ops-alert-migration"
PASSWORD_ROLLBACK_NAME = ".restic-password.ops-alert-rollback"
CONFIGURATION_ROLLBACK_NAME = ".restic-observation.env.ops-alert-rollback"
DESTINATION_PATH = CONFIG_DIRECTORY + "/" + DESTINATION_NAME
MAX_PATH_BYTES = 4_096
MAX_SECRET_BYTES = 16_384
CONFIGURATION_BYTES = (
    "RESTIC_PASSWORD_FILE=" + DESTINATION_PATH + "\n"
).encode("ascii")
RESTIC_COMMAND = (
    "/usr/bin/restic",
    "-r",
    "/mnt/backup/restic/homeserver",
    "--no-lock",
    "snapshots",
    "--latest",
    "1",
    "--json",
)
RESTIC_ENVIRONMENT = {"RESTIC_PASSWORD_FILE": DESTINATION_PATH}

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
        "invalid_environment",
        "preflight_failed",
        "execution_failed",
        "mutation_ambiguous",
        "invalid_invocation",
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
        "credential_installed",
        "configuration_installed",
        "post_change_readback_succeeded",
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
    credential_installed: bool = False,
    configuration_installed: bool = False,
    readback_succeeded: bool = False,
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
        "credential_installed": credential_installed is True,
        "configuration_installed": configuration_installed is True,
        "post_change_readback_succeeded": readback_succeeded is True,
        "automatic_rollback_attempted": rollback_attempted is True,
        "automatic_rollback_succeeded": rollback_succeeded is True,
        "retry_permitted": False,
        **{key: False for key in _VISIBILITY},
    }
    payload["evidence_sha256"] = _digest(payload)
    return payload


def validate_envelope(value: Any) -> bool:
    boolean_keys = {
        "effect_may_have_occurred",
        "credential_installed",
        "configuration_installed",
        "post_change_readback_succeeded",
        "automatic_rollback_attempted",
        "automatic_rollback_succeeded",
        *_VISIBILITY,
    }
    if (
        type(value) is not dict
        or set(value) != _KEYS
        or value.get("schema_id") != SCHEMA_ID
        or value.get("status")
        not in {"blocked", "succeeded", "rolled_back", "unknown"}
        or value.get("error_code") not in _ERRORS
        or value.get("retry_permitted") is not False
        or any(type(value.get(key)) is not bool for key in boolean_keys)
        or any(value[key] is not False for key in _VISIBILITY)
    ):
        return False
    state = (
        value["status"],
        value["error_code"],
        value["effect_may_have_occurred"],
        value["credential_installed"],
        value["configuration_installed"],
        value["post_change_readback_succeeded"],
        value["automatic_rollback_attempted"],
        value["automatic_rollback_succeeded"],
    )
    valid_state = (
        (
            state[0] == "blocked"
            and state[1]
            in {
                "execution_disabled",
                "invalid_environment",
                "preflight_failed",
                "invalid_invocation",
                "published_blob_mismatch",
                "transport_timeout",
                "transport_failed",
                "transport_invalid",
            }
            and state[2:] == (False, False, False, False, False, False)
        )
        or state == ("succeeded", "none", True, True, True, True, False, False)
        or state
        == (
            "rolled_back",
            "execution_failed",
            True,
            False,
            False,
            False,
            True,
            True,
        )
        or (
            state[0:6]
            == (
                "unknown",
                "mutation_ambiguous",
                True,
                False,
                False,
                False,
            )
            and state[6] in {False, True}
            and state[7] is False
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

    def open_source(self, path: str, flags: int) -> int:
        if not _O_NOFOLLOW:
            raise OSError("unsupported_platform")
        return os.open(path, flags)

    def open_directory(self, flags: int) -> int:
        if not _O_DIRECTORY or not _O_NOFOLLOW:
            raise OSError("unsupported_platform")
        return os.open(CONFIG_DIRECTORY, flags)

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

    def read(self, descriptor: int, maximum: int) -> bytes:
        return os.read(descriptor, maximum)

    def write(self, descriptor: int, value: bytes) -> int:
        return os.write(descriptor, value)

    def fchown(self, descriptor: int, uid: int, gid: int) -> None:
        os.fchown(descriptor, uid, gid)

    def fchmod(self, descriptor: int, mode: int) -> None:
        os.fchmod(descriptor, mode)

    def fsync(self, descriptor: int) -> None:
        os.fsync(descriptor)

    def supports_noreplace(self) -> bool:
        import ctypes

        return getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None) is not None

    def rename_noreplace_at(
        self,
        directory_fd: int,
        source: str,
        target: str,
    ) -> None:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError("unsupported_platform")
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            directory_fd,
            source.encode("ascii"),
            directory_fd,
            target.encode("ascii"),
            1,
        )
        if result != 0:
            raise OSError(ctypes.get_errno(), "rename_noreplace_failed")

    def unlink_at(self, directory_fd: int, name: str) -> None:
        os.unlink(name, dir_fd=directory_fd)

    def run_restic(self, command: tuple[str, ...], environment: Mapping[str, str]) -> int:
        completed = subprocess.run(
            command,
            check=False,
            close_fds=True,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        return completed.returncode

    def close(self, descriptor: int) -> None:
        os.close(descriptor)


def _valid_owner(value: Any) -> tuple[int, int] | None:
    try:
        uid, gid = value
    except Exception:
        return None
    if type(uid) is not int or type(gid) is not int or uid < 0 or gid < 0:
        return None
    return uid, gid


def _valid_source_path(value: Any) -> str | None:
    if type(value) is not str or not value or "\x00" in value:
        return None
    try:
        encoded = value.encode("utf-8", "strict")
    except (UnicodeEncodeError, UnicodeError):
        return None
    if not encoded or len(encoded) > MAX_PATH_BYTES:
        return None
    if not value.startswith("/") or value.startswith("//"):
        return None
    if not posixpath.isabs(value) or posixpath.normpath(value) != value:
        return None
    return value


def _safe_source(info: Any, uid: int) -> bool:
    try:
        mode = stat.S_IMODE(int(info.st_mode))
        return bool(
            stat.S_ISREG(int(info.st_mode))
            and int(info.st_nlink) == 1
            and 0 < int(info.st_size) <= MAX_SECRET_BYTES
            and int(info.st_uid) in {0, uid}
            and mode & 0o022 == 0
            and mode & 0o7000 == 0
        )
    except Exception:
        return False


def _safe_directory(info: Any, uid: int) -> bool:
    try:
        mode = stat.S_IMODE(int(info.st_mode))
        return bool(
            stat.S_ISDIR(int(info.st_mode))
            and int(info.st_uid) == uid
            and mode & 0o700 == 0o700
            and mode & 0o022 == 0
            and mode & 0o7000 == 0
        )
    except Exception:
        return False


def _safe_private_file(
    info: Any,
    uid: int,
    gid: int,
    expected_size: int,
) -> bool:
    try:
        return bool(
            stat.S_ISREG(int(info.st_mode))
            and int(info.st_nlink) == 1
            and int(info.st_uid) == uid
            and int(info.st_gid) == gid
            and stat.S_IMODE(int(info.st_mode)) == 0o600
            and int(info.st_size) == expected_size
        )
    except Exception:
        return False


def _snapshot(info: Any) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mode),
        int(info.st_uid),
        int(info.st_gid),
        int(info.st_nlink),
        int(info.st_size),
        int(info.st_mtime_ns),
    )


def _source_snapshot(
    info: Any,
) -> tuple[int, int, int, int, int, int, int, int, int]:
    return (
        *_snapshot(info),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _identity(info: Any) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _same_identity(info: Any, expected: tuple[int, int]) -> bool:
    try:
        return _identity(info) == expected
    except Exception:
        return False


def _same_snapshot(info: Any, expected: tuple[int, ...]) -> bool:
    try:
        return _snapshot(info) == expected
    except Exception:
        return False


def _same_source_snapshot(info: Any, expected: tuple[int, ...]) -> bool:
    try:
        return _source_snapshot(info) == expected
    except Exception:
        return False


def _optional_stat(operations: Any, directory_fd: int, name: str) -> Any | None:
    try:
        return operations.stat_at(directory_fd, name)
    except FileNotFoundError:
        return None


def _read_exact(operations: Any, descriptor: int, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = expected_size
    while remaining:
        value = operations.read(descriptor, remaining)
        if type(value) is not bytes or not value or len(value) > remaining:
            raise OSError("read_failed")
        chunks.append(value)
        remaining -= len(value)
    extra = operations.read(descriptor, 1)
    if type(extra) is not bytes or extra:
        raise OSError("read_failed")
    return b"".join(chunks)


def _write_all(operations: Any, descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = operations.write(descriptor, value[offset:])
        if type(written) is not int or written <= 0 or written > len(value) - offset:
            raise OSError("write_failed")
        offset += written


def _read_named_exact(
    operations: Any,
    directory_fd: int,
    name: str,
    uid: int,
    gid: int,
    expected: bytes,
) -> bool:
    descriptor: int | None = None
    try:
        descriptor = operations.open_at(
            directory_fd,
            name,
            os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC,
        )
        info = operations.stat_fd(descriptor)
        if not _safe_private_file(info, uid, gid, len(expected)):
            return False
        return _read_exact(operations, descriptor, len(expected)) == expected
    except Exception:
        return False
    finally:
        if descriptor is not None:
            try:
                operations.close(descriptor)
            except Exception:
                pass


def _entry_matches(
    operations: Any,
    directory_fd: int,
    name: str,
    expected: tuple[int, ...] | None,
) -> bool:
    try:
        current = _optional_stat(operations, directory_fd, name)
    except Exception:
        return False
    if expected is None:
        return current is None
    return current is not None and _same_snapshot(current, expected)


def _entry_identity_matches(
    operations: Any,
    directory_fd: int,
    name: str,
    expected: tuple[int, int] | None,
) -> bool:
    try:
        current = _optional_stat(operations, directory_fd, name)
    except Exception:
        return False
    if expected is None:
        return current is None
    return current is not None and _same_identity(current, expected)


def _remove_if_new(
    operations: Any,
    directory_fd: int,
    name: str,
    expected_new: tuple[int, int] | None,
) -> None:
    if expected_new is None:
        return
    current = _optional_stat(operations, directory_fd, name)
    if current is None:
        return
    if not _same_identity(current, expected_new):
        return
    try:
        operations.unlink_at(directory_fd, name)
    except Exception:
        pass


def _restore_entry(
    operations: Any,
    directory_fd: int,
    target: str,
    rollback: str,
    old: tuple[int, ...] | None,
    preserved: tuple[int, ...] | None,
    new: tuple[int, int] | None,
) -> None:
    try:
        target_info = _optional_stat(operations, directory_fd, target)
        rollback_info = _optional_stat(operations, directory_fd, rollback)
    except Exception:
        return
    if old is None:
        if (
            rollback_info is None
            and target_info is not None
            and new is not None
            and _same_identity(target_info, new)
        ):
            try:
                operations.unlink_at(directory_fd, target)
            except Exception:
                pass
        return
    if (
        rollback_info is not None
        and preserved is not None
        and _same_source_snapshot(rollback_info, preserved)
    ):
        if target_info is not None:
            if new is None or not _same_identity(target_info, new):
                return
            try:
                operations.unlink_at(directory_fd, target)
            except Exception:
                return
        try:
            operations.rename_noreplace_at(directory_fd, rollback, target)
        except Exception:
            pass


def _rollback(
    operations: Any,
    directory_fd: int,
    uid: int,
    old_directory_mode: int,
    old_password: tuple[int, ...] | None,
    old_configuration: tuple[int, ...] | None,
    preserved_password: tuple[int, ...] | None,
    preserved_configuration: tuple[int, ...] | None,
    new_password: tuple[int, int] | None,
    new_configuration: tuple[int, int] | None,
) -> bool:
    _restore_entry(
        operations,
        directory_fd,
        CONFIGURATION_NAME,
        CONFIGURATION_ROLLBACK_NAME,
        old_configuration,
        preserved_configuration,
        new_configuration,
    )
    _restore_entry(
        operations,
        directory_fd,
        DESTINATION_NAME,
        PASSWORD_ROLLBACK_NAME,
        old_password,
        preserved_password,
        new_password,
    )
    _remove_if_new(
        operations,
        directory_fd,
        CONFIGURATION_TEMPORARY_NAME,
        new_configuration,
    )
    _remove_if_new(
        operations,
        directory_fd,
        PASSWORD_TEMPORARY_NAME,
        new_password,
    )
    durable = True
    try:
        operations.fchmod(directory_fd, old_directory_mode)
        operations.fsync(directory_fd)
    except Exception:
        durable = False
    try:
        directory_info = operations.stat_fd(directory_fd)
        directory_restored = bool(
            stat.S_ISDIR(int(directory_info.st_mode))
            and int(directory_info.st_uid) == uid
            and stat.S_IMODE(int(directory_info.st_mode)) == old_directory_mode
        )
    except Exception:
        directory_restored = False
    return bool(
        durable
        and directory_restored
        and _entry_matches(
            operations, directory_fd, DESTINATION_NAME, old_password
        )
        and _entry_matches(
            operations, directory_fd, CONFIGURATION_NAME, old_configuration
        )
        and _entry_matches(
            operations, directory_fd, PASSWORD_TEMPORARY_NAME, None
        )
        and _entry_matches(
            operations, directory_fd, CONFIGURATION_TEMPORARY_NAME, None
        )
        and _entry_matches(
            operations, directory_fd, PASSWORD_ROLLBACK_NAME, None
        )
        and _entry_matches(
            operations, directory_fd, CONFIGURATION_ROLLBACK_NAME, None
        )
    )


def migrate_backup_credential(
    *,
    execute: bool = False,
    environment: Mapping[str, Any] | None = None,
    operations: Any = None,
) -> dict[str, Any]:
    if execute is not True:
        return envelope("blocked", "execution_disabled")
    selected_environment = os.environ if environment is None else environment
    try:
        environment_valid = bool(
            isinstance(selected_environment, Mapping)
            and "RESTIC_PASSWORD_FILE" in selected_environment
            and "RESTIC_PASSWORD" not in selected_environment
            and "RESTIC_PASSWORD_COMMAND" not in selected_environment
        )
        source_value = (
            selected_environment.get("RESTIC_PASSWORD_FILE")
            if environment_valid
            else None
        )
    except Exception:
        environment_valid = False
        source_value = None
    if not environment_valid:
        return envelope("blocked", "invalid_environment")
    source_path = _valid_source_path(source_value)
    if source_path is None:
        return envelope("blocked", "invalid_environment")

    selected = _ProductionOperations() if operations is None else operations
    source_fd: int | None = None
    directory_fd: int | None = None
    password_temporary_fd: int | None = None
    configuration_temporary_fd: int | None = None
    old_directory_mode: int | None = None
    old_password: tuple[int, ...] | None = None
    old_configuration: tuple[int, ...] | None = None
    preserved_password: tuple[int, ...] | None = None
    preserved_configuration: tuple[int, ...] | None = None
    new_password: tuple[int, int] | None = None
    new_configuration: tuple[int, int] | None = None
    mutation_possible = False
    uid: int | None = None
    try:
        owner = _valid_owner(selected.owner())
        if owner is None:
            return envelope("blocked", "preflight_failed")
        uid, gid = owner
        source_fd = selected.open_source(
            source_path,
            os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC,
        )
        source_info = selected.stat_fd(source_fd)
        if not _safe_source(source_info, uid):
            return envelope("blocked", "preflight_failed")
        source_snapshot = _source_snapshot(source_info)
        secret = _read_exact(selected, source_fd, int(source_info.st_size))
        if not _same_source_snapshot(selected.stat_fd(source_fd), source_snapshot):
            return envelope("blocked", "preflight_failed")

        directory_fd = selected.open_directory(
            os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC,
        )
        directory_info = selected.stat_fd(directory_fd)
        if not _safe_directory(directory_info, uid):
            return envelope("blocked", "preflight_failed")
        try:
            noreplace_supported = selected.supports_noreplace() is True
        except Exception:
            noreplace_supported = False
        if not noreplace_supported:
            return envelope("blocked", "preflight_failed")
        old_directory_mode = stat.S_IMODE(int(directory_info.st_mode))

        for name in (
            PASSWORD_TEMPORARY_NAME,
            CONFIGURATION_TEMPORARY_NAME,
            PASSWORD_ROLLBACK_NAME,
            CONFIGURATION_ROLLBACK_NAME,
        ):
            if _optional_stat(selected, directory_fd, name) is not None:
                return envelope("blocked", "preflight_failed")
        password_info = _optional_stat(selected, directory_fd, DESTINATION_NAME)
        configuration_info = _optional_stat(
            selected, directory_fd, CONFIGURATION_NAME
        )
        if (
            password_info is not None
            and stat.S_ISDIR(int(password_info.st_mode))
        ) or (
            configuration_info is not None
            and stat.S_ISDIR(int(configuration_info.st_mode))
        ):
            return envelope("blocked", "preflight_failed")
        old_password = None if password_info is None else _snapshot(password_info)
        old_configuration = (
            None if configuration_info is None else _snapshot(configuration_info)
        )

        mutation_possible = True
        selected.fchmod(directory_fd, 0o700)
        selected.fsync(directory_fd)
        hardened_directory = selected.stat_fd(directory_fd)
        if (
            int(hardened_directory.st_uid) != uid
            or stat.S_IMODE(int(hardened_directory.st_mode)) != 0o700
        ):
            raise OSError("directory_not_safe")

        password_temporary_fd = selected.open_at(
            directory_fd,
            PASSWORD_TEMPORARY_NAME,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | _O_NOFOLLOW
            | _O_CLOEXEC,
            0o600,
        )
        new_password = _identity(selected.stat_fd(password_temporary_fd))
        selected.fchown(password_temporary_fd, uid, gid)
        selected.fchmod(password_temporary_fd, 0o600)
        _write_all(selected, password_temporary_fd, secret)
        selected.fsync(password_temporary_fd)
        password_temporary_info = selected.stat_fd(password_temporary_fd)
        if not _safe_private_file(
            password_temporary_info, uid, gid, len(secret)
        ):
            raise OSError("temporary_not_safe")

        configuration_temporary_fd = selected.open_at(
            directory_fd,
            CONFIGURATION_TEMPORARY_NAME,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | _O_NOFOLLOW
            | _O_CLOEXEC,
            0o600,
        )
        new_configuration = _identity(
            selected.stat_fd(configuration_temporary_fd)
        )
        selected.fchown(configuration_temporary_fd, uid, gid)
        selected.fchmod(configuration_temporary_fd, 0o600)
        _write_all(selected, configuration_temporary_fd, CONFIGURATION_BYTES)
        selected.fsync(configuration_temporary_fd)
        configuration_temporary_info = selected.stat_fd(
            configuration_temporary_fd
        )
        if not _safe_private_file(
            configuration_temporary_info, uid, gid, len(CONFIGURATION_BYTES)
        ):
            raise OSError("temporary_not_safe")

        if old_password is not None:
            selected.rename_noreplace_at(
                directory_fd, DESTINATION_NAME, PASSWORD_ROLLBACK_NAME
            )
            selected.fsync(directory_fd)
            preserved_password_info = _optional_stat(
                selected, directory_fd, PASSWORD_ROLLBACK_NAME
            )
            if (
                preserved_password_info is None
                or not _same_snapshot(preserved_password_info, old_password)
                or not _entry_matches(
                selected, directory_fd, DESTINATION_NAME, None
                )
            ):
                raise OSError("password_preservation_failed")
            preserved_password = _source_snapshot(preserved_password_info)
        if old_configuration is not None:
            selected.rename_noreplace_at(
                directory_fd, CONFIGURATION_NAME, CONFIGURATION_ROLLBACK_NAME
            )
            selected.fsync(directory_fd)
            preserved_configuration_info = _optional_stat(
                selected, directory_fd, CONFIGURATION_ROLLBACK_NAME
            )
            if (
                preserved_configuration_info is None
                or not _same_snapshot(
                    preserved_configuration_info, old_configuration
                )
                or not _entry_matches(
                    selected, directory_fd, CONFIGURATION_NAME, None
                )
            ):
                raise OSError("configuration_preservation_failed")
            preserved_configuration = _source_snapshot(
                preserved_configuration_info
            )
        selected.rename_noreplace_at(
            directory_fd, PASSWORD_TEMPORARY_NAME, DESTINATION_NAME
        )
        selected.fsync(directory_fd)
        if not _entry_identity_matches(
            selected, directory_fd, DESTINATION_NAME, new_password
        ) or not _entry_matches(
            selected, directory_fd, PASSWORD_TEMPORARY_NAME, None
        ):
            raise OSError("password_install_failed")
        selected.rename_noreplace_at(
            directory_fd, CONFIGURATION_TEMPORARY_NAME, CONFIGURATION_NAME
        )
        selected.fsync(directory_fd)
        if not _entry_identity_matches(
            selected, directory_fd, CONFIGURATION_NAME, new_configuration
        ) or not _entry_matches(
            selected, directory_fd, CONFIGURATION_TEMPORARY_NAME, None
        ):
            raise OSError("configuration_install_failed")

        if not _read_named_exact(
            selected, directory_fd, DESTINATION_NAME, uid, gid, secret
        ) or not _read_named_exact(
            selected,
            directory_fd,
            CONFIGURATION_NAME,
            uid,
            gid,
            CONFIGURATION_BYTES,
        ):
            raise OSError("final_validation_failed")
        result = selected.run_restic(RESTIC_COMMAND, RESTIC_ENVIRONMENT)
        if type(result) is not int or result != 0:
            raise OSError("readback_failed")
        final_directory = selected.stat_fd(directory_fd)
        if (
            not _safe_directory(final_directory, uid)
            or stat.S_IMODE(int(final_directory.st_mode)) != 0o700
            or not _entry_identity_matches(
                selected, directory_fd, DESTINATION_NAME, new_password
            )
            or not _entry_identity_matches(
                selected, directory_fd, CONFIGURATION_NAME, new_configuration
            )
            or not _read_named_exact(
                selected, directory_fd, DESTINATION_NAME, uid, gid, secret
            )
            or not _read_named_exact(
                selected,
                directory_fd,
                CONFIGURATION_NAME,
                uid,
                gid,
                CONFIGURATION_BYTES,
            )
            or (
                preserved_password is not None
                and not _same_source_snapshot(
                    selected.stat_at(directory_fd, PASSWORD_ROLLBACK_NAME),
                    preserved_password,
                )
            )
            or (
                preserved_password is None
                and not _entry_matches(
                    selected, directory_fd, PASSWORD_ROLLBACK_NAME, None
                )
            )
            or (
                preserved_configuration is not None
                and not _same_source_snapshot(
                    selected.stat_at(
                        directory_fd, CONFIGURATION_ROLLBACK_NAME
                    ),
                    preserved_configuration,
                )
            )
            or (
                preserved_configuration is None
                and not _entry_matches(
                    selected,
                    directory_fd,
                    CONFIGURATION_ROLLBACK_NAME,
                    None,
                )
            )
        ):
            raise OSError("preserved_entries_changed")
        return envelope(
            "succeeded",
            "none",
            effect=True,
            credential_installed=True,
            configuration_installed=True,
            readback_succeeded=True,
        )
    except Exception:
        if not mutation_possible:
            return envelope("blocked", "preflight_failed")
        if (
            directory_fd is not None
            and uid is not None
            and old_directory_mode is not None
        ):
            restored = _rollback(
                selected,
                directory_fd,
                uid,
                old_directory_mode,
                old_password,
                old_configuration,
                preserved_password,
                preserved_configuration,
                new_password,
                new_configuration,
            )
            if restored:
                return envelope(
                    "rolled_back",
                    "execution_failed",
                    effect=True,
                    rollback_attempted=True,
                    rollback_succeeded=True,
                )
        return envelope(
            "unknown",
            "mutation_ambiguous",
            effect=True,
            rollback_attempted=True,
        )
    finally:
        for descriptor in (
            configuration_temporary_fd,
            password_temporary_fd,
            directory_fd,
            source_fd,
        ):
            if descriptor is not None:
                try:
                    selected.close(descriptor)
                except Exception:
                    pass


# Compatibility aliases are intentionally narrow and keep the same strict core.
migrate = migrate_backup_credential
validate = validate_envelope


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

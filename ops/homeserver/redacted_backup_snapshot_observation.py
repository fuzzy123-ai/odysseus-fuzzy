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
import select
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence


SCHEMA_ID = "odysseus.redacted_backup_snapshot_observation.v1"
RESTIC_BINARY = "/usr/bin/restic"
BACKUP_MOUNT = "/mnt/backup"
REPOSITORY = "/mnt/backup/restic/homeserver"
SOURCE = "/opt/odysseus"
CONFIG_PATH = "/home/homebase/.config/odysseus-backup/restic-observation.env"
PASSWORD_FILE = "/home/homebase/.config/odysseus-backup/restic-password"
CONFIG_DIRECTORY = "/home/homebase/.config/odysseus-backup"
EXPECTED_OWNER = "homebase"
TIMEOUT_SECONDS = 20
MAX_OUTPUT_BYTES = 65_536
MAX_SNAPSHOTS = 4096
MAX_SNAPSHOT_AGE_SECONDS = 86_400
MAX_CONFIG_BYTES = 4_096
MAX_PASSWORD_BYTES = 16_384
MAX_FDINFO_BYTES = 4_096
MAX_MOUNTINFO_BYTES = 1_048_576
MAX_MOUNTINFO_LINE_BYTES = 4_096
MAX_MOUNTINFO_LINES = 8_192

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


@dataclass(frozen=True, slots=True)
class _OpenedIdentities:
    """Exact filesystem objects retained through the Restic readback."""

    config: int
    password: int
    repository: int
    restic: int
    source: int
    mount: int

    def all_fds(self) -> tuple[int, ...]:
        return (self.config, self.password, self.repository, self.restic, self.source, self.mount)

    def dispatch_fds(self, sealed_password: int) -> tuple[int, ...]:
        return (sealed_password, self.repository, self.restic)


def _open_path_no_symlinks(path: str, final_flags: int) -> int:
    """Open an absolute path component-by-component without following links."""
    if not isinstance(path, str) or not path.startswith("/") or path == "/":
        raise OSError("invalid path")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(nofollow, int) or not isinstance(directory, int):
        raise OSError("required flags unavailable")
    parent = os.open("/", os.O_RDONLY | directory)
    try:
        parts = tuple(part for part in path.split("/") if part)
        if any(part in {".", ".."} for part in parts):
            raise OSError("invalid path")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | directory | nofollow, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(parts[-1], final_flags | nofollow, dir_fd=parent)
    finally:
        os.close(parent)


def _open_relative_no_symlinks(parent: int, path: Sequence[str], final_flags: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(nofollow, int) or not isinstance(directory, int) or not path:
        raise OSError("required flags unavailable")
    current = os.dup(parent)
    try:
        for part in path[:-1]:
            child = os.open(part, os.O_RDONLY | directory | nofollow, dir_fd=current)
            os.close(current)
            current = child
        return os.open(path[-1], final_flags | nofollow, dir_fd=current)
    finally:
        os.close(current)


def _production_open_identities() -> _OpenedIdentities:
    opened: list[int] = []
    directory = getattr(os, "O_DIRECTORY", 0)
    try:
        try:
            config_parent = _open_path_no_symlinks(CONFIG_DIRECTORY, os.O_RDONLY | directory)
            opened.append(config_parent)
            config = _open_relative_no_symlinks(config_parent, ("restic-observation.env",), os.O_RDONLY)
            opened.append(config)
        except Exception:
            raise ObservationFailure("config_unavailable") from None
        try:
            password = _open_relative_no_symlinks(config_parent, ("restic-password",), os.O_RDONLY)
            opened.append(password)
        except Exception:
            raise ObservationFailure("password_file_unsafe") from None
        try:
            mount = _open_path_no_symlinks(BACKUP_MOUNT, os.O_RDONLY | directory)
            opened.append(mount)
            repository = _open_relative_no_symlinks(mount, ("restic", "homeserver"), os.O_RDONLY | directory)
            opened.append(repository)
        except Exception:
            raise ObservationFailure("repository_unsafe") from None
        try:
            restic = _open_path_no_symlinks(RESTIC_BINARY, os.O_RDONLY)
            opened.append(restic)
        except Exception:
            raise ObservationFailure("restic_unavailable") from None
        try:
            source = _open_path_no_symlinks(SOURCE, os.O_RDONLY | directory)
            opened.append(source)
        except Exception:
            raise ObservationFailure("source_path_missing") from None
        os.close(config_parent)
        opened.remove(config_parent)
        return _OpenedIdentities(config, password, repository, restic, source, mount)
    except Exception:
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except Exception:
                pass
        raise


def _read_proc_bounded(path: str, maximum: int) -> bytearray | None:
    descriptor: int | None = None
    output = bytearray()
    try:
        descriptor = os.open(path, os.O_RDONLY | int(getattr(os, "O_CLOEXEC", 0)))
        while len(output) <= maximum:
            chunk = os.read(descriptor, min(4096, maximum + 1 - len(output)))
            if not chunk:
                return output
            output.extend(chunk)
        return None
    except Exception:
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:
                pass


def _mount_proof_from_proc(fdinfo: Any, mountinfo: Any) -> bool:
    """Bind the retained mount FD to exactly /mnt/backup without exposing data."""
    if (
        type(fdinfo) not in {bytes, bytearray}
        or type(mountinfo) not in {bytes, bytearray}
        or len(fdinfo) > MAX_FDINFO_BYTES
        or len(mountinfo) > MAX_MOUNTINFO_BYTES
    ):
        return False
    try:
        fd_lines = fdinfo.decode("ascii", errors="strict").splitlines()
        mount_lines = mountinfo.decode("ascii", errors="strict").splitlines()
    except Exception:
        return False
    if len(fd_lines) > 128 or len(mount_lines) > MAX_MOUNTINFO_LINES:
        return False
    identifiers: list[int] = []
    for line in fd_lines:
        if len(line.encode("ascii")) > MAX_MOUNTINFO_LINE_BYTES:
            return False
        match = re.fullmatch(r"mnt_id:\s*([1-9][0-9]*)", line)
        if match:
            identifiers.append(int(match.group(1)))
    if len(identifiers) != 1:
        return False
    matches: list[str] = []
    for line in mount_lines:
        if len(line.encode("ascii")) > MAX_MOUNTINFO_LINE_BYTES:
            return False
        fields = line.split()
        if len(fields) < 10 or not fields[0].isdigit():
            return False
        if int(fields[0]) == identifiers[0]:
            matches.append(fields[4])
    return matches == [BACKUP_MOUNT]


def _production_mount_proof(descriptor: int) -> bool:
    if type(descriptor) is not int or descriptor < 0:
        return False
    fdinfo = _read_proc_bounded(f"/proc/self/fdinfo/{descriptor}", MAX_FDINFO_BYTES)
    mountinfo = _read_proc_bounded("/proc/self/mountinfo", MAX_MOUNTINFO_BYTES)
    return _mount_proof_from_proc(fdinfo, mountinfo)


def _read_config_fd(descriptor: int) -> str:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = os.read(descriptor, 4097)
        return raw.decode("utf-8", errors="strict")
    except Exception:
        raise ObservationFailure("config_unavailable") from None


def _read_credential_fd(descriptor: int, expected_size: int) -> bytearray:
    buffer: bytearray | None = None
    probe = bytearray(1)
    try:
        if type(expected_size) is not int or not 0 < expected_size <= MAX_PASSWORD_BYTES:
            raise OSError("invalid size")
        os.lseek(descriptor, 0, os.SEEK_SET)
        buffer = bytearray(expected_size)
        view = memoryview(buffer)
        try:
            offset = 0
            while offset < expected_size:
                count = os.readv(descriptor, [view[offset:]])
                if type(count) is not int or not 0 < count <= expected_size - offset:
                    raise OSError("short read")
                offset += count
        finally:
            view.release()
        probe_count = os.readv(descriptor, [memoryview(probe)])
        if type(probe_count) is not int or probe_count != 0:
            raise OSError("file grew")
        probe[0] = 0
        return buffer
    except Exception:
        if buffer is not None:
            for index in range(len(buffer)):
                buffer[index] = 0
        probe[0] = 0
        raise ObservationFailure("password_file_unsafe") from None


def _seal_credential_bytes(value: bytearray) -> int:
    """Copy bounded credential bytes into a write-sealed anonymous file."""
    if type(value) is not bytearray or not 0 < len(value) <= MAX_PASSWORD_BYTES:
        raise ObservationFailure("password_file_unsafe")
    create = getattr(os, "memfd_create", None)
    close_on_exec = getattr(os, "MFD_CLOEXEC", None)
    allow_sealing = getattr(os, "MFD_ALLOW_SEALING", None)
    if not callable(create) or not isinstance(close_on_exec, int) or not isinstance(allow_sealing, int):
        raise ObservationFailure("password_file_unsafe")
    descriptor: int | None = None
    try:
        import fcntl

        descriptor = create("odysseus-restic-credential", close_on_exec | allow_sealing)
        view = memoryview(value)
        try:
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                    raise OSError("short write")
                written += count
        finally:
            view.release()
        os.lseek(descriptor, 0, os.SEEK_SET)
        required = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, required)
        applied = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
        info = os.fstat(descriptor)
        if applied & required != required or not stat.S_ISREG(info.st_mode) or int(info.st_size) != len(value):
            raise OSError("seal validation failed")
        return descriptor
    except ObservationFailure:
        raise
    except Exception:
        raise ObservationFailure("password_file_unsafe") from None
    finally:
        if descriptor is not None and sys.exc_info()[0] is not None:
            try:
                os.close(descriptor)
            except Exception:
                pass


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


def _safe_private_file(info: Any, *, expected_uid: int) -> bool:
    try:
        mode = int(info.st_mode)
        return (
            stat.S_ISREG(mode)
            and int(info.st_uid) == expected_uid
            and int(info.st_nlink) == 1
            and stat.S_IMODE(mode) == 0o600
        )
    except Exception:
        return False


def _stable_file_identity(before: Any, after: Any) -> bool:
    """Bind one regular-file read to an unchanged inode and metadata epoch."""
    try:
        fields = ("st_dev", "st_ino", "st_mtime_ns", "st_ctime_ns", "st_size")
        return all(int(getattr(before, field)) == int(getattr(after, field)) for field in fields)
    except Exception:
        return False


def _safe_repository_info(info: Any, *, expected_uid: int) -> bool:
    try:
        mode = int(info.st_mode)
        return stat.S_ISDIR(mode) and int(info.st_uid) == expected_uid and (stat.S_IMODE(mode) & 0o022) == 0
    except Exception:
        return False


def _safe_mount_info(info: Any, *, expected_uid: int) -> bool:
    try:
        mode = int(info.st_mode)
        return stat.S_ISDIR(mode) and int(info.st_uid) in {0, expected_uid} and (stat.S_IMODE(mode) & 0o022) == 0
    except Exception:
        return False


def _safe_binary_info(info: Any) -> bool:
    try:
        mode = int(info.st_mode)
        return (
            stat.S_ISREG(mode)
            and int(info.st_uid) == 0
            and bool(stat.S_IMODE(mode) & 0o100)
            and (stat.S_IMODE(mode) & 0o022) == 0
        )
    except Exception:
        return False


def _safe_source_info(info: Any, *, expected_uid: int) -> bool:
    try:
        mode = int(info.st_mode)
        permissions = stat.S_IMODE(mode)
        return stat.S_ISDIR(mode) and int(info.st_uid) == expected_uid and (permissions & 0o500) == 0o500 and (permissions & 0o022) == 0
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


def _kill_and_reap(process: Any) -> None:
    try:
        if process.poll() is None:
            process.kill()
    except Exception:
        pass
    try:
        process.wait()
    except Exception:
        pass


def _bounded_restic_subprocess(
    command: list[str], *, env: Mapping[str, str], pass_fds: tuple[int, ...],
    timeout: int, maximum_stdout: int, popen: Callable[..., Any] = subprocess.Popen,
    selector: Callable[..., Any] = select.select, reader: Callable[[int, int], bytes] = os.read,
    monotonic: Callable[[], float] = time.monotonic,
) -> Any:
    """Stream one child stdout with a hard maximum; always reap on ambiguity."""
    process = popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=False, shell=False, env=dict(env), pass_fds=pass_fds, close_fds=True,
    )
    output = bytearray()
    pipe = getattr(process, "stdout", None)
    if pipe is None:
        _kill_and_reap(process)
        raise OSError("missing stdout")
    try:
        descriptor = pipe.fileno()
        deadline = monotonic() + timeout
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            ready, _, _ = selector([descriptor], [], [], remaining)
            if not ready:
                raise subprocess.TimeoutExpired(command, timeout)
            chunk = reader(descriptor, min(4096, maximum_stdout + 1 - len(output)))
            if not chunk:
                break
            if type(chunk) is not bytes:
                raise OSError("invalid stdout chunk")
            output.extend(chunk)
            if len(output) > maximum_stdout:
                _kill_and_reap(process)
                return SimpleNamespace(returncode=-1, stdout=bytes(output), stdout_oversized=True)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        return_code = process.wait(timeout=remaining)
        return SimpleNamespace(returncode=return_code, stdout=bytes(output), stdout_oversized=False)
    except Exception:
        _kill_and_reap(process)
        raise
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _run_restic(runner: Callable[..., Any], identities: _OpenedIdentities, sealed_password: int) -> str:
    restic_path = f"/proc/self/fd/{identities.restic}"
    repository_path = f"/proc/self/fd/{identities.repository}"
    password_path = f"/proc/self/fd/{sealed_password}"
    command = (
        restic_path, "-r", repository_path, "--no-lock", "snapshots",
        "--tag", "odysseus-pre-update", "--latest", "1", "--json",
    )
    try:
        result = runner(
            list(command),
            env={"RESTIC_PASSWORD_FILE": password_path, "PATH": "/usr/bin:/bin"},
            pass_fds=identities.dispatch_fds(sealed_password), timeout=TIMEOUT_SECONDS,
            maximum_stdout=MAX_OUTPUT_BYTES,
        )
    except subprocess.TimeoutExpired:
        raise ObservationFailure("timeout") from None
    except FileNotFoundError:
        raise ObservationFailure("restic_unavailable") from None
    except Exception:
        raise ObservationFailure("internal_error") from None
    if getattr(result, "stdout_oversized", False) is True:
        raise ObservationFailure("output_too_large")
    if getattr(result, "returncode", None) != 0:
        raise ObservationFailure("snapshot_query_failed")
    stdout = getattr(result, "stdout", None)
    if type(stdout) is bytes and len(stdout) > MAX_OUTPUT_BYTES:
        raise ObservationFailure("output_too_large")
    if type(stdout) is not bytes:
        raise ObservationFailure("malformed_output")
    try:
        return stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ObservationFailure("malformed_output") from None


def collect_backup_snapshot_observation(
    *,
    runner: Callable[..., Any] = _bounded_restic_subprocess,
    identity_opener: Callable[[], _OpenedIdentities] = _production_open_identities,
    fstat: Callable[[int], Any] = os.fstat,
    read_config: Callable[[int], str] = _read_config_fd,
    read_credential: Callable[[int, int], bytearray] = _read_credential_fd,
    seal_credential: Callable[[bytearray], int] = _seal_credential_bytes,
    close_fd: Callable[[int], None] = os.close,
    mount_prover: Callable[[int], bool] = _production_mount_proof,
    owner_lookup: Callable[[str], Any] = _production_owner_lookup,
    clock: Callable[[], float] = time.time,
    process_environment: Mapping[str, Any] = os.environ,
) -> dict[str, Any]:
    """Return the fixed redacted observation; dependencies exist for offline tests."""
    identities: _OpenedIdentities | None = None
    sealed_password: int | None = None
    credential_buffer: bytearray | None = None
    try:
        _validate_process_environment(process_environment)
        expected_uid = _expected_uid(owner_lookup)
        identities = identity_opener()
        if (
            type(identities) is not _OpenedIdentities
            or any(type(descriptor) is not int or descriptor < 0 for descriptor in identities.all_fds())
            or len(set(identities.all_fds())) != 6
        ):
            raise ObservationFailure("internal_error")
        if not _safe_binary_info(fstat(identities.restic)):
            raise ObservationFailure("restic_unavailable")
        if not _safe_source_info(fstat(identities.source), expected_uid=expected_uid):
            raise ObservationFailure("source_path_missing")
        if not _safe_mount_info(fstat(identities.mount), expected_uid=expected_uid) or mount_prover(identities.mount) is not True:
            raise ObservationFailure("mount_unavailable")
        if not _safe_repository_info(fstat(identities.repository), expected_uid=expected_uid):
            raise ObservationFailure("repository_unsafe")
        config_before = fstat(identities.config)
        if not _safe_private_file(config_before, expected_uid=expected_uid) or not 0 < int(config_before.st_size) <= MAX_CONFIG_BYTES:
            raise ObservationFailure("config_invalid")
        config_value = read_config(identities.config)
        config_after = fstat(identities.config)
        if not _stable_file_identity(config_before, config_after):
            raise ObservationFailure("config_invalid")
        _parse_config(config_value)
        password_before = fstat(identities.password)
        if not _safe_private_file(password_before, expected_uid=expected_uid) or not 0 < int(password_before.st_size) <= MAX_PASSWORD_BYTES:
            raise ObservationFailure("password_file_unsafe")
        credential_buffer = read_credential(identities.password, int(password_before.st_size))
        if type(credential_buffer) is not bytearray:
            raise ObservationFailure("password_file_unsafe")
        password_after = fstat(identities.password)
        if (
            not _stable_file_identity(password_before, password_after)
            or len(credential_buffer) != int(password_before.st_size)
            or not credential_buffer
        ):
            raise ObservationFailure("password_file_unsafe")
        candidate_sealed = seal_credential(credential_buffer)
        if type(candidate_sealed) is not int or candidate_sealed < 0 or candidate_sealed in identities.all_fds():
            raise ObservationFailure("password_file_unsafe")
        sealed_password = candidate_sealed
        for index in range(len(credential_buffer)):
            credential_buffer[index] = 0
        snapshot_id, snapshot_age_seconds = _latest_pre_update_snapshot(
            _run_restic(runner, identities, sealed_password), now=clock()
        )
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
    finally:
        if credential_buffer is not None:
            for index in range(len(credential_buffer)):
                credential_buffer[index] = 0
        if sealed_password is not None:
            try:
                close_fd(sealed_password)
            except Exception:
                pass
        if identities is not None:
            for descriptor in reversed(identities.all_fds()):
                try:
                    close_fd(descriptor)
                except Exception:
                    pass


def main() -> int:
    try:
        payload = collect_backup_snapshot_observation()
    except Exception:
        payload = blocked("internal_error")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

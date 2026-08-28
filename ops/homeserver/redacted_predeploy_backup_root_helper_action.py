#!/usr/bin/env python3
"""One-shot, fail-closed arm/start/readback action for the root backup helper.

The installed helper deliberately cannot run without a fresh root-owned arm
record.  This module creates exactly one no-clobber arm, invokes exactly one
fixed systemd unit, validates the installed redacted readback, and removes
only the arm inode created by this attempt after the unit is proven inactive.
It accepts no command, path, environment, or service name from the caller.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_action.v1"
PACKET_SCHEMA_ID = "odysseus.predeploy_backup_root_helper_action_packet.v1"
ARM_SCHEMA_ID = "odysseus.predeploy_backup_root_helper_arm.v1"
STATE_DIR = "/var/lib/odysseus-predeploy-backup-root-helper"
ARM_NAME = "arm.json"
UNIT = "odysseus-predeploy-backup-root-helper.service"
HELPER_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper.py"
READBACK_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py"
UNIT_PATH = "/etc/systemd/system/odysseus-predeploy-backup-root-helper.service"
SUDOERS_PATH = "/etc/sudoers.d/odysseus-predeploy-backup-root-helper"
HELPER_SHA256 = "9c9e6632be23a04d6c8d284b868b227b5576f35bb480208c1b4f7f0635f21032"
READBACK_SHA256 = "e647b6f1faa409f42cbeb80c74826b730695d9e0fad14cf47aa22c1a59a0a046"
UNIT_SHA256 = "466de2f889a00ee2759bd06380ddb213f8c0f4cee5644e3a2e27083863c1ab98"
SUDOERS_SHA256 = "1a6a7f1ec4d328c9fed20b758a87bb9905b68145122f6751fd5eeea748d5847d"
MAX_ASSET_BYTES = 400_000
MAX_ARM_BYTES = 1_024
MAX_FUTURE_SECONDS = 600
START_TIMEOUT_SECONDS = 1_880
READBACK_TIMEOUT_SECONDS = 10
MAX_READBACK_BYTES = 8_192
ZERO_SHA256 = "0" * 64
MINIMAL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
START_COMMAND = ("/usr/bin/systemctl", "start", "--wait", UNIT)
INACTIVE_COMMAND = ("/usr/bin/systemctl", "is-active", UNIT)
DISABLED_COMMAND = ("/usr/bin/systemctl", "is-enabled", UNIT)
READBACK_COMMAND = ("/usr/bin/python3", "-I", READBACK_PATH)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ACTION_REF = re.compile(r"^predeploy_backup_root_helper_v1:[0-9a-f]{64}$")
_PACKET_KEYS = frozenset({"schema_id", "grant_id", "expires_at_epoch", "helper_sha256"})
_VISIBILITY = frozenset(
    {
        "raw_stdout_visible",
        "raw_stderr_visible",
        "exception_text_visible",
        "environment_visible",
        "paths_visible",
        "hostnames_visible",
        "secret_values_visible",
    }
)
_KEYS = frozenset(
    {
        "schema_id",
        "status",
        "error_code",
        "arm_created",
        "unit_invoked",
        "backup_succeeded",
        "unit_inactive",
        "arm_cleanup_succeeded",
        "result_status",
        "result_evidence_sha256",
        "action_provenance_ref",
        "retry_permitted",
        "manual_recovery_required",
        *_VISIBILITY,
        "evidence_sha256",
    }
)
_ERRORS = frozenset(
    {
        "none",
        "execution_disabled",
        "invalid_packet",
        "preflight_failed",
        "already_armed",
        "arm_publish_failed",
        "helper_blocked",
        "helper_unknown",
        "start_failed",
        "readback_failed",
        "cleanup_failed",
        "transport_ambiguous",
        "published_blob_mismatch",
    }
)
_ASSETS = (
    (HELPER_PATH, HELPER_SHA256, 0o700),
    (READBACK_PATH, READBACK_SHA256, 0o700),
    (UNIT_PATH, UNIT_SHA256, 0o644),
    (SUDOERS_PATH, SUDOERS_SHA256, 0o440),
)


def _digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "evidence_sha256"}
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def envelope(
    status: str,
    error_code: str,
    *,
    arm_created: bool = False,
    unit_invoked: bool = False,
    backup_succeeded: bool = False,
    unit_inactive: bool = False,
    arm_cleanup_succeeded: bool = False,
    result_status: str = "none",
    result_evidence_sha256: str = ZERO_SHA256,
    action_provenance_ref: str = "none",
    manual_recovery_required: bool = False,
) -> dict[str, Any]:
    value = {
        "schema_id": SCHEMA_ID,
        "status": status if status in {"blocked", "ok", "failed", "unknown"} else "unknown",
        "error_code": error_code if error_code in _ERRORS else "transport_ambiguous",
        "arm_created": arm_created is True,
        "unit_invoked": unit_invoked is True,
        "backup_succeeded": backup_succeeded is True,
        "unit_inactive": unit_inactive is True,
        "arm_cleanup_succeeded": arm_cleanup_succeeded is True,
        "result_status": result_status if result_status in {"none", "ok", "blocked", "unknown"} else "none",
        "result_evidence_sha256": result_evidence_sha256,
        "action_provenance_ref": action_provenance_ref,
        "retry_permitted": False,
        "manual_recovery_required": manual_recovery_required is True,
        **{key: False for key in _VISIBILITY},
    }
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    if (
        type(value) is not dict
        or set(value) != _KEYS
        or value.get("schema_id") != SCHEMA_ID
        or value.get("status") not in {"blocked", "ok", "failed", "unknown"}
        or value.get("error_code") not in _ERRORS
        or value.get("result_status") not in {"none", "ok", "blocked", "unknown"}
        or value.get("retry_permitted") is not False
        or any(value.get(key) is not False for key in _VISIBILITY)
        or any(
            type(value.get(key)) is not bool
            for key in (
                "arm_created",
                "unit_invoked",
                "backup_succeeded",
                "unit_inactive",
                "arm_cleanup_succeeded",
                "manual_recovery_required",
            )
        )
        or not isinstance(value.get("result_evidence_sha256"), str)
        or not _HEX64.fullmatch(value["result_evidence_sha256"])
        or not isinstance(value.get("action_provenance_ref"), str)
        or not isinstance(value.get("evidence_sha256"), str)
        or value["evidence_sha256"] != _digest(value)
    ):
        return False
    status = value["status"]
    if status == "blocked":
        return bool(
            value["error_code"]
            in {
                "execution_disabled",
                "invalid_packet",
                "preflight_failed",
                "already_armed",
                "arm_publish_failed",
                "published_blob_mismatch",
            }
            and all(
                value[key] is False
                for key in (
                    "arm_created",
                    "unit_invoked",
                    "backup_succeeded",
                    "unit_inactive",
                    "arm_cleanup_succeeded",
                    "manual_recovery_required",
                )
            )
            and value["result_status"] == "none"
            and value["result_evidence_sha256"] == ZERO_SHA256
            and value["action_provenance_ref"] == "none"
        )
    reference_valid = bool(_ACTION_REF.fullmatch(value["action_provenance_ref"]))
    digest_valid = value["result_evidence_sha256"] != ZERO_SHA256
    if status == "ok":
        return bool(
            value["error_code"] == "none"
            and all(
                value[key] is True
                for key in (
                    "arm_created",
                    "unit_invoked",
                    "backup_succeeded",
                    "unit_inactive",
                    "arm_cleanup_succeeded",
                )
            )
            and value["result_status"] == "ok"
            and digest_valid
            and reference_valid
            and value["manual_recovery_required"] is False
        )
    if status == "failed":
        return bool(
            value["error_code"] == "helper_blocked"
            and value["arm_created"] is True
            and value["unit_invoked"] is True
            and value["backup_succeeded"] is False
            and value["unit_inactive"] is True
            and value["arm_cleanup_succeeded"] is True
            and value["result_status"] == "blocked"
            and digest_valid
            and value["action_provenance_ref"] == "none"
            and value["manual_recovery_required"] is False
        )
    return bool(
        value["error_code"]
        in {
            "arm_publish_failed",
            "helper_unknown",
            "start_failed",
            "readback_failed",
            "cleanup_failed",
            "transport_ambiguous",
        }
        and value["manual_recovery_required"] is True
        and value["backup_succeeded"] is False
        and (
            value["action_provenance_ref"] == "none" or reference_valid
        )
    )


def _packet_valid(packet: Any, now: int) -> bool:
    return bool(
        type(packet) is dict
        and set(packet) == _PACKET_KEYS
        and packet.get("schema_id") == PACKET_SCHEMA_ID
        and isinstance(packet.get("grant_id"), str)
        and _HEX64.fullmatch(packet["grant_id"])
        and type(packet.get("expires_at_epoch")) is int
        and now < packet["expires_at_epoch"] <= now + MAX_FUTURE_SECONDS
        and packet.get("helper_sha256") == HELPER_SHA256
    )


def _open_parent(path: str, *, api: Any = os) -> tuple[int, str]:
    parts = path.split("/")[1:]
    if not parts or any(not item or item in {".", ".."} for item in parts):
        raise OSError("invalid path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current = api.open("/", flags)
    try:
        for part in parts[:-1]:
            following = api.open(part, flags, dir_fd=current)
            api.close(current)
            current = following
            info = api.fstat(current)
            if not (
                stat.S_ISDIR(info.st_mode)
                and info.st_uid == 0
                and info.st_gid == 0
                and stat.S_IMODE(info.st_mode) in {0o700, 0o755}
            ):
                raise OSError("unsafe parent")
        return current, parts[-1]
    except Exception:
        try:
            api.close(current)
        except Exception:
            pass
        raise


def _asset_valid(path: str, expected: str, mode: int, *, api: Any = os) -> bool:
    parent = descriptor = None
    try:
        parent, name = _open_parent(path, api=api)
        descriptor = api.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        before = api.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if not (
            stat.S_ISREG(before.st_mode)
            and before.st_uid == 0
            and before.st_gid == 0
            and stat.S_IMODE(before.st_mode) == mode
            and before.st_nlink == 1
            and 0 < before.st_size <= MAX_ASSET_BYTES
        ):
            return False
        remaining = before.st_size
        hasher = hashlib.sha256()
        while remaining:
            chunk = api.read(descriptor, min(8_192, remaining))
            if not chunk or len(chunk) > remaining:
                return False
            hasher.update(chunk)
            remaining -= len(chunk)
        after = api.fstat(descriptor)
        return bool(
            api.read(descriptor, 1) == b""
            and (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            == identity
            and after.st_nlink == 1
            and hasher.hexdigest() == expected
        )
    except Exception:
        return False
    finally:
        for item in (descriptor, parent):
            if isinstance(item, int):
                try:
                    api.close(item)
                except Exception:
                    pass


def _state_dir_safe(info: Any) -> bool:
    try:
        return bool(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == 0
            and info.st_gid == 0
            and stat.S_IMODE(info.st_mode) == 0o700
            and info.st_nlink >= 1
        )
    except Exception:
        return False


def _systemctl_projection(command: tuple[str, ...], *, timeout: int = 5) -> tuple[int | None, bytes]:
    try:
        completed = subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=MINIMAL_ENV,
            close_fds=True,
            timeout=timeout,
            check=False,
        )
        output = completed.stdout
        if type(output) is not bytes or len(output) > 32:
            return None, b""
        return completed.returncode, output
    except Exception:
        return None, b""


def _preflight(*, api: Any = os) -> str | None:
    if getattr(api, "geteuid", lambda: -1)() != 0:
        return "preflight_failed"
    if not all(_asset_valid(path, digest, mode, api=api) for path, digest, mode in _ASSETS):
        return "preflight_failed"
    directory = None
    try:
        directory = api.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = api.fstat(directory)
        if not _state_dir_safe(info):
            return "preflight_failed"
        try:
            api.stat(ARM_NAME, dir_fd=directory, follow_symlinks=False)
            return "already_armed"
        except FileNotFoundError:
            pass
        except Exception:
            return "preflight_failed"
    except Exception:
        return "preflight_failed"
    finally:
        if isinstance(directory, int):
            try:
                api.close(directory)
            except Exception:
                pass
    active_code, active = _systemctl_projection(INACTIVE_COMMAND)
    enabled_code, enabled = _systemctl_projection(DISABLED_COMMAND)
    if active_code not in {0, 1, 3} or active != b"inactive\n":
        return "preflight_failed"
    if enabled_code not in {0, 1} or enabled not in {b"disabled\n", b"static\n"}:
        return "preflight_failed"
    return None


@dataclass(frozen=True)
class ArmToken:
    device: int
    inode: int


class ArmPublicationUncertain(Exception):
    pass


def _arm_bytes(packet: Mapping[str, Any]) -> bytes:
    value = {
        "schema_id": ARM_SCHEMA_ID,
        "grant_id": packet["grant_id"],
        "expires_at_epoch": packet["expires_at_epoch"],
        "helper_sha256": HELPER_SHA256,
    }
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    if not 0 < len(raw) <= MAX_ARM_BYTES:
        raise OSError("arm size")
    return raw


def _create_arm(packet: Mapping[str, Any], *, api: Any = os) -> ArmToken:
    directory = descriptor = None
    temporary = ".arm-" + packet["grant_id"] + ".tmp"
    temporary_created = False
    published = False
    try:
        directory = api.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        if not _state_dir_safe(api.fstat(directory)):
            raise OSError("unsafe state directory")
        raw = _arm_bytes(packet)
        descriptor = api.open(
            temporary,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        temporary_created = True
        api.fchown(descriptor, 0, 0)
        api.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            written = api.write(descriptor, raw[offset:])
            if not isinstance(written, int) or written <= 0:
                raise OSError("short write")
            offset += written
        api.fsync(descriptor)
        info = api.fstat(descriptor)
        if not (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and info.st_gid == 0
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1
            and info.st_size == len(raw)
        ):
            raise OSError("unsafe arm")
        api.lseek(descriptor, 0, os.SEEK_SET)
        observed = bytearray()
        while len(observed) < len(raw):
            chunk = api.read(descriptor, len(raw) - len(observed))
            if not chunk:
                raise OSError("short read")
            observed.extend(chunk)
        if bytes(observed) != raw or api.read(descriptor, 1) != b"":
            raise OSError("arm mismatch")
        api.link(temporary, ARM_NAME, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        published = True
        final = api.stat(ARM_NAME, dir_fd=directory, follow_symlinks=False)
        if (final.st_dev, final.st_ino) != (info.st_dev, info.st_ino):
            raise ArmPublicationUncertain()
        api.unlink(temporary, dir_fd=directory)
        temporary_created = False
        api.fsync(directory)
        return ArmToken(int(info.st_dev), int(info.st_ino))
    except FileExistsError:
        raise
    except ArmPublicationUncertain:
        raise
    except Exception:
        if published:
            raise ArmPublicationUncertain() from None
        raise
    finally:
        if isinstance(descriptor, int):
            try:
                api.close(descriptor)
            except Exception:
                pass
        if temporary_created and isinstance(directory, int):
            try:
                api.unlink(temporary, dir_fd=directory)
                api.fsync(directory)
            except Exception:
                pass
        if isinstance(directory, int):
            try:
                api.close(directory)
            except Exception:
                pass


def _cleanup_arm(token: ArmToken, *, api: Any = os) -> bool:
    directory = None
    try:
        directory = api.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        if not _state_dir_safe(api.fstat(directory)):
            return False
        info = api.stat(ARM_NAME, dir_fd=directory, follow_symlinks=False)
        if not (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and info.st_gid == 0
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1
            and (int(info.st_dev), int(info.st_ino)) == (token.device, token.inode)
        ):
            return False
        api.unlink(ARM_NAME, dir_fd=directory)
        api.fsync(directory)
        try:
            api.stat(ARM_NAME, dir_fd=directory, follow_symlinks=False)
            return False
        except FileNotFoundError:
            return True
    except Exception:
        return False
    finally:
        if isinstance(directory, int):
            try:
                api.close(directory)
            except Exception:
                pass


def _start_unit() -> int | None:
    try:
        result = subprocess.run(
            list(START_COMMAND),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=MINIMAL_ENV,
            close_fds=True,
            timeout=START_TIMEOUT_SECONDS,
            check=False,
        )
        return result.returncode if type(result.returncode) is int else None
    except Exception:
        return None


def _unit_inactive() -> bool:
    code, output = _systemctl_projection(INACTIVE_COMMAND)
    return code in {0, 1, 3} and output == b"inactive\n"


def _read_bounded_process(command: tuple[str, ...], timeout: int, maximum: int) -> bytes | None:
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=MINIMAL_ENV,
            close_fds=True,
            shell=False,
        )
    except Exception:
        return None
    output = bytearray()
    oversized = [False]

    def reader() -> None:
        try:
            while True:
                piece = process.stdout.read(min(4_096, maximum + 1 - len(output)))
                if not piece:
                    return
                output.extend(piece)
                if len(output) > maximum:
                    oversized[0] = True
                    process.kill()
                    return
        except Exception:
            oversized[0] = True
            try:
                process.kill()
            except Exception:
                pass

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        process.wait(timeout=timeout)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=1)
        except Exception:
            pass
        return None
    finally:
        thread.join(timeout=1)
        try:
            if process.stdout is not None:
                process.stdout.close()
        except Exception:
            pass
    if thread.is_alive() or oversized[0] or process.returncode != 0:
        return None
    return bytes(output)


def _readback_valid(value: Any) -> bool:
    keys = frozenset(
        {
            "schema_id",
            "status",
            "receipt_available",
            "result_status",
            "result_evidence_sha256",
            "action_provenance_ref",
            "raw_output_visible",
            "environment_visible",
            "paths_visible",
            "secret_values_visible",
            "evidence_sha256",
        }
    )
    if (
        type(value) is not dict
        or set(value) != keys
        or value.get("schema_id") != "odysseus.predeploy_backup_root_helper_readback.v1"
        or value.get("status") not in {"available", "unavailable"}
        or any(
            value.get(key) is not False
            for key in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")
        )
        or value.get("evidence_sha256") != _digest(value)
    ):
        return False
    if value["status"] == "unavailable":
        return bool(
            value.get("receipt_available") is False
            and value.get("result_status") == "none"
            and value.get("result_evidence_sha256") == ZERO_SHA256
            and value.get("action_provenance_ref") == "none"
        )
    return bool(
        value.get("receipt_available") is True
        and value.get("result_status") in {"ok", "blocked", "unknown"}
        and isinstance(value.get("result_evidence_sha256"), str)
        and _HEX64.fullmatch(value["result_evidence_sha256"])
        and (
            (value["result_status"] == "blocked" and value.get("action_provenance_ref") == "none")
            or (
                value["result_status"] in {"ok", "unknown"}
                and isinstance(value.get("action_provenance_ref"), str)
                and _ACTION_REF.fullmatch(value["action_provenance_ref"])
            )
        )
    )


def _readback() -> dict[str, Any] | None:
    raw = _read_bounded_process(READBACK_COMMAND, READBACK_TIMEOUT_SECONDS, MAX_READBACK_BYTES)
    if (
        type(raw) is not bytes
        or raw.count(b"\n") != 1
        or not raw.endswith(b"\n")
        or b"\r" in raw
    ):
        return None
    try:
        value = json.loads(raw[:-1].decode("ascii"))
    except Exception:
        return None
    return value if _readback_valid(value) else None


def perform(
    packet: Any = None,
    *,
    execute: bool = False,
    now: Callable[[], float] = time.time,
    preflight: Callable[[], str | None] = _preflight,
    create_arm: Callable[[Mapping[str, Any]], ArmToken] = _create_arm,
    start_unit: Callable[[], int | None] = _start_unit,
    readback: Callable[[], dict[str, Any] | None] = _readback,
    unit_inactive: Callable[[], bool] = _unit_inactive,
    cleanup_arm: Callable[[ArmToken], bool] = _cleanup_arm,
) -> dict[str, Any]:
    if execute is not True:
        return envelope("blocked", "execution_disabled")
    current = int(now())
    if not _packet_valid(packet, current):
        return envelope("blocked", "invalid_packet")
    preflight_error = preflight()
    if preflight_error is not None:
        return envelope("blocked", preflight_error)
    try:
        token = create_arm(packet)
    except FileExistsError:
        return envelope("blocked", "already_armed")
    except ArmPublicationUncertain:
        return envelope(
            "unknown",
            "arm_publish_failed",
            arm_created=True,
            manual_recovery_required=True,
        )
    except Exception:
        return envelope("blocked", "arm_publish_failed")

    start_code = start_unit()
    observed = readback()
    inactive = unit_inactive()
    result_status = observed.get("result_status") if isinstance(observed, dict) else "none"
    result_digest = (
        observed.get("result_evidence_sha256")
        if isinstance(observed, dict)
        and isinstance(observed.get("result_evidence_sha256"), str)
        else ZERO_SHA256
    )
    reference = (
        observed.get("action_provenance_ref")
        if isinstance(observed, dict)
        and isinstance(observed.get("action_provenance_ref"), str)
        else "none"
    )
    cleaned = cleanup_arm(token) if inactive else False
    common = {
        "arm_created": True,
        "unit_invoked": True,
        "unit_inactive": inactive,
        "arm_cleanup_succeeded": cleaned,
        "result_status": result_status,
        "result_evidence_sha256": result_digest,
        "action_provenance_ref": reference,
    }
    if start_code == 0 and observed is not None and result_status == "ok" and inactive and cleaned:
        return envelope("ok", "none", backup_succeeded=True, **common)
    if observed is not None and result_status == "blocked" and inactive and cleaned:
        return envelope("failed", "helper_blocked", **common)
    if not inactive or not cleaned:
        code = "cleanup_failed" if inactive else "start_failed"
    elif observed is None:
        code = "readback_failed"
    elif result_status == "unknown":
        code = "helper_unknown"
    else:
        code = "start_failed"
    return envelope("unknown", code, manual_recovery_required=True, **common)


def main() -> int:
    print(json.dumps(envelope("blocked", "execution_disabled"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

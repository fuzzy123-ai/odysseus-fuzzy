#!/usr/bin/env python3
"""Persist a validated, redacted namespace-capability result before reporting it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable, Mapping

from ops.homeserver import redacted_predeploy_backup_capability as capability
from ops.homeserver import redacted_predeploy_backup_capability_transport as transport


SCHEMA_ID = "odysseus.redacted_predeploy_backup_capability_receipt.v1"
MAX_RECEIPT_BYTES = 4096
RECEIPT_ROOT = Path(__file__).resolve().parents[2] / ".odysseus-predeploy-capability-receipts"
_ZERO_DIGEST = "0" * 64
_SUMMARY_KEYS = frozenset({
    "schema_id", "status", "error_code", "effect_may_have_occurred",
    "source_envelope_kind", "source_status", "source_error_code",
    "source_evidence_sha256", "receipt_sha256",
    "retry_permitted", "summary_sha256",
})
_SUMMARY_CODES = frozenset({
    "none", "invalid_invocation", "receipt_storage_unavailable",
    "collector_ambiguous", "collector_envelope_invalid", "receipt_persistence_failed",
    "receipt_readback_unavailable",
})
_RECEIPT_KEYS = frozenset({"schema_id", "source_envelope_kind", "source_envelope", "receipt_sha256"})
_RECEIPT_NAME = re.compile(r"^receipt-([0-9a-f]{64})\.json$")


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def _digest(value: Mapping[str, Any], *, omitted: str) -> str:
    return hashlib.sha256(_canonical_json({key: item for key, item in value.items() if key != omitted})).hexdigest()


def _summary(
    status: str, code: str, *, effect: bool, kind: str = "none", source_status: str = "none",
    source_error_code: str = "none", source_digest: str = _ZERO_DIGEST,
    receipt_digest: str = _ZERO_DIGEST,
) -> dict[str, Any]:
    payload = {
        "schema_id": SCHEMA_ID,
        "status": status,
        "error_code": code,
        "effect_may_have_occurred": effect,
        "source_envelope_kind": kind,
        "source_status": source_status,
        "source_error_code": source_error_code,
        "source_evidence_sha256": source_digest,
        "receipt_sha256": receipt_digest,
        "retry_permitted": False,
    }
    payload["summary_sha256"] = _digest(payload, omitted="summary_sha256")
    return payload


def validate_receipt_summary(value: Any) -> bool:
    if not (
        type(value) is dict and set(value) == _SUMMARY_KEYS
        and value.get("schema_id") == SCHEMA_ID
        and type(value.get("effect_may_have_occurred")) is bool
        and value.get("retry_permitted") is False
        and all(type(value.get(key)) is str and len(value[key]) == 64 and all(char in "0123456789abcdef" for char in value[key])
                for key in {"source_evidence_sha256", "receipt_sha256", "summary_sha256"})
        and value.get("summary_sha256") == _digest(value, omitted="summary_sha256")
    ):
        return False
    status, code, effect = value["status"], value["error_code"], value["effect_may_have_occurred"]
    kind, source_status, source_error, source_digest, receipt_digest = (
        value["source_envelope_kind"], value["source_status"], value["source_error_code"],
        value["source_evidence_sha256"], value["receipt_sha256"],
    )
    local = kind == "none" and source_status == "none" and source_error == "none" and source_digest == _ZERO_DIGEST
    if status == "blocked" and code in {"invalid_invocation", "receipt_storage_unavailable"}:
        return effect is False and local and receipt_digest == _ZERO_DIGEST
    if status == "unknown" and code in {"collector_ambiguous", "collector_envelope_invalid"}:
        return effect is True and local and receipt_digest == _ZERO_DIGEST
    if status == "unknown" and code == "receipt_readback_unavailable":
        return effect is True and local and receipt_digest == _ZERO_DIGEST
    if status == "unknown" and code == "receipt_persistence_failed":
        return _valid_source_outcome(kind, source_status, source_error, effect, source_digest) and receipt_digest == _ZERO_DIGEST
    if status == "persisted" and code == "none":
        return _valid_source_outcome(kind, source_status, source_error, effect, source_digest) and receipt_digest != _ZERO_DIGEST
    return False


def _valid_source_outcome(kind: str, status: str, error: str, effect: bool, digest: str) -> bool:
    if digest == _ZERO_DIGEST:
        return False
    if kind == "core":
        return (
            (status == "supported" and error == "none" and effect is True)
            or (status == "unsupported" and error in {"capability_unavailable", "timeout", "internal_error"} and effect is True)
            or (status == "blocked" and error in {"invalid_invocation", "preflight_failed", "internal_error"} and effect is False)
        )
    if kind == "transport":
        return (
            (status == "blocked" and error in {"invalid_invocation", "published_blob_mismatch"} and effect is False)
            or (status == "unknown" and error == "transport_ambiguous" and effect is True)
        )
    return False


def _source(value: Any) -> tuple[str, str, str, bool, str] | None:
    core_valid = capability.validate_envelope(value)
    transport_valid = transport.validate_transport_envelope(value)
    if int(core_valid) + int(transport_valid) != 1:
        return None
    if core_valid:
        source = "core", value["status"], value["error_code"], bool(value["probe_invoked"]), value["evidence_sha256"]
    else:
        source = "transport", value["status"], value["error_code"], bool(value["effect_may_have_occurred"]), value["evidence_sha256"]
    return source if _valid_source_outcome(*source) else None


def _fixed_non_symlink_root() -> str | None:
    try:
        raw = os.fspath(RECEIPT_ROOT)
        if type(raw) is not str or not os.path.isabs(raw):
            return None
        raw_path = Path(raw)
        if any(part in {".", ".."} for part in raw_path.parts):
            return None
        candidate = os.path.abspath(raw)
        probe = Path(candidate)
        current = Path(probe.anchor)
        for part in probe.parts[1:]:
            current = current / part
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                return None
        info = os.lstat(candidate)
        return candidate if stat.S_ISDIR(info.st_mode) else None
    except Exception:
        return None


def _descriptor_relative_supported() -> bool:
    return bool(all(hasattr(os, attribute) for attribute in ("O_DIRECTORY", "O_NOFOLLOW")) and os.open in os.supports_dir_fd)


def _close(descriptor: int | None) -> bool:
    if descriptor is None:
        return True
    try:
        os.close(descriptor)
        return True
    except Exception:
        return False


def _persist_receipt(receipt: Mapping[str, Any], receipt_dir: str) -> str | None:
    """Create exactly one local receipt.  Any storage uncertainty is failure."""
    directory_descriptor: int | None = None
    descriptor: int | None = None
    failed = False
    try:
        serialized = _canonical_json(receipt)
        if not 0 < len(serialized) <= MAX_RECEIPT_BYTES:
            return None
        filename = "receipt-" + receipt["receipt_sha256"] + ".json"
        if "/" in filename or "\\" in filename:
            return None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if _descriptor_relative_supported():
            directory_descriptor = os.open(receipt_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
                failed = True
                raise OSError
            descriptor = os.open(filename, flags, 0o600, dir_fd=directory_descriptor)
        else:
            # Windows lacks descriptor-relative opens.  The fixed root and lstat
            # preflight limit traversal, but cannot close a parent-replace race.
            descriptor = os.open(os.path.join(receipt_dir, filename), flags, 0o600)
    except Exception:
        failed = True
    if descriptor is not None and not failed:
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1:
                failed = True
            else:
                written = 0
                while written < len(serialized):
                    count = os.write(descriptor, serialized[written:])
                    if type(count) is not int or count <= 0:
                        failed = True
                        break
                    written += count
                if not failed:
                    os.fsync(descriptor)
                    if directory_descriptor is not None:
                        os.fsync(directory_descriptor)
        except Exception:
            failed = True
    if not _close(descriptor):
        failed = True
    if not _close(directory_descriptor):
        failed = True
    return None if failed else receipt["receipt_sha256"]


def _receipt_source(value: Any) -> tuple[str, str, str, bool, str, str] | None:
    if type(value) is not dict or set(value) != _RECEIPT_KEYS or value.get("schema_id") != SCHEMA_ID:
        return None
    receipt_digest = value.get("receipt_sha256")
    if type(receipt_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", receipt_digest):
        return None
    if receipt_digest != _digest(value, omitted="receipt_sha256"):
        return None
    source = _source(value.get("source_envelope"))
    if source is None or value.get("source_envelope_kind") != source[0]:
        return None
    return *source, receipt_digest


def _read_only_receipt(receipt_dir: str) -> dict[str, Any] | None:
    directory_descriptor: int | None = None
    descriptor: int | None = None
    try:
        if _descriptor_relative_supported():
            directory_descriptor = os.open(receipt_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            names = os.listdir(directory_descriptor)
        else:
            # Windows lacks descriptor-relative opens.  The fixed root and lstat
            # preflight limit traversal, but cannot close a parent-replace race.
            names = os.listdir(receipt_dir)
        if len(names) != 1:
            return None
        name = names[0]
        matched_name = _RECEIPT_NAME.fullmatch(name)
        if matched_name is None:
            return None
        flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        if directory_descriptor is not None:
            descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        else:
            full_name = os.path.join(receipt_dir, name)
            if stat.S_ISLNK(os.lstat(full_name).st_mode):
                return None
            descriptor = os.open(full_name, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1 or info.st_size > MAX_RECEIPT_BYTES:
            return None
        output = bytearray()
        while len(output) <= MAX_RECEIPT_BYTES:
            chunk = os.read(descriptor, min(4096, MAX_RECEIPT_BYTES + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
        if not output or len(output) > MAX_RECEIPT_BYTES:
            return None
        value = json.loads(bytes(output).decode("ascii"))
        source = _receipt_source(value)
        return value if source is not None and value["receipt_sha256"] == matched_name.group(1) else None
    except Exception:
        return None
    finally:
        _close(descriptor)
        _close(directory_descriptor)


def collect_predeploy_backup_capability_receipt(
    *, execute: bool = False,
    collector: Callable[..., Any] = transport.collect_published_predeploy_backup_capability,
) -> dict[str, Any]:
    """Collect only on explicit execution and return a redacted persistence summary."""
    if execute is not True:
        return _summary("blocked", "invalid_invocation", effect=False)
    directory = _fixed_non_symlink_root()
    if directory is None:
        return _summary("blocked", "receipt_storage_unavailable", effect=False)
    try:
        value = collector(execute=True)
    except Exception:
        return _summary("unknown", "collector_ambiguous", effect=True)
    source = _source(value)
    if source is None:
        return _summary("unknown", "collector_envelope_invalid", effect=True)
    kind, source_status, source_error_code, effect, source_digest = source
    receipt: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "source_envelope_kind": kind,
        "source_envelope": value,
    }
    receipt["receipt_sha256"] = _digest(receipt, omitted="receipt_sha256")
    receipt_digest = _persist_receipt(receipt, directory)
    if receipt_digest is None:
        return _summary("unknown", "receipt_persistence_failed", effect=effect, kind=kind, source_status=source_status,
                        source_error_code=source_error_code, source_digest=source_digest)
    return _summary("persisted", "none", effect=effect, kind=kind, source_status=source_status,
                    source_error_code=source_error_code, source_digest=source_digest, receipt_digest=receipt_digest)


def read_predeploy_backup_capability_receipt() -> dict[str, Any]:
    """Recover one validated local receipt without invoking any collector or transport."""
    directory = _fixed_non_symlink_root()
    if directory is None:
        return _summary("unknown", "receipt_readback_unavailable", effect=True)
    value = _read_only_receipt(directory)
    source = _receipt_source(value) if value is not None else None
    if source is None:
        return _summary("unknown", "receipt_readback_unavailable", effect=True)
    kind, source_status, source_error_code, effect, source_digest, receipt_digest = source
    return _summary("persisted", "none", effect=effect, kind=kind, source_status=source_status,
                    source_error_code=source_error_code, source_digest=source_digest, receipt_digest=receipt_digest)

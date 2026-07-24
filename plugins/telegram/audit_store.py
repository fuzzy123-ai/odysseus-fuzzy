"""Bounded, content-free Telegram audit receipt persistence.

This store is deliberately separate from the mixed Telegram history.  It never
reads, repairs, or migrates that legacy file, and it accepts source records only
to immediately project them into the closed audit receipt schema.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from plugins.telegram.history_privacy import _AUDIT_KINDS, _AUDIT_STATUSES, project_telegram_audit_record


_AUDIT_FILE = "telegram_audit_receipts.json"
_AUDIT_SCHEMA = "odysseus.telegram.audit_store.v1"
_RECEIPT_SCHEMA = "odysseus.telegram.audit_receipt.v1"
_SCOPE_REF = re.compile(r"^(?:|chat_[0-9a-f]{12})$")
_RECEIPT_FIELDS = frozenset({
    "schema", "record_class", "direction", "kind", "status", "recorded_at",
    "raw_content_visible", "raw_identifiers_visible", "token_value_visible",
})
_RECORD_CLASSES = frozenset({"metadata_only", "raw_bearing"})
_DIRECTIONS = frozenset({"inbound", "outbound", "system", "unknown"})
_CLOCK_SKEW_SECONDS = 300
_DEFAULT_RETENTION_DAYS = 30
_DEFAULT_MAX_RECORDS = 100
_DEFAULT_MAX_BYTES = 131_072
_ENVELOPE_OVERHEAD_BYTES = 512
_POLICY_LIMITS = {
    "TELEGRAM_AUDIT_RETENTION_DAYS": (_DEFAULT_RETENTION_DAYS, 1, 90),
    "TELEGRAM_AUDIT_MAX_RECORDS": (_DEFAULT_MAX_RECORDS, 1, 1_000),
    "TELEGRAM_AUDIT_MAX_BYTES": (_DEFAULT_MAX_BYTES, 4_096, 1_048_576),
}
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_NOW = time.time


def _path_lock(path: Path) -> threading.RLock | None:
    try:
        key = os.path.normcase(str(path.resolve(strict=False)))
    except (OSError, RuntimeError):
        return None
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _compact_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _policy() -> tuple[int, int, int] | None:
    values: list[int] = []
    for name, (default, minimum, maximum) in _POLICY_LIMITS.items():
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            values.append(default)
            continue
        try:
            value = int(raw.strip())
        except ValueError:
            return None
        if not minimum <= value <= maximum:
            return None
        values.append(value)
    return tuple(values)  # type: ignore[return-value]


def _valid_receipt(receipt: Any, *, now: int) -> bool:
    if not isinstance(receipt, Mapping) or set(receipt) != _RECEIPT_FIELDS:
        return False
    if not isinstance(receipt.get("schema"), str) or receipt["schema"] != _RECEIPT_SCHEMA:
        return False
    if not isinstance(receipt.get("record_class"), str) or receipt["record_class"] not in _RECORD_CLASSES:
        return False
    if not isinstance(receipt.get("direction"), str) or receipt["direction"] not in _DIRECTIONS:
        return False
    if not isinstance(receipt.get("kind"), str) or receipt["kind"] not in _AUDIT_KINDS | {"unclassified"}:
        return False
    if not isinstance(receipt.get("status"), str) or receipt["status"] not in _AUDIT_STATUSES | {"unknown"}:
        return False
    recorded_at = receipt.get("recorded_at")
    if not isinstance(recorded_at, int) or isinstance(recorded_at, bool) or recorded_at <= 0:
        return False
    if recorded_at > now + _CLOCK_SKEW_SECONDS:
        return False
    return all(receipt.get(field) is False for field in (
        "raw_content_visible", "raw_identifiers_visible", "token_value_visible",
    ))


def _valid_entry(entry: Any, *, now: int) -> bool:
    return (
        isinstance(entry, Mapping)
        and set(entry) == {"scope_ref", "receipt"}
        and isinstance(entry.get("scope_ref"), str)
        and bool(_SCOPE_REF.fullmatch(entry["scope_ref"]))
        and _valid_receipt(entry.get("receipt"), now=now)
    )


def _generation_within_bounds(generation: list[dict[str, Any]], *, max_records: int, max_bytes: int) -> bool:
    return len(generation) <= max_records and len(_compact_bytes(generation)) <= max_bytes


class TelegramAuditStore:
    """Atomic two-generation receipt store; failures intentionally become no-ops."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / _AUDIT_FILE

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schema": _AUDIT_SCHEMA, "current": [], "previous": []}

    def _read_unlocked(self, *, now: int, max_records: int, max_bytes: int) -> tuple[dict[str, Any] | None, bool]:
        if not self.path.exists():
            return self._empty(), True
        try:
            max_envelope_bytes = 2 * max_bytes + _ENVELOPE_OVERHEAD_BYTES
            if self.path.stat().st_size > max_envelope_bytes:
                return None, False
            raw = self.path.read_bytes()
            if len(raw) > max_envelope_bytes:
                return None, False
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return None, False
        try:
            if not isinstance(payload, Mapping) or set(payload) != {"schema", "current", "previous"}:
                return None, False
            if payload.get("schema") != _AUDIT_SCHEMA:
                return None, False
            current, previous = payload.get("current"), payload.get("previous")
            if not isinstance(current, list) or not isinstance(previous, list):
                return None, False
            if not all(_valid_entry(entry, now=now) for entry in (*current, *previous)):
                return None, False
            if not _generation_within_bounds(current, max_records=max_records, max_bytes=max_bytes):
                return None, False
            if not _generation_within_bounds(previous, max_records=max_records, max_bytes=max_bytes):
                return None, False
        except (TypeError, ValueError, OverflowError):
            return None, False
        return {"schema": _AUDIT_SCHEMA, "current": list(current), "previous": list(previous)}, True

    def _write_unlocked(self, payload: dict[str, Any]) -> bool:
        temp_name = ""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{self.path.name}.tmp-", dir=self.data_dir, delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(_compact_bytes(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            temp_name = ""
            return True
        except OSError:
            return False
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    @staticmethod
    def _expired(entry: Mapping[str, Any], *, now: int, retention_days: int) -> bool:
        return entry["receipt"]["recorded_at"] < now - retention_days * 86_400

    def append(self, source: Mapping[str, Any] | None, *, scope_ref: str = "") -> bool:
        """Project and append one receipt, preserving legacy success on any failure."""

        policy = _policy()
        if policy is None or not isinstance(scope_ref, str) or not _SCOPE_REF.fullmatch(scope_ref):
            return False
        retention_days, max_records, max_bytes = policy
        now = int(_NOW())
        receipt = project_telegram_audit_record(source)
        if not _valid_receipt(receipt, now=now):
            return False
        entry = {"scope_ref": scope_ref, "receipt": receipt}
        if self._expired(entry, now=now, retention_days=retention_days):
            return False
        if not _generation_within_bounds([entry], max_records=max_records, max_bytes=max_bytes):
            return False
        lock = _path_lock(self.path)
        if lock is None:
            return False
        with lock:
            payload, available = self._read_unlocked(now=now, max_records=max_records, max_bytes=max_bytes)
            if not available or payload is None:
                return False
            current = [item for item in payload["current"] if not self._expired(item, now=now, retention_days=retention_days)]
            previous = [item for item in payload["previous"] if not self._expired(item, now=now, retention_days=retention_days)]
            candidate = current + [entry]
            if _generation_within_bounds(candidate, max_records=max_records, max_bytes=max_bytes):
                payload["current"] = candidate
                payload["previous"] = previous
            else:
                payload["current"] = [entry]
                payload["previous"] = current
            if len(_compact_bytes(payload)) > 2 * max_bytes + _ENVELOPE_OVERHEAD_BYTES:
                return False
            return self._write_unlocked(payload)

    def history(self, *, scope_ref: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Return only closed receipts newest first; no legacy fallback exists."""

        policy = _policy()
        if policy is None or (scope_ref is not None and (
            not isinstance(scope_ref, str) or not _SCOPE_REF.fullmatch(scope_ref)
        )):
            return []
        retention_days, max_records, max_bytes = policy
        now = int(_NOW())
        limit = max(1, min(int(limit or 50), 200))
        lock = _path_lock(self.path)
        if lock is None:
            return []
        with lock:
            payload, available = self._read_unlocked(now=now, max_records=max_records, max_bytes=max_bytes)
            if not available or payload is None:
                return []
            entries = [
                item for generation in (payload["current"], payload["previous"])
                for item in reversed(generation)
                if not self._expired(item, now=now, retention_days=retention_days)
                and (scope_ref is None or item["scope_ref"] == scope_ref)
            ]
        return [dict(item["receipt"]) for item in entries[:limit]]

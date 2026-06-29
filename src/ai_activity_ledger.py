"""Redacted ledger for upstream AI/model activity.

The ledger is metadata-first by design. It records when a model was called,
which safe route was used, timing, counts, hashes, and status. It must not
store raw prompts, document text, e-mail bodies, image data, headers, cookies,
or provider secrets.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from src.constants import DATA_DIR


AI_ACTIVITY_LEDGER_DIR = os.path.join(DATA_DIR, "ai_activity_ledger")
AI_ACTIVITY_LEDGER_SCHEMA = "odysseus.ai_activity.v1"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
_SECRET_KEY_HINTS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "api_key",
    "credential",
    "chat_id",
)


class AIActivityLedgerError(ValueError):
    """Raised when an activity record would be unsafe."""


def build_ai_activity_record(
    *,
    owner: str | None = None,
    surface: str | None = None,
    correlation_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    doc_id: str | None = None,
    prompt_type: str | None = None,
    provider: str | None = None,
    endpoint_url: str | None = None,
    model: str | None = None,
    messages: Any = None,
    input_chars: int | None = None,
    output_chars: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_ms: int | None = None,
    status: str = "unknown",
    error_class: str | None = None,
    cache_hit: bool = False,
    side_effects: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Build a safe ledger record without writing it."""

    prompt_hash = _stable_hash(messages) if messages is not None else ""
    if input_chars is None:
        input_chars = _input_char_count(messages)

    endpoint_hash, base_host = _endpoint_fingerprint(endpoint_url)
    record: dict[str, Any] = {
        "schema": AI_ACTIVITY_LEDGER_SCHEMA,
        "ts": _now_iso(),
        "owner": _safe_label(owner or "unknown", field="owner"),
        "surface": _safe_label(surface or "unknown", field="surface"),
        "correlation_id": _safe_label(correlation_id or "", field="correlation_id"),
        "session_id": _safe_label(session_id or "", field="session_id"),
        "task_id": _safe_label(task_id or "", field="task_id"),
        "doc_id": _safe_label(doc_id or "", field="doc_id"),
        "prompt_type": _safe_label(prompt_type or "unknown", field="prompt_type"),
        "provider": _safe_label(provider or "unknown", field="provider"),
        "endpoint_hash": endpoint_hash,
        "base_host": _safe_label(base_host, field="base_host"),
        "model": _safe_label(model or "unknown", field="model"),
        "prompt_hash": prompt_hash,
        "input_chars": _safe_count(input_chars),
        "output_chars": _safe_count(output_chars),
        "input_tokens": _safe_count(input_tokens),
        "output_tokens": _safe_count(output_tokens),
        "duration_ms": _safe_count(duration_ms),
        "status": _safe_label(status, field="status"),
        "error_class": _safe_label(error_class or "", field="error_class"),
        "cache_hit": bool(cache_hit),
        "side_effects": tuple(_safe_label(value, field="side_effect") for value in side_effects),
    }
    _reject_forbidden_payload(record)
    return record


def append_ai_activity(record: Mapping[str, Any]) -> dict[str, Any]:
    """Append one safe record to the date-partitioned JSONL ledger."""

    payload = dict(record)
    _reject_forbidden_payload(payload)
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return payload


def record_ai_activity(**kwargs: Any) -> dict[str, Any]:
    """Build and append a safe activity record."""

    return append_ai_activity(build_ai_activity_record(**kwargs))


def read_ai_activity(
    *,
    day: str | None = None,
    limit: int = 100,
    owner: str | None = None,
    surface: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Read recent redacted activity records for diagnostics.

    The reader returns only records already accepted by the ledger safety
    checks. Filters use the same label sanitizer as writes, so diagnostics
    callers cannot use this endpoint to probe host paths or secrets.
    """

    capped_limit = max(1, min(int(limit or 100), 1000))
    filters = {
        "owner": _safe_label(owner, field="owner_filter") if owner else "",
        "surface": _safe_label(surface, field="surface_filter") if surface else "",
        "status": _safe_label(status, field="status_filter") if status else "",
    }
    path = ledger_path(day)
    if not path.exists():
        return {
            "status": "success",
            "day": path.stem,
            "limit": capped_limit,
            "filters": {key: value for key, value in filters.items() if value},
            "count": 0,
            "records": [],
            "summary": _summarize_records([]),
        }

    matches: list[dict[str, Any]] = []
    skipped = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                _reject_forbidden_payload(record)
            except Exception:
                skipped += 1
                continue
            if not isinstance(record, dict):
                skipped += 1
                continue
            if any(value and str(record.get(key, "")) != value for key, value in filters.items()):
                continue
            matches.append(_diagnostic_record(record))

    recent = matches[-capped_limit:]
    recent.reverse()
    summary = _summarize_records(matches)
    summary["skipped"] = skipped
    return {
        "status": "success",
        "day": path.stem,
        "limit": capped_limit,
        "filters": {key: value for key, value in filters.items() if value},
        "count": len(recent),
        "total_matches": len(matches),
        "records": recent,
        "summary": summary,
    }


def ledger_path(day: str | None = None) -> Path:
    day_text = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_text):
        raise AIActivityLedgerError("ledger day must be YYYY-MM-DD")
    return Path(AI_ACTIVITY_LEDGER_DIR) / f"{day_text}.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        encoded = str(value)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()


def _input_char_count(messages: Any) -> int:
    if not isinstance(messages, (tuple, list)):
        return len(str(messages or ""))
    total = 0
    for message in messages:
        if isinstance(message, Mapping):
            total += _value_char_count(message.get("content"))
            total += _value_char_count(message.get("tool_calls"))
            total += _value_char_count(message.get("images"))
        else:
            total += len(str(message))
    return total


def _value_char_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Mapping):
        return sum(_value_char_count(v) for v in value.values())
    if isinstance(value, (tuple, list)):
        return sum(_value_char_count(v) for v in value)
    return len(str(value))


def _endpoint_fingerprint(endpoint_url: str | None) -> tuple[str, str]:
    text = str(endpoint_url or "").strip()
    endpoint_hash = "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest() if text else ""
    try:
        parsed = urlparse(text)
    except Exception:
        return endpoint_hash, ""
    return endpoint_hash, (parsed.netloc or parsed.hostname or "").lower()


def _safe_count(value: Any) -> int | None:
    if value is None:
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, count)


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(hint in lowered for hint in _SECRET_KEY_HINTS):
        raise AIActivityLedgerError(f"{field} contains forbidden secret marker")
    if any(ord(ch) < 32 for ch in text):
        raise AIActivityLedgerError(f"{field} contains control characters")
    if re.search(r"^[A-Za-z]:[\\/]|^/|^~", text):
        raise AIActivityLedgerError(f"{field} must not contain host paths")
    if not _SAFE_TOKEN_RE.fullmatch(text):
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return text[:180]


def _reject_forbidden_payload(payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    lowered = encoded.lower()
    forbidden = (
        "authorization",
        "bearer ",
        "api_key",
        "password",
        "cookie",
        "data:image",
        "private raw text",
        "begin private key",
    )
    if any(marker in lowered for marker in forbidden):
        raise AIActivityLedgerError("activity payload contains forbidden content marker")


_DIAGNOSTIC_FIELDS = (
    "ts",
    "owner",
    "surface",
    "correlation_id",
    "session_id",
    "task_id",
    "doc_id",
    "prompt_type",
    "provider",
    "base_host",
    "model",
    "input_chars",
    "output_chars",
    "input_tokens",
    "output_tokens",
    "duration_ms",
    "status",
    "error_class",
    "cache_hit",
    "side_effects",
)


def _diagnostic_record(record: Mapping[str, Any]) -> dict[str, Any]:
    safe = {field: record.get(field) for field in _DIAGNOSTIC_FIELDS if field in record}
    _reject_forbidden_payload(safe)
    return safe


def _summarize_records(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    by_surface: dict[str, int] = {}
    by_status: dict[str, int] = {}
    error_classes: dict[str, int] = {}
    total_duration = 0
    duration_count = 0
    for record in records:
        surface = str(record.get("surface") or "unknown")
        status = str(record.get("status") or "unknown")
        by_surface[surface] = by_surface.get(surface, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        error_class = str(record.get("error_class") or "")
        if error_class:
            error_classes[error_class] = error_classes.get(error_class, 0) + 1
        try:
            duration = int(record.get("duration_ms") or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration > 0:
            total_duration += duration
            duration_count += 1
    return {
        "total": sum(by_status.values()),
        "by_surface": by_surface,
        "by_status": by_status,
        "error_classes": error_classes,
        "avg_duration_ms": int(total_duration / duration_count) if duration_count else None,
    }

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
from typing import Any, Mapping
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

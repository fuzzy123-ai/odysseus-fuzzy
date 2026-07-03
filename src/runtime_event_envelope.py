"""Shared redacted runtime event envelope.

The envelope is a schema and validation layer for diagnostics. It does not own
storage and must not contain raw prompts, chat messages, document text, image
data, secrets, cookies, authorization headers, private host paths or verbose
tool output.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterable, Mapping
from uuid import uuid4


RUNTIME_EVENT_SCHEMA = "odysseus.runtime_event.v1"

REQUIRED_EVENT_FIELDS = (
    "ts",
    "event_id",
    "schema",
    "surface",
    "component",
    "event_type",
    "status",
    "severity",
    "owner_hash_or_owner_scope",
    "correlation_id",
    "privacy_level",
    "raw_content_visible",
)

SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
HOST_PATH_RE = re.compile(r"([A-Za-z]:\\|/(home|Users|var/lib|mnt|srv)/)", re.IGNORECASE)

FORBIDDEN_FIELD_NAMES = {
    "api_key",
    "authorization_header",
    "authorization",
    "cookie",
    "telegram_chat_id_raw",
    "telegram_token",
    "private_document_text",
    "private_email_body",
    "image_base64",
    "absolute_private_host_path",
    "unredacted_tool_output",
    "raw_prompt",
    "raw_output",
    "raw_document_text",
    "document_text",
    "email_body",
    "message_text",
    "chat_id",
    "token",
    "secret",
    "password",
    "credential",
}

FORBIDDEN_STRING_MARKERS = (
    "authorization:",
    "bearer ",
    "api_key",
    "telegram_token",
    "private_document_text",
    "private_email_body",
    "image_base64",
    "unredacted_tool_output",
)

SAFE_STATUSES = {
    "unknown",
    "received",
    "queued",
    "running",
    "success",
    "ok",
    "warn",
    "blocked",
    "failed",
    "error",
    "skipped",
    "dry_run",
}

SAFE_SEVERITIES = {"debug", "info", "notice", "warn", "error", "critical"}
SAFE_PRIVACY_LEVELS = {"public", "metadata", "private_metadata", "sensitive_metadata", "dsgvo_local"}


class RuntimeEventEnvelopeError(ValueError):
    """Raised when a runtime event would be unsafe or invalid."""


def build_runtime_event(
    *,
    surface: str,
    component: str,
    event_type: str,
    status: str = "unknown",
    severity: str = "info",
    owner: str | None = None,
    owner_scope: str | None = None,
    correlation_id: str | None = None,
    privacy_level: str = "metadata",
    raw_content_visible: bool = False,
    event_id: str | None = None,
    ts: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    doc_id: str | None = None,
    message_ref: str | None = None,
    run_id: str | None = None,
    model_ref: str | None = None,
    provider: str | None = None,
    duration_ms: int | None = None,
    input_chars: int | None = None,
    output_chars: int | None = None,
    error_class: str | None = None,
    retry_count: int | None = None,
    gate_ids: Iterable[Any] | None = None,
    side_effects: Iterable[Any] | None = None,
    source_event_ids: Iterable[Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one safe runtime event without writing it."""

    if raw_content_visible:
        raise RuntimeEventEnvelopeError("runtime events must not include raw content")

    normalized_status = _safe_label(status or "unknown", field="status")
    if normalized_status not in SAFE_STATUSES:
        raise RuntimeEventEnvelopeError("unsupported runtime event status")

    normalized_severity = _safe_label(severity or "info", field="severity")
    if normalized_severity not in SAFE_SEVERITIES:
        raise RuntimeEventEnvelopeError("unsupported runtime event severity")

    normalized_privacy = _safe_label(privacy_level or "metadata", field="privacy_level")
    if normalized_privacy not in SAFE_PRIVACY_LEVELS:
        raise RuntimeEventEnvelopeError("unsupported runtime event privacy_level")

    event: dict[str, Any] = {
        "ts": _safe_timestamp(ts),
        "event_id": _safe_event_id(event_id),
        "schema": RUNTIME_EVENT_SCHEMA,
        "surface": _safe_label(surface, field="surface"),
        "component": _safe_label(component, field="component"),
        "event_type": _safe_label(event_type, field="event_type"),
        "status": normalized_status,
        "severity": normalized_severity,
        "owner_hash_or_owner_scope": _owner_hash_or_scope(owner=owner, owner_scope=owner_scope),
        "correlation_id": _safe_label(correlation_id or "", field="correlation_id"),
        "privacy_level": normalized_privacy,
        "raw_content_visible": False,
        "session_id": _safe_label(session_id or "", field="session_id"),
        "task_id": _safe_label(task_id or "", field="task_id"),
        "doc_id": _safe_label(doc_id or "", field="doc_id"),
        "message_ref": _safe_reference(message_ref),
        "run_id": _safe_label(run_id or "", field="run_id"),
        "model_ref": _safe_label(model_ref or "", field="model_ref"),
        "provider": _safe_label(provider or "", field="provider"),
        "duration_ms": _safe_count(duration_ms),
        "input_chars": _safe_count(input_chars),
        "output_chars": _safe_count(output_chars),
        "error_class": _safe_label(error_class or "", field="error_class"),
        "retry_count": _safe_count(retry_count),
        "gate_ids": tuple(_safe_label(value, field="gate_id") for value in (gate_ids or ())),
        "side_effects": tuple(_safe_label(value, field="side_effect") for value in (side_effects or ())),
        "source_event_ids": tuple(_safe_event_id(value) for value in (source_event_ids or ())),
        "metadata": _safe_metadata(metadata or {}),
    }
    _validate_required_fields(event)
    _reject_forbidden_payload(event)
    return event


def event_for_loki(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a low-cardinality projection suitable for log shipping labels."""

    _validate_required_fields(event)
    _reject_forbidden_payload(event)
    return {
        "schema": RUNTIME_EVENT_SCHEMA,
        "labels": {
            "surface": _safe_label(event.get("surface"), field="surface"),
            "component": _safe_label(event.get("component"), field="component"),
            "event_type": _safe_label(event.get("event_type"), field="event_type"),
            "status": _safe_label(event.get("status"), field="status"),
            "severity": _safe_label(event.get("severity"), field="severity"),
        },
        "payload": {
            "ts": _safe_timestamp(event.get("ts")),
            "event_id": _safe_event_id(event.get("event_id")),
            "correlation_id": _safe_label(event.get("correlation_id") or "", field="correlation_id"),
            "owner_hash_or_owner_scope": _safe_label(
                event.get("owner_hash_or_owner_scope") or "owner:unknown",
                field="owner_hash_or_owner_scope",
            ),
            "privacy_level": _safe_label(event.get("privacy_level") or "metadata", field="privacy_level"),
            "raw_content_visible": False,
            "duration_ms": _safe_count(event.get("duration_ms")),
            "retry_count": _safe_count(event.get("retry_count")),
            "error_class": _safe_label(event.get("error_class") or "", field="error_class"),
        },
        "raw_content_visible": False,
    }


def required_fields_missing(event: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(field for field in REQUIRED_EVENT_FIELDS if field not in event)


def stable_payload_hash(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        encoded = str(value)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()


def _validate_required_fields(event: Mapping[str, Any]) -> None:
    missing = required_fields_missing(event)
    if missing:
        raise RuntimeEventEnvelopeError(f"runtime event missing fields: {', '.join(missing)}")
    if event.get("schema") != RUNTIME_EVENT_SCHEMA:
        raise RuntimeEventEnvelopeError("unsupported runtime event schema")
    if bool(event.get("raw_content_visible")):
        raise RuntimeEventEnvelopeError("runtime event raw_content_visible must be false")


def _safe_timestamp(value: Any = None) -> str:
    if value is None or value == "":
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?", text):
        raise RuntimeEventEnvelopeError("timestamp must be ISO-like")
    return text[:40]


def _safe_event_id(value: Any = None) -> str:
    text = str(value or "").strip()
    if not text:
        text = f"evt-{uuid4().hex}"
    return _safe_label(text, field="event_id")


def _owner_hash_or_scope(*, owner: str | None, owner_scope: str | None) -> str:
    if owner_scope:
        return "scope:" + _safe_label(owner_scope, field="owner_scope")
    owner_text = str(owner or "unknown")
    digest = hashlib.sha256(owner_text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"owner:sha256:{digest}"


def _safe_reference(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if HOST_PATH_RE.search(text):
        return stable_payload_hash(text)
    return _safe_label(text, field="reference")


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if HOST_PATH_RE.search(text):
        raise RuntimeEventEnvelopeError(f"{field} contains a private host path")
    if len(text) > 180:
        raise RuntimeEventEnvelopeError(f"{field} is too long for a runtime label")
    lowered = text.lower()
    if any(marker in lowered for marker in FORBIDDEN_STRING_MARKERS):
        raise RuntimeEventEnvelopeError(f"{field} contains a forbidden marker")
    if not SAFE_LABEL_RE.fullmatch(text):
        raise RuntimeEventEnvelopeError(f"{field} contains unsafe characters")
    return text


def _safe_count(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), 1_000_000_000))
    except (TypeError, ValueError):
        return 0


def _safe_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise RuntimeEventEnvelopeError("metadata must be a mapping")
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if str(key or "").strip().lower() in FORBIDDEN_FIELD_NAMES:
            raise RuntimeEventEnvelopeError("metadata contains a forbidden field")
        safe_key = _safe_label(key, field="metadata_key")
        if safe_key.lower() in FORBIDDEN_FIELD_NAMES:
            raise RuntimeEventEnvelopeError("metadata contains a forbidden field")
        result[safe_key] = _safe_metadata_value(value, key=safe_key)
    return result


def _safe_metadata_value(value: Any, *, key: str) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return _safe_count(value)
    if isinstance(value, float):
        return max(0.0, min(float(value), 1_000_000_000.0))
    if isinstance(value, str):
        text = value.strip()
        if HOST_PATH_RE.search(text):
            return stable_payload_hash(text)
        if len(text) > 240:
            raise RuntimeEventEnvelopeError(f"metadata value for {key} is too long")
        return _safe_label(text, field=f"metadata.{key}")
    if isinstance(value, (tuple, list)):
        if len(value) > 20:
            raise RuntimeEventEnvelopeError(f"metadata list for {key} is too long")
        return tuple(_safe_metadata_value(item, key=key) for item in value)
    if isinstance(value, Mapping):
        return _safe_metadata(value)
    return stable_payload_hash(value)


def _reject_forbidden_payload(value: Any, *, key: str = "") -> None:
    if key.lower() in FORBIDDEN_FIELD_NAMES:
        raise RuntimeEventEnvelopeError("runtime event contains a forbidden field")
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            _reject_forbidden_payload(nested_value, key=str(nested_key))
        return
    if isinstance(value, (tuple, list, set)):
        for item in value:
            _reject_forbidden_payload(item, key=key)
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in FORBIDDEN_STRING_MARKERS):
            raise RuntimeEventEnvelopeError("runtime event contains a forbidden marker")
        if HOST_PATH_RE.search(value):
            raise RuntimeEventEnvelopeError("runtime event contains a private host path")

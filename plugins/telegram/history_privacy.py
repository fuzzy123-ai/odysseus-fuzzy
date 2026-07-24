"""Closed, content-free projections for Telegram audit surfaces.

The plugin's local store has legacy mixed records: some records are intentionally
used as the internal conversation transport and can contain raw text, while
others are audit events.  This module never turns the mixed store into an audit
source by omission.  It classifies uncertain records conservatively and emits a
small allowlisted receipt for public/admin diagnostics.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


_SAFE_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@/-]{0,79}$")
_RAW_FIELD_NAMES = frozenset({
    "text", "caption", "content", "prompt", "persisted_prompt", "reply",
    "reply_text", "transcript", "transcription", "context", "error",
    "exception", "failure_reason", "traceback", "path", "output_path",
    "filename", "file_name", "token", "chat_id", "sender_id", "file_id",
    "file_unique_id", "session_id", "message_id", "update_id",
})
_SAFE_RECORD_FIELDS = frozenset({
    "schema", "direction", "kind", "status", "stored_at", "updated_at",
    "delivery_status", "delivery_mode", "formatting_mode", "intake_status",
    "transcript_status", "voice_status", "universal_inbox_status",
    "raw_content_visible", "raw_content_persisted", "raw_identifiers_visible",
    "token_value_visible", "chat_id_value_visible", "raw_rich_payload_visible",
    "filename_visible", "host_paths_visible", "spool_path_visible",
    "path_visible", "stored", "chat_allowed", "plugin", "date",
    "runtime_event", "correlation_id", "chat_handle", "sender", "media",
})
_AUDIT_KINDS = frozenset({
    "agent_turn", "blocked", "command", "control_command", "document",
    "duplicate", "image", "message", "reply_delivery", "session_bridge",
    "text", "unsupported", "voice",
})
_AUDIT_STATUSES = frozenset({
    "accepted", "blocked", "blocked_chat", "chat_not_allowed", "duplicate_ignored",
    "failed", "handled", "pending_image_action", "pending_stt", "pending_universal_inbox",
    "processed", "ready", "sent", "skipped", "unsupported",
})
_RUNTIME_EVENT_FIELDS = frozenset({
    "ts", "event_id", "schema", "surface", "component", "event_type", "status",
    "severity", "owner_hash_or_owner_scope", "correlation_id", "privacy_level",
    "raw_content_visible", "session_id", "task_id", "doc_id", "message_ref", "run_id",
    "model_ref", "provider", "duration_ms", "input_chars", "output_chars", "error_class",
    "retry_count", "gate_ids", "side_effects", "source_event_ids", "metadata",
})
_RUNTIME_STRING_FIELDS = frozenset({
    "event_id", "schema", "surface", "component", "event_type", "status", "severity",
    "owner_hash_or_owner_scope", "correlation_id", "privacy_level", "session_id", "task_id",
    "doc_id", "message_ref", "run_id", "model_ref", "provider", "error_class",
})
_RUNTIME_LIST_FIELDS = frozenset({"gate_ids", "side_effects", "source_event_ids"})
_RUNTIME_METADATA_FIELDS = frozenset({
    "telegram_status", "direction", "kind", "status", "chat_handle", "stored_at",
    "token_value_visible", "chat_id_value_visible", "intake_status", "transcript_status",
    "universal_inbox_status", "delivery_status", "delivery_mode", "formatting_mode",
    "truth_gate_status", "truth_gate_changed",
    "correlation_id",
})
_RUNTIME_STATUSES = frozenset({"success", "blocked", "failed", "queued", "skipped", "unknown"})
_RUNTIME_SEVERITIES = frozenset({"debug", "info", "warn", "error"})
_RUNTIME_PRIVACY_LEVELS = frozenset({"private_metadata"})


def _safe_token(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    return text if _SAFE_TOKEN.fullmatch(text) else default


def _bounded_timestamp(value: Any) -> int:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return 0
    return timestamp if 0 < timestamp < 4_102_444_800 else 0


def _safe_runtime_value(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return 0 <= value < 4_102_444_800
    return isinstance(value, str) and bool(_SAFE_TOKEN.fullmatch(value))


def _safe_runtime_string(value: Any) -> bool:
    return isinstance(value, str) and (not value or bool(_SAFE_TOKEN.fullmatch(value)))


def _valid_runtime_event(value: Any) -> bool:
    """Accept only the closed, content-free runtime event envelope."""

    if not isinstance(value, Mapping) or set(value) != _RUNTIME_EVENT_FIELDS:
        return False
    if not isinstance(value.get("ts"), str) or not value["ts"].endswith("Z"):
        return False
    if not all(_safe_runtime_string(value[field]) for field in _RUNTIME_STRING_FIELDS):
        return False
    if value["schema"] != "odysseus.runtime_event.v1":
        return False
    if value["surface"] != "telegram" or value["status"] not in _RUNTIME_STATUSES:
        return False
    if value["severity"] not in _RUNTIME_SEVERITIES or value["privacy_level"] not in _RUNTIME_PRIVACY_LEVELS:
        return False
    if value["raw_content_visible"] is not False:
        return False
    if not all(isinstance(value[field], int) and not isinstance(value[field], bool) and 0 <= value[field] < 4_102_444_800
               for field in ("duration_ms", "input_chars", "output_chars", "retry_count")):
        return False
    for field in _RUNTIME_LIST_FIELDS:
        items = value[field]
        if not isinstance(items, (tuple, list)) or len(items) > 20 or not all(_safe_runtime_value(item) for item in items):
            return False
    metadata = value["metadata"]
    if not isinstance(metadata, Mapping) or not set(metadata).issubset(_RUNTIME_METADATA_FIELDS):
        return False
    return all(_safe_runtime_value(item) or (
        isinstance(item, (tuple, list)) and len(item) <= 20 and all(_safe_runtime_value(part) for part in item)
    ) for item in metadata.values())


def _is_allowlisted_audit_value(value: Any, allowed: frozenset[str]) -> bool:
    """Treat missing audit category fields as neutral, but never trust unknown ones."""

    if value in (None, "", False):
        return True
    return _safe_token(value, default="") in allowed


def record_has_raw_content(record: Mapping[str, Any] | None) -> bool:
    """Return true when a mixed record is raw-bearing or cannot be proven safe."""

    if not isinstance(record, Mapping):
        return True
    if bool(record.get("raw_content_visible")) or bool(record.get("raw_content_persisted")):
        return True
    if not _is_allowlisted_audit_value(record.get("kind"), _AUDIT_KINDS):
        return True
    for field in ("status", "delivery_status", "intake_status"):
        if not _is_allowlisted_audit_value(record.get(field), _AUDIT_STATUSES):
            return True
    for key, value in record.items():
        normalized = str(key or "").strip().lower()
        if normalized in _RAW_FIELD_NAMES and value not in (None, "", False):
            return True
        if normalized not in _SAFE_RECORD_FIELDS and value not in (None, "", False, 0):
            # Unknown payload fields are not evidence of redaction.  This keeps
            # future append_event extras from being silently misclassified.
            return True
        if normalized in {"sender", "media"} and value:
            # These are legacy nested records; they may include display/profile
            # data or provider metadata, so treat them as raw-bearing.
            return True
        if normalized == "runtime_event" and value and not _valid_runtime_event(value):
            # Runtime events are safe only when their entire nested envelope is
            # a known, closed shape.  Unknown nested data is raw-bearing.
            return True
    return False


def project_telegram_audit_record(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Create the only supported content-free projection of a store record."""

    source = record if isinstance(record, Mapping) else {}
    raw_bearing = record_has_raw_content(source)
    direction = _safe_token(source.get("direction"), default="unknown")
    if direction not in {"inbound", "outbound", "system"}:
        direction = "unknown"
    projected = {
        "schema": "odysseus.telegram.audit_receipt.v1",
        "record_class": "raw_bearing" if raw_bearing else "metadata_only",
        "direction": direction,
        "kind": _safe_token(source.get("kind"), default="unclassified"),
        "status": _safe_token(source.get("status") or source.get("delivery_status") or source.get("intake_status"), default="unknown"),
        "recorded_at": _bounded_timestamp(source.get("stored_at") or source.get("updated_at")),
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "token_value_visible": False,
    }
    if projected["kind"] not in _AUDIT_KINDS:
        projected["kind"] = "unclassified"
    if projected["status"] not in _AUDIT_STATUSES:
        projected["status"] = "unknown"
    return projected

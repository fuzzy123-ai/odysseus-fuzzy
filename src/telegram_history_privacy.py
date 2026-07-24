"""Privacy, capacity and diagnostic contracts for Telegram history stores."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import time
from typing import Any, Iterable, Mapping, Sequence


TELEGRAM_HISTORY_EXPORT_SCHEMA = "odysseus.telegram_history_export.v1"
TELEGRAM_RAW_STORE_SCHEMA = "odysseus.telegram_raw_conversation_store.v1"
TELEGRAM_AUDIT_STORE_SCHEMA = "odysseus.telegram_redacted_audit_store.v1"
TELEGRAM_HISTORY_RETENTION_LIVE_GATE = "TTD-LIVE-HISTORY-RETENTION"

_DIAGNOSTIC_FIELDS = (
    "direction",
    "kind",
    "status",
    "intake_status",
    "delivery_status",
    "transcript_status",
    "voice_status",
    "universal_inbox_status",
    "image_action_status",
    "stored_at",
    "updated_at",
    "chat_handle",
    "correlation_id",
    "delivery_mode",
    "formatting_mode",
    "store_class",
    "raw_content_visible",
    "raw_identifiers_visible",
)
_RAW_CONTENT_FIELDS = (
    "text",
    "prompt",
    "reply_text",
    "transcript",
    "ocr_text",
    "extracted_text",
    "caption",
)


@dataclass(frozen=True)
class TelegramHistoryPolicy:
    max_entry_bytes: int = 262_144
    max_file_bytes: int = 5_242_880
    max_entries_per_segment: int = 2_000
    max_segments: int = 8
    rotation_enabled: bool = True
    retention_days: int = 30
    configuration_errors: tuple[str, ...] = ()

    @classmethod
    def from_environment(cls) -> "TelegramHistoryPolicy":
        defaults = cls()
        errors: list[str] = []

        def positive(name: str, default: int, upper: int) -> int:
            raw = str(os.getenv(name) or default).strip()
            try:
                value = int(raw)
            except ValueError:
                value = default
                errors.append(f"{name}:invalid_integer")
            if value <= 0 or value > upper:
                value = default
                errors.append(f"{name}:out_of_range")
            return value

        rotation_raw = str(os.getenv("TELEGRAM_HISTORY_ROTATION_ENABLED") or "true").strip().lower()
        if rotation_raw not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
            errors.append("TELEGRAM_HISTORY_ROTATION_ENABLED:invalid_boolean")
            rotation_enabled = defaults.rotation_enabled
        else:
            rotation_enabled = rotation_raw in {"1", "true", "yes", "on"}
        return cls(
            max_entry_bytes=positive(
                "TELEGRAM_HISTORY_MAX_ENTRY_BYTES", defaults.max_entry_bytes, 10_485_760
            ),
            max_file_bytes=positive(
                "TELEGRAM_HISTORY_MAX_FILE_BYTES", defaults.max_file_bytes, 104_857_600
            ),
            max_entries_per_segment=positive(
                "TELEGRAM_HISTORY_MAX_ENTRIES", defaults.max_entries_per_segment, 100_000
            ),
            max_segments=positive(
                "TELEGRAM_HISTORY_MAX_SEGMENTS", defaults.max_segments, 1_000
            ),
            rotation_enabled=rotation_enabled,
            retention_days=positive(
                "TELEGRAM_HISTORY_RETENTION_DAYS", defaults.retention_days, 3_650
            ),
            configuration_errors=tuple(errors),
        )

    def as_public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["configuration_errors"] = list(self.configuration_errors)
        result.update(
            {
                "retention_apply_supported": False,
                "retention_mode": "preview_only",
                "required_live_gate": TELEGRAM_HISTORY_RETENTION_LIVE_GATE,
                "existing_data_migrated": False,
                "existing_data_deleted": False,
            }
        )
        return result


def record_contains_raw_content(record: Mapping[str, Any]) -> bool:
    return bool(record.get("raw_content_visible")) or any(
        bool(str(record.get(field) or "").strip()) for field in _RAW_CONTENT_FIELDS
    )


def mark_raw_conversation_record(record: Mapping[str, Any]) -> dict[str, Any]:
    marked = dict(record)
    marked["store_class"] = "raw_conversation"
    marked["raw_content_visible"] = bool(
        marked.get("raw_content_visible") or record_contains_raw_content(marked)
    )
    marked["raw_identifiers_visible"] = bool(marked.get("raw_identifiers_visible"))
    return marked


def mark_redacted_audit_record(record: Mapping[str, Any]) -> dict[str, Any]:
    marked = dict(record)
    for field in _RAW_CONTENT_FIELDS:
        marked.pop(field, None)
    marked["store_class"] = "redacted_audit"
    marked["raw_content_visible"] = False
    marked["raw_identifiers_visible"] = False
    return marked


def build_history_diagnostic_export(
    *,
    raw_records: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    audit_records: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    policy: TelegramHistoryPolicy,
    limit: int = 50,
    review_details: bool = False,
    operator_authorized: bool = False,
    now: int | None = None,
) -> dict[str, Any]:
    raw = [dict(item) for item in raw_records if isinstance(item, Mapping)]
    audit = [dict(item) for item in audit_records if isinstance(item, Mapping)]
    bounded_limit = max(1, min(int(limit or 50), 200))
    exact_review = bool(review_details and operator_authorized)
    combined = sorted(
        [*raw, *audit],
        key=lambda item: (int(item.get("stored_at") or 0), str(item.get("correlation_id") or "")),
    )
    selected = list(reversed(combined[-bounded_limit:]))
    if exact_review:
        events = selected
        mode = "operator_authorized_exact_review"
    else:
        events = [project_diagnostic_record(item) for item in selected]
        mode = "redacted_default"

    current = int(now if now is not None else time.time())
    cutoff = current - policy.retention_days * 86_400
    expired_raw = sum(1 for item in raw if int(item.get("stored_at") or current) < cutoff)
    expired_audit = sum(1 for item in audit if int(item.get("stored_at") or current) < cutoff)
    return {
        "schema": TELEGRAM_HISTORY_EXPORT_SCHEMA,
        "mode": mode,
        "events": events,
        "event_count": len(events),
        "raw_record_count": len(raw),
        "audit_record_count": len(audit),
        "raw_content_visible": exact_review and any(
            record_contains_raw_content(item) for item in selected
        ),
        "raw_identifiers_visible": False,
        "review_details_requested": bool(review_details),
        "operator_authorized": bool(operator_authorized),
        "not_for_persistence": exact_review,
        "output_path_available": False,
        "retention_preview": {
            "mode": "preview_only",
            "expired_raw_record_count": expired_raw,
            "expired_audit_record_count": expired_audit,
            "apply_supported": False,
            "required_live_gate": TELEGRAM_HISTORY_RETENTION_LIVE_GATE,
        },
        "policy": policy.as_public_dict(),
    }


def project_diagnostic_record(record: Mapping[str, Any]) -> dict[str, Any]:
    projection = {
        field: record[field]
        for field in _DIAGNOSTIC_FIELDS
        if field in record and _diagnostic_scalar(record[field])
    }
    projection["record_ref"] = _record_ref(record)
    projection["raw_content_visible"] = False
    projection["raw_identifiers_visible"] = False
    projection["source_record_contains_raw_content"] = record_contains_raw_content(record)
    return projection


def encoded_record_size(record: Mapping[str, Any]) -> int:
    return len(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def encoded_store_size(records: Sequence[Mapping[str, Any]], *, store_class: str) -> int:
    schema = TELEGRAM_RAW_STORE_SCHEMA if store_class == "raw_conversation" else TELEGRAM_AUDIT_STORE_SCHEMA
    payload = {
        "schema": schema,
        "store_class": store_class,
        "raw_content_visible": store_class == "raw_conversation"
        and any(record_contains_raw_content(item) for item in records),
        "raw_identifiers_visible": False,
        "messages": list(records),
    }
    return len(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))


def _record_ref(record: Mapping[str, Any]) -> str:
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(
        b"odysseus.telegram-history-record.v1\0" + canonical.encode("utf-8")
    ).hexdigest()[:20]
    return f"history_ref:{digest}"


def _diagnostic_scalar(value: Any) -> bool:
    if isinstance(value, (bool, int)):
        return True
    if not isinstance(value, str):
        return False
    return len(value) <= 160 and "\n" not in value and "\r" not in value

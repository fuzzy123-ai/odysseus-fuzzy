"""Redacted debug bundle generator for incident diagnostics.

Bundles are compact evidence packages assembled from already-redacted runtime
events and summaries. They must be safe to attach to an issue or handoff:
metadata only, no raw document text, chat ids, tokens, host paths, or provider
output.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable, Mapping

from src.runtime_event_envelope import RUNTIME_EVENT_SCHEMA, stable_payload_hash


DEBUG_BUNDLE_SCHEMA = "odysseus.debug_bundle.v1"

_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
_HOST_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]|^/|^~")
_FORBIDDEN_MARKERS = (
    "authorization:",
    "bearer ",
    "api_key",
    "password=",
    "cookie:",
    "telegram_token",
    "chat_id",
    "private_document_text",
    "private_email_body",
    "image_base64",
    "unredacted_tool_output",
)


class DebugBundleError(ValueError):
    """Raised when a debug bundle would contain unsafe material."""


def build_redacted_debug_bundle(
    *,
    incident_ref: str,
    events: Iterable[Mapping[str, Any]],
    summaries: Iterable[Mapping[str, Any]] = (),
    limit: int = 50,
) -> dict[str, Any]:
    """Build one bounded redacted bundle from runtime events and summaries."""

    capped_limit = _safe_limit(limit)
    safe_events = tuple(_safe_event(event) for event in events)[:capped_limit]
    safe_summaries = tuple(_safe_summary(summary) for summary in summaries)[:20]
    event_counts = _count_by(safe_events, "event_type")
    status_counts = _count_by(safe_events, "status")
    surface_counts = _count_by(safe_events, "surface")
    correlation_ids = tuple(
        dict.fromkeys(str(event.get("correlation_id") or "") for event in safe_events if event.get("correlation_id"))
    )
    bundle = {
        "schema": DEBUG_BUNDLE_SCHEMA,
        "bundle_id": _bundle_id(incident_ref, safe_events, safe_summaries),
        "created_at": _now_iso(),
        "incident_ref": _safe_ref(incident_ref),
        "status": "ready" if safe_events or safe_summaries else "empty",
        "event_count": len(safe_events),
        "summary_count": len(safe_summaries),
        "limit": capped_limit,
        "correlation_ids": correlation_ids,
        "event_counts": event_counts,
        "status_counts": status_counts,
        "surface_counts": surface_counts,
        "timeline": tuple(_timeline_item(event) for event in safe_events),
        "summaries": safe_summaries,
        "evidence_refs": tuple(_evidence_ref(event) for event in safe_events),
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "writes_performed": False,
    }
    _reject_forbidden_payload(bundle)
    return bundle


def summarize_debug_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact redacted projection of a bundle."""

    if not isinstance(bundle, Mapping):
        raise DebugBundleError("bundle must be a mapping")
    _reject_forbidden_payload(bundle)
    return {
        "schema": DEBUG_BUNDLE_SCHEMA,
        "bundle_id": _safe_ref(bundle.get("bundle_id")),
        "incident_ref": _safe_ref(bundle.get("incident_ref")),
        "status": _safe_label(bundle.get("status"), field="status"),
        "event_count": _safe_limit(bundle.get("event_count"), default=0, maximum=1_000_000),
        "summary_count": _safe_limit(bundle.get("summary_count"), default=0, maximum=1_000_000),
        "correlation_count": len(tuple(bundle.get("correlation_ids") or ())),
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "writes_performed": False,
    }


def _safe_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise DebugBundleError("event must be a mapping")
    if bool(event.get("raw_content_visible")):
        raise DebugBundleError("raw event content is not allowed")
    if str(event.get("schema") or RUNTIME_EVENT_SCHEMA) != RUNTIME_EVENT_SCHEMA:
        raise DebugBundleError("unsupported event schema")
    _reject_forbidden_payload(event)
    return {
        "ts": _safe_ref(event.get("ts")),
        "event_id": _safe_ref(event.get("event_id")),
        "surface": _safe_label(event.get("surface"), field="surface"),
        "component": _safe_label(event.get("component"), field="component"),
        "event_type": _safe_label(event.get("event_type"), field="event_type"),
        "status": _safe_label(event.get("status"), field="status"),
        "severity": _safe_label(event.get("severity"), field="severity"),
        "correlation_id": _safe_ref(event.get("correlation_id")),
        "privacy_level": _safe_label(event.get("privacy_level"), field="privacy_level"),
        "error_class": _safe_label(event.get("error_class"), field="error_class"),
        "raw_content_visible": False,
    }


def _safe_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        return {}
    _reject_forbidden_payload(summary)
    safe: dict[str, Any] = {}
    for key, value in list(summary.items())[:40]:
        safe_key = _safe_label(key, field="summary_key")
        safe[safe_key] = _safe_value(value)
    return safe


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(0, min(value, 1_000_000))
    if isinstance(value, float):
        return max(0.0, min(value, 1_000_000.0))
    if isinstance(value, Mapping):
        return _safe_summary(value)
    if isinstance(value, (tuple, list)):
        return tuple(_safe_value(item) for item in value[:20])
    return _safe_ref(value)


def _timeline_item(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ts": _safe_ref(event.get("ts")),
        "event_id": _safe_ref(event.get("event_id")),
        "surface": _safe_label(event.get("surface"), field="surface"),
        "component": _safe_label(event.get("component"), field="component"),
        "event_type": _safe_label(event.get("event_type"), field="event_type"),
        "status": _safe_label(event.get("status"), field="status"),
        "severity": _safe_label(event.get("severity"), field="severity"),
        "correlation_id": _safe_ref(event.get("correlation_id")),
    }


def _evidence_ref(event: Mapping[str, Any]) -> str:
    return stable_payload_hash(
        {
            "event_id": event.get("event_id") or "",
            "correlation_id": event.get("correlation_id") or "",
            "event_type": event.get("event_type") or "",
        }
    )


def _bundle_id(incident_ref: str, events: tuple[Mapping[str, Any], ...], summaries: tuple[Mapping[str, Any], ...]) -> str:
    digest = stable_payload_hash(
        {
            "incident_ref": _safe_ref(incident_ref),
            "events": tuple(_evidence_ref(event) for event in events),
            "summaries": summaries,
        }
    ).split(":", 1)[1][:16]
    return f"dbg-{digest}"


def _count_by(events: Iterable[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        key = _safe_label(event.get(field), field=field) or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _safe_limit(value: Any, *, default: int = 50, maximum: int = 100) -> int:
    try:
        return max(1 if default else 0, min(int(value if value is not None else default), maximum))
    except (TypeError, ValueError):
        return default


def _safe_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if _HOST_PATH_RE.search(text) or any(marker in lowered for marker in _FORBIDDEN_MARKERS):
        return stable_payload_hash(text)
    if len(text) > 180:
        return stable_payload_hash(text)
    if not _SAFE_LABEL_RE.fullmatch(text):
        return stable_payload_hash(text)
    return text


def _safe_label(value: Any, *, field: str) -> str:
    text = _safe_ref(value)
    if text.startswith("sha256:"):
        return text
    if len(text) > 180:
        raise DebugBundleError(f"{field} is too long")
    return text


def _reject_forbidden_payload(value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).lower()
    if any(marker in encoded for marker in _FORBIDDEN_MARKERS):
        raise DebugBundleError("debug bundle payload contains forbidden marker")
    if re.search(r"[a-z]:[\\/]|/(home|users|var/lib|mnt|srv|opt)/|~[\\/]", encoded):
        raise DebugBundleError("debug bundle payload contains a host path")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

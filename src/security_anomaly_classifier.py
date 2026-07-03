"""Classify defensive security anomalies from redacted runtime events.

The classifier consumes already-redacted event envelopes and observability
summaries. It never executes remediation, persists raw logs, or requires live
access.
"""

from __future__ import annotations

from collections import defaultdict
import json
from typing import Any, Iterable, Mapping

from src.runtime_event_envelope import RUNTIME_EVENT_SCHEMA, stable_payload_hash
from src.security_incident_model import build_recommended_action, build_security_incident


SECURITY_ANOMALY_CLASSIFIER_SCHEMA = "odysseus.security_anomaly_classifier.v1"


class SecurityAnomalyClassifierError(ValueError):
    """Raised when classifier input is unsafe."""


def classify_security_anomalies(
    events: Iterable[Mapping[str, Any]],
    *,
    observability_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return redacted incident candidates for known anomaly families."""

    normalized_events = tuple(_safe_event(event) for event in events)
    summary = _safe_summary(observability_summary or {})
    incidents: list[dict[str, Any]] = []
    incidents.extend(_classify_auth_failures(normalized_events, summary))
    incidents.extend(_classify_endpoint_probing(normalized_events, summary))
    incidents.extend(_classify_telegram_abuse(normalized_events, summary))
    incidents.extend(_classify_service_down(normalized_events, summary))
    incidents.extend(_classify_secret_leak_indicators(normalized_events, summary))
    return {
        "schema": SECURITY_ANOMALY_CLASSIFIER_SCHEMA,
        "status": "success",
        "event_count": len(normalized_events),
        "incident_count": len(incidents),
        "incidents": tuple(incidents),
        "summary": {
            "auth_failure_events": _count_events(normalized_events, _is_auth_failure),
            "endpoint_probe_events": _count_events(normalized_events, _is_endpoint_probe),
            "telegram_abuse_events": _count_events(normalized_events, _is_telegram_abuse),
            "service_down_events": _count_events(normalized_events, _is_service_down),
            "secret_leak_indicator_events": _count_events(normalized_events, _is_secret_leak_indicator),
        },
        "raw_content_visible": False,
    }


def _classify_auth_failures(events: tuple[dict[str, Any], ...], summary: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    grouped = _group(events, _is_auth_failure, key_fields=("surface", "component"))
    incidents: list[dict[str, Any]] = []
    for key, items in grouped.items():
        count = len(items) + _summary_count(summary, "auth_failure_count")
        if count < 3:
            continue
        incidents.append(
            _incident(
                level=2,
                severity="medium",
                confidence=min(0.95, 0.58 + count * 0.08),
                trigger="repeated_auth_failures",
                affected_surfaces=_surfaces(items, fallback=key[0] or "auth"),
                events=items,
                actions=("read_only_diagnostics", "redacted_debug_bundle", "operator_notification"),
            )
        )
    return tuple(incidents)


def _classify_endpoint_probing(events: tuple[dict[str, Any], ...], summary: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    items = tuple(event for event in events if _is_endpoint_probe(event))
    count = len(items) + _summary_count(summary, "endpoint_probe_count") + _summary_count(summary, "http_404_count")
    unique_count = max(_summary_count(summary, "unique_endpoint_hash_count"), _metadata_unique_count(items, "endpoint_hash"))
    if count < 5 and unique_count < 5:
        return ()
    return (
        _incident(
            level=2,
            severity="medium",
            confidence=min(0.92, 0.55 + max(count, unique_count) * 0.05),
            trigger="suspicious_endpoint_probing",
            affected_surfaces=_surfaces(items, fallback="http"),
            events=items,
            actions=("read_only_diagnostics", "redacted_debug_bundle", "operator_notification"),
        ),
    )


def _classify_telegram_abuse(events: tuple[dict[str, Any], ...], summary: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    items = tuple(event for event in events if _is_telegram_abuse(event))
    count = len(items) + _summary_count(summary, "telegram_abuse_count") + _summary_count(summary, "telegram_blocked_count")
    if count < 3:
        return ()
    return (
        _incident(
            level=2,
            severity="medium",
            confidence=min(0.92, 0.56 + count * 0.07),
            trigger="telegram_abuse_or_spam_pattern",
            affected_surfaces=("telegram",),
            events=items,
            actions=("read_only_diagnostics", "redacted_debug_bundle", "operator_notification"),
        ),
    )


def _classify_service_down(events: tuple[dict[str, Any], ...], summary: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    items = tuple(event for event in events if _is_service_down(event))
    count = len(items) + _summary_count(summary, "service_down_count")
    if count < 1:
        return ()
    severity = "high" if any(event.get("severity") in {"critical", "error"} for event in items) else "medium"
    return (
        _incident(
            level=3,
            severity=severity,
            confidence=min(0.94, 0.7 + count * 0.06),
            trigger="service_down_security_relevant",
            affected_surfaces=_surfaces(items, fallback="ops"),
            events=items,
            actions=("read_only_diagnostics", "redacted_debug_bundle", "operator_notification", "service_restart"),
        ),
    )


def _classify_secret_leak_indicators(events: tuple[dict[str, Any], ...], summary: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    items = tuple(event for event in events if _is_secret_leak_indicator(event))
    count = len(items) + _summary_count(summary, "secret_leak_indicator_count")
    if count < 1:
        return ()
    return (
        _incident(
            level=3,
            severity="high",
            confidence=min(0.97, 0.78 + count * 0.05),
            trigger="secret_leak_indicator_detected",
            affected_surfaces=_surfaces(items, fallback="security"),
            events=items,
            actions=("read_only_diagnostics", "redacted_debug_bundle", "operator_notification", "token_rotation_prepare"),
        ),
    )


def _incident(
    *,
    level: int,
    severity: str,
    confidence: float,
    trigger: str,
    affected_surfaces: Iterable[str],
    events: Iterable[Mapping[str, Any]],
    actions: Iterable[str],
) -> dict[str, Any]:
    event_tuple = tuple(events)
    action_objects = tuple(_action(action_type) for action_type in actions)
    correlation_ids = tuple(dict.fromkeys(str(event.get("correlation_id") or "") for event in event_tuple if event.get("correlation_id")))
    evidence_refs = tuple(dict.fromkeys(str(event.get("event_id") or "") for event in event_tuple if event.get("event_id")))
    if not correlation_ids and not evidence_refs:
        evidence_refs = (stable_payload_hash({"trigger": trigger, "count": len(event_tuple)}),)
    return build_security_incident(
        level=level,
        severity=severity,
        confidence=confidence,
        status="candidate",
        trigger=trigger,
        affected_surfaces=tuple(dict.fromkeys(affected_surfaces)),
        correlation_ids=correlation_ids,
        evidence_refs=evidence_refs,
        recommended_actions=action_objects,
    )


def _action(action_type: str) -> dict[str, Any]:
    summaries = {
        "read_only_diagnostics": "Collect bounded redacted diagnostics for this incident candidate.",
        "redacted_debug_bundle": "Prepare a redacted debug bundle for operator review.",
        "operator_notification": "Notify the operator with a compact incident summary.",
        "service_restart": "Prepare a gated service restart recommendation only.",
        "token_rotation_prepare": "Prepare a gated token rotation recommendation only.",
    }
    risks = {
        "service_restart": "May interrupt active users and requires explicit operator confirmation.",
        "token_rotation_prepare": "May invalidate integrations and requires explicit operator confirmation.",
    }
    return build_recommended_action(
        action_type=action_type,
        summary=summaries[action_type],
        risk=risks.get(action_type, "Read-only or notification-only action with redacted evidence."),
    )


def _safe_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise SecurityAnomalyClassifierError("event must be a mapping")
    if bool(event.get("raw_content_visible")):
        raise SecurityAnomalyClassifierError("raw event content is not allowed")
    schema = str(event.get("schema") or RUNTIME_EVENT_SCHEMA)
    if schema != RUNTIME_EVENT_SCHEMA:
        raise SecurityAnomalyClassifierError("unsupported event schema")
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str).lower()
    forbidden = (
        "authorization:",
        "bearer ",
        "api_key",
        "private_document_text",
        "private_email_body",
        "image_base64",
        "unredacted_tool_output",
    )
    if any(marker in encoded for marker in forbidden):
        raise SecurityAnomalyClassifierError("event contains forbidden raw marker")
    return dict(event)


def _safe_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        return {}
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str).lower()
    if any(marker in encoded for marker in ("authorization:", "bearer ", "api_key", "password=", "token=")):
        raise SecurityAnomalyClassifierError("summary contains forbidden raw marker")
    return dict(summary)


def _group(events: Iterable[dict[str, Any]], predicate, *, key_fields: tuple[str, ...]) -> dict[tuple[str, ...], tuple[dict[str, Any], ...]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if predicate(event):
            grouped[tuple(str(event.get(field) or "") for field in key_fields)].append(event)
    return {key: tuple(value) for key, value in grouped.items()}


def _is_auth_failure(event: Mapping[str, Any]) -> bool:
    haystack = _event_text(event)
    return event.get("status") in {"failed", "error", "blocked"} and any(
        marker in haystack for marker in ("auth", "login", "session", "permission_denied")
    )


def _is_endpoint_probe(event: Mapping[str, Any]) -> bool:
    haystack = _event_text(event)
    metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
    return any(marker in haystack for marker in ("endpoint_probe", "path_probe", "http_404", "not_found_probe")) or bool(
        metadata.get("endpoint_hash")
    )


def _is_telegram_abuse(event: Mapping[str, Any]) -> bool:
    if str(event.get("surface") or "") != "telegram":
        return False
    haystack = _event_text(event)
    return event.get("status") in {"blocked", "failed", "error", "warn"} or any(
        marker in haystack for marker in ("rate_limit", "abuse", "spam", "unauthorized")
    )


def _is_service_down(event: Mapping[str, Any]) -> bool:
    haystack = _event_text(event)
    return event.get("status") in {"failed", "error", "blocked"} and any(
        marker in haystack for marker in ("service_down", "health", "podman", "systemd", "heartbeat")
    )


def _is_secret_leak_indicator(event: Mapping[str, Any]) -> bool:
    haystack = _event_text(event)
    return any(marker in haystack for marker in ("secret_leak", "secret_detected", "credential_leak", "token_rotation"))


def _event_text(event: Mapping[str, Any]) -> str:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
    parts = (
        event.get("surface"),
        event.get("component"),
        event.get("event_type"),
        event.get("status"),
        event.get("severity"),
        event.get("error_class"),
        *(metadata.keys()),
        *(metadata.values()),
    )
    return " ".join(str(part or "").lower() for part in parts)


def _surfaces(events: Iterable[Mapping[str, Any]], *, fallback: str) -> tuple[str, ...]:
    surfaces = tuple(dict.fromkeys(str(event.get("surface") or "") for event in events if event.get("surface")))
    return surfaces or (fallback,)


def _count_events(events: Iterable[Mapping[str, Any]], predicate) -> int:
    return sum(1 for event in events if predicate(event))


def _summary_count(summary: Mapping[str, Any], key: str) -> int:
    try:
        return max(0, int(summary.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _metadata_unique_count(events: Iterable[Mapping[str, Any]], key: str) -> int:
    values = set()
    for event in events:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        value = metadata.get(key)
        if value:
            values.add(str(value))
    return len(values)

"""Classify defensive security anomalies from redacted runtime events.

The classifier consumes already-redacted event envelopes and observability
summaries. It never executes remediation, persists raw logs, or requires live
access.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from src.runtime_event_envelope import REQUIRED_EVENT_FIELDS, RUNTIME_EVENT_SCHEMA, stable_payload_hash
from src.security_incident_model import build_recommended_action, build_security_incident


SECURITY_ANOMALY_CLASSIFIER_SCHEMA = "odysseus.security_anomaly_classifier.v1"
CLASSIFIER_FAMILY = "deterministic_offline_rules"
CLASSIFIER_REVISION = "sirp03-r1"
_PRIVATE_PATH = re.compile(r"(?:[a-z]:[\\/]|/(?:home|users|var|mnt|srv|opt)/|~[\\/])", re.I)
_RAW_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")
_COMMAND_TEXT = re.compile(r"(?:^|\s)(?:rm|del|curl|wget|powershell|bash|cmd|python(?:3)?)\s", re.I)
_FORBIDDEN_KEY_PARTS = frozenset({"authorization", "token", "cookie", "header", "command", "target", "private", "credential", "secret"})
_DEBUG_SERVER_LEGACY_EVENT_FIELDS = frozenset({"schema", "event_id", "surface", "component", "event_type", "status", "severity", "correlation_id", "raw_content_visible"})


class SecurityAnomalyClassifierError(ValueError):
    """Raised when classifier input is unsafe."""


def classify_security_anomalies(
    events: Iterable[Mapping[str, Any]],
    *,
    observability_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return redacted incident candidates for known anomaly families."""

    # Canonical ordering prevents the caller's collection order from changing
    # candidate identifiers, action identifiers, or output ordering.
    normalized_events = tuple(sorted((_safe_event(event) for event in events), key=_event_sort_key))
    summary = _safe_summary(observability_summary or {})
    incidents: list[dict[str, Any]] = []
    incidents.extend(_classify_auth_failures(normalized_events, summary))
    incidents.extend(_classify_endpoint_probing(normalized_events, summary))
    incidents.extend(_classify_telegram_abuse(normalized_events, summary))
    incidents.extend(_classify_service_down(normalized_events, summary))
    incidents.extend(_classify_secret_leak_indicators(normalized_events, summary))
    return {
        "schema": SECURITY_ANOMALY_CLASSIFIER_SCHEMA,
        "classifier_family": CLASSIFIER_FAMILY,
        "classifier_revision": CLASSIFIER_REVISION,
        "status": "success",
        "event_count": len(normalized_events),
        "incident_count": len(incidents),
        "incidents": tuple(sorted(incidents, key=lambda incident: str(incident["incident_id"]))),
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
    correlation_ids = tuple(sorted({str(event.get("correlation_id") or "") for event in event_tuple if event.get("correlation_id")}))
    evidence_refs = tuple(sorted({str(event.get("event_id") or "") for event in event_tuple if event.get("event_id")}))
    if not correlation_ids and not evidence_refs:
        evidence_refs = (stable_payload_hash({"trigger": trigger, "count": len(event_tuple)}),)
    fingerprint = hashlib.sha256(json.dumps({"trigger": trigger, "correlation_ids": correlation_ids, "evidence_refs": evidence_refs}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    action_objects = tuple(_action(action_type, fingerprint=fingerprint) for action_type in actions)
    observation_time = _observation_time(event_tuple)
    return build_security_incident(
        incident_id=f"inc-{fingerprint[:24]}",
        level=level,
        severity=severity,
        confidence=confidence,
        status="candidate",
        trigger=trigger,
        affected_surfaces=tuple(dict.fromkeys(affected_surfaces)),
        correlation_ids=correlation_ids,
        evidence_refs=evidence_refs,
        recommended_actions=action_objects,
        created_at=observation_time,
        updated_at=observation_time,
    )


def _action(action_type: str, *, fingerprint: str) -> dict[str, Any]:
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
        action_id=f"act-{action_type[:32]}-{fingerprint[:12]}",
    )


def _safe_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise SecurityAnomalyClassifierError("event must be a mapping")
    if bool(event.get("raw_content_visible")):
        raise SecurityAnomalyClassifierError("raw event content is not allowed")
    schema = str(event.get("schema") or RUNTIME_EVENT_SCHEMA)
    if schema != RUNTIME_EVENT_SCHEMA:
        raise SecurityAnomalyClassifierError("unsupported event schema")
    if event.get("schema") == RUNTIME_EVENT_SCHEMA:
        keys = frozenset(event)
        is_full_runtime_envelope = all(field in event for field in REQUIRED_EVENT_FIELDS)
        if not is_full_runtime_envelope and keys != _DEBUG_SERVER_LEGACY_EVENT_FIELDS:
            raise SecurityAnomalyClassifierError("schema-tagged event is incomplete")
    _reject_private_classifier_input(event, allow_root_raw_content_flag=True)
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


def _event_sort_key(event: Mapping[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _observation_time(events: Iterable[Mapping[str, Any]]) -> str:
    values = sorted(str(event.get("ts") or "") for event in events)
    valid = [value for value in values if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?", value)]
    # A fixed epoch is an explicit "time unavailable" sentinel for legacy
    # envelopes rather than a fabricated classification-time observation.
    return valid[-1] if valid else "1970-01-01T00:00:00Z"


def _safe_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        return {}
    _reject_private_classifier_input(summary)
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str).lower()
    if any(marker in encoded for marker in ("authorization:", "bearer ", "api_key", "password=", "token=")):
        raise SecurityAnomalyClassifierError("summary contains forbidden raw marker")
    return dict(summary)


def _reject_private_classifier_input(value: Any, *, allow_root_raw_content_flag: bool = False, depth: int = 0) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            name = str(key).lower()
            parts = set(re.split(r"[^a-z0-9]+", name))
            if name != "raw_content_visible" and ("raw" in parts or bool(parts & _FORBIDDEN_KEY_PARTS) or ({"chat", "id"} <= parts)):
                raise SecurityAnomalyClassifierError("classifier input contains a forbidden field")
            if name == "raw_content_visible" and not (depth == 0 and allow_root_raw_content_flag and nested is False):
                raise SecurityAnomalyClassifierError("classifier input contains a forbidden field")
            _reject_private_classifier_input(nested, depth=depth + 1)
        return
    if isinstance(value, (tuple, list, set)):
        for nested in value:
            _reject_private_classifier_input(nested, depth=depth + 1)
        return
    if isinstance(value, str) and (_PRIVATE_PATH.search(value) or _RAW_IP.search(value) or _EMAIL.search(value) or _COMMAND_TEXT.search(value)):
        raise SecurityAnomalyClassifierError("classifier input contains private or executable content")


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

"""Read-only ops timeline model for health, observability and security."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Iterable, Mapping
from uuid import uuid4


OPS_TIMELINE_EVENT_SCHEMA = "odysseus.ops_timeline.event.v1"
OPS_TIMELINE_SCHEMA = "odysseus.ops_timeline.v1"

OPS_TIMELINE_STAGES = (
    "signal",
    "triage",
    "evidence",
    "decision",
    "action_plan",
    "operator_gate",
    "handoff",
)
OPS_TIMELINE_STATUSES = (
    "normal",
    "watch",
    "alert",
    "contain",
    "lockdown",
    "recovery",
    "blocked",
    "denied",
)
OPS_TIMELINE_SURFACES = (
    "system_health",
    "observability",
    "diagnostics",
    "security",
    "remediation",
    "manual",
)
OPS_TIMELINE_SEVERITIES = ("info", "warning", "error", "critical")

_STAGE_ORDER = {stage: index for index, stage in enumerate(OPS_TIMELINE_STAGES)}
_STATUS_RANK = {
    "normal": 0,
    "recovery": 1,
    "watch": 2,
    "alert": 3,
    "contain": 4,
    "blocked": 4,
    "lockdown": 5,
    "denied": 5,
}
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
_HOST_PATH_RE = re.compile(r"([A-Za-z]:\\|/(home|Users|var/lib|mnt|srv|opt)/|~[\\/])", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL_RE = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")
_FORBIDDEN_MARKERS = (
    "authorization",
    "bearer ",
    "api_key",
    "password",
    "cookie",
    "token=",
    "telegram_token",
    "chat_id",
    "private_document_text",
    "private_email_body",
    "raw_log",
    "raw_output",
    "unredacted_tool_output",
)


class OpsTimelineError(ValueError):
    """Raised when an ops timeline event would be unsafe or invalid."""


def build_ops_timeline_event(
    *,
    stage: str,
    status: str,
    surface: str,
    summary: str,
    severity: str = "info",
    event_id: str | None = None,
    created_at: str | None = None,
    evidence_refs: Iterable[Any] = (),
    correlation_ids: Iterable[Any] = (),
    required_gates: Iterable[Any] = (),
    action_refs: Iterable[Any] = (),
) -> dict[str, Any]:
    """Build one redacted timeline event without querying or mutating systems."""

    normalized_stage = _safe_enum(stage, OPS_TIMELINE_STAGES, field="stage")
    normalized_status = _safe_enum(status, OPS_TIMELINE_STATUSES, field="status")
    normalized_surface = _safe_enum(surface, OPS_TIMELINE_SURFACES, field="surface")
    normalized_severity = _safe_enum(severity, OPS_TIMELINE_SEVERITIES, field="severity")
    gate_refs = tuple(_safe_label(value, field="required_gate") for value in required_gates if str(value or "").strip())
    if normalized_status in {"contain", "lockdown"} and not gate_refs:
        raise OpsTimelineError("containment and lockdown events require an operator gate")
    if normalized_stage == "operator_gate" and not gate_refs:
        raise OpsTimelineError("operator_gate events require a gate reference")

    event = {
        "schema": OPS_TIMELINE_EVENT_SCHEMA,
        "event_id": _safe_id(event_id, prefix="ops_evt"),
        "created_at": _safe_optional_time(created_at) or _now_iso(),
        "stage": normalized_stage,
        "status": normalized_status,
        "surface": normalized_surface,
        "severity": normalized_severity,
        "summary": _safe_summary(summary),
        "evidence_refs": tuple(_safe_reference(value, prefix="evidence") for value in evidence_refs if str(value or "").strip()),
        "correlation_ids": tuple(
            _safe_reference(value, prefix="correlation") for value in correlation_ids if str(value or "").strip()
        ),
        "required_gates": gate_refs,
        "action_refs": tuple(_safe_label(value, field="action_ref") for value in action_refs if str(value or "").strip()),
        "raw_content_visible": False,
        "raw_logs_visible": False,
        "host_paths_visible": False,
        "writes_performed": False,
        "live_actions_performed": False,
        "host_commands_performed": False,
    }
    _reject_forbidden_payload(event)
    return event


def build_ops_timeline(
    events: Iterable[Mapping[str, Any]],
    *,
    timeline_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a sorted redacted timeline packet from ops timeline events."""

    normalized = tuple(sorted((_normalize_event(event) for event in events), key=_event_sort_key))
    status = _overall_status(normalized)
    gates = tuple(sorted({gate for event in normalized for gate in event["required_gates"]}))
    timeline = {
        "schema": OPS_TIMELINE_SCHEMA,
        "timeline_id": _safe_id(timeline_id, prefix="ops_timeline"),
        "generated_at": _safe_optional_time(generated_at) or _now_iso(),
        "status": status,
        "event_count": len(normalized),
        "events": normalized,
        "stages_present": tuple(stage for stage in OPS_TIMELINE_STAGES if any(event["stage"] == stage for event in normalized)),
        "surfaces_present": tuple(
            surface for surface in OPS_TIMELINE_SURFACES if any(event["surface"] == surface for event in normalized)
        ),
        "required_gates": gates,
        "source_contracts": (
            "odysseus.system_health_dashboard_summary.v1",
            "odysseus.observability_diagnostic_bridge.v1",
            "odysseus.observability_alert_routing.v1",
            "odysseus.security_incident.v1",
            "odysseus.security_response_policy.v1",
            "odysseus.security_remediation_plan.v1",
        ),
        "raw_content_visible": False,
        "raw_logs_visible": False,
        "host_paths_visible": False,
        "writes_performed": False,
        "live_actions_performed": False,
        "host_commands_performed": False,
    }
    _reject_forbidden_payload(timeline)
    return timeline


def _normalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise OpsTimelineError("event must be a mapping")
    if event.get("schema") != OPS_TIMELINE_EVENT_SCHEMA:
        raise OpsTimelineError("event has unsupported schema")
    if bool(event.get("raw_content_visible")) or bool(event.get("raw_logs_visible")) or bool(event.get("host_paths_visible")):
        raise OpsTimelineError("event contains visible raw/private data flags")
    if bool(event.get("writes_performed")) or bool(event.get("live_actions_performed")):
        raise OpsTimelineError("timeline events must be read-only")
    return build_ops_timeline_event(
        event_id=str(event.get("event_id") or "") or None,
        created_at=str(event.get("created_at") or "") or None,
        stage=str(event.get("stage") or ""),
        status=str(event.get("status") or ""),
        surface=str(event.get("surface") or ""),
        severity=str(event.get("severity") or "info"),
        summary=str(event.get("summary") or ""),
        evidence_refs=event.get("evidence_refs") or (),
        correlation_ids=event.get("correlation_ids") or (),
        required_gates=event.get("required_gates") or (),
        action_refs=event.get("action_refs") or (),
    )


def _event_sort_key(event: Mapping[str, Any]) -> tuple[str, int, str]:
    return (
        str(event.get("created_at") or ""),
        _STAGE_ORDER.get(str(event.get("stage") or ""), 999),
        str(event.get("event_id") or ""),
    )


def _overall_status(events: tuple[Mapping[str, Any], ...]) -> str:
    if not events:
        return "normal"
    return max((str(event.get("status") or "normal") for event in events), key=lambda value: _STATUS_RANK[value])


def _safe_enum(value: Any, allowed: tuple[str, ...], *, field: str) -> str:
    text = _safe_label(value, field=field)
    if text not in allowed:
        raise OpsTimelineError(f"unsupported {field}")
    return text


def _safe_id(value: Any, *, prefix: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = f"{prefix}-{uuid4().hex}"
    return _safe_label(text, field=f"{prefix}_id")


def _safe_summary(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise OpsTimelineError("summary must not be empty")
    if len(text) > 280:
        raise OpsTimelineError("summary is too long")
    _reject_forbidden_text(text, field="summary")
    if _IPV4_RE.search(text) or _EMAIL_RE.search(text):
        raise OpsTimelineError("summary contains a raw identifier")
    return text


def _safe_reference(value: Any, *, prefix: str) -> str:
    text = str(value or "").strip()
    _reject_forbidden_text(text, field=f"{prefix}_ref")
    if _HOST_PATH_RE.search(text) or _IPV4_RE.search(text) or _EMAIL_RE.search(text) or not _SAFE_LABEL_RE.fullmatch(text):
        return _hash_ref(text, prefix=prefix)
    return text


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    _reject_forbidden_text(text, field=field)
    if len(text) > 180:
        raise OpsTimelineError(f"{field} is too long")
    if not _SAFE_LABEL_RE.fullmatch(text):
        raise OpsTimelineError(f"{field} contains unsafe characters")
    return text


def _safe_optional_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?", text):
        raise OpsTimelineError("timestamp must be ISO-like")
    return text[:40]


def _hash_ref(value: Any, *, prefix: str) -> str:
    digest = hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{prefix}:sha256:{digest}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _reject_forbidden_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_forbidden_payload(nested)
        return
    if isinstance(value, (tuple, list, set)):
        for nested in value:
            _reject_forbidden_payload(nested)
        return
    if isinstance(value, str):
        _reject_forbidden_text(value, field="payload")


def _reject_forbidden_text(text: str, *, field: str) -> None:
    lowered = str(text or "").lower()
    if any(marker in lowered for marker in _FORBIDDEN_MARKERS):
        raise OpsTimelineError(f"{field} contains a forbidden marker")
    if field == "summary" and _HOST_PATH_RE.search(str(text or "")):
        raise OpsTimelineError(f"{field} contains a private host path")

"""Redacted security incident and recommended action models.

This module defines serializable incident objects for defensive diagnostics. It
does not execute remediation, query live systems, store private logs, or expose
raw identifiers.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Iterable, Mapping
from uuid import uuid4


SECURITY_INCIDENT_SCHEMA = "odysseus.security_incident.v1"
SECURITY_ACTION_SCHEMA = "odysseus.security_incident_action.v1"

INCIDENT_LEVELS: dict[int, str] = {
    0: "normal",
    1: "watch",
    2: "alert",
    3: "contain",
    4: "lockdown",
    5: "recovery",
}

INCIDENT_SEVERITIES = {"info", "low", "medium", "high", "critical"}
INCIDENT_STATUSES = {"candidate", "open", "acknowledged", "contained", "recovery", "closed", "dismissed"}
ACTION_STATUSES = {"proposed", "prepared", "approved", "denied", "executed", "expired", "blocked"}

AUTO_ALLOWED_ACTION_TYPES = {
    "read_only_diagnostics",
    "redacted_debug_bundle",
    "incident_candidate",
    "alert_deduplication",
    "operator_notification",
    "local_only_sensitive_analysis",
    "observe_only",
}

CONFIRMATION_REQUIRED_ACTION_TYPES = {
    "crowdsec_temp_block",
    "crowdsec_unblock",
    "firewall_rule_change",
    "reverse_proxy_rule_change",
    "service_restart",
    "scheduler_pause",
    "scheduler_retry",
    "raptorgraph_maintenance_restart",
    "nextcloud_import_retry",
    "token_rotation_prepare",
    "session_invalidate_prepare",
    "cloudflare_tunnel_change",
    "deploy_rollback",
    "log_level_increase",
}

NEVER_ALLOWED_ACTION_TYPES = {
    "hackback",
    "third_party_exploit",
    "secret_print",
    "external_upload_private_evidence",
    "destructive_cleanup_before_evidence",
    "arbitrary_shell",
    "broad_firewall_change_without_gate",
}

SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")
HOST_PATH_RE = re.compile(r"([A-Za-z]:\\|/(home|Users|var/lib|mnt|srv|opt)/|~[\\/])", re.IGNORECASE)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
EMAIL_RE = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")
SECRET_MARKERS = (
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
    "image_base64",
    "unredacted_tool_output",
)


class SecurityIncidentModelError(ValueError):
    """Raised when an incident/action object is unsafe or invalid."""


def build_recommended_action(
    *,
    action_type: str,
    summary: str,
    risk: str,
    requires_confirmation: bool | None = None,
    expires_at: str | None = None,
    policy_gate: str | None = None,
    status: str = "proposed",
    action_id: str | None = None,
) -> dict[str, Any]:
    """Build one redacted recommended action object."""

    normalized_type = _safe_label(action_type, field="action_type")
    if normalized_type in NEVER_ALLOWED_ACTION_TYPES:
        raise SecurityIncidentModelError("action type is never allowed")
    if normalized_type not in AUTO_ALLOWED_ACTION_TYPES | CONFIRMATION_REQUIRED_ACTION_TYPES:
        raise SecurityIncidentModelError("unsupported action type")

    confirmation = (
        normalized_type in CONFIRMATION_REQUIRED_ACTION_TYPES
        if requires_confirmation is None
        else bool(requires_confirmation)
    )
    if normalized_type in CONFIRMATION_REQUIRED_ACTION_TYPES and not confirmation:
        raise SecurityIncidentModelError("remediation action requires confirmation")

    normalized_status = _safe_label(status, field="action_status")
    if normalized_status not in ACTION_STATUSES:
        raise SecurityIncidentModelError("unsupported action status")

    action = {
        "schema": SECURITY_ACTION_SCHEMA,
        "action_id": _safe_id(action_id, prefix="act"),
        "type": normalized_type,
        "summary": _safe_summary(summary, field="summary"),
        "risk": _safe_summary(risk, field="risk"),
        "requires_confirmation": confirmation,
        "expires_at": _safe_optional_time(expires_at),
        "policy_gate": _safe_label(policy_gate or _default_policy_gate(normalized_type), field="policy_gate"),
        "status": normalized_status,
        "raw_content_visible": False,
    }
    _reject_forbidden_payload(action)
    return action


def build_security_incident(
    *,
    level: int,
    severity: str,
    confidence: float,
    status: str,
    trigger: str,
    affected_surfaces: Iterable[Any],
    correlation_ids: Iterable[Any] = (),
    evidence_refs: Iterable[Any] = (),
    recommended_actions: Iterable[Mapping[str, Any]] = (),
    incident_id: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Build one redacted security incident object."""

    parsed_level = _safe_level(level)
    normalized_severity = _safe_label(severity, field="severity")
    if normalized_severity not in INCIDENT_SEVERITIES:
        raise SecurityIncidentModelError("unsupported incident severity")

    normalized_status = _safe_label(status, field="incident_status")
    if normalized_status not in INCIDENT_STATUSES:
        raise SecurityIncidentModelError("unsupported incident status")

    actions = tuple(_safe_action(action) for action in recommended_actions)
    incident = {
        "schema": SECURITY_INCIDENT_SCHEMA,
        "incident_id": _safe_id(incident_id, prefix="inc"),
        "created_at": _safe_optional_time(created_at) or _now_iso(),
        "updated_at": _safe_optional_time(updated_at) or _now_iso(),
        "level": parsed_level,
        "level_name": INCIDENT_LEVELS[parsed_level],
        "severity": normalized_severity,
        "confidence": _safe_confidence(confidence),
        "status": normalized_status,
        "trigger": _safe_summary(trigger, field="trigger"),
        "affected_surfaces": tuple(_safe_label(value, field="affected_surface") for value in affected_surfaces),
        "correlation_ids": tuple(_safe_label(value, field="correlation_id") for value in correlation_ids),
        "evidence_refs": tuple(_safe_reference(value) for value in evidence_refs),
        "recommended_actions": actions,
        "recommended_action_count": len(actions),
        "raw_content_visible": False,
    }
    if not incident["affected_surfaces"]:
        raise SecurityIncidentModelError("incident requires at least one affected surface")
    if not incident["evidence_refs"] and not incident["correlation_ids"]:
        raise SecurityIncidentModelError("incident requires evidence_refs or correlation_ids")
    _reject_forbidden_payload(incident)
    return incident


def summarize_incident(incident: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact redacted projection for notifications or lists."""

    _reject_forbidden_payload(incident)
    return {
        "schema": SECURITY_INCIDENT_SCHEMA,
        "incident_id": _safe_id(incident.get("incident_id"), prefix="inc"),
        "level": _safe_level(incident.get("level")),
        "level_name": INCIDENT_LEVELS[_safe_level(incident.get("level"))],
        "severity": _safe_label(incident.get("severity"), field="severity"),
        "confidence": _safe_confidence(incident.get("confidence")),
        "status": _safe_label(incident.get("status"), field="status"),
        "affected_surfaces": tuple(
            _safe_label(value, field="affected_surface")
            for value in incident.get("affected_surfaces", ())
        ),
        "recommended_action_count": _safe_count(incident.get("recommended_action_count")),
        "requires_confirmation_count": sum(
            1
            for action in incident.get("recommended_actions", ())
            if isinstance(action, Mapping) and bool(action.get("requires_confirmation"))
        ),
        "raw_content_visible": False,
    }


def hash_sensitive_identifier(value: Any, *, prefix: str = "ref") -> str:
    label = _safe_label(prefix, field="hash_prefix") or "ref"
    digest = hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{label}:sha256:{digest}"


def _safe_action(action: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(action, Mapping):
        raise SecurityIncidentModelError("recommended action must be a mapping")
    if action.get("schema") != SECURITY_ACTION_SCHEMA:
        raise SecurityIncidentModelError("recommended action has unsupported schema")
    return build_recommended_action(
        action_type=str(action.get("type") or ""),
        summary=str(action.get("summary") or ""),
        risk=str(action.get("risk") or ""),
        requires_confirmation=bool(action.get("requires_confirmation")),
        expires_at=str(action.get("expires_at") or "") or None,
        policy_gate=str(action.get("policy_gate") or "") or None,
        status=str(action.get("status") or "proposed"),
        action_id=str(action.get("action_id") or "") or None,
    )


def _safe_level(value: Any) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        raise SecurityIncidentModelError("incident level must be an integer") from None
    if level not in INCIDENT_LEVELS:
        raise SecurityIncidentModelError("incident level must be between 0 and 5")
    return level


def _safe_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        raise SecurityIncidentModelError("confidence must be a number between 0 and 1") from None
    if confidence < 0 or confidence > 1:
        raise SecurityIncidentModelError("confidence must be between 0 and 1")
    return round(confidence, 3)


def _safe_id(value: Any, *, prefix: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = f"{prefix}-{uuid4().hex}"
    return _safe_label(text, field=f"{prefix}_id")


def _safe_reference(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if HOST_PATH_RE.search(text) or IPV4_RE.search(text) or EMAIL_RE.search(text):
        return hash_sensitive_identifier(text, prefix="evidence")
    return _safe_label(text, field="evidence_ref")


def _safe_summary(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SecurityIncidentModelError(f"{field} must not be empty")
    _reject_forbidden_text(text, field=field)
    if len(text) > 280:
        raise SecurityIncidentModelError(f"{field} is too long")
    return text


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    _reject_forbidden_text(text, field=field)
    if len(text) > 180:
        raise SecurityIncidentModelError(f"{field} is too long")
    if not SAFE_LABEL_RE.fullmatch(text):
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return text


def _safe_optional_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?", text):
        raise SecurityIncidentModelError("timestamp must be ISO-like")
    return text[:40]


def _safe_count(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), 1_000_000))
    except (TypeError, ValueError):
        return 0


def _default_policy_gate(action_type: str) -> str:
    if action_type in CONFIRMATION_REQUIRED_ACTION_TYPES:
        return f"{action_type}-operator-go"
    return "read-only-auto-allowed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _reject_forbidden_payload(value: Any, *, field: str = "") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_forbidden_text(str(key), field="field_name")
            _reject_forbidden_payload(nested, field=str(key))
        return
    if isinstance(value, (tuple, list, set)):
        for item in value:
            _reject_forbidden_payload(item, field=field)
        return
    if isinstance(value, str):
        _reject_forbidden_text(value, field=field or "value")


def _reject_forbidden_text(text: str, *, field: str) -> None:
    lowered = text.lower()
    if any(marker in lowered for marker in SECRET_MARKERS):
        raise SecurityIncidentModelError(f"{field} contains a forbidden marker")
    if HOST_PATH_RE.search(text):
        raise SecurityIncidentModelError(f"{field} contains a private host path")
    if field in {"summary", "risk", "trigger"} and (IPV4_RE.search(text) or EMAIL_RE.search(text)):
        raise SecurityIncidentModelError(f"{field} contains a raw identifier")

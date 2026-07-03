"""Prepare-only remediation action plans for security incidents.

The planner creates bounded, redacted action plans. It never executes CrowdSec,
firewall, scheduler, service, token, tunnel or deploy changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Iterable, Mapping

from src.security_incident_model import (
    CONFIRMATION_REQUIRED_ACTION_TYPES,
    SECURITY_ACTION_SCHEMA,
    summarize_incident,
)
from src.security_response_policy import SecurityResponsePolicyError, decide_action


SECURITY_REMEDIATION_PLAN_SCHEMA = "odysseus.security_remediation_plan.v1"
SECURITY_REMEDIATION_ACTION_PLAN_SCHEMA = "odysseus.security_remediation_action_plan.v1"

SUPPORTED_PREPARE_TYPES = frozenset({
    "crowdsec_temp_block",
    "crowdsec_unblock",
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
})

FORBIDDEN_MARKERS = (
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
HOST_PATH_RE = re.compile(r"([A-Za-z]:\\|/(home|Users|var/lib|mnt|srv|opt)/|~[\\/])", re.IGNORECASE)
SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,180}$")


class SecurityRemediationActionError(ValueError):
    """Raised when a remediation plan would be unsafe or unsupported."""


def prepare_remediation_plan(
    incident: Mapping[str, Any],
    *,
    approved_gates: Iterable[Any] = (),
    requested_action_ids: Iterable[Any] = (),
    incident_mode: bool = True,
    dsgvo_mode: bool = False,
) -> dict[str, Any]:
    """Prepare all requested remediation actions for one incident."""

    if not isinstance(incident, Mapping):
        raise SecurityRemediationActionError("incident must be a mapping")
    if bool(incident.get("raw_content_visible")):
        raise SecurityRemediationActionError("incident raw_content_visible must be false")
    _reject_forbidden_payload(incident)
    requested = {str(value or "").strip() for value in requested_action_ids if str(value or "").strip()}
    action_plans = []
    for action in incident.get("recommended_actions", ()):
        if not isinstance(action, Mapping):
            continue
        action_id = str(action.get("action_id") or "")
        if requested and action_id not in requested:
            continue
        if str(action.get("type") or "") not in SUPPORTED_PREPARE_TYPES:
            continue
        action_plans.append(
            prepare_remediation_action(
                action,
                incident=incident,
                approved_gates=approved_gates,
                incident_mode=incident_mode,
                dsgvo_mode=dsgvo_mode,
            )
        )
    plan = {
        "schema": SECURITY_REMEDIATION_PLAN_SCHEMA,
        "incident": summarize_incident(incident),
        "status": "prepared" if action_plans else "empty",
        "action_count": len(action_plans),
        "actions": tuple(action_plans),
        "allowed_to_execute": False,
        "writes_performed": False,
        "dry_run": True,
        "raw_content_visible": False,
    }
    _reject_forbidden_payload(plan)
    return plan


def prepare_remediation_action(
    action: Mapping[str, Any],
    *,
    incident: Mapping[str, Any] | None = None,
    approved_gates: Iterable[Any] = (),
    incident_level: int | None = None,
    incident_confidence: float | None = None,
    incident_mode: bool = True,
    dsgvo_mode: bool = False,
    duration_minutes: int | None = None,
    scope_ref: str | None = None,
) -> dict[str, Any]:
    """Prepare one redacted remediation action plan without executing it."""

    if not isinstance(action, Mapping):
        raise SecurityRemediationActionError("action must be a mapping")
    if action.get("schema") != SECURITY_ACTION_SCHEMA:
        raise SecurityRemediationActionError("action has unsupported schema")
    if bool(action.get("raw_content_visible")):
        raise SecurityRemediationActionError("action raw_content_visible must be false")
    _reject_forbidden_payload(action)

    action_type = _safe_label(action.get("type"), field="action_type")
    if action_type not in SUPPORTED_PREPARE_TYPES:
        raise SecurityRemediationActionError("unsupported remediation action type")

    incident_level = _incident_value(incident, "level", incident_level, default=0)
    incident_confidence = _incident_value(incident, "confidence", incident_confidence, default=0.0)
    try:
        policy = decide_action(
            action,
            approved_gates=approved_gates,
            incident_level=int(incident_level),
            incident_confidence=float(incident_confidence),
            incident_mode=incident_mode,
            dsgvo_mode=dsgvo_mode,
        )
    except SecurityResponsePolicyError as exc:
        raise SecurityRemediationActionError(str(exc)) from exc

    blocked = policy["decision"] in {"blocked", "denied"}
    status = "blocked" if blocked else "prepared"
    live_gate_required = action_type in CONFIRMATION_REQUIRED_ACTION_TYPES
    plan = {
        "schema": SECURITY_REMEDIATION_ACTION_PLAN_SCHEMA,
        "action_id": _safe_label(action.get("action_id"), field="action_id"),
        "action_type": action_type,
        "status": status,
        "summary": _safe_text(action.get("summary"), field="summary"),
        "risk": _safe_text(action.get("risk"), field="risk"),
        "policy_decision": _safe_label(policy.get("decision"), field="policy_decision"),
        "policy_reason": _safe_label(policy.get("reason"), field="policy_reason"),
        "policy_gate": _safe_label(action.get("policy_gate"), field="policy_gate"),
        "requires_operator_confirmation": bool(action.get("requires_confirmation")),
        "live_gate_required": live_gate_required,
        "approved_gate_present": str(action.get("policy_gate") or "") in _approved_gate_set(approved_gates),
        "bounded_scope_ref": _safe_optional_ref(scope_ref),
        "expires_at": _expires_at(duration_minutes, action_type=action_type),
        "operator_instruction": _operator_instruction(action),
        "allowed_to_execute": False,
        "writes_performed": False,
        "dry_run": True,
        "raw_content_visible": False,
    }
    _reject_forbidden_payload(plan)
    return plan


def remediation_readiness() -> dict[str, Any]:
    """Return a compact redacted readiness summary."""

    return {
        "schema": SECURITY_REMEDIATION_PLAN_SCHEMA,
        "status": "ready",
        "supported_prepare_types": tuple(sorted(SUPPORTED_PREPARE_TYPES)),
        "executes_live_actions": False,
        "requires_operator_gate_for_live_actions": True,
        "writes_performed": False,
        "raw_content_visible": False,
    }


def _operator_instruction(action: Mapping[str, Any]) -> str:
    action_id = _safe_label(action.get("action_id"), field="action_id")
    if bool(action.get("requires_confirmation")):
        return f"Review then approve or deny action_id {action_id}."
    return f"Review prepared read-only action_id {action_id}."


def _expires_at(duration_minutes: int | None, *, action_type: str) -> str:
    if duration_minutes is None:
        duration_minutes = 30 if action_type == "crowdsec_temp_block" else 60
    duration = max(1, min(int(duration_minutes), 24 * 60))
    return (datetime.now(timezone.utc) + timedelta(minutes=duration)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _incident_value(incident: Mapping[str, Any] | None, key: str, explicit: Any, *, default: Any) -> Any:
    if explicit is not None:
        return explicit
    if isinstance(incident, Mapping):
        return incident.get(key, default)
    return default


def _approved_gate_set(values: Iterable[Any]) -> set[str]:
    return {str(value or "").strip() for value in values if str(value or "").strip()}


def _safe_optional_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _safe_label(text, field="scope_ref")


def _safe_text(value: Any, *, field: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise SecurityRemediationActionError(f"{field} must not be empty")
    if len(text) > 280:
        text = text[:279].rstrip() + "..."
    _reject_forbidden_text(text, field=field)
    return text


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    _reject_forbidden_text(text, field=field)
    if len(text) > 180:
        raise SecurityRemediationActionError(f"{field} is too long")
    if not SAFE_LABEL_RE.fullmatch(text):
        raise SecurityRemediationActionError(f"{field} contains unsafe characters")
    return text


def _reject_forbidden_payload(value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).lower()
    if any(marker in encoded for marker in FORBIDDEN_MARKERS):
        raise SecurityRemediationActionError("payload contains a forbidden marker")
    if HOST_PATH_RE.search(encoded):
        raise SecurityRemediationActionError("payload contains a private host path")


def _reject_forbidden_text(text: str, *, field: str) -> None:
    lowered = text.lower()
    if any(marker in lowered for marker in FORBIDDEN_MARKERS):
        raise SecurityRemediationActionError(f"{field} contains a forbidden marker")
    if HOST_PATH_RE.search(text):
        raise SecurityRemediationActionError(f"{field} contains a private host path")

"""Policy engine for defensive security incident responses.

The policy engine decides what Odysseus may do next. It never executes live
remediation. It only classifies an incident or recommended action as observe,
diagnose, recommend, gated action, blocked or denied.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from src.security_incident_model import (
    AUTO_ALLOWED_ACTION_TYPES,
    CONFIRMATION_REQUIRED_ACTION_TYPES,
    INCIDENT_LEVELS,
    NEVER_ALLOWED_ACTION_TYPES,
    SECURITY_ACTION_SCHEMA,
    SECURITY_INCIDENT_SCHEMA,
    SecurityIncidentModelError,
    build_recommended_action,
    summarize_incident,
)


SECURITY_RESPONSE_POLICY_SCHEMA = "odysseus.security_response_policy.v1"

# This table is the SIRP action authority.  It describes availability only;
# it never grants execution permission.
ACTION_POLICY_DISPOSITIONS = {
    "operator_notification": ("delivery_prepare_only", "OPS-ALERT-DELIVERY-GO"),
    "crowdsec_temp_block": ("typed_executor", "crowdsec-remediation-go"),
    "crowdsec_unblock": ("typed_executor", "crowdsec-remediation-go"),
    "session_invalidate_prepare": ("typed_executor", "security-incident-session-invalidation-go"),
    "service_restart": ("manual_handoff", "OPS-REMEDIATION-GO"),
    "scheduler_pause": ("manual_handoff", "OPS-REMEDIATION-GO"),
    "scheduler_retry": ("manual_handoff", "OPS-REMEDIATION-GO"),
    "raptorgraph_maintenance_restart": ("manual_handoff", "OPS-REMEDIATION-GO"),
    "nextcloud_import_retry": ("manual_handoff", "nextcloud-local-only-extraction-go"),
    "token_rotation_prepare": ("manual_handoff", "security-incident-credential-rotation-go"),
    "cloudflare_tunnel_change": ("manual_handoff", "security-incident-cloudflare-change-go"),
    "deploy_rollback": ("manual_handoff", "deploy-live-go"),
    "log_level_increase": ("manual_handoff", "security-incident-log-privacy-go"),
    "firewall_change": ("never_allowed_in_SIRP", "none"),
    "external_upload_private_evidence": ("never_allowed_in_SIRP", "none"),
    "hackback_or_third_party_exploit": ("never_allowed_in_SIRP", "none"),
}

TYPED_EXECUTOR_ACTION_TYPES = frozenset(
    action_type for action_type, (disposition, _gate) in ACTION_POLICY_DISPOSITIONS.items()
    if disposition == "typed_executor"
)

POLICY_DECISIONS = {
    "observe",
    "diagnose",
    "recommend",
    "gated_action",
    "blocked",
    "denied",
}

MIN_CONFIDENCE_FOR_OPERATOR_ACTION = 0.65
MIN_CONFIDENCE_FOR_CONTAINMENT_RECOMMENDATION = 0.75

INCIDENT_LEVEL_DEFAULT_DECISION = {
    0: "observe",
    1: "observe",
    2: "diagnose",
    3: "recommend",
    4: "blocked",
    5: "diagnose",
}


class SecurityResponsePolicyError(ValueError):
    """Raised when policy input is unsafe or invalid."""


def decide_action(
    action: Mapping[str, Any],
    *,
    approved_gates: Iterable[str] = (),
    incident_level: int | None = None,
    incident_confidence: float | None = None,
    incident_mode: bool = False,
    dsgvo_mode: bool = False,
) -> dict[str, Any]:
    """Classify one recommended action without executing it."""

    if _is_canonical_never_allowed_action(action):
        return _decision(
            action=_minimal_never_allowed_action(action),
            decision="denied",
            reason="action_type_never_allowed",
            operator_gate_required=False,
            allowed_to_execute=False,
            incident_mode=incident_mode,
            dsgvo_mode=dsgvo_mode,
        )
    normalized = _normalize_action(action)
    action_type = str(normalized["type"])
    approved = _approved_gate_set(approved_gates)
    policy_gate = str(normalized.get("policy_gate") or "")
    level = _safe_level(incident_level if incident_level is not None else 0)
    confidence = _safe_confidence(incident_confidence if incident_confidence is not None else 0)

    if action_type in NEVER_ALLOWED_ACTION_TYPES:
        return _decision(
            action=normalized,
            decision="denied",
            reason="action_type_never_allowed",
            operator_gate_required=False,
            allowed_to_execute=False,
            incident_mode=incident_mode,
            dsgvo_mode=dsgvo_mode,
        )

    if _sensitive_external_action_blocked(action_type, incident_mode=incident_mode, dsgvo_mode=dsgvo_mode):
        return _decision(
            action=normalized,
            decision="blocked",
            reason="sensitive_external_action_blocked",
            operator_gate_required=True,
            allowed_to_execute=False,
            incident_mode=incident_mode,
            dsgvo_mode=dsgvo_mode,
        )

    if action_type == "operator_notification":
        normalized = dict(normalized)
        normalized["policy_gate"] = "OPS-ALERT-DELIVERY-GO"
        return _decision(
            action=normalized,
            decision="gated_action",
            reason="operator_delivery_gate_required_prepare_only",
            operator_gate_required=True,
            allowed_to_execute=False,
            incident_mode=incident_mode,
            dsgvo_mode=dsgvo_mode,
        )

    if action_type in AUTO_ALLOWED_ACTION_TYPES:
        return _decision(
            action=normalized,
            decision="diagnose" if action_type in {"read_only_diagnostics", "redacted_debug_bundle"} else "observe",
            reason="auto_allowed_read_only_or_notification",
            operator_gate_required=False,
            allowed_to_execute=True,
            incident_mode=incident_mode,
            dsgvo_mode=dsgvo_mode,
        )

    if action_type in CONFIRMATION_REQUIRED_ACTION_TYPES:
        if confidence < MIN_CONFIDENCE_FOR_OPERATOR_ACTION:
            return _decision(
                action=normalized,
                decision="blocked",
                reason="confidence_below_operator_action_threshold",
                operator_gate_required=True,
                allowed_to_execute=False,
                incident_mode=incident_mode,
                dsgvo_mode=dsgvo_mode,
            )
        if level < 2:
            return _decision(
                action=normalized,
                decision="blocked",
                reason="incident_level_too_low_for_remediation",
                operator_gate_required=True,
                allowed_to_execute=False,
                incident_mode=incident_mode,
                dsgvo_mode=dsgvo_mode,
            )
        if policy_gate not in approved:
            return _decision(
                action=normalized,
                decision="gated_action",
                reason="operator_gate_required",
                operator_gate_required=True,
                allowed_to_execute=False,
                incident_mode=incident_mode,
                dsgvo_mode=dsgvo_mode,
            )
        return _decision(
            action=normalized,
            decision="gated_action",
            reason="operator_gate_approved_prepare_only",
            operator_gate_required=True,
            allowed_to_execute=False,
            incident_mode=incident_mode,
            dsgvo_mode=dsgvo_mode,
        )

    return _decision(
        action=normalized,
        decision="denied",
        reason="unsupported_action_type",
        operator_gate_required=True,
        allowed_to_execute=False,
        incident_mode=incident_mode,
        dsgvo_mode=dsgvo_mode,
    )


def decide_incident_response(
    incident: Mapping[str, Any],
    *,
    approved_gates: Iterable[str] = (),
    incident_mode: bool = False,
    dsgvo_mode: bool = False,
) -> dict[str, Any]:
    """Classify an incident and all recommended actions."""

    _validate_incident(incident)
    level = _safe_level(incident.get("level"))
    confidence = _safe_confidence(incident.get("confidence"))
    actions = tuple(
        decide_action(
            action,
            approved_gates=approved_gates,
            incident_level=level,
            incident_confidence=confidence,
            incident_mode=incident_mode,
            dsgvo_mode=dsgvo_mode,
        )
        for action in incident.get("recommended_actions", ())
    )
    action_decisions = {str(action.get("decision") or "") for action in actions}
    base_decision = INCIDENT_LEVEL_DEFAULT_DECISION[level]
    if "denied" in action_decisions:
        decision = "blocked"
        reason = "one_or_more_actions_denied"
    elif "blocked" in action_decisions:
        decision = "blocked"
        reason = "one_or_more_actions_blocked"
    elif "gated_action" in action_decisions:
        decision = "gated_action"
        reason = "one_or_more_actions_need_operator_gate"
    elif level >= 3 and confidence >= MIN_CONFIDENCE_FOR_CONTAINMENT_RECOMMENDATION:
        decision = "recommend"
        reason = "containment_recommendation_threshold_met"
    else:
        decision = base_decision
        reason = f"level_{level}_{base_decision}"

    return {
        "schema": SECURITY_RESPONSE_POLICY_SCHEMA,
        "decision": _safe_decision(decision),
        "reason": reason,
        "incident": summarize_incident(incident),
        "action_results": actions,
        "approved_gates": tuple(sorted(_approved_gate_set(approved_gates))),
        "operator_gate_required": any(bool(action.get("operator_gate_required")) for action in actions),
        "allowed_to_execute": False,
        "incident_mode": bool(incident_mode),
        "dsgvo_mode": bool(dsgvo_mode),
        "raw_content_visible": False,
    }


def policy_readiness() -> dict[str, Any]:
    """Return a redacted readiness summary for diagnostics/MCP."""

    return {
        "schema": SECURITY_RESPONSE_POLICY_SCHEMA,
        "status": "ready",
        "decisions": tuple(sorted(POLICY_DECISIONS)),
        "incident_levels": INCIDENT_LEVELS,
        "auto_allowed_action_types": tuple(sorted(AUTO_ALLOWED_ACTION_TYPES)),
        "confirmation_required_action_types": tuple(sorted(CONFIRMATION_REQUIRED_ACTION_TYPES)),
        "never_allowed_action_types": tuple(sorted(NEVER_ALLOWED_ACTION_TYPES)),
        "action_policy_dispositions": tuple(
            {"action_type": action_type, "disposition": disposition, "gate": gate}
            for action_type, (disposition, gate) in sorted(ACTION_POLICY_DISPOSITIONS.items())
        ),
        "operator_action_min_confidence": MIN_CONFIDENCE_FOR_OPERATOR_ACTION,
        "containment_recommendation_min_confidence": MIN_CONFIDENCE_FOR_CONTAINMENT_RECOMMENDATION,
        "executes_live_actions": False,
        "raw_content_visible": False,
    }


def action_policy_disposition(action_type: Any) -> tuple[str, str]:
    """Return the canonical SIRP disposition and named gate for one action."""

    normalized = str(action_type or "").strip()
    if normalized in ACTION_POLICY_DISPOSITIONS:
        return ACTION_POLICY_DISPOSITIONS[normalized]
    if normalized in AUTO_ALLOWED_ACTION_TYPES:
        return "read_only_allowed", "none"
    if normalized in NEVER_ALLOWED_ACTION_TYPES:
        return "never_allowed_in_SIRP", "none"
    return "never_allowed_in_SIRP", "none"


def typed_executor_action_types() -> frozenset[str]:
    """Return the closed set which a future typed executor may dispatch.

    This is an availability classification only; it never grants execution.
    """

    return TYPED_EXECUTOR_ACTION_TYPES


def _normalize_action(action: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(action, Mapping):
        raise SecurityResponsePolicyError("action must be a mapping")
    if action.get("schema") != SECURITY_ACTION_SCHEMA:
        raise SecurityResponsePolicyError("action has unsupported schema")
    try:
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
    except SecurityIncidentModelError as exc:
        raise SecurityResponsePolicyError(str(exc)) from exc


def _is_canonical_never_allowed_action(action: Any) -> bool:
    return isinstance(action, Mapping) and str(action.get("type") or "") in {
        "firewall_change", "external_upload_private_evidence", "hackback_or_third_party_exploit",
    }


def _minimal_never_allowed_action(action: Mapping[str, Any]) -> dict[str, Any]:
    if action.get("schema") != SECURITY_ACTION_SCHEMA or action.get("raw_content_visible") is not False:
        raise SecurityResponsePolicyError("never allowed action has invalid schema")
    _reject_unsafe_never_allowed_action(action)
    action_id = str(action.get("action_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,180}", action_id):
        raise SecurityResponsePolicyError("never allowed action has invalid identifier")
    return {"action_id": action_id, "type": str(action["type"]), "policy_gate": "none"}


def _reject_unsafe_never_allowed_action(value: Any, *, field: str = "") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            name = str(key).lower()
            if name != "raw_content_visible" and ("raw" in name or any(marker in name for marker in ("token", "cookie", "authorization", "private", "chat_id", "command"))):
                raise SecurityResponsePolicyError("never allowed action contains forbidden content")
            _reject_unsafe_never_allowed_action(nested, field=name)
        return
    if isinstance(value, (tuple, list, set)):
        for nested in value:
            _reject_unsafe_never_allowed_action(nested, field=field)
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in ("authorization:", "bearer ", "api_key", "password=", "cookie:", "token=", "chat_id", "private_document", "private_email", "raw_output")) or re.search(r"(?:[a-z]:[\\/]|/(?:home|users|var|mnt|srv|opt)/|~[\\/]|\b(?:\d{1,3}\.){3}\d{1,3}\b)", value, re.I):
            raise SecurityResponsePolicyError("never allowed action contains forbidden content")


def _validate_incident(incident: Mapping[str, Any]) -> None:
    if not isinstance(incident, Mapping):
        raise SecurityResponsePolicyError("incident must be a mapping")
    if incident.get("schema") != SECURITY_INCIDENT_SCHEMA:
        raise SecurityResponsePolicyError("incident has unsupported schema")
    if bool(incident.get("raw_content_visible")):
        raise SecurityResponsePolicyError("incident raw_content_visible must be false")
    _safe_level(incident.get("level"))
    _safe_confidence(incident.get("confidence"))


def _decision(
    *,
    action: Mapping[str, Any],
    decision: str,
    reason: str,
    operator_gate_required: bool,
    allowed_to_execute: bool,
    incident_mode: bool,
    dsgvo_mode: bool,
) -> dict[str, Any]:
    return {
        "schema": SECURITY_RESPONSE_POLICY_SCHEMA,
        "action_id": str(action.get("action_id") or ""),
        "action_type": str(action.get("type") or ""),
        "decision": _safe_decision(decision),
        "reason": reason,
        "policy_gate": str(action.get("policy_gate") or ""),
        "operator_gate_required": bool(operator_gate_required),
        "allowed_to_execute": bool(allowed_to_execute),
        "incident_mode": bool(incident_mode),
        "dsgvo_mode": bool(dsgvo_mode),
        "raw_content_visible": False,
    }


def _sensitive_external_action_blocked(action_type: str, *, incident_mode: bool, dsgvo_mode: bool) -> bool:
    if not (incident_mode or dsgvo_mode):
        return False
    return action_type in {
        "external_upload_private_evidence",
        "log_level_increase",
        "cloudflare_tunnel_change",
    }


def _approved_gate_set(values: Iterable[Any]) -> set[str]:
    result: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text:
            result.add(text[:180])
    return result


def _safe_level(value: Any) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError):
        raise SecurityResponsePolicyError("incident level must be an integer") from None
    if level not in INCIDENT_LEVELS:
        raise SecurityResponsePolicyError("incident level must be between 0 and 5")
    return level


def _safe_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        raise SecurityResponsePolicyError("confidence must be a number between 0 and 1") from None
    if confidence < 0 or confidence > 1:
        raise SecurityResponsePolicyError("confidence must be between 0 and 1")
    return round(confidence, 3)


def _safe_decision(value: Any) -> str:
    text = str(value or "").strip()
    if text not in POLICY_DECISIONS:
        raise SecurityResponsePolicyError("unsupported policy decision")
    return text

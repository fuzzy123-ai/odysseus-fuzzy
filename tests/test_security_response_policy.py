import pytest

from src.security_incident_model import build_recommended_action, build_security_incident
from src.security_response_policy import (
    SECURITY_RESPONSE_POLICY_SCHEMA,
    SecurityResponsePolicyError,
    decide_action,
    decide_incident_response,
    policy_readiness,
)


def _incident(*, level=2, confidence=0.8, actions=()):
    return build_security_incident(
        level=level,
        severity="high" if level >= 2 else "medium",
        confidence=confidence,
        status="open",
        trigger="Repeated suspicious runtime events exceeded policy threshold.",
        affected_surfaces=["api"],
        correlation_ids=["corr-1"],
        evidence_refs=["runtime-event:evt-1"],
        recommended_actions=actions,
    )


def test_auto_allowed_action_can_run_readonly():
    action = build_recommended_action(
        action_type="read_only_diagnostics",
        summary="Read recent redacted diagnostic events.",
        risk="No mutation and no raw content.",
    )

    decision = decide_action(action, incident_level=2, incident_confidence=0.4)

    assert decision["schema"] == SECURITY_RESPONSE_POLICY_SCHEMA
    assert decision["decision"] == "diagnose"
    assert decision["reason"] == "auto_allowed_read_only_or_notification"
    assert decision["operator_gate_required"] is False
    assert decision["allowed_to_execute"] is True
    assert decision["raw_content_visible"] is False


def test_remediation_action_requires_gate_and_never_executes_from_policy():
    action = build_recommended_action(
        action_type="service_restart",
        summary="Prepare a service restart request.",
        risk="Brief service interruption.",
    )

    blocked = decide_action(action, incident_level=2, incident_confidence=0.9)
    approved = decide_action(
        action,
        incident_level=2,
        incident_confidence=0.9,
        approved_gates=[action["policy_gate"]],
    )

    assert blocked["decision"] == "gated_action"
    assert blocked["reason"] == "operator_gate_required"
    assert blocked["allowed_to_execute"] is False
    assert approved["decision"] == "gated_action"
    assert approved["reason"] == "operator_gate_approved_prepare_only"
    assert approved["allowed_to_execute"] is False


def test_remediation_blocks_when_confidence_or_level_too_low():
    action = build_recommended_action(
        action_type="crowdsec_temp_block",
        summary="Prepare a temporary block request.",
        risk="False positive could block a legitimate user.",
    )

    low_confidence = decide_action(action, incident_level=3, incident_confidence=0.2)
    low_level = decide_action(action, incident_level=1, incident_confidence=0.9)

    assert low_confidence["decision"] == "blocked"
    assert low_confidence["reason"] == "confidence_below_operator_action_threshold"
    assert low_level["decision"] == "blocked"
    assert low_level["reason"] == "incident_level_too_low_for_remediation"


def test_never_allowed_or_unsupported_actions_are_denied():
    with pytest.raises(Exception):
        build_recommended_action(
            action_type="hackback",
            summary="Not allowed.",
            risk="Unsafe.",
        )

    unsupported = {
        "schema": "odysseus.security_incident_action.v1",
        "action_id": "act-custom",
        "type": "unknown_custom_action",
        "summary": "Unknown action.",
        "risk": "Unknown risk.",
        "requires_confirmation": True,
        "policy_gate": "unknown-gate",
        "status": "proposed",
        "raw_content_visible": False,
    }
    with pytest.raises(SecurityResponsePolicyError, match="unsupported action type"):
        decide_action(unsupported, incident_level=2, incident_confidence=0.9)


def test_incident_response_aggregates_actions_and_gates():
    action = build_recommended_action(
        action_type="service_restart",
        summary="Prepare service restart request.",
        risk="Brief service interruption.",
    )
    incident = _incident(level=3, confidence=0.85, actions=[action])

    result = decide_incident_response(incident)

    assert result["decision"] == "gated_action"
    assert result["reason"] == "one_or_more_actions_need_operator_gate"
    assert result["operator_gate_required"] is True
    assert result["allowed_to_execute"] is False
    assert result["incident"]["level_name"] == "contain"
    assert len(result["action_results"]) == 1


def test_incident_response_recommends_containment_without_remediation_action():
    incident = _incident(level=3, confidence=0.81, actions=[])

    result = decide_incident_response(incident)

    assert result["decision"] == "recommend"
    assert result["reason"] == "containment_recommendation_threshold_met"
    assert result["operator_gate_required"] is False
    assert result["allowed_to_execute"] is False


def test_incident_and_dsgvo_mode_block_sensitive_external_action():
    action = build_recommended_action(
        action_type="log_level_increase",
        summary="Prepare a temporary log level increase.",
        risk="Higher log level may capture private metadata.",
    )

    result = decide_action(
        action,
        incident_level=4,
        incident_confidence=0.9,
        approved_gates=[action["policy_gate"]],
        incident_mode=True,
        dsgvo_mode=True,
    )

    assert result["decision"] == "blocked"
    assert result["reason"] == "sensitive_external_action_blocked"
    assert result["incident_mode"] is True
    assert result["dsgvo_mode"] is True


def test_policy_rejects_raw_content_incident_and_reports_readiness():
    incident = dict(_incident())
    incident["raw_content_visible"] = True

    with pytest.raises(SecurityResponsePolicyError, match="raw_content_visible"):
        decide_incident_response(incident)

    readiness = policy_readiness()
    assert readiness["status"] == "ready"
    assert readiness["executes_live_actions"] is False
    assert "read_only_diagnostics" in readiness["auto_allowed_action_types"]
    assert "service_restart" in readiness["confirmation_required_action_types"]
    assert "hackback" in readiness["never_allowed_action_types"]
    assert readiness["raw_content_visible"] is False

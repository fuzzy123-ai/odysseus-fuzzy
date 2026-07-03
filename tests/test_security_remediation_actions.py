from __future__ import annotations

import pytest

from src.security_incident_model import build_recommended_action, build_security_incident
from src.security_remediation_actions import (
    SecurityRemediationActionError,
    prepare_remediation_action,
    prepare_remediation_plan,
    remediation_readiness,
)


def _action(action_type: str = "service_restart", *, action_id: str = "act-restart"):
    return build_recommended_action(
        action_type=action_type,
        summary=f"Prepare {action_type} safely.",
        risk="Requires explicit operator confirmation.",
        action_id=action_id,
    )


def _incident(*, level: int = 3, confidence: float = 0.9):
    return build_security_incident(
        level=level,
        severity="high",
        confidence=confidence,
        status="open",
        trigger="Service down event exceeded policy threshold.",
        affected_surfaces=("ops",),
        correlation_ids=("corr-remediation-1",),
        evidence_refs=("evt-remediation-1",),
        recommended_actions=(
            _action("redacted_debug_bundle", action_id="act-debug"),
            _action("service_restart", action_id="act-restart"),
            _action("crowdsec_temp_block", action_id="act-crowdsec"),
        ),
        incident_id="inc-remediation",
    )


def test_prepare_service_restart_remains_prepare_only_even_with_gate():
    action = _action("service_restart", action_id="act-restart")

    plan = prepare_remediation_action(
        action,
        incident_level=3,
        incident_confidence=0.9,
        approved_gates=[action["policy_gate"]],
    )

    assert plan["schema"] == "odysseus.security_remediation_action_plan.v1"
    assert plan["status"] == "prepared"
    assert plan["policy_reason"] == "operator_gate_approved_prepare_only"
    assert plan["approved_gate_present"] is True
    assert plan["allowed_to_execute"] is False
    assert plan["writes_performed"] is False
    assert plan["dry_run"] is True


def test_prepare_crowdsec_temp_block_blocks_low_confidence():
    action = _action("crowdsec_temp_block", action_id="act-crowdsec")

    plan = prepare_remediation_action(action, incident_level=3, incident_confidence=0.2)

    assert plan["status"] == "blocked"
    assert plan["policy_reason"] == "confidence_below_operator_action_threshold"
    assert plan["allowed_to_execute"] is False


def test_prepare_remediation_plan_filters_to_supported_remediation_actions():
    plan = prepare_remediation_plan(_incident(), requested_action_ids=["act-restart", "act-crowdsec"])

    assert plan["schema"] == "odysseus.security_remediation_plan.v1"
    assert plan["status"] == "prepared"
    assert plan["action_count"] == 2
    assert {action["action_id"] for action in plan["actions"]} == {"act-restart", "act-crowdsec"}
    assert plan["writes_performed"] is False
    assert all(action["raw_content_visible"] is False for action in plan["actions"])


def test_rejects_raw_action_and_private_scope_ref():
    action = dict(_action("service_restart"))
    action["raw_content_visible"] = True

    with pytest.raises(SecurityRemediationActionError):
        prepare_remediation_action(action, incident_level=3, incident_confidence=0.9)

    with pytest.raises(SecurityRemediationActionError):
        prepare_remediation_action(
            _action("service_restart"),
            incident_level=3,
            incident_confidence=0.9,
            scope_ref=r"C:\Users\nkatz\private.txt",
        )


def test_remediation_readiness_never_claims_execution():
    readiness = remediation_readiness()

    assert readiness["status"] == "ready"
    assert "crowdsec_temp_block" in readiness["supported_prepare_types"]
    assert readiness["executes_live_actions"] is False
    assert readiness["requires_operator_gate_for_live_actions"] is True
    assert readiness["writes_performed"] is False

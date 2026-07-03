import pytest

from src.security_incident_model import (
    INCIDENT_LEVELS,
    SECURITY_ACTION_SCHEMA,
    SECURITY_INCIDENT_SCHEMA,
    SecurityIncidentModelError,
    build_recommended_action,
    build_security_incident,
    hash_sensitive_identifier,
    summarize_incident,
)


def test_build_recommended_action_defaults_confirmation_policy():
    action = build_recommended_action(
        action_type="service_restart",
        summary="Prepare a restart request for the Odysseus service.",
        risk="Brief service interruption and active sessions may be affected.",
    )

    assert action["schema"] == SECURITY_ACTION_SCHEMA
    assert action["type"] == "service_restart"
    assert action["requires_confirmation"] is True
    assert action["policy_gate"] == "service_restart-operator-go"
    assert action["status"] == "proposed"
    assert action["raw_content_visible"] is False


def test_build_recommended_action_auto_allowed_readonly():
    action = build_recommended_action(
        action_type="read_only_diagnostics",
        summary="Trace recent redacted MCP diagnostics.",
        risk="No mutation; diagnostic summary only.",
    )

    assert action["requires_confirmation"] is False
    assert action["policy_gate"] == "read-only-auto-allowed"


def test_action_rejects_never_allowed_and_unconfirmed_remediation():
    with pytest.raises(SecurityIncidentModelError, match="never allowed"):
        build_recommended_action(
            action_type="hackback",
            summary="Do not do this.",
            risk="Illegal and unsafe.",
        )

    with pytest.raises(SecurityIncidentModelError, match="requires confirmation"):
        build_recommended_action(
            action_type="crowdsec_temp_block",
            summary="Temporarily block the redacted source.",
            risk="A false positive could block a legitimate user.",
            requires_confirmation=False,
        )


def test_build_security_incident_serializes_required_fields():
    action = build_recommended_action(
        action_type="redacted_debug_bundle",
        summary="Create a redacted debug bundle for the failed reminder flow.",
        risk="No private content included.",
    )
    incident = build_security_incident(
        incident_id="inc-test-1",
        level=2,
        severity="high",
        confidence=0.82,
        status="open",
        trigger="Repeated Telegram delivery failures with security relevance.",
        affected_surfaces=["telegram", "scheduler"],
        correlation_ids=["corr-1"],
        evidence_refs=["runtime-event:evt-1"],
        recommended_actions=[action],
    )

    assert incident["schema"] == SECURITY_INCIDENT_SCHEMA
    assert incident["incident_id"] == "inc-test-1"
    assert incident["level"] == 2
    assert incident["level_name"] == INCIDENT_LEVELS[2]
    assert incident["severity"] == "high"
    assert incident["confidence"] == 0.82
    assert incident["status"] == "open"
    assert incident["affected_surfaces"] == ("telegram", "scheduler")
    assert incident["recommended_action_count"] == 1
    assert incident["raw_content_visible"] is False


def test_incident_rejects_invalid_level_confidence_and_missing_evidence():
    with pytest.raises(SecurityIncidentModelError, match="between 0 and 5"):
        build_security_incident(
            level=8,
            severity="high",
            confidence=0.5,
            status="open",
            trigger="Synthetic trigger.",
            affected_surfaces=["api"],
            evidence_refs=["evt-1"],
        )

    with pytest.raises(SecurityIncidentModelError, match="between 0 and 1"):
        build_security_incident(
            level=2,
            severity="high",
            confidence=1.5,
            status="open",
            trigger="Synthetic trigger.",
            affected_surfaces=["api"],
            evidence_refs=["evt-1"],
        )

    with pytest.raises(SecurityIncidentModelError, match="evidence_refs or correlation_ids"):
        build_security_incident(
            level=2,
            severity="high",
            confidence=0.5,
            status="open",
            trigger="Synthetic trigger.",
            affected_surfaces=["api"],
        )


def test_incident_redacts_sensitive_evidence_refs_but_rejects_raw_user_summary():
    incident = build_security_incident(
        level=1,
        severity="medium",
        confidence=0.61,
        status="candidate",
        trigger="Endpoint probing exceeded the watch threshold.",
        affected_surfaces=["reverse_proxy"],
        evidence_refs=["192.0.2.9"],
    )

    assert incident["evidence_refs"][0].startswith("evidence:sha256:")
    assert "192.0.2.9" not in incident["evidence_refs"][0]

    with pytest.raises(SecurityIncidentModelError, match="raw identifier"):
        build_security_incident(
            level=2,
            severity="high",
            confidence=0.8,
            status="open",
            trigger="Suspicious request from 192.0.2.9",
            affected_surfaces=["api"],
            evidence_refs=["evt-1"],
        )


def test_incident_rejects_secret_markers_and_host_paths():
    with pytest.raises(SecurityIncidentModelError, match="forbidden marker"):
        build_recommended_action(
            action_type="operator_notification",
            summary="Authorization: Bearer abc leaked.",
            risk="Secret exposure.",
        )

    with pytest.raises(SecurityIncidentModelError, match="private host path"):
        build_security_incident(
            level=2,
            severity="high",
            confidence=0.8,
            status="open",
            trigger=r"Unexpected access to C:\Users\nkatz\secret.txt",
            affected_surfaces=["api"],
            evidence_refs=["evt-1"],
        )


def test_summarize_incident_excludes_evidence_and_action_details():
    action = build_recommended_action(
        action_type="token_rotation_prepare",
        summary="Prepare token rotation request.",
        risk="Operator must coordinate clients.",
    )
    incident = build_security_incident(
        level=4,
        severity="critical",
        confidence=0.9,
        status="open",
        trigger="Secret leak indicator matched a redacted event.",
        affected_surfaces=["provider"],
        correlation_ids=["corr-secret-1"],
        evidence_refs=[hash_sensitive_identifier("secret-source", prefix="evidence")],
        recommended_actions=[action],
    )

    summary = summarize_incident(incident)

    assert summary["incident_id"] == incident["incident_id"]
    assert summary["level_name"] == "lockdown"
    assert summary["recommended_action_count"] == 1
    assert summary["requires_confirmation_count"] == 1
    assert "evidence_refs" not in summary
    assert "recommended_actions" not in summary
    assert summary["raw_content_visible"] is False

import json

import pytest

from src.ops_timeline import (
    OPS_TIMELINE_EVENT_SCHEMA,
    OPS_TIMELINE_SCHEMA,
    OpsTimelineError,
    build_ops_timeline,
    build_ops_timeline_event,
)


def test_builds_sorted_readonly_timeline_with_overall_status_and_gates():
    later = build_ops_timeline_event(
        event_id="evt-decision",
        created_at="2026-07-06T09:05:00Z",
        stage="decision",
        status="alert",
        surface="security",
        severity="error",
        summary="Security policy recommends operator review.",
        evidence_refs=("incident:inc-1",),
    )
    earlier = build_ops_timeline_event(
        event_id="evt-signal",
        created_at="2026-07-06T09:00:00Z",
        stage="signal",
        status="watch",
        surface="system_health",
        severity="warning",
        summary="System health dashboard entered watch state.",
        correlation_ids=("runtime-event:evt-1",),
    )
    gated = build_ops_timeline_event(
        event_id="evt-gate",
        created_at="2026-07-06T09:06:00Z",
        stage="operator_gate",
        status="contain",
        surface="remediation",
        severity="critical",
        summary="Prepare-only containment action awaits operator approval.",
        required_gates=("OPS-REMEDIATION-GO",),
        action_refs=("act-restart",),
    )

    timeline = build_ops_timeline((later, earlier, gated), timeline_id="ops-test", generated_at="2026-07-06T09:07:00Z")

    assert timeline["schema"] == OPS_TIMELINE_SCHEMA
    assert timeline["status"] == "contain"
    assert [event["event_id"] for event in timeline["events"]] == ["evt-signal", "evt-decision", "evt-gate"]
    assert timeline["stages_present"] == ("signal", "decision", "operator_gate")
    assert timeline["required_gates"] == ("OPS-REMEDIATION-GO",)
    assert timeline["raw_content_visible"] is False
    assert timeline["raw_logs_visible"] is False
    assert timeline["host_commands_performed"] is False
    assert timeline["writes_performed"] is False
    assert timeline["live_actions_performed"] is False


def test_event_hashes_sensitive_references_without_leaking_raw_values():
    event = build_ops_timeline_event(
        stage="evidence",
        status="watch",
        surface="observability",
        summary="Redacted diagnostic evidence was attached.",
        evidence_refs=(r"C:\Users\nkatz\private.log", "192.0.2.9", "ops@example.invalid"),
        correlation_ids=("corr-safe-1",),
    )
    encoded = json.dumps(event, sort_keys=True)

    assert event["schema"] == OPS_TIMELINE_EVENT_SCHEMA
    assert all(ref.startswith("evidence:sha256:") for ref in event["evidence_refs"])
    assert "private.log" not in encoded
    assert "192.0.2.9" not in encoded
    assert "ops@example.invalid" not in encoded
    assert event["correlation_ids"] == ("corr-safe-1",)


def test_event_rejects_forbidden_summary_markers_and_host_paths():
    with pytest.raises(OpsTimelineError, match="forbidden marker"):
        build_ops_timeline_event(
            stage="signal",
            status="alert",
            surface="security",
            summary="Authorization bearer token leaked in raw_output.",
        )

    with pytest.raises(OpsTimelineError, match="private host path"):
        build_ops_timeline_event(
            stage="signal",
            status="alert",
            surface="security",
            summary=r"Suspicious access to C:\Users\nkatz\secret.txt",
        )


def test_containment_and_operator_gate_require_explicit_gate_references():
    with pytest.raises(OpsTimelineError, match="require an operator gate"):
        build_ops_timeline_event(
            stage="action_plan",
            status="contain",
            surface="remediation",
            severity="critical",
            summary="Containment was proposed without a gate.",
        )

    with pytest.raises(OpsTimelineError, match="operator_gate events require"):
        build_ops_timeline_event(
            stage="operator_gate",
            status="blocked",
            surface="remediation",
            severity="warning",
            summary="Operator gate is missing its reference.",
        )


def test_timeline_rejects_events_with_live_or_raw_flags():
    event = build_ops_timeline_event(
        stage="signal",
        status="normal",
        surface="manual",
        summary="Operator added a safe manual signal.",
    )
    dirty = dict(event)
    dirty["live_actions_performed"] = True

    with pytest.raises(OpsTimelineError, match="read-only"):
        build_ops_timeline((dirty,))

from src.orchestration_activation_audit_trail import ActivationAuditEvent, ActivationAuditTrail
from src.orchestration_activation_handoff_checklist import build_handoff_checklist_report
from src.orchestration_operator_activation_packet import build_operator_activation_packet
from src.orchestration_activation_readiness_index import (
    ActivationReadinessIndexStatus,
    build_activation_readiness_index,
)


def _trail(*event_types: str) -> ActivationAuditTrail:
    events = []
    for index, event_type in enumerate(event_types, start=1):
        events.append(
            ActivationAuditEvent.create(
                event_id=f"evt-{index}",
                event_type=event_type,
                run_id="run-1",
                slice_id="auto20b",
                actor="charlie",
                timestamp=f"2026-06-17T10:0{index}:00Z",
                decision="prepare_dispatch",
                reason=f"{event_type} recorded",
            )
        )
    return ActivationAuditTrail.create(events)


def test_not_started_without_inputs():
    index = build_activation_readiness_index()

    assert index.overall_status == ActivationReadinessIndexStatus.NOT_STARTED


def test_ready_when_foundation_and_evidence_are_present_but_runtime_is_known_limited():
    checklist = build_handoff_checklist_report(
        commit_present=True,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )
    audit = _trail("activation_requested", "preflight_checked")
    packet = build_operator_activation_packet(audit_trail=audit, checklist=checklist)

    index = build_activation_readiness_index(packet=packet, checklist=checklist, audit_trail=audit)

    assert index.overall_status == ActivationReadinessIndexStatus.READY


def test_blocked_when_checklist_or_audit_is_blocked():
    blocked_checklist = build_handoff_checklist_report(
        commit_present=False,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )
    blocked_audit = _trail("gate_blocked")
    packet = build_operator_activation_packet(audit_trail=blocked_audit, checklist=blocked_checklist)

    index = build_activation_readiness_index(packet=packet, checklist=blocked_checklist, audit_trail=blocked_audit)

    assert index.overall_status == ActivationReadinessIndexStatus.BLOCKED


def test_review_required_for_needs_review_or_deferred_states():
    checklist = build_handoff_checklist_report(
        commit_present=None,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )
    audit = _trail("activation_deferred")
    packet = build_operator_activation_packet(audit_trail=audit, checklist=checklist)

    index = build_activation_readiness_index(packet=packet, checklist=checklist, audit_trail=audit)

    assert index.overall_status == ActivationReadinessIndexStatus.REVIEW_REQUIRED


def test_to_dict_is_stable():
    checklist = build_handoff_checklist_report(
        commit_present=True,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )
    audit = _trail("activation_requested")
    packet = build_operator_activation_packet(audit_trail=audit, checklist=checklist)

    index = build_activation_readiness_index(packet=packet, checklist=checklist, audit_trail=audit)

    assert index.to_dict() == {
        "overall_status": "ready",
        "items": (
            {
                "section_id": "blocked_runtime_capabilities",
                "status": "ready",
                "summary": "runtime capabilities remain intentionally blocked as a known boundary",
                "detail_count": 4,
            },
            {
                "section_id": "evidence_artifacts",
                "status": "ready",
                "summary": "audit evidence is present for operator review",
                "detail_count": 1,
            },
            {
                "section_id": "known_limits",
                "status": "ready",
                "summary": "known runtime limits are documented in blocked capability boundaries",
                "detail_count": 4,
            },
            {
                "section_id": "operator_next_steps",
                "status": "ready",
                "summary": "operator next steps are defined from current packet state",
                "detail_count": 1,
            },
            {
                "section_id": "prepared_foundation",
                "status": "ready",
                "summary": "foundation packet models are prepared for operator review",
                "detail_count": 3,
            },
            {
                "section_id": "readiness_gates",
                "status": "ready",
                "summary": "handoff checklist is ready",
                "detail_count": 9,
            },
        ),
    }

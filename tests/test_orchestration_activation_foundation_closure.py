from src.orchestration_activation_audit_trail import ActivationAuditEvent, ActivationAuditTrail
from src.orchestration_activation_handoff_checklist import build_handoff_checklist_report
from src.orchestration_operator_activation_packet import build_operator_activation_packet
from src.orchestration_activation_readiness_index import build_activation_readiness_index
from src.orchestration_activation_foundation_closure import (
    ClosureBundleStatus,
    build_activation_foundation_closure_bundle,
)


def _trail(*event_types: str) -> ActivationAuditTrail:
    events = []
    for index, event_type in enumerate(event_types, start=1):
        events.append(
            ActivationAuditEvent.create(
                event_id=f"evt-{index}",
                event_type=event_type,
                run_id="run-1",
                slice_id="auto21b",
                actor="charlie",
                timestamp=f"2026-06-17T10:0{index}:00Z",
                decision="prepare_dispatch",
                reason=f"{event_type} recorded",
            )
        )
    return ActivationAuditTrail.create(events)


def _ready_packet_and_index():
    checklist = build_handoff_checklist_report(
        commit_present=True,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )
    audit = _trail("activation_requested")
    packet = build_operator_activation_packet(audit_trail=audit, checklist=checklist)
    index = build_activation_readiness_index(packet=packet, checklist=checklist, audit_trail=audit)
    return packet, index


def test_foundation_ready_when_index_ready_and_runtime_gates_stay_closed():
    packet, index = _ready_packet_and_index()

    bundle = build_activation_foundation_closure_bundle(readiness_index=index, packet=packet)

    assert bundle.status == ClosureBundleStatus.FOUNDATION_READY


def test_runtime_blocked_when_operator_approved_but_runtime_gates_remain_closed():
    checklist = build_handoff_checklist_report(
        commit_present=True,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )
    audit = _trail("activation_requested", "operator_approved")
    packet = build_operator_activation_packet(audit_trail=audit, checklist=checklist)
    index = build_activation_readiness_index(packet=packet, checklist=checklist, audit_trail=audit)

    bundle = build_activation_foundation_closure_bundle(readiness_index=index, packet=packet)

    assert bundle.status == ClosureBundleStatus.RUNTIME_BLOCKED


def test_review_required_for_reviewable_or_deferred_state():
    checklist = build_handoff_checklist_report(
        commit_present=None,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )
    audit = _trail("activation_deferred")
    packet = build_operator_activation_packet(audit_trail=audit, checklist=checklist)
    index = build_activation_readiness_index(packet=packet, checklist=checklist, audit_trail=audit)

    bundle = build_activation_foundation_closure_bundle(readiness_index=index, packet=packet)

    assert bundle.status == ClosureBundleStatus.REVIEW_REQUIRED


def test_incomplete_when_required_inputs_are_missing():
    bundle = build_activation_foundation_closure_bundle()

    assert bundle.status == ClosureBundleStatus.INCOMPLETE


def test_to_dict_and_markdown_are_stable():
    packet, index = _ready_packet_and_index()
    bundle = build_activation_foundation_closure_bundle(readiness_index=index, packet=packet)

    assert bundle.to_dict() == {
        "status": "foundation_ready",
        "sections": (
            {
                "section_id": "artifact_inventory",
                "status": "foundation_ready",
                "summary": "activation packet artifacts are present",
                "detail_count": 3,
            },
            {
                "section_id": "followup_slices",
                "status": "runtime_blocked",
                "summary": "follow-up runtime slices remain outside this foundation closure bundle",
                "detail_count": 4,
            },
            {
                "section_id": "foundation_components",
                "status": "foundation_ready",
                "summary": "foundation components are ready for operator-facing closure",
                "detail_count": 6,
            },
            {
                "section_id": "operator_release_note",
                "status": "foundation_ready",
                "summary": "foundation is ready for operator review while runtime actions stay closed",
                "detail_count": 1,
            },
            {
                "section_id": "readiness_index_summary",
                "status": "foundation_ready",
                "summary": "readiness index is ready",
                "detail_count": 6,
            },
            {
                "section_id": "runtime_gates_closed",
                "status": "runtime_blocked",
                "summary": "runtime gates remain intentionally closed for foundation mode",
                "detail_count": 4,
            },
        ),
    }
    markdown = bundle.to_markdown()
    assert "# Activation Foundation Closure Bundle" in markdown
    assert "foundation_ready" in markdown
    assert "runtime_gates_closed" in markdown

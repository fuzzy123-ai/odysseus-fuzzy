from src.orchestration_activation_audit_trail import ActivationAuditEvent, ActivationAuditTrail
from src.orchestration_activation_handoff_checklist import build_handoff_checklist_report
from src.orchestration_operator_activation_packet import (
    OperatorActivationPacketState,
    build_operator_activation_packet,
)


def _trail(*event_types: str) -> ActivationAuditTrail:
    events = []
    for index, event_type in enumerate(event_types, start=1):
        events.append(
            ActivationAuditEvent.create(
                event_id=f"evt-{index}",
                event_type=event_type,
                run_id="run-1",
                slice_id="auto18b",
                actor="charlie",
                timestamp=f"2026-06-17T10:0{index}:00Z",
                decision="prepare_dispatch",
                reason=f"{event_type} recorded",
            )
        )
    return ActivationAuditTrail.create(events)


def test_ready_for_review_requires_ready_checklist_and_no_blocking_events():
    packet = build_operator_activation_packet(
        audit_trail=_trail("activation_requested", "preflight_checked"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )

    assert packet.state == OperatorActivationPacketState.READY_FOR_REVIEW


def test_blocked_when_checklist_blocked_or_gate_blocked():
    blocked_by_checklist = build_operator_activation_packet(
        checklist=build_handoff_checklist_report(
            commit_present=False,
            worktree_clean=True,
            no_foreign_staged_files=True,
        )
    )
    blocked_by_audit = build_operator_activation_packet(
        audit_trail=_trail("activation_requested", "gate_blocked"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )

    assert blocked_by_checklist.state == OperatorActivationPacketState.BLOCKED
    assert blocked_by_audit.state == OperatorActivationPacketState.BLOCKED


def test_cancelled_beats_other_states():
    packet = build_operator_activation_packet(
        audit_trail=_trail("operator_approved", "activation_cancelled"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )

    assert packet.state == OperatorActivationPacketState.CANCELLED


def test_operator_approved_becomes_pending_runtime_gate():
    packet = build_operator_activation_packet(
        audit_trail=_trail("activation_requested", "operator_approved"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )

    assert packet.state == OperatorActivationPacketState.APPROVED_PENDING_RUNTIME_GATE
    assert packet.blocked_runtime_actions == ("git_runner", "runtime_hooks", "test_runner", "thread_sends")


def test_deferred_when_deferred_event_or_missing_inputs():
    deferred = build_operator_activation_packet(audit_trail=_trail("activation_deferred"))
    missing = build_operator_activation_packet()

    assert deferred.state == OperatorActivationPacketState.DEFERRED
    assert missing.state == OperatorActivationPacketState.DEFERRED


def test_to_dict_is_stable():
    packet = build_operator_activation_packet(
        audit_trail=_trail("activation_requested"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )

    assert packet.to_dict() == {
        "state": "ready_for_review",
        "blocked_runtime_actions": ("git_runner", "runtime_hooks", "test_runner", "thread_sends"),
        "sections": (
            {
                "section_id": "audit",
                "summary": "audit trail present",
                "status": "present",
                "item_count": 1,
            },
            {
                "section_id": "checklist",
                "summary": "handoff checklist is ready",
                "status": "ready",
                "item_count": 9,
            },
            {
                "section_id": "runtime_gates",
                "summary": "runtime actions remain blocked pending operator-controlled runtime phase",
                "status": "blocked",
                "item_count": 4,
            },
        ),
    }

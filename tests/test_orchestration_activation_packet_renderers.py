from src.orchestration_activation_audit_trail import ActivationAuditEvent, ActivationAuditTrail
from src.orchestration_activation_handoff_checklist import build_handoff_checklist_report
from src.orchestration_operator_activation_packet import build_operator_activation_packet
from src.orchestration_activation_packet_renderers import (
    render_activation_packet_json,
    render_activation_packet_markdown,
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


def test_json_renderer_is_stable():
    packet = build_operator_activation_packet(
        audit_trail=_trail("activation_requested"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )

    rendered = render_activation_packet_json(packet)

    assert '"state": "ready_for_review"' in rendered
    assert '"blocked_runtime_actions"' in rendered
    assert '"section_id": "runtime_gates"' in rendered


def test_markdown_renderer_contains_required_sections():
    packet = build_operator_activation_packet(
        audit_trail=_trail("activation_requested", "preflight_checked"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )

    rendered = render_activation_packet_markdown(packet)

    assert "## Summary" in rendered
    assert "## Decision" in rendered
    assert "## Gate Status" in rendered
    assert "## Handoff Checklist" in rendered
    assert "## Audit Events" in rendered
    assert "## Evidence" in rendered
    assert "## Blocked Runtime Actions" in rendered
    assert "## Operator Next Step" in rendered


def test_decision_copy_covers_key_states():
    blocked = build_operator_activation_packet(
        audit_trail=_trail("gate_blocked"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )
    approved = build_operator_activation_packet(
        audit_trail=_trail("operator_approved"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )
    cancelled = build_operator_activation_packet(
        audit_trail=_trail("activation_cancelled"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )
    deferred = build_operator_activation_packet()

    assert "Blocked" in render_activation_packet_markdown(blocked)
    assert "Approved pending runtime gate" in render_activation_packet_markdown(approved)
    assert "Cancelled" in render_activation_packet_markdown(cancelled)
    assert "Deferred" in render_activation_packet_markdown(deferred)


def test_renderer_uses_placeholders_when_sections_are_missing():
    packet = build_operator_activation_packet()

    rendered = render_activation_packet_markdown(packet)

    assert "handoff checklist not provided" in rendered
    assert "audit trail not provided" in rendered
    assert "raw prompts and logs are intentionally omitted" in rendered


def test_renderer_does_not_dump_raw_secretish_text():
    packet = build_operator_activation_packet(
        audit_trail=_trail("activation_requested"),
        checklist=build_handoff_checklist_report(
            commit_present=True,
            worktree_clean=True,
            no_foreign_staged_files=True,
        ),
    )

    rendered = render_activation_packet_markdown(packet)

    assert "token=" not in rendered.lower()
    assert "password=" not in rendered.lower()
    assert "api_key" not in rendered.lower()

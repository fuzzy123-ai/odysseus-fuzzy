from src.orchestration_activation_audit_trail import ActivationAuditEvent, ActivationAuditTrail
from src.orchestration_activation_handoff_checklist import build_handoff_checklist_report
from src.orchestration_operator_activation_packet import build_operator_activation_packet
from src.orchestration_activation_readiness_index import build_activation_readiness_index
from src.orchestration_activation_foundation_closure import build_activation_foundation_closure_bundle
from src.orchestration_activation_operator_handoff_index import build_operator_handoff_index


def _trail(*event_types: str) -> ActivationAuditTrail:
    events = []
    for index, event_type in enumerate(event_types, start=1):
        events.append(
            ActivationAuditEvent.create(
                event_id=f"evt-{index}",
                event_type=event_type,
                run_id="run-1",
                slice_id="auto22b",
                actor="charlie",
                timestamp=f"2026-06-17T10:0{index}:00Z",
                decision="prepare_dispatch",
                reason=f"{event_type} recorded",
            )
        )
    return ActivationAuditTrail.create(events)


def _closure_bundle():
    checklist = build_handoff_checklist_report(
        commit_present=True,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )
    audit = _trail("activation_requested")
    packet = build_operator_activation_packet(audit_trail=audit, checklist=checklist)
    index = build_activation_readiness_index(packet=packet, checklist=checklist, audit_trail=audit)
    return build_activation_foundation_closure_bundle(readiness_index=index, packet=packet)


def test_default_runtime_no_go_list_is_present():
    handoff = build_operator_handoff_index()

    assert handoff.runtime_no_go_list == (
        "git_runner",
        "provider_rag_runtime",
        "scheduler_loop",
        "telegram_delivery",
        "test_runner",
        "thread_sends",
    )
    assert handoff.overall_status == "incomplete"


def test_handoff_index_uses_closure_bundle_status():
    handoff = build_operator_handoff_index(closure_bundle=_closure_bundle())

    assert handoff.overall_status == "foundation_ready"


def test_to_dict_is_stable():
    handoff = build_operator_handoff_index(closure_bundle=_closure_bundle())

    assert handoff.to_dict() == {
        "overall_status": "foundation_ready",
        "runtime_no_go_list": (
            "git_runner",
            "provider_rag_runtime",
            "scheduler_loop",
            "telegram_delivery",
            "test_runner",
            "thread_sends",
        ),
        "sections": (
            {
                "section_id": "completed_foundation_artifacts",
                "summary": "foundation closure bundle is present",
                "detail_count": 6,
            },
            {
                "section_id": "followup_slices",
                "summary": "follow-up runtime slices remain deferred after foundation closure",
                "detail_count": 6,
            },
            {
                "section_id": "next_manual_gate",
                "summary": "next manual gate is operator review of the foundation closure bundle",
                "detail_count": 1,
            },
            {
                "section_id": "operator_checklist",
                "summary": "review the closure bundle state and confirm runtime gates stay closed",
                "detail_count": 6,
            },
            {
                "section_id": "purpose",
                "summary": "summarize foundation-only activation readiness for an operator handoff without enabling runtime execution",
                "detail_count": 1,
            },
            {
                "section_id": "runtime_no_go_list",
                "summary": "runtime execution remains out of scope for operator handoff review",
                "detail_count": 6,
            },
            {
                "section_id": "verification_tests",
                "summary": "verification remains model-level and should be confirmed from recorded evidence, not rerun from this index",
                "detail_count": 1,
            },
        ),
    }


def test_markdown_renderer_is_readme_like():
    handoff = build_operator_handoff_index(closure_bundle=_closure_bundle())
    markdown = handoff.to_markdown()

    assert "# Operator Activation Handoff Index" in markdown
    assert "runtime_no_go_list" in markdown
    assert "foundation_ready" in markdown

from src.orchestration_activation_audit_trail import ActivationAuditEvent, ActivationAuditTrail
from src.orchestration_activation_handoff_checklist import build_handoff_checklist_report
from src.orchestration_operator_activation_packet import build_operator_activation_packet
from src.orchestration_activation_readiness_index import build_activation_readiness_index
from src.orchestration_activation_foundation_closure import build_activation_foundation_closure_bundle
from src.orchestration_activation_operator_handoff_index import build_operator_handoff_index
from src.orchestration_activation_foundation_regression_index import build_activation_foundation_regression_index


def _trail(*event_types: str) -> ActivationAuditTrail:
    events = []
    for index, event_type in enumerate(event_types, start=1):
        events.append(
            ActivationAuditEvent.create(
                event_id=f"evt-{index}",
                event_type=event_type,
                run_id="run-1",
                slice_id="auto23b",
                actor="charlie",
                timestamp=f"2026-06-17T10:0{index}:00Z",
                decision="prepare_dispatch",
                reason=f"{event_type} recorded",
            )
        )
    return ActivationAuditTrail.create(events)


def _closure_and_handoff():
    checklist = build_handoff_checklist_report(
        commit_present=True,
        worktree_clean=True,
        no_foreign_staged_files=True,
    )
    audit = _trail("activation_requested")
    packet = build_operator_activation_packet(audit_trail=audit, checklist=checklist)
    readiness_index = build_activation_readiness_index(packet=packet, checklist=checklist, audit_trail=audit)
    closure = build_activation_foundation_closure_bundle(readiness_index=readiness_index, packet=packet)
    handoff = build_operator_handoff_index(closure_bundle=closure)
    return closure, handoff


def test_default_required_tests_and_runtime_disabled_are_present():
    index = build_activation_foundation_regression_index()

    assert len(index.required_regression_tests) == 7
    assert index.runtime_capabilities_still_disabled == (
        "git_runner",
        "provider_rag_runtime",
        "scheduler_loop",
        "telegram_delivery",
        "test_runner",
        "thread_sends",
    )
    assert index.overall_status == "incomplete"


def test_index_uses_closure_and_handoff_status():
    closure, handoff = _closure_and_handoff()
    index = build_activation_foundation_regression_index(closure_bundle=closure, handoff_index=handoff)

    assert index.overall_status == "foundation_ready"


def test_to_dict_is_stable():
    closure, handoff = _closure_and_handoff()
    index = build_activation_foundation_regression_index(closure_bundle=closure, handoff_index=handoff)

    assert index.to_dict() == {
        "overall_status": "foundation_ready",
        "required_regression_tests": (
            {"test_ref": "tests/test_orchestration_activation_audit_trail.py"},
            {"test_ref": "tests/test_orchestration_activation_handoff_checklist.py"},
            {"test_ref": "tests/test_orchestration_operator_activation_packet.py"},
            {"test_ref": "tests/test_orchestration_activation_packet_renderers.py"},
            {"test_ref": "tests/test_orchestration_activation_readiness_index.py"},
            {"test_ref": "tests/test_orchestration_activation_foundation_closure.py"},
            {"test_ref": "tests/test_orchestration_activation_operator_handoff_index.py"},
        ),
        "runtime_capabilities_still_disabled": (
            "git_runner",
            "provider_rag_runtime",
            "scheduler_loop",
            "telegram_delivery",
            "test_runner",
            "thread_sends",
        ),
        "sections": (
            {
                "section_id": "evidence_boundaries",
                "summary": "this index only references evidence and tests; it does not execute or persist anything",
                "detail_count": 2,
            },
            {
                "section_id": "foundation_artifacts",
                "summary": "foundation closure and operator handoff artifacts are present",
                "detail_count": 13,
            },
            {
                "section_id": "next_post_foundation_slices",
                "summary": "post-foundation runtime slices remain deferred until operator review explicitly opens them",
                "detail_count": 6,
            },
            {
                "section_id": "operator_review_order",
                "summary": "operator should review closure bundle first, then handoff index, then regression references",
                "detail_count": 3,
            },
            {
                "section_id": "release_gate_summary",
                "summary": "current foundation gate status is foundation_ready",
                "detail_count": 2,
            },
            {
                "section_id": "required_regression_tests",
                "summary": "required regression tests are recorded as references only and are not executed by this model",
                "detail_count": 7,
            },
            {
                "section_id": "runtime_capabilities_still_disabled",
                "summary": "runtime capabilities remain disabled in the foundation phase",
                "detail_count": 6,
            },
        ),
    }


def test_markdown_is_operator_friendly():
    closure, handoff = _closure_and_handoff()
    index = build_activation_foundation_regression_index(closure_bundle=closure, handoff_index=handoff)
    markdown = index.to_markdown()

    assert "# Activation Foundation Regression Index" in markdown
    assert "Required Regression Tests" in markdown
    assert "foundation_ready" in markdown

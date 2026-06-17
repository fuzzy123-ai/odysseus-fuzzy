from src.system_health_dashboard_summary import build_system_health_dashboard_summary
from src.system_health_ops_readiness import build_foundation_ops_readiness_report
from src.system_health_plugin_audit_index import build_system_health_plugin_audit_index
from src.system_health_plugin_foundation_bundle import build_foundation_bundle_readiness
from src.system_health_plugin_foundation_readiness_index import (
    build_system_health_plugin_foundation_readiness_index,
)
from src.system_health_plugin_operator_review_packet import (
    build_system_health_plugin_operator_review_packet,
)
from src.system_health_plugin_readiness_score import build_system_health_plugin_readiness_score


def _build_base_inputs():
    audit_index = build_system_health_plugin_audit_index(
        foundation_bundle=build_foundation_bundle_readiness(),
        ops_readiness=build_foundation_ops_readiness_report(),
        dashboard_summary=build_system_health_dashboard_summary(),
    )
    readiness_score = build_system_health_plugin_readiness_score(audit_index)
    operator_review_packet = build_system_health_plugin_operator_review_packet(
        audit_index=audit_index,
        readiness_score=readiness_score,
    )
    return audit_index, readiness_score, operator_review_packet


def test_index_is_deferred_without_inputs():
    index = build_system_health_plugin_foundation_readiness_index()

    assert index.status == "deferred"
    assert index.runtime_disabled is True
    assert index.artifacts_present == ()


def test_index_is_blocked_when_review_packet_is_blocked():
    audit_index, readiness_score, operator_review_packet = _build_base_inputs()
    blocked_packet = operator_review_packet.__class__(
        decision_state="blocked",
        included_artifacts=operator_review_packet.included_artifacts,
        review_order=operator_review_packet.review_order,
        go_no_go_questions=operator_review_packet.go_no_go_questions,
        blocked_runtime_actions=operator_review_packet.blocked_runtime_actions,
        operator_signoff_inputs=operator_review_packet.operator_signoff_inputs,
        followup_slices=operator_review_packet.followup_slices,
        sections=operator_review_packet.sections,
    )

    index = build_system_health_plugin_foundation_readiness_index(
        audit_index=audit_index,
        readiness_score=readiness_score,
        operator_review_packet=blocked_packet,
    )

    assert index.status == "blocked"


def test_index_is_review_required_for_needs_operator_input_packet():
    audit_index, readiness_score, operator_review_packet = _build_base_inputs()

    index = build_system_health_plugin_foundation_readiness_index(
        audit_index=audit_index,
        readiness_score=readiness_score,
        operator_review_packet=operator_review_packet,
    )

    assert index.status == "review_required"
    assert index.runtime_disabled is True


def test_index_is_foundation_ready_only_for_review_ready_packet_with_runtime_disabled():
    audit_index, readiness_score, operator_review_packet = _build_base_inputs()
    review_ready_packet = operator_review_packet.__class__(
        decision_state="review_ready",
        included_artifacts=operator_review_packet.included_artifacts,
        review_order=operator_review_packet.review_order,
        go_no_go_questions=operator_review_packet.go_no_go_questions,
        blocked_runtime_actions=operator_review_packet.blocked_runtime_actions,
        operator_signoff_inputs=operator_review_packet.operator_signoff_inputs,
        followup_slices=operator_review_packet.followup_slices,
        sections=operator_review_packet.sections,
    )

    index = build_system_health_plugin_foundation_readiness_index(
        audit_index=audit_index,
        readiness_score=readiness_score,
        operator_review_packet=review_ready_packet,
    )

    assert index.status == "foundation_ready"


def test_to_dict_is_stable():
    audit_index, readiness_score, operator_review_packet = _build_base_inputs()
    review_ready_packet = operator_review_packet.__class__(
        decision_state="review_ready",
        included_artifacts=operator_review_packet.included_artifacts,
        review_order=operator_review_packet.review_order,
        go_no_go_questions=operator_review_packet.go_no_go_questions,
        blocked_runtime_actions=operator_review_packet.blocked_runtime_actions,
        operator_signoff_inputs=operator_review_packet.operator_signoff_inputs,
        followup_slices=operator_review_packet.followup_slices,
        sections=operator_review_packet.sections,
    )
    index = build_system_health_plugin_foundation_readiness_index(
        audit_index=audit_index,
        readiness_score=readiness_score,
        operator_review_packet=review_ready_packet,
    )

    assert index.to_dict() == {
        "status": "foundation_ready",
        "runtime_disabled": True,
        "artifacts_present": ("audit_index", "readiness_score", "operator_review_packet"),
        "readiness_evidence": (
            {"evidence_id": "audit_index", "label": "audit index status `foundation_ready` is attached"},
            {
                "evidence_id": "readiness_score",
                "label": "readiness score decision `ready_for_manual_review` is attached",
            },
            {
                "evidence_id": "operator_review_packet",
                "label": "operator review packet decision `review_ready` is attached",
            },
        ),
        "known_limits": (
            "no_live_host_agent_runtime",
            "no_telegram_delivery",
            "no_container_runtime_calls",
        ),
        "next_allowed_slices": (
            "host-agent-runtime",
            "telegram-delivery",
            "container-runtime-probes",
        ),
        "sections": (
            {
                "section_id": "foundation_artifacts",
                "summary": "foundation artifacts are attached for operator review",
                "detail_count": 3,
            },
            {
                "section_id": "known_limits",
                "summary": "known limits keep host, telegram, and container runtime actions out of scope",
                "detail_count": 3,
            },
            {
                "section_id": "manual_review_gates",
                "summary": "manual review gates are satisfied for foundation-only readiness",
                "detail_count": 3,
            },
            {
                "section_id": "next_allowed_slices",
                "summary": "follow-up slices remain deferred until operator review clears the foundation packet",
                "detail_count": 3,
            },
            {
                "section_id": "readiness_evidence",
                "summary": "readiness evidence references remain read-only and operator-facing",
                "detail_count": 3,
            },
            {
                "section_id": "runtime_still_disabled",
                "summary": "runtime remains explicitly disabled during foundation readiness review",
                "detail_count": 6,
            },
        ),
    }


def test_markdown_is_operator_friendly():
    audit_index, readiness_score, operator_review_packet = _build_base_inputs()
    index = build_system_health_plugin_foundation_readiness_index(
        audit_index=audit_index,
        readiness_score=readiness_score,
        operator_review_packet=operator_review_packet,
    )
    markdown = index.to_markdown()

    assert "# System Health Plugin Foundation Readiness Index" in markdown
    assert "Readiness Evidence" in markdown
    assert "review_required" in markdown

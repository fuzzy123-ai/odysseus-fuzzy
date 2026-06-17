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
from src.system_health_plugin_release_audit_summary import (
    build_system_health_plugin_release_audit_summary,
)


def _build_release_ready_inputs():
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
    foundation_index = build_system_health_plugin_foundation_readiness_index(
        audit_index=audit_index,
        readiness_score=readiness_score,
        operator_review_packet=review_ready_packet,
    )
    return foundation_index, review_ready_packet, readiness_score


def test_summary_is_deferred_without_inputs():
    summary = build_system_health_plugin_release_audit_summary()

    assert summary.status == "deferred"
    assert summary.runtime_disabled is True
    assert summary.included_foundation_artifacts == ()


def test_summary_is_blocked_when_foundation_index_is_blocked():
    foundation_index, operator_review_packet, readiness_score = _build_release_ready_inputs()
    blocked_foundation_index = foundation_index.__class__(
        status="blocked",
        runtime_disabled=foundation_index.runtime_disabled,
        artifacts_present=foundation_index.artifacts_present,
        readiness_evidence=foundation_index.readiness_evidence,
        known_limits=foundation_index.known_limits,
        next_allowed_slices=foundation_index.next_allowed_slices,
        sections=foundation_index.sections,
    )

    summary = build_system_health_plugin_release_audit_summary(
        foundation_readiness_index=blocked_foundation_index,
        operator_review_packet=operator_review_packet,
        readiness_score=readiness_score,
    )

    assert summary.status == "blocked"


def test_summary_needs_operator_input_for_review_required_foundation_index():
    foundation_index, operator_review_packet, readiness_score = _build_release_ready_inputs()
    review_required_index = foundation_index.__class__(
        status="review_required",
        runtime_disabled=foundation_index.runtime_disabled,
        artifacts_present=foundation_index.artifacts_present,
        readiness_evidence=foundation_index.readiness_evidence,
        known_limits=foundation_index.known_limits,
        next_allowed_slices=foundation_index.next_allowed_slices,
        sections=foundation_index.sections,
    )

    summary = build_system_health_plugin_release_audit_summary(
        foundation_readiness_index=review_required_index,
        operator_review_packet=operator_review_packet,
        readiness_score=readiness_score,
    )

    assert summary.status == "needs_operator_input"


def test_summary_is_release_review_ready_only_when_foundation_ready_and_runtime_disabled():
    foundation_index, operator_review_packet, readiness_score = _build_release_ready_inputs()

    summary = build_system_health_plugin_release_audit_summary(
        foundation_readiness_index=foundation_index,
        operator_review_packet=operator_review_packet,
        readiness_score=readiness_score,
    )

    assert summary.status == "release_review_ready"
    assert summary.runtime_disabled is True


def test_to_dict_is_stable():
    foundation_index, operator_review_packet, readiness_score = _build_release_ready_inputs()
    summary = build_system_health_plugin_release_audit_summary(
        foundation_readiness_index=foundation_index,
        operator_review_packet=operator_review_packet,
        readiness_score=readiness_score,
    )

    assert summary.to_dict() == {
        "status": "release_review_ready",
        "runtime_disabled": True,
        "included_foundation_artifacts": (
            "foundation_readiness_index",
            "operator_review_packet",
            "readiness_score",
        ),
        "verification_references": (
            "foundation_status:foundation_ready",
            "review_packet:review_ready",
            "readiness_score:ready_for_manual_review",
        ),
        "release_risks": (),
        "next_allowed_slices": (
            "host-agent-runtime",
            "telegram-delivery",
            "container-runtime-probes",
        ),
        "sections": (
            {
                "section_id": "included_foundation_artifacts",
                "summary": "foundation artifacts are present for release audit review",
                "detail_count": 3,
            },
            {
                "section_id": "manual_go_no_go",
                "summary": "manual go/no-go package is ready for release-audit review",
                "detail_count": 3,
            },
            {
                "section_id": "next_allowed_slices",
                "summary": "next allowed slices stay deferred until release audit review is complete",
                "detail_count": 3,
            },
            {
                "section_id": "release_risks",
                "summary": "release risks are tracked conservatively for operator review",
                "detail_count": 0,
            },
            {
                "section_id": "runtime_boundaries",
                "summary": "runtime boundaries remain disabled during release audit review",
                "detail_count": 6,
            },
            {
                "section_id": "summary_purpose",
                "summary": "release audit summary packages foundation evidence for operator review without enabling runtime execution",
                "detail_count": 1,
            },
            {
                "section_id": "verification_references",
                "summary": "verification references capture the attached foundation and operator review states",
                "detail_count": 3,
            },
        ),
    }


def test_markdown_is_operator_friendly():
    foundation_index, operator_review_packet, readiness_score = _build_release_ready_inputs()
    summary = build_system_health_plugin_release_audit_summary(
        foundation_readiness_index=foundation_index,
        operator_review_packet=operator_review_packet,
        readiness_score=readiness_score,
    )
    markdown = summary.to_markdown()

    assert "# System Health Plugin Release Audit Summary" in markdown
    assert "Release Risks" in markdown
    assert "release_review_ready" in markdown

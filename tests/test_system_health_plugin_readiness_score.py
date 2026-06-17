from src.system_health_plugin_audit_index import build_system_health_plugin_audit_index
from src.system_health_plugin_readiness_score import build_system_health_plugin_readiness_score
from src.system_health_plugin_foundation_bundle import build_foundation_bundle_readiness
from src.system_health_ops_readiness import build_foundation_ops_readiness_report
from src.system_health_dashboard_summary import build_system_health_dashboard_summary


def test_default_builder_without_audit_index_is_review_required():
    score = build_system_health_plugin_readiness_score()

    assert score.decision_state == "review_required"
    assert score.runtime_ready is False
    assert score.summary.overall_score == 0


def test_complete_audit_index_is_ready_for_manual_review_but_not_runtime_ready():
    audit_index = build_system_health_plugin_audit_index(
        foundation_bundle=build_foundation_bundle_readiness(),
        ops_readiness=build_foundation_ops_readiness_report(),
        dashboard_summary=build_system_health_dashboard_summary(),
    )

    score = build_system_health_plugin_readiness_score(audit_index)

    assert score.decision_state == "ready_for_manual_review"
    assert score.runtime_ready is False
    assert score.summary.next_action == "request manual operator review while keeping runtime execution disabled"


def test_missing_required_no_go_action_blocks_readiness():
    audit_index = build_system_health_plugin_audit_index()
    weakened_index = audit_index.__class__(
        overall_status=audit_index.overall_status,
        no_go_runtime_actions=tuple(
            value for value in audit_index.no_go_runtime_actions if value != "telegram_tokens"
        ),
        architecture_notes=audit_index.architecture_notes,
        required_review_tests=audit_index.required_review_tests,
        sections=audit_index.sections,
    )

    score = build_system_health_plugin_readiness_score(weakened_index)

    assert score.decision_state == "blocked"
    assert score.summary.blocker_count == 1
    assert "no-go runtime actions" in score.to_markdown().lower()


def test_to_dict_is_stable_for_default_complete_audit_index():
    audit_index = build_system_health_plugin_audit_index(
        foundation_bundle=build_foundation_bundle_readiness(),
        ops_readiness=build_foundation_ops_readiness_report(),
        dashboard_summary=build_system_health_dashboard_summary(),
    )
    score = build_system_health_plugin_readiness_score(audit_index)

    assert score.to_dict() == {
        "decision_state": "ready_for_manual_review",
        "runtime_ready": False,
        "dimensions": (
            {
                "dimension_id": "audit_coverage",
                "score": 100,
                "status": "pass",
                "summary": "audit coverage includes required review tests",
            },
            {
                "dimension_id": "deployment_prerequisites",
                "score": 100,
                "status": "pass",
                "summary": "deployment prerequisites are documented for manual review",
            },
            {
                "dimension_id": "foundation_completeness",
                "score": 100,
                "status": "pass",
                "summary": "foundation artifacts are present for operator review",
            },
            {
                "dimension_id": "host_boundary_safety",
                "score": 100,
                "status": "pass",
                "summary": "host boundary safety notes are complete",
            },
            {
                "dimension_id": "operator_docs",
                "score": 100,
                "status": "pass",
                "summary": "operator checklist references are present",
            },
            {
                "dimension_id": "runtime_no_go_integrity",
                "score": 100,
                "status": "pass",
                "summary": "runtime no-go integrity is preserved and runtime remains intentionally disabled",
            },
        ),
        "summary": {
            "decision_state": "ready_for_manual_review",
            "runtime_ready": False,
            "overall_score": 100,
            "next_action": "request manual operator review while keeping runtime execution disabled",
            "blocker_count": 0,
        },
    }


def test_markdown_is_operator_friendly():
    audit_index = build_system_health_plugin_audit_index(
        foundation_bundle=build_foundation_bundle_readiness(),
        ops_readiness=build_foundation_ops_readiness_report(),
        dashboard_summary=build_system_health_dashboard_summary(),
    )
    score = build_system_health_plugin_readiness_score(audit_index)
    markdown = score.to_markdown()

    assert "# System Health Plugin Readiness Score" in markdown
    assert "ready_for_manual_review" in markdown
    assert "Dimensions" in markdown

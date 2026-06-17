from src.system_health_dashboard_summary import build_system_health_dashboard_summary
from src.system_health_ops_readiness import build_foundation_ops_readiness_report
from src.system_health_plugin_audit_index import build_system_health_plugin_audit_index
from src.system_health_plugin_foundation_bundle import build_foundation_bundle_readiness
from src.system_health_plugin_operator_review_packet import build_system_health_plugin_operator_review_packet
from src.system_health_plugin_readiness_score import build_system_health_plugin_readiness_score


def _build_complete_inputs():
    audit_index = build_system_health_plugin_audit_index(
        foundation_bundle=build_foundation_bundle_readiness(),
        ops_readiness=build_foundation_ops_readiness_report(),
        dashboard_summary=build_system_health_dashboard_summary(),
    )
    readiness_score = build_system_health_plugin_readiness_score(audit_index)
    return audit_index, readiness_score


def test_packet_is_deferred_without_artifacts():
    packet = build_system_health_plugin_operator_review_packet()

    assert packet.decision_state == "deferred"
    assert packet.included_artifacts == ()


def test_packet_is_blocked_when_readiness_score_is_blocked():
    audit_index, readiness_score = _build_complete_inputs()
    blocked_score = readiness_score.__class__(
        decision_state="blocked",
        runtime_ready=False,
        dimensions=readiness_score.dimensions,
        summary=readiness_score.summary.__class__(
            decision_state="blocked",
            runtime_ready=False,
            overall_score=readiness_score.summary.overall_score,
            next_action="restore the blocked boundary",
            blocker_count=1,
        ),
    )

    packet = build_system_health_plugin_operator_review_packet(
        audit_index=audit_index,
        readiness_score=blocked_score,
    )

    assert packet.decision_state == "blocked"


def test_ready_for_manual_review_score_becomes_needs_operator_input_without_signoff():
    audit_index, readiness_score = _build_complete_inputs()

    packet = build_system_health_plugin_operator_review_packet(
        audit_index=audit_index,
        readiness_score=readiness_score,
    )

    assert packet.decision_state == "needs_operator_input"
    assert packet.included_artifacts == ("audit_index", "readiness_score")


def test_packet_blocks_when_critical_no_go_boundaries_are_missing():
    audit_index, readiness_score = _build_complete_inputs()
    weakened_index = audit_index.__class__(
        overall_status=audit_index.overall_status,
        no_go_runtime_actions=tuple(
            value for value in audit_index.no_go_runtime_actions if value != "telegram_tokens"
        ),
        architecture_notes=audit_index.architecture_notes,
        required_review_tests=audit_index.required_review_tests,
        sections=audit_index.sections,
    )

    packet = build_system_health_plugin_operator_review_packet(
        audit_index=weakened_index,
        readiness_score=readiness_score,
    )

    assert packet.decision_state == "blocked"


def test_to_dict_is_stable():
    audit_index, readiness_score = _build_complete_inputs()
    packet = build_system_health_plugin_operator_review_packet(
        audit_index=audit_index,
        readiness_score=readiness_score,
    )

    assert packet.to_dict() == {
        "decision_state": "needs_operator_input",
        "included_artifacts": ("audit_index", "readiness_score"),
        "review_order": (
            "audit_index",
            "readiness_score",
            "blocked_runtime_actions",
            "operator_signoff_inputs",
        ),
        "go_no_go_questions": (
            {
                "question_id": "question_1",
                "prompt": "Do host-agent boundaries stay outside Odysseus core runtime paths?",
            },
            {
                "question_id": "question_2",
                "prompt": "Are runtime no-go actions still explicitly blocked for foundation mode?",
            },
            {
                "question_id": "question_3",
                "prompt": "Are review tests and deployment prerequisites documented for manual operator review?",
            },
        ),
        "blocked_runtime_actions": (
            "host_commands_from_core",
            "telegram_tokens",
            "webhook_activation",
            "podman_docker_socket_mount",
            "privileged_container_access",
            "direct_smart_access_from_container",
        ),
        "operator_signoff_inputs": (
            "operator_name",
            "review_timestamp",
            "manual_go_no_go_decision",
            "followup_notes",
        ),
        "followup_slices": (
            "host-agent-runtime",
            "telegram-delivery",
            "container-runtime-probes",
        ),
        "sections": (
            {
                "section_id": "blocked_runtime_actions",
                "summary": "runtime actions remain blocked and intact for foundation review",
                "detail_count": 6,
            },
            {
                "section_id": "followup_slices",
                "summary": "runtime follow-up slices remain deferred beyond the operator review packet",
                "detail_count": 3,
            },
            {
                "section_id": "go_no_go_questions",
                "summary": "go/no-go questions remain manual and evidence-bound",
                "detail_count": 3,
            },
            {
                "section_id": "included_artifacts",
                "summary": "audit index and readiness score are attached for operator review",
                "detail_count": 2,
            },
            {
                "section_id": "operator_signoff_inputs",
                "summary": "operator signoff fields are required before any manual go/no-go conclusion",
                "detail_count": 4,
            },
            {
                "section_id": "packet_purpose",
                "summary": "operator review packet summarizes foundation evidence without enabling runtime execution",
                "detail_count": 1,
            },
            {
                "section_id": "review_order",
                "summary": "operator should review artifacts and runtime boundaries in a fixed order",
                "detail_count": 4,
            },
        ),
    }


def test_markdown_is_operator_friendly():
    audit_index, readiness_score = _build_complete_inputs()
    packet = build_system_health_plugin_operator_review_packet(
        audit_index=audit_index,
        readiness_score=readiness_score,
    )
    markdown = packet.to_markdown()

    assert "# System Health Plugin Operator Review Packet" in markdown
    assert "Go / No-Go Questions" in markdown
    assert "needs_operator_input" in markdown

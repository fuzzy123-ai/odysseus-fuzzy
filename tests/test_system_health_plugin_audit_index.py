from src.system_health_dashboard_summary import build_system_health_dashboard_summary
from src.system_health_ops_readiness import build_foundation_ops_readiness_report
from src.system_health_plugin_foundation_bundle import build_foundation_bundle_readiness
from src.system_health_plugin_audit_index import build_system_health_plugin_audit_index


def test_default_no_go_runtime_actions_and_architecture_notes_are_present():
    index = build_system_health_plugin_audit_index()

    assert index.no_go_runtime_actions == (
        "direct_smart_access_from_container",
        "host_commands_from_core",
        "podman_docker_socket_mount",
        "privileged_container_access",
        "telegram_tokens",
        "webhook_activation",
    )
    assert index.architecture_notes == (
        "docker_compatible",
        "host_agent_required",
        "odysseus_consumes_sanitized_snapshot",
        "podman_first",
    )
    assert index.overall_status == "foundation_only"


def test_builder_accepts_optional_foundation_ops_and_dashboard_inputs():
    foundation = build_foundation_bundle_readiness()
    ops = build_foundation_ops_readiness_report()
    dashboard = build_system_health_dashboard_summary()

    index = build_system_health_plugin_audit_index(
        foundation_bundle=foundation,
        ops_readiness=ops,
        dashboard_summary=dashboard,
    )

    assert index.overall_status == "foundation_ready"


def test_to_dict_is_stable():
    foundation = build_foundation_bundle_readiness()
    ops = build_foundation_ops_readiness_report()
    dashboard = build_system_health_dashboard_summary()
    index = build_system_health_plugin_audit_index(
        foundation_bundle=foundation,
        ops_readiness=ops,
        dashboard_summary=dashboard,
    )

    assert index.to_dict() == {
        "overall_status": "foundation_ready",
        "no_go_runtime_actions": (
            "direct_smart_access_from_container",
            "host_commands_from_core",
            "podman_docker_socket_mount",
            "privileged_container_access",
            "telegram_tokens",
            "webhook_activation",
        ),
        "architecture_notes": (
            "docker_compatible",
            "host_agent_required",
            "odysseus_consumes_sanitized_snapshot",
            "podman_first",
        ),
        "required_review_tests": (
            {"reference_id": "tests/test_system_health_agent_interface.py"},
            {"reference_id": "tests/test_system_health_basic_collectors.py"},
            {"reference_id": "tests/test_system_health_rule_engine.py"},
            {"reference_id": "tests/test_system_health_telegram_pull.py"},
            {"reference_id": "tests/test_system_health_container_runtime.py"},
            {"reference_id": "tests/test_system_health_advanced_collectors.py"},
            {"reference_id": "tests/test_system_health_dashboard_summary.py"},
            {"reference_id": "tests/test_system_health_ops_readiness.py"},
        ),
        "sections": (
            {
                "section_id": "deployment_prerequisites",
                "summary": "deployment prerequisites require a host agent and sanitized snapshot handoff before any runtime phase",
                "detail_count": 3,
            },
            {
                "section_id": "followup_slices",
                "summary": "follow-up runtime and deployment slices remain deferred beyond the foundation audit index",
                "detail_count": 6,
            },
            {
                "section_id": "host_agent_boundaries",
                "summary": "host-agent boundaries stay outside core execution and only sanitized snapshots enter Odysseus",
                "detail_count": 4,
            },
            {
                "section_id": "no_go_runtime_actions",
                "summary": "runtime no-go actions remain disabled during foundation audit review",
                "detail_count": 6,
            },
            {
                "section_id": "operator_audit_checklist",
                "summary": "operator audit checklist can reference foundation, ops, and dashboard summaries",
                "detail_count": 24,
            },
            {
                "section_id": "plugin_foundation_artifacts",
                "summary": "foundation artifacts are present for plugin audit review",
                "detail_count": 14,
            },
            {
                "section_id": "required_review_tests",
                "summary": "review tests are recorded as references only and are not executed by this model",
                "detail_count": 8,
            },
        ),
    }


def test_markdown_is_operator_friendly():
    index = build_system_health_plugin_audit_index()
    markdown = index.to_markdown()

    assert "# System Health Plugin Audit Index" in markdown
    assert "Required Review Tests" in markdown
    assert "No-go runtime actions" in markdown

from src.mvp_system_health_closure import (
    SystemHealthClosureGate,
    build_system_health_closure_report,
)


def test_default_system_health_progress_reflects_foundation_done_runtime_open():
    report = build_system_health_closure_report()

    assert report.roadmap_id == "system_health_checker_host_agent"
    assert report.percent_complete == 60
    assert "Host-agent MVP plan reviewed" in report.why_not_100
    assert "Review or defer" in report.recommended_next_human_decision

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["plugin_foundation_bundle"].status == "go"
    assert gates["ops_security_readiness"].status == "go"
    assert gates["host_agent_plan_reviewed"].status == "needs_operator_input"
    assert gates["host_agent_runtime_live"].slice_class == "needs_live_go"
    assert gates["dashboard_and_alert_ui_live"].slice_class == "needs_design"


def test_system_health_reaches_100_when_all_gates_are_complete():
    report = build_system_health_closure_report(
        host_agent_plan_reviewed_go=True,
        local_api_consumer_plan_go=True,
        host_agent_runtime_live_go=True,
        dashboard_and_alert_ui_live_go=True,
    )

    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "Telegram Voice Pipeline" in report.recommended_next_human_decision
    assert report.to_markdown_row() == "| 4 | System Health Checker Host-Agent | 100 | - |"


def test_system_health_live_gate_is_next_after_operator_plans():
    report = build_system_health_closure_report(
        host_agent_plan_reviewed_go=True,
        local_api_consumer_plan_go=True,
    )

    assert report.percent_complete == 80
    assert "Host-agent runtime live smoke" in report.why_not_100
    assert "Grant or defer" in report.recommended_next_human_decision


def test_system_health_gate_validation_rejects_unknown_values():
    try:
        SystemHealthClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="maybe",
            slice_class="repo_only",
            reason="invalid status",
        )
    except ValueError as exc:
        assert "unsupported system health closure gate status" in str(exc)
    else:
        raise AssertionError("unknown status should fail closed")

    try:
        SystemHealthClosureGate.create(
            gate_id="bad",
            title="Bad",
            status="go",
            slice_class="just_run_on_host",
            reason="invalid class",
        )
    except ValueError as exc:
        assert "unsupported system health closure slice class" in str(exc)
    else:
        raise AssertionError("unknown slice class should fail closed")

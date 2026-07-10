import json

from scripts.non_ui_gate_readiness import build_non_ui_gate_readiness, render_markdown


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_non_ui_gate_readiness_blocks_without_runtime_or_operator_inputs(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "roadmap.json",
        {
            "gate_queue": [
                {"id": "telegram-reminder-live-go", "status": "open"},
                {"id": "autonomous-coding-live-remote-control-go", "status": "open"},
                {"id": "PLANNING-WRITE-GO", "status": "open"},
                {"id": "ui-placement", "status": "open"},
            ]
        },
    )

    payload = build_non_ui_gate_readiness(
        plan_dir=plans,
        mvp_state_path=plans / "missing-mvp.json",
        env={},
        tool_lookup=lambda _tool: None,
    )
    encoded = json.dumps(payload, sort_keys=True)
    by_family = {item["family"]: item for item in payload["families"]}

    assert payload["schema"] == "odysseus.non_ui_gate_readiness.v1"
    assert payload["status"] == "blocked"
    assert payload["non_ui_decision_packet_count"] == 3
    assert payload["excluded_design_decision_packet_count"] == 1
    assert payload["recommended_next_operator_action"]["family"] == "calendar_reminders"
    assert payload["recommended_next_operator_action"]["reason"] == "runtime_readiness_gates_blocked"
    assert all(item["can_execute_now"] is False for item in payload["families"])
    assert "telegram_agent_reply_enabled" in by_family["calendar_reminders"]["missing_runtime_gates"]
    assert "podman_available" in by_family["autonomous_coding"]["missing_runtime_gates"]
    assert by_family["autonomous_coding"]["missing_runtime_gates"].count("operator_live_go_required") == 1
    assert by_family["planning_mcp"]["missing_runtime_gates"] == ()
    assert by_family["planning_mcp"]["execution_blockers"] == (
        "operator_go_package_inputs_required",
        "no_runtime_probe_for_family",
    )
    assert payload["live_execution_performed"] is False
    assert payload["network_probe_performed"] is False
    assert payload["tokens_visible"] is False
    assert payload["chat_ids_visible"] is False
    assert "TOKEN_VALUE" not in encoded
    assert "12345" not in encoded


def test_non_ui_gate_readiness_markdown_is_redacted_and_actionable(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "roadmap.json",
        {
            "gate_queue": [
                {"id": "telegram-reminder-live-go", "status": "open"},
                {"id": "ui-placement", "status": "open"},
            ]
        },
    )
    payload = build_non_ui_gate_readiness(
        plan_dir=plans,
        mvp_state_path=plans / "missing-mvp.json",
        env={
            "TELEGRAM_BOT_TOKEN": "TOKEN_VALUE",
            "TELEGRAM_ALLOWED_CHAT_IDS": "12345",
        },
        tool_lookup=lambda _tool: None,
    )

    rendered = render_markdown(payload)

    assert "# Non-UI Gate Readiness" in rendered
    assert "## Recommended Next Operator Action" in rendered
    assert "- Family: calendar_reminders" in rendered
    assert "telegram_agent_reply_enabled" in rendered
    assert "operator_live_go_required" in rendered
    assert "## Non-UI Families" in rendered
    assert "TOKEN_VALUE" not in rendered
    assert "12345" not in rendered


def test_non_ui_gate_readiness_still_requires_operator_inputs_when_runtime_config_exists(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_json(
        plans / "roadmap.json",
        {
            "gate_queue": [
                {"id": "telegram-reminder-live-go", "status": "open"},
            ]
        },
    )
    env = {
        "TELEGRAM_AGENT_REPLY_ENABLED": "true",
        "TELEGRAM_BOT_TOKEN": "TOKEN_VALUE",
        "TELEGRAM_ALLOWED_CHAT_IDS": "12345",
    }

    payload = build_non_ui_gate_readiness(
        plan_dir=plans,
        mvp_state_path=plans / "missing-mvp.json",
        env=env,
        tool_lookup=lambda _tool: None,
    )
    encoded = json.dumps(payload, sort_keys=True)
    calendar = payload["families"][0]

    assert calendar["family"] == "calendar_reminders"
    assert calendar["runtime_ready"] is False
    assert calendar["missing_runtime_gates"] == ("operator_live_go_required",)
    assert calendar["execution_blockers"] == (
        "operator_go_package_inputs_required",
        "runtime_readiness_gates_blocked",
    )
    assert calendar["can_execute_now"] is False
    assert payload["recommended_next_operator_action"]["family"] == "calendar_reminders"
    assert payload["recommended_next_operator_action"]["missing_runtime_gates"] == ("operator_live_go_required",)
    assert payload["recommended_next_operator_action"]["values_visible"] is False
    assert "TOKEN_VALUE" not in encoded
    assert "12345" not in encoded

from dataclasses import dataclass

from src.runtime_tool_status import build_runtime_tool_status


@dataclass
class _PluginTool:
    name: str
    description: str
    parameters: dict
    permission: str = "admin"


def test_runtime_tool_status_reports_schema_effect_and_gate_classes():
    payload = build_runtime_tool_status(
        disabled_tools=["bash"],
        builtin_descriptions={
            "bash": "Run shell commands.",
            "ask_user": "Ask the user.",
            "web_search": "Search the web.",
            "read_file": "Read a file.",
        },
        function_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ask_user",
                    "parameters": {
                        "type": "object",
                        "properties": {"question": {"type": "string"}},
                        "required": ["question"],
                    },
                },
            },
        ],
        plugin_tools=[
            _PluginTool(
                name="telegram_document_reply",
                description="Send Telegram document.",
                parameters={"type": "object", "properties": {"artifact_ref": {"type": "string"}}},
            )
        ],
    )
    tools = {item["tool_id"]: item for item in payload["tools"]}

    assert payload["schema"] == "odysseus.runtime_tool_status.v1"
    assert tools["bash"]["availability"] == "disabled"
    assert tools["bash"]["gate_status"] == "disabled_by_settings"
    assert tools["bash"]["side_effect_class"] == "local_command"
    assert tools["ask_user"]["gate_status"] == "turn_or_plan_gated"
    assert tools["web_search"]["side_effect_class"] == "live_or_network"
    assert tools["telegram_document_reply"]["side_effect_class"] == "telegram_outbound"
    assert tools["telegram_document_reply"]["gate_status"] == "operator_or_live_gate_required"
    assert tools["bash"]["parameter_names"] == ("command",)
    assert tools["bash"]["required_parameters"] == ("command",)
    assert tools["bash"]["schema_fingerprint"].startswith("sha256:")
    assert payload["raw_schema_visible"] is False
    assert payload["secret_values_visible"] is False


def test_runtime_tool_status_redacts_secret_descriptions():
    payload = build_runtime_tool_status(
        builtin_descriptions={"danger": "Authorization: Bearer abcdefghijk"},
        function_schemas=[],
        plugin_tools=[],
    )
    tool = payload["tools"][0]

    assert tool["description_hash"].startswith("sha256:")
    assert "Authorization" not in repr(payload)
    assert "abcdefghijk" not in repr(payload)


def test_runtime_tool_status_explains_catalog_drift_and_conservative_mcp_policy():
    payload = build_runtime_tool_status(
        builtin_descriptions={"tail_serve_output": "Read bounded serve output."},
        function_schemas=[],
        mcp_tools=[
            {
                "qualified_name": "mcp__review__lookup",
                "description": "Authorization: Bearer should-not-appear",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                "is_disabled": False,
            }
        ],
    )
    tools = {item["tool_id"]: item for item in payload["tools"]}

    tail = tools["tail_serve_output"]
    assert tail["source"] == "builtin"
    assert tail["catalog_availability"] == "blocked"
    assert tail["registration_disposition"] == "security_blocked"
    assert tail["gate_status"] == "blocked_by_catalog"
    assert "catalog_security_blocked" in tail["drift_codes"]

    mcp = tools["mcp__review__lookup"]
    assert mcp["source"] == "mcp"
    assert mcp["permission"] == "admin"
    assert mcp["risk_level"] == "elevated"
    assert mcp["effect_class"] == "control"
    assert mcp["requires_confirmation"] is True
    assert mcp["gate_status"] == "evidence_or_confirmation_required"
    assert "Authorization" not in repr(payload)
    assert "should-not-appear" not in repr(payload)

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
    assert tools["bash"]["lifecycle"] == "contextual"
    assert tools["bash"]["descriptor_permission"] == "admin"
    assert tools["bash"]["risk_level"] == "dangerous"
    assert tools["bash"]["projection_drift"] == ()
    assert tools["telegram_document_reply"]["source"] == "plugin"
    assert tools["telegram_document_reply"]["catalog_availability"] == "unavailable"
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


def test_runtime_tool_status_includes_conservative_mcp_source_without_raw_schema():
    payload = build_runtime_tool_status(
        builtin_descriptions={},
        function_schemas=[],
        plugin_tools=[],
        mcp_tools=[
            {
                "server_id": "demo_mcp",
                "qualified_name": "mcp__demo_mcp__lookup",
                "description": "Look up a reviewed item.",
                "input_schema": {
                    "type": "object",
                    "properties": {"private_query": {"type": "string"}},
                },
            }
        ],
    )

    tool = payload["tools"][0]
    assert tool["tool_id"] == "mcp__demo_mcp__lookup"
    assert tool["source"] == "mcp"
    assert tool["descriptor_permission"] == "admin"
    assert tool["risk_level"] == "dangerous"
    assert tool["parameter_names"] == ("private_query",)
    assert "private_query" not in tool["schema_fingerprint"]
    assert payload["raw_schema_visible"] is False

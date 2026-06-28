from src import tool_index
from src.agent_loop import TOOL_SECTIONS


def test_manage_settings_prompt_advertises_secret_handoff():
    prompt = TOOL_SECTIONS["manage_settings"]
    retrieval = tool_index.BUILTIN_TOOL_DESCRIPTIONS["manage_settings"]
    combined = f"{prompt}\n{retrieval}"

    assert "Secrets/API keys are read-only" not in combined
    assert "request_secret" in combined
    assert "secret_handoffs" in combined
    assert "confirmed=true" in combined
    assert "NEVER pass the secret value" in prompt


def test_admin_tool_descriptions_advertise_confirmed_flows():
    for name in ("manage_endpoints", "manage_mcp", "manage_webhooks", "manage_tokens"):
        combined = f"{TOOL_SECTIONS[name]}\n{tool_index.BUILTIN_TOOL_DESCRIPTIONS[name]}"
        assert "confirmed=true" in combined

    assert "secure handoff" in tool_index.BUILTIN_TOOL_DESCRIPTIONS["manage_endpoints"]
    assert "MCP command allowlist" in TOOL_SECTIONS["manage_mcp"]
    assert "masked" in TOOL_SECTIONS["manage_webhooks"]
    assert "shown once" in TOOL_SECTIONS["manage_tokens"]

from src import tool_index
from src.agent_loop import TOOL_SECTIONS
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


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


def test_app_api_prompt_advertises_named_tool_mutation_guardrails():
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == "app_api")
    combined = "\n".join([
        TOOL_SECTIONS["app_api"],
        tool_index.BUILTIN_TOOL_DESCRIPTIONS["app_api"],
        schema["function"]["description"],
    ])

    assert "assistant settings/run triggers" in combined
    assert "mutating /api/chat" in combined
    assert "/api/inject_context" in combined
    assert "/api/rewrite" in combined
    assert "mutating /api/embeddings" in combined
    assert "manage_embeddings" in combined
    assert "/api/upload" in combined
    assert "attachment UI" in combined
    assert "/api/signatures" in combined
    assert "Signature/Documents UI" in combined
    assert "mutating /api/presets" in combined
    assert "Presets UI" in combined
    assert "/api/editor-drafts" in combined
    assert "Gallery Editor UI" in combined
    assert "POST /api/cleanup" in combined
    assert "Cleanup UI" in combined
    assert "document create/import/export/mutations/tidy" in combined
    assert "manage_documents" in combined
    assert "gallery" in combined
    assert "contact mutations/import/config/clear" in combined
    assert "manage_assistant" in combined
    assert "manage_session" in combined
    assert "mutating /api/personal" in combined
    assert "manage_personal_docs" in combined
    assert "mutating /api/plugins" in combined
    assert "manage_plugins" in combined
    assert "mutating /api/email" in combined
    assert "memory writes/search/import/audit" in combined
    assert "notes/calendar mutations" in combined
    assert "prefs writes" in combined
    assert "skill mutations/test/audit/import" in combined
    assert "send_email" in combined
    assert "bulk_email" in combined
    assert "manage_contact" in combined
    assert "resolve_contact" in combined
    assert "manage_memory" in combined
    assert "manage_notes" in combined
    assert "manage_calendar" in combined
    assert "manage_settings" in combined
    assert "manage_skills" in combined
    assert "ui_control" in combined

from types import SimpleNamespace

from src import agent_loop_system_prompt
from src.agent_loop_prompts import _all_tool_sections
from src.builtin_tool_catalog import (
    DEFAULT_DEFERRED_TOOLS,
    DEPENDENT_CONTACT_DEFERRED_TOOLS,
    OPERATOR_PRIORITY_DEFERRED_TOOLS,
    OTHER_DEFAULT_DEFERRED_TOOLS,
    BuiltInDefaultPolicy,
    build_builtin_descriptors,
    builtin_catalog_audit_summary,
    builtin_spec,
)
from src.tool_catalog import ToolLifecycle, ToolVisibility
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
from src.tool_policy import (
    DEFAULT_DEFERRED_RUNTIME_TOOLS,
    build_effective_tool_policy,
    operator_priority_disabled_tools,
)


def test_catalog_has_exact_operator_and_dependent_deferred_families():
    assert OPERATOR_PRIORITY_DEFERRED_TOOLS == {
        "archive_email",
        "bulk_email",
        "delete_email",
        "list_email_accounts",
        "list_emails",
        "manage_calendar",
        "mark_email_read",
        "read_email",
        "reply_to_email",
        "send_email",
    }
    assert DEPENDENT_CONTACT_DEFERRED_TOOLS == {
        "manage_contact",
        "resolve_contact",
    }
    assert OTHER_DEFAULT_DEFERRED_TOOLS == {
        "manage_assistant",
        "manage_presets",
    }
    assert len(DEFAULT_DEFERRED_TOOLS) == 14

    for tool_id in OPERATOR_PRIORITY_DEFERRED_TOOLS:
        spec = builtin_spec(tool_id)
        assert spec.default_policy == BuiltInDefaultPolicy.DEFERRED_BY_OPERATOR_PRIORITY
        assert "deferred_by_operator_priority" in spec.projection_exceptions
    for tool_id in DEPENDENT_CONTACT_DEFERRED_TOOLS:
        spec = builtin_spec(tool_id)
        assert spec.default_policy == BuiltInDefaultPolicy.DEPENDENT_DEFERRED
        assert "dependent_deferred_by_communications_priority" in spec.projection_exceptions
    for tool_id in OTHER_DEFAULT_DEFERRED_TOOLS:
        spec = builtin_spec(tool_id)
        assert spec.default_policy == BuiltInDefaultPolicy.DEFERRED
        assert "default_deferred" in spec.projection_exceptions


def test_deferred_descriptors_are_default_off_and_hidden_when_available():
    index = build_builtin_descriptors(BUILTIN_TOOL_DESCRIPTIONS)

    for tool_id in DEFAULT_DEFERRED_TOOLS:
        descriptor = index.resolve(tool_id)
        assert descriptor.lifecycle == ToolLifecycle.DEFERRED
        assert descriptor.default_enabled is False
        expected_visibility = (
            ToolVisibility.BLOCKED
            if tool_id in OTHER_DEFAULT_DEFERRED_TOOLS
            else ToolVisibility.HIDDEN
        )
        assert descriptor.default_visibility == expected_visibility

    audit = builtin_catalog_audit_summary()
    assert set(audit["default_policies"]["deferred_by_operator_priority"]) == (
        OPERATOR_PRIORITY_DEFERRED_TOOLS
    )
    assert set(audit["default_policies"]["dependent_deferred"]) == (
        DEPENDENT_CONTACT_DEFERRED_TOOLS
    )
    assert set(audit["default_policies"]["deferred"]) == OTHER_DEFAULT_DEFERRED_TOOLS


def test_missing_setting_uses_safe_defaults_without_writing_configuration():
    disabled = operator_priority_disabled_tools({"dsgvo_mode": False})

    assert DEFAULT_DEFERRED_TOOLS <= disabled
    assert "mcp__email__read_email" in disabled
    assert "download_attachment" in disabled
    assert "mcp__email__download_attachment" in disabled
    assert disabled == DEFAULT_DEFERRED_RUNTIME_TOOLS

    policy = build_effective_tool_policy(
        last_user_message="Please help normally.",
        settings={"dsgvo_mode": False},
    )
    for tool_id in DEFAULT_DEFERRED_RUNTIME_TOOLS:
        assert policy.blocks(tool_id)
        assert tool_id in policy.hidden_tools
        assert "operator priority" in policy.reason_for(tool_id)


def test_explicit_existing_admin_configuration_is_preserved_exactly():
    enabled_existing = {"dsgvo_mode": False, "disabled_tools": []}
    policy = build_effective_tool_policy(
        last_user_message="Please help normally.",
        settings=enabled_existing,
    )

    assert operator_priority_disabled_tools(enabled_existing) == frozenset()
    assert not any(policy.blocks(tool_id) for tool_id in DEFAULT_DEFERRED_TOOLS)

    configured = {"dsgvo_mode": False, "disabled_tools": ["send_email", "web_search"]}
    disabled = operator_priority_disabled_tools(configured)
    assert disabled == {"send_email", "mcp__email__send_email", "web_search"}
    configured_policy = build_effective_tool_policy(
        last_user_message="Please help normally.",
        settings=configured,
    )
    assert configured_policy.blocks("send_email")
    assert configured_policy.blocks("mcp__email__send_email")
    assert configured_policy.blocks("web_search")
    assert not configured_policy.blocks("manage_calendar")

    per_turn_policy = build_effective_tool_policy(
        disabled_tools={"send_email"},
        last_user_message="Please help normally.",
        settings=enabled_existing,
    )
    assert per_turn_policy.blocks("send_email")
    assert per_turn_policy.blocks("mcp__email__send_email")


def test_malformed_disabled_setting_fails_closed_to_safe_defaults():
    assert operator_priority_disabled_tools({"disabled_tools": "send_email"}) == (
        DEFAULT_DEFERRED_RUNTIME_TOOLS
    )


def _listed_tools(monkeypatch, settings):
    from routes import model_routes

    monkeypatch.setattr(model_routes, "_load_settings", lambda: settings)
    monkeypatch.setattr("src.tool_registry.list_tools", lambda: [])
    router = model_routes.setup_model_routes(SimpleNamespace())
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/tools" and "GET" in route.methods
    )
    return {tool["id"]: tool for tool in endpoint()["tools"]}


def test_tool_listing_uses_defaults_without_overwriting_explicit_admin_state(monkeypatch):
    default_listing = _listed_tools(monkeypatch, {})
    assert default_listing["send_email"]["enabled"] is False
    assert default_listing["manage_calendar"]["enabled"] is False
    assert default_listing["manage_contact"]["enabled"] is False
    assert default_listing["read_file"]["enabled"] is True

    explicit_listing = _listed_tools(monkeypatch, {"disabled_tools": []})
    assert explicit_listing["send_email"]["enabled"] is True
    assert explicit_listing["manage_calendar"]["enabled"] is True
    assert explicit_listing["manage_contact"]["enabled"] is True


class _FakeMcpManager:
    def get_tool_descriptions_for_prompt(self, _disabled_map):
        return ""

    def get_all_openai_schemas(self, _disabled_map):
        return [
            {
                "type": "function",
                "function": {"name": "mcp__email__read_email"},
            },
            {
                "type": "function",
                "function": {"name": "mcp__vault__search"},
            },
        ]


def _system_prompt(monkeypatch, settings):
    monkeypatch.setattr("src.settings.load_settings", lambda: settings)
    monkeypatch.setattr(agent_loop_system_prompt, "_cached_base_prompt", None)
    monkeypatch.setattr(agent_loop_system_prompt, "_cached_base_prompt_key", None)
    messages, schemas = agent_loop_system_prompt._build_system_prompt(
        [{"role": "user", "content": "Please help normally."}],
        "local-test-model",
        None,
        _FakeMcpManager(),
        disabled_tools=set(),
        needs_admin=True,
        relevant_tools=set(DEFAULT_DEFERRED_TOOLS) | {"read_file"},
        suppress_local_context=True,
    )
    prompt = "\n\n".join(
        str(message.get("content", ""))
        for message in messages
        if message.get("role") == "system"
    )
    return prompt, schemas


def test_normal_prompt_and_mcp_schemas_hide_default_deferred_families(monkeypatch):
    prompt, schemas = _system_prompt(monkeypatch, {"dsgvo_mode": False})
    sections = _all_tool_sections()

    for tool_id in DEFAULT_DEFERRED_TOOLS:
        if tool_id in sections:
            assert sections[tool_id] not in prompt
    assert "mcp__email__read_email" not in {
        schema["function"]["name"] for schema in schemas
    }
    assert "mcp__vault__search" in {
        schema["function"]["name"] for schema in schemas
    }


def test_explicit_admin_activation_remains_available_without_migration(monkeypatch):
    prompt, schemas = _system_prompt(
        monkeypatch,
        {"dsgvo_mode": False, "disabled_tools": []},
    )
    sections = _all_tool_sections()

    assert sections["send_email"] in prompt
    assert "mcp__email__read_email" in {
        schema["function"]["name"] for schema in schemas
    }

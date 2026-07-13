from src.settings import DEFAULT_FEATURES, DEFAULT_SETTINGS, _PER_USER_KEYS
from src.settings_registry import (
    build_settings_registry,
    get_registry_entry,
    iter_registry_entries,
    public_registry,
    resolve_setting_alias,
)


def test_registry_covers_all_default_settings_and_features():
    setting_keys = {entry.key for entry in iter_registry_entries("setting")}
    feature_keys = {entry.key for entry in iter_registry_entries("feature")}

    assert setting_keys == set(DEFAULT_SETTINGS)
    assert feature_keys == set(DEFAULT_FEATURES)


def test_every_registry_entry_has_required_policy_metadata():
    registry = build_settings_registry()

    assert registry
    for entry in registry.values():
        assert entry.key
        assert entry.registry_key == f"{entry.source}:{entry.key}"
        assert entry.value_type in {"bool", "int", "float", "str", "enum", "list", "object"}
        assert entry.scope in {"global", "user", "both"}
        assert entry.agent_access in {"read", "write", "confirm", "secret_handoff", "human_only"}
        assert entry.category
        assert entry.owner_policy


def test_secret_credentials_are_handoff_only_but_token_budget_is_writable():
    for key in {"brave_api_key", "google_pse_key", "google_pse_cx", "tavily_api_key", "serper_api_key"}:
        entry = get_registry_entry(key)
        assert entry.secret is True
        assert entry.agent_access == "secret_handoff"
        assert entry.owner_policy == "secure_secret_handoff"

    budget = get_registry_entry("agent_input_token_budget")
    hard_max = get_registry_entry("agent_input_token_hard_max")

    assert budget.secret is False
    assert budget.agent_access == "write"
    assert hard_max.secret is False
    assert hard_max.agent_access == "write"


def test_per_user_keys_are_marked_both_scope():
    for key in _PER_USER_KEYS:
        entry = get_registry_entry(key)
        assert entry.scope == "both"
        assert entry.owner_policy == "user_scope_default_admin_global_explicit"


def test_structured_settings_have_patch_schema():
    structured = {
        key: entry
        for key, entry in ((entry.key, entry) for entry in iter_registry_entries("setting"))
        if isinstance(DEFAULT_SETTINGS[key], (dict, list))
    }

    assert structured
    assert set(structured) == {
        "agent_input_token_budget_overrides",
        "default_model_fallbacks",
        "keybinds",
        "memory.answer_fallback_models",
        "search_fallback_chain",
        "tool_path_extra_roots",
        "utility_model_fallbacks",
        "vision_model_fallbacks",
    }
    for entry in structured.values():
        assert entry.structured_schema
        assert entry.structured_schema.get("patch_ops")

    assert structured["tool_path_extra_roots"].agent_access == "confirm"


def test_aliases_and_public_registry_are_machine_readable():
    assert resolve_setting_alias("default model") == "default_model"
    assert resolve_setting_alias("token budget") == "agent_input_token_budget"
    assert resolve_setting_alias("agent_input_token_budget") == "agent_input_token_budget"
    assert resolve_setting_alias("telegram reminder dry run") == "reminder_telegram_dry_run"

    public = public_registry("feature")
    assert {entry["key"] for entry in public} == set(DEFAULT_FEATURES)
    assert all(entry["agent_access"] == "confirm" for entry in public)


def test_reminder_channel_registry_includes_telegram():
    entry = get_registry_entry("reminder_channel")

    assert entry.value_type == "enum"
    assert "telegram" in entry.enum_values
    assert get_registry_entry("reminder_telegram_dry_run").value_type == "bool"

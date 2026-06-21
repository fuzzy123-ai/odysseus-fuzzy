from plugins.obsidian.backend import feature_flags
from plugins.obsidian.backend.context_provider import (
    ORCA_PROVIDER_ID,
    PROVIDER_ID,
    provider_alias_specs,
    provider_spec,
    retrieve_vault_context,
)
from plugins.obsidian.backend.tool_specs import (
    DESTRUCTIVE_TOOL_NAMES,
    ORCA_TOOL_ALIASES,
    VAULT_TOOL_BY_NAME,
    execute_vault_tool,
)


def _clear_flag_env(monkeypatch):
    for env_name in [*feature_flags.ENV_NAMES.values(), *feature_flags.ORCA_ENV_NAMES.values()]:
        monkeypatch.delenv(env_name, raising=False)


def test_orca_feature_flag_names_and_env_aliases_fallback_to_obsidian(monkeypatch):
    _clear_flag_env(monkeypatch)

    assert feature_flags.is_enabled("obsidian_somt_enabled") is True
    assert feature_flags.is_enabled("orca_somt_enabled") is True
    assert feature_flags.is_enabled("obsidian_raptor_enabled") is False
    assert feature_flags.is_enabled("orca_raptor_enabled") is False
    assert feature_flags.is_enabled("orca_unknown_enabled") is False

    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    assert feature_flags.is_enabled("obsidian_raptor_enabled") is True
    assert feature_flags.is_enabled("orca_raptor_enabled") is True

    monkeypatch.setenv("ODYSSEUS_ORCA_RAPTOR_ENABLED", "false")
    assert feature_flags.is_enabled("obsidian_raptor_enabled") is False
    assert feature_flags.is_enabled("orca_raptor_enabled") is False


def test_orca_tool_aliases_share_vault_tool_contracts():
    assert ORCA_TOOL_ALIASES
    assert all(alias.startswith("orca_") for alias in ORCA_TOOL_ALIASES.values())

    for legacy_name, alias_name in ORCA_TOOL_ALIASES.items():
        legacy = VAULT_TOOL_BY_NAME[legacy_name]
        alias = VAULT_TOOL_BY_NAME[alias_name]

        assert alias.handler is legacy.handler
        assert alias.input_schema is legacy.input_schema
        assert alias.access == legacy.access
        assert (alias_name in DESTRUCTIVE_TOOL_NAMES) == (legacy_name in DESTRUCTIVE_TOOL_NAMES)

    assert "obsidian_read_note" in VAULT_TOOL_BY_NAME
    assert "obsidian_write_note" in VAULT_TOOL_BY_NAME
    assert "orca_read_note" in VAULT_TOOL_BY_NAME
    assert "orca_write_note" in VAULT_TOOL_BY_NAME
    assert "orca_vault_batch" in VAULT_TOOL_BY_NAME
    assert {"orca_write_note", "orca_vault_batch", "orca_delete_note", "orca_undo"} <= DESTRUCTIVE_TOOL_NAMES
    assert {"orca_read_note", "orca_search_notes", "orca_graph"}.isdisjoint(DESTRUCTIVE_TOOL_NAMES)


def test_orca_tool_alias_executes_same_handler(tmp_path):
    note = tmp_path / "Demo.md"
    note.write_text("# Demo\n\nbody", encoding="utf-8")

    result = execute_vault_tool(
        "orca_read_note",
        str(tmp_path),
        {"path": "Demo.md", "owner": "mallory"},
        "alice",
        {"source": "test"},
    )

    assert result == "# Demo\n\nbody"


def test_orca_context_provider_alias_spec_preserves_obsidian_provider_contract():
    legacy = provider_spec()
    alias = provider_spec(ORCA_PROVIDER_ID)

    assert legacy["id"] == PROVIDER_ID
    assert alias["id"] == ORCA_PROVIDER_ID
    assert legacy["retrieve"] is retrieve_vault_context
    assert alias["retrieve"] is retrieve_vault_context
    assert set(alias["capabilities"]) == set(legacy["capabilities"])
    assert provider_alias_specs() == [alias]

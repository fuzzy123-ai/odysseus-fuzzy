import json
import logging

import pytest

from src.builtin_tool_catalog import OPERATOR_PRIORITY_DEFERRED_IDS
from src.settings import migrate_tool_settings_file, rollback_tool_settings_file
from src.tool_catalog import (
    TOOL_SETTINGS_MIGRATION_KEY,
    TOOL_SETTINGS_QUARANTINE_KEY,
    TOOL_SETTINGS_SCHEMA_KEY,
    TOOL_SETTINGS_SCHEMA_VERSION,
    ToolCatalogError,
    migrate_tool_settings,
    rollback_tool_settings_migration,
)


def test_legacy_alias_is_explained_and_unknown_id_remains_safely_disabled():
    original = {
        "disabled_tools": ["web_search", "manage_rag", "unknown_private_tool"],
        "provider_api_key": "provider-secret-value",
    }

    migrated, report = migrate_tool_settings(original)

    assert original["disabled_tools"] == ["web_search", "manage_rag", "unknown_private_tool"]
    assert migrated[TOOL_SETTINGS_SCHEMA_KEY] == TOOL_SETTINGS_SCHEMA_VERSION
    assert "manage_rag" not in migrated["disabled_tools"]
    assert "manage_personal_docs" in migrated["disabled_tools"]
    assert "unknown_private_tool" in migrated["disabled_tools"]
    assert migrated[TOOL_SETTINGS_QUARANTINE_KEY] == ["unknown_private_tool"]
    assert OPERATOR_PRIORITY_DEFERRED_IDS <= set(migrated["disabled_tools"])
    assert migrated["provider_api_key"] == "provider-secret-value"

    metadata = migrated[TOOL_SETTINGS_MIGRATION_KEY]
    assert metadata["alias_rewrites"] == [
        {"alias": "manage_rag", "canonical": "manage_personal_docs", "occurrences": 1}
    ]
    assert metadata["unknown_disabled_tools"] == ["unknown_private_tool"]
    assert report.alias_rewrite_count == 1
    assert report.quarantined_count == 1
    assert "unknown_private_tool" not in repr(report.audit_dict())
    assert "provider-secret-value" not in repr(report.audit_dict())


def test_migration_is_semantically_and_byte_stable_on_second_run():
    first, first_report = migrate_tool_settings({"disabled_tools": ["manage_rag"]})
    first_bytes = json.dumps(first, sort_keys=True, separators=(",", ":")).encode()

    second, second_report = migrate_tool_settings(first)
    second_bytes = json.dumps(second, sort_keys=True, separators=(",", ":")).encode()

    assert first_report.changed is True
    assert second_report.changed is False
    assert second == first
    assert second_bytes == first_bytes


def test_missing_disabled_setting_materializes_safe_defaults_without_fake_legacy_opt_in():
    migrated, report = migrate_tool_settings({"theme": "dark"})

    assert migrated["disabled_tools"] == sorted(OPERATOR_PRIORITY_DEFERRED_IDS)
    assert migrated[TOOL_SETTINGS_MIGRATION_KEY]["legacy_enabled_deferred_tools"] == []
    assert report.disabled_setting_present is False
    assert report.legacy_enabled_deferred_count == 0


def test_explicit_legacy_empty_denylist_is_traceable_but_does_not_activate_deferred_tools():
    migrated, report = migrate_tool_settings({"disabled_tools": []})

    assert migrated["disabled_tools"] == sorted(OPERATOR_PRIORITY_DEFERRED_IDS)
    assert migrated[TOOL_SETTINGS_MIGRATION_KEY]["legacy_enabled_deferred_tools"] == sorted(
        OPERATOR_PRIORITY_DEFERRED_IDS
    )
    assert report.legacy_enabled_deferred_count == len(OPERATOR_PRIORITY_DEFERRED_IDS)


def test_rollback_restores_exact_migration_owned_values_and_other_settings():
    original = {
        "disabled_tools": ["manage_rag", "unknown_tool"],
        "disabled_tools_quarantine": ["older_unknown"],
        "model": "local-model",
    }
    migrated, _report = migrate_tool_settings(original)

    assert rollback_tool_settings_migration(migrated) == original


def test_invalid_or_future_schema_versions_fail_without_mutating_input():
    future = {TOOL_SETTINGS_SCHEMA_KEY: TOOL_SETTINGS_SCHEMA_VERSION + 1, "disabled_tools": []}
    invalid = {TOOL_SETTINGS_SCHEMA_KEY: True, "disabled_tools": []}

    with pytest.raises(ToolCatalogError, match="newer"):
        migrate_tool_settings(future)
    with pytest.raises(ToolCatalogError, match="integer"):
        migrate_tool_settings(invalid)
    assert future[TOOL_SETTINGS_SCHEMA_KEY] == TOOL_SETTINGS_SCHEMA_VERSION + 1
    assert invalid[TOOL_SETTINGS_SCHEMA_KEY] is True


def test_file_migration_is_byte_stable_and_logs_aggregate_counts_only(tmp_path, caplog):
    settings_path = tmp_path / "settings.json"
    private_identity = "account-alice@example.test"
    settings_path.write_text(
        json.dumps(
            {
                "disabled_tools": ["manage_rag", private_identity],
                "provider_api_key": "provider-secret-value",
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.INFO, logger="src.settings"):
        first_report = migrate_tool_settings_file(settings_path)
    first_bytes = settings_path.read_bytes()
    second_report = migrate_tool_settings_file(settings_path)
    second_bytes = settings_path.read_bytes()

    assert first_report["changed"] is True
    assert second_report["changed"] is False
    assert first_bytes == second_bytes
    assert private_identity not in caplog.text
    assert "provider-secret-value" not in caplog.text
    assert "aliases=1" in caplog.text
    assert "quarantined=1" in caplog.text


def test_file_rollback_restores_original_json_semantics(tmp_path):
    settings_path = tmp_path / "settings.json"
    original = {
        "disabled_tools": ["manage_rag", "unknown_tool"],
        "provider": {"endpoint": "local"},
    }
    settings_path.write_text(json.dumps(original), encoding="utf-8")

    migrate_tool_settings_file(settings_path)
    restored = rollback_tool_settings_file(settings_path)

    assert restored == original
    assert json.loads(settings_path.read_text(encoding="utf-8")) == original

import json

from scripts.update_database import (
    migrate_tool_settings_file,
    rollback_tool_settings_file,
)
from src.settings import (
    TOOL_SETTINGS_MIGRATION_KEY,
    migrate_tool_settings,
    rollback_tool_settings,
)
from src.tool_policy import DEFAULT_DEFERRED_RUNTIME_TOOLS


def test_migration_resolves_aliases_and_explains_legacy_non_runtime_ids():
    source = {
        "disabled_tools": [
            "old_reader",
            "read_file",
            "old_reader",
            "manage_rag",
            "dynamic_provider_tool",
        ],
        "theme": "dark",
    }

    migrated, report = migrate_tool_settings(
        source,
        known_tool_ids={"read_file"},
        alias_targets={"old_reader": "read_file"},
    )

    assert migrated["disabled_tools"] == ["dynamic_provider_tool", "read_file"]
    metadata = migrated[TOOL_SETTINGS_MIGRATION_KEY]
    assert metadata["aliases"] == [
        {"source": "old_reader", "target": "read_file"},
    ]
    assert metadata["quarantine"] == [
        {
            "value": "manage_rag",
            "reason": "legacy_ui_identifier_without_runtime_tool",
        },
        {"value": "dynamic_provider_tool", "reason": "unknown_tool_id"},
    ]
    assert report["alias_migration_count"] == 1
    assert report["quarantine_reason_counts"] == {
        "legacy_ui_identifier_without_runtime_tool": 1,
        "unknown_tool_id": 1,
    }


def test_missing_setting_materializes_safe_deferred_defaults():
    migrated, report = migrate_tool_settings({"theme": "dark"})

    assert set(migrated["disabled_tools"]) == DEFAULT_DEFERRED_RUNTIME_TOOLS
    assert {"send_email", "manage_calendar", "manage_contact"} <= set(
        migrated["disabled_tools"]
    )
    assert report["quarantine_count"] == 0


def test_explicit_empty_operator_choice_stays_empty():
    migrated, _report = migrate_tool_settings({"disabled_tools": []})

    assert migrated["disabled_tools"] == []


def test_invalid_setting_falls_back_safe_and_remains_reversible():
    source = {"disabled_tools": "not-a-list", "theme": "light"}

    migrated, report = migrate_tool_settings(source)

    assert set(migrated["disabled_tools"]) == DEFAULT_DEFERRED_RUNTIME_TOOLS
    assert report["quarantine_reason_counts"] == {
        "invalid_disabled_tools_container": 1
    }
    assert rollback_tool_settings(migrated) == source


def test_migration_is_semantically_idempotent_and_report_is_redacted():
    source = {
        "disabled_tools": ["manage_rag", "dynamic_provider_tool", "read_file"]
    }

    once, first_report = migrate_tool_settings(
        source,
        known_tool_ids={"read_file"},
    )
    twice, second_report = migrate_tool_settings(
        once,
        known_tool_ids={"read_file"},
    )

    assert twice == once
    assert first_report["changed"] is True
    assert second_report["changed"] is False
    assert rollback_tool_settings(twice) == source
    diagnostics = json.dumps(first_report, sort_keys=True)
    assert "manage_rag" not in diagnostics
    assert "dynamic_provider_tool" not in diagnostics
    assert first_report["raw_values_visible"] is False
    assert first_report["user_data_visible"] is False
    assert first_report["provider_data_visible"] is False


def test_file_migration_is_byte_stable_on_second_run_and_rolls_back(tmp_path):
    settings_path = tmp_path / "settings.json"
    source = {"disabled_tools": ["read_file", "manage_rag"], "theme": "dark"}
    settings_path.write_text(json.dumps(source), encoding="utf-8")

    first_report = migrate_tool_settings_file(settings_path)
    first_bytes = settings_path.read_bytes()
    second_report = migrate_tool_settings_file(settings_path)

    assert first_report["file_changed"] is True
    assert second_report["file_changed"] is False
    assert settings_path.read_bytes() == first_bytes

    rollback_report = rollback_tool_settings_file(settings_path)
    assert rollback_report == {
        "schema_version": 1,
        "file_changed": True,
        "rollback_applied": True,
    }
    assert json.loads(settings_path.read_text(encoding="utf-8")) == source
    assert TOOL_SETTINGS_MIGRATION_KEY not in json.loads(
        settings_path.read_text(encoding="utf-8")
    )

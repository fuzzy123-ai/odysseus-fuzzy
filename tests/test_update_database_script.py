from pathlib import Path


def test_update_database_has_single_main_guard():
    script = Path(__file__).resolve().parent.parent / "scripts" / "update_database.py"
    text = script.read_text()

    assert text.count('if __name__ == "__main__":') == 1


def test_update_database_runs_idempotent_tool_usage_migration_for_sqlite():
    script = Path(__file__).resolve().parent.parent / "scripts" / "update_database.py"
    text = script.read_text(encoding="utf-8")

    assert "from src.tool_usage_store import ToolUsageStore" in text
    assert "with ToolUsageStore(db_path) as tool_usage_store:" in text
    assert "tool_usage_store.migrate()" in text
    assert text.index("if db_path:", text.index("TUA2:")) < text.index("tool_usage_store.migrate()")


def test_update_database_runs_aggregate_only_tool_settings_migration():
    script = Path(__file__).resolve().parent.parent / "scripts" / "update_database.py"
    text = script.read_text(encoding="utf-8")

    assert "from src.settings import migrate_tool_settings_file" in text
    assert "tool_settings_report = migrate_tool_settings_file()" in text
    assert "tool_settings_report['alias_rewrite_count']" in text
    assert "tool_settings_report['quarantined_count']" in text
    assert "unknown_disabled_tools" not in text
    assert "legacy_enabled_deferred_tools" not in text

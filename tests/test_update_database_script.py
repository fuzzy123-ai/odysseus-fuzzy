from pathlib import Path


def test_update_database_has_single_main_guard():
    script = Path(__file__).resolve().parent.parent / "scripts" / "update_database.py"
    text = script.read_text()

    assert text.count('if __name__ == "__main__":') == 1


def test_update_database_does_not_log_database_url():
    script = Path(__file__).resolve().parent.parent / "scripts" / "update_database.py"
    text = script.read_text()

    assert 'print(f"Updating database at: {DATABASE_URL}")' not in text
    assert 'print(f"Error updating database: {e}")' not in text
    assert 'print("Updating database schema...")' in text
    assert "details were redacted" in text


def test_update_database_wires_versioned_tool_usage_schema_without_activation():
    script = Path(__file__).resolve().parent.parent / "scripts" / "update_database.py"
    text = script.read_text()

    assert "def migrate_tool_usage_schema(engine):" in text
    assert "def rollback_tool_usage_schema(engine):" in text
    assert "usage_schema_report = migrate_tool_usage_schema(engine)" in text
    assert "Tool usage schema migration:" in text
    assert "tool usage capture enabled" not in text.casefold()
    assert "backfill" not in text.casefold()

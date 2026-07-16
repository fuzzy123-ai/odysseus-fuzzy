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

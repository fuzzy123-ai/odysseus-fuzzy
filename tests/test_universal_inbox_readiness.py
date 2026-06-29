import json

from src.universal_inbox_readiness import (
    build_universal_inbox_readiness,
    format_universal_inbox_readiness_for_telegram,
)


def test_universal_inbox_readiness_blocks_missing_path_without_path_leak(tmp_path):
    missing = tmp_path / "missing-private-inbox"

    snapshot = build_universal_inbox_readiness(missing)
    encoded = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["status"] == "blocked"
    assert snapshot["ready"] is False
    assert snapshot["path_visible"] is False
    assert snapshot["host_paths_visible"] is False
    assert str(missing) not in encoded


def test_universal_inbox_readiness_reports_counts_without_file_names(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "rechnung-sensitive-name.txt").write_text("hello", encoding="utf-8")

    snapshot = build_universal_inbox_readiness(inbox)
    text = format_universal_inbox_readiness_for_telegram(snapshot)
    encoded = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["status"] in {"go", "partial"}
    assert snapshot["discovered_count"] == 1
    assert snapshot["processable_count"] == 1
    assert snapshot["path_visible"] is False
    assert snapshot["raw_content_visible"] is False
    assert "rechnung-sensitive-name" not in encoded
    assert "rechnung-sensitive-name" not in text
    assert str(inbox) not in encoded
    assert str(inbox) not in text
    assert "Private Inhalte" in text

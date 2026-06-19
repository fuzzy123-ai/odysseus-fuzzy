import hashlib
import json

import pytest

from src.universal_inbox_discovery import (
    UniversalInboxDiscoveryError,
    discover_universal_inbox_local,
)


def test_local_discovery_returns_metadata_only_relative_paths(tmp_path):
    inbox = tmp_path / "Inbox"
    nested = inbox / "nested"
    nested.mkdir(parents=True)
    document = nested / "note.md"
    document.write_text("# Hello\nBody\n", encoding="utf-8")

    report = discover_universal_inbox_local(inbox)

    assert report.discovered_count == 1
    item = report.items[0]
    assert item.relative_path == "nested/note.md"
    assert item.filename == "note.md"
    assert item.size == document.stat().st_size
    assert item.suffix == ".md"
    assert item.sha256 == hashlib.sha256(document.read_bytes()).hexdigest()

    encoded = json.dumps(report.to_dict(), sort_keys=True)
    assert str(tmp_path) not in encoded
    assert "# Hello" not in encoded


def test_local_discovery_ignores_hidden_temporary_and_symlink_files(tmp_path):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    (inbox / "visible.txt").write_text("visible", encoding="utf-8")
    (inbox / ".hidden.txt").write_text("hidden", encoding="utf-8")
    (inbox / "upload.tmp").write_text("temporary", encoding="utf-8")
    (inbox / "~$draft.docx").write_text("temporary", encoding="utf-8")
    target = inbox / "target.txt"
    target.write_text("target", encoding="utf-8")
    symlink = inbox / "linked.txt"
    try:
        symlink.symlink_to(target)
    except OSError:
        symlink = None

    report = discover_universal_inbox_local(inbox)

    assert [item.relative_path for item in report.items] == ["target.txt", "visible.txt"]
    warning_codes = {warning.code for warning in report.warnings}
    assert "hidden_file_ignored" in warning_codes
    assert "temporary_file_ignored" in warning_codes
    if symlink is not None:
        assert "symlink_ignored" in warning_codes


def test_local_discovery_size_limit_is_structured_warning(tmp_path):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    (inbox / "large.txt").write_text("abcdef", encoding="utf-8")

    report = discover_universal_inbox_local(inbox, max_file_size_bytes=3)

    assert report.items == ()
    assert report.warnings[0].to_dict() == {
        "code": "size_limit_exceeded",
        "relative_path": "large.txt",
        "detail": "size>3",
    }


def test_local_discovery_requires_directory(tmp_path):
    with pytest.raises(UniversalInboxDiscoveryError):
        discover_universal_inbox_local(tmp_path / "missing")

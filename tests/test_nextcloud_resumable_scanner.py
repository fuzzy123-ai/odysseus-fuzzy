from src.nextcloud_resumable_scanner import classify_nextcloud_inventory_path, run_nextcloud_scanner_dry_run
from src.bigdata_ledger_contract import AppendOnlyBigDataLedger


def test_resumable_scanner_writes_metadata_inventory_and_resumes(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "b.txt").write_text("beta", encoding="utf-8")
    ledger_path = tmp_path / "ledger" / "events.jsonl"

    first = run_nextcloud_scanner_dry_run(
        root=root,
        ledger_path=ledger_path,
        source_id="nextcloud-main",
    )
    second = run_nextcloud_scanner_dry_run(
        root=root,
        ledger_path=ledger_path,
        source_id="nextcloud-main",
    )

    assert first.scanned == 2
    assert first.committed == 2
    assert first.skipped_existing == 0
    assert first.ledger_summary["latest_records"] == 2
    assert second.scanned == 2
    assert second.committed == 0
    assert second.skipped_existing == 2
    assert second.ledger_summary["latest_records"] == 2
    assert "alpha" not in ledger_path.read_text(encoding="utf-8")
    assert "beta" not in ledger_path.read_text(encoding="utf-8")


def test_resumable_scanner_can_resume_after_batch_interruption(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for index in range(5):
        (root / f"{index}.txt").write_text(f"value {index}", encoding="utf-8")
    ledger_path = tmp_path / "ledger.jsonl"

    first = run_nextcloud_scanner_dry_run(
        root=root,
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        batch_limit=2,
    )
    second = run_nextcloud_scanner_dry_run(
        root=root,
        ledger_path=ledger_path,
        source_id="nextcloud-main",
    )

    assert first.interrupted is True
    assert first.committed == 2
    assert second.scanned == 5
    assert second.skipped_existing == 2
    assert second.committed == 3
    assert second.ledger_summary["latest_records"] == 5


def test_resumable_scanner_rejects_ledger_inside_scanned_root(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")

    try:
        run_nextcloud_scanner_dry_run(
            root=root,
            ledger_path=root / "ledger.jsonl",
            source_id="nextcloud-main",
        )
    except ValueError as exc:
        assert "ledger_path must not live inside the scanned root" in str(exc)
    else:
        raise AssertionError("ledger inside source root should be rejected")


def test_resumable_scanner_marks_sensitive_roots_without_reading_content(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "sensitive-root").mkdir()
    (root / "sensitive-root" / "a.txt").write_text("sensitive body", encoding="utf-8")
    (root / "archive-root").mkdir()
    (root / "archive-root" / "b.txt").write_text("archive body", encoding="utf-8")
    ledger_path = tmp_path / "ledger.jsonl"

    result = run_nextcloud_scanner_dry_run(
        root=root,
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        sensitive_roots=("sensitive-root",),
    )
    latest = AppendOnlyBigDataLedger(ledger_path).latest_state()
    records = {record.item.relative_path: record for record in latest.values()}
    encoded = ledger_path.read_text(encoding="utf-8")

    assert result.committed == 2
    assert records["sensitive-root/a.txt"].metadata["privacy"]["privacy_class"] == "local_sensitive"
    assert records["sensitive-root/a.txt"].metadata["privacy"]["archive_allowed"] is False
    assert records["sensitive-root/a.txt"].metadata["privacy"]["mirror_to_new_nextcloud"] is False
    assert records["sensitive-root/a.txt"].metadata["privacy"]["required_model_scope"] == "local_only"
    assert records["archive-root/b.txt"].metadata["privacy"]["privacy_class"] == "archive_candidate"
    assert records["archive-root/b.txt"].metadata["privacy"]["archive_allowed"] is True
    assert records["archive-root/b.txt"].metadata["privacy"]["mirror_to_new_nextcloud"] is True
    assert "sensitive body" not in encoded
    assert "archive body" not in encoded


def test_resumable_scanner_applies_exclusions_and_file_type_metadata(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "Desktop.ini").write_text("system", encoding="utf-8")
    (root / ".sync_123.db").write_text("sync", encoding="utf-8")
    (root / "~$draft.docx").write_text("temp", encoding="utf-8")
    (root / "empty.txt").write_text("", encoding="utf-8")
    (root / "notes.md").write_text("private note body", encoding="utf-8")
    (root / "doc.pdf").write_bytes(b"%PDF-1.4")
    (root / "tool.exe").write_bytes(b"MZ")
    (root / "photo.jpg").write_bytes(b"image")
    (root / "archive.zip").write_bytes(b"zip")
    (root / "unknown").write_text("unknown body", encoding="utf-8")
    ledger_path = tmp_path / "ledger.jsonl"

    result = run_nextcloud_scanner_dry_run(
        root=root,
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        config={
            "default_unknown_private": False,
            "sensitive_roots": (),
        },
    )
    latest = AppendOnlyBigDataLedger(ledger_path).latest_state()
    records = {record.item.relative_path: record for record in latest.values()}
    encoded = ledger_path.read_text(encoding="utf-8")

    assert result.scanned == 10
    assert result.excluded == 4
    assert result.committed == 6
    assert "Desktop.ini" not in records
    assert ".sync_123.db" not in records
    assert records["notes.md"].metadata["file_category"] == "text_extractable"
    assert records["doc.pdf"].metadata["file_category"] == "document_extractable"
    assert records["tool.exe"].metadata["file_category"] == "dangerous_or_binary"
    assert records["photo.jpg"].metadata["file_category"] == "media_metadata"
    assert records["archive.zip"].metadata["file_category"] == "archive_review"
    assert records["unknown"].metadata["file_category"] == "unsupported"
    assert records["notes.md"].metadata["extension"] == ".md"
    assert records["notes.md"].metadata["exclusion_status"] == "included"
    assert records["notes.md"].metadata["long_path"] is False
    assert records["notes.md"].metadata["top_level_root"] == "notes.md"
    assert records["notes.md"].metadata["scan_profile"] == "full"
    assert "private note body" not in encoded
    assert "unknown body" not in encoded


def test_inventory_path_classifier_marks_long_paths_without_touching_disk():
    relative = "Daten/" + "/".join(["deep"] * 60) + "/report.pdf"

    metadata = classify_nextcloud_inventory_path(
        relative,
        size_bytes=128,
        config={"default_unknown_private": False},
        scan_profile="documents_only",
    )

    assert metadata["file_category"] == "document_extractable"
    assert metadata["long_path"] is True
    assert metadata["relative_path_length"] > 240
    assert metadata["top_level_root"] == "Daten"
    assert metadata["scan_profile"] == "documents_only"

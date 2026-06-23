from src.nextcloud_resumable_scanner import run_nextcloud_scanner_dry_run


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

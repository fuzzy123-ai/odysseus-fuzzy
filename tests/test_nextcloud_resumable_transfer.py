from src.bigdata_ledger_contract import AppendOnlyBigDataLedger, BigDataLedgerItem, BigDataLedgerRecord
from src.nextcloud_resumable_transfer import plan_nextcloud_resumable_transfer


def _inventory_item(path: str, *, source_id: str = "nextcloud-main") -> BigDataLedgerItem:
    return BigDataLedgerItem(
        provider="nextcloud",
        source_id=source_id,
        relative_path=path,
        size_bytes=12,
        mtime="2026-06-22T10:00:00Z",
    )


def _seed_inventory(ledger_path, *paths: str, privacy_class: str = "archive_candidate") -> None:
    ledger = AppendOnlyBigDataLedger(ledger_path)
    for path in paths:
        ledger.append_record(
            BigDataLedgerRecord.create(
                _inventory_item(path),
                stage="inventory",
                status="completed",
                metadata={
                    "scanner": "test",
                    "privacy": {
                        "privacy_class": privacy_class,
                        "archive_allowed": privacy_class == "archive_candidate",
                        "mirror_to_new_nextcloud": privacy_class == "archive_candidate",
                    },
                },
            )
        )


def test_resumable_transfer_plans_pending_copy_only_records_and_resumes(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    _seed_inventory(ledger_path, "a.txt", "nested/b.txt")

    first = plan_nextcloud_resumable_transfer(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        target_label="private-mirror",
    )
    second = plan_nextcloud_resumable_transfer(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        target_label="private-mirror",
    )
    latest = AppendOnlyBigDataLedger(ledger_path).latest_state()
    transfer_records = [record for record in latest.values() if record.stage == "transfer"]

    assert first.planned == 2
    assert first.skipped_existing == 0
    assert second.planned == 0
    assert second.skipped_existing == 2
    assert {record.status for record in transfer_records} == {"pending"}
    assert all(record.metadata["copy_only"] is True for record in transfer_records)
    assert all(record.metadata["live_action"] is False for record in transfer_records)
    assert "private-mirror" in ledger_path.read_text(encoding="utf-8")
    assert "/srv/" not in ledger_path.read_text(encoding="utf-8")


def test_resumable_transfer_can_resume_after_batch_interruption(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    _seed_inventory(ledger_path, "0.txt", "1.txt", "2.txt")

    first = plan_nextcloud_resumable_transfer(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        target_label="private-mirror",
        batch_limit=1,
    )
    second = plan_nextcloud_resumable_transfer(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        target_label="private-mirror",
    )

    assert first.interrupted is True
    assert first.planned == 1
    assert second.planned == 2
    assert second.skipped_existing == 1


def test_resumable_transfer_rejects_unsafe_target_label(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    _seed_inventory(ledger_path, "a.txt")

    try:
        plan_nextcloud_resumable_transfer(
            ledger_path=ledger_path,
            source_id="nextcloud-main",
            target_label="/srv/private",
        )
    except ValueError as exc:
        assert "target_label must be a safe redacted label" in str(exc)
    else:
        raise AssertionError("raw target paths should be rejected")


def test_resumable_transfer_skips_local_sensitive_inventory(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    _seed_inventory(ledger_path, "archive-root/a.txt")
    _seed_inventory(ledger_path, "sensitive-root/b.txt", privacy_class="local_sensitive")

    result = plan_nextcloud_resumable_transfer(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        target_label="nextcloud-mirror",
    )
    latest = AppendOnlyBigDataLedger(ledger_path).latest_state()
    transfer_paths = {
        record.item.relative_path
        for record in latest.values()
        if record.stage == "transfer"
    }

    assert result.planned == 1
    assert transfer_paths == {"archive-root/a.txt"}

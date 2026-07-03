import json

from src.bigdata_ledger_contract import AppendOnlyBigDataLedger, BigDataLedgerItem, BigDataLedgerRecord
from src.nextcloud_local_extraction_review import run_nextcloud_local_only_extraction_review


def _inventory(
    path: str,
    *,
    file_category: str = "text_extractable",
    privacy_class: str = "local_sensitive",
    source_id: str = "nextcloud-main",
) -> BigDataLedgerRecord:
    return BigDataLedgerRecord.create(
        BigDataLedgerItem(
            provider="nextcloud",
            source_id=source_id,
            relative_path=path,
            size_bytes=64,
            mtime="2026-07-03T10:00:00Z",
        ),
        stage="inventory",
        status="completed",
        metadata={
            "scanner": "test",
            "file_category": file_category,
            "extension": "." + path.rsplit(".", 1)[-1] if "." in path else "",
            "privacy": {
                "privacy_class": privacy_class,
                "local_model_only": privacy_class != "archive_candidate",
                "memory_write_candidate": False,
                "required_model_scope": "local_only",
            },
        },
    )


def test_local_extraction_review_blocks_without_operator_go(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    ledger_path = tmp_path / "events.jsonl"
    AppendOnlyBigDataLedger(ledger_path).append_record(_inventory("Privat/private.md"))

    result = run_nextcloud_local_only_extraction_review(
        root=root,
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        operator_local_extraction_go=False,
    )
    latest = AppendOnlyBigDataLedger(ledger_path).latest_state()

    assert result.status == "blocked"
    assert result.processed_count == 0
    assert result.reasons == ("operator_local_extraction_go_required",)
    assert not any(record.stage == "extraction" for record in latest.values())


def test_local_extraction_review_persists_redacted_runtime_only_records(tmp_path):
    root = tmp_path / "source"
    private_dir = root / "Privat"
    private_dir.mkdir(parents=True)
    (private_dir / "private.md").write_text("PRIVATE FIXTURE BODY Rechnung 123", encoding="utf-8")
    ledger_path = tmp_path / "events.jsonl"
    AppendOnlyBigDataLedger(ledger_path).append_record(_inventory("Privat/private.md"))

    result = run_nextcloud_local_only_extraction_review(
        root=root,
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        batch_limit=5,
        operator_local_extraction_go=True,
    )
    latest = AppendOnlyBigDataLedger(ledger_path).latest_state()
    extraction_records = [record for record in latest.values() if record.stage == "extraction"]
    encoded_result = json.dumps(result.to_dict(), sort_keys=True)
    encoded_extraction = json.dumps(extraction_records[0].to_dict(), sort_keys=True)

    assert result.status == "completed"
    assert result.processed_count == 1
    assert result.appended_count == 1
    assert result.items[0].char_count > 0
    assert result.memory_writes_permitted is False
    assert result.raptor_writes_permitted is False
    assert len(extraction_records) == 1
    assert extraction_records[0].item.relative_path.startswith("Local Extraction Review/")
    assert extraction_records[0].metadata["extraction_runtime_only"] is True
    assert extraction_records[0].metadata["derived_material_persisted"] is False
    assert extraction_records[0].metadata["memory_writes_permitted"] is False
    assert extraction_records[0].metadata["raptor_writes_permitted"] is False
    assert "Privat/private.md" not in encoded_result
    assert "Privat/private.md" not in encoded_extraction
    assert "PRIVATE FIXTURE BODY" not in encoded_result
    assert "PRIVATE FIXTURE BODY" not in encoded_extraction

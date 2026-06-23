import json

from src.bigdata_ledger_contract import AppendOnlyBigDataLedger, BigDataLedgerItem, BigDataLedgerRecord
from src.nextcloud_chunked_extraction import (
    RuntimeExtractionDocument,
    build_extraction_chunk_refs,
    plan_nextcloud_chunked_extraction,
)


def _item(path: str, *, source_id: str = "nextcloud-main") -> BigDataLedgerItem:
    return BigDataLedgerItem(
        provider="nextcloud",
        source_id=source_id,
        relative_path=path,
        size_bytes=12,
        mtime="2026-06-22T10:00:00Z",
    )


def _seed_transfer(ledger_path, item: BigDataLedgerItem, *, status: str = "completed") -> None:
    ledger = AppendOnlyBigDataLedger(ledger_path)
    ledger.append_record(
        BigDataLedgerRecord.create(
            item,
            stage="transfer",
            status=status,
            metadata={"planner": "test", "copy_only": True},
        )
    )


def test_chunk_refs_are_deterministic_and_do_not_return_bodies():
    refs = build_extraction_chunk_refs("abcdef", max_chunk_chars=2)

    assert [ref.to_dict()["chars"] for ref in refs] == [2, 2, 2]
    assert [ref.to_dict()["start"] for ref in refs] == [0, 2, 4]
    assert all("ab" not in json.dumps(ref.to_dict(), sort_keys=True) for ref in refs)


def test_chunked_extraction_persists_only_hash_refs_and_is_resumable(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    item = _item("notes/private.md")
    _seed_transfer(ledger_path, item)

    first = plan_nextcloud_chunked_extraction(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        documents=[
            RuntimeExtractionDocument.create(
                item,
                runtime_text="alpha secret body should remain runtime only",
                warning_codes=("encoding_fallback_used",),
            )
        ],
        max_chunk_chars=12,
    )
    second = plan_nextcloud_chunked_extraction(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        documents=[RuntimeExtractionDocument.create(item, runtime_text="changed runtime body")],
        max_chunk_chars=12,
    )
    latest = AppendOnlyBigDataLedger(ledger_path).latest_state()
    extraction = latest[(item.item_id, "extraction")]
    encoded_ledger = ledger_path.read_text(encoding="utf-8")

    assert first.completed == 1
    assert first.planned == 1
    assert second.completed == 0
    assert second.skipped_existing == 1
    assert extraction.status == "completed"
    assert extraction.metadata["runtime_only"] is True
    assert extraction.metadata["chunk_count"] == 4
    assert extraction.metadata["persisted_ref_count"] == 4
    assert extraction.metadata["warning_codes"] == ["encoding_fallback_used"]
    assert "alpha secret body" not in encoded_ledger
    assert "changed runtime body" not in encoded_ledger


def test_chunked_extraction_records_retryable_state_without_raw_content(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    item = _item("notes/retry.md")
    _seed_transfer(ledger_path, item, status="pending")

    first = plan_nextcloud_chunked_extraction(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        documents=[
            RuntimeExtractionDocument.create(
                item,
                runtime_text="private runtime body",
                error="temporary parser failure token=should-redact",
            )
        ],
    )
    second = plan_nextcloud_chunked_extraction(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        documents=[
            RuntimeExtractionDocument.create(
                item,
                runtime_text="private runtime body",
                error="temporary parser failure again",
            )
        ],
    )
    record = AppendOnlyBigDataLedger(ledger_path).latest_state()[(item.item_id, "extraction")]
    encoded_ledger = ledger_path.read_text(encoding="utf-8")

    assert first.retryable == 1
    assert second.retryable == 1
    assert record.status == "retryable"
    assert record.attempt_count == 2
    assert record.last_error == "temporary parser failure again"
    assert "private runtime body" not in encoded_ledger
    assert "should-redact" not in encoded_ledger


def test_chunked_extraction_marks_empty_or_oversized_items_for_review(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    empty = _item("empty.md")
    large = _item("large.md")
    _seed_transfer(ledger_path, empty)
    _seed_transfer(ledger_path, large)

    result = plan_nextcloud_chunked_extraction(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        documents=[
            RuntimeExtractionDocument.create(empty, runtime_text=""),
            RuntimeExtractionDocument.create(large, runtime_text="abcdef", warning_codes=("size_limit_exceeded",)),
        ],
        max_chunk_chars=2,
        max_chunks_per_item=2,
    )
    latest = AppendOnlyBigDataLedger(ledger_path).latest_state()

    assert result.needs_review == 2
    assert latest[(empty.item_id, "extraction")].metadata["reason_code"] == "empty_runtime_input"
    assert latest[(large.item_id, "extraction")].metadata["reason_code"] == "chunk_limit_exceeded"
    assert latest[(large.item_id, "extraction")].metadata["truncated"] is True


def test_chunked_extraction_can_resume_after_batch_interruption(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    items = [_item(f"{index}.md") for index in range(3)]
    for item in items:
        _seed_transfer(ledger_path, item)

    documents = [RuntimeExtractionDocument.create(item, runtime_text=f"runtime {index}") for index, item in enumerate(items)]
    first = plan_nextcloud_chunked_extraction(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        documents=documents,
        batch_limit=1,
    )
    second = plan_nextcloud_chunked_extraction(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        documents=documents,
    )

    assert first.interrupted is True
    assert first.completed == 1
    assert second.completed == 2
    assert second.skipped_existing == 1


def test_chunked_extraction_skips_items_without_transfer_state(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    item = _item("unplanned.md")

    result = plan_nextcloud_chunked_extraction(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        documents=[RuntimeExtractionDocument.create(item, runtime_text="runtime body")],
    )

    assert result.planned == 0
    assert result.skipped_missing_transfer == 1

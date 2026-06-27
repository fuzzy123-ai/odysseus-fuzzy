import json

from src.bigdata_ledger_contract import AppendOnlyBigDataLedger, BigDataLedgerItem, BigDataLedgerRecord
from src.nextcloud_chunked_extraction import RuntimeExtractionDocument, plan_nextcloud_chunked_extraction
from src.nextcloud_ingestion_integration import (
    build_nextcloud_memory_abstract_metadata,
    build_nextcloud_rag_metadata,
    classify_nextcloud_ingestion_path,
)
from src.rag_vector import VectorRAG


def _item(path: str) -> BigDataLedgerItem:
    return BigDataLedgerItem(
        provider="nextcloud",
        source_id="nextcloud-main",
        relative_path=path,
        size_bytes=42,
        mtime="2026-06-22T10:00:00Z",
    )


def _seed_transfer(ledger_path, item: BigDataLedgerItem) -> None:
    AppendOnlyBigDataLedger(ledger_path).append_record(
        BigDataLedgerRecord.create(
            item,
            stage="transfer",
            status="completed",
            metadata={"planner": "test", "copy_only": True},
        )
    )


def test_ingestion_metadata_feeds_memory_and_rag_without_marker_leak():
    privacy = classify_nextcloud_ingestion_path(
        "sensitive-root/invoice.pdf",
        sensitive_roots=("sensitive-root",),
    )
    memory = build_nextcloud_memory_abstract_metadata(
        "sensitive-root/invoice.pdf",
        sensitive_roots=("sensitive-root",),
    )
    rag = build_nextcloud_rag_metadata(
        "sensitive-root/invoice.pdf",
        sensitive_roots=("sensitive-root",),
    )
    encoded = json.dumps({"memory": memory, "rag": rag, "privacy": privacy.to_metadata()}, sort_keys=True)

    assert privacy.classification == "sensitive"
    assert privacy.local_model_only is True
    assert memory["local_model_only"] is True
    assert memory["classification"] == "sensitive"
    assert rag["local_model_only"] is True
    assert rag["classification"] == "sensitive"
    assert rag["source_provider"] == "nextcloud"
    assert "sensitive-root" not in encoded


def test_chunked_extraction_records_local_only_privacy_for_sensitive_items(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    item = _item("sensitive-root/note.md")
    _seed_transfer(ledger_path, item)

    result = plan_nextcloud_chunked_extraction(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        documents=[RuntimeExtractionDocument.create(item, runtime_text="runtime text")],
        sensitive_roots=("sensitive-root",),
    )
    record = AppendOnlyBigDataLedger(ledger_path).latest_state()[(item.item_id, "extraction")]
    encoded = ledger_path.read_text(encoding="utf-8")

    assert result.completed == 1
    assert record.metadata["classification"] == "sensitive"
    assert record.metadata["local_model_only"] is True
    assert record.metadata["privacy"]["privacy_class"] == "local_sensitive"
    assert record.metadata["privacy"]["memory_write_candidate"] is True
    assert "runtime text" not in encoded
    assert "sensitive-root" not in json.dumps(record.metadata, sort_keys=True)


def test_chunked_extraction_blocks_unknown_private_until_review(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    item = _item("unknown/file.md")
    _seed_transfer(ledger_path, item)

    result = plan_nextcloud_chunked_extraction(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        documents=[RuntimeExtractionDocument.create(item, runtime_text="runtime text")],
        default_unknown_private=True,
    )
    record = AppendOnlyBigDataLedger(ledger_path).latest_state()[(item.item_id, "extraction")]

    assert result.needs_review == 1
    assert record.status == "needs_review"
    assert record.metadata["reason_code"] == "privacy_review_required"
    assert record.metadata["privacy"]["privacy_class"] == "unknown_private"
    assert record.metadata["privacy"]["memory_write_candidate"] is False
    assert "chunk_refs" not in record.metadata


def test_rag_index_accepts_nextcloud_privacy_metadata(tmp_path):
    root = tmp_path / "source"
    sensitive_dir = root / "sensitive-root"
    sensitive_dir.mkdir(parents=True)
    (sensitive_dir / "note.txt").write_text("important private note", encoding="utf-8")
    captured = []

    rag = VectorRAG.__new__(VectorRAG)
    rag._split_into_chunks = lambda text: [text]

    def fake_add_document(text, metadata):
        captured.append((text, metadata))
        return True

    rag.add_document = fake_add_document

    result = rag.index_personal_documents(
        str(root),
        owner="alice",
        metadata_provider=lambda _fpath, relative_path: build_nextcloud_rag_metadata(
            relative_path,
            sensitive_roots=("sensitive-root",),
        ),
    )

    assert result["success"] is True
    assert result["indexed_count"] == 1
    assert captured[0][0] == "important private note"
    metadata = captured[0][1]
    assert metadata["owner"] == "alice"
    assert metadata["source_provider"] == "nextcloud"
    assert metadata["privacy_class"] == "local_sensitive"
    assert metadata["classification"] == "sensitive"
    assert metadata["local_model_only"] is True
    assert metadata["required_model_scope"] == "local_only"

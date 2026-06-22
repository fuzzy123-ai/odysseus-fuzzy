import json

import pytest

from src.bigdata_ledger_contract import (
    AppendOnlyBigDataLedger,
    BigDataLedgerError,
    BigDataLedgerItem,
    BigDataLedgerRecord,
)


def _item(path="docs/file.txt", *, size=12, content_hash=""):
    return BigDataLedgerItem(
        provider="nextcloud",
        source_id="nextcloud-main",
        relative_path=path,
        size_bytes=size,
        mtime="2026-06-22T10:00:00Z",
        content_hash=content_hash,
        etag="etag-1",
    )


def test_ledger_item_and_record_roundtrip_are_metadata_only():
    item = _item(content_hash="sha256:" + "a" * 64)
    record = BigDataLedgerRecord.create(
        item,
        stage="inventory",
        status="completed",
        metadata={"mime": "text/markdown", "summary": "planning note"},
        last_error="token=super-secret-value should be redacted",
    )

    payload = record.to_dict()
    rebuilt = BigDataLedgerRecord.from_mapping(payload)
    encoded = json.dumps(payload, sort_keys=True)

    assert rebuilt.item.item_id == item.item_id
    assert rebuilt.item.version_digest() == item.version_digest()
    assert payload["metadata"] == {"mime": "text/markdown", "summary": "planning note"}
    assert payload["last_error"] == "[redacted] should be redacted"
    assert "super-secret-value" not in encoded
    assert "docs/file.txt" in encoded


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "nextcloud", "source_id": "s", "relative_path": "/abs/file.txt", "size_bytes": 1, "mtime": "2026-06-22T10:00:00Z"},
        {"provider": "nextcloud", "source_id": "s", "relative_path": "../file.txt", "size_bytes": 1, "mtime": "2026-06-22T10:00:00Z"},
        {"provider": "bad provider", "source_id": "s", "relative_path": "file.txt", "size_bytes": 1, "mtime": "2026-06-22T10:00:00Z"},
        {"provider": "nextcloud", "source_id": "s", "relative_path": "file.txt", "size_bytes": -1, "mtime": "2026-06-22T10:00:00Z"},
        {"provider": "nextcloud", "source_id": "s", "relative_path": "file.txt", "size_bytes": 1, "mtime": "bad"},
        {"provider": "nextcloud", "source_id": "s", "relative_path": "file.txt", "size_bytes": 1, "mtime": "2026-06-22T10:00:00Z", "raw_text": "private"},
    ],
)
def test_ledger_item_rejects_unsafe_fields(payload):
    with pytest.raises(BigDataLedgerError):
        BigDataLedgerItem.from_mapping(payload)


@pytest.mark.parametrize(
    "metadata",
    [
        {"content": "raw private body"},
        {"body": "raw private body"},
        {"token": "secret"},
        {"nested": {"chat_id": "123"}},
    ],
)
def test_ledger_record_rejects_raw_or_secret_metadata(metadata):
    with pytest.raises(BigDataLedgerError):
        BigDataLedgerRecord.create(_item(), stage="analysis", status="completed", metadata=metadata)


def test_append_only_ledger_replay_ignores_uncommitted_intent(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = AppendOnlyBigDataLedger(path)
    uncommitted = BigDataLedgerRecord.create(_item("docs/a.txt"), stage="transfer", status="running")
    committed = BigDataLedgerRecord.create(_item("docs/b.txt"), stage="transfer", status="completed")

    ledger.append_intent(uncommitted)
    ledger.append_record(committed)

    reloaded = AppendOnlyBigDataLedger(path)
    state = reloaded.latest_state()

    assert len(reloaded.events) == 3
    assert len(state) == 1
    assert next(iter(state.values())).item.relative_path == "docs/b.txt"
    assert reloaded.summary()["by_status"] == {"completed": 1}


def test_retry_contract_increments_attempts_and_keeps_item_queryable(tmp_path):
    ledger = AppendOnlyBigDataLedger(tmp_path / "ledger.jsonl")
    failed = BigDataLedgerRecord.create(
        _item("docs/retry.txt"),
        stage="extraction",
        status="failed",
        attempt_count=2,
        last_error="temporary parser failure",
    )
    retryable = ledger.retry_record(failed, last_error="api_key=super-secret-value retry later", next_retry_at="2026-06-22T11:00:00Z")
    ledger.append_record(retryable)

    summary = AppendOnlyBigDataLedger(tmp_path / "ledger.jsonl").summary()
    record = next(iter(ledger.latest_state().values()))

    assert record.attempt_count == 3
    assert record.status == "retryable"
    assert record.last_error == "[redacted] retry later"
    assert summary["retryable"] == 1
    assert summary["by_stage"] == {"extraction": 1}


def test_large_synthetic_inventory_replay_is_bounded(tmp_path):
    path = tmp_path / "large-ledger.jsonl"
    ledger = AppendOnlyBigDataLedger(path)
    for index in range(1500):
        record = BigDataLedgerRecord.create(
            _item(f"bulk/{index}.txt", size=index),
            stage="inventory",
            status="completed",
            metadata={"ordinal": index},
        )
        ledger.append_record(record)

    reloaded = AppendOnlyBigDataLedger(path)
    summary = reloaded.summary()
    encoded_summary = json.dumps(summary, sort_keys=True)

    assert summary["latest_records"] == 1500
    assert summary["by_stage"] == {"inventory": 1500}
    assert summary["by_status"] == {"completed": 1500}
    assert len(encoded_summary) < 500

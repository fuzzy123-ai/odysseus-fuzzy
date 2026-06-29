import json

import pytest

from src.universal_inbox_raptorgraph_store import (
    UniversalInboxRaptorGraphStoreError,
    build_universal_inbox_raptorgraph_writer,
    normalize_universal_inbox_raptorgraph_event,
)


READY_EVENT = {
    "source_hash": "a" * 64,
    "memory_record_ids": ("uix-abc",),
    "classification": "private",
    "document_type": "reference",
    "domain": "admin",
    "local_only": True,
    "dsgvo_mode": True,
    "review_reasons": ("needs_review",),
    "raw_content_stored": False,
}


def test_normalizes_redacted_raptorgraph_event():
    event = normalize_universal_inbox_raptorgraph_event(READY_EVENT)

    assert event["schema"] == "odysseus.universal_inbox.raptorgraph_event_store.v1"
    assert event["event"] == "universal_inbox_memory_write"
    assert event["event_id"].startswith("uix-rg-")
    assert event["source_hash"] == "a" * 64
    assert event["memory_record_ids"] == ("uix-abc",)
    assert event["raw_content_stored"] is False
    assert event["raw_content_visible"] is False
    assert "created_at" in event


def test_writer_appends_jsonl_and_dedupes_by_event_id(tmp_path):
    writer = build_universal_inbox_raptorgraph_writer(tmp_path)

    first = writer(READY_EVENT).to_dict()
    second = writer(READY_EVENT).to_dict()

    assert first["status"] == "written"
    assert first["duplicate"] is False
    assert second["status"] == "duplicate"
    assert second["duplicate"] is True
    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["source_hash"] == "a" * 64
    encoded = json.dumps(rows[0], ensure_ascii=False)
    assert "PRIVATE RAW TEXT" not in encoded


def test_rejects_unsafe_raw_or_secret_material():
    event = dict(READY_EVENT)
    event["review_reasons"] = ("api_key=super-secret-value",)

    normalized = normalize_universal_inbox_raptorgraph_event(event)

    assert normalized["review_reasons"] == ()


def test_rejects_bad_source_hash():
    event = dict(READY_EVENT)
    event["source_hash"] = "not-a-hash"

    with pytest.raises(UniversalInboxRaptorGraphStoreError):
        normalize_universal_inbox_raptorgraph_event(event)

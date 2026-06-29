import json

import pytest

from src.universal_inbox_analysis import build_universal_inbox_file_analysis_packet
from src.universal_inbox_memory import UniversalInboxMemoryAbstraction
from src.universal_inbox_memory_write_intent import (
    UniversalInboxMemoryWriteIntentError,
    build_universal_inbox_memory_write_intent,
)


BASE_MEMORY = {
    "source_hash": "a" * 64,
    "original_path": "AI Inbox/Incoming/reference.txt",
    "planned_path": "Documents/Private/Reference/reference.txt",
    "current_path": "Documents/Private/Reference/reference.txt",
    "routing_policy": "universal_inbox_mvp:v1",
    "confidence": 0.93,
    "review_status": "routed",
    "domain": "private",
    "document_type": "reference",
}


def _analysis(**overrides):
    item = {
        "filename": "reference.txt",
        "source_channel": "telegram",
        "classification": "private",
        "document_type": "reference",
        "extraction_status": "completed",
        "extractor": "plain_text",
    }
    item.update(overrides)
    return build_universal_inbox_file_analysis_packet(item, settings={"dsgvo_mode": False})


def test_ready_intent_builds_memory_record_and_raptor_event_without_raw_content():
    memory = UniversalInboxMemoryAbstraction.from_mapping(
        BASE_MEMORY,
        abstract={
            "summary": "User received a reference document for project planning.",
            "topics": ["project", "planning"],
            "source_material_stored": False,
        },
    )

    intent = build_universal_inbox_memory_write_intent(memory=memory, analysis=_analysis())
    payload = intent.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "ready"
    assert payload["ready_to_write"] is True
    assert payload["writes_performed"] is False
    assert payload["memory_records"][0]["source"] == "universal_inbox"
    assert payload["memory_records"][0]["metadata"]["classification"] == "private"
    assert payload["memory_records"][0]["metadata"]["raw_content_stored"] is False
    assert payload["raptorgraph_event"]["event"] == "universal_inbox_memory_write_intent"
    assert payload["raptorgraph_event"]["memory_record_ids"] == (
        payload["memory_records"][0]["memory_id"],
    )
    assert "Raw document content was not stored." in payload["memory_records"][0]["text"]
    assert "PRIVATE RAW TEXT" not in encoded


def test_sensitive_analysis_requires_review_and_creates_no_records():
    memory = UniversalInboxMemoryAbstraction.from_mapping(
        BASE_MEMORY,
        abstract={"summary": "Sensitive document abstraction only."},
    )
    analysis = _analysis(source_labels=["Privat"])

    intent = build_universal_inbox_memory_write_intent(memory=memory, analysis=analysis)
    payload = intent.to_dict()

    assert payload["status"] == "review"
    assert payload["ready_to_write"] is False
    assert payload["memory_records"] == ()
    assert payload["raptorgraph_event"]["memory_record_ids"] == ()
    assert payload["analysis_policy"]["classification"] == "sensitive"


def test_no_go_analysis_blocks_memory_and_raptor_write():
    memory = UniversalInboxMemoryAbstraction.from_mapping(
        BASE_MEMORY,
        abstract={"summary": "Blocked executable abstraction."},
    )
    analysis = _analysis(filename="setup.exe", extraction_status="blocked", dangerous=True)

    intent = build_universal_inbox_memory_write_intent(memory=memory, analysis=analysis)
    payload = intent.to_dict()

    assert payload["status"] == "blocked"
    assert payload["ready_to_write"] is False
    assert payload["memory_records"] == ()
    assert "dangerous_file_blocked" in payload["analysis_policy"]["no_go_reasons"]


def test_blocked_memory_abstraction_fields_prevent_write_intent():
    memory = UniversalInboxMemoryAbstraction.from_mapping(
        BASE_MEMORY,
        abstract={
            "summary": "Safe summary",
            "raw_text": "PRIVATE RAW TEXT",
            "nested": {"password": "secret"},
        },
    )

    intent = build_universal_inbox_memory_write_intent(memory=memory, analysis=_analysis())
    payload = intent.to_dict()

    assert payload["status"] == "blocked"
    assert payload["reason"] == "memory_abstraction_fields_blocked"
    assert payload["memory_records"] == ()


def test_suspicious_summary_is_rejected_before_memory_record_creation():
    memory = UniversalInboxMemoryAbstraction.from_mapping(
        BASE_MEMORY,
        abstract={"summary": "password = very-secret"},
    )

    with pytest.raises(UniversalInboxMemoryWriteIntentError):
        build_universal_inbox_memory_write_intent(memory=memory, analysis=_analysis())

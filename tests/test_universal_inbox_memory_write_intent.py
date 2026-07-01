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
    stamp = payload["memory_records"][0]["metadata"]["author_stamp"]
    assert stamp["action"] == "cataloged"
    assert stamp["model_id"] == "deterministic_policy_v1"
    assert stamp["source_material_stored"] is False
    assert payload["raptorgraph_event"]["event"] == "universal_inbox_memory_write_intent"
    assert payload["raptorgraph_event"]["author_stamp"]["model_id"] == "deterministic_policy_v1"
    assert payload["raptorgraph_event"]["memory_record_ids"] == (
        payload["memory_records"][0]["memory_id"],
    )
    assert "Raw document content was not stored." in payload["memory_records"][0]["text"]
    assert "PRIVATE RAW TEXT" not in encoded


def test_ready_intent_carries_redacted_maintenance_route_to_raptor_event():
    memory = UniversalInboxMemoryAbstraction.from_mapping(
        BASE_MEMORY,
        abstract={
            "summary": "User received a reference document for project planning.",
            "topics": ["project", "planning"],
            "source_material_stored": False,
        },
    )
    analysis = _analysis().to_dict()
    analysis["metadata"] = {
        **analysis["metadata"],
        "maintenance_route": {
            "schema": "odysseus.maintenance_model_policy.v1",
            "workload": "inbox_triage",
            "action": "stay_on_maintenance_model",
            "model_ref": "gemma4:e4b",
            "provider": "local_ollama",
            "local_only_required": False,
            "api_escalation_allowed": True,
            "review_required": False,
            "reason": "maintenance_model_default",
            "token_budget": 1200,
            "max_input_chars": 6000,
            "raw_content_allowed": True,
            "truth_write_allowed": True,
            "raw_text": "PRIVATE RAW TEXT",
        },
    }

    payload = build_universal_inbox_memory_write_intent(memory=memory, analysis=analysis).to_dict()
    record_route = payload["memory_records"][0]["metadata"]["maintenance_route"]
    raptor_route = payload["raptorgraph_event"]["maintenance_route"]
    encoded = json.dumps(payload, sort_keys=True)

    assert record_route["model_ref"] == "gemma4:e4b"
    assert record_route["token_budget"] == 1200
    assert record_route["raw_content_allowed"] is False
    assert record_route["truth_write_allowed"] is False
    assert raptor_route == record_route
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
    assert payload["raptorgraph_event"]["author_stamp"]["action"] == "cataloged"
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

import json

import pytest

from src.universal_inbox_memory import (
    UniversalInboxMemoryAbstraction,
    UniversalInboxMemoryError,
    to_raptorgraph_event,
)
from src.universal_inbox_routing import plan_universal_inbox_route


def test_memory_event_from_routing_decision_contains_abstraction_and_provenance_only():
    decision = plan_universal_inbox_route(
        {
            "original_path": "AI Inbox/Incoming/reference.txt",
            "domain": "private",
            "document_type": "reference",
            "title": "Reference Note",
            "confidence": 0.95,
            "source_hash": "a" * 64,
        }
    )

    memory = UniversalInboxMemoryAbstraction.from_routing_decision(
        decision,
        abstract={
            "summary": "Billing reference, no full body stored",
            "topics": ["billing", "reference"],
        },
        tags=["private", "reference"],
    )
    event = memory.to_raptorgraph_event()

    assert event["event"] == "universal_inbox_memory_abstraction"
    assert event["source_hash"] == "a" * 64
    assert event["original_path"] == "AI Inbox/Incoming/reference.txt"
    assert event["planned_path"] == "Documents/Private/Reference/reference-note.txt"
    assert event["current_path"] == "Documents/Private/Reference/reference-note.txt"
    assert event["routing_policy"] == decision.routing_policy
    assert event["confidence"] == 0.95
    assert event["review_status"] == "routed"
    assert event["abstract"]["topics"] == ("billing", "reference")
    assert event["provenance"]["planned_path"] == event["planned_path"]


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "raw_text",
        "content",
        "body",
        "payload",
        "bytes",
        "binary",
        "ocr_dump",
        "transcript",
        "full_text",
        "page_text",
        "email_body",
        "attachment_bytes",
        "secret",
        "token",
        "password",
        "api_key",
        "credential",
        "chat_id",
    ],
)
def test_forbidden_fields_are_blocked_from_serialized_event(forbidden_key):
    memory = UniversalInboxMemoryAbstraction.from_mapping(
        {
            "source_hash": "b" * 64,
            "original_path": "AI Inbox/Incoming/file.pdf",
            "planned_path": "AI Inbox/Needs Review/file.pdf",
            "current_path": "AI Inbox/Needs Review/file.pdf",
            "routing_policy": "universal_inbox_mvp:v1",
            "confidence": 0.4,
            "review_status": "needs_review",
            "domain": "private",
            "document_type": "invoice",
            "abstract": {
                "summary": "safe derived statement",
                forbidden_key: "DO-NOT-PERSIST",
                "nested": {forbidden_key: "ALSO-PRIVATE", "signal": "safe"},
            },
        }
    )

    encoded = json.dumps(memory.to_raptorgraph_event(), sort_keys=True)
    assert "DO-NOT-PERSIST" not in encoded
    assert "ALSO-PRIVATE" not in encoded
    assert f'"{forbidden_key}"' not in encoded
    assert "safe derived statement" in encoded
    assert memory.blocked_field_count == 2


def test_review_status_and_current_path_are_derived_from_routing_payload():
    decision = plan_universal_inbox_route(
        {
            "original_path": "AI Inbox/Incoming/file.pdf",
            "domain": "private",
            "document_type": "invoice",
            "title": "Low Confidence Invoice",
            "confidence": 0.2,
            "source_hash": "c" * 64,
        }
    )

    event = UniversalInboxMemoryAbstraction.from_routing_decision(decision).to_raptorgraph_event()

    assert event["review_status"] == "needs_review"
    assert event["planned_path"] == "AI Inbox/Needs Review/file.pdf"
    assert event["current_path"] == "AI Inbox/Needs Review/file.pdf"
    assert event["review_reasons"] == ("low_confidence",)


def test_top_level_helper_accepts_safe_mapping():
    event = to_raptorgraph_event(
        {
            "source_hash": "d" * 64,
            "original_path": "AI Inbox/Incoming/spec.md",
            "planned_path": "Documents/Work/Projects/odysseus/spec.md",
            "current_path": "Documents/Work/Projects/odysseus/spec.md",
            "routing_policy": "universal_inbox_mvp:v1",
            "confidence": 0.9,
            "review_status": "routed",
            "domain": "work",
            "document_type": "project",
            "abstract": {"summary": "safe model-facing abstraction"},
        }
    )

    assert event["source_hash"] == "d" * 64
    assert event["abstract"] == {"summary": "safe model-facing abstraction"}


@pytest.mark.parametrize(
    "payload",
    [
        {"source_hash": "not-a-hash"},
        {"original_path": "../outside.txt"},
        {"planned_path": "C:/Users/nkatz/private.txt"},
        {"confidence": 1.1},
    ],
)
def test_unsafe_provenance_is_rejected(payload):
    base = {
        "source_hash": "e" * 64,
        "original_path": "AI Inbox/Incoming/file.txt",
        "planned_path": "Documents/Private/Reference/file.txt",
        "current_path": "Documents/Private/Reference/file.txt",
        "routing_policy": "universal_inbox_mvp:v1",
        "confidence": 0.9,
        "review_status": "routed",
        "domain": "private",
        "document_type": "reference",
    }
    base.update(payload)

    with pytest.raises(UniversalInboxMemoryError):
        UniversalInboxMemoryAbstraction.from_mapping(base)

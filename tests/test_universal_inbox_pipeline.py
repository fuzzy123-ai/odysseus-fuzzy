import json

import pytest

from src.universal_inbox_pipeline import (
    UniversalInboxExtractionPacket,
    UniversalInboxPipelineError,
    build_universal_inbox_pipeline_run,
)
from src.universal_inbox_routing import plan_universal_inbox_route


TEST_RULES = {
    "schema": "odysseus.universal_inbox.routing_rules.v1",
    "version": 1,
    "policy_name": "universal_inbox_mvp",
    "defaults": {
        "incoming_root": "AI Inbox/Incoming",
        "review_root": "AI Inbox/Needs Review",
        "metadata_root": "AI Inbox/Metadata",
        "documents_root": "Documents",
        "min_auto_route_confidence": 0.82,
        "copy_only": True,
        "no_delete": True,
        "no_overwrite": True,
        "allowed_domains": ["private", "work"],
        "fallback_document_type": "reference",
    },
    "review_triggers": ["low_confidence", "secret_detected"],
    "routes": [
        {
            "domain": "private",
            "document_type": "reference",
            "target_template": "Documents/Private/Reference/{safe_title}{ext}",
        },
        {
            "domain": "private",
            "document_type": "invoice",
            "target_template": "Documents/Private/Invoices/{year}/{safe_title}{ext}",
        },
    ],
}


def _routing_decision(**overrides):
    item = {
        "original_path": "AI Inbox/Incoming/reference.txt",
        "domain": "private",
        "document_type": "reference",
        "title": "Reference Note",
        "confidence": 0.95,
        "source_hash": "a" * 64,
        "year": 2026,
    }
    item.update(overrides)
    return plan_universal_inbox_route(item, rules=TEST_RULES)


def test_pipeline_report_links_stage_statuses_routing_and_memory_event():
    run = build_universal_inbox_pipeline_run(
        run_id="uix-run-001",
        discovery={"status": "completed", "metadata": {"source": "offline_fixture"}},
        ledger="completed",
        extraction_packet=UniversalInboxExtractionPacket(
            abstract={"summary": "safe derived abstraction", "topics": ["reference"]},
            raw_packet={"page_count": 1},
        ),
        analysis={"status": "completed", "metadata": {"classifier": "offline"}},
        routing_decision=_routing_decision(),
    )

    report = run.to_dict()

    assert report["schema"] == "odysseus.universal_inbox.pipeline_run.v1"
    assert report["stages"]["discovery"]["status"] == "completed"
    assert report["stages"]["ledger"]["status"] == "completed"
    assert report["stages"]["extraction"]["metadata"]["ephemeral"] is True
    assert report["stages"]["extraction"]["metadata"]["persisted"] is False
    assert "extraction_packet" not in report
    assert report["routing_decision"]["target_path"] == (
        "Documents/Private/Reference/reference-note.txt"
    )
    assert report["memory_abstraction_event"]["event"] == "universal_inbox_memory_abstraction"
    assert report["memory_abstraction_event"]["abstract"] == {
        "summary": "safe derived abstraction",
        "topics": ("reference",),
    }
    assert report["policy_gate"]["status"] == "go"


def test_raw_extraction_fields_do_not_land_in_serialized_pipeline_report():
    run = build_universal_inbox_pipeline_run(
        run_id="uix-run-raw-filter",
        discovery={"status": "completed"},
        ledger={"status": "completed"},
        extraction_packet={
            "status": "completed",
            "abstract": {
                "summary": "safe short abstraction",
                "full_text": "FULL PRIVATE TEXT",
                "nested": {"email_body": "PRIVATE EMAIL"},
            },
            "raw_packet": {
                "raw_text": "PRIVATE RAW TEXT",
                "content": "PRIVATE CONTENT",
                "body": "PRIVATE BODY",
                "payload": "PRIVATE PAYLOAD",
                "bytes": "PRIVATE BYTES",
                "ocr_dump": "PRIVATE OCR",
                "token": "PRIVATE TOKEN",
            },
        },
        analysis={"status": "completed"},
        routing_decision=_routing_decision(),
    )

    encoded = json.dumps(run.to_dict(), sort_keys=True)

    for forbidden in (
        "raw_text",
        "content",
        "body",
        "payload",
        "bytes",
        "ocr_dump",
        "full_text",
        "email_body",
        "token",
        "PRIVATE RAW TEXT",
        "PRIVATE CONTENT",
        "PRIVATE BODY",
        "PRIVATE PAYLOAD",
        "PRIVATE BYTES",
        "PRIVATE OCR",
        "PRIVATE TOKEN",
        "FULL PRIVATE TEXT",
        "PRIVATE EMAIL",
    ):
        assert forbidden not in encoded
    assert "safe short abstraction" in encoded
    assert run.to_dict()["policy_gate"]["review_reasons"] == ("extraction_fields_blocked",)
    assert run.to_dict()["policy_gate"]["status"] == "review"


def test_review_reasons_are_machine_readable_from_routing_and_policy_gate():
    run = build_universal_inbox_pipeline_run(
        run_id="uix-run-review",
        discovery="completed",
        ledger="completed",
        extraction_packet={"abstract": {"summary": "safe"}},
        analysis="completed",
        routing_decision=_routing_decision(confidence=0.2),
    )

    report = run.to_dict()

    assert report["stages"]["routing"]["status"] == "needs_review"
    assert report["review_reasons"] == ("low_confidence", "routing_needs_review")
    assert report["no_go_reasons"] == ()
    assert report["policy_gate"]["status"] == "review"


def test_no_go_reasons_are_machine_readable_from_failed_stage():
    run = build_universal_inbox_pipeline_run(
        run_id="uix-run-no-go",
        discovery="completed",
        ledger={"status": "failed", "reasons": ["ledger_write_unavailable"]},
        extraction_packet={"abstract": {"summary": "safe"}},
        analysis="completed",
        routing_decision=_routing_decision(),
    )

    report = run.to_dict()

    assert report["policy_gate"]["status"] == "no_go"
    assert report["no_go_reasons"] == ("ledger_write_unavailable",)
    assert report["review_reasons"] == ()


def test_extraction_packet_must_be_ephemeral():
    with pytest.raises(UniversalInboxPipelineError):
        build_universal_inbox_pipeline_run(
            run_id="uix-run-persisted-extraction",
            discovery="completed",
            ledger="completed",
            extraction_packet={"ephemeral": False, "abstract": {"summary": "safe"}},
            analysis="completed",
            routing_decision=_routing_decision(),
        )

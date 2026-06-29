import json

from src.universal_inbox_worker import run_universal_inbox_dry_run


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
    "review_triggers": ["partial_extraction", "low_confidence"],
    "routes": [
        {
            "domain": "private",
            "document_type": "invoice",
            "target_template": "Documents/Private/Invoices/{year}/{safe_title}{ext}",
        },
        {
            "domain": "private",
            "document_type": "reference",
            "target_template": "Documents/Private/Reference/{safe_title}{ext}",
        },
    ],
}


def test_worker_dry_run_produces_redacted_go_report(tmp_path):
    inbox = tmp_path / "Incoming"
    inbox.mkdir()
    source = inbox / "reference.md"
    source.write_text("# Private body\nThis raw text must not persist.", encoding="utf-8")

    report = run_universal_inbox_dry_run(inbox, rules=TEST_RULES)
    payload = report.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "go"
    assert payload["dry_run"] is True
    assert payload["writes_performed"] is False
    assert payload["host_paths_visible"] is False
    assert payload["raw_content_visible"] is False
    assert payload["item_count"] == 1
    assert payload["items"][0]["placement_plan"]["operation"] == "copy"
    assert payload["items"][0]["placement_plan"]["delete_original"] is False
    assert payload["items"][0]["placement_plan"]["overwrite_existing"] is False
    memory_event = payload["items"][0]["pipeline_report"]["memory_abstraction_event"]
    assert memory_event["abstract"]["classification"] == "private"
    assert memory_event["abstract"]["memory_mode"] == "abstract_only"
    assert memory_event["abstract"]["source_material_stored"] is False
    assert payload["items"][0]["pipeline_report"]["stages"]["analysis"]["metadata"]["api_model_allowed"] is True
    assert "This raw text must not persist" not in encoded
    assert str(tmp_path) not in encoded


def test_worker_dry_run_routes_partial_pdf_to_review(tmp_path):
    inbox = tmp_path / "Incoming"
    inbox.mkdir()
    (inbox / "invoice.pdf").write_bytes(b"%PDF-1.4 private bytes")

    report = run_universal_inbox_dry_run(inbox, rules=TEST_RULES)
    payload = report.to_dict()

    assert payload["status"] == "partial"
    assert payload["items"][0]["extraction_status"] == "partial"
    assert "partial_extraction" in payload["items"][0]["routing_decision"]["review_reasons"]
    assert payload["items"][0]["placement_plan"]["status"] == "review"
    assert payload["no_go_reasons"] == ()


def test_worker_dry_run_discovery_warnings_make_partial_without_no_go(tmp_path):
    inbox = tmp_path / "Incoming"
    inbox.mkdir()
    (inbox / "visible.txt").write_text("visible", encoding="utf-8")
    (inbox / "upload.tmp").write_text("temporary", encoding="utf-8")

    report = run_universal_inbox_dry_run(inbox, rules=TEST_RULES)
    payload = report.to_dict()

    assert payload["status"] == "partial"
    assert payload["item_count"] == 1
    assert payload["discovery"]["warnings"][0]["code"] == "temporary_file_ignored"
    assert payload["no_go_reasons"] == ()

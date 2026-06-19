import json

import pytest

from src.universal_inbox_routing import (
    UniversalInboxRoutingError,
    load_universal_inbox_routing_rules,
    plan_universal_inbox_route,
)


def test_rules_file_loads_private_and_work_domains():
    rules = load_universal_inbox_routing_rules()

    assert rules.schema == "odysseus.universal_inbox.routing_rules.v1"
    assert rules.copy_only is True
    assert rules.no_delete is True
    assert rules.no_overwrite is True
    assert set(rules.allowed_domains) == {"private", "work"}
    assert rules.find_route("private", "invoice") is not None
    assert rules.find_route("work", "project") is not None


def test_private_invoice_routes_to_copy_only_target():
    decision = plan_universal_inbox_route(
        {
            "original_path": "AI Inbox/Incoming/stadtwerke.pdf",
            "domain": "private",
            "document_type": "invoice",
            "suggested_title": "Rechnung Stadtwerke Juni 2026",
            "routing_confidence": 0.93,
            "sha256": "a" * 64,
            "mtime": "2026-06-19T08:15:00Z",
        }
    )

    payload = decision.to_dict()
    assert payload["status"] == "routed"
    assert payload["decision"] == "copy_to_target"
    assert payload["ledger_status"] == "routed"
    assert payload["safe_operation"] == "copy"
    assert payload["delete_original"] is False
    assert payload["overwrite_existing"] is False
    assert payload["target_path"] == (
        "Documents/Private/Invoices/2026/rechnung-stadtwerke-juni-2026.pdf"
    )
    assert payload["sidecar_path"] == f"AI Inbox/Metadata/{'a' * 64}.odysseus.json"


def test_work_project_uses_project_bucket():
    decision = plan_universal_inbox_route(
        {
            "original_path": "AI Inbox/Incoming/spec.md",
            "domain": "work",
            "document_type": "project",
            "title": "Inbox Ablagelogik Rahmen",
            "project": "Odysseus Inbox",
            "confidence": 0.9,
            "source_hash": "b" * 64,
            "observed_at": "2026-06-19T09:00:00Z",
        }
    )

    assert decision.status == "routed"
    assert decision.target_path == (
        "Documents/Work/Projects/odysseus-inbox/inbox-ablagelogik-rahmen.md"
    )
    assert decision.review_reasons == ()


def test_unicode_original_name_is_allowed_but_target_is_slugged():
    decision = plan_universal_inbox_route(
        {
            "original_path": "AI Inbox/Incoming/Rechnung Müller & Söhne Juni.pdf",
            "filename": "Rechnung Müller & Söhne Juni.pdf",
            "domain": "private",
            "document_type": "invoice",
            "title": "Rechnung Müller & Söhne Juni",
            "confidence": 0.91,
            "source_hash": "e" * 64,
            "year": 2026,
        }
    )

    assert decision.original_path == "AI Inbox/Incoming/Rechnung Müller & Söhne Juni.pdf"
    assert decision.target_path == (
        "Documents/Private/Invoices/2026/rechnung-muller-sohne-juni.pdf"
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"domain": "family"}, "unknown_domain"),
        ({"document_type": "receipt"}, "unknown_document_type"),
        ({"confidence": 0.4}, "low_confidence"),
        ({"sensitive": True}, "sensitive"),
        ({"secret_detected": True}, "secret_detected"),
        ({"duplicate": True}, "duplicate"),
        ({"partial_extraction": True}, "partial_extraction"),
        ({"target_conflict": True}, "target_conflict"),
    ],
)
def test_review_triggers_route_to_needs_review(overrides, reason):
    item = {
        "original_path": "AI Inbox/Incoming/file.pdf",
        "domain": "private",
        "document_type": "invoice",
        "title": "Safe File",
        "confidence": 0.95,
        "source_hash": "c" * 64,
    }
    item.update(overrides)

    decision = plan_universal_inbox_route(item)

    assert decision.status == "needs_review"
    assert decision.decision == "copy_to_review"
    assert decision.target_path == "AI Inbox/Needs Review/file.pdf"
    assert decision.ledger_status == "needs_review"
    assert reason in decision.review_reasons


def test_unsafe_rule_paths_are_rejected():
    with pytest.raises(UniversalInboxRoutingError):
        load_universal_inbox_routing_rules().from_dict(
            {
                "schema": "odysseus.universal_inbox.routing_rules.v1",
                "version": 1,
                "policy_name": "bad",
                "defaults": {
                    "incoming_root": "AI Inbox/Incoming",
                    "review_root": "AI Inbox/Needs Review",
                    "metadata_root": "AI Inbox/Metadata",
                    "documents_root": "Documents",
                    "allowed_domains": ["private"],
                    "copy_only": True,
                    "no_delete": True,
                    "no_overwrite": True,
                },
                "routes": [
                    {
                        "domain": "private",
                        "document_type": "invoice",
                        "target_template": "../escape/{safe_title}{ext}",
                    }
                ],
            }
        )


def test_raptorgraph_payload_is_provenance_only():
    decision = plan_universal_inbox_route(
        {
            "original_path": "AI Inbox/Incoming/reference.txt",
            "domain": "private",
            "document_type": "reference",
            "title": "Reference Note",
            "confidence": 0.95,
            "source_hash": "d" * 64,
            "content": "private body must not be copied into routing decision",
            "raw_text": "private body must not be copied into routing decision",
        }
    )

    encoded = json.dumps(decision.to_dict(), sort_keys=True)
    event = decision.raptorgraph_event or {}
    assert event["event"] == "document_routing_planned"
    assert event["source_provider"] == "nextcloud_inbox"
    assert event["permission_scope"] == "no_delete:copy_only:no_overwrite"
    assert event["source_hash"] == "d" * 64
    assert event["original_path"] == "AI Inbox/Incoming/reference.txt"
    assert event["planned_path"] == "Documents/Private/Reference/reference-note.txt"
    assert "private body" not in encoded
    assert decision.private_content_visible is False
    assert decision.secret_values_visible is False

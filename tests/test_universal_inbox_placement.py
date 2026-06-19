from src.universal_inbox_placement import build_universal_inbox_placement_plan
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
    "review_triggers": ["low_confidence", "target_conflict"],
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


def _decision(**overrides):
    item = {
        "original_path": "AI Inbox/Incoming/invoice.pdf",
        "domain": "private",
        "document_type": "invoice",
        "title": "Invoice June",
        "confidence": 0.95,
        "source_hash": "a" * 64,
        "year": 2026,
    }
    item.update(overrides)
    return plan_universal_inbox_route(item, rules=TEST_RULES)


def test_placement_plan_from_routing_decision_is_copy_only_dry_run():
    plan = build_universal_inbox_placement_plan(_decision())

    payload = plan.to_dict()

    assert payload["schema"] == "odysseus.universal_inbox.placement_plan.v1"
    assert payload["status"] == "planned"
    assert payload["operation"] == "copy"
    assert payload["copy_only"] is True
    assert payload["delete_original"] is False
    assert payload["overwrite_existing"] is False
    assert payload["dry_run"] is True
    assert payload["writes_performed"] is False
    assert payload["original_path"] == "AI Inbox/Incoming/invoice.pdf"
    assert payload["target_path"] == "Documents/Private/Invoices/2026/invoice-june.pdf"
    assert payload["sidecar_path"] == f"AI Inbox/Metadata/{'a' * 64}.odysseus.json"
    assert payload["reasons"] == ()


def test_placement_plan_accepts_safe_mapping():
    plan = build_universal_inbox_placement_plan(
        {
            "original_path": "AI Inbox/Incoming/reference.txt",
            "target_path": "Documents/Private/Reference/reference.txt",
            "sidecar_path": "AI Inbox/Metadata/ref.odysseus.json",
            "safe_operation": "copy",
            "copy_only": True,
            "delete_original": False,
            "overwrite_existing": False,
        }
    )

    assert plan.status == "planned"
    assert plan.operation == "copy"
    assert plan.no_go_reasons == ()


def test_target_conflict_becomes_review_without_overwrite():
    plan = build_universal_inbox_placement_plan(_decision(target_conflict=True))

    payload = plan.to_dict()

    assert payload["status"] == "review"
    assert "target_conflict" in payload["review_reasons"]
    assert payload["no_go_reasons"] == ()
    assert payload["operation"] == "copy"
    assert payload["overwrite_existing"] is False


def test_destructive_or_overwrite_inputs_become_no_go_but_plan_operation_stays_copy():
    plan = build_universal_inbox_placement_plan(
        {
            "original_path": "AI Inbox/Incoming/file.pdf",
            "target_path": "Documents/Private/Reference/file.pdf",
            "sidecar_path": "AI Inbox/Metadata/file.odysseus.json",
            "operation": "move",
            "delete_original": True,
            "overwrite_existing": True,
        }
    )

    payload = plan.to_dict()

    assert payload["status"] == "no_go"
    assert payload["operation"] == "copy"
    assert payload["delete_original"] is False
    assert payload["overwrite_existing"] is False
    assert payload["no_go_reasons"] == (
        "destructive_operation",
        "delete_original",
        "overwrite_existing",
    )


def test_unsafe_paths_become_no_go_and_are_not_serialized():
    plan = build_universal_inbox_placement_plan(
        {
            "original_path": "../outside.pdf",
            "target_path": "C:/Users/nkatz/private.pdf",
            "sidecar_path": "AI Inbox/Metadata/file.odysseus.json",
            "safe_operation": "copy",
        }
    )

    payload = plan.to_dict()

    assert payload["status"] == "no_go"
    assert payload["original_path"] == ""
    assert payload["target_path"] == ""
    assert payload["sidecar_path"] == "AI Inbox/Metadata/file.odysseus.json"
    assert payload["no_go_reasons"] == ("unsafe_original_path", "unsafe_target_path")
    assert ".." not in str(payload)
    assert "C:/Users" not in str(payload)

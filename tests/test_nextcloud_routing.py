import json

import pytest

from src.nextcloud_intake_ledger import NextcloudIntakeLedgerEntry, compute_content_hash
from src.nextcloud_routing import (
    build_nextcloud_routing_decision,
    build_nextcloud_safe_placement_plan,
)


def _entry(
    status: str = "analyzed",
    *,
    text: str = "invoice",
    metadata: dict | None = None,
    errors: tuple[str, ...] = (),
) -> NextcloudIntakeLedgerEntry:
    return NextcloudIntakeLedgerEntry(
        digest=compute_content_hash(text),
        path=f"inbox/{text}.pdf",
        size=123,
        mtime="2026-06-29T08:00:00Z",
        status=status,
        actor="nextcloud.bot",
        permission_scope="metadata_only:review",
        metadata=metadata or {},
        errors=errors,
    )


def test_routing_decision_places_confident_item_without_private_payload() -> None:
    decision = build_nextcloud_routing_decision(
        _entry(
            metadata={
                "document_type": "invoice",
                "target_area": "Finance",
                "confidence": 0.91,
                "raw_text": "private body must not survive",
                "api_key": "secret-test-sentinel",
            }
        ),
        tag_candidates=[{"tag": "decision", "tag_class": "semantic", "confidence": 0.95}],
    )
    payload = decision.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "routable"
    assert payload["review_required"] is False
    assert payload["review_reasons"] == ()
    assert payload["target_path"] == "Archive/Finance/invoice.pdf"
    assert payload["projected_tags"] == ("decision",)
    assert "private body must not survive" not in encoded
    assert "secret-test-sentinel" not in encoded


def test_sensitive_low_confidence_or_partial_items_go_to_review() -> None:
    decision = build_nextcloud_routing_decision(
        _entry(
            "needs_review",
            metadata={
                "privacy_class": "local_sensitive",
                "confidence": 0.4,
                "partial": True,
                "target_conflict": True,
            },
            errors=("extractor failed",),
        ),
        tag_candidates=[{"tag": "freeform", "tag_class": "semantic", "confidence": 0.9}],
    )

    assert decision.review_required is True
    assert decision.status == "needs_review"
    assert decision.target_path == "Review-Queue/unsorted/invoice.pdf"
    assert decision.review_reasons == (
        "status_needs_review",
        "ledger_errors_present",
        "low_confidence",
        "sensitive_or_secret_class",
        "partial_extraction",
        "existing_target_conflict",
        "uncertain_target",
        "tag_policy_review",
    )


def test_safe_placement_plan_is_dry_run_copy_sidecar_and_tag_projection() -> None:
    decision = build_nextcloud_routing_decision(
        _entry(metadata={"target_area": "Projects", "confidence": 0.86}),
        tag_candidates=[{"tag": "task", "tag_class": "semantic", "confidence": 0.9}],
    )

    plan = build_nextcloud_safe_placement_plan(decision)
    payload = plan.to_dict()

    assert payload["execution_allowed"] is False
    assert payload["blocked_actions"] == ("delete", "move", "overwrite", "occ_admin")
    assert [action["action"] for action in payload["actions"]] == [
        "copy",
        "write_sidecar",
        "project_tags",
    ]
    assert payload["actions"][0]["source_path"] == "inbox/invoice.pdf"
    assert payload["actions"][0]["target_path"] == "Archive/Projects/invoice.pdf"
    assert payload["actions"][1]["target_path"] == "Archive/Projects/invoice.pdf.odysseus.json"


def test_placement_rejects_absolute_or_traversal_paths() -> None:
    with pytest.raises(ValueError):
        build_nextcloud_safe_placement_plan(
            {
                "digest": compute_content_hash("bad"),
                "source_path": "../private.pdf",
                "target_path": "Archive/private.pdf",
                "status": "routable",
                "confidence": 1.0,
                "review_required": False,
            }
        )

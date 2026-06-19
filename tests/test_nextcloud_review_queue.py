import json

import pytest

from src.nextcloud_intake_ledger import NextcloudIntakeLedgerEntry, compute_content_hash
from src.nextcloud_review_queue import (
    build_review_queue,
    build_review_queue_item,
    summarize_review_queue,
)


def _entry(
    status: str,
    *,
    text: str = "item",
    errors: tuple[str, ...] = (),
    metadata: dict | None = None,
) -> NextcloudIntakeLedgerEntry:
    return NextcloudIntakeLedgerEntry(
        digest=compute_content_hash(text),
        path=f"inbox/{text}.txt",
        size=42,
        mtime="2026-06-19T08:15:00Z",
        status=status,
        actor="review.bot",
        permission_scope="metadata_only:review",
        errors=errors,
        metadata=metadata or {},
    )


def test_review_item_uses_redacted_metadata_keys_without_private_values() -> None:
    entry = _entry(
        "needs_review",
        text="contract",
        errors=("authorization=<redacted-test-sentinel>",),
        metadata={
            "title": "safe title",
            "raw_text": "private body should never be in review packets",
            "api_key": "<redacted-test-sentinel>",
        },
    )

    item = build_review_queue_item(entry)
    payload = item.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "needs_review"
    assert payload["reasons"] == ("status_needs_review", "ledger_errors_present")
    assert payload["suggested_actions"] == ("review", "copy_to_review")
    assert payload["metadata_keys"] == ("api_key", "raw_text", "title")
    assert payload["private_content_visible"] is False
    assert payload["secret_values_visible"] is False
    assert "private body should never be in review packets" not in encoded
    assert "redacted-test-sentinel" not in encoded
    assert "safe title" not in encoded


def test_review_queue_filters_route_ready_items_by_default() -> None:
    route_ready = _entry("routed_indexed", text="done", metadata={"summary": "ok"})
    denied = _entry("permission_denied", text="denied", errors=("access denied",))

    queue = build_review_queue([route_ready, denied])

    assert len(queue) == 1
    assert queue[0].status == "permission_denied"
    assert queue[0].suggested_actions == ("review", "request_metadata", "defer")


def test_review_queue_can_include_route_ready_items_for_operator_overview() -> None:
    route_ready = _entry("routed_indexed", text="done", metadata={"summary": "ok"})

    queue = build_review_queue([route_ready], include_route_ready=True)

    assert queue[0].reasons == ("review_not_required",)
    assert queue[0].suggested_actions == ("skip",)


def test_review_queue_summary_is_counts_and_items_only() -> None:
    queue = build_review_queue(
        [
            _entry("needs_review", text="a"),
            _entry("failed", text="b", errors=("token=<redacted-test-sentinel>",)),
        ]
    )

    summary = summarize_review_queue(queue)
    encoded = json.dumps(summary, sort_keys=True)

    assert summary["total"] == 2
    assert summary["by_status"] == {"failed": 1, "needs_review": 1}
    assert summary["by_action"] == {
        "copy_to_review": 1,
        "defer": 1,
        "request_metadata": 1,
        "review": 2,
    }
    assert summary["private_content_visible"] is False
    assert summary["secret_values_visible"] is False
    assert "redacted-test-sentinel" not in encoded


def test_invalid_review_action_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_review_queue_item(_entry("needs_review"), suggested_actions=["delete"])

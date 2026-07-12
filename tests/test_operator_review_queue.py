import json

from src.operator_dashboard import (
    OPERATOR_REVIEW_QUEUE_SCHEMA,
    SUPPORTED_REVIEW_FAMILIES,
    build_operator_review_queue,
)


def test_operator_review_queue_normalizes_review_gates_without_raw_values():
    payload = build_operator_review_queue(
        review_gate_status={
            "schema": "odysseus.review_gate_state.v1",
            "gates": [
                {
                    "id": "nextcloud_copy",
                    "family": "nextcloud",
                    "state": "pending_review",
                    "reason": "nextcloud_review_candidates",
                    "source_ref": "nextcloud:/private/path/secret.pdf",
                    "metadata": {"path": "C:/Users/private/secret.pdf"},
                },
                {
                    "id": "memory_write",
                    "family": "memory",
                    "state": "ready_to_write",
                    "reason": "policy_allows_abstract_memory_write",
                    "source_ref": "memory:private-note",
                    "metadata": {"raw_content": "PRIVATE NOTE"},
                },
                {
                    "id": "raptorgraph_write",
                    "family": "raptorgraph",
                    "state": "blocked",
                    "reason": "memory_write_gate_not_open",
                    "source_ref": "graph:private-node",
                },
                {
                    "id": "file_export",
                    "family": "export",
                    "state": "no_pending",
                    "reason": "done",
                },
            ],
        }
    )
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == OPERATOR_REVIEW_QUEUE_SCHEMA
    assert payload["status"] == "blocked"
    assert payload["item_count"] == 3
    assert payload["pending_count"] == 2
    assert payload["blocked_count"] == 1
    assert payload["supported_families"] == SUPPORTED_REVIEW_FAMILIES
    assert payload["live_action_enabled"] is False
    assert payload["write_action_enabled"] is False
    families = {item["family"] for item in payload["items"]}
    assert {"nextcloud_copy", "memory_write", "raptorgraph_write"} <= families
    memory = next(item for item in payload["items"] if item["family"] == "memory_write")
    assert memory["required_gate"] == "uix-memory-write-go"
    assert memory["next_action"] == "request_bounded_live_go"
    assert memory["write_action_enabled"] is False
    assert memory["source_ref_visible"] is False
    graph = next(item for item in payload["items"] if item["family"] == "raptorgraph_write")
    assert graph["next_action"] == "fix_blocker"
    assert "nextcloud:/private/path/secret.pdf" not in encoded
    assert "C:/Users/private" not in encoded
    assert "PRIVATE NOTE" not in encoded
    assert "memory:private-note" not in encoded


def test_operator_review_queue_adds_live_and_generic_review_items_as_preview_only():
    payload = build_operator_review_queue(
        live_affordance_readiness={
            "status": "ready",
            "affordances": [
                {
                    "id": "telegram-send",
                    "family": "telegram_delivery",
                    "status": "needs_go",
                    "required_gate": "TGR-LIVE-SEND-GO",
                    "target_url": "https://api.telegram.org/botSECRET/sendMessage",
                }
            ],
        },
        coding_approvals=[
            {
                "id": "publish-pr",
                "status": "pending_review",
                "proposed_action": "publish private branch",
                "why": "tests passed",
                "risk": "unreviewed code publication",
                "source_ref": "branch:secret-feature",
            }
        ],
        security_reviews=[
            {
                "id": "rotate-token",
                "status": "blocked",
                "reason": "token leaked in private log",
                "source_ref": "security:secret",
            }
        ],
    )
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "blocked"
    assert payload["item_count"] == 3
    assert payload["blocked_count"] == 1
    assert any(item["family"] == "telegram_delivery" for item in payload["items"])
    assert any(item["family"] == "coding_approval" for item in payload["items"])
    assert any(item["family"] == "security_action" for item in payload["items"])
    assert all(item["live_action_enabled"] is False for item in payload["items"])
    assert all(item["write_action_enabled"] is False for item in payload["items"])
    assert "api.telegram.org" not in encoded
    assert "SECRET" not in encoded
    assert "secret-feature" not in encoded
    assert "token leaked" not in encoded


def test_operator_review_queue_is_clear_for_empty_inputs():
    payload = build_operator_review_queue()

    assert payload["status"] == "clear"
    assert payload["item_count"] == 0
    assert payload["pending_count"] == 0
    assert payload["blocked_count"] == 0
    assert payload["items"] == ()

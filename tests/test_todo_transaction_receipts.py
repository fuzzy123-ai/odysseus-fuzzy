from copy import deepcopy

import pytest

from src.agent_loop import _todo_tool_event_forward
from src.todo_transaction_receipts import (
    TODO_RECEIPT_FIELD,
    TODO_SEMANTIC_RECEIPT_SCHEMA,
    attach_todo_semantic_receipt,
    project_todo_semantic_receipt,
    todo_semantic_event,
    validated_todo_semantic_receipt_from_event,
)
from src.todo_digest_receipts import TODO_DIGEST_RECEIPT_FIELD, build_todo_digest_membership_receipt


def _mutation_result(action, *, transaction_status="committed"):
    current_state = {"add": False, "complete": True, "reopen": False, "remove": None}[action]
    previous_state = False if action in {"complete", "reopen", "remove"} else None
    return {
        "status": "ok",
        "action": action,
        "exit_code": 0,
        "operation": action,
        "verified": True,
        "transaction_status": transaction_status,
        "open_count": 2,
        "previous_state": previous_state,
        "current_state": current_state,
        "evidence_refs_redacted": (
            "owner:0123456789abcdef",
            "list:fedcba9876543210",
            "item:0011223344556677",
            f"operation:{action}",
        ),
        "list_ref": "private-list-id",
        "item_ref": "private-item-id",
        "text": "private todo text",
    }


def test_projects_closed_content_free_receipts_for_list_and_all_mutations():
    list_receipt = project_todo_semantic_receipt(
        "list",
        {
            "status": "ok",
            "action": "list",
            "exit_code": 0,
            "open_count": 2,
            "evidence_refs_redacted": (
                "owner:0123456789abcdef",
                "list:fedcba9876543210",
                "operation:list",
            ),
            "list_ref": "private-list-id",
            "items": [{"item_ref": "private-item-id", "text": "private todo text"}],
        },
    )
    assert list_receipt["claim_type"] == "todo_list_read"
    assert list_receipt["transaction_status"] == "read_verified"
    assert list_receipt["evidence_refs"] == (
        "owner:0123456789abcdef",
        "list:fedcba9876543210",
        "operation:list",
    )
    assert "private" not in repr(list_receipt)

    for action, claim in (
        ("add", "todo_item_created"),
        ("complete", "todo_item_completed"),
        ("reopen", "todo_item_reopened"),
        ("remove", "todo_item_removed"),
    ):
        receipt = project_todo_semantic_receipt(action, _mutation_result(action))
        assert receipt["schema"] == TODO_SEMANTIC_RECEIPT_SCHEMA
        assert receipt["claim_type"] == claim
        assert receipt["action"] == receipt["operation"] == action
        assert receipt["verified"] is True
        assert "private" not in repr(receipt)


def test_idempotent_add_receipt_allows_a_completed_existing_item():
    result = _mutation_result("add", transaction_status="idempotent_noop")
    result["current_state"] = True

    receipt = project_todo_semantic_receipt("add", result)

    assert receipt["transaction_status"] == "idempotent_noop"
    assert receipt["current_state"] is True


@pytest.mark.parametrize("action", ["complete", "reopen", "remove"])
def test_non_add_mutations_reject_impossible_idempotent_noop_status(action):
    assert project_todo_semantic_receipt(
        action, _mutation_result(action, transaction_status="idempotent_noop")
    ) is None


@pytest.mark.parametrize(
    ("transaction_status", "current_state"),
    [("committed", True), ("idempotent_noop", None)],
)
def test_add_rejects_impossible_status_and_state_pairs(transaction_status, current_state):
    result = _mutation_result("add", transaction_status=transaction_status)
    result["current_state"] = current_state

    assert project_todo_semantic_receipt("add", result) is None


@pytest.mark.parametrize("exit_code", [False, 0.0, "0"])
def test_semantic_receipts_require_an_exact_integer_zero_exit_code(exit_code):
    result = _mutation_result("add")
    result["exit_code"] = exit_code

    assert project_todo_semantic_receipt("add", result) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda result: result.update(status="rejected"),
        lambda result: result.update(action="reopen"),
        lambda result: result.update(exit_code=1),
        lambda result: result.update(operation="reopen"),
        lambda result: result.update(verified="true"),
        lambda result: result.update(transaction_status="failed"),
        lambda result: result.update(open_count=True),
        lambda result: result.update(open_count=-1),
        lambda result: result.update(current_state="false"),
        lambda result: result.update(evidence_refs_redacted=("operation:add", "private todo text")),
    ],
)
def test_rejects_malformed_or_private_mutation_receipts(mutate):
    result = _mutation_result("add")
    mutate(result)

    assert project_todo_semantic_receipt("add", result) is None


@pytest.mark.parametrize(
    "refs",
    [
        ("operation:add",),
        ("owner:0123456789abcdef", "list:fedcba9876543210", "operation:add"),
        (
            "owner:0123456789abcdef",
            "list:fedcba9876543210",
            "item:0011223344556677",
            "operation:add",
            "operation:add",
        ),
        (
            "owner:0123456789abcdef",
            "list:fedcba9876543210",
            "item:0011223344556677",
            "item:0011223344556677",
        ),
        (
            "list:fedcba9876543210",
            "owner:0123456789abcdef",
            "item:0011223344556677",
            "operation:add",
        ),
        (
            "owner:0123456789abcdef",
            "list:fedcba9876543210",
            "item:0011223344556677",
            "operation:complete",
        ),
    ],
)
def test_mutation_requires_exact_unique_canonical_evidence_roles(refs):
    result = _mutation_result("add")
    result["evidence_refs_redacted"] = refs

    assert project_todo_semantic_receipt("add", result) is None


@pytest.mark.parametrize(
    "result",
    [
        {"status": "ok", "action": "list", "exit_code": 0, "open_count": "2"},
        {"status": "ok", "action": "list", "exit_code": 0, "open_count": -1},
        {"status": "ok", "action": "list", "exit_code": 0, "open_count": 1_000_001},
    ],
)
def test_list_requires_a_bounded_integer_open_count(result):
    assert project_todo_semantic_receipt("list", result) is None


@pytest.mark.parametrize(
    "refs",
    [
        ("operation:list",),
        ("owner:0123456789abcdef", "operation:list"),
        ("owner:0123456789abcdef", "list:fedcba9876543210", "operation:list", "item:0011223344556677"),
        ("owner:0123456789abcdef", "owner:0123456789abcdef", "operation:list"),
        ("list:fedcba9876543210", "owner:0123456789abcdef", "operation:list"),
    ],
)
def test_list_requires_exact_owner_list_operation_evidence(refs):
    assert project_todo_semantic_receipt(
        "list",
        {
            "status": "ok",
            "action": "list",
            "exit_code": 0,
            "open_count": 2,
            "evidence_refs_redacted": refs,
        },
    ) is None


def test_facade_derives_list_evidence_without_retaining_raw_owner_or_list_refs():
    result = attach_todo_semantic_receipt(
        {
            "status": "ok",
            "action": "list",
            "exit_code": 0,
            "open_count": 2,
            "list_ref": "private-list-id",
        },
        "list",
        owner="alice",
        list_ref="private-list-id",
    )

    receipt = result[TODO_RECEIPT_FIELD]
    assert receipt["transaction_status"] == "read_verified"
    assert receipt["evidence_refs"] == (
        "owner:2bd806c97f0e00af",
        "list:bb400c2a12242213",
        "operation:list",
    )
    assert "private-list-id" not in repr(receipt)
    assert TODO_RECEIPT_FIELD not in attach_todo_semantic_receipt(
        {
            "status": "ok",
            "action": "list",
            "exit_code": 0,
            "open_count": 2,
            "list_ref": "private-list-id",
        },
        "list",
        owner=" alice ",
        list_ref="private-list-id",
    )


@pytest.mark.parametrize("snapshot_list_ref", [None, "other-list-id"])
def test_facade_list_receipt_requires_an_exact_snapshot_list_ref(snapshot_list_ref):
    result = {"status": "ok", "action": "list", "exit_code": 0, "open_count": 2}
    if snapshot_list_ref is not None:
        result["list_ref"] = snapshot_list_ref

    attached = attach_todo_semantic_receipt(
        result,
        "list",
        owner="alice",
        list_ref="private-list-id",
    )

    assert TODO_RECEIPT_FIELD not in attached


def test_attach_and_agent_forwarder_keep_only_canonical_action_and_receipt():
    result = attach_todo_semantic_receipt(_mutation_result("complete"), "complete")

    event = todo_semantic_event(result)
    forwarded = _todo_tool_event_forward(result)

    assert event == forwarded
    assert set(event) == {"tool", "action", TODO_RECEIPT_FIELD}
    assert event["action"] == "complete"
    assert validated_todo_semantic_receipt_from_event(event) == event[TODO_RECEIPT_FIELD]
    assert "private" not in repr(event)


def test_generic_success_and_tampered_semantic_events_are_not_todo_evidence():
    assert todo_semantic_event({"status": "ok", "action": "complete"}) is None

    result = attach_todo_semantic_receipt(_mutation_result("reopen"), "reopen")
    event = todo_semantic_event(result)
    tampered = deepcopy(event)
    tampered["action"] = "complete"
    assert validated_todo_semantic_receipt_from_event(tampered) is None


def test_closed_history_keeps_only_a_valid_digest_postcondition():
    result = attach_todo_semantic_receipt(_mutation_result("add"), "add")
    semantic = result[TODO_RECEIPT_FIELD]
    digest = build_todo_digest_membership_receipt(
        action="add", evidence_refs=semantic["evidence_refs"],
        current_state={"exists": True, "done": False}, included=True,
        selection_position=0, open_item_count=1, selected_open_item_count=1, limit=20,
        label_filter_active=False, list_filter_active=False, builder_date="2026-07-24",
        snapshot_manifest={
            "schema": "odysseus.todo_digest_snapshot.v1", "builder_date": "2026-07-24",
            "builder_clock": "naive_local", "limit": 20, "label_filter_active": False,
            "list_filter_active": False,
            "selected": [{"list_ref": semantic["evidence_refs"][1], "item_ref": semantic["evidence_refs"][2], "position": 0, "done": False}],
        },
    )
    result[TODO_DIGEST_RECEIPT_FIELD] = digest

    event = todo_semantic_event(result)

    assert event[TODO_DIGEST_RECEIPT_FIELD] == digest
    result[TODO_DIGEST_RECEIPT_FIELD] = {"private_text": "nope"}
    assert TODO_DIGEST_RECEIPT_FIELD not in todo_semantic_event(result)

    tampered = deepcopy(event)
    tampered[TODO_RECEIPT_FIELD]["evidence_refs"] = ("operation:reopen", "C:/private")
    assert validated_todo_semantic_receipt_from_event(tampered) is None


@pytest.mark.parametrize("status", ["rejected", "confirmation_required", "unexpected"])
def test_invalid_todo_history_events_are_closed_and_content_free(status):
    forwarded = _todo_tool_event_forward(
        {
            "status": status,
            "action": "complete",
            "error": "private exception details",
            "candidate_refs": ["private-item-id"],
            "output": "private command output",
        }
    )

    expected_status = status if status in {"rejected", "confirmation_required"} else "invalid"
    assert forwarded == {
        "tool": "manage_todos",
        "action": "complete",
        "semantic_status": expected_status,
    }
    assert "private" not in repr(forwarded)

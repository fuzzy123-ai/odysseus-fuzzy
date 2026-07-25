import pytest

from src.todo_receipts import todo_receipts_from_tool_result
from src.tool_transaction_ledger import (
    ToolTransaction,
    ToolTransactionError,
    ToolTransactionStatus,
    transaction_evidence_for_claim,
    transaction_from_tool_event,
    transactions_from_tool_events,
)


def test_successful_test_event_becomes_evidence_bound_transaction():
    transactions = transactions_from_tool_events([
        {
            "tool": "bash",
            "command": "python -m pytest tests/test_demo.py",
            "output": "1 passed",
            "exit_code": 0,
        }
    ])

    command_txs = [tx for tx in transactions if tx.claim_type == "command_passed"]

    assert command_txs
    assert command_txs[0].status == ToolTransactionStatus.SUCCEEDED
    assert command_txs[0].verified_done is True
    assert command_txs[0].raw_content_visible is False
    assert "1 passed" not in repr(command_txs[0].to_dict())


def test_failed_or_blocked_transactions_are_never_verified_done():
    failed = ToolTransaction.create(
        surface="agent",
        tool="bash",
        claim_type="command_passed",
        status="failed",
        evidence_refs=["exit_code:1"],
        exit_code=1,
        command="python -m pytest",
    )
    blocked = ToolTransaction.create(
        surface="agent",
        tool="telegram_reply",
        claim_type="telegram_sent",
        status="blocked",
        evidence_refs=["exit_code:1"],
        exit_code=1,
        command="telegram_reply",
    )

    assert failed.verified_done is False
    assert blocked.verified_done is False
    assert transaction_evidence_for_claim([failed.to_dict(), blocked.to_dict()], "command_passed") == ()


def test_telegram_artifact_transaction_can_support_exact_artifact_claim():
    transactions = transactions_from_tool_events([
        {
            "tool": "telegram_document_reply",
            "command": "send photo",
            "output": "sent ok data/reports/autonomous_coding_agent/pong/screen.png",
            "exit_code": 0,
            "artifact_refs": ["data/reports/autonomous_coding_agent/pong/screen.png"],
        }
    ])

    evidence = transaction_evidence_for_claim(
        [tx.to_dict() for tx in transactions],
        "artifact_exists",
        ["screen.png"],
    )

    assert evidence
    assert evidence[0] == "data/reports/autonomous_coding_agent/pong/screen.png"


@pytest.mark.parametrize(
    "artifact_ref",
    [
        "C:/Users/private/out.log",
        "/home/private/out.log",
        "../private/out.log",
        "data/reports/token=secret.txt",
    ],
)
def test_transaction_rejects_unsafe_artifact_refs(artifact_ref):
    with pytest.raises(ToolTransactionError):
        ToolTransaction.create(
            surface="agent",
            tool="bash",
            claim_type="artifact_exists",
            status="verified",
            evidence_refs=["exit_code:0"],
            artifact_refs=[artifact_ref],
            exit_code=0,
        )


def test_valid_todo_semantic_event_creates_exact_verified_claim_transaction():
    event = {
        "tool": "manage_todos",
        "action": "complete",
        "todo_semantic_receipt": {
            "schema": "odysseus.todo_semantic_receipt.v1",
            "action": "complete",
            "operation": "complete",
            "claim_type": "todo_item_completed",
            "verified": True,
            "transaction_status": "committed",
            "open_count": 0,
            "previous_state": False,
            "current_state": True,
            "evidence_refs": (
                "owner:0123456789abcdef",
                "list:fedcba9876543210",
                "item:0011223344556677",
                "operation:complete",
            ),
        },
    }

    transactions = transactions_from_tool_events([event])

    assert len(transactions) == 1
    assert transactions[0].claim_type == "todo_item_completed"
    assert transactions[0].status == ToolTransactionStatus.VERIFIED
    assert transactions[0].verified_done is True


def test_valid_canonical_todo_receipt_has_priority_over_invalid_legacy_payloads():
    legacy_add = todo_receipts_from_tool_result(
        {
            "action": "add",
            "operation": "add",
            "list_ref": "todo-list:v1:0123456789abcdef:list-alpha",
            "item_ref": "todo-item:v1:itm_0123456789abcdef",
            "previous_state": {"exists": False, "done": None},
            "current_state": {"exists": True, "done": False},
            "open_count": 0,
            "transaction_status": "committed",
            "verified": True,
            "evidence_refs": ["notes-readback:v1:abc123def456"],
            "exit_code": 0,
        }
    )[0].to_dict()
    event = {
        "tool": "manage_todos",
        "action": "complete",
        "todo_semantic_receipt": {
            "schema": "odysseus.todo_semantic_receipt.v1",
            "action": "complete",
            "operation": "complete",
            "claim_type": "todo_item_completed",
            "verified": True,
            "transaction_status": "committed",
            "open_count": 0,
            "previous_state": False,
            "current_state": True,
            "evidence_refs": (
                "owner:0123456789abcdef",
                "list:fedcba9876543210",
                "item:0011223344556677",
                "operation:complete",
            ),
        },
        "todo_receipts": [{"operation": "add"}],
    }

    invalid_canonical_with_legacy_downgrade = {
        "tool": "manage_todos",
        "action": "add",
        "exit_code": 0,
        "todo_semantic_receipt": {},
        "todo_receipts": [legacy_add],
    }

    transactions = transactions_from_tool_events([event])

    assert len(transactions) == 1
    assert transactions[0].claim_type == "todo_item_completed"
    assert transactions_from_tool_events([invalid_canonical_with_legacy_downgrade]) == ()
    with pytest.raises(ToolTransactionError, match="valid semantic receipt"):
        transaction_from_tool_event(invalid_canonical_with_legacy_downgrade)


def test_read_verified_todo_list_receipt_creates_a_verified_read_claim():
    event = {
        "tool": "manage_todos",
        "action": "list",
        "todo_semantic_receipt": {
            "schema": "odysseus.todo_semantic_receipt.v1",
            "action": "list",
            "operation": "list",
            "claim_type": "todo_list_read",
            "verified": True,
            "transaction_status": "read_verified",
            "open_count": 0,
            "previous_state": None,
            "current_state": None,
            "evidence_refs": (
                "owner:0123456789abcdef",
                "list:fedcba9876543210",
                "operation:list",
            ),
        },
    }

    transaction = transactions_from_tool_events([event])[0]

    assert transaction.claim_type == "todo_list_read"
    assert transaction.status == ToolTransactionStatus.VERIFIED
    assert transaction.evidence_refs[-1] == "operation:list"


def test_generic_or_malformed_todo_events_never_verify_a_todo_claim():
    transactions = transactions_from_tool_events(
        [
            {"tool": "manage_todos", "action": "complete", "exit_code": 0},
            {
                "tool": "manage_todos",
                "action": "complete",
                "todo_semantic_receipt": {"claim_type": "todo_item_completed"},
            },
        ]
    )

    assert all(not tx.claim_type.startswith("todo_") for tx in transactions)
    assert all(tx.status is not ToolTransactionStatus.VERIFIED for tx in transactions)


def test_invalid_todo_event_cannot_create_any_todo_claim_even_when_supplied_directly():
    event = {
        "tool": "manage_todos",
        "action": "complete",
        "semantic_status": "rejected",
    }

    assert transactions_from_tool_events([event]) == ()
    with pytest.raises(ToolTransactionError, match="valid semantic receipt"):
        transaction_from_tool_event(event, claim_type="todo_item_completed")

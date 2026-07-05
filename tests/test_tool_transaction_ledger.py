import pytest

from src.tool_transaction_ledger import (
    ToolTransaction,
    ToolTransactionError,
    ToolTransactionStatus,
    transaction_evidence_for_claim,
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

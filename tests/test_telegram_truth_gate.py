import json
import re
from pathlib import Path

import pytest

from src.telegram_truth_gate import gate_telegram_reply_text, project_telegram_todo_transactions
from src.todo_transaction_receipts import TODO_RECEIPT_FIELD, TODO_SEMANTIC_RECEIPT_SCHEMA
from src.tool_transaction_ledger import TOOL_TRANSACTION_LEDGER_SCHEMA, transactions_from_tool_events


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "telegram_truth_runtime_failures.json"


def _todo_transaction(claim_type: str = "todo_item_created") -> dict:
    action = {
        "todo_item_created": "add",
        "todo_item_completed": "complete",
        "todo_item_reopened": "reopen",
        "todo_item_removed": "remove",
        "todo_list_read": "list",
    }[claim_type]
    refs = ["owner:0123456789abcdef", "list:fedcba9876543210"]
    if action != "list":
        refs.append("item:0011223344556677")
    refs.append(f"operation:{action}")
    return {
        "schema": TOOL_TRANSACTION_LEDGER_SCHEMA,
        "transaction_id": f"agent:0:manage_todos:{claim_type}",
        "surface": "agent",
        "tool": "manage_todos",
        "claim_type": claim_type,
        "status": "verified",
        "evidence_refs": refs,
        "exit_code": 0,
        "artifact_refs": [],
        "command_hash": "sha256:e3b0c44298fc1c149afbf4c8996fb924",
        "verified_done": True,
        "raw_content_visible": False,
    }


@pytest.mark.parametrize("case", json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
def test_redacted_failure_corpus_contains_no_private_material(case: dict):
    raw = json.dumps(case, ensure_ascii=False)

    assert case["contains_private_content"] is False
    assert not re.search(r"sk-[A-Za-z0-9_-]{10,}", raw)
    assert not re.search(r"\b\d{8,}\b", raw)
    assert "redacted-token" not in raw


@pytest.mark.parametrize(
    "text,expected_claim",
    [
        ("Ich habe pygame installiert, alles fertig!", "dependency_installed"),
        ("Der Delegate hat eine falsche Rueckmeldung gegeben.", "delegate_alibi"),
        ("Ich habe `pong.py` erstellt.", "file_changed"),
        ("Ich habe den Screenshot `screenshot.png` per Telegram geschickt.", "telegram_sent"),
    ],
)
def test_truth_gate_marks_unverified_success_claims(tmp_path: Path, text: str, expected_claim: str):
    result = gate_telegram_reply_text(text, [], repo_root=tmp_path)

    assert result.status == "unknown"
    assert "nicht verifiziert" in result.text
    assert expected_claim in {finding.claim_type for finding in result.findings}


def test_truth_gate_strips_jubilation_for_unknown_status(tmp_path: Path):
    result = gate_telegram_reply_text(
        "Geschafft! Screenshot gesendet. \U0001f389",
        [],
        repo_root=tmp_path,
    )

    lowered = result.text.lower()
    assert "nicht verifiziert" in lowered
    assert "geschafft" not in lowered
    assert "\U0001f389" not in result.text


def test_truth_gate_keeps_plain_blocked_reply_plain(tmp_path: Path):
    result = gate_telegram_reply_text(
        "Blockiert: pygame ist in der Sandbox nicht verfuegbar.",
        [],
        repo_root=tmp_path,
    )

    assert result.status == "verified"
    assert result.text == "Blockiert: pygame ist in der Sandbox nicht verfuegbar."


@pytest.mark.parametrize(
    "text,claim_type",
    [
        ("I created the todo item.", "todo_item_created"),
        ("I completed the todo item.", "todo_item_completed"),
        ("I reopened the todo item.", "todo_item_reopened"),
        ("I removed the todo item.", "todo_item_removed"),
        ("I listed the todo list.", "todo_list_read"),
    ],
)
def test_truth_gate_accepts_each_matching_verified_todo_transaction(
    tmp_path: Path,
    text: str,
    claim_type: str,
):
    result = gate_telegram_reply_text(
        text,
        repo_root=tmp_path,
        tool_transactions=[_todo_transaction(claim_type)],
    )

    assert result.status == "verified"
    assert "nicht verifiziert" not in result.text


@pytest.mark.parametrize(
    "transactions",
    [
        [],
        [_todo_transaction("todo_item_completed")],
        [{**_todo_transaction(), "status": "failed", "verified_done": False}],
        [{**_todo_transaction(), "evidence_refs": ["owner:0123456789abcdef", "list:fedcba9876543210", "item:0011223344556677", "operation:add", "operation:complete"]}],
        [{**_todo_transaction(), "unexpected_raw_output": "secret"}],
        [{**_todo_transaction(), "tool": "not_todos"}],
        [{**_todo_transaction(), "transaction_id": "agent:0:manage_todos:todo_item_completed"}],
        [{**_todo_transaction(), "command_hash": "a" * 64}],
        [{**_todo_transaction(), "exit_code": True}],
    ],
    ids=["missing", "wrong_action", "failed", "ambiguous", "malformed_extra", "non_todo", "wrong_tx_id", "nonempty_command_hash", "bool_exit_code"],
)
def test_truth_gate_rejects_invalid_todo_transaction_carriers(tmp_path: Path, transactions: list[dict]):
    result = gate_telegram_reply_text(
        "I created the todo item.",
        repo_root=tmp_path,
        tool_transactions=transactions,
    )

    assert result.status == "unknown"
    assert "nicht verifiziert" in result.text


def test_todo_transaction_projection_is_closed_and_content_free():
    source = _todo_transaction()
    projected = project_telegram_todo_transactions([source])

    assert projected and projected[0] == {**source, "evidence_refs": tuple(source["evidence_refs"]), "artifact_refs": ()}
    assert projected[0] is not source
    assert "command" not in projected[0]
    assert "output" not in projected[0]
    assert "secret" not in repr(projected)
    assert project_telegram_todo_transactions([{**source, "command": "raw-secret"}]) == ()
    assert project_telegram_todo_transactions([{**source, "evidence_refs": ["owner:0123456789abcdef", "list:fedcba9876543210", {"unhashable": "raw"}, "operation:add"]}]) == ()
    assert project_telegram_todo_transactions([source] * 65) == ()


def test_todo_transaction_projection_rejects_hostile_iterable():
    class HostileIterable:
        def __iter__(self):
            raise RuntimeError("do not inspect me")

    assert project_telegram_todo_transactions(HostileIterable()) == ()


def test_truth_gate_accepts_only_real_completion_receipt_transaction(tmp_path: Path):
    receipt = {
        "schema": TODO_SEMANTIC_RECEIPT_SCHEMA,
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
    }
    event = {"tool": "manage_todos", "action": "complete", TODO_RECEIPT_FIELD: receipt}
    transactions = tuple(item.to_dict() for item in transactions_from_tool_events([event], surface="agent"))
    valid = gate_telegram_reply_text(
        "I completed the todo item.", repo_root=tmp_path, tool_transactions=transactions
    )

    impossible_event = {
        "tool": "manage_todos",
        "action": "complete",
        TODO_RECEIPT_FIELD: {**receipt, "current_state": False},
    }
    impossible_transactions = tuple(
        item.to_dict() for item in transactions_from_tool_events([impossible_event], surface="agent")
    )
    invalid = gate_telegram_reply_text(
        "I completed the todo item.", repo_root=tmp_path, tool_transactions=impossible_transactions
    )

    assert transactions
    assert valid.status == "verified"
    assert impossible_transactions == ()
    assert invalid.status == "unknown"

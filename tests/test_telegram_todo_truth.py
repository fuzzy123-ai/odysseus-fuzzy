import asyncio
from copy import deepcopy
from pathlib import Path

from plugins.telegram.polling import _public_agent_turn_result, _run_agent_turn
from plugins.telegram.webhook_service import run_webhook_agent_turn_branch
from src.telegram_todo_truth import (
    TELEGRAM_TODO_TRUTH_ENVELOPE_SCHEMA,
    build_telegram_todo_truth_envelope,
    tool_events_from_telegram_todo_truth_envelope,
)
from src.telegram_truth_gate import gate_telegram_reply_text
from src.todo_digest_receipts import todo_digest_receipts_from_postconditions
from src.todo_receipts import todo_receipts_from_tool_result


LIST_REF = "todo-list:v1:0123456789abcdef:list-alpha"
ITEM_REF = "todo-item:v1:itm_0123456789abcdef"
ROOT = Path(__file__).resolve().parents[1]


def _receipt(operation="complete"):
    states = {
        "add": {"exists": True, "done": False},
        "complete": {"exists": True, "done": True},
        "reopen": {"exists": True, "done": False},
        "remove": {"exists": False, "done": None},
    }
    return todo_receipts_from_tool_result(
        {
            "action": operation,
            "operation": operation,
            "list_ref": LIST_REF,
            "item_ref": ITEM_REF,
            "previous_state": {"exists": True, "done": False},
            "current_state": states[operation],
            "open_count": 1,
            "transaction_status": "committed",
            "verified": True,
            "evidence_refs": ["notes-readback:v1:abcdef123456"],
            "exit_code": 0,
            "text": "private task text",
        }
    )[0]


def _envelope(operation="complete"):
    receipt = _receipt(operation)
    return build_telegram_todo_truth_envelope(
        [
            {
                "tool": "manage_todos",
                "command": "private command must be redacted",
                "output": "private task text must be redacted",
                "exit_code": 0,
                "todo_receipts": [receipt.to_dict()],
            }
        ]
    )


def _envelope_with_digest():
    mutation = _receipt("add")
    digest_receipts = todo_digest_receipts_from_postconditions(
        {
            "claim_type": "todo_digest_contains",
            "list_ref": LIST_REF,
            "item_ref": ITEM_REF,
            "included": True,
            "current_state": {"exists": True, "done": False},
            "projection_ref": "todo-digest-projection:v1:" + "a" * 32,
            "transaction_status": "projected",
            "verified": True,
            "evidence_refs": ["notes-digest-readback:v1:" + "a" * 32],
        },
        {
            "claim_type": "todo_digest_schedule_active",
            "status": "active",
            "schedule_ref": "todo-digest-schedule:v1:" + "b" * 12,
            "next_run": "2026-07-23T07:00:00",
            "verified": True,
            "evidence_refs": ["scheduled-task-readback:v1:" + "b" * 12],
        },
    )
    return build_telegram_todo_truth_envelope([
        {
            "tool": "manage_todos",
            "exit_code": 0,
            "todo_receipts": [mutation.to_dict()],
            "todo_digest_receipts": [
                receipt.to_dict() for receipt in digest_receipts
            ],
        }
    ])


def test_envelope_preserves_four_evidence_layers_without_raw_content():
    envelope = _envelope()

    assert envelope["schema"] == TELEGRAM_TODO_TRUTH_ENVELOPE_SCHEMA
    assert envelope["counts"] == {
        "tool_starts": 1,
        "tool_outputs": 1,
        "transactions": 1,
        "postconditions": 1,
    }
    assert envelope["raw_content_visible"] is False
    assert envelope["raw_identifiers_visible"] is False
    assert "private" not in repr(envelope).lower()


def test_envelope_round_trip_supports_matching_telegram_todo_claim():
    envelope = _envelope("complete")

    events = tool_events_from_telegram_todo_truth_envelope(envelope)
    gated = gate_telegram_reply_text(
        "Todo erledigt.",
        todo_truth_envelope=envelope,
    )

    assert len(events) == 1
    assert events[0]["tool"] == "manage_todos"
    assert gated.status == "verified"
    assert gated.changed is False


def test_envelope_preserves_digest_receipts_but_does_not_prove_exact_timing():
    envelope = _envelope_with_digest()
    gated = gate_telegram_reply_text(
        "Todo erscheint morgen im Digest.",
        todo_truth_envelope=envelope,
    )
    tampered = deepcopy(envelope)
    tampered["digest_postconditions"][1]["verified"] = False
    rejected = gate_telegram_reply_text(
        "Todo erscheint morgen im Digest.",
        todo_truth_envelope=tampered,
    )

    assert envelope["counts"]["digest_postconditions"] == 2
    assert gated.status == "unknown"
    assert gated.changed is True
    assert rejected.status == "unknown"
    assert rejected.changed is True


def test_missing_or_tampered_envelope_weakens_claim_before_send():
    missing = gate_telegram_reply_text("Todo erledigt.")
    tampered_envelope = deepcopy(_envelope("complete"))
    tampered_envelope["tool_outputs"][0]["exit_code"] = 1
    tampered = gate_telegram_reply_text(
        "Todo erledigt.",
        todo_truth_envelope=tampered_envelope,
    )
    tampered_transaction = deepcopy(_envelope("complete"))
    tampered_transaction["transactions"][0]["verified_done"] = False
    transaction_tamper = gate_telegram_reply_text(
        "Todo erledigt.",
        todo_truth_envelope=tampered_transaction,
    )

    assert missing.status == "unknown"
    assert missing.changed is True
    assert "nicht verifiziert" in missing.text
    assert tampered.status == "unknown"
    assert tampered.changed is True
    assert transaction_tamper.status == "unknown"
    assert transaction_tamper.changed is True


def test_polling_agent_bridge_keeps_internal_envelope_but_public_shape_is_summary():
    envelope = _envelope("add")
    result = _run_agent_turn(
        lambda _bridge: {
            "status": "accepted",
            "reply_text": "Todo gespeichert.",
            "todo_truth_envelope": envelope,
        },
        {"ready_for_agent": True},
    )
    public = _public_agent_turn_result(result)

    assert result["todo_truth_envelope"] == envelope
    assert public["todo_truth_envelope"]["present"] is True
    assert public["todo_truth_envelope"]["postcondition_count"] == 1
    assert LIST_REF not in repr(public)
    assert ITEM_REF not in repr(public)


def test_core_agent_sse_bridge_extracts_metrics_envelope_instead_of_raw_events():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    start = source.index("def _telegram_agent_turn_handler")
    end = source.index("app.state.telegram_session_bridge", start)
    body = source[start:end]

    assert 'event.get("type") == "metrics"' in body
    assert 'event["data"].get("tool_events")' in body
    assert "build_telegram_todo_truth_envelope(metric_events)" in body
    assert 'result["todo_truth_envelope"] = todo_truth_envelope' in body


def test_webhook_agent_branch_passes_envelope_to_pre_send_gate():
    envelope = _envelope("remove")
    captured = {}

    class _Sessions:
        def bind_chat(self, **_kwargs):
            return {"session_id": "session-redacted"}

    class _Store:
        def append_event(self, **payload):
            captured["event"] = payload

    async def _typing(_chat_id, *, store):
        raise AssertionError("typing pulse should not run for deterministic turn")

    def _reply(chat_id, text, *, source_message_id=None, todo_truth_envelope=None):
        captured.update(
            chat_id=chat_id,
            text=text,
            source_message_id=source_message_id,
            envelope=todo_truth_envelope,
        )
        return {"exit_code": 0}

    bridge = {
        "ready_for_agent": True,
        "chat_id": "chat-redacted",
        "session_alias": "alias-redacted",
        "recommended_session_name": "telegram",
        "desired_session_scope": "normal",
        "source_message_id": 7,
    }
    agent_turn = {
        "status": "accepted",
        "reply_text": "Todo entfernt.",
        "reply_text_present": True,
        "todo_truth_envelope": envelope,
    }

    _final_bridge, returned_turn, reply = asyncio.run(
        run_webhook_agent_turn_branch(
            stored_message={},
            bridge=bridge,
            raw_chat_id="chat-redacted",
            sessions=_Sessions(),
            session_creator=lambda **_kwargs: {},
            store=_Store(),
            voice_agent_turn=None,
            recent_attachment_context=None,
            agent_turn_handler=None,
            build_agent_bridge_request=lambda *_args, **_kwargs: bridge,
            deterministic_agent_turn=lambda _bridge: agent_turn,
            run_agent_turn_async=lambda *_args, **_kwargs: None,
            typing_pulse=_typing,
            agent_failure_reply=lambda _turn: "failed",
            reply_with_gate=_reply,
        )
    )

    assert returned_turn is agent_turn
    assert reply == {"exit_code": 0}
    assert captured["envelope"] == envelope
    assert captured["event"]["todo_truth_envelope"]["present"] is True

from src.claim_evidence_gate import evaluate_response_claims
from src.todo_digest_receipts import (
    todo_digest_receipts_from_postconditions,
    todo_digest_receipts_from_tool_events,
)


LIST_REF = "todo-list:v1:0123456789abcdef:list-alpha"
ITEM_REF = "todo-item:v1:itm_0123456789abcdef"


def _projection(*, included=True, done=False, exists=True):
    return {
        "claim_type": "todo_digest_contains" if included else "todo_digest_excludes",
        "list_ref": LIST_REF,
        "item_ref": ITEM_REF,
        "included": included,
        "current_state": {"exists": exists, "done": done},
        "projection_ref": "todo-digest-projection:v1:" + "a" * 32,
        "transaction_status": "projected",
        "verified": True,
        "evidence_refs": ["notes-digest-readback:v1:" + "a" * 32],
        "raw_content_visible": False,
    }


def _schedule(*, verified=True):
    return {
        "claim_type": "todo_digest_schedule_active",
        "status": "active" if verified else "missing",
        "schedule_ref": "todo-digest-schedule:v1:" + "b" * 12 if verified else "",
        "next_run": "2026-07-23T07:00:00" if verified else "",
        "verified": verified,
        "evidence_refs": ["scheduled-task-readback:v1:" + "b" * 12] if verified else [],
    }


def _event(projection, schedule, *, exit_code=0):
    receipts = todo_digest_receipts_from_postconditions(projection, schedule)
    return {
        "tool": "manage_todos",
        "exit_code": exit_code,
        "todo_digest_receipts": [receipt.to_dict() for receipt in receipts],
    }


def test_contains_and_excludes_receipts_bind_to_semantic_projection_state():
    contains = todo_digest_receipts_from_tool_events((_event(_projection(), _schedule()),))
    excludes = todo_digest_receipts_from_tool_events((
        _event(_projection(included=False, done=True), _schedule()),
    ))

    assert contains[0].claim_type == "todo_digest_contains"
    assert contains[0].verified is True
    assert excludes[0].claim_type == "todo_digest_excludes"
    assert excludes[0].verified is True
    assert "private" not in repr(contains[0].to_dict()).lower()


def test_timed_digest_claim_requires_membership_and_active_schedule_receipts():
    missing_schedule = evaluate_response_claims(
        "Todo erscheint morgen im Digest.",
        [_event(_projection(), _schedule(verified=False))],
    )
    verified = evaluate_response_claims(
        "Todo erscheint morgen im Digest.",
        [_event(_projection(), _schedule())],
    )

    assert missing_schedule.ok is False
    assert [item.claim_type for item in missing_schedule.unsupported] == [
        "todo_digest_schedule_active"
    ]
    assert verified.ok is True
    assert [item.claim_type for item in verified.findings] == [
        "todo_digest_contains",
        "todo_digest_schedule_active",
    ]

    common_wording = evaluate_response_claims(
        "Todo ist im naechsten Digest enthalten.",
        [_event(_projection(), _schedule())],
    )
    assert common_wording.ok is True


def test_exclusion_claim_and_nonzero_exit_fail_closed():
    event = _event(_projection(included=False, done=True), _schedule())
    verified = evaluate_response_claims("Todo erscheint nicht mehr im Digest.", [event])
    failed = evaluate_response_claims(
        "Todo erscheint nicht mehr im Digest.",
        [{**event, "exit_code": 1}],
    )

    assert verified.ok is True
    assert verified.findings[0].claim_type == "todo_digest_excludes"
    assert failed.ok is False


def test_inconsistent_projection_cannot_become_verified():
    inconsistent = _projection(included=True, done=True)
    receipts = todo_digest_receipts_from_postconditions(inconsistent, _schedule())

    assert receipts[0].claim_type == "todo_digest_contains"
    assert receipts[0].verified is False


def test_explicitly_unverified_exclusion_wording_is_not_a_success_claim():
    report = evaluate_response_claims(
        "Todo ist nicht verifiziert und im Digest nicht mehr enthalten.",
        [],
    )

    assert report.findings == ()

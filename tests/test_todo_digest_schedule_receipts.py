from copy import deepcopy
from datetime import datetime, timezone

from src.todo_digest_schedule_receipts import (
    build_todo_digest_schedule_receipt,
    schedule_receipts_from_tool_events,
    validate_todo_digest_schedule_receipt,
    validated_todo_digest_schedule_receipt_from_event,
)


NOW = datetime(2026, 7, 24, 8, 0)


def _task(**changes):
    value = {
        "id": "private-task-id", "owner": "alice", "task_type": "action", "action": "todo_digest",
        "trigger_type": "schedule", "schedule": "cron", "status": "active",
        "cron_expression": "0 9 * * 1,2,3,4,5", "scheduled_time": "09:00", "next_run": datetime(2026, 7, 25, 7, 0),
    }
    value.update(changes)
    return value


def test_active_future_schedule_receipt_is_deterministic_and_content_free():
    first = build_todo_digest_schedule_receipt(owner="alice", candidates=[_task()], now_utc=NOW)
    second = build_todo_digest_schedule_receipt(owner="alice", candidates=[_task()], now_utc=NOW)

    assert first == second == validate_todo_digest_schedule_receipt(first)
    assert first["claim_type"] == "todo_digest_schedule_active"
    assert "alice" not in repr(first)
    assert "private-task-id" not in repr(first)
    assert "0 9" not in repr(first)
    assert "2026-07-25" not in repr(first)
    assert build_todo_digest_schedule_receipt(owner="alice", candidates=[_task(cron_expression="0 9 * * 1-5")], now_utc=NOW)
    assert build_todo_digest_schedule_receipt(owner="alice", candidates=[_task(cron_expression="0 9 * * 0")], now_utc=NOW)


def test_schedule_receipt_fails_closed_for_owner_state_time_and_shape_errors():
    cases = [
        ("bob", [_task()], NOW), ("alice", [], NOW), ("alice", [_task(), _task(id="other")], NOW),
        ("alice", [_task(status="paused")], NOW), ("alice", [_task(status="completed")], NOW),
        ("alice", [_task(next_run=NOW)], NOW), ("alice", [_task(next_run=NOW.replace(hour=7))], NOW),
        ("alice", [_task(next_run=NOW.replace(tzinfo=timezone.utc))], NOW), ("alice", [_task(next_run=None)], NOW),
        ("alice", [_task(cron_expression="bad")], NOW), ("alice", [_task(scheduled_time="10:00")], NOW),
    ]
    for owner, candidates, now in cases:
        assert build_todo_digest_schedule_receipt(owner=owner, candidates=candidates, now_utc=now) is None


def test_schedule_receipt_rejects_tampering_extra_and_hostile_rows():
    receipt = build_todo_digest_schedule_receipt(owner="alice", candidates=[_task()], now_utc=NOW)
    for mutate in (lambda value: value.update(active=False), lambda value: value.update(extra=True), lambda value: value.update(receipt_ref="sha256:" + "0" * 64)):
        changed = deepcopy(receipt)
        mutate(changed)
        assert validate_todo_digest_schedule_receipt(changed) is None

    class Hostile(dict):
        def get(self, *_args): raise RuntimeError("hostile")
    assert build_todo_digest_schedule_receipt(owner="alice", candidates=[Hostile()], now_utc=NOW) is None
    assert validate_todo_digest_schedule_receipt(Hostile()) is None
    assert validated_todo_digest_schedule_receipt_from_event(Hostile()) is None
    assert schedule_receipts_from_tool_events(Hostile()) == ()

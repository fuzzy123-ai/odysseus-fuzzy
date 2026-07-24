from __future__ import annotations

import asyncio
from datetime import datetime
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from core.database import Base, Note, ScheduledTask
from src.tool_domains import todos


@pytest.fixture()
def todo_tool_store(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'manage-todos.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(todos, "_SESSION_FACTORY", session_factory)
    try:
        yield session_factory
    finally:
        engine.dispose()


def _run(payload, *, owner="alice"):
    return asyncio.run(todos.do_manage_todos(json.dumps(payload), owner=owner))


def _seed(session_factory, *, note_id, owner="alice", title="List", items=None):
    db = session_factory()
    try:
        db.add(
            Note(
                id=note_id,
                owner=owner,
                title=title,
                note_type="checklist",
                archived=False,
                items=json.dumps(items or []),
            )
        )
        db.commit()
    finally:
        db.close()


def _stored_notes(session_factory):
    db = session_factory()
    try:
        return db.query(Note).order_by(Note.id.asc()).all()
    finally:
        db.close()


def test_add_creates_one_owner_stable_default_list_and_replay_is_idempotent(todo_tool_store):
    first = _run({
        "action": "add",
        "text": "Synthetic task",
        "idempotency_key": "turn-1-call-1",
    })
    replay = _run({
        "action": "add",
        "text": "Synthetic task",
        "idempotency_key": "turn-1-call-1",
    })
    listed = _run({"action": "list"})

    notes = _stored_notes(todo_tool_store)
    assert first["transaction_status"] == "committed"
    assert replay["transaction_status"] == "idempotent"
    assert first["item_ref"] == replay["item_ref"]
    assert listed["list_count"] == 1
    assert listed["open_count"] == 1
    assert len(notes) == 1
    assert notes[0].owner == "alice"
    assert len(json.loads(notes[0].items)) == 1


def test_complete_reopen_remove_round_trip_uses_stable_refs(todo_tool_store):
    added = _run({
        "action": "add",
        "text": "Round trip task",
        "idempotency_key": "add-round-trip",
    })
    listed = _run({"action": "list"})
    list_ref = listed["lists"][0]["list_ref"]
    item_ref = added["item_ref"]

    completed = _run({
        "action": "complete",
        "list_ref": list_ref,
        "item_ref": item_ref,
        "idempotency_key": "complete-round-trip",
    })
    reopened = _run({
        "action": "reopen",
        "list_ref": list_ref,
        "item_ref": item_ref,
        "idempotency_key": "reopen-round-trip",
    })
    removed = _run({
        "action": "remove",
        "list_ref": list_ref,
        "item_ref": item_ref,
        "idempotency_key": "remove-round-trip",
    })

    assert completed["current_state"] == {"exists": True, "done": True}
    assert reopened["current_state"] == {"exists": True, "done": False}
    assert removed["current_state"] == {"exists": False, "done": None}
    assert completed["todo_digest_receipts"][0]["claim_type"] == "todo_digest_excludes"
    assert completed["todo_digest_receipts"][0]["verified"] is True
    assert reopened["todo_digest_receipts"][0]["claim_type"] == "todo_digest_contains"
    assert reopened["todo_digest_receipts"][0]["verified"] is True
    assert removed["todo_digest_receipts"][0]["claim_type"] == "todo_digest_excludes"
    assert removed["todo_digest_receipts"][0]["verified"] is True
    assert _run({"action": "list"})["open_count"] == 0


def test_add_receipt_separates_digest_membership_from_schedule_truth(todo_tool_store):
    db = todo_tool_store()
    try:
        db.add(ScheduledTask(
            id="digest-schedule",
            owner="alice",
            name="Synthetic digest",
            task_type="action",
            action="todo_digest",
            trigger_type="schedule",
            schedule="daily",
            scheduled_time="09:00",
            status="active",
            output_target="telegram",
            next_run=datetime(2026, 7, 23, 7, 0),
        ))
        db.commit()
    finally:
        db.close()

    added = _run({
        "action": "add",
        "text": "Synthetic digest item",
        "idempotency_key": "digest-add",
    })
    by_claim = {
        receipt["claim_type"]: receipt
        for receipt in added["todo_digest_receipts"]
    }

    assert by_claim["todo_digest_contains"]["verified"] is True
    assert by_claim["todo_digest_schedule_active"]["verified"] is True
    assert "Synthetic digest item" not in repr(added["todo_digest_receipts"])


def test_add_outside_real_digest_limit_does_not_verify_membership(todo_tool_store):
    _seed(
        todo_tool_store,
        note_id="full-list",
        items=[
            {"id": f"itm_{index:016d}", "text": f"Synthetic {index}", "done": False}
            for index in range(20)
        ],
    )
    list_ref = _run({"action": "list"})["lists"][0]["list_ref"]

    added = _run({
        "action": "add",
        "list_ref": list_ref,
        "text": "Synthetic beyond limit",
        "idempotency_key": "digest-limit-add",
    })
    membership = next(
        receipt
        for receipt in added["todo_digest_receipts"]
        if receipt["claim_type"] == "todo_digest_contains"
    )

    assert membership["included"] is False
    assert membership["verified"] is False


def test_multiple_lists_require_stable_list_ref_for_add(todo_tool_store):
    _seed(todo_tool_store, note_id="list-one", title="One")
    _seed(todo_tool_store, note_id="list-two", title="Two")

    outcome = _run({
        "action": "add",
        "text": "Must not guess",
        "idempotency_key": "ambiguous-list-add",
    })

    assert outcome["status"] == "ambiguous"
    assert outcome["exit_code"] == 1
    assert len(outcome["candidate_refs"]) == 2
    assert all(json.loads(note.items) == [] for note in _stored_notes(todo_tool_store))


def test_text_match_across_lists_returns_item_refs_and_mutates_nothing(todo_tool_store):
    _seed(
        todo_tool_store,
        note_id="list-one",
        title="One",
        items=[{"text": "Same task", "done": False}],
    )
    _seed(
        todo_tool_store,
        note_id="list-two",
        title="Two",
        items=[{"text": " same   task ", "done": False}],
    )

    outcome = _run({
        "action": "complete",
        "text": "SAME TASK",
        "idempotency_key": "ambiguous-item-complete",
    })

    assert outcome["status"] == "ambiguous"
    assert outcome["exit_code"] == 1
    assert len(outcome["candidate_refs"]) == 2
    assert all(ref.startswith("todo-item:v1:") for ref in outcome["candidate_refs"])
    assert all(
        item["done"] is False
        for note in _stored_notes(todo_tool_store)
        for item in json.loads(note.items)
    )


def test_owner_and_idempotency_boundaries_fail_closed(todo_tool_store):
    _seed(todo_tool_store, note_id="alice-list", owner="alice")
    listed = _run({"action": "list"})
    list_ref = listed["lists"][0]["list_ref"]

    assert _run({"action": "list"}, owner=None)["exit_code"] == 1
    assert _run({"action": "list", "list_ref": list_ref}, owner="bob")["exit_code"] == 1
    assert _run({"action": "add", "text": "No key"})["exit_code"] == 1
    assert json.loads(_stored_notes(todo_tool_store)[0].items) == []

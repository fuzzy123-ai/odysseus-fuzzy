from __future__ import annotations

import asyncio
from datetime import datetime
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as core_database
from core.database import Base, Note, ScheduledTask
from src.tool_domains import todos
from src.todo_domain_service import TodoDomainService
from src.todo_transaction_receipts import TODO_RECEIPT_FIELD
from src.todo_digest_receipts import TODO_DIGEST_RECEIPT_FIELD
from src.todo_digest_schedule_receipts import TODO_DIGEST_SCHEDULE_RECEIPT_FIELD


@pytest.fixture()
def todo_tool_store(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'manage-todos.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(
        todos,
        "_service_factory",
        lambda: TodoDomainService(session_factory, Note),
    )
    monkeypatch.setattr(core_database, "SessionLocal", session_factory)
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
    list_ref = "alice-list"
    _seed(todo_tool_store, note_id=list_ref, title="Alice")
    first = _run({
        "action": "add",
        "list_ref": list_ref,
        "text": "Synthetic task",
        "idempotency_key": "turn-1-call-1",
    })
    replay = _run({
        "action": "add",
        "list_ref": list_ref,
        "text": "Synthetic task",
        "idempotency_key": "turn-1-call-1",
    })
    listed = _run({"action": "list", "list_ref": list_ref})

    notes = _stored_notes(todo_tool_store)
    assert first["transaction_status"] == "committed"
    assert replay["transaction_status"] == "idempotent_noop"
    assert first["item_ref"] == replay["item_ref"]
    assert first[TODO_RECEIPT_FIELD]["claim_type"] == "todo_item_created"
    assert first[TODO_RECEIPT_FIELD]["verified"] is True
    assert replay[TODO_RECEIPT_FIELD]["transaction_status"] == "idempotent_noop"
    assert listed[TODO_RECEIPT_FIELD]["claim_type"] == "todo_list_read"
    assert listed["list_ref"] == list_ref
    assert listed["open_count"] == 1
    assert len(notes) == 1
    assert notes[0].owner == "alice"
    assert len(json.loads(notes[0].items)) == 1


def test_complete_reopen_remove_round_trip_uses_stable_refs(todo_tool_store):
    list_ref = "round-trip-list"
    _seed(todo_tool_store, note_id=list_ref, title="Round trip")
    added = _run({
        "action": "add",
        "list_ref": list_ref,
        "text": "Round trip task",
        "idempotency_key": "add-round-trip",
    })
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
    blocked = _run({
        "action": "remove",
        "list_ref": list_ref,
        "item_ref": item_ref,
        "idempotency_key": "remove-round-trip",
    })
    removed = _run({
        "action": "remove",
        "list_ref": list_ref,
        "item_ref": item_ref,
        "idempotency_key": "remove-round-trip",
        "confirmed": True,
    })

    assert blocked["status"] == "confirmation_required"
    assert completed["current_state"] is True
    assert reopened["current_state"] is False
    assert removed["current_state"] is None
    assert completed[TODO_RECEIPT_FIELD]["claim_type"] == "todo_item_completed"
    assert reopened[TODO_RECEIPT_FIELD]["claim_type"] == "todo_item_reopened"
    assert removed[TODO_RECEIPT_FIELD]["claim_type"] == "todo_item_removed"
    assert completed[TODO_DIGEST_RECEIPT_FIELD]["claim_type"] == "todo_digest_excludes"
    assert reopened[TODO_DIGEST_RECEIPT_FIELD]["claim_type"] == "todo_digest_contains"
    assert removed[TODO_DIGEST_RECEIPT_FIELD]["claim_type"] == "todo_digest_excludes"
    assert _run({"action": "list", "list_ref": list_ref})["open_count"] == 0


def test_add_receipt_separates_digest_membership_from_schedule_truth(todo_tool_store):
    list_ref = "digest-list"
    _seed(todo_tool_store, note_id=list_ref, title="Digest")
    db = todo_tool_store()
    try:
        db.add(ScheduledTask(
            id="digest-schedule",
            owner="alice",
            name="Synthetic digest",
            task_type="action",
            action="todo_digest",
            trigger_type="schedule",
            schedule="cron",
            cron_expression="0 9 * * 1-5",
            scheduled_time="09:00",
            status="active",
            output_target="telegram",
            next_run=datetime(2099, 7, 23, 7, 0),
        ))
        db.commit()
    finally:
        db.close()

    added = _run({
        "action": "add",
        "list_ref": list_ref,
        "text": "Synthetic digest item",
        "idempotency_key": "digest-add",
    })

    assert added[TODO_RECEIPT_FIELD]["claim_type"] == "todo_item_created"
    assert added[TODO_DIGEST_RECEIPT_FIELD]["claim_type"] == "todo_digest_contains"
    assert added[TODO_DIGEST_RECEIPT_FIELD]["verified"] is True
    assert added[TODO_DIGEST_SCHEDULE_RECEIPT_FIELD]["claim_type"] == "todo_digest_schedule_active"
    assert added[TODO_DIGEST_SCHEDULE_RECEIPT_FIELD]["verified"] is True
    assert "Synthetic digest item" not in repr(added)


def test_add_outside_real_digest_limit_does_not_verify_membership(todo_tool_store):
    list_ref = "full-list"
    _seed(
        todo_tool_store,
        note_id="full-list",
        items=[{"id": f"item{index:04d}", "text": f"Synthetic {index}", "done": False} for index in range(20)],
    )

    added = _run({
        "action": "add",
        "list_ref": list_ref,
        "text": "Synthetic beyond limit",
        "idempotency_key": "digest-limit-add",
    })
    assert added[TODO_RECEIPT_FIELD]["claim_type"] == "todo_item_created"
    assert TODO_DIGEST_RECEIPT_FIELD not in added


def test_multiple_lists_require_stable_list_ref_for_add(todo_tool_store):
    _seed(todo_tool_store, note_id="list-one", title="One")
    _seed(todo_tool_store, note_id="list-two", title="Two")

    missing_list_ref = _run({
        "action": "add",
        "text": "Must not guess",
        "idempotency_key": "missing-list-ref-add",
    })
    assert missing_list_ref["status"] == "rejected"
    assert missing_list_ref["error_code"] == "invalid_arguments"
    assert all(json.loads(note.items) == [] for note in _stored_notes(todo_tool_store))

    outcome = _run({
        "action": "add",
        "list_ref": "list-one",
        "text": "Must not guess",
        "idempotency_key": "ambiguous-list-add",
    })

    assert outcome["status"] == "ok"
    assert outcome["list_ref"] == "list-one"
    assert json.loads(_stored_notes(todo_tool_store)[0].items)
    assert json.loads(_stored_notes(todo_tool_store)[1].items) == []


def test_text_match_across_lists_returns_item_refs_and_mutates_nothing(todo_tool_store):
    _seed(
        todo_tool_store,
        note_id="list-one",
        title="One",
        items=[
            {"id": "itemone1", "text": "Same task", "done": False},
            {"id": "itemone2", "text": "Same task", "done": False},
        ],
    )
    _seed(
        todo_tool_store,
        note_id="list-two",
        title="Two",
        items=[{"id": "itemtwo2", "text": " same   task ", "done": False}],
    )

    outcome = _run({
        "action": "complete",
        "list_ref": "list-one",
        "text": "Same task",
    })

    assert outcome["status"] == "rejected"
    assert outcome["error_code"] == "ambiguous_item"
    assert len(outcome["candidate_refs"]) == 2
    assert set(outcome["candidate_refs"]) == {"itemone1", "itemone2"}
    assert "Same task" not in repr(outcome)
    assert all(
        item["done"] is False
        for note in _stored_notes(todo_tool_store)
        for item in json.loads(note.items)
    )


def test_owner_and_idempotency_boundaries_fail_closed(todo_tool_store):
    _seed(todo_tool_store, note_id="alice-list", owner="alice")
    list_ref = "alice-list"

    assert _run({"action": "list", "list_ref": list_ref}, owner=None)["exit_code"] == 1
    assert _run({"action": "list", "list_ref": list_ref}, owner="bob")["exit_code"] == 1
    assert _run({"action": "add", "list_ref": list_ref, "text": "No key"})["exit_code"] == 1
    assert json.loads(_stored_notes(todo_tool_store)[0].items) == []

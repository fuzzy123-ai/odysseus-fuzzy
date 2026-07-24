from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from core.database import Base, Note
from src.todo_domain_service import (
    TodoDataIntegrityError,
    TodoDomainService,
    TodoIdempotencyConflictError,
    TodoListNotFoundError,
    TodoValidationError,
    make_list_ref,
)


@pytest.fixture()
def todo_store(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'todo-domain.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    service = TodoDomainService(
        session_factory,
        max_retries=12,
        retry_backoff_seconds=0.002,
    )
    try:
        yield service, session_factory
    finally:
        engine.dispose()


def _seed_note(session_factory, *, note_id="list-alpha", owner="alice", items=None):
    if items is None:
        items = [
            {"text": "Prepare release", "done": False},
            {"text": "Review evidence", "done": False},
        ]
    db = session_factory()
    try:
        db.add(
            Note(
                id=note_id,
                owner=owner,
                title="Synthetic list",
                note_type="checklist",
                archived=False,
                items=json.dumps(items),
            )
        )
        db.commit()
    finally:
        db.close()
    return make_list_ref(owner, note_id)


def _raw_items(session_factory, note_id="list-alpha"):
    db = session_factory()
    try:
        return db.query(Note).filter(Note.id == note_id).one().items
    finally:
        db.close()


def test_legacy_checklist_is_readable_without_mutating_storage(todo_store):
    service, session_factory = todo_store
    list_ref = _seed_note(session_factory)
    before = _raw_items(session_factory)

    first = service.list_items(owner="alice", list_ref=list_ref)
    second = service.list_items(owner="alice", list_ref=list_ref)

    assert [item.text for item in first.items] == ["Prepare release", "Review evidence"]
    assert [item.item_ref for item in first.items] == [item.item_ref for item in second.items]
    assert all(item.item_ref.startswith("todo-item:v1:itm_") for item in first.items)
    assert first.open_count == 2
    assert _raw_items(session_factory) == before


def test_owner_scope_is_redacted_and_cross_owner_access_fails_closed(todo_store):
    service, session_factory = todo_store
    list_ref = _seed_note(session_factory)

    assert "alice" not in list_ref
    with pytest.raises(TodoListNotFoundError):
        service.list_items(owner="bob", list_ref=list_ref)
    with pytest.raises(TodoValidationError):
        service.list_items(owner="", list_ref=list_ref)


def test_add_is_idempotent_and_atomically_upgrades_the_full_legacy_list(todo_store):
    service, session_factory = todo_store
    list_ref = _seed_note(session_factory)

    added = service.add_item(
        owner="alice",
        list_ref=list_ref,
        text="Ship synthetic build",
        idempotency_key="request-add-1",
    )
    replay = service.add_item(
        owner="alice",
        list_ref=list_ref,
        text="Ship synthetic build",
        idempotency_key="request-add-1",
    )

    stored = json.loads(_raw_items(session_factory))
    assert added.transaction_status == "committed"
    assert added.verified is True
    assert any(ref.startswith("notes-pre-upgrade:v1:") for ref in added.evidence_refs)
    assert replay.transaction_status == "idempotent"
    assert replay.item_ref == added.item_ref
    assert len(stored) == 3
    assert all(set(item) == {"id", "text", "done"} for item in stored)
    assert len({item["id"] for item in stored}) == 3

    with pytest.raises(TodoIdempotencyConflictError):
        service.add_item(
            owner="alice",
            list_ref=list_ref,
            text="Different payload",
            idempotency_key="request-add-1",
        )
    assert len(json.loads(_raw_items(session_factory))) == 3


def test_ambiguous_text_matching_returns_refs_and_mutates_nothing(todo_store):
    service, session_factory = todo_store
    list_ref = _seed_note(
        session_factory,
        items=[
            {"text": "Duplicate task", "done": False},
            {"text": " duplicate   task ", "done": False},
        ],
    )
    before = _raw_items(session_factory)

    outcome = service.complete_item(
        owner="alice",
        list_ref=list_ref,
        text="DUPLICATE TASK",
        idempotency_key="request-complete-ambiguous",
    )

    assert outcome.transaction_status == "ambiguous"
    assert outcome.mutated is False
    assert outcome.verified is False
    assert len(outcome.candidate_refs) == 2
    assert all(ref.startswith("todo-item:v1:") for ref in outcome.candidate_refs)
    assert _raw_items(session_factory) == before


def test_complete_reopen_and_remove_use_stable_item_refs(todo_store):
    service, session_factory = todo_store
    list_ref = _seed_note(session_factory)
    item_ref = service.list_items(owner="alice", list_ref=list_ref).items[0].item_ref

    completed = service.complete_item(
        owner="alice",
        list_ref=list_ref,
        item_ref=item_ref,
        idempotency_key="request-complete-1",
    )
    completion_replay = service.complete_item(
        owner="alice",
        list_ref=list_ref,
        item_ref=item_ref,
        idempotency_key="request-complete-1-retry",
    )
    reopened = service.reopen_item(
        owner="alice",
        list_ref=list_ref,
        item_ref=item_ref,
        idempotency_key="request-reopen-1",
    )
    removed = service.remove_item(
        owner="alice",
        list_ref=list_ref,
        item_ref=item_ref,
        idempotency_key="request-remove-1",
    )

    assert completed.transaction_status == "committed"
    assert completed.current_state == {"exists": True, "done": True}
    assert completion_replay.transaction_status == "idempotent"
    assert reopened.current_state == {"exists": True, "done": False}
    assert removed.current_state == {"exists": False, "done": None}
    assert removed.verified is True
    assert all(
        item.item_ref != item_ref
        for item in service.list_items(owner="alice", list_ref=list_ref).items
    )


def test_parallel_add_and_complete_do_not_lose_items(todo_store):
    service, session_factory = todo_store
    list_ref = _seed_note(
        session_factory,
        items=[{"text": "Existing task", "done": False}],
    )
    existing_ref = service.list_items(owner="alice", list_ref=list_ref).items[0].item_ref

    def add(index):
        return service.add_item(
            owner="alice",
            list_ref=list_ref,
            text=f"Concurrent task {index}",
            idempotency_key=f"concurrent-add-{index}",
        )

    def complete():
        return service.complete_item(
            owner="alice",
            list_ref=list_ref,
            item_ref=existing_ref,
            idempotency_key="concurrent-complete",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(add, index) for index in range(7)]
        futures.append(pool.submit(complete))
        outcomes = [future.result(timeout=10) for future in futures]

    snapshot = service.list_items(owner="alice", list_ref=list_ref)
    assert all(outcome.transaction_status == "committed" for outcome in outcomes)
    assert len(snapshot.items) == 8
    assert len({item.item_ref for item in snapshot.items}) == 8
    assert next(item for item in snapshot.items if item.item_ref == existing_ref).done is True
    assert {item.text for item in snapshot.items if item.item_ref != existing_ref} == {
        f"Concurrent task {index}" for index in range(7)
    }


def test_mixed_shape_is_readable_but_fails_closed_on_write(todo_store):
    service, session_factory = todo_store
    list_ref = _seed_note(
        session_factory,
        items=[
            {"id": "itm_existing0001", "text": "Canonical", "done": False},
            {"text": "Legacy", "done": False},
        ],
    )
    before = _raw_items(session_factory)

    assert len(service.list_items(owner="alice", list_ref=list_ref).items) == 2
    with pytest.raises(TodoDataIntegrityError):
        service.add_item(
            owner="alice",
            list_ref=list_ref,
            text="Must not be written",
            idempotency_key="mixed-shape-add",
        )
    assert _raw_items(session_factory) == before


def test_mutations_require_an_idempotency_key_and_one_target_selector(todo_store):
    service, session_factory = todo_store
    list_ref = _seed_note(session_factory)

    with pytest.raises(TodoValidationError):
        service.add_item(
            owner="alice", list_ref=list_ref, text="Task", idempotency_key=""
        )
    with pytest.raises(TodoValidationError):
        service.complete_item(
            owner="alice",
            list_ref=list_ref,
            item_ref=None,
            text=None,
            idempotency_key="selector-missing",
        )
    with pytest.raises(TodoValidationError):
        service.remove_item(
            owner="alice",
            list_ref=list_ref,
            item_ref="todo-item:v1:itm_unknown0001",
            text="Task",
            idempotency_key="selector-double",
        )

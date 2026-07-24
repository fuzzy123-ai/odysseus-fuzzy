"""Focused, synthetic file-backed SQLite tests for TodoDomainService."""
import json
import pytest

from core.database import Base, Note
from src.todo_domain_service import (TodoAmbiguousMatchError, TodoConflictError,
    TodoDomainError, TodoDomainService, TodoIdempotencyConflictError, TodoNotFoundError, TodoValidationError)
from tests.helpers.sqlite_db import make_temp_sqlite


@pytest.fixture
def database():
    factory, engine, tmpfile = make_temp_sqlite(Base.metadata)
    try: yield factory
    finally:
        engine.dispose(); tmpfile.close()

def service(factory, retries=3): return TodoDomainService(factory, Note, max_retries=retries)

def _session_type(factory):
    session = factory()
    try: return type(session)
    finally: session.close()

def seed(factory, *, note_id="list-a", owner="alice", items=None):
    db = factory(); db.add(Note(id=note_id, owner=owner, title="private list", note_type="checklist",
                                items=json.dumps(items) if items is not None else None)); db.commit(); db.close()

def items(factory, note_id="list-a"):
    db = factory(); row = db.query(Note).filter(Note.id == note_id).one(); result = json.loads(row.items) if row.items else []; db.close(); return result

def test_add_is_idempotent_and_receipt_is_content_free(database):
    seed(database, items=[]); todo = service(database)
    first = todo.add(owner="alice", list_ref="list-a", text="private todo", idempotency_key="request-1")
    second = todo.add(owner="alice", list_ref="list-a", text="private todo", idempotency_key="request-1")
    assert (first.transaction_status, second.transaction_status) == ("committed", "idempotent_noop")
    assert first.item_ref == second.item_ref and len(items(database)) == 1
    assert "private todo" not in repr(first) and first.evidence_refs_redacted
    persisted = json.dumps(items(database))
    assert "request-1" not in persisted and "idempotency_ref" in persisted

def test_same_key_with_different_payload_fails_closed(database):
    seed(database, items=[]); todo = service(database)
    todo.add(owner="alice", list_ref="list-a", text="one", idempotency_key="key")
    with pytest.raises(TodoIdempotencyConflictError): todo.add(owner="alice", list_ref="list-a", text="two", idempotency_key="key")
    assert [item["text"] for item in items(database)] == ["one"]

def test_add_survives_frontend_removal_of_idempotency_metadata(database):
    seed(database, items=[]); todo = service(database)
    first = todo.add(owner="alice", list_ref="list-a", text="one", idempotency_key="key")
    db = database(); row = db.query(Note).filter(Note.id == "list-a").one(); stored = json.loads(row.items)
    stored[0].pop("idempotency_ref"); stored[0].pop("idempotency_payload_hash"); row.items = json.dumps(stored); db.commit(); db.close()
    second = todo.add(owner="alice", list_ref="list-a", text="one", idempotency_key="key")
    assert second.transaction_status == "idempotent_noop" and second.item_ref == first.item_ref and len(items(database)) == 1
    with pytest.raises(TodoIdempotencyConflictError): todo.add(owner="alice", list_ref="list-a", text="changed", idempotency_key="key")

def test_mutation_upgrades_legacy_items_and_preserves_unknown_fields(database):
    seed(database, items=[{"text":"legacy", "done":False, "indent":2, "custom":{"ui":True}}]); todo = service(database)
    receipt = todo.complete(owner="alice", list_ref="list-a", text="legacy"); item = items(database)[0]
    assert item["id"] == receipt.item_ref and item["done"] is True
    assert item["indent"] == 2 and item["custom"] == {"ui":True}

def test_list_reads_legacy_without_mutation_or_text_exposure(database):
    seed(database, items=[{"text":"private legacy", "done":False}]); snapshot = service(database).list(owner="alice", list_ref="list-a")
    assert snapshot.open_count == 1 and snapshot.items[0].item_ref is None
    assert "private legacy" not in repr(snapshot) and items(database)[0] == {"text":"private legacy", "done":False}

def test_missing_legacy_done_is_open_on_read_and_materialised_on_mutation(database):
    seed(database, items=[{"text":"legacy"}]); todo = service(database)
    assert todo.list(owner="alice", list_ref="list-a").open_count == 1
    todo.add(owner="alice", list_ref="list-a", text="new", idempotency_key="key")
    assert items(database)[0]["done"] is False

def test_complete_reopen_remove_by_full_item_ref(database):
    seed(database, items=[]); todo = service(database)
    added = todo.add(owner="alice", list_ref="list-a", text="one", idempotency_key="key")
    done = todo.complete(owner="alice", list_ref="list-a", item_ref=added.item_ref)
    open_ = todo.reopen(owner="alice", list_ref="list-a", item_ref=added.item_ref)
    gone = todo.remove(owner="alice", list_ref="list-a", item_ref=added.item_ref)
    assert (done.previous_state, done.current_state, done.open_count) == (False, True, 0)
    assert (open_.previous_state, open_.current_state, open_.open_count) == (True, False, 1)
    assert (gone.previous_state, gone.current_state, gone.open_count) == (False, None, 0) and items(database) == []

def test_ambiguous_text_fails_closed_with_content_free_candidate_refs(database):
    seed(database, items=[{"text":"same", "done":False}, {"text":"same", "done":False}]); todo = service(database)
    with pytest.raises(TodoAmbiguousMatchError) as caught: todo.complete(owner="alice", list_ref="list-a", text="same")
    assert len(caught.value.candidate_refs) == 2 and all("same" not in ref for ref in caught.value.candidate_refs)
    assert items(database)[0].get("id") is None

def test_owner_scope_is_exact_including_null_owner(database):
    seed(database, note_id="owned", owner="alice", items=[]); seed(database, note_id="null", owner=None, items=[]); todo = service(database)
    with pytest.raises(TodoNotFoundError): todo.list(owner="bob", list_ref="owned")
    with pytest.raises(TodoNotFoundError): todo.list(owner=None, list_ref="owned")
    with pytest.raises(TodoNotFoundError): todo.list(owner="alice", list_ref="null")
    assert todo.list(owner=None, list_ref="null").open_count == 0

def test_full_note_id_and_owner_are_required_not_legacy_prefixes(database):
    item_ref = "a1b2c3d4"
    seed(database, note_id="full-note-id-123", owner="alice", items=[{"id":item_ref, "text":"one", "done":False}])
    todo = service(database)
    with pytest.raises(TodoNotFoundError): todo.list(owner="alice", list_ref="full-note")
    with pytest.raises(TodoNotFoundError): todo.add(owner="alice", list_ref="full-note", text="new", idempotency_key="prefix")
    with pytest.raises(TodoNotFoundError): todo.complete(owner="bob", list_ref="full-note-id-123", item_ref=item_ref)
    assert todo.list(owner="alice", list_ref="full-note-id-123").open_count == 1
    assert todo.complete(owner="alice", list_ref="full-note-id-123", item_ref=item_ref).current_state is True

def test_validation_and_missing_reference_fail_closed(database):
    seed(database, items=[]); todo = service(database)
    with pytest.raises(TodoValidationError): todo.add(owner="alice", list_ref="list-a", text=" ", idempotency_key="key")
    with pytest.raises(TodoValidationError): todo.complete(owner="alice", list_ref="list-a", item_ref="x", text="x")
    with pytest.raises(TodoNotFoundError): todo.complete(owner="alice", list_ref="list-a", item_ref="00000000-0000-0000-0000-000000000000")

def test_frontend_base36_item_refs_are_mutable_but_unsafe_refs_are_rejected(database):
    seed(database, items=[{"id":"a1b2c3d4", "text":"ui", "done":False}]); todo = service(database)
    assert todo.complete(owner="alice", list_ref="list-a", item_ref="a1b2c3d4").current_state is True
    with pytest.raises(TodoValidationError): todo.reopen(owner="alice", list_ref="list-a", item_ref="https://unsafe")

def test_cas_retries_after_competing_service_write(database, monkeypatch):
    seed(database, items=[]); todo = service(database, retries=2); session_type = _session_type(database); original = session_type.execute; calls = {"n":0}
    def conflict_once(session, statement, *args, **kwargs):
        if getattr(statement, "is_update", False) and not calls["n"]:
            calls["n"] += 1; other = database(); row = other.query(Note).filter(Note.id == "list-a").one()
            row.items = json.dumps([{"text":"concurrent", "done":False}]); other.commit(); other.close()
        return original(session, statement, *args, **kwargs)
    monkeypatch.setattr(session_type, "execute", conflict_once)
    assert todo.add(owner="alice", list_ref="list-a", text="requested", idempotency_key="retry").transaction_status == "committed"
    assert [item["text"] for item in items(database)] == ["concurrent", "requested"]

def test_add_retry_preserves_concurrent_completion(database, monkeypatch):
    existing_ref = "00000000-0000-0000-0000-000000000001"
    seed(database, items=[{"id":existing_ref, "text":"existing", "done":False}])
    todo = service(database, retries=2); session_type = _session_type(database); original = session_type.execute; completed = {"value":False}
    def complete_between_read_and_cas(session, statement, *args, **kwargs):
        if getattr(statement, "is_update", False) and not completed["value"]:
            completed["value"] = True; other = database(); row = other.query(Note).filter(Note.id == "list-a").one(); stored = json.loads(row.items); stored[0]["done"] = True; row.items = json.dumps(stored); other.commit(); other.close()
        return original(session, statement, *args, **kwargs)
    monkeypatch.setattr(session_type, "execute", complete_between_read_and_cas)
    added = todo.add(owner="alice", list_ref="list-a", text="new", idempotency_key="race")
    stored = {item["id"]: item for item in items(database)}
    assert stored[existing_ref]["done"] is True and stored[added.item_ref]["text"] == "new"

def test_cas_conflict_is_bounded(database, monkeypatch):
    seed(database, items=[]); todo = service(database, retries=1); session_type = _session_type(database); original = session_type.execute; entered = {"value":False}
    def conflict(session, statement, *args, **kwargs):
        if getattr(statement, "is_update", False) and not entered["value"]:
            entered["value"] = True; other = database(); row = other.query(Note).filter(Note.id == "list-a").one(); row.items = json.dumps([{"text":"other", "done":False}]); other.commit(); other.close()
        return original(session, statement, *args, **kwargs)
    monkeypatch.setattr(session_type, "execute", conflict)
    with pytest.raises(TodoConflictError): todo.add(owner="alice", list_ref="list-a", text="requested", idempotency_key="retry")

@pytest.mark.parametrize("field,value", [("archived", True), ("note_type", "note")])
def test_cas_rejects_concurrent_decanonicalisation(database, monkeypatch, field, value):
    seed(database, items=[]); todo = service(database, retries=2); session_type = _session_type(database); original = session_type.execute; changed = {"value":False}
    def decanonicalise(session, statement, *args, **kwargs):
        if getattr(statement, "is_update", False) and not changed["value"]:
            changed["value"] = True; other = database(); row = other.query(Note).filter(Note.id == "list-a").one(); setattr(row, field, value); other.commit(); other.close()
        return original(session, statement, *args, **kwargs)
    monkeypatch.setattr(session_type, "execute", decanonicalise)
    with pytest.raises(TodoNotFoundError): todo.add(owner="alice", list_ref="list-a", text="requested", idempotency_key="retry")

@pytest.mark.parametrize("archived,note_type", [(False, "note"), (True, "checklist")])
def test_only_active_checklists_are_canonical_todo_lists(database, archived, note_type):
    seed(database, items=[{"id":"00000000-0000-0000-0000-000000000001", "text":"one", "done":False}])
    db = database(); row = db.query(Note).filter(Note.id == "list-a").one(); row.archived = archived; row.note_type = note_type; db.commit(); db.close()
    todo = service(database)
    operations = (
        lambda: todo.list(owner="alice", list_ref="list-a"),
        lambda: todo.add(owner="alice", list_ref="list-a", text="new", idempotency_key="canonical"),
        lambda: todo.complete(owner="alice", list_ref="list-a", item_ref="00000000-0000-0000-0000-000000000001"),
        lambda: todo.reopen(owner="alice", list_ref="list-a", item_ref="00000000-0000-0000-0000-000000000001"),
        lambda: todo.remove(owner="alice", list_ref="list-a", item_ref="00000000-0000-0000-0000-000000000001"),
    )
    for operation in operations:
        with pytest.raises(TodoNotFoundError): operation()

@pytest.mark.parametrize("bad_items", [
    [{"text": 42, "done": False}],
    [{"text": "one", "done": "false"}],
    [{"text": "one", "done": False, "id": "not-an-opaque-id"}],
    [{"text": "one", "done": False, "id": "00000000-0000-0000-0000-000000000001"},
     {"text": "two", "done": False, "id": "00000000-0000-0000-0000-000000000001"}],
])
def test_list_fail_closed_for_malformed_or_duplicate_stored_items(database, bad_items):
    seed(database, items=bad_items)
    with pytest.raises(TodoDomainError): service(database).list(owner="alice", list_ref="list-a")
    assert items(database) == bad_items

def test_malformed_json_has_no_private_cause(database):
    seed(database, items=[]); db = database(); row = db.query(Note).filter(Note.id == "list-a").one(); row.items = '{"private":"broken"'; db.commit(); db.close()
    with pytest.raises(TodoDomainError) as caught: service(database).list(owner="alice", list_ref="list-a")
    assert caught.value.__cause__ is None and "private" not in str(caught.value)

def test_unexpected_mutation_error_has_no_private_chained_cause():
    class BrokenSession:
        def query(self, *_args): raise RuntimeError("private SQL parameter: secret")
        def rollback(self): pass
        def close(self): pass
    todo = TodoDomainService(lambda: BrokenSession(), Note)
    with pytest.raises(TodoDomainError) as caught:
        todo.add(owner="alice", list_ref="list-a", text="private", idempotency_key="key")
    assert caught.value.__cause__ is None and "secret" not in str(caught.value)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ClarificationEvent, ClarificationRun
from src.auth_helpers import SessionPrincipal
from src.clarification_store import ClarificationStore, ClarificationStoreError


def _principal(owner="alice", sid="owner_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"):
    return SessionPrincipal(owner=owner, creator_subject_id=sid, authority="browser_session")


def _session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _request():
    return {
        "schema": "odysseus.clarification_request.v2",
        "scope": "project",
        "intent_summary": "Build a local document review flow.",
        "questions": [
            {
                "key": "target_documents",
                "type": "short_text",
                "prompt": "Which documents should be reviewed first?",
                "required": True,
                "reason": "The document source changes scope and privacy boundaries.",
                "category": "scope",
            },
            {
                "key": "tone",
                "type": "single_select",
                "prompt": "Which review tone should be used?",
                "required": False,
                "reason": "This only changes wording.",
                "options": [{"label": "Concise"}, {"label": "Detailed", "recommended": True}],
            },
        ],
        "batch": {"label": "Scope", "index": 1, "total": 1, "max_visible_questions": 5},
        "defaults_visible": False,
    }


def test_clarification_store_creates_owner_scoped_run_and_request_event():
    store = ClarificationStore(session_factory=_session_factory())

    result = store.create_run(principal=_principal(), session_id="sess-1", request=_request(), project_slug="demo")

    assert result.run["schema"] == "odysseus.clarification_run.v1"
    assert result.run["owner"] == "alice"
    assert result.run["status"] == "clarifying"
    assert result.run["version"] == 1
    assert result.run["unresolved_required_question_ids"] == ("target_documents",)
    assert result.event["event_type"] == "request_created"
    assert result.event["version"] == 1
    assert result.run["raw_content_visible"] is False
    assert store.read_run(principal=_principal("bob", "owner_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"), clarification_id=result.run["clarification_id"]) is None


def test_clarification_store_answer_is_versioned_and_idempotent():
    factory = _session_factory()
    store = ClarificationStore(session_factory=factory)
    created = store.create_run(principal=_principal(), session_id="sess-1", request=_request())

    answered = store.answer_question(
        principal=_principal(),
        clarification_id=created.run["clarification_id"],
        question_id="target_documents",
        answer={"text": "reports folder"},
        expected_version=1,
        idempotency_key="idem-answer-1",
    )
    replay = store.answer_question(
        principal=_principal(),
        clarification_id=created.run["clarification_id"],
        question_id="target_documents",
        answer={"text": "reports folder"},
        expected_version=1,
        idempotency_key="idem-answer-1",
    )

    assert answered.run["version"] == 2
    assert answered.run["status"] == "understanding_review"
    assert answered.run["unresolved_required_count"] == 0
    assert answered.event["event_type"] == "question_answered"
    assert replay.idempotent_replay is True
    assert replay.run["version"] == 2

    db = factory()
    try:
        assert db.query(ClarificationRun).count() == 1
        assert db.query(ClarificationEvent).count() == 2
    finally:
        db.close()


def test_clarification_answer_event_carries_privacy_boundary_and_review_candidate():
    factory = _session_factory()
    store = ClarificationStore(session_factory=factory)
    created = store.create_run(principal=_principal(), session_id="sess-1", request=_request(), project_slug="demo")

    answered = store.answer_question(
        principal=_principal(),
        clarification_id=created.run["clarification_id"],
        question_id="tone",
        answer={"label": "Concise"},
        expected_version=1,
        idempotency_key="idem-tone",
    )

    payload = answered.event["payload"]
    assert payload["privacy_boundary"]["answer_storage_scope"] == "project"
    assert payload["privacy_boundary"]["global_memory_write_allowed"] is False
    assert payload["memory_candidate"]["status"] == "proposed"
    assert payload["memory_candidate"]["requires_review"] is True
    assert payload["memory_candidate"]["truth_write_allowed"] is False


def test_inbox_action_receipt_is_sid_bound_and_reads_cancelled_lifecycle_only():
    factory = _session_factory()
    store = ClarificationStore(session_factory=factory)
    browser = _principal()
    mcp = SessionPrincipal(owner="alice", creator_subject_id=browser.creator_subject_id, authority="mcp_subject")
    created = store.create_run(
        principal=browser, session_id="receipt-session", request=_request(),
        clarification_id="clar-receipt-001",
    )
    assert store.read_inbox_action_receipt(
        principal=mcp, clarification_id=created.run["clarification_id"], event_version=1,
        expected_current_version=1, expected_receipt_handle=None,
    ) is None
    paused = store.pause_run(
        principal=mcp, clarification_id=created.run["clarification_id"], expected_version=1,
        idempotency_key="receipt-pause",
    )
    discovered = store.read_inbox_action_receipt(
        principal=mcp, clarification_id=created.run["clarification_id"], event_version=2,
        expected_current_version=2, expected_receipt_handle=None,
    )
    assert discovered is not None
    assert discovered.operation == "pause" and discovered.committed_status == "paused"
    assert discovered.receipt_handle.startswith("hir1:c:")
    cancelled = store.cancel_run(
        principal=mcp, clarification_id=created.run["clarification_id"], expected_version=paused.run["version"],
        idempotency_key="receipt-cancel",
    )
    historical = store.read_inbox_action_receipt(
        principal=mcp, clarification_id=created.run["clarification_id"], event_version=2,
        expected_current_version=cancelled.run["version"], expected_receipt_handle=discovered.receipt_handle,
    )
    receipt = store.read_inbox_action_receipt(
        principal=mcp, clarification_id=created.run["clarification_id"], event_version=3,
        expected_current_version=cancelled.run["version"], expected_receipt_handle=None,
    )
    assert historical is not None
    assert historical.operation == "pause" and historical.committed_status == "paused"
    assert receipt is not None
    assert receipt.operation == "cancel" and receipt.committed_status == "cancelled"
    assert "receipt-session" not in repr(receipt)


def test_inbox_receipt_rejects_duplicate_json_but_accepts_historical_alias_with_same_sid():
    factory = _session_factory()
    store = ClarificationStore(session_factory=factory)
    browser = _principal()
    mcp = SessionPrincipal(owner="alice", creator_subject_id=browser.creator_subject_id, authority="mcp_subject")
    created = store.create_run(principal=browser, session_id="receipt-session", request=_request(), clarification_id="clar-receipt-002")
    paused = store.pause_run(
        principal=mcp, clarification_id=created.run["clarification_id"], expected_version=1,
        idempotency_key="receipt-alias-pause",
    )
    db = factory()
    try:
        db.query(ClarificationRun).filter(
            ClarificationRun.id == created.run["clarification_id"],
        ).update({"owner": "renamed"})
        db.query(ClarificationEvent).filter(
            ClarificationEvent.clarification_id == created.run["clarification_id"],
            ClarificationEvent.event_type == "run_paused",
            ClarificationEvent.version == paused.run["version"],
        ).update({"owner": "renamed"})
        db.commit()
    finally:
        db.close()
    renamed = SessionPrincipal(owner="renamed", creator_subject_id=browser.creator_subject_id, authority="mcp_subject")
    historical = store.read_inbox_action_receipt(
        principal=renamed, clarification_id=created.run["clarification_id"], event_version=paused.run["version"],
        expected_current_version=paused.run["version"], expected_receipt_handle=None,
    )
    assert historical is not None and historical.operation == "pause"
    db = factory()
    try:
        event = db.query(ClarificationEvent).filter(
            ClarificationEvent.clarification_id == created.run["clarification_id"],
            ClarificationEvent.event_type == "run_paused",
            ClarificationEvent.version == paused.run["version"],
        ).one()
        event.payload_json = event.payload_json[:-1] + ',"status":"paused"}'
        db.commit()
    finally:
        db.close()
    with pytest.raises(ClarificationStoreError) as exc:
        store.read_inbox_action_receipt(
            principal=renamed, clarification_id=created.run["clarification_id"], event_version=paused.run["version"],
            expected_current_version=paused.run["version"], expected_receipt_handle=None,
        )
    assert exc.value.code == "inbox_failed"


def test_clarification_store_routes_secret_answers_to_secure_handoff():
    store = ClarificationStore(session_factory=_session_factory())
    created = store.create_run(principal=_principal(), session_id="sess-1", request=_request())

    with pytest.raises(ClarificationStoreError) as exc:
        store.answer_question(
            principal=_principal(),
            clarification_id=created.run["clarification_id"],
            question_id="target_documents",
            answer={"text": "api_key=super-secret"},
            expected_version=1,
            idempotency_key="idem-handoff",
        )

    assert exc.value.code == "secure_handoff_required"
    assert exc.value.current_version == 1
    assert exc.value.details["secure_handoff"]["value_visible"] is False
    assert "super-secret" not in repr(exc.value.details)


def test_clarification_store_rejects_stale_expected_version():
    store = ClarificationStore(session_factory=_session_factory())
    created = store.create_run(principal=_principal(), session_id="sess-1", request=_request())
    store.answer_question(
        principal=_principal(),
        clarification_id=created.run["clarification_id"],
        question_id="target_documents",
        answer="reports",
        expected_version=1,
        idempotency_key="idem-answer-1",
    )

    with pytest.raises(ClarificationStoreError) as exc:
        store.answer_question(
            principal=_principal(),
            clarification_id=created.run["clarification_id"],
            question_id="tone",
            answer="Concise",
            expected_version=1,
            idempotency_key="idem-answer-2",
        )

    assert exc.value.code == "version_conflict"
    assert exc.value.current_version == 2


def test_clarification_store_confirms_understanding_only_after_required_answers():
    store = ClarificationStore(session_factory=_session_factory())
    created = store.create_run(principal=_principal(), session_id="sess-1", request=_request())

    with pytest.raises(ClarificationStoreError, match="required clarification questions remain"):
        store.confirm_understanding(
            principal=_principal(),
            clarification_id=created.run["clarification_id"],
            understanding_summary="Review reports first.",
            expected_version=1,
            idempotency_key="idem-ready-early",
        )

    answered = store.answer_question(
        principal=_principal(),
        clarification_id=created.run["clarification_id"],
        question_id="target_documents",
        answer="reports",
        expected_version=1,
        idempotency_key="idem-answer-1",
    )
    ready = store.confirm_understanding(
        principal=_principal(),
        clarification_id=created.run["clarification_id"],
        understanding_summary="Review reports first.",
        expected_version=answered.run["version"],
        idempotency_key="idem-ready-1",
    )

    assert ready.run["status"] == "ready_for_plan"
    assert ready.run["ready_for_plan"] is True
    assert ready.event["event_type"] == "ready_for_plan"


def test_clarification_store_rejects_secrets_private_paths_and_unknown_questions():
    store = ClarificationStore(session_factory=_session_factory())

    unsafe = _request()
    unsafe["questions"][0]["prompt"] = "Use token=SECRET please"
    with pytest.raises(ClarificationStoreError) as exc:
        store.create_run(principal=_principal(), session_id="sess-1", request=unsafe)
    assert exc.value.code == "unsafe_content"

    created = store.create_run(principal=_principal(), session_id="sess-1", request=_request())
    with pytest.raises(ClarificationStoreError) as unknown:
        store.answer_question(
            principal=_principal(),
            clarification_id=created.run["clarification_id"],
            question_id="missing_question",
            answer="reports",
            expected_version=1,
            idempotency_key="idem-missing",
        )
    assert unknown.value.code == "unknown_question"

    with pytest.raises(ClarificationStoreError) as unsafe_answer:
        store.answer_question(
            principal=_principal(),
            clarification_id=created.run["clarification_id"],
            question_id="target_documents",
            answer="C:/Users/private/report.pdf",
            expected_version=1,
            idempotency_key="idem-path",
        )
    assert unsafe_answer.value.code == "unsafe_content"


def test_sid_principal_hides_same_alias_different_subject_and_replay_is_snapshot():
    store = ClarificationStore(session_factory=_session_factory())
    alice = _principal("alice", "owner_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    created = store.create_run(principal=alice, session_id="sid-session", request=_request())
    assert store.read_run(principal=_principal("alice", "owner_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"), clarification_id=created.run["clarification_id"]) is None
    first = store.answer_question(principal=alice, clarification_id=created.run["clarification_id"], question_id="target_documents", answer={"text": "reports"}, expected_version=1, idempotency_key="snapshot")
    store.confirm_understanding(principal=alice, clarification_id=created.run["clarification_id"], understanding_summary="Reports first.", expected_version=2, idempotency_key="later")
    replay = store.answer_question(principal=alice, clarification_id=created.run["clarification_id"], question_id="target_documents", answer={"text": "reports"}, expected_version=1, idempotency_key="snapshot")
    assert replay.idempotent_replay is True
    assert replay.run == first.run
    assert "__odysseus_clarification_private_v1" not in repr(store.read_events(principal=alice, clarification_id=created.run["clarification_id"]))

def test_explicit_local_principal_reads_only_local_null_subject_rows():
    factory = _session_factory()
    store = ClarificationStore(session_factory=factory)
    local = SessionPrincipal(owner="local", creator_subject_id=None, authority="local")
    created = store.create_run(principal=local, session_id="local-session", request=_request())
    assert store.read_run(principal=local, clarification_id=created.run["clarification_id"]) is not None
    assert store.read_run(principal=_principal(), clarification_id=created.run["clarification_id"]) is None

def test_secret_handoff_precedes_public_payload_rejection_and_replay_conflict():
    store = ClarificationStore(session_factory=_session_factory())
    principal = _principal()
    created = store.create_run(principal=principal, session_id="secure-order", request=_request())
    with pytest.raises(ClarificationStoreError) as handoff:
        store.answer_question(principal=principal, clarification_id=created.run["clarification_id"], question_id="target_documents", answer={"text": "api_key=not-exposed"}, expected_version=1, idempotency_key="secure-key")
    assert handoff.value.code == "secure_handoff_required"
    first = store.answer_question(principal=principal, clarification_id=created.run["clarification_id"], question_id="target_documents", answer={"text": "reports"}, expected_version=1, idempotency_key="replay-key")
    with pytest.raises(ClarificationStoreError) as conflict:
        store.answer_question(principal=principal, clarification_id=created.run["clarification_id"], question_id="target_documents", answer={"text": "different"}, expected_version=1, idempotency_key="replay-key")
    assert first.run["version"] == 2 and conflict.value.code == "idempotency_conflict"


def test_invalid_browser_subject_is_rejected_before_store_access():
    store = ClarificationStore(session_factory=_session_factory())
    with pytest.raises(ClarificationStoreError, match="trusted session principal"):
        store.create_run(principal=_principal(sid="sid-a"), session_id="bad-subject", request=_request())

def test_file_sqlite_cas_and_idempotency_races_are_observed(tmp_path):
    import threading
    from sqlalchemy import create_engine, event
    database = tmp_path / "clarification-race.db"
    engine = create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    principal = _principal()
    seed = ClarificationStore(session_factory=factory)
    run = seed.create_run(principal=principal, session_id="race-session", request=_request()).run
    barrier, outcomes, errors, update_threads = threading.Barrier(2), [], [], []
    def writer(key, text):
        try:
            result = ClarificationStore(session_factory=factory).answer_question(principal=principal, clarification_id=run["clarification_id"], question_id="target_documents", answer={"text": text}, expected_version=1, idempotency_key=key)
            outcomes.append((key, result.idempotent_replay, result.run["version"]))
        except ClarificationStoreError as exc:
            outcomes.append((key, exc.code, 0))
        except BaseException as exc:
            errors.append(exc)
    def before_cas(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith("update clarification_runs"):
            update_threads.append(threading.get_ident())
            barrier.wait(timeout=5)
    event.listen(engine, "before_cursor_execute", before_cas)
    first, second = threading.Thread(target=writer, args=("distinct-a", "reports")), threading.Thread(target=writer, args=("distinct-b", "invoices"))
    try:
        first.start(); second.start()
        first.join(timeout=10); second.join(timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", before_cas)
        barrier.abort()
        first.join(timeout=1); second.join(timeout=1)
    assert not first.is_alive() and not second.is_alive() and not errors and len(update_threads) == 2 and len(set(update_threads)) == 2
    assert sum(item[1] is False for item in outcomes) == 1
    assert sum(item[1] == "version_conflict" for item in outcomes) == 1
    persisted = seed.read_run(principal=principal, clarification_id=run["clarification_id"])
    assert persisted["version"] == 2 and len(seed.read_events(principal=principal, clarification_id=run["clarification_id"])) == 2


def test_replay_is_original_after_later_mutation_and_metadata_sql_is_closed(tmp_path):
    from sqlalchemy import create_engine, event
    database = tmp_path / "clarification-replay.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    store, principal = ClarificationStore(session_factory=factory), _principal()
    created = store.create_run(principal=principal, session_id="replay-session", request=_request())
    first = store.answer_question(principal=principal, clarification_id=created.run["clarification_id"], question_id="target_documents", answer={"text": "reports"}, expected_version=1, idempotency_key="same")
    store.confirm_understanding(principal=principal, clarification_id=created.run["clarification_id"], understanding_summary="Reports first.", expected_version=2, idempotency_key="later")
    replay = store.answer_question(principal=principal, clarification_id=created.run["clarification_id"], question_id="target_documents", answer={"text": "reports"}, expected_version=1, idempotency_key="same")
    assert replay.idempotent_replay and replay.run == first.run and replay.event == first.event
    assert isinstance(replay.run["unresolved_required_question_ids"], tuple)
    assert "__odysseus_clarification_private_v1" not in repr(store.read_events(principal=principal, clarification_id=created.run["clarification_id"]))
    statements=[]
    def capture(conn, cursor, statement, parameters, context, executemany): statements.append(statement.lower())
    event.listen(engine, "before_cursor_execute", capture)
    try: store.list_inbox_metadata(principal=principal)
    finally: event.remove(engine, "before_cursor_execute", capture)
    selects = [statement for statement in statements if "select" in statement]
    assert len(selects) == 1
    selected = selects[0]
    assert "request_json" not in selected and "answers_json" not in selected and "understanding_summary" not in selected and "session_id" not in selected and "payload_json" not in selected and "project_slug" not in selected and "clarification_runs.owner" not in selected
    metadata = store.list_inbox_metadata(principal=principal)[0]
    current = store.read_run(principal=principal, clarification_id=created.run["clarification_id"])
    assert metadata == {
        "clarification_id": created.run["clarification_id"],
        "status": current["status"],
        "version": current["version"],
        "ready_for_plan": current["ready_for_plan"],
        "owner": "self",
        "authority": "session_principal",
        "source": "clarification_store",
        "freshness": "current",
        "required_action": "review_clarification",
        "due": None,
        "latest_event_receipt": True,
        "updated_at": metadata["updated_at"],
    }
    mcp_metadata = store.list_inbox_metadata(principal=SessionPrincipal(
        principal.owner, principal.creator_subject_id, "mcp_subject",
    ))[0]
    assert (
        mcp_metadata["owner"] == "self"
        and mcp_metadata["event_receipt_present"] is True
        and mcp_metadata["required_action"]
    )
    assert mcp_metadata["redaction_flags"] == {
        "content": True, "identity": True, "events": True,
    }


def test_mcp_subject_uses_same_sid_scope_but_distinct_private_fingerprint_channel():
    import hashlib
    import json
    from src.clarification_store import _fingerprint

    browser = _principal()
    mcp = SessionPrincipal(browser.owner, browser.creator_subject_id, "mcp_subject")
    params = {}
    legacy_bytes = json.dumps({
        "sid": browser.creator_subject_id,
        "local": False,
        "operation": "pause",
        "params": params,
        "expected_version": 1,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    assert _fingerprint(browser, "pause", params, 1) == hashlib.sha256(legacy_bytes).hexdigest()
    assert _fingerprint(mcp, "pause", params, 1) != _fingerprint(browser, "pause", params, 1)

    factory = _session_factory()
    store = ClarificationStore(session_factory=factory)
    created = store.create_run(principal=browser, session_id="mcp-scope", request=_request())
    listed = store.list_inbox_metadata(principal=mcp)
    assert listed[0]["clarification_id"] == created.run["clarification_id"]
    assert listed[0]["authority"] == "mcp_subject"
    assert set(listed[0]) == {
        "clarification_id", "source", "owner", "status", "version",
        "ready_for_plan", "required_action", "due", "authority",
        "event_receipt_present", "freshness", "updated_at", "redaction_flags",
    }
    local_store, local = ClarificationStore(session_factory=factory), SessionPrincipal("local", None, "local")
    local_created = local_store.create_run(
        principal=local, session_id="local-inbox-shape", request=_request(),
        clarification_id="clar-local-shape-001",
    )
    local_metadata = local_store.list_inbox_metadata(principal=local)[0]
    assert set(local_metadata) == {
        "clarification_id", "status", "version", "ready_for_plan", "owner",
        "authority", "source", "freshness", "required_action", "due",
        "latest_event_receipt", "updated_at",
    }
    assert local_metadata["clarification_id"] == local_created.run["clarification_id"]
    assert local_metadata["authority"] == "session_principal"
    assert local_metadata["required_action"] == "review_clarification"

def test_create_commit_fault_rolls_back_run_and_first_event(tmp_path):
    from sqlalchemy import create_engine
    database = tmp_path / "clarification-fault.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(bind=engine)
    base_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    class CommitFaultSession:
        def __init__(self): self.inner = base_factory()
        def __getattr__(self, name): return getattr(self.inner, name)
        def commit(self): raise RuntimeError("injected commit fault")
        def close(self): self.inner.close()
    store = ClarificationStore(session_factory=CommitFaultSession)
    with pytest.raises(RuntimeError, match="injected commit fault"):
        store.create_run(principal=_principal(), session_id="fault-session", request=_request(), clarification_id="fault-run-001")
    db = base_factory()
    try:
        assert db.query(ClarificationRun).count() == 0
        assert db.query(ClarificationEvent).count() == 0
    finally:
        db.close()

def test_file_sqlite_same_key_race_replays_or_conflicts_by_canonical_payload(tmp_path):
    import threading
    from sqlalchemy import create_engine
    database = tmp_path / "clarification-same-key.db"
    engine = create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    principal = _principal(); seed = ClarificationStore(session_factory=factory)
    run = seed.create_run(principal=principal, session_id="same-key-session", request=_request()).run
    barrier, results, errors = threading.Barrier(2), [], []
    def write(text):
        try:
            barrier.wait(timeout=5)
            value = ClarificationStore(session_factory=factory).answer_question(principal=principal, clarification_id=run["clarification_id"], question_id="target_documents", answer={"text": text}, expected_version=1, idempotency_key="same-key")
            results.append((text, "replay" if value.idempotent_replay else "write"))
        except ClarificationStoreError as exc: results.append((text, exc.code))
        except BaseException as exc: errors.append(exc)
    a,b=threading.Thread(target=write,args=("reports",)),threading.Thread(target=write,args=("reports",));a.start();b.start();a.join(timeout=10);b.join(timeout=10)
    assert not a.is_alive() and not b.is_alive() and not errors
    assert sum(kind == "write" for _, kind in results) == 1
    assert sum(kind == "replay" for _, kind in results) == 1
    with pytest.raises(ClarificationStoreError) as conflict:
        seed.answer_question(principal=principal, clarification_id=run["clarification_id"], question_id="target_documents", answer={"text":"different"}, expected_version=1, idempotency_key="same-key")
    assert conflict.value.code == "idempotency_conflict"


def test_receipt_fault_leaves_no_partial_event_or_version(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    import src.clarification_store as module
    database=tmp_path / "clarification-mutation-fault.db";engine=create_engine(f"sqlite:///{database}");Base.metadata.create_all(bind=engine)
    base_factory=sessionmaker(bind=engine,autocommit=False,autoflush=False);principal=_principal();seed=ClarificationStore(session_factory=base_factory);run=seed.create_run(principal=principal,session_id="mutation-fault",request=_request()).run
    monkeypatch.setattr(module,"_receipt",lambda *args: (_ for _ in ()).throw(RuntimeError("receipt fault")))
    with pytest.raises(RuntimeError,match="receipt fault"):
        seed.answer_question(principal=principal,clarification_id=run["clarification_id"],question_id="target_documents",answer={"text":"reports"},expected_version=1,idempotency_key="receipt-fault")
    db=base_factory()
    try:
        assert db.query(ClarificationRun).filter_by(id=run["clarification_id"]).one().version==1
        assert db.query(ClarificationEvent).filter_by(clarification_id=run["clarification_id"]).count()==1
    finally: db.close()

def test_event_projection_fault_rolls_back_mutation_transaction(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    import src.clarification_store as module
    database=tmp_path / "clarification-event-fault.db";engine=create_engine(f"sqlite:///{database}");Base.metadata.create_all(bind=engine)
    factory=sessionmaker(bind=engine,autocommit=False,autoflush=False);principal=_principal();store=ClarificationStore(session_factory=factory);run=store.create_run(principal=principal,session_id="event-fault",request=_request()).run
    original=module._event
    def fail_new_event(row, subject):
        if row.event_type != "request_created": raise RuntimeError("event projection fault")
        return original(row,subject)
    monkeypatch.setattr(module,"_event",fail_new_event)
    with pytest.raises(RuntimeError,match="event projection fault"):
        store.answer_question(principal=principal,clarification_id=run["clarification_id"],question_id="target_documents",answer={"text":"reports"},expected_version=1,idempotency_key="event-fault")
    assert store.read_run(principal=principal,clarification_id=run["clarification_id"])["version"]==1
    assert len(store.read_events(principal=principal,clarification_id=run["clarification_id"]))==1
@pytest.mark.parametrize("right_text, expected", [("reports", {"write", "replay"}), ("invoices", {"write", "idempotency_conflict"})])
def test_file_sqlite_same_key_cases_synchronize_at_sql_cas(tmp_path, right_text, expected):
    import threading
    from sqlalchemy import create_engine, event
    engine=create_engine(f"sqlite:///{tmp_path / ('same-'+right_text+'.db')}",connect_args={"check_same_thread":False});Base.metadata.create_all(bind=engine)
    factory=sessionmaker(bind=engine,autocommit=False,autoflush=False);principal=_principal();seed=ClarificationStore(session_factory=factory);run=seed.create_run(principal=principal,session_id="same-cas",request=_request()).run
    barrier, attempts, results, errors=threading.Barrier(2),[],[],[]
    def before_cas(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().lower().startswith("update clarification_runs"):
            attempts.append(threading.get_ident());barrier.wait(timeout=5)
    def writer(text):
        try:
            result=ClarificationStore(session_factory=factory).answer_question(principal=principal,clarification_id=run["clarification_id"],question_id="target_documents",answer={"text":text},expected_version=1,idempotency_key="same-cas-key")
            results.append("replay" if result.idempotent_replay else "write")
        except ClarificationStoreError as exc: results.append(exc.code)
        except BaseException as exc: errors.append(exc)
    event.listen(engine,"before_cursor_execute",before_cas);left,right=threading.Thread(target=writer,args=("reports",)),threading.Thread(target=writer,args=(right_text,))
    try:
        left.start();right.start();left.join(timeout=10);right.join(timeout=10)
    finally:
        event.remove(engine,"before_cursor_execute",before_cas);barrier.abort();left.join(timeout=1);right.join(timeout=1)
    assert not left.is_alive() and not right.is_alive() and not errors
    assert len(attempts)==2 and len(set(attempts))==2 and set(results)==expected
    persisted=seed.read_run(principal=principal,clarification_id=run["clarification_id"])
    assert persisted["version"]==2 and len(seed.read_events(principal=principal,clarification_id=run["clarification_id"]))==2

def test_mutation_commit_fault_leaves_version_event_and_answers_unchanged(tmp_path):
    from sqlalchemy import create_engine
    engine=create_engine(f"sqlite:///{tmp_path / 'mutation-commit.db'}");Base.metadata.create_all(bind=engine);base=sessionmaker(bind=engine,autocommit=False,autoflush=False);principal=_principal();seed=ClarificationStore(session_factory=base);run=seed.create_run(principal=principal,session_id="mutation-commit",request=_request()).run
    class FaultSession:
        def __init__(self): self.inner=base();self.commits=0
        def __getattr__(self,name): return getattr(self.inner,name)
        def commit(self):
            self.commits+=1
            if self.commits==1: raise RuntimeError("mutation commit fault")
            return self.inner.commit()
        def close(self): self.inner.close()
    with pytest.raises(RuntimeError,match="mutation commit fault"):
        ClarificationStore(session_factory=FaultSession).answer_question(principal=principal,clarification_id=run["clarification_id"],question_id="target_documents",answer={"text":"reports"},expected_version=1,idempotency_key="mutation-commit")
    current=seed.read_run(principal=principal,clarification_id=run["clarification_id"])
    assert current["version"]==1 and current["answers"]=={} and len(seed.read_events(principal=principal,clarification_id=run["clarification_id"]))==1

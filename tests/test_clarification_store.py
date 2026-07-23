import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ClarificationEvent, ClarificationRun
from src.clarification_store import ClarificationStore, ClarificationStoreError


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

    result = store.create_run(owner="alice", session_id="sess-1", request=_request(), project_slug="demo")

    assert result.run["schema"] == "odysseus.clarification_run.v1"
    assert result.run["owner"] == "alice"
    assert result.run["status"] == "clarifying"
    assert result.run["version"] == 1
    assert result.run["unresolved_required_question_ids"] == ("target_documents",)
    assert result.event["event_type"] == "request_created"
    assert result.event["version"] == 1
    assert result.run["raw_content_visible"] is False
    assert store.read_run(owner="bob", clarification_id=result.run["clarification_id"]) is None


def test_clarification_store_answer_is_versioned_and_idempotent():
    factory = _session_factory()
    store = ClarificationStore(session_factory=factory)
    created = store.create_run(owner="alice", session_id="sess-1", request=_request())

    answered = store.answer_question(
        owner="alice",
        clarification_id=created.run["clarification_id"],
        question_id="target_documents",
        answer={"text": "reports folder"},
        expected_version=1,
        idempotency_key="idem-answer-1",
    )
    replay = store.answer_question(
        owner="alice",
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
    created = store.create_run(owner="alice", session_id="sess-1", request=_request(), project_slug="demo")

    answered = store.answer_question(
        owner="alice",
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


def test_clarification_store_routes_secret_answers_to_secure_handoff():
    store = ClarificationStore(session_factory=_session_factory())
    created = store.create_run(owner="alice", session_id="sess-1", request=_request())

    with pytest.raises(ClarificationStoreError) as exc:
        store.answer_question(
            owner="alice",
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
    created = store.create_run(owner="alice", session_id="sess-1", request=_request())
    store.answer_question(
        owner="alice",
        clarification_id=created.run["clarification_id"],
        question_id="target_documents",
        answer="reports",
        expected_version=1,
        idempotency_key="idem-answer-1",
    )

    with pytest.raises(ClarificationStoreError) as exc:
        store.answer_question(
            owner="alice",
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
    created = store.create_run(owner="alice", session_id="sess-1", request=_request())

    with pytest.raises(ClarificationStoreError, match="required clarification questions remain"):
        store.confirm_understanding(
            owner="alice",
            clarification_id=created.run["clarification_id"],
            understanding_summary="Review reports first.",
            expected_version=1,
            idempotency_key="idem-ready-early",
        )

    answered = store.answer_question(
        owner="alice",
        clarification_id=created.run["clarification_id"],
        question_id="target_documents",
        answer="reports",
        expected_version=1,
        idempotency_key="idem-answer-1",
    )
    ready = store.confirm_understanding(
        owner="alice",
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
        store.create_run(owner="alice", session_id="sess-1", request=unsafe)
    assert exc.value.code == "unsafe_content"

    created = store.create_run(owner="alice", session_id="sess-1", request=_request())
    with pytest.raises(ClarificationStoreError) as unknown:
        store.answer_question(
            owner="alice",
            clarification_id=created.run["clarification_id"],
            question_id="missing_question",
            answer="reports",
            expected_version=1,
            idempotency_key="idem-missing",
        )
    assert unknown.value.code == "unknown_question"

    with pytest.raises(ClarificationStoreError) as unsafe_answer:
        store.answer_question(
            owner="alice",
            clarification_id=created.run["clarification_id"],
            question_id="target_documents",
            answer="C:/Users/private/report.pdf",
            expected_version=1,
            idempotency_key="idem-path",
        )
    assert unsafe_answer.value.code == "unsafe_content"

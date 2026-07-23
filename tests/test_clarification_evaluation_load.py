import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from src.clarification_policy import evaluate_clarification_completeness
from src.clarification_store import ClarificationStore, ClarificationStoreError
from src.local_model_scheduler import classify_local_model_request


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _question(key: str, *, required: bool = True, depends_on: str = "") -> dict:
    payload = {
        "key": key,
        "type": "short_text",
        "prompt": f"Clarify {key}.",
        "required": required,
        "reason": "This answer changes the implementation plan.",
        "category": "scope",
    }
    if depends_on:
        payload["depends_on"] = depends_on
    return payload


def _request(questions: list[dict], *, scope: str = "project") -> dict:
    return {
        "schema": "odysseus.clarification_request.v2",
        "scope": scope,
        "intent_summary": "Build a local document review service.",
        "questions": questions,
        "batch": {"label": "Load", "index": 1, "total": 8, "max_visible_questions": 7},
        "defaults_visible": False,
    }


def test_complete_prompt_policy_enters_understanding_review_without_questions() -> None:
    review = evaluate_clarification_completeness(
        intent_text="Build a local PDF-only document review service with pytest evidence.",
        known_answers={
            "outcome": "Flag risky clauses in PDFs.",
            "target_users": "Internal reviewers.",
            "scope": "PDF only, no OCR.",
            "data_privacy": "Local only.",
            "acceptance_criteria": ["pytest passes", "sample PDF report generated"],
        },
        candidate_questions=[],
    )

    assert review["requires_clarification"] is False
    assert review["ready_for_understanding_review"] is True
    assert review["accepted_question_count"] == 0


def test_vague_prompt_policy_budgets_many_candidate_questions() -> None:
    candidates = [
        {
            "key": key,
            "prompt": f"What is the {key} requirement?",
            "required": True,
            "reason": "The answer changes the result.",
        }
        for key in (
            "outcome",
            "target_users",
            "scope",
            "data_privacy",
            "acceptance_criteria",
            "runtime_constraints",
            "design_direction",
            "live_permissions",
        )
    ]
    candidates.extend(candidates[:4])

    review = evaluate_clarification_completeness(
        intent_text="Make the AI check documents.",
        candidate_questions=candidates,
        max_visible_questions=3,
        max_total_questions=5,
    )

    assert review["requires_clarification"] is True
    assert len(review["accepted_questions"]) == 3
    assert review["accepted_question_count"] == 5
    assert any(item["reason"] == "duplicate" for item in review["rejected_questions"])
    assert any(item["reason"] == "budget_exceeded" for item in review["rejected_questions"])


def test_large_run_resume_conflict_and_revision_survive_store_reload() -> None:
    factory = _session_factory()
    store = ClarificationStore(session_factory=factory)
    questions = [_question(f"q{i:03d}") for i in range(55)]
    created = store.create_run(owner="alice", session_id="sess-load", request=_request(questions), project_slug="demo")
    clarification_id = created.run["clarification_id"]

    assert created.run["unresolved_required_count"] == 55
    resumed = store.read_active_run_for_session(owner="alice", session_id="sess-load")
    assert resumed is not None
    assert resumed["clarification_id"] == clarification_id

    answered = store.answer_question(
        owner="alice",
        clarification_id=clarification_id,
        question_id="q000",
        answer={"text": "local PDFs first"},
        expected_version=1,
        idempotency_key="idem-q000-a",
    )
    with pytest.raises(ClarificationStoreError) as stale:
        store.answer_question(
            owner="alice",
            clarification_id=clarification_id,
            question_id="q001",
            answer={"text": "DOCX too"},
            expected_version=1,
            idempotency_key="idem-q001-stale",
        )
    revised = store.answer_question(
        owner="alice",
        clarification_id=clarification_id,
        question_id="q000",
        answer={"text": "local PDFs and DOCX first"},
        expected_version=answered.run["version"],
        idempotency_key="idem-q000-b",
    )
    events = store.read_events(owner="alice", clarification_id=clarification_id)

    assert stale.value.code == "version_conflict"
    assert revised.event["event_type"] == "answer_revised"
    assert revised.run["unresolved_required_count"] == 54
    assert [event["event_type"] for event in events] == ["request_created", "question_answered", "answer_revised"]


def test_conditional_followups_are_persisted_without_expanding_visible_batch() -> None:
    store = ClarificationStore(session_factory=_session_factory())
    created = store.create_run(
        owner="alice",
        session_id="sess-conditional",
        request=_request([
            _question("q_parent"),
            _question("q_child", depends_on="q_parent"),
            _question("q_optional", required=False),
        ]),
    )

    questions = created.run["request"]["questions"]
    child = next(item for item in questions if item["key"] == "q_child")

    assert child["depends_on"] == "q_parent"
    assert created.run["request"]["batch"]["max_visible_questions"] == 7
    assert created.run["unresolved_required_question_ids"] == ("q_parent", "q_child")


def test_local_gemma_clarification_batch_is_foreground_without_live_call() -> None:
    kind = classify_local_model_request(
        surface="clarification",
        prompt_type="local_gemma_batch_question_generation_memory_maintenance",
    )

    assert kind == "foreground"

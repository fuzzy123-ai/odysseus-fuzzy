from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from routes.chat_routes import _clarification_gate_for_session
from src.clarification_store import ClarificationStore
from src.tool_policy import build_effective_tool_policy


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
            }
        ],
        "batch": {"label": "Scope", "index": 1, "total": 1, "max_visible_questions": 5},
        "defaults_visible": False,
    }


def test_active_clarification_session_blocks_plan_and_mutation_policy():
    store = ClarificationStore(session_factory=_session_factory())
    created = store.create_run(owner="alice", session_id="sess-1", request=_request())

    gate = _clarification_gate_for_session(owner="alice", session_id="sess-1", store=store)
    policy = build_effective_tool_policy(
        clarification_open=bool(gate["open"]),
        clarification_reason=gate["reason"],
    )

    assert gate["open"] is True
    assert gate["clarification_id"] == created.run["clarification_id"]
    assert policy.blocks("update_plan")
    assert policy.blocks("write_file")
    assert not policy.blocks("ask_user")

    answered = store.answer_question(
        owner="alice",
        clarification_id=created.run["clarification_id"],
        question_id="target_documents",
        answer="reports",
        expected_version=1,
        idempotency_key="idem-answer",
    )
    store.confirm_understanding(
        owner="alice",
        clarification_id=created.run["clarification_id"],
        understanding_summary="Review reports first.",
        expected_version=answered.run["version"],
        idempotency_key="idem-complete",
    )

    ready_gate = _clarification_gate_for_session(owner="alice", session_id="sess-1", store=store)
    assert ready_gate["open"] is False

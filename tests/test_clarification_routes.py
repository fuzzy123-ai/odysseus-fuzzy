from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from routes.clarification_routes import setup_clarification_routes
from src.clarification_store import ClarificationStore


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _app(factory, *, user="alice"):
    app = FastAPI()
    app.include_router(setup_clarification_routes(store=ClarificationStore(session_factory=factory)))

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        request.state.current_user = user
        return await call_next(request)

    return app


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


def _create(client):
    response = client.post(
        "/api/sessions/sess-1/clarification",
        json={"request": _request(), "project_slug": "demo"},
    )
    assert response.status_code == 200, response.text
    return response.json()["run"]


def test_clarification_routes_create_read_active_and_owner_scope():
    factory = _session_factory()
    alice = TestClient(_app(factory, user="alice"))
    run = _create(alice)

    active = alice.get("/api/sessions/sess-1/clarification")
    assert active.status_code == 200
    assert active.json()["active"] is True
    assert active.json()["clarification"]["clarification_id"] == run["clarification_id"]
    assert active.json()["clarification"]["owner"] == "alice"

    detail = alice.get(f"/api/clarifications/{run['clarification_id']}")
    assert detail.status_code == 200
    assert detail.json()["clarification"]["status"] == "clarifying"

    bob = TestClient(_app(factory, user="bob"))
    denied = bob.get(f"/api/clarifications/{run['clarification_id']}")
    assert denied.status_code == 404


def test_clarification_routes_answer_replay_conflict_and_revision():
    client = TestClient(_app(_session_factory(), user="alice"))
    run = _create(client)
    clarification_id = run["clarification_id"]

    answer = client.post(
        f"/api/clarifications/{clarification_id}/answers",
        json={
            "question_id": "target_documents",
            "answer": {"text": "reports folder"},
            "expected_version": 1,
            "idempotency_key": "idem-answer-1",
        },
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["run"]["version"] == 2
    assert answer.json()["event"]["event_type"] == "question_answered"

    replay = client.post(
        f"/api/clarifications/{clarification_id}/answers",
        json={
            "question_id": "target_documents",
            "answer": {"text": "reports folder"},
            "expected_version": 1,
            "idempotency_key": "idem-answer-1",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True

    stale = client.post(
        f"/api/clarifications/{clarification_id}/answers",
        json={
            "question_id": "tone",
            "answer": "Concise",
            "expected_version": 1,
            "idempotency_key": "idem-answer-2",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "version_conflict"
    assert stale.json()["detail"]["current_version"] == 2

    revision = client.post(
        f"/api/clarifications/{clarification_id}/answers",
        json={
            "question_id": "target_documents",
            "answer": {"text": "reports and invoices"},
            "expected_version": 2,
            "idempotency_key": "idem-answer-3",
        },
    )
    assert revision.status_code == 200, revision.text
    assert revision.json()["event"]["event_type"] == "answer_revised"
    assert revision.json()["run"]["answers"]["target_documents"]["answer"]["text"] == "reports and invoices"


def test_clarification_routes_secret_answer_returns_secure_handoff_intent():
    client = TestClient(_app(_session_factory(), user="alice"))
    run = _create(client)

    response = client.post(
        f"/api/clarifications/{run['clarification_id']}/answers",
        json={
            "question_id": "target_documents",
            "answer": {"text": "api_key=super-secret"},
            "expected_version": 1,
            "idempotency_key": "idem-handoff",
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "secure_handoff_required"
    assert detail["secure_handoff"]["value_visible"] is False
    assert detail["secure_handoff"]["value_stored"] is False
    assert "super-secret" not in response.text


def test_clarification_routes_actions_pause_reopen_cancel_and_complete():
    client = TestClient(_app(_session_factory(), user="alice"))
    run = _create(client)
    clarification_id = run["clarification_id"]

    pause = client.post(
        f"/api/clarifications/{clarification_id}/actions",
        json={"action": "pause", "expected_version": 1, "idempotency_key": "idem-pause"},
    )
    assert pause.status_code == 200, pause.text
    assert pause.json()["run"]["status"] == "paused"

    pause_replay = client.post(
        f"/api/clarifications/{clarification_id}/actions",
        json={"action": "pause", "expected_version": 1, "idempotency_key": "idem-pause"},
    )
    assert pause_replay.status_code == 200
    assert pause_replay.json()["idempotent_replay"] is True

    reopen = client.post(
        f"/api/clarifications/{clarification_id}/actions",
        json={"action": "reopen", "expected_version": 2, "idempotency_key": "idem-reopen"},
    )
    assert reopen.status_code == 200, reopen.text
    assert reopen.json()["run"]["status"] == "clarifying"

    answer = client.post(
        f"/api/clarifications/{clarification_id}/answers",
        json={
            "question_id": "target_documents",
            "answer": "reports",
            "expected_version": 3,
            "idempotency_key": "idem-answer",
        },
    )
    assert answer.status_code == 200, answer.text

    complete = client.post(
        f"/api/clarifications/{clarification_id}/actions",
        json={
            "action": "complete",
            "understanding_summary": "Review reports first.",
            "expected_version": 4,
            "idempotency_key": "idem-complete",
        },
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["run"]["status"] == "ready_for_plan"
    assert complete.json()["run"]["ready_for_plan"] is True

    cancelled_run = _create(client)
    cancel = client.post(
        f"/api/clarifications/{cancelled_run['clarification_id']}/actions",
        json={"action": "cancel", "expected_version": 1, "idempotency_key": "idem-cancel"},
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["run"]["status"] == "cancelled"

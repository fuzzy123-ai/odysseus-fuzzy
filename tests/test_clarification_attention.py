from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from routes.workspace_snapshot_routes import setup_workspace_snapshot_routes
from src.clarification_attention import (
    build_session_clarification_attention,
    build_workspace_clarification_status,
)
from src.clarification_store import ClarificationStore


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


def test_clarification_attention_tracks_active_and_ready_states():
    store = ClarificationStore(session_factory=_session_factory())
    created = store.create_run(owner="alice", session_id="sess-1", request=_request())

    attention = build_session_clarification_attention(owner="alice", session_id="sess-1", store=store)
    workspace = build_workspace_clarification_status(owner="alice", store=store)

    assert attention["active"] is True
    assert attention["reason"] == "clarification_required"
    assert attention["unresolved_required_count"] == 1
    assert workspace["status"] == "pending"
    assert workspace["pending_count"] == 1
    assert workspace["active_run_count"] == 1

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
        idempotency_key="idem-ready",
    )

    ready = build_session_clarification_attention(owner="alice", session_id="sess-1", store=store)
    assert ready["active"] is False
    assert ready["status"] == "ready_for_plan"


def test_workspace_snapshot_default_clarification_provider_is_connected(monkeypatch):
    import routes.workspace_snapshot_routes as routes

    monkeypatch.setattr(
        routes,
        "build_workspace_clarification_status",
        lambda owner=None: {
            "schema": "odysseus.clarification_workspace_status.v1",
            "state": "live",
            "status": "pending",
            "pending_count": 2,
            "summary": "2 required answer(s) pending",
            "raw_content_visible": False,
        },
    )

    app = FastAPI()
    app.state.auth_manager = type("Auth", (), {"is_configured": True, "is_admin": lambda self, user: True})()

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        request.state.current_user = "admin"
        return await call_next(request)

    app.include_router(setup_workspace_snapshot_routes())
    response = TestClient(app).get("/api/workspace/snapshot")

    assert response.status_code == 200
    sections = {section["id"]: section for section in response.json()["snapshot"]["sections"]}
    assert sections["clarification"]["state"] == "live"
    assert sections["clarification"]["pending_count"] == 2
    assert sections["clarification"]["frontend_hint"] == "render_attention"

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import ScheduledTask
import routes.task_summary_routes as task_summary_routes
from routes.task_summary_routes import setup_task_summary_routes
from src.task_summary import describe_recurrence


def _isolated_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tasks.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(cdb, "SessionLocal", session_factory)
    monkeypatch.setattr(task_summary_routes, "SessionLocal", session_factory)
    return session_factory


def _app(*, user="alice", auth_configured=True) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = type("_AuthManager", (), {"is_configured": auth_configured})()

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_task_summary_routes())
    return app


def _seed_task(session_factory, **overrides):
    db = session_factory()
    try:
        values = {
            "id": "weekday-task",
            "owner": "alice",
            "name": "Weekday reminder",
            "prompt": "private prompt must not leak",
            "task_type": "llm",
            "schedule": "cron",
            "cron_expression": "0 9 * * 1-5",
            "trigger_type": "schedule",
            "status": "active",
            "output_target": "telegram",
            "next_run": datetime(2026, 7, 6, 9, 0, 0),
            "run_count": 2,
            "webhook_token": "secret-token",
        }
        values.update(overrides)
        db.add(ScheduledTask(**values))
        db.commit()
    finally:
        db.close()


def test_describe_recurrence_collapses_weekday_cron():
    recurrence = describe_recurrence(
        trigger_type="schedule",
        schedule="cron",
        cron_expression="0 9 * * 1-5",
    )

    assert recurrence["kind"] == "cron"
    assert recurrence["label"] == "Mo-Fr 09:00"
    assert recurrence["weekdays"] == ("Mo", "Di", "Mi", "Do", "Fr")


def test_task_summary_route_is_owner_scoped_and_redacted(tmp_path, monkeypatch):
    session_factory = _isolated_db(tmp_path, monkeypatch)
    _seed_task(session_factory)
    _seed_task(
        session_factory,
        id="bob-task",
        owner="bob",
        name="Bob private",
        prompt="bob private",
        webhook_token="bob-token",
    )

    response = TestClient(_app()).get("/api/tasks/summary")
    payload = response.json()
    encoded = response.text

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.task_summary.v1"
    assert payload["count"] == 1
    assert payload["active_count"] == 1
    assert payload["items"][0]["id"] == "weekday-task"
    assert payload["items"][0]["recurrence"]["label"] == "Mo-Fr 09:00"
    assert payload["items"][0]["output_target"] == "telegram"
    assert payload["items"][0]["prompt_visible"] is False
    assert payload["items"][0]["webhook_token_visible"] is False
    assert "private prompt" not in encoded
    assert "secret-token" not in encoded
    assert "Bob private" not in encoded


def test_task_summary_route_requires_auth_when_configured(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "true")

    response = TestClient(_app(user=None, auth_configured=True)).get("/api/tasks/summary")

    assert response.status_code == 401

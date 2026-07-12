import json
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import ScheduledTask
from plugins.telegram.stores import TelegramInboxStore
import routes.operator_dashboard_routes as operator_dashboard_routes
from routes.operator_dashboard_routes import setup_operator_dashboard_routes
from src.operator_dashboard import build_operator_dashboard_snapshot, build_operator_review_queue
from src.operator_dashboard_snapshot import build_operator_dashboard_snapshot as legacy_snapshot_builder
from src.operator_review_queue import build_operator_review_queue as legacy_queue_builder


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


def _app(*, user="admin", admins=("admin",)) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins=admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(
        setup_operator_dashboard_routes(
            review_gate_provider=lambda: {
                "schema": "odysseus.review_gate_state.v1",
                "status": "pending",
                "pending_count": 1,
                "blocked_count": 0,
                "gate_count": 1,
                "gates": [
                    {
                        "id": "telegram_delivery",
                        "family": "telegram",
                        "state": "pending_review",
                        "reason": "operator_live_go_missing",
                        "source_ref": "telegram:chat-123",
                        "raw_content": "PRIVATE MESSAGE",
                    }
                ],
            },
            live_affordance_provider=lambda: {
                "schema": "odysseus.live_affordance_readiness.v1",
                "status": "blocked",
                "blocked_count": 1,
                "affordances": [
                    {
                        "id": "nextcloud-copy",
                        "family": "nextcloud_copy",
                        "status": "blocked",
                        "required_gate": "UIX-NEXTCLOUD-LIVE-WRITE",
                        "target_url": "https://cloud.example.test/private?token=SECRET",
                    }
                ],
            },
            tasks_summary_provider=lambda: {
                "schema": "odysseus.tasks.summary.v1",
                "open_count": 2,
                "items": [{"title": "Private task body"}],
            },
            diagnostics_summary_provider=lambda: {
                "schema": "odysseus.operator_quick_status.v1",
                "status": "warn",
                "endpoint_count": 8,
                "command": "run-secret --token SECRET",
            },
            version_readiness_provider=lambda: {
                "schema": "odysseus.version_one.readiness.v1",
                "status": "partial",
                "overall_percent": 80,
                "remaining_count": 2,
            },
            orchestration_status_provider=lambda: {
                "schema": "odysseus.orchestration.dashboard.v1",
                "plan_status": "waiting",
                "next_actions": [{"title": "Handle private worktree"}],
            },
            coding_approvals_provider=lambda: [
                {"id": "publish-pr", "status": "pending_review", "source_ref": "branch:secret-feature"}
            ],
        )
    )
    return app


def _default_app(data_dir, *, user="admin", admins=("admin",)) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins=admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_operator_dashboard_routes(telegram_data_dir=data_dir))
    return app


def _isolated_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'operator-dashboard.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(cdb, "SessionLocal", session_factory)
    monkeypatch.setattr(operator_dashboard_routes.cdb, "SessionLocal", session_factory)
    return session_factory


def _seed_task(session_factory):
    db = session_factory()
    try:
        db.add(
            ScheduledTask(
                id="private-task",
                owner="alice",
                name="Private reminder",
                prompt="private prompt must not leak",
                task_type="llm",
                schedule="daily",
                scheduled_time="09:00",
                trigger_type="schedule",
                status="active",
                output_target="telegram",
                next_run=datetime(2026, 7, 6, 9, 0, 0),
                webhook_token="secret-token",
            )
        )
        db.commit()
    finally:
        db.close()


def test_operator_dashboard_snapshot_route_is_admin_gated_and_redacted():
    response = TestClient(_app()).get("/api/operator-dashboard/snapshot")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.operator_dashboard.route.v1"
    assert payload["snapshot"]["schema"] == "odysseus.operator_dashboard.snapshot.v1"
    assert payload["review_queue"]["schema"] == "odysseus.operator_review_queue.v1"
    assert payload["snapshot"]["status"] == "blocked"
    assert payload["review_queue"]["item_count"] == 3
    assert payload["live_probe_performed"] is False
    assert payload["live_mutation_performed"] is False
    assert payload["write_action_enabled"] is False
    assert "PRIVATE MESSAGE" not in encoded
    assert "chat-123" not in encoded
    assert "cloud.example" not in encoded
    assert "SECRET" not in encoded
    assert "Private task body" not in encoded
    assert "run-secret" not in encoded
    assert "secret-feature" not in encoded


def test_operator_dashboard_snapshot_route_requires_admin():
    response = TestClient(_app(user="alice", admins=("admin",))).get("/api/operator-dashboard/snapshot")

    assert response.status_code == 403


def test_operator_dashboard_route_default_sources_are_local_read_only_and_redacted(tmp_path, monkeypatch):
    session_factory = _isolated_db(tmp_path, monkeypatch)
    _seed_task(session_factory)
    TelegramInboxStore(tmp_path).append_event(
        kind="universal_inbox_attachment",
        status="processed",
        chat_id="raw-chat-123",
        message_id=42,
        universal_inbox_status="needs_review",
        attachment_family="document",
        attachment_suffix=".pdf",
        review_reason_count=2,
        maintenance_review_required=True,
        memory_write_intent_status="review",
        memory_records_planned=1,
        memory_records_written=0,
        raptorgraph_events_planned=1,
        raptorgraph_events_written=0,
        writes_performed=False,
    )

    response = TestClient(_default_app(tmp_path)).get("/api/operator-dashboard/snapshot")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["snapshot"]["counts"]["pending_count"] >= 1
    assert payload["review_queue"]["item_count"] >= 3
    assert payload["snapshot"]["live_probe_performed"] is False
    assert payload["snapshot"]["live_mutation_performed"] is False
    assert payload["review_queue"]["live_action_enabled"] is False
    assert payload["review_queue"]["write_action_enabled"] is False
    assert "raw-chat-123" not in encoded
    assert "message_id" not in encoded
    assert "Private reminder" not in encoded
    assert "private prompt" not in encoded
    assert "secret-token" not in encoded


def test_operator_dashboard_legacy_snapshot_import_alias_remains_available():
    assert legacy_snapshot_builder is build_operator_dashboard_snapshot


def test_operator_dashboard_legacy_review_queue_import_alias_remains_available():
    assert legacy_queue_builder is build_operator_review_queue

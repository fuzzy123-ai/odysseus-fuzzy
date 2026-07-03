import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import McpServer
import routes.operator_quick_status_routes as quick_routes
from routes.operator_quick_status_routes import setup_operator_quick_status_routes


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


class _FakeMcpManager:
    def get_all_statuses(self):
        return {
            "server-one": {"status": "connected", "tool_count": 2, "error": "secret error text"},
            "server-two": {"status": "needs_auth", "auth_url": "https://example.invalid/?token=secret"},
        }

    def get_all_tools(self):
        return [
            {
                "server_id": "server-one",
                "server_name": "Sensitive Server",
                "name": "read_secret",
                "description": "reads secret host paths",
                "is_disabled": False,
            },
            {
                "server_id": "server-two",
                "server_name": "Auth Server",
                "name": "write_secret",
                "description": "writes secret host paths",
                "is_disabled": True,
            },
        ]


def _isolated_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'quick-status.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(cdb, "SessionLocal", session_factory)
    monkeypatch.setattr(quick_routes, "SessionLocal", session_factory)
    return session_factory


def _app(*, user="admin", admins=("admin",)) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = _AuthManager(admins=admins)

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        if user is not None:
            request.state.current_user = user
        return await call_next(request)

    app.include_router(setup_operator_quick_status_routes(_FakeMcpManager()))
    return app


def _seed_mcp_servers(session_factory):
    db = session_factory()
    try:
        db.add(
            McpServer(
                id="server-one",
                name="Sensitive Server",
                transport="stdio",
                command="C:/private/bin/run-secret.exe",
                args=json.dumps(["--token", "secret"]),
                env=json.dumps({"API_TOKEN": "secret"}),
                is_enabled=True,
            )
        )
        db.add(
            McpServer(
                id="server-two",
                name="Auth Server",
                transport="sse",
                url="https://example.invalid/mcp?token=secret",
                is_enabled=True,
                disabled_tools=json.dumps(["write_secret"]),
            )
        )
        db.commit()
    finally:
        db.close()


def test_quick_status_summarizes_mcp_and_system_health_without_secrets(tmp_path, monkeypatch):
    session_factory = _isolated_db(tmp_path, monkeypatch)
    _seed_mcp_servers(session_factory)

    response = TestClient(_app()).get("/api/diagnostics/quick-status")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.operator_quick_status.v1"
    assert payload["status"] == "warn"
    assert payload["app"]["status"] == "ok"
    assert payload["app"]["remote_lookup_performed"] is False
    assert payload["mcp"]["configured_server_count"] == 2
    assert payload["mcp"]["enabled_server_count"] == 2
    assert payload["mcp"]["connected_server_count"] == 1
    assert payload["mcp"]["needs_auth_count"] == 1
    assert payload["mcp"]["tool_count"] == 2
    assert payload["mcp"]["tool_names_visible"] is False
    assert payload["system_health"]["status"] == "unknown"
    assert payload["diagnostics"]["endpoint_count"] >= 8
    assert payload["live_probe_performed"] is False
    assert payload["live_mutation_performed"] is False
    assert "secret" not in encoded
    assert "Sensitive Server" not in encoded
    assert "C:/private" not in encoded
    assert "example.invalid" not in encoded


def test_quick_status_requires_admin(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)

    response = TestClient(_app(user="alice", admins=("admin",))).get("/api/diagnostics/quick-status")

    assert response.status_code == 403

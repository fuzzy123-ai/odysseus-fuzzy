import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.coding_agent_quick_entry_routes import setup_coding_agent_quick_entry_routes
from src.coding_agent_quick_entry import build_coding_agent_quick_entry


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

    app.include_router(setup_coding_agent_quick_entry_routes())
    return app


def test_coding_agent_quick_entry_summarizes_actions_without_execution():
    payload = build_coding_agent_quick_entry()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == "odysseus.coding_agent_quick_entry.v1"
    assert payload["status"] == "available"
    assert payload["summary"]["read_only_preview_count"] >= 4
    assert payload["summary"]["gated_mutation_count"] >= 4
    assert payload["summary"]["live_action_count"] >= 4
    assert payload["classes"]["live_action"]["requires_live_go"] is True
    assert payload["safety"]["execution_performed"] is False
    assert payload["safety"]["sandbox_started"] is False
    assert payload["safety"]["repo_mutation_performed"] is False
    assert payload["safety"]["host_write_performed"] is False
    assert payload["safety"]["sensitive_values_visible"] is False
    assert payload["safety"]["host_paths_visible"] is False
    assert "C:" not in encoded
    assert "secret" not in encoded


def test_coding_agent_quick_entry_route_requires_admin():
    response = TestClient(_app(user="alice", admins=("admin",))).get("/api/coding-agent/quick-entry")

    assert response.status_code == 403


def test_coding_agent_quick_entry_route_returns_static_contract():
    response = TestClient(_app()).get("/api/coding-agent/quick-entry")
    payload = response.json()

    assert response.status_code == 200
    assert payload["summary"]["action_count"] == len(payload["actions"])
    assert any(action["path"] == "/api/sandbox-worker/submit" for action in payload["actions"])
    assert all(action["raw_values_visible"] is False for action in payload["actions"])

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.non_ui_gate_readiness_routes import setup_non_ui_gate_readiness_routes


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

    app.include_router(setup_non_ui_gate_readiness_routes())
    return app


def test_non_ui_gate_readiness_route_requires_admin():
    response = TestClient(_app(user="alice", admins=("admin",))).get("/api/non-ui-gates/readiness")

    assert response.status_code == 403


def test_non_ui_gate_readiness_route_returns_redacted_contract(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOKEN_VALUE")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "12345")

    response = TestClient(_app()).get("/api/non-ui-gates/readiness")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.non_ui_gate_readiness.v1"
    assert payload["status"] == "blocked"
    assert payload["queue_exhausted"] is True
    assert payload["non_ui_decision_packet_count"] >= 1
    assert payload["excluded_design_decision_packet_count"] >= 1
    assert payload["recommended_next_operator_action"]["values_visible"] is False
    assert payload["tokens_visible"] is False
    assert payload["chat_ids_visible"] is False
    assert payload["host_paths_visible"] is False
    assert payload["raw_content_visible"] is False
    assert "TOKEN_VALUE" not in encoded
    assert "12345" not in encoded


def test_non_ui_gate_readiness_markdown_route_is_redacted(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOKEN_VALUE")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "12345")

    response = TestClient(_app()).get("/api/non-ui-gates/readiness.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# Non-UI Gate Readiness" in response.text
    assert "Unsafe evidence flags: none" in response.text
    assert "calendar_reminders" in response.text
    assert "TOKEN_VALUE" not in response.text
    assert "12345" not in response.text

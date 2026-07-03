import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.live_affordance_routes import setup_live_affordance_routes
from src.live_affordance_readiness import build_live_affordance_readiness


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

    app.include_router(setup_live_affordance_routes())
    return app


def test_live_affordance_readiness_is_conservative_without_live_inputs():
    payload = build_live_affordance_readiness(env={}, tool_lookup=lambda _tool: None)
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == "odysseus.live_affordance_readiness.v1"
    assert payload["status"] == "blocked"
    assert all(action["ready"] is False for action in payload["actions"])
    assert payload["live_execution_performed"] is False
    assert payload["network_probe_performed"] is False
    assert payload["telegram_send_performed"] is False
    assert payload["nextcloud_write_performed"] is False
    assert payload["converter_process_started"] is False
    assert "TOKEN_VALUE" not in encoded
    assert "C:" not in encoded


def test_live_affordance_readiness_reports_gates_without_values():
    env = {
        "TELEGRAM_AGENT_REPLY_ENABLED": "true",
        "TELEGRAM_BOT_TOKEN": "TOKEN_VALUE",
        "TELEGRAM_ALLOWED_CHAT_IDS": "12345",
        "UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED": "true",
        "UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO": "true",
        "NEXTCLOUD_WEBDAV_BASE_URL": "https://cloud.example.invalid/remote.php/dav/files/ai",
        "NEXTCLOUD_WEBDAV_USERNAME": "ai",
        "NEXTCLOUD_WEBDAV_APP_PASSWORD": "TOKEN_VALUE",
        "UNIVERSAL_FILE_IO_LIVE_CONVERTER_ENABLED": "true",
        "UNIVERSAL_FILE_IO_OPERATOR_LIVE_GO": "true",
    }
    payload = build_live_affordance_readiness(env=env, tool_lookup=lambda tool: f"C:/tools/{tool}.exe")
    encoded = json.dumps(payload, sort_keys=True)
    by_id = {action["action_id"]: action for action in payload["actions"]}

    assert payload["status"] == "blocked"
    assert by_id["telegram_delivery"]["ready"] is False
    assert by_id["nextcloud_copy"]["ready"] is False
    assert by_id["converter_execution"]["ready"] is False
    assert "operator_live_go_required" in by_id["telegram_delivery"]["readiness_gap_names"]
    assert "bounded_copy_request_required" in by_id["nextcloud_copy"]["readiness_gap_names"]
    assert "bounded_conversion_request_required" in by_id["converter_execution"]["readiness_gap_names"]
    assert "TOKEN_VALUE" not in encoded
    assert "12345" not in encoded
    assert "cloud.example" not in encoded
    assert "C:/tools" not in encoded


def test_live_affordance_readiness_route_requires_admin():
    response = TestClient(_app(user="alice", admins=("admin",))).get("/api/live-affordances/readiness")

    assert response.status_code == 403


def test_live_affordance_readiness_route_returns_contract(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    response = TestClient(_app()).get("/api/live-affordances/readiness")
    payload = response.json()

    assert response.status_code == 200
    assert payload["schema"] == "odysseus.live_affordance_readiness.v1"
    assert len(payload["actions"]) == 3
    assert payload["tokens_visible"] is False

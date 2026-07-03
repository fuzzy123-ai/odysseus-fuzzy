import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.legacy_chat_contract_routes import setup_legacy_chat_contract_routes
from src.legacy_chat_contracts import build_legacy_chat_contracts


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

    app.include_router(setup_legacy_chat_contract_routes())
    return app


def test_legacy_chat_contracts_cover_lc1_to_lc10_without_live_execution():
    payload = build_legacy_chat_contracts()
    encoded = json.dumps(payload, sort_keys=True)
    ids = {item["slice_id"] for item in payload["contracts"]}

    assert payload["schema"] == "odysseus.legacy_chat.contracts.v1"
    assert payload["status"] == "backend_ready"
    assert ids == {f"lc{idx}" for idx in range(1, 11)}
    assert payload["ui_execution_required"] is True
    assert payload["ui_code_included"] is False
    assert payload["live_execution_performed"] is False
    assert payload["token_values_visible"] is False
    assert payload["chat_id_values_visible"] is False
    assert "C:" not in encoded
    assert "TOKEN_VALUE" not in encoded


def test_legacy_chat_contracts_route_requires_admin():
    response = TestClient(_app(user="alice", admins=("admin",))).get("/api/legacy-chat/contracts")

    assert response.status_code == 403


def test_legacy_chat_contracts_route_returns_ui_manifest():
    response = TestClient(_app()).get("/api/legacy-chat/contracts")
    payload = response.json()

    assert response.status_code == 200
    assert payload["contract_count"] == 10
    assert any(
        endpoint["path"] == "/api/live-affordances/readiness"
        for contract in payload["contracts"]
        for endpoint in contract["endpoints"]
    )

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.auth_routes as auth_routes
import routes.prefs_routes as prefs_routes
import src.secret_handoff as secret_handoff
import src.settings as settings_store


class _FakeAuthManager:
    def get_username_for_token(self, token):
        return {
            "admin-token": "admin",
            "user-token": "bob",
        }.get(token)

    def is_admin(self, user):
        return user == "admin"


class _FakeRequest:
    def __init__(self, token="", body=None, query_params=None):
        self.cookies = {auth_routes.SESSION_COOKIE: token} if token else {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self._body = body or {}
        self.query_params = query_params or {}

    async def json(self):
        return dict(self._body)


@pytest.fixture
def isolated_route_settings(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    features_file = tmp_path / "features.json"
    prefs_file = tmp_path / "user_prefs.json"
    handoffs_file = tmp_path / "secret_handoffs.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(settings_store, "FEATURES_FILE", str(features_file))
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(secret_handoff, "SECRET_HANDOFFS_FILE", str(handoffs_file))
    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)
    settings_store._invalidate_caches()
    yield {
        "settings": settings_file,
        "features": features_file,
        "prefs": prefs_file,
        "handoffs": handoffs_file,
    }
    settings_store._invalidate_caches()


def _endpoint(path, method):
    router = auth_routes.setup_auth_routes(_FakeAuthManager())
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def test_post_auth_settings_uses_service_validation_and_keeps_response_shape(isolated_route_settings):
    target = _endpoint("/api/auth/settings", "POST")

    result = asyncio.run(target(_FakeRequest(
        "admin-token",
        {
            "agent_max_tool_calls": 5000,
            "image_quality": "HIGH",
            "search_fallback_chain": ["duckduckgo", "brave"],
        },
    )))

    assert result["agent_max_tool_calls"] == 1000
    assert result["image_quality"] == "high"
    assert result["search_fallback_chain"] == ["duckduckgo", "brave"]
    assert "dsgvo_mode" in result
    assert settings_store.load_settings()["agent_max_tool_calls"] == 1000


def test_post_auth_settings_returns_400_for_service_validation_error(isolated_route_settings):
    target = _endpoint("/api/auth/settings", "POST")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(_FakeRequest("admin-token", {"image_quality": "ultra"})))

    assert exc.value.status_code == 400
    assert "image_quality" in exc.value.detail


def test_get_auth_settings_scrubs_non_admin_response_via_service(isolated_route_settings):
    settings_store.save_settings({
        **settings_store.DEFAULT_SETTINGS,
        "brave_api_key": "brave-secret",
        "google_pse_cx": "public-cx",
        "reminder_webhook_integration_id": "capability-handle",
    })
    target = _endpoint("/api/auth/settings", "GET")

    result = asyncio.run(target(_FakeRequest("user-token")))

    assert result["brave_api_key"] == ""
    assert result["google_pse_cx"] == "public-cx"
    assert result["reminder_webhook_integration_id"] == ""


def test_post_auth_features_uses_service_and_preserves_bool_only_update(isolated_route_settings):
    target = _endpoint("/api/auth/features", "POST")

    result = asyncio.run(target(_FakeRequest("admin-token", {
        "deep_research": True,
        "rag": "false",
        "unknown_flag": True,
    })))

    assert result["deep_research"] is True
    assert result["rag"] is True
    assert settings_store.load_features()["deep_research"] is True
    assert "unknown_flag" not in settings_store.load_features()


def test_auth_settings_and_features_mutation_require_admin(isolated_route_settings):
    settings_target = _endpoint("/api/auth/settings", "POST")
    features_target = _endpoint("/api/auth/features", "POST")

    with pytest.raises(HTTPException) as settings_exc:
        asyncio.run(settings_target(_FakeRequest("user-token", {"image_quality": "high"})))
    with pytest.raises(HTTPException) as features_exc:
        asyncio.run(features_target(_FakeRequest("user-token", {"deep_research": True})))

    assert settings_exc.value.status_code == 403
    assert features_exc.value.status_code == 403


def test_auth_secret_handoff_routes_require_admin_and_do_not_echo_secret(isolated_route_settings):
    pending = secret_handoff.create_secret_handoff("brave_api_key", ttl_seconds=60)
    list_target = _endpoint("/api/auth/settings/secret-handoffs", "GET")
    complete_target = _endpoint("/api/auth/settings/secret-handoffs/{request_id}/complete", "POST")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(list_target(_FakeRequest("user-token")))
    assert exc.value.status_code == 403

    listed = asyncio.run(list_target(_FakeRequest("admin-token")))
    completed = asyncio.run(complete_target(
        pending["id"],
        _FakeRequest("admin-token", {"value": "secret-value"}),
    ))

    assert listed["count"] == 1
    assert listed["requests"][0]["id"] == pending["id"]
    assert completed["stored"] is True
    assert completed["setting"]["value_visible"] is False
    assert "secret-value" not in json.dumps(completed)
    assert settings_store.load_settings()["brave_api_key"] == "secret-value"
    assert "secret-value" not in isolated_route_settings["handoffs"].read_text(encoding="utf-8")


def test_auth_secret_handoff_cancel_marks_pending(isolated_route_settings):
    pending = secret_handoff.create_secret_handoff("brave_api_key", ttl_seconds=60)
    cancel_target = _endpoint("/api/auth/settings/secret-handoffs/{request_id}/cancel", "POST")

    result = asyncio.run(cancel_target(pending["id"], _FakeRequest("admin-token")))

    assert result["status"] == "cancelled"
    assert secret_handoff.list_secret_handoffs(status="cancelled")["count"] == 1

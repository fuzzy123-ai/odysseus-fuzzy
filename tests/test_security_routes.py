import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.security_routes import setup_security_routes
import src.settings as settings_store


def _app(*, is_admin: bool = True) -> FastAPI:
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: bool(is_admin),
    )

    @app.middleware("http")
    async def _stamp_user(request, call_next):
        request.state.current_user = "admin" if is_admin else "user"
        return await call_next(request)

    app.include_router(setup_security_routes())
    return app


def test_dsgvo_status_is_browser_safe_and_redacted(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")
    settings_store._invalidate_caches()
    settings_store.save_settings({**settings_store.DEFAULT_SETTINGS, "dsgvo_mode": True, "brave_api_key": "sk-secret"})

    response = TestClient(_app()).get("/api/security/dsgvo/status")
    payload = response.json()
    encoded = json.dumps(payload, sort_keys=True)

    assert response.status_code == 200
    assert payload["dsgvo_mode"] is True
    assert payload["local_only_required"] is True
    assert payload["effective_security_mode"] == "secure"
    assert payload["required_provider_scope"] == "local_only"
    assert payload["external_io_allowed"] is False
    assert payload["settings_values_visible"] is False
    assert "sk-secret" not in encoded


def test_dsgvo_toggle_requires_admin_and_updates_global_setting(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")
    settings_store._invalidate_caches()
    settings_store.save_settings({**settings_store.DEFAULT_SETTINGS, "dsgvo_mode": False})

    denied = TestClient(_app(is_admin=False)).post("/api/security/dsgvo/toggle")
    assert denied.status_code == 403

    response = TestClient(_app(is_admin=True)).post("/api/security/dsgvo/toggle")
    payload = response.json()

    assert response.status_code == 200
    assert payload["requested"] is True
    assert payload["before"] is False
    assert payload["changed"] is True
    assert payload["dsgvo_mode"] is True
    assert settings_store.load_settings()["dsgvo_mode"] is True


def test_dsgvo_disable_reports_forced_active_when_env_requires_it(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "true")
    settings_store._invalidate_caches()
    settings_store.save_settings({**settings_store.DEFAULT_SETTINGS, "dsgvo_mode": True})

    response = TestClient(_app(is_admin=True)).post("/api/security/dsgvo", json={"enabled": False})
    payload = response.json()

    assert response.status_code == 200
    assert payload["requested"] is False
    assert payload["dsgvo_mode"] is True
    assert payload["setting_enabled"] is False
    assert payload["forced_active"] is True
    assert payload["changed"] is False

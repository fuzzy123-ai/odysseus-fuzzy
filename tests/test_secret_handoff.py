import json

import pytest

import routes.prefs_routes as prefs_routes
import src.secret_handoff as secret_handoff
import src.settings as settings_store
from src.settings_service import SettingsServiceError


@pytest.fixture
def isolated_secret_handoff(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    features_file = tmp_path / "features.json"
    prefs_file = tmp_path / "user_prefs.json"
    handoffs_file = tmp_path / "secret_handoffs.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(settings_store, "FEATURES_FILE", str(features_file))
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(secret_handoff, "SECRET_HANDOFFS_FILE", str(handoffs_file))
    settings_store._invalidate_caches()
    yield {
        "settings": settings_file,
        "features": features_file,
        "prefs": prefs_file,
        "handoffs": handoffs_file,
    }
    settings_store._invalidate_caches()


def test_create_secret_handoff_persists_metadata_without_value(isolated_secret_handoff):
    result = secret_handoff.create_secret_handoff("brave_api_key", ttl_seconds=60)

    assert result["ok"] is True
    assert result["status"] == "pending"
    assert result["key"] == "brave_api_key"
    assert result["value_visible"] is False

    stored = json.loads(isolated_secret_handoff["handoffs"].read_text(encoding="utf-8"))
    assert stored["requests"][0]["id"] == result["id"]
    assert "value" not in stored["requests"][0]


def test_complete_secret_handoff_stores_secret_but_never_echoes_it(isolated_secret_handoff):
    pending = secret_handoff.create_secret_handoff("brave_api_key", ttl_seconds=60)

    result = secret_handoff.complete_secret_handoff(pending["id"], "secret-value", actor="admin")

    assert result["ok"] is True
    assert result["stored"] is True
    assert result["setting"]["key"] == "brave_api_key"
    assert result["setting"]["value_visible"] is False
    assert "secret-value" not in json.dumps(result)
    assert settings_store.load_settings()["brave_api_key"] == "secret-value"
    assert "secret-value" not in isolated_secret_handoff["handoffs"].read_text(encoding="utf-8")


def test_non_secret_setting_cannot_request_secret_handoff(isolated_secret_handoff):
    with pytest.raises(SettingsServiceError) as exc:
        secret_handoff.create_secret_handoff("agent_input_token_budget")

    assert exc.value.code == "not_secret_handoff"


def test_cancel_secret_handoff_marks_pending_without_secret_value(isolated_secret_handoff):
    pending = secret_handoff.create_secret_handoff("brave_api_key", ttl_seconds=60)

    result = secret_handoff.cancel_secret_handoff(pending["id"], actor="admin")

    assert result["ok"] is True
    assert result["status"] == "cancelled"
    listed = secret_handoff.list_secret_handoffs(status="cancelled")
    assert listed["count"] == 1
    assert listed["requests"][0]["id"] == pending["id"]

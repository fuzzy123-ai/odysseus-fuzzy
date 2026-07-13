import json

import pytest

import routes.prefs_routes as prefs_routes
import src.settings as settings_store
from src.settings_service import (
    SettingsServiceError,
    explain_setting,
    get_setting,
    patch_setting,
    reset_setting,
    set_setting,
)


@pytest.fixture
def isolated_settings_files(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    features_file = tmp_path / "features.json"
    prefs_file = tmp_path / "user_prefs.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(settings_store, "FEATURES_FILE", str(features_file))
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    settings_store._invalidate_caches()
    yield {
        "settings": settings_file,
        "features": features_file,
        "prefs": prefs_file,
    }
    settings_store._invalidate_caches()


def test_set_global_scalar_coerces_enum_and_clamps_int(isolated_settings_files):
    quality = set_setting("image_quality", "HIGH", scope="global")
    tool_calls = set_setting("agent_max_tool_calls", 5000, scope="global")

    assert quality["ok"] is True
    assert quality["value"] == "high"
    assert tool_calls["value"] == 1000

    persisted = json.loads(isolated_settings_files["settings"].read_text(encoding="utf-8"))
    assert persisted["image_quality"] == "high"
    assert persisted["agent_max_tool_calls"] == 1000


def test_invalid_enum_raises_structured_error(isolated_settings_files):
    with pytest.raises(SettingsServiceError) as exc:
        set_setting("image_quality", "ultra", scope="global")

    assert exc.value.code == "invalid_value"
    assert "image_quality" in str(exc.value)


def test_user_scoped_setting_writes_pref_and_auto_read_prefers_user(isolated_settings_files):
    result = set_setting("default_model", "qwen-local", owner="alice", scope="user")

    assert result["ok"] is True
    assert result["effective_scope"] == "user"
    assert get_setting("default_model", owner="alice")["value"] == "qwen-local"
    assert get_setting("default_model", owner="alice")["effective_scope"] == "user"
    assert get_setting("default_model", scope="global")["value"] == ""

    prefs = json.loads(isolated_settings_files["prefs"].read_text(encoding="utf-8"))
    assert prefs["_users"]["alice"]["default_model"] == "qwen-local"
    assert not isolated_settings_files["settings"].exists()


def test_explicit_global_write_for_both_scope_does_not_touch_user_prefs(isolated_settings_files):
    result = set_setting("default_model", "global-model", owner="alice", scope="global")

    assert result["ok"] is True
    assert result["effective_scope"] == "global"
    assert get_setting("default_model", owner="alice", scope="global")["value"] == "global-model"
    assert not isolated_settings_files["prefs"].exists()


def test_feature_write_requires_confirmation_for_agent(isolated_settings_files):
    blocked = set_setting("deep_research", True, store="feature")

    assert blocked["ok"] is False
    assert blocked["status"] == "confirmation_required"
    assert not isolated_settings_files["features"].exists()

    updated = set_setting("deep_research", "true", store="feature", confirmed=True)
    assert updated["ok"] is True
    assert updated["value"] is True
    assert settings_store.load_features()["deep_research"] is True


def test_secret_setting_requires_handoff_for_agent_but_ui_can_write_redacted(isolated_settings_files):
    blocked = set_setting("brave_api_key", "secret-value", scope="global")

    assert blocked["ok"] is False
    assert blocked["status"] == "secret_handoff_required"
    assert not isolated_settings_files["settings"].exists()

    updated = set_setting("brave_api_key", "secret-value", scope="global", actor="ui")
    assert updated["ok"] is True
    assert updated["value"] is None
    assert updated["value_visible"] is False
    assert settings_store.load_settings()["brave_api_key"] == "secret-value"


def test_patch_structured_list_and_object_values(isolated_settings_files):
    chain = patch_setting("search_fallback_chain", {"op": "append", "value": "brave"}, scope="global")
    keybinds = patch_setting("keybinds", {"op": "set", "path": "search", "value": "ctrl+j"}, scope="global")

    assert chain["value"] == ["duckduckgo", "brave"]
    assert keybinds["value"]["search"] == "ctrl+j"
    persisted = settings_store.load_settings()
    assert persisted["search_fallback_chain"] == ["duckduckgo", "brave"]
    assert persisted["keybinds"]["search"] == "ctrl+j"


def test_context_budget_overrides_validate_nested_positive_integer_maps(isolated_settings_files):
    value = {
        "providers": {"deepseek": 64_000},
        "models": {"deepseek-v4-pro": 128_000},
    }

    result = set_setting(
        "agent_input_token_budget_overrides", value, scope="global", actor="ui"
    )

    assert result["value"] == value
    assert settings_store.load_settings()["agent_input_token_budget_overrides"] == value

    invalid_values = [
        {"providers": {"deepseek": "64000"}, "models": {}},
        {"providers": {"deepseek": True}, "models": {}},
        {"providers": {"": 64_000}, "models": {}},
        {"providers": {}, "models": {"deepseek-v4-pro": 0}},
        {"providers": {}},
        {"providers": {}, "models": {}, "unexpected": {}},
    ]
    for invalid in invalid_values:
        with pytest.raises(SettingsServiceError) as exc:
            set_setting(
                "agent_input_token_budget_overrides",
                invalid,
                scope="global",
                actor="ui",
            )
        assert exc.value.code == "invalid_value"


def test_confirm_setting_blocks_until_confirmed(isolated_settings_files):
    blocked = patch_setting(
        "tool_path_extra_roots",
        {"op": "append", "value": "C:\\tmp"},
        scope="global",
    )

    assert blocked["ok"] is False
    assert blocked["status"] == "confirmation_required"
    assert not isolated_settings_files["settings"].exists()

    updated = patch_setting(
        "tool_path_extra_roots",
        {"op": "append", "value": "C:\\tmp"},
        scope="global",
        confirmed=True,
    )
    assert updated["ok"] is True
    assert updated["value"] == ["C:\\tmp"]


def test_reset_user_setting_removes_override_and_falls_back_to_global(isolated_settings_files):
    set_setting("default_model", "global-model", scope="global")
    set_setting("default_model", "user-model", owner="alice", scope="user")

    reset = reset_setting("default_model", owner="alice", scope="user")

    assert reset["ok"] is True
    assert reset["previous"] == "user-model"
    assert reset["value"] == "global-model"
    assert reset["effective_scope"] == "global"
    assert "default_model" not in prefs_routes._load_for_user("alice")


def test_explain_setting_exposes_policy_without_secret_value(isolated_settings_files):
    explanation = explain_setting("brave_api_key")

    assert explanation["ok"] is True
    assert explanation["secret_handoff_required"] is True
    assert explanation["value"] is None
    assert explanation["value_visible"] is False

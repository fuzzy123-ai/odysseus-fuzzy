import asyncio
import json

import pytest

import routes.prefs_routes as prefs_routes
import src.secret_handoff as secret_handoff
import src.settings as settings_store
from src.tool_implementations import do_manage_settings


@pytest.fixture
def isolated_settings_files(tmp_path, monkeypatch):
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


def _run_manage(payload, owner=None):
    return asyncio.run(do_manage_settings(json.dumps(payload), owner=owner))


def test_agent_set_uses_aliases_and_service_payload(isolated_settings_files):
    result = _run_manage({
        "action": "set",
        "key": "token budget",
        "value": 8000,
    })

    assert result["exit_code"] == 0
    assert result["value"] == 8000
    assert result["setting"]["key"] == "agent_input_token_budget"
    assert settings_store.load_settings()["agent_input_token_budget"] == 8000


def test_auto_scope_writes_user_preference_when_owner_present(isolated_settings_files):
    result = _run_manage({
        "action": "set",
        "key": "default model",
        "value": "qwen-local",
    }, owner="alice")

    assert result["exit_code"] == 0
    assert result["setting"]["effective_scope"] == "user"

    prefs = json.loads(isolated_settings_files["prefs"].read_text(encoding="utf-8"))
    assert prefs["_users"]["alice"]["default_model"] == "qwen-local"
    assert not isolated_settings_files["settings"].exists()


def test_explicit_global_scope_bypasses_user_preference(isolated_settings_files):
    result = _run_manage({
        "action": "set",
        "key": "default_model",
        "value": "global-model",
        "scope": "global",
    }, owner="alice")

    assert result["exit_code"] == 0
    assert result["setting"]["effective_scope"] == "global"
    assert settings_store.load_settings()["default_model"] == "global-model"
    assert not isolated_settings_files["prefs"].exists()


def test_secret_setting_returns_handoff_without_persisting(isolated_settings_files):
    result = _run_manage({
        "action": "set",
        "key": "brave_api_key",
        "value": "secret-value",
        "scope": "global",
    })

    assert result["exit_code"] == 0
    assert result["status"] == "secret_handoff_required"
    assert result["secret_handoff_required"] is True
    assert not isolated_settings_files["settings"].exists()


def test_request_secret_creates_pending_handoff_without_echoing_value(isolated_settings_files):
    result = _run_manage({
        "action": "request_secret",
        "key": "brave_api_key",
        "value": "secret-value",
        "ttl_seconds": 60,
    })
    listed = _run_manage({"action": "secret_handoffs"})

    assert result["exit_code"] == 0
    assert result["secret_handoff"]["status"] == "pending"
    assert result["secret_handoff"]["key"] == "brave_api_key"
    assert listed["secret_handoffs"][0]["id"] == result["secret_handoff"]["id"]
    assert "secret-value" not in json.dumps(result)
    assert "secret-value" not in isolated_settings_files["handoffs"].read_text(encoding="utf-8")


def test_patch_structured_setting_through_tool(isolated_settings_files):
    result = _run_manage({
        "action": "patch",
        "key": "search_fallback_chain",
        "op": "append",
        "value": "brave",
        "scope": "global",
    })

    assert result["exit_code"] == 0
    assert result["value"] == ["duckduckgo", "brave"]
    assert settings_store.load_settings()["search_fallback_chain"] == ["duckduckgo", "brave"]


def test_reminder_channel_accepts_telegram_and_keeps_dry_run_configurable(isolated_settings_files):
    channel = _run_manage({
        "action": "set",
        "key": "reminder channel",
        "value": "telegram",
        "scope": "global",
    })
    dry_run = _run_manage({
        "action": "set",
        "key": "telegram reminder dry run",
        "value": True,
        "scope": "global",
    })

    assert channel["exit_code"] == 0
    assert channel["value"] == "telegram"
    assert settings_store.load_settings()["reminder_channel"] == "telegram"
    assert dry_run["exit_code"] == 0
    assert dry_run["value"] is True
    assert settings_store.load_settings()["reminder_telegram_dry_run"] is True


def test_confirm_protected_patch_blocks_until_confirmed(isolated_settings_files):
    absolute_root = str(
        (isolated_settings_files["settings"].parent / "tool-path-extra-root").resolve()
    )
    blocked = _run_manage({
        "action": "patch",
        "key": "tool_path_extra_roots",
        "op": "append",
        "value": absolute_root,
        "scope": "global",
    })
    assert blocked["exit_code"] == 0
    assert blocked["status"] == "confirmation_required"
    assert not isolated_settings_files["settings"].exists()

    updated = _run_manage({
        "action": "patch",
        "key": "tool_path_extra_roots",
        "op": "append",
        "value": absolute_root,
        "scope": "global",
        "confirmed": True,
    })

    assert updated["exit_code"] == 0
    assert updated["value"] == [absolute_root]


def test_feature_write_blocks_until_confirmed(isolated_settings_files):
    blocked = _run_manage({
        "action": "features",
        "key": "deep_research",
        "value": True,
    })
    updated = _run_manage({
        "action": "features",
        "key": "deep_research",
        "value": "true",
        "confirmed": True,
    })

    assert blocked["exit_code"] == 0
    assert blocked["status"] == "confirmation_required"
    assert updated["exit_code"] == 0
    assert updated["value"] is True
    assert settings_store.load_features()["deep_research"] is True


def test_explain_setting_exposes_policy(isolated_settings_files):
    result = _run_manage({
        "action": "explain",
        "key": "brave_api_key",
    })

    assert result["exit_code"] == 0
    assert result["setting"]["secret_handoff_required"] is True
    assert "secret_handoff_required" in result["response"]


def test_legacy_tool_toggle_still_works(isolated_settings_files):
    result = _run_manage({
        "action": "disable_tool",
        "tool": "shell",
    })

    assert result["exit_code"] == 0
    assert "bash" in result["disabled"]
    assert settings_store.load_settings()["disabled_tools"] == ["bash"]

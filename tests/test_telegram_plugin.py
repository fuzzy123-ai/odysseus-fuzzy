import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.telegram.plugin import PLUGIN, build_telegram_readiness, setup
from src.plugin_capability_boundary import validate_plugin_capability_boundary


@dataclass
class _PluginContext:
    app: FastAPI
    data_dir: Path
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("test.telegram"))

    def add_router(self, router):
        self.app.include_router(router)


def test_manifest_keeps_telegram_as_visible_standalone_ui_plugin():
    assert PLUGIN["name"] == "Telegram"
    assert PLUGIN["category"] == "Communications"
    assert PLUGIN["permission"] == "admin"
    assert PLUGIN["kind"] == "ui"
    assert PLUGIN["capabilities"] == ["local_api"]
    assert PLUGIN["ui"]["open"] == "/api/plugins/telegram/app"


def test_manifest_passes_plugin_capability_boundary():
    report = validate_plugin_capability_boundary(PLUGIN)

    assert report.ok
    assert report.error_codes == ()
    assert report.warning_codes == ()


def test_readiness_is_redacted_and_network_send_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "redacted-chat")

    status = build_telegram_readiness()

    assert status["plugin"] == "telegram"
    assert status["state"] == "manual_send_smoke_ready"
    assert status["token_env_present"] is True
    assert status["chat_id_env_present"] is True
    assert status["token_value_visible"] is False
    assert status["chat_id_value_visible"] is False
    assert status["network_enabled"] is False
    assert status["send_enabled"] is False
    assert "redacted-token" not in str(status)
    assert "redacted-chat" not in str(status)


def test_status_route_returns_redacted_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.get("/api/plugins/telegram/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["plugin"] == "telegram"
    assert payload["state"] == "needs_chat_id"
    assert payload["token_env_present"] is True
    assert payload["chat_id_env_present"] is False
    assert "redacted-token" not in response.text


def test_plugin_app_route_renders_safety_boundary(tmp_path):
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.get("/api/plugins/telegram/app")

    assert response.status_code == 200
    assert "Standalone plugin" in response.text
    assert "never displays tokens" in response.text
    assert "/api/plugins/telegram/status" in response.text


def test_plugin_file_loader_imports_without_package_context():
    plugin_path = Path("plugins/telegram/plugin.py")
    spec = importlib.util.spec_from_file_location("odysseus_plugin_telegram", plugin_path)
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert module.PLUGIN["name"] == "Telegram"

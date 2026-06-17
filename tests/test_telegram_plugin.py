import importlib.util
import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.telegram.plugin import (
    PLUGIN,
    TelegramInboxStore,
    TelegramPollingStateStore,
    TelegramSessionBridgeStore,
    build_agent_bridge_request,
    build_telegram_readiness,
    parse_telegram_update,
    run_telegram_polling_cycle,
    setup,
)
from src.plugin_capability_boundary import validate_plugin_capability_boundary


@dataclass
class _PluginContext:
    app: FastAPI
    data_dir: Path
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("test.telegram"))
    registered_tools: list = field(default_factory=list)

    def add_router(self, router):
        self.app.include_router(router)

    def register_tool(self, spec):
        self.registered_tools.append(spec)


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
    monkeypatch.delenv("TELEGRAM_AGENT_REPLY_ENABLED", raising=False)

    status = build_telegram_readiness()

    assert status["plugin"] == "telegram"
    assert status["state"] == "token_ready"
    assert status["token_env_present"] is True
    assert status["chat_id_env_present"] is True
    assert status["token_value_visible"] is False
    assert status["chat_id_value_visible"] is False
    assert status["send_enabled"] is False
    assert status["polling_enabled"] is False
    assert "redacted-token" not in str(status)
    assert "redacted-chat" not in str(status)


def test_status_route_returns_redacted_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.get("/api/plugins/telegram/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["plugin"] == "telegram"
    assert payload["state"] == "token_ready"
    assert payload["token_env_present"] is True
    assert payload["chat_id_env_present"] is False
    assert "redacted-token" not in response.text


def test_plugin_app_route_renders_safety_boundary(tmp_path):
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.get("/api/plugins/telegram/app")

    assert response.status_code == 200
    assert "Telegram agent chat" in response.text
    assert "local history" in response.text
    assert "/api/plugins/telegram/status" in response.text


def test_setup_registers_gated_telegram_reply_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.delenv("TELEGRAM_AGENT_REPLY_ENABLED", raising=False)
    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path)

    setup(ctx)

    tools = {tool.name: tool for tool in ctx.registered_tools}
    assert "telegram_reply" in tools
    result = asyncio.run(tools["telegram_reply"].execute(json.dumps({
        "chat_id": "123",
        "text": "Hallo",
    })))
    assert result["exit_code"] == 1
    assert "reply gate is disabled" in result["error"]


def test_plugin_file_loader_imports_without_package_context():
    plugin_path = Path("plugins/telegram/plugin.py")
    spec = importlib.util.spec_from_file_location("odysseus_plugin_telegram", plugin_path)
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert module.PLUGIN["name"] == "Telegram"


def test_parse_text_update_and_bridge_request(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    message = parse_telegram_update({
        "update_id": 7,
        "message": {
            "message_id": 11,
            "date": 123456,
            "chat": {"id": 123},
            "from": {"id": 42, "first_name": "Nina", "username": "nina"},
            "text": "Hallo Odysseus",
        },
    })

    assert message["kind"] == "text"
    assert message["chat_id"] == "123"
    assert message["chat_allowed"] is True
    assert message["token_value_visible"] is False

    bridge = build_agent_bridge_request(message)
    assert bridge["session_alias"] == "telegram:123"
    assert bridge["recommended_session_name"] == "Telegram nina"
    assert bridge["prompt"] == "Hallo Odysseus"
    assert bridge["ready_for_agent"] is True
    assert bridge["reply_required"] is True


def test_parse_voice_update_marks_pending_stt():
    message = parse_telegram_update({
        "update_id": 8,
        "message": {
            "message_id": 12,
            "chat": {"id": 456},
            "voice": {
                "file_id": "voice-file-id",
                "file_unique_id": "unique-voice",
                "duration": 3,
                "mime_type": "audio/ogg",
                "file_size": 2048,
            },
        },
    })

    assert message["kind"] == "voice"
    assert message["transcript_status"] == "pending_stt"
    assert message["media"]["file_id"] == "voice-file-id"
    bridge = build_agent_bridge_request(message)
    assert bridge["ready_for_agent"] is False
    assert bridge["note"] == "voice_needs_transcription"


def test_inbox_store_deduplicates_and_returns_history(tmp_path):
    store = TelegramInboxStore(tmp_path)
    message = {
        "direction": "inbound",
        "kind": "text",
        "update_id": 1,
        "message_id": 2,
        "chat_id": "abc",
        "text": "hi",
    }

    first = store.append_inbound(message)
    second = store.append_inbound(message)

    assert first["stored"] is True
    assert second["stored"] is False
    assert store.counts()["inbound"] == 1
    assert store.counts()["duplicates"] == 1
    assert any(item.get("text") == "hi" for item in store.history(chat_id="abc"))


def test_webhook_route_stores_inbound_and_returns_agent_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    app = FastAPI()
    created_sessions = []

    def _session_bridge(**kwargs):
        created_sessions.append(kwargs)
        return {"session_id": "sess-123"}

    ctx = _PluginContext(app=app, data_dir=tmp_path)
    ctx.telegram_session_bridge = _session_bridge
    setup(ctx)
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 10,
        "message": {
            "message_id": 20,
            "chat": {"id": 123},
            "from": {"id": 1, "first_name": "User"},
            "text": "Bitte fasse den Stand zusammen",
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["stored"] is True
    assert payload["agent_bridge"]["ready_for_agent"] is True
    assert payload["agent_bridge"]["session_alias"] == "telegram:123"
    assert payload["agent_bridge"]["session_id"] == "sess-123"
    assert "Bitte fasse" in payload["agent_bridge"]["prompt"]
    assert created_sessions[0]["chat_id"] == "123"

    history_response = client.get("/api/plugins/telegram/history?chat_id=123")
    assert history_response.status_code == 200
    assert history_response.json()["messages"][0]["text"] == "Bitte fasse den Stand zusammen"


def test_reply_route_is_blocked_without_explicit_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.delenv("TELEGRAM_AGENT_REPLY_ENABLED", raising=False)
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/reply", json={"chat_id": "123", "text": "Hallo"})

    assert response.status_code == 403
    assert "redacted-token" not in response.text
    history = TelegramInboxStore(tmp_path).history(chat_id="123")
    assert history[0]["delivery_status"] == "blocked"
    assert history[0]["failure_reason"] == "reply_gate_disabled"


def test_session_bridge_reuses_existing_mapping(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    created = []

    def _creator(**kwargs):
        created.append(kwargs)
        return {"session_id": "session-a"}

    first = store.bind_chat(
        chat_id="123",
        session_alias="telegram:123",
        recommended_session_name="Telegram nina",
        creator=_creator,
    )
    second = store.bind_chat(
        chat_id="123",
        session_alias="telegram:123",
        recommended_session_name="Telegram nina",
        creator=_creator,
    )

    assert first["created"] is True
    assert second["created"] is False
    assert second["session_id"] == "session-a"
    assert len(created) == 1


def test_polling_cycle_is_gated_and_stores_offset_without_network(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_POLLING_ENABLED", raising=False)

    result = run_telegram_polling_cycle(data_dir=tmp_path, fetch_updates=lambda offset: [])

    assert result["ok"] is False
    assert result["status"] == "polling_disabled"
    assert TelegramPollingStateStore(tmp_path).get_offset() == 0


def test_polling_cycle_stores_duplicate_and_unsupported_history(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    created = []

    def _fetch(_offset):
        return [
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 123},
                    "from": {"id": 1, "first_name": "Nina"},
                    "text": "Hallo",
                },
            },
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 123},
                    "from": {"id": 1, "first_name": "Nina"},
                    "text": "Hallo",
                },
            },
            {
                "update_id": 2,
                "message": {
                    "message_id": 11,
                    "chat": {"id": 123},
                    "sticker": {"emoji": "🙂"},
                },
            },
        ]

    def _creator(**kwargs):
        created.append(kwargs)
        return {"session_id": "session-123"}

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=_fetch,
        session_creator=_creator,
    )

    history = TelegramInboxStore(tmp_path).history(limit=20)
    statuses = {(item.get("kind"), item.get("status")) for item in history if item.get("direction") == "system"}

    assert result["ok"] is True
    assert result["offset"] == 3
    assert ("duplicate", "duplicate_ignored") in statuses
    assert ("unsupported", "unsupported_message") in statuses
    assert created[0]["chat_id"] == "123"


def test_reply_route_records_success_and_failure_history(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path)
    setup(ctx)
    client = TestClient(app)

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", lambda chat_id, text: {
        "ok": True,
        "telegram_message_id": 77,
        "token_value_visible": False,
    })
    ok_response = client.post("/api/plugins/telegram/reply", json={"chat_id": "123", "text": "Hallo"})
    assert ok_response.status_code == 200
    ok_history = TelegramInboxStore(tmp_path).history(chat_id="123")
    assert ok_history[0]["delivery_status"] == "sent"

    def _raise(*_args, **_kwargs):
        raise ValueError("transport offline")

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", _raise)
    fail_response = client.post("/api/plugins/telegram/reply", json={"chat_id": "123", "text": "Noch mal"})
    assert fail_response.status_code == 403
    fail_history = TelegramInboxStore(tmp_path).history(chat_id="123")
    assert any(item.get("delivery_status") == "failed" for item in fail_history)
    assert any(item.get("failure_reason") == "transport offline" for item in fail_history)

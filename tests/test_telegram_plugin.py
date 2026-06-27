import importlib.util
import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from plugins.telegram.plugin import (
    PLUGIN,
    TelegramInboxStore,
    TelegramPollingStateStore,
    TelegramPrivacyPinStore,
    TelegramSessionBridgeStore,
    _telegram_control_command,
    build_agent_bridge_request,
    build_telegram_readiness,
    download_telegram_voice_bytes,
    parse_telegram_update,
    run_telegram_polling_cycle,
    setup,
)
from src.image_tools_worker import ImageToolsWorkerResult
from src.plugin_capability_boundary import validate_plugin_capability_boundary


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _PluginContext:
    app: FastAPI
    data_dir: Path
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("test.telegram"))
    registered_tools: list = field(default_factory=list)
    require_admin: Callable[[Any], None] = lambda _request: None
    telegram_agent_turn_handler: Callable[[dict[str, Any]], Any] | None = None

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


def test_core_telegram_bridge_uses_agent_loop_for_tool_access():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    start = source.index("def _telegram_agent_turn_handler")
    end = source.index("app.state.telegram_session_bridge", start)
    body = source[start:end]

    assert "stream_agent_loop" in body
    assert "llm_call(" not in body
    assert "enforce_session_provider_runtime_gate" in body
    assert "telegram_dsgvo_provider_gate_failed" in body


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
    assert status["voice_boundary"]["mode"] == "metadata_only"
    assert status["voice_boundary"]["download_enabled"] is False
    assert status["voice_boundary"]["stt_enabled"] is False
    assert status["voice_boundary"]["raw_voice_ids_visible"] is False
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


def test_plugin_routes_call_admin_gate(tmp_path):
    def _deny_admin(_request):
        raise HTTPException(403, "admin required")

    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path, require_admin=_deny_admin))
    client = TestClient(app)

    checks = [
        ("get", "/api/plugins/telegram/status", None),
        ("get", "/api/plugins/telegram/history", None),
        ("get", "/api/plugins/telegram/app", None),
        ("post", "/api/plugins/telegram/poll", None),
        ("post", "/api/plugins/telegram/webhook", {"message": {"chat": {"id": "fake"}, "text": "hi"}}),
        ("post", "/api/plugins/telegram/reply", {"chat_id": "fake", "text": "hi"}),
    ]

    for method, path, body in checks:
        response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
        assert response.status_code == 403
        assert "admin required" in response.text


def test_setup_registers_gated_telegram_reply_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.delenv("TELEGRAM_AGENT_REPLY_ENABLED", raising=False)
    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path)

    setup(ctx)

    tools = {tool.name: tool for tool in ctx.registered_tools}
    assert "telegram_reply" in tools
    properties = tools["telegram_reply"].parameters["properties"]
    assert properties["classification"]["enum"] == ["public", "private", "sensitive", "secret"]
    assert properties["security_mode"]["enum"] == ["normal", "secure"]
    result = asyncio.run(tools["telegram_reply"].execute(json.dumps({
        "chat_id": "123",
        "text": "Hallo",
    })))
    assert result["exit_code"] == 1
    assert "reply gate is disabled" in result["error"]


def test_setup_registers_safe_notification_tool_without_target_parameter(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_AGENT_REPLY_ENABLED", raising=False)
    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path)

    setup(ctx)

    tools = {tool.name: tool for tool in ctx.registered_tools}
    notify_tool = tools["odysseus_notify_user"]
    properties = notify_tool.parameters["properties"]
    assert "chat_id" not in properties
    assert "token" not in properties
    result = asyncio.run(notify_tool.execute(json.dumps({
        "event": "roadmap_completed",
        "message": "ABC roadmap completed.",
        "severity": "success",
    })))
    payload = json.loads(result["output"])
    assert result["exit_code"] == 0
    assert payload["status"] == "dry_run"
    assert payload["dispatch_allowed"] is False
    assert payload["token_value_visible"] is False
    assert payload["chat_target_value_visible"] is False


def test_notification_tool_rejects_secret_or_target_arguments(tmp_path):
    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path)
    setup(ctx)
    tools = {tool.name: tool for tool in ctx.registered_tools}

    result = asyncio.run(tools["odysseus_notify_user"].execute(json.dumps({
        "message": "Do not route this.",
        "chat_id": "synthetic-test-target",
    })))

    assert result["exit_code"] == 1
    assert "Forbidden notification key" in result["error"]
    assert result["token_value_visible"] is False
    assert result["chat_target_value_visible"] is False


def test_notification_tool_uses_server_side_target_for_gated_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "server-side-target")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    sent = []

    def _send(chat_id, text):
        sent.append((chat_id, text))
        return {"ok": True, "telegram_message_id": 93, "token_value_visible": False}

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", _send)
    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path)
    ctx.telegram_notification_target = "server-side-target"
    setup(ctx)
    tools = {tool.name: tool for tool in ctx.registered_tools}

    result = asyncio.run(tools["odysseus_notify_user"].execute(json.dumps({
        "event": "roadmap_completed",
        "message": "ABC roadmap completed.",
        "severity": "success",
        "dry_run": False,
    })))
    payload = json.loads(result["output"])

    assert result["exit_code"] == 0
    assert payload["status"] == "sent"
    assert payload["dispatch_allowed"] is True
    assert payload["reason"] == "server_side_dispatch_sent"
    assert sent == [("server-side-target", "[Odysseus][success] roadmap_completed: ABC roadmap completed.")]
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "server-side-target" not in persisted_text
    assert '"chat_id"' not in persisted_text


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
    assert bridge["session_alias"].startswith("telegram:chat_")
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
    assert message["voice_status"] == "pending_stt"
    assert message["media"]["file_id"] == "voice-file-id"
    bridge = build_agent_bridge_request(message)
    assert bridge["ready_for_agent"] is False
    assert bridge["note"] == "voice_needs_transcription"
    assert "file_handle=" in bridge["prompt"]
    assert "voice-file-id" not in bridge["prompt"]


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
    persisted = json.loads((tmp_path / "telegram_history.json").read_text(encoding="utf-8"))
    persisted_text = json.dumps(persisted, ensure_ascii=False)
    assert '"chat_id"' not in persisted_text
    assert "abc" not in persisted_text


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
    assert payload["message"]["chat_handle"].startswith("chat_")
    assert "chat_id" not in payload["message"]
    assert payload["message"]["sender"]["handle"].startswith("sender_")
    assert "id" not in payload["message"]["sender"]
    assert payload["agent_bridge"]["ready_for_agent"] is True
    assert payload["agent_bridge"]["session_alias"].startswith("telegram:chat_")
    assert payload["agent_bridge"]["session_id"] == "sess-123"
    assert "Bitte fasse" in payload["agent_bridge"]["prompt"]
    assert created_sessions[0]["chat_id"] == "123"

    history_response = client.get("/api/plugins/telegram/history?chat_id=123")
    assert history_response.status_code == 200
    assert history_response.json()["messages"][0]["text"] == "Bitte fasse den Stand zusammen"
    persisted = json.loads((tmp_path / "telegram_history.json").read_text(encoding="utf-8"))
    persisted_text = json.dumps(persisted, ensure_ascii=False)
    assert '"chat_id"' not in persisted_text
    assert '"id"' not in persisted_text


def test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "allowed-chat")
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 22,
        "message": {
            "message_id": 33,
            "chat": {"id": "blocked-chat"},
            "from": {"id": "blocked-sender"},
            "text": "ignore me",
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_bridge"]["ready_for_agent"] is False
    assert payload["message"]["intake_status"] == "blocked_chat"
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(item.get("kind") == "blocked" and item.get("status") == "chat_not_allowed" for item in history)
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "blocked-chat" not in persisted_text
    assert "blocked-sender" not in persisted_text
    assert '"chat_id"' not in persisted_text


def test_webhook_rejects_malformed_update_without_raw_payload_leak(tmp_path):
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "raw": {"chat": {"id": "raw-chat-id"}, "text": "do not persist"},
    })

    assert response.status_code == 400
    assert "raw-chat-id" not in response.text
    assert "do not persist" not in response.text
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "raw-chat-id" not in persisted_text
    assert "do not persist" not in persisted_text
    assert "invalid_update" in persisted_text


def test_webhook_invokes_agent_turn_handler_and_gated_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    turns = []

    def _session_bridge(**_kwargs):
        return {"session_id": "sess-telegram"}

    def _agent_turn(bridge):
        turns.append(bridge)
        return {"status": "accepted", "reply_text": "Antwort vom Agenten"}

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", lambda chat_id, text: {
        "ok": True,
        "telegram_message_id": 88,
        "token_value_visible": False,
    })
    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path, telegram_agent_turn_handler=_agent_turn)
    ctx.telegram_session_bridge = _session_bridge
    setup(ctx)
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 44,
        "message": {
            "message_id": 55,
            "chat": {"id": 123},
            "from": {"id": 1, "first_name": "User"},
            "text": "Bitte antworte",
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert turns[0]["session_id"] == "sess-telegram"
    assert turns[0]["prompt"] == "Bitte antworte"
    assert payload["agent_turn"]["status"] == "accepted"
    assert payload["agent_turn"]["reply_text_present"] is True
    assert payload["agent_turn"]["reply_text_value_visible"] is False
    assert "Antwort vom Agenten" not in json.dumps(payload["agent_turn"], ensure_ascii=False)
    assert payload["reply"]["sent"]["telegram_message_id"] == 88
    history = TelegramInboxStore(tmp_path).history(chat_id="123", limit=20)
    assert any(item.get("kind") == "agent_turn" for item in history)
    assert any(item.get("direction") == "outbound" and item.get("delivery_status") == "sent" for item in history)


def test_webhook_uses_app_state_agent_bridge_when_context_has_no_direct_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    turns = []

    def _session_bridge(**kwargs):
        assert kwargs["chat_id"] == "123"
        return {"session_id": "sess-from-app-state"}

    async def _agent_turn(bridge):
        turns.append(bridge)
        await asyncio.sleep(0)
        return {"status": "accepted", "reply_text": "App-state Antwort"}

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", lambda chat_id, text: {
        "ok": True,
        "telegram_message_id": 89,
        "token_value_visible": False,
    })
    app = FastAPI()
    app.state.telegram_session_bridge = _session_bridge
    app.state.telegram_agent_turn_handler = _agent_turn
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 45,
        "message": {
            "message_id": 56,
            "chat": {"id": 123},
            "from": {"id": 1, "first_name": "User"},
            "text": "Bitte antworte ueber die echte Bridge",
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert turns[0]["session_id"] == "sess-from-app-state"
    assert payload["agent_turn"]["status"] == "accepted"
    assert payload["agent_turn"]["reply_text_present"] is True
    assert "App-state Antwort" not in json.dumps(payload["agent_turn"], ensure_ascii=False)
    assert payload["reply"]["sent"]["telegram_message_id"] == 89


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


def test_reply_route_rejects_disallowed_chat_when_reply_gate_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "allowed-chat")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/reply", json={"chat_id": "blocked-chat", "text": "Hallo"})

    assert response.status_code == 403
    history = TelegramInboxStore(tmp_path).history(chat_id="blocked-chat")
    assert history[0]["delivery_status"] == "blocked"
    assert history[0]["failure_reason"] == "chat_not_allowed"
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "blocked-chat" not in persisted_text
    assert '"chat_id"' not in persisted_text


def test_reply_route_blocks_sensitive_classification_by_channel_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    sent = []

    def _send(chat_id, text):
        sent.append((chat_id, text))
        return {"ok": True}

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", _send)
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/plugins/telegram/reply",
        json={
            "chat_id": "123",
            "text": "Sensible Antwort",
            "classification": "sensitive",
            "security_mode": "normal",
        },
    )

    assert response.status_code == 403
    assert sent == []
    history = TelegramInboxStore(tmp_path).history(chat_id="123")
    assert history[0]["delivery_status"] == "blocked"
    assert history[0]["failure_reason"] == "sensitive_source_in_normal_chat"


def test_reply_route_allows_public_classification_by_channel_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    sent = []

    def _send(chat_id, text):
        sent.append((chat_id, text))
        return {"ok": True, "telegram_message_id": 77, "token_value_visible": False}

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", _send)
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/plugins/telegram/reply",
        json={
            "chat_id": "123",
            "text": "Public Antwort",
            "classification": "public",
            "security_mode": "normal",
        },
    )

    assert response.status_code == 200
    assert sent == [("123", "Public Antwort")]


def test_session_bridge_reuses_existing_mapping(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    created = []
    raw_chat_id = "chat-raw-123"

    def _creator(**kwargs):
        created.append(kwargs)
        return {"session_id": "session-a"}

    first = store.bind_chat(
        chat_id=raw_chat_id,
        session_alias=f"telegram:{raw_chat_id}",
        recommended_session_name="Telegram nina",
        creator=_creator,
    )
    second = store.bind_chat(
        chat_id=raw_chat_id,
        session_alias=f"telegram:{raw_chat_id}",
        recommended_session_name="Telegram nina",
        creator=_creator,
    )

    assert first["created"] is True
    assert second["created"] is False
    assert second["session_id"] == "session-a"
    assert len(created) == 1
    persisted = json.loads((tmp_path / "telegram_session_bridge.json").read_text(encoding="utf-8"))
    persisted_text = json.dumps(persisted, ensure_ascii=False)
    assert raw_chat_id not in persisted_text
    assert '"chat_id"' not in persisted_text
    assert second["mapping"]["chat_handle"].startswith("chat_")
    assert second["mapping"]["session_alias"].startswith("telegram:chat_")


def test_session_bridge_rebinds_existing_mapping_for_new_chat(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    raw_chat_id = "chat-raw-123"
    created = []

    def _creator(**kwargs):
        created.append(kwargs)
        return {"session_id": f"session-{len(created)}"}

    first = store.bind_chat(
        chat_id=raw_chat_id,
        session_alias=f"telegram:{raw_chat_id}",
        recommended_session_name="Telegram nina",
        creator=_creator,
    )
    second = store.rebind_chat(
        chat_id=raw_chat_id,
        session_alias=f"telegram:{raw_chat_id}",
        recommended_session_name="Telegram nina",
        creator=_creator,
    )

    assert first["session_id"] == "session-1"
    assert second["session_id"] == "session-2"
    assert len(created) == 2
    persisted = json.loads((tmp_path / "telegram_session_bridge.json").read_text(encoding="utf-8"))
    persisted_text = json.dumps(persisted, ensure_ascii=False)
    assert "session-1" not in persisted_text
    assert "session-2" in persisted_text
    assert raw_chat_id not in persisted_text


def test_polling_cycle_is_gated_and_stores_offset_without_network(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_POLLING_ENABLED", raising=False)

    result = run_telegram_polling_cycle(data_dir=tmp_path, fetch_updates=lambda offset: [])

    assert result["ok"] is False
    assert result["status"] == "polling_disabled"
    assert TelegramPollingStateStore(tmp_path).get_offset() == 0


def test_polling_cycle_new_command_rebinds_session_without_agent_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    created = []
    replies = []
    turns = []
    store = TelegramSessionBridgeStore(tmp_path)
    store.bind_chat(
        chat_id="123",
        session_alias="telegram:123",
        recommended_session_name="Telegram Nina",
        creator=lambda **_kwargs: {"session_id": "old-session"},
    )

    def _creator(**kwargs):
        created.append(kwargs)
        return {"session_id": "new-session"}

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 8,
            "message": {
                "message_id": 80,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "/new",
            },
        }],
        session_creator=_creator,
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": "nope"},
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert result["ok"] is True
    assert result["processed"] == 1
    assert result["control_commands"] == 1
    assert result["agent_turns"] == 0
    assert result["replies"] == 1
    assert replies == [("123", "Neuer Chat gestartet.", 80)]
    assert turns == []
    assert created[0]["chat_id"] == "123"
    mapping = TelegramSessionBridgeStore(tmp_path).get("123")
    assert mapping is not None
    assert mapping["session_id"] == "new-session"
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(item.get("kind") == "control_command" and item.get("status") == "new_chat_bound" for item in history)


def test_telegram_control_command_detects_dsgvo_aliases():
    assert _telegram_control_command({"kind": "text", "text": "/dsgvo on"}) == "dsgvo_enable"
    assert _telegram_control_command({"kind": "text", "text": "/privacy aus"}) == "dsgvo_disable"
    assert _telegram_control_command({"kind": "text", "text": "/gdpr"}) == "dsgvo_toggle"
    assert _telegram_control_command({"kind": "text", "text": "/dsgvo status"}) == "dsgvo_status"
    assert _telegram_control_command({"kind": "text", "text": "/datenschutz maybe"}) == "dsgvo_help"


def test_polling_cycle_dsgvo_command_updates_settings_without_agent_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")
    settings_state = {"dsgvo_mode": False}
    replies = []
    pin_calls = []
    turns = []

    def _load_settings():
        return dict(settings_state)

    def _save_settings(new_settings):
        settings_state.clear()
        settings_state.update(new_settings)

    monkeypatch.setattr("plugins.telegram.plugin._load_dsgvo_settings", _load_settings)
    monkeypatch.setattr("plugins.telegram.plugin._save_dsgvo_settings", _save_settings)
    monkeypatch.setattr(
        "plugins.telegram.plugin.send_telegram_pin_message",
        lambda chat_id, message_id: pin_calls.append((chat_id, message_id)) or {
            "ok": True,
            "telegram_message_id": message_id,
            "pin_status": "pinned",
        },
    )

    def _reply(chat_id, text, source_message_id=None):
        replies.append((chat_id, text, source_message_id))
        return {
            "exit_code": 0,
            "output": json.dumps({
                "sent": {
                    "ok": True,
                    "telegram_message_id": 777,
                    "token_value_visible": False,
                    "chat_id_value_visible": False,
                },
                "message": {"delivery_status": "sent", "chat_id_value_visible": False},
            }),
        }

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 81,
            "message": {
                "message_id": 90,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "/dsgvo",
            },
        }],
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": "nope"},
        reply_handler=_reply,
    )

    assert result["ok"] is True
    assert result["control_commands"] == 1
    assert result["agent_turns"] == 0
    assert settings_state["dsgvo_mode"] is True
    assert replies == [("123", "DSGVO-Modus ist jetzt aktiv. Telegram laeuft local-only; externe Web-, Provider- und Tool-I/O ist gesperrt.", 90)]
    assert pin_calls == [("123", 777)]
    assert TelegramPrivacyPinStore(tmp_path).get_pin("123")["message_id"] == 777
    assert turns == []
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(item.get("kind") == "control_command" and item.get("status") == "dsgvo_enabled" for item in history)
    assert any(item.get("kind") == "privacy_pin" and item.get("status") == "pinned" for item in history)
    persisted = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "123" not in persisted
    assert '"chat_id"' not in persisted
    pin_state = (tmp_path / "telegram_privacy_pin_state.json").read_text(encoding="utf-8")
    assert "123" not in pin_state
    assert '"chat_id"' not in pin_state


def test_dsgvo_command_from_blocked_chat_does_not_change_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "allowed-chat")
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")
    settings_state = {"dsgvo_mode": False}
    replies = []
    created = []

    monkeypatch.setattr("plugins.telegram.plugin._load_dsgvo_settings", lambda: dict(settings_state))
    monkeypatch.setattr("plugins.telegram.plugin._save_dsgvo_settings", lambda new_settings: settings_state.update(new_settings))

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 82,
            "message": {
                "message_id": 91,
                "chat": {"id": "blocked-chat"},
                "from": {"id": "blocked-sender"},
                "text": "/dsgvo on",
            },
        }],
        session_creator=lambda **kwargs: created.append(kwargs) or {"session_id": "should-not-happen"},
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert result["ok"] is True
    assert result["control_commands"] == 1
    assert settings_state["dsgvo_mode"] is False
    assert replies == []
    assert created == []
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(item.get("kind") == "control_command" and item.get("status") == "control_chat_not_allowed" for item in history)
    persisted = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "blocked-chat" not in persisted
    assert "blocked-sender" not in persisted


def test_polling_cycle_dsgvo_disable_unpins_privacy_message(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")
    settings_state = {"dsgvo_mode": True}
    replies = []
    unpin_calls = []
    TelegramPrivacyPinStore(tmp_path).set_pin("123", 777)

    monkeypatch.setattr("plugins.telegram.plugin._load_dsgvo_settings", lambda: dict(settings_state))
    monkeypatch.setattr("plugins.telegram.plugin._save_dsgvo_settings", lambda new_settings: settings_state.update(new_settings))
    monkeypatch.setattr(
        "plugins.telegram.plugin.send_telegram_unpin_message",
        lambda chat_id, message_id: unpin_calls.append((chat_id, message_id)) or {
            "ok": True,
            "telegram_message_id": message_id,
            "pin_status": "unpinned",
        },
    )

    def _reply(chat_id, text, source_message_id=None):
        replies.append((chat_id, text, source_message_id))
        return {
            "exit_code": 0,
            "output": json.dumps({
                "sent": {"ok": True, "telegram_message_id": 778},
                "message": {"delivery_status": "sent", "chat_id_value_visible": False},
            }),
        }

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 83,
            "message": {
                "message_id": 92,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "/dsgvo",
            },
        }],
        reply_handler=_reply,
    )

    assert result["ok"] is True
    assert result["control_commands"] == 1
    assert settings_state["dsgvo_mode"] is False
    assert replies == [("123", "DSGVO-Modus ist jetzt aus. Normale Provider- und Tool-Regeln gelten wieder.", 92)]
    assert unpin_calls == [("123", 777)]
    assert TelegramPrivacyPinStore(tmp_path).get_pin("123") is None
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(item.get("kind") == "privacy_pin" and item.get("status") == "unpinned" for item in history)
    persisted = (tmp_path / "telegram_privacy_pin_state.json").read_text(encoding="utf-8")
    assert "123" not in persisted
    assert '"chat_id"' not in persisted


def test_readiness_reports_dsgvo_boundary_without_settings_values(tmp_path, monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "0")
    monkeypatch.setattr("plugins.telegram.plugin._load_dsgvo_settings", lambda: {
        "dsgvo_mode": True,
        "api_key": "sk-leak-sentinel",
        "telegram_chat_id": "1234567890",
    })

    readiness = build_telegram_readiness(tmp_path)
    encoded = json.dumps(readiness, ensure_ascii=False)

    assert readiness["privacy_boundary"]["dsgvo_mode"] is True
    assert readiness["privacy_boundary"]["local_only_required"] is True
    assert readiness["privacy_boundary"]["settings_values_visible"] is False
    assert readiness["privacy_boundary"]["pinned_status_enabled"] is True
    assert readiness["privacy_boundary"]["active_pinned_status_count"] == 0
    assert readiness["privacy_boundary"]["pin_message_id_value_visible"] is False
    assert "/dsgvo" in readiness["privacy_boundary"]["telegram_commands"]
    assert "pinned_status_message" in readiness["privacy_boundary"]["chat_feedback_modes"]
    assert "sk-leak-sentinel" not in encoded
    assert "1234567890" not in encoded


def test_polling_cycle_records_blocked_chat_without_session_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "allowed-chat")
    created = []

    def _fetch(_offset):
        return [{
            "update_id": 5,
            "message": {
                "message_id": 50,
                "chat": {"id": "blocked-chat"},
                "from": {"id": "sender-id"},
                "text": "Hallo",
            },
        }]

    def _creator(**kwargs):
        created.append(kwargs)
        return {"session_id": "should-not-happen"}

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=_fetch,
        session_creator=_creator,
    )

    assert result["ok"] is True
    assert result["processed"] == 1
    assert created == []
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(item.get("kind") == "blocked" and item.get("status") == "chat_not_allowed" for item in history)
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "blocked-chat" not in persisted_text
    assert "sender-id" not in persisted_text
    assert '"chat_id"' not in persisted_text


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
        return {"session_id": "session-alpha"}

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
    persisted = json.loads((tmp_path / "telegram_history.json").read_text(encoding="utf-8"))
    persisted_text = json.dumps(persisted, ensure_ascii=False)
    assert '"chat_id"' not in persisted_text


def test_poll_route_uses_app_state_hooks_without_event_loop_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    turns = []

    def _fetch(_offset):
        return [{
            "update_id": 9,
            "message": {
                "message_id": 90,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "Hallo Poll",
            },
        }]

    def _session_bridge(**_kwargs):
        return {"session_id": "session-poll"}

    def _agent_turn(bridge):
        turns.append(bridge)
        return {"status": "accepted", "reply_text": "Poll Antwort"}

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", lambda chat_id, text: {
        "ok": True,
        "telegram_message_id": 91,
        "token_value_visible": False,
    })
    app = FastAPI()
    app.state.telegram_fetch_updates = _fetch
    app.state.telegram_session_bridge = _session_bridge
    app.state.telegram_agent_turn_handler = _agent_turn
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/poll")

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed"] == 1
    assert payload["agent_turns"] == 1
    assert payload["replies"] == 1
    assert turns[0]["session_id"] == "session-poll"
    history = TelegramInboxStore(tmp_path).history(chat_id="123", limit=20)
    assert any(item.get("direction") == "outbound" and item.get("delivery_status") == "sent" for item in history)


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
    persisted = json.loads((tmp_path / "telegram_history.json").read_text(encoding="utf-8"))
    persisted_text = json.dumps(persisted, ensure_ascii=False)
    assert "123" not in persisted_text
    assert '"chat_id"' not in persisted_text


def test_voice_identifiers_are_redacted_in_persisted_history(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat-999")
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 8,
        "message": {
            "message_id": 12,
            "chat": {"id": "voice-chat-999"},
            "voice": {
                "file_id": "voice-file-id",
                "file_unique_id": "unique-voice",
                "duration": 3,
                "mime_type": "audio/ogg",
                "file_size": 2048,
            },
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"]["media"]["file_handle"].startswith("voice_file_")
    assert payload["message"]["media"]["file_unique_handle"].startswith("voice_unique_")
    assert payload["message"]["voice_status"] == "pending_stt"
    assert "file_id" not in payload["message"]["media"]
    assert "file_unique_id" not in payload["message"]["media"]
    persisted = json.loads((tmp_path / "telegram_history.json").read_text(encoding="utf-8"))
    persisted_text = json.dumps(persisted, ensure_ascii=False)
    assert "voice-file-id" not in persisted_text
    assert "unique-voice" not in persisted_text


def test_image_identifiers_are_redacted_and_actions_are_default_off(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "image-chat-999")
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 28,
        "message": {
            "message_id": 32,
            "chat": {"id": "image-chat-999"},
            "photo": [
                {"file_id": "small-image-file-id", "file_unique_id": "small-unique", "width": 32, "height": 32, "file_size": 128},
                {"file_id": "large-image-file-id", "file_unique_id": "large-unique", "width": 512, "height": 512, "file_size": 4096},
            ],
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"]["kind"] == "image"
    assert payload["message"]["media"]["file_handle"].startswith("image_file_")
    assert payload["message"]["media"]["file_unique_handle"].startswith("image_unique_")
    assert payload["image_action"]["plan"]["status"] == "disabled"
    assert payload["agent_bridge"]["note"] == "image_action_pending"
    assert "file_id" not in payload["message"]["media"]
    assert "file_unique_id" not in payload["message"]["media"]
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "large-image-file-id" not in persisted_text
    assert "large-unique" not in persisted_text


def test_webhook_image_action_uses_injected_worker_without_raw_image_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "image-chat-999")
    monkeypatch.setenv("TELEGRAM_IMAGE_ACTIONS_ENABLED", "true")
    calls = []

    class FakeImageWorker:
        def remove_background(self, image_bytes, hint_mask_bytes=None):
            calls.append((image_bytes, hint_mask_bytes))
            return ImageToolsWorkerResult(ok=True, image_bytes=b"\x89PNG\r\n\x1a\ntelegram-worker")

    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path)
    ctx.telegram_image_bytes_provider = lambda file_handle: b"source:" + file_handle.encode("ascii")
    ctx.telegram_image_worker_client = FakeImageWorker()
    setup(ctx)
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 29,
        "message": {
            "message_id": 33,
            "chat": {"id": "image-chat-999"},
            "photo": [
                {"file_id": "image-file-id", "file_unique_id": "image-unique", "width": 128, "height": 128, "file_size": 2048},
            ],
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["image_action"]["plan"]["allowed"] is True
    assert payload["image_action"]["worker"]["called"] is True
    assert payload["image_action"]["worker"]["ok"] is True
    assert payload["image_action"]["worker"]["output_image_present"] is True
    assert payload["image_action"]["worker"]["raw_image_visible"] is False
    assert calls and calls[0][0].startswith(b"source:image_file_")
    assert "image-file-id" not in response.text


def test_webhook_voice_pipeline_can_create_fake_stt_agent_turn_without_persisting_transcript(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat-999")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_DOWNLOAD_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_STT_ENABLED", "true")
    turns = []

    def _session_bridge(**_kwargs):
        return {"session_id": "sess-voice"}

    def _agent_turn(bridge):
        turns.append(bridge)
        return {"status": "accepted", "reply_text": ""}

    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path, telegram_agent_turn_handler=_agent_turn)
    ctx.telegram_session_bridge = _session_bridge
    ctx.telegram_voice_stt_provider = lambda _local_ref: "Bitte fasse meine Notiz zusammen token=raw-secret"
    setup(ctx)
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 18,
        "message": {
            "message_id": 22,
            "chat": {"id": "voice-chat-999"},
            "voice": {
                "file_id": "voice-file-id",
                "file_unique_id": "unique-voice",
                "duration": 3,
                "mime_type": "audio/ogg",
                "file_size": 2048,
            },
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["voice_pipeline"]["download"]["allowed"] is True
    assert payload["voice_pipeline"]["stt"]["transcript_present"] is True
    assert payload["voice_pipeline"]["stt"]["transcript_value_visible"] is False
    assert payload["agent_bridge"]["ready_for_agent"] is True
    assert payload["agent_bridge"]["note"] == "voice_transcribed"
    assert "Bitte fasse meine Notiz" in turns[0]["prompt"]
    assert "raw-secret" not in turns[0]["prompt"]
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "Bitte fasse meine Notiz" not in persisted_text
    assert "voice-file-id" not in persisted_text
    assert "unique-voice" not in persisted_text


def test_polling_cycle_voice_pipeline_uses_fake_stt_provider_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat-999")
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_DOWNLOAD_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_STT_ENABLED", "true")
    turns = []

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 1,
            "message": {
                "message_id": 2,
                "chat": {"id": "voice-chat-999"},
                "voice": {
                    "file_id": "voice-file-id",
                    "file_unique_id": "unique-voice",
                    "duration": 2,
                    "mime_type": "audio/ogg",
                    "file_size": 1024,
                },
            },
        }],
        session_creator=lambda **_kwargs: {"session_id": "sess-poll-voice"},
        voice_stt_provider=lambda _local_ref: "voice transcript ready",
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": ""},
    )

    assert result["status"] == "poll_ok"
    assert result["agent_turns"] == 1
    assert turns[0]["note"] == "voice_transcribed"
    assert "voice transcript ready" in turns[0]["prompt"]
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "voice transcript ready" not in persisted_text


def test_polling_cycle_voice_pipeline_uses_local_stt_service_without_persisting_transcript(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat-999")
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_DOWNLOAD_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_STT_ENABLED", "true")
    calls = []
    turns = []

    class _FakeSttService:
        def transcribe(self, audio_bytes: bytes) -> str:
            calls.append(audio_bytes)
            return "lokale STT Abschrift token=raw-secret"

    monkeypatch.setattr("services.stt.get_stt_service", lambda: _FakeSttService())

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 2,
            "message": {
                "message_id": 3,
                "chat": {"id": "voice-chat-999"},
                "voice": {
                    "file_id": "voice-file-id",
                    "file_unique_id": "unique-voice",
                    "duration": 2,
                    "mime_type": "audio/ogg",
                    "file_size": 1024,
                },
            },
        }],
        session_creator=lambda **_kwargs: {"session_id": "sess-local-stt"},
        voice_bytes_provider=lambda _message, max_bytes: b"synthetic-ogg-bytes",
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": ""},
    )

    assert result["status"] == "poll_ok"
    assert result["agent_turns"] == 1
    assert calls == [b"synthetic-ogg-bytes"]
    assert turns[0]["note"] == "voice_transcribed"
    assert "lokale STT Abschrift" in turns[0]["prompt"]
    assert "raw-secret" not in turns[0]["prompt"]
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "lokale STT Abschrift" not in persisted_text
    assert "voice-file-id" not in persisted_text
    assert "unique-voice" not in persisted_text


def test_polling_cycle_holds_offset_for_pending_voice_stt_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat-999")
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_DOWNLOAD_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_STT_ENABLED", "true")

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 11,
            "message": {
                "message_id": 12,
                "chat": {"id": "voice-chat-999"},
                "voice": {
                    "file_id": "voice-file-id",
                    "file_unique_id": "unique-voice",
                    "duration": 2,
                    "mime_type": "audio/ogg",
                    "file_size": 1024,
                },
            },
        }],
        session_creator=lambda **_kwargs: {"session_id": "sess-pending-stt"},
        voice_stt_provider=lambda _local_ref: "",
        agent_turn_handler=lambda _bridge: {"status": "accepted", "reply_text": ""},
    )

    assert result["status"] == "poll_ok"
    assert result["pending_retries"] == 1
    assert result["offset"] == 0
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(
        item.get("kind") == "voice_retry" and item.get("status") == "pending_stt_retry_scheduled"
        for item in history
    )


def test_polling_cycle_retries_duplicate_pending_voice_before_advancing_offset(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat-999")
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_DOWNLOAD_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_STT_ENABLED", "true")
    turns = []

    first = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 21,
            "message": {
                "message_id": 22,
                "chat": {"id": "voice-chat-999"},
                "voice": {
                    "file_id": "voice-file-id",
                    "file_unique_id": "unique-voice",
                    "duration": 2,
                    "mime_type": "audio/ogg",
                    "file_size": 1024,
                },
            },
        }],
        session_creator=lambda **_kwargs: {"session_id": "sess-retry-stt"},
        voice_stt_provider=lambda _local_ref: "",
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": ""},
    )

    second = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 21,
            "message": {
                "message_id": 22,
                "chat": {"id": "voice-chat-999"},
                "voice": {
                    "file_id": "voice-file-id",
                    "file_unique_id": "unique-voice",
                    "duration": 2,
                    "mime_type": "audio/ogg",
                    "file_size": 1024,
                },
            },
        }],
        session_creator=lambda **_kwargs: {"session_id": "sess-retry-stt"},
        voice_stt_provider=lambda _local_ref: "voice transcript after restart",
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": ""},
    )

    assert first["offset"] == 0
    assert second["offset"] == 22
    assert second["agent_turns"] == 1
    assert turns[0]["note"] == "voice_transcribed"
    assert "voice transcript after restart" in turns[0]["prompt"]
    counts = TelegramInboxStore(tmp_path).counts()
    assert counts["voice"] == 1
    assert counts["pending_stt"] == 0
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "voice transcript after restart" not in persisted_text
    assert "voice-file-id" not in persisted_text
    assert "unique-voice" not in persisted_text


def test_download_telegram_voice_bytes_uses_get_file_and_size_limit(monkeypatch):
    class _Response:
        def __init__(self, payload: bytes, headers: dict[str, str] | None = None):
            self._payload = payload
            self.headers = headers or {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return self._payload

    calls = []

    def _urlopen(url, timeout):
        calls.append((url, timeout))
        if len(calls) == 1:
            return _Response(json.dumps({
                "ok": True,
                "result": {"file_path": "voice/file.ogg", "file_size": 4},
            }).encode("utf-8"))
        return _Response(b"1234", {"Content-Length": "4"})

    audio = download_telegram_voice_bytes(
        {
            "media": {
                "file_id": "raw-file-id",
                "file_size": 4,
            },
        },
        max_bytes=8,
        token="secret-token",
        urlopen=_urlopen,
    )

    assert audio == b"1234"
    assert len(calls) == 2
    assert "/getFile?" in calls[0][0]
    assert "/file/bot" in calls[1][0]


def test_readiness_reports_voice_pipeline_gates_without_enabling_network(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_VOICE_DOWNLOAD_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_VOICE_STT_ENABLED", "true")

    readiness = build_telegram_readiness(tmp_path)

    assert readiness["voice_boundary"]["mode"] == "fakeable_pipeline"
    assert readiness["voice_boundary"]["download_enabled"] is True
    assert readiness["voice_boundary"]["stt_enabled"] is True
    assert readiness["network_enabled"] is False


def test_readiness_reports_pending_voice_without_raw_identifiers(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat-999")
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 9,
        "message": {
            "message_id": 13,
            "chat": {"id": "voice-chat-999"},
            "voice": {
                "file_id": "voice-file-id",
                "file_unique_id": "unique-voice",
                "duration": 4,
                "mime_type": "audio/ogg",
                "file_size": 4096,
            },
        },
    })

    assert response.status_code == 200
    readiness = client.get("/api/plugins/telegram/status").json()
    encoded = json.dumps(readiness, ensure_ascii=False)

    assert readiness["history_counts"]["voice"] == 1
    assert readiness["history_counts"]["pending_stt"] == 1
    assert readiness["voice_boundary"]["pending_stt_count"] == 1
    assert "voice-file-id" not in encoded
    assert "unique-voice" not in encoded
    assert "voice-chat-999" not in encoded


def test_blocked_chat_voice_stays_redacted_and_skips_session_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "allowed-chat")
    created_sessions = []

    def _session_bridge(**kwargs):
        created_sessions.append(kwargs)
        return {"session_id": "should-not-happen"}

    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path)
    ctx.telegram_session_bridge = _session_bridge
    setup(ctx)
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 30,
        "message": {
            "message_id": 31,
            "chat": {"id": "blocked-voice-chat"},
            "from": {"id": "sender-voice"},
            "voice": {
                "file_id": "blocked-voice-file-id",
                "file_unique_id": "blocked-voice-unique",
                "duration": 5,
                "mime_type": "audio/ogg",
                "file_size": 1024,
            },
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"]["intake_status"] == "blocked_chat"
    assert payload["agent_bridge"]["ready_for_agent"] is False
    assert created_sessions == []
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(item.get("kind") == "blocked" and item.get("status") == "chat_not_allowed" for item in history)
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "blocked-voice-chat" not in persisted_text
    assert "sender-voice" not in persisted_text
    assert "blocked-voice-file-id" not in persisted_text
    assert "blocked-voice-unique" not in persisted_text

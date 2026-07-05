import importlib.util
import asyncio
import json
import logging
import time
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
    _handle_telegram_control_command,
    _telegram_control_command,
    build_agent_bridge_request,
    build_recent_telegram_attachment_context,
    build_telegram_readiness,
    download_telegram_file_bytes,
    download_telegram_voice_bytes,
    parse_telegram_update,
    run_telegram_universal_inbox_attachment_pipeline,
    run_telegram_polling_cycle,
    run_telegram_voice_pipeline,
    setup,
)
from src.image_tools_worker import ImageToolsWorkerResult
from src import agent_task_ledger
from src.plugin_capability_boundary import validate_plugin_capability_boundary
from src.server_project_registry import ServerProjectRegistry
from src.telegram_voice_pipeline import VoiceAgentTurn


ROOT = Path(__file__).resolve().parents[1]


def _json_contains_exact_value(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_json_contains_exact_value(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_json_contains_exact_value(item, needle) for item in value)
    return value == needle


@dataclass
class _PluginContext:
    app: FastAPI
    data_dir: Path
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("test.telegram"))
    registered_tools: list = field(default_factory=list)
    require_admin: Callable[[Any], None] = lambda _request: None
    telegram_agent_turn_handler: Callable[[dict[str, Any]], Any] | None = None
    memory_manager: Any = None
    memory_vector: Any = None

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
    assert "_telegram_rebind_local_session" in body
    assert "telegram_local_rebind" in source
    assert "DSGVO-Modus aktiv: Ich habe auf lokale Verarbeitung umgeschaltet." in body
    assert "telegram_dsgvo_provider_gate_failed" in body
    assert "resolve_workflow_skills" in body
    assert "workflow_skill_resolution=workflow_skill_resolution" in body
    assert "sensitivity_delegation" in body
    assert "local_worker_required" in body
    assert "chat_processor.build_context_preface" in body
    assert "use_rag=True" in body
    assert "use_web=False" in body
    assert "use_memory=False" in body
    assert "owner=owner" in body
    assert "owner_inventory(owner=owner)" in body
    assert "telegram rag import status" in body
    assert "only proves that redacted sources are currently present" in body
    assert "Do not claim that the automatic Nextcloud/background import workflow is active" in body
    assert "Do not mention unrelated builds, model downloads, or pending operations" in body


def test_voice_transcript_forces_local_only_in_dsgvo(monkeypatch):
    monkeypatch.setattr("plugins.telegram.plugin._dsgvo_mode_active", lambda: True)
    turn = VoiceAgentTurn(
        ready_for_agent=True,
        prompt="[Telegram voice transcript]\nBitte fasse das kurz zusammen.",
        status="agent_ready",
        reason="transcript_ready",
    )

    bridge = build_agent_bridge_request(
        {
            "kind": "voice",
            "chat_id": "voice-chat-999",
            "chat_handle": "voice_handle",
            "message_id": 42,
            "intake_status": "ready",
        },
        raw_chat_id="voice-chat-999",
        voice_agent_turn=turn,
    )

    assert bridge["dsgvo_mode"] is True
    assert bridge["note"] == "voice_transcribed"
    assert bridge["local_only_required"] is True
    assert bridge["security_mode"] == "secure"
    assert bridge["desired_session_scope"] == "secure"
    assert bridge["session_scope"] == "secure"
    assert bridge["telegram_voice_dsgvo_exempt"] is False
    assert bridge["sensitivity_delegation"]["mode"] == "local_raw_worker"
    assert bridge["sensitivity_delegation"]["local_worker_required"] is True


def test_telegram_model_spec_prefers_dedicated_setting(monkeypatch):
    from src.telegram_model_settings import resolve_telegram_model_spec

    def fake_get_setting(key, default=""):
        values = {
            "telegram_model_spec": "gemma4:e4b@Local Ollama",
            "default_model": "deepseek-v4-flash",
        }
        return values.get(key, default)

    assert resolve_telegram_model_spec(env={}, get_setting=fake_get_setting) == "gemma4:e4b@Local Ollama"


def test_telegram_model_spec_env_still_overrides_setting(monkeypatch):
    from src.telegram_model_settings import resolve_telegram_model_spec

    assert (
        resolve_telegram_model_spec(
            env={"TELEGRAM_MODEL_SPEC": "gemma3:4b@Local Ollama"},
            get_setting=lambda key, default="": "gemma4:e4b@Local Ollama",
        )
        == "gemma3:4b@Local Ollama"
    )


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
        ("post", "/api/plugins/telegram/document-reply", {"chat_id": "fake", "artifact_ref": "data/reports/autonomous_coding_agent/x.png"}),
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
    assert bridge["desired_session_scope"] == "normal"
    assert bridge["session_scope"] == "normal"


def test_telegram_store_adds_redacted_runtime_events(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    store = TelegramInboxStore(tmp_path)
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

    stored = store.append_inbound(message)["message"]
    event = store.append_event(
        kind="control_command",
        status="dsgvo_enabled",
        chat_id="123",
        update_id=7,
        message_id=11,
        command="dsgvo_toggle",
    )
    outbound = store.append_outbound("123", "Antwort", source_message_id=11, delivery_status="sent")

    for item in (stored, event, outbound):
        runtime_event = item["runtime_event"]
        assert runtime_event["schema"] == "odysseus.runtime_event.v1"
        assert runtime_event["surface"] == "telegram"
        assert runtime_event["correlation_id"].startswith("telegram:")
        assert runtime_event["raw_content_visible"] is False
        assert not _json_contains_exact_value(runtime_event, "123")
        assert not _json_contains_exact_value(runtime_event, "Hallo Odysseus")
        assert not _json_contains_exact_value(runtime_event, "Antwort")
    assert event["runtime_event"]["event_type"] == "control_command"
    assert outbound["runtime_event"]["event_type"] == "reply_delivery"


def test_telegram_workflow_context_normalizes_memory_status():
    from plugins.telegram.parsing import build_telegram_workflow_context

    context = build_telegram_workflow_context(
        {"kind": "text", "text": "analysiere das"},
        recent_attachment_context={
            "present": True,
            "family": "document",
            "suffix": ".pdf",
            "universal_inbox_status": "partial",
            "memory_write_intent_status": "pending_review",
        },
        dsgvo_mode=True,
    )

    assert context["dsgvo_mode"] == "on"
    assert context["security_mode"] == "secure"
    assert context["recent_attachment"]["memory_write_intent_status"] == "review"


def test_telegram_workflow_context_detects_coding_agent_tasks():
    from plugins.telegram.parsing import build_telegram_workflow_context

    context = build_telegram_workflow_context(
        {"kind": "text", "text": "Baue im Projekt demo ein Feature und teste es"},
        recent_attachment_context={},
    )

    assert context["intent"] == "coding-agent-task"
    assert context["message_kind"] == "text"


def test_agent_bridge_includes_redacted_long_running_task_intent(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    message = parse_telegram_update({
        "update_id": 9,
        "message": {
            "message_id": 13,
            "chat": {"id": 123},
            "text": "Analysiere https://www.asv-bw.de/hilfe?private=1 vollstaendig und fasse alles im Gedaechtnis zusammen.",
        },
    })

    bridge = build_agent_bridge_request(message)
    task_intent = bridge["task_intent"]
    encoded = json.dumps(task_intent, ensure_ascii=False, sort_keys=True).lower()
    assert bridge["long_running_task"] is True
    assert task_intent["task_type"] == "website_research_to_memory"
    assert task_intent["target_ref"] == "https://www.asv-bw.de/"
    assert "live_web_target_approval" in task_intent["gates_required"]
    assert "memory_write_policy" in task_intent["gates_required"]
    assert "task erkannt" in bridge["task_status_message"].lower()
    assert "private=1" not in encoded
    assert "vollstaendig" not in encoded


def test_task_control_commands_are_detected():
    assert _telegram_control_command({"kind": "text", "text": "/task"}) == "agent_task_status"
    assert _telegram_control_command({"kind": "text", "text": "/task pause"}) == "agent_task_pause"
    assert _telegram_control_command({"kind": "text", "text": "/task weiter"}) == "agent_task_resume"
    assert _telegram_control_command({"kind": "text", "text": "/task cancel"}) == "agent_task_cancel"


def test_task_control_status_and_pause_use_redacted_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setattr(agent_task_ledger, "AGENT_TASK_LEDGER_DIR", str(tmp_path / "task-ledger"))
    import plugins.telegram.plugin as telegram_plugin

    monkeypatch.setattr(telegram_plugin, "AGENT_TASK_LEDGER_DIR", str(tmp_path / "task-ledger"), raising=False)
    agent_task_ledger.record_task_event(
        task_id="tg_task_abc",
        task_type="website_research_to_memory",
        status="running",
        correlation_id="tg_task_abc",
        target_ref="https://www.asv-bw.de/",
        progress_percent=42,
        gates_waiting=("memory_write_policy",),
    )
    message = parse_telegram_update({
        "update_id": 10,
        "message": {
            "message_id": 14,
            "chat": {"id": 123},
            "text": "/task status",
        },
    })
    replies: list[tuple[str, str, int | None]] = []

    result = _handle_telegram_control_command(
        "agent_task_status",
        message=message,
        raw_chat_id="123",
        sessions=TelegramSessionBridgeStore(tmp_path),
        session_creator=None,
        reply_handler=lambda chat_id, text, reply_to: replies.append((chat_id, text, reply_to)) or {"ok": True},
    )
    assert result is not None
    assert result["status"] == "agent_task_status"
    assert result["agent_task"]["raw_content_visible"] is False
    assert "tg_task_abc" in result["reply_text"]
    assert "42%" in result["reply_text"]

    pause = _handle_telegram_control_command(
        "agent_task_pause",
        message=message,
        raw_chat_id="123",
        sessions=TelegramSessionBridgeStore(tmp_path),
        session_creator=None,
    )
    assert pause is not None
    assert pause["status"] == "pause_requested"
    assert pause["agent_task"]["status"] == "pause_requested"
    assert "123" not in json.dumps(pause["agent_task"], sort_keys=True)


def test_task_control_events_are_filterable_for_coding_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setattr(agent_task_ledger, "AGENT_TASK_LEDGER_DIR", str(tmp_path / "task-ledger"))
    agent_task_ledger.record_task_event(
        task_id="coding_task_abc",
        task_type="coding_agent_task",
        status="running",
        surface="workstation",
        target_ref="repo:demo",
        progress_percent=20,
    )
    agent_task_ledger.record_task_event(
        task_id="research_task",
        task_type="website_research",
        status="pause_requested",
        surface="telegram",
        progress_percent=20,
    )
    agent_task_ledger.record_task_event(
        task_id="coding_task_abc",
        task_type="coding_agent_task",
        status="pause_requested",
        surface="telegram",
        target_ref="repo:demo",
        progress_percent=20,
    )

    events = agent_task_ledger.read_task_control_events()

    assert events["count"] == 1
    assert events["records"][0]["task_id"] == "coding_task_abc"
    assert events["records"][0]["status"] == "pause_requested"
    assert events["raw_content_visible"] is False
    assert "123" not in json.dumps(events, sort_keys=True)


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


def test_webhook_keeps_typing_indicator_until_agent_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_TYPING_KEEPALIVE_SECONDS", "0.05")
    typing_calls: list[tuple[str, str]] = []
    replies: list[tuple[str, str]] = []

    def _session_bridge(**_kwargs):
        return {"session_id": "sess-typing"}

    async def _agent_turn(bridge):
        await asyncio.sleep(0.13)
        return {"status": "accepted", "reply_text": "Antwort nach Arbeit"}

    def _typing(chat_id, action="typing"):
        typing_calls.append((chat_id, action))
        return {"ok": True, "action": action, "token_value_visible": False}

    def _send(chat_id, text):
        replies.append((chat_id, text))
        return {"ok": True, "telegram_message_id": 90, "token_value_visible": False}

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_chat_action", _typing)
    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", _send)
    app = FastAPI()
    app.state.telegram_session_bridge = _session_bridge
    app.state.telegram_agent_turn_handler = _agent_turn
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 47,
        "message": {
            "message_id": 58,
            "chat": {"id": 123},
            "from": {"id": 1, "first_name": "User"},
            "text": "Bitte arbeite kurz",
        },
    })

    assert response.status_code == 200
    assert len(typing_calls) >= 2
    assert all(call == ("123", "typing") for call in typing_calls)
    assert replies == [("123", "Antwort nach Arbeit")]
    stopped_count = len(typing_calls)
    time.sleep(0.12)
    assert len(typing_calls) == stopped_count


def test_webhook_capability_question_uses_diagnostics_not_model_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    turns: list[dict[str, Any]] = []
    typing_calls: list[tuple[str, str]] = []
    replies: list[tuple[str, str]] = []

    def fake_diagnostics():
        return {
            "status": "success",
            "snapshot": {
                "id": "tool-capabilities-telegram",
                "commit": "cap123",
                "builtin_tool_count": 91,
                "index_status": {"status": "ok"},
                "domains": {"filesystem_code": 9, "agent_development": 7},
            },
            "memory_records": {"count": 10},
            "raptorgraph": {"event_present": True},
            "raw_content_visible": False,
        }

    monkeypatch.setattr("src.tool_capability_maintenance.read_tool_capability_diagnostics", fake_diagnostics)
    monkeypatch.setattr(
        "plugins.telegram.plugin.send_telegram_chat_action",
        lambda chat_id, action="typing": typing_calls.append((chat_id, action)) or {"ok": True},
    )
    monkeypatch.setattr(
        "plugins.telegram.plugin.send_telegram_text",
        lambda chat_id, text: replies.append((chat_id, text)) or {"ok": True, "telegram_message_id": 93},
    )
    app = FastAPI()
    app.state.telegram_session_bridge = lambda **_kwargs: {"session_id": "sess-capability"}
    app.state.telegram_agent_turn_handler = lambda bridge: turns.append(bridge) or {
        "status": "accepted",
        "reply_text": "Falsch: keine Tools vorhanden",
    }
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 48,
        "message": {
            "message_id": 59,
            "chat": {"id": 123},
            "from": {"id": 1, "first_name": "User"},
            "text": "Was fehlt dir als autonomous coding assistant fuer Sandbox, Terminal und Nextcloud Write?",
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert turns == []
    assert typing_calls == []
    assert payload["agent_turn"]["source"] == "tool_capability_diagnostics"
    assert "tool-capabilities-telegram" in replies[0][1]
    assert "sandbox-bound Worker/Runner" in replies[0][1]
    assert "copy-only Universal-Inbox-Transfer" in replies[0][1]
    assert "nichts davon ist grunds" in replies[0][1]
    assert "keine Tools vorhanden" not in replies[0][1]


def test_webhook_allowed_text_reaches_coding_agent_task_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    turns = []

    def _session_bridge(**_kwargs):
        return {"session_id": "sess-coding-task"}

    def _agent_turn(bridge):
        turns.append(bridge)
        return {"status": "accepted", "reply_text": "Coding task accepted"}

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", lambda chat_id, text: {
        "ok": True,
        "telegram_message_id": 92,
        "token_value_visible": False,
    })
    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path, telegram_agent_turn_handler=_agent_turn)
    ctx.telegram_session_bridge = _session_bridge
    setup(ctx)
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 46,
        "message": {
            "message_id": 57,
            "chat": {"id": 123},
            "from": {"id": 1, "first_name": "User"},
            "text": "Repo demo bitte implementiere feature ping und teste es",
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert turns
    assert turns[0]["ready_for_agent"] is True
    assert turns[0]["task_intent"]["task_type"] == "coding_agent_task"
    assert turns[0]["task_intent"]["target_ref"] == "repo:demo"
    assert turns[0]["task_intent"]["gates_required"] == (
        "coding_task_scope_review",
        "sandbox_execution_policy",
    )
    assert payload["agent_bridge"]["ready_for_agent"] is True
    assert payload["agent_bridge"]["task_intent"]["task_type"] == "coding_agent_task"
    assert payload["agent_turn"]["reply_text_value_visible"] is False
    assert "Coding task accepted" not in json.dumps(payload["agent_turn"], ensure_ascii=False)


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
    assert second["mapping"]["normal_session_id"] == "session-a"
    assert second["mapping"]["secure_session_id"] == ""
    assert second["mapping"]["last_selected_scope"] == "normal"


def test_session_bridge_keeps_normal_and_secure_slots_separate(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    created = []
    raw_chat_id = "chat-raw-123"

    def _creator(**kwargs):
        created.append(kwargs)
        return {"session_id": f"session-{kwargs['session_scope']}-{len(created)}"}

    normal = store.bind_chat(
        chat_id=raw_chat_id,
        session_alias=f"telegram:{raw_chat_id}",
        recommended_session_name="Telegram nina",
        scope="normal",
        creator=_creator,
    )
    secure = store.bind_chat(
        chat_id=raw_chat_id,
        session_alias=f"telegram:{raw_chat_id}",
        recommended_session_name="Telegram nina",
        scope="secure",
        creator=_creator,
    )
    normal_again = store.bind_chat(
        chat_id=raw_chat_id,
        session_alias=f"telegram:{raw_chat_id}",
        recommended_session_name="Telegram nina",
        scope="normal",
        creator=_creator,
    )

    assert normal["session_id"] == "session-normal-1"
    assert secure["session_id"] == "session-secure-2"
    assert normal_again["session_id"] == "session-normal-1"
    assert len(created) == 2
    assert created[0]["session_scope"] == "normal"
    assert created[0]["local_only_required"] is False
    assert created[1]["session_scope"] == "secure"
    assert created[1]["local_only_required"] is True
    mapping = store.get(raw_chat_id)
    assert mapping is not None
    assert mapping["normal_session_id"] == "session-normal-1"
    assert mapping["secure_session_id"] == "session-secure-2"
    assert mapping["session_id"] == "session-normal-1"
    assert mapping["last_selected_scope"] == "normal"
    persisted = json.loads((tmp_path / "telegram_session_bridge.json").read_text(encoding="utf-8"))
    persisted_text = json.dumps(persisted, ensure_ascii=False)
    assert raw_chat_id not in persisted_text
    assert '"chat_id"' not in persisted_text


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
    assert mapping["last_selected_scope"] == "normal"
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(item.get("kind") == "control_command" and item.get("status") == "new_chat_bound" for item in history)


def test_polling_cycle_keeps_typing_indicator_until_agent_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_TYPING_KEEPALIVE_SECONDS", "0.05")
    typing_calls: list[tuple[str, str]] = []
    replies: list[tuple[str, str, int | None]] = []

    def _typing(chat_id, action="typing"):
        typing_calls.append((chat_id, action))
        return {"ok": True, "action": action, "token_value_visible": False}

    def _agent_turn(_bridge):
        time.sleep(0.13)
        return {"status": "accepted", "reply_text": "Antwort nach Polling-Arbeit"}

    def _reply(chat_id, text, source_message_id=None):
        time.sleep(0.08)
        replies.append((chat_id, text, source_message_id))
        return {"ok": True, "telegram_message_id": 91}

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_chat_action", _typing)

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 18,
            "message": {
                "message_id": 180,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "Bitte arbeite per polling",
            },
        }],
        session_creator=lambda **_kwargs: {"session_id": "polling-typing"},
        agent_turn_handler=_agent_turn,
        reply_handler=_reply,
    )

    assert result["ok"] is True
    assert result["agent_turns"] == 1
    assert result["replies"] == 1
    assert len(typing_calls) >= 2
    assert all(call == ("123", "typing") for call in typing_calls)
    assert replies == [("123", "Antwort nach Polling-Arbeit", 180)]
    stopped_count = len(typing_calls)
    time.sleep(0.12)
    assert len(typing_calls) == stopped_count


def test_polling_cycle_capability_question_uses_diagnostics_not_model_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    turns: list[dict[str, Any]] = []
    typing_calls: list[tuple[str, str]] = []
    replies: list[tuple[str, str, int | None]] = []

    def fake_diagnostics():
        return {
            "status": "success",
            "snapshot": {
                "id": "tool-capabilities-polling",
                "commit": "cap456",
                "builtin_tool_count": 91,
                "index_status": {"status": "ok"},
                "domains": {"filesystem_code": 9, "agent_development": 7},
            },
            "memory_records": {"count": 10},
            "raptorgraph": {"event_present": True},
            "raw_content_visible": False,
        }

    monkeypatch.setattr("src.tool_capability_maintenance.read_tool_capability_diagnostics", fake_diagnostics)
    monkeypatch.setattr(
        "plugins.telegram.plugin.send_telegram_chat_action",
        lambda chat_id, action="typing": typing_calls.append((chat_id, action)) or {"ok": True},
    )

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 19,
            "message": {
                "message_id": 190,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "Was fehlt dir noch fuer Sandbox Terminal Coding und Nextcloud Write?",
            },
        }],
        session_creator=lambda **_kwargs: {"session_id": "polling-capability"},
        agent_turn_handler=lambda bridge: turns.append(bridge) or {
            "status": "accepted",
            "reply_text": "Falsch: keine Tools vorhanden",
        },
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert result["ok"] is True
    assert result["agent_turns"] == 1
    assert result["replies"] == 1
    assert turns == []
    assert typing_calls == []
    assert replies[0][0] == "123"
    assert replies[0][2] == 190
    assert "tool-capabilities-polling" in replies[0][1]
    assert "sandbox-bound Worker/Runner" in replies[0][1]
    assert "copy-only Universal-Inbox-Transfer" in replies[0][1]
    assert "keine Tools vorhanden" not in replies[0][1]


def test_telegram_control_command_detects_dsgvo_aliases():
    assert _telegram_control_command({"kind": "text", "text": "/dsgvo on"}) == "dsgvo_enable"
    assert _telegram_control_command({"kind": "text", "text": "/privacy aus"}) == "dsgvo_disable"
    assert _telegram_control_command({"kind": "text", "text": "/gdpr"}) == "dsgvo_toggle"
    assert _telegram_control_command({"kind": "text", "text": "/dsgvo status"}) == "dsgvo_status"
    assert _telegram_control_command({"kind": "text", "text": "/datenschutz maybe"}) == "dsgvo_help"


def test_telegram_control_command_detects_universal_inbox_status():
    assert _telegram_control_command({"kind": "text", "text": "/inbox"}) == "universal_inbox_status"
    assert _telegram_control_command({"kind": "text", "text": "/universal_inbox"}) == "universal_inbox_status"
    assert _telegram_control_command({"kind": "text", "text": "/universalinbox status"}) == "universal_inbox_status"


def test_telegram_control_command_detects_calendar_commands():
    assert _telegram_control_command({"kind": "text", "text": "/calendar"}) == "calendar_readiness"
    assert _telegram_control_command({"kind": "text", "text": "/agenda"}) == "calendar_agenda"
    assert _telegram_control_command({"kind": "text", "text": "/reminders"}) == "calendar_reminders_status"
    assert _telegram_control_command({"kind": "text", "text": "/remind 2026-07-04T09:00 Test"}) == "calendar_reminder_create"
    assert _telegram_control_command({"kind": "text", "text": "/remind update abc123 09:00 Test"}) == "calendar_reminder_update"
    assert _telegram_control_command({"kind": "text", "text": "/todo 09:00 mo-fr"}) == "calendar_todo_digest_create"


def _bind_calendar_temp_db(monkeypatch):
    import core.database as cdb
    from tests.helpers.sqlite_db import make_temp_sqlite

    ts, _engine, _tmpdb = make_temp_sqlite(cdb.Base.metadata)
    monkeypatch.setattr(cdb, "SessionLocal", ts)
    return cdb, ts


def test_polling_cycle_calendar_status_replies_without_agent_turn(tmp_path, monkeypatch):
    cdb, ts = _bind_calendar_temp_db(monkeypatch)
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    db = ts()
    try:
        db.add(cdb.CalendarCal(id="cal-alice", owner="alice", name="Alice"))
        db.commit()
    finally:
        db.close()
    replies = []
    turns = []

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 101,
            "message": {
                "message_id": 1001,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "/calendar",
            },
        }],
        memory_owner="alice",
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": "nope"},
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert result["ok"] is True
    assert result["control_commands"] == 1
    assert result["agent_turns"] == 0
    assert turns == []
    assert replies[0][0] == "123"
    assert replies[0][2] == 1001
    assert "Kalender-Status" in replies[0][1]
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(item.get("kind") == "control_command" and item.get("status") == "calendar_ready" for item in history)


def test_polling_cycle_calendar_reminder_create_and_update(tmp_path, monkeypatch):
    cdb, ts = _bind_calendar_temp_db(monkeypatch)
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    replies = []

    first = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 102,
            "message": {
                "message_id": 1002,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "/remind 2026-07-04T09:00 OctoGate pruefen",
            },
        }],
        memory_owner="alice",
        agent_turn_handler=lambda _bridge: {"status": "accepted", "reply_text": "nope"},
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    db = ts()
    try:
        note = db.query(cdb.Note).filter(cdb.Note.owner == "alice").one()
        note_id = note.id[:8]
    finally:
        db.close()

    second = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 103,
            "message": {
                "message_id": 1003,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": f"/remind update {note_id} 2026-07-05T08:30 OctoGate final pruefen",
            },
        }],
        memory_owner="alice",
        agent_turn_handler=lambda _bridge: {"status": "accepted", "reply_text": "nope"},
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert first["control_commands"] == 1
    assert second["control_commands"] == 1
    assert first["agent_turns"] == 0
    assert second["agent_turns"] == 0
    db = ts()
    try:
        notes = db.query(cdb.Note).filter(cdb.Note.owner == "alice").all()
        assert len(notes) == 1
        assert notes[0].title == "OctoGate final pruefen"
        assert "2026-07-05" in str(notes[0].due_date)
    finally:
        db.close()
    assert any("Erinnerung erstellt" in reply[1] for reply in replies)
    assert any("Erinnerung aktualisiert" in reply[1] for reply in replies)


def test_polling_cycle_calendar_todo_digest_creates_single_task(tmp_path, monkeypatch):
    cdb, ts = _bind_calendar_temp_db(monkeypatch)
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    replies = []

    first = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 104,
            "message": {
                "message_id": 1004,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "/todo 09:00 mo-fr",
            },
        }],
        memory_owner="alice",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )
    second = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 105,
            "message": {
                "message_id": 1005,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "/todo 09:00 mo-fr",
            },
        }],
        memory_owner="alice",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert first["control_commands"] == 1
    assert second["control_commands"] == 1
    db = ts()
    try:
        tasks = db.query(cdb.ScheduledTask).filter(cdb.ScheduledTask.owner == "alice").all()
        assert len(tasks) == 1
        assert tasks[0].action == "todo_digest"
        assert tasks[0].output_target == "telegram"
        assert tasks[0].cron_expression == "0 9 * * 1,2,3,4,5"
    finally:
        db.close()
    assert any("Todo-Digest erstellt" in reply[1] for reply in replies)
    assert any("Todo-Digest aktualisiert" in reply[1] for reply in replies)


def test_polling_cycle_universal_inbox_command_replies_without_agent_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "private-file-name.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setenv("UNIVERSAL_INBOX_PATH", str(inbox))
    replies = []
    turns = []

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 84,
            "message": {
                "message_id": 93,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "/inbox",
            },
        }],
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": "nope"},
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert result["ok"] is True
    assert result["control_commands"] == 1
    assert result["agent_turns"] == 0
    assert result["replies"] == 1
    assert turns == []
    assert replies[0][0] == "123"
    assert replies[0][2] == 93
    assert "Universal Inbox" in replies[0][1]
    assert "private-file-name" not in replies[0][1]
    assert str(inbox) not in replies[0][1]
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(
        item.get("kind") == "control_command"
        and item.get("status") in {"universal_inbox_go", "universal_inbox_partial"}
        for item in history
    )


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
    persisted_payload = json.loads(persisted)
    assert not _json_contains_exact_value(persisted_payload, "123")
    assert '"chat_id"' not in persisted
    pin_state = (tmp_path / "telegram_privacy_pin_state.json").read_text(encoding="utf-8")
    pin_state_payload = json.loads(pin_state)
    assert not _json_contains_exact_value(pin_state_payload, "123")
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


def test_polling_cycle_dsgvo_text_uses_secure_session_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setattr("plugins.telegram.plugin._dsgvo_mode_active", lambda: True)
    created = []
    turns = []

    def _creator(**kwargs):
        created.append(kwargs)
        return {"session_id": f"session-{kwargs['session_scope']}"}

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 10,
            "message": {
                "message_id": 91,
                "chat": {"id": 123},
                "from": {"id": 1, "first_name": "Nina"},
                "text": "Bitte analysiere diese Notiz.",
            },
        }],
        session_creator=_creator,
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": ""},
    )

    assert result["ok"] is True
    assert result["agent_turns"] == 1
    assert created == [{
        "chat_id": "123",
        "session_alias": turns[0]["session_alias"],
        "recommended_session_name": "Telegram Nina",
        "session_scope": "secure",
        "local_only_required": True,
    }]
    assert turns[0]["session_id"] == "session-secure"
    assert turns[0]["desired_session_scope"] == "secure"
    assert turns[0]["session_scope"] == "secure"
    mapping = TelegramSessionBridgeStore(tmp_path).get("123")
    assert mapping is not None
    assert mapping["normal_session_id"] == ""
    assert mapping["secure_session_id"] == "session-secure"
    assert mapping["session_id"] == "session-secure"
    persisted_text = (tmp_path / "telegram_session_bridge.json").read_text(encoding="utf-8")
    assert '"chat_id"' not in persisted_text
    assert "Bitte analysiere" not in persisted_text


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


def test_document_reply_route_sends_screenshot_artifact_as_photo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    artifact = tmp_path / "data" / "reports" / "autonomous_coding_agent" / "pong" / "screen.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"\x89PNG\r\n\x1a\n")
    calls = []

    def _photo(chat_id, file_path, *, filename, caption):
        calls.append((chat_id, Path(file_path), filename, caption))
        return {
            "ok": True,
            "telegram_message_id": 88,
            "delivery_mode": "photo",
            "formatting_mode": "photo_caption",
            "token_value_visible": False,
        }

    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_photo", _photo)
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/plugins/telegram/document-reply",
        json={
            "chat_id": "123",
            "artifact_ref": "data/reports/autonomous_coding_agent/pong/screen.png",
            "filename": "pong-screenshot.png",
            "caption": "Pong screenshot",
        },
    )

    assert response.status_code == 200
    assert response.json()["sent"]["delivery_mode"] == "photo"
    assert calls[0][0] == "123"
    assert calls[0][1] == artifact
    history = TelegramInboxStore(tmp_path).history(chat_id="123")
    assert history[0]["delivery_mode"] == "photo"
    assert history[0]["formatting_mode"] == "photo_caption"


def test_document_reply_route_rejects_non_artifact_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/plugins/telegram/document-reply",
        json={"chat_id": "123", "artifact_ref": "src/private.png"},
    )

    assert response.status_code == 400


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


def test_document_attachment_is_redacted_and_processed_by_universal_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "redacted-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "document-chat-999")
    monkeypatch.setenv("TELEGRAM_AGENT_REPLY_ENABLED", "true")
    sent = []
    monkeypatch.setattr("plugins.telegram.plugin.send_telegram_text", lambda chat_id, text: sent.append((chat_id, text)) or {
        "ok": True,
        "telegram_message_id": 901,
        "token_value_visible": False,
    })
    app = FastAPI()
    ctx = _PluginContext(app=app, data_dir=tmp_path)
    ctx.telegram_attachment_bytes_provider = lambda _message, max_bytes=None: b"Rechnung Test\nBetrag 12 Euro\n"
    setup(ctx)
    client = TestClient(app)

    response = client.post("/api/plugins/telegram/webhook", json={
        "update_id": 38,
        "message": {
            "message_id": 42,
            "chat": {"id": "document-chat-999"},
            "document": {
                "file_id": "document-file-id",
                "file_unique_id": "document-unique",
                "file_name": "private-rechnung-name.txt",
                "mime_type": "text/plain",
                "file_size": 28,
            },
        },
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"]["kind"] == "document"
    assert payload["message"]["media"]["file_handle"].startswith("document_file_")
    assert payload["message"]["media"]["file_unique_handle"].startswith("document_unique_")
    assert payload["message"]["universal_inbox_status"] == "processed"
    assert payload["universal_inbox_attachment"]["status"] == "processed"
    assert payload["universal_inbox_attachment"]["processable_count"] == 1
    assert payload["agent_bridge"]["ready_for_agent"] is False
    assert sent
    assert "Anhang" in sent[0][1]
    assert "file_id" not in payload["message"]["media"]
    assert "file_unique_id" not in payload["message"]["media"]
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "document-file-id" not in persisted_text
    assert "document-unique" not in persisted_text
    assert "private-rechnung-name" not in persisted_text
    assert "Betrag 12" not in persisted_text
    assert any((tmp_path / "universal_inbox_telegram").glob("*/telegram-attachment.txt"))


def test_polling_cycle_document_attachment_processes_without_prompt_or_agent_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "document-chat-999")
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    turns = []
    replies = []

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 39,
            "message": {
                "message_id": 43,
                "chat": {"id": "document-chat-999"},
                "document": {
                    "file_id": "poll-document-file-id",
                    "file_unique_id": "poll-document-unique",
                    "file_name": "rechnung-ohne-prompt.txt",
                    "mime_type": "text/plain",
                    "file_size": 15,
                },
            },
        }],
        attachment_bytes_provider=lambda _message, max_bytes=None: b"Nur Datei\n",
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": "nope"},
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert result["status"] == "poll_ok"
    assert result["processed"] == 1
    assert result["agent_turns"] == 0
    assert result["replies"] == 1
    assert turns == []
    assert replies and "Anhang" in replies[0][1]
    history = TelegramInboxStore(tmp_path).history(limit=20)
    assert any(
        item.get("kind") == "universal_inbox_attachment"
        and item.get("status") == "processed"
        and item.get("processable_count") == 1
        for item in history
    )
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "poll-document-file-id" not in persisted_text
    assert "rechnung-ohne-prompt" not in persisted_text
    assert "Nur Datei" not in persisted_text


def test_polling_cycle_next_text_turn_receives_recent_attachment_context_ephemerally(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "document-chat-999")
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    turns = []

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [
            {
                "update_id": 50,
                "message": {
                    "message_id": 60,
                    "chat": {"id": "document-chat-999"},
                    "document": {
                        "file_id": "context-document-file-id",
                        "file_unique_id": "context-document-unique",
                        "file_name": "notiz.txt",
                        "mime_type": "text/plain",
                        "file_size": 24,
                    },
                },
            },
            {
                "update_id": 51,
                "message": {
                    "message_id": 61,
                    "chat": {"id": "document-chat-999"},
                    "text": "Worum geht es in der Datei?",
                },
            },
        ],
        session_creator=lambda **_kwargs: {"session_id": "sess-attachment-context"},
        attachment_bytes_provider=lambda _message, max_bytes=None: b"Projekt Alpha braucht Review.",
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"status": "accepted", "reply_text": ""},
    )

    assert result["status"] == "poll_ok"
    assert result["processed"] == 2
    assert result["agent_turns"] == 1
    assert turns[0]["recent_attachment_context"]["present"] is True
    assert turns[0]["recent_attachment_context"]["raw_content_visible"] is True
    assert turns[0]["workflow_context"] == {
        "channel": "telegram",
        "message_kind": "text",
        "intent": "question-answer",
        "dsgvo_mode": "off",
        "security_mode": "normal",
        "recent_attachment": {
            "present": True,
            "family": "document",
            "suffix": ".txt",
            "universal_inbox_status": "go",
            "memory_write_intent_status": "ready",
        },
    }
    assert "Projekt Alpha braucht Review." in turns[0]["prompt"]
    assert "Worum geht es in der Datei?" in turns[0]["prompt"]
    assert turns[0]["persisted_prompt"] == "Worum geht es in der Datei?"
    assert "Projekt Alpha braucht Review." not in str(turns[0]["workflow_context"])
    assert "document-chat-999" not in str(turns[0]["workflow_context"])
    assert "context-document-file-id" not in str(turns[0]["workflow_context"])
    assert "notiz.txt" not in str(turns[0]["workflow_context"])
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "Projekt Alpha braucht Review." not in persisted_text
    assert "context-document-file-id" not in persisted_text
    assert "notiz.txt" not in persisted_text


def test_agent_bridge_workflow_context_classifies_export_without_raw_prompt(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_DSGVO_MODE", raising=False)
    bridge = build_agent_bridge_request(
        {
            "kind": "text",
            "chat_id": "sensitive-chat-id",
            "message_id": 77,
            "text": "Mach daraus bitte ein PDF.",
        },
        raw_chat_id="sensitive-chat-id",
        recent_attachment_context={
            "present": True,
            "context": "Private Rechnung mit Betrag 123",
            "family": "document",
            "suffix": ".docx",
            "universal_inbox_status": "go",
            "memory_write_intent_status": "review",
            "source_message_id": 76,
        },
    )

    assert bridge["workflow_context"]["intent"] == "export"
    assert bridge["workflow_context"]["recent_attachment"] == {
        "present": True,
        "family": "document",
        "suffix": ".docx",
        "universal_inbox_status": "go",
        "memory_write_intent_status": "review",
    }
    assert "Mach daraus" not in str(bridge["workflow_context"])
    assert "Private Rechnung" not in str(bridge["workflow_context"])
    assert "sensitive-chat-id" not in str(bridge["workflow_context"])


def test_recent_pdf_attachment_context_uses_separate_extract_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ATTACHMENT_CONTEXT_MAX_CHARS", "512")
    monkeypatch.setenv("TELEGRAM_ATTACHMENT_CONTEXT_MAX_EXTRACT_BYTES", "1048576")
    monkeypatch.setattr(
        "src.personal_docs.extract_pdf_text",
        lambda _path: "Projekt Alpha PDF Inhalt " * 80,
    )
    store = TelegramInboxStore(tmp_path)
    spool = tmp_path / "universal_inbox_telegram" / "spoolpdf"
    spool.mkdir(parents=True)
    (spool / "telegram-attachment.pdf").write_bytes(b"%PDF-1.4\n" + (b"x" * 200_000))
    store.append_event(
        kind="universal_inbox_attachment",
        status="processed",
        chat_id="pdf-chat-1",
        message_id=123,
        universal_inbox_status="go",
        memory_write_intent_status="ready",
        attachment_family="document",
        attachment_suffix=".pdf",
        spool_key="spoolpdf",
        raw_content_visible=False,
        raw_identifiers_visible=False,
        filename_visible=False,
    )

    context = build_recent_telegram_attachment_context(
        data_dir=tmp_path,
        store=store,
        chat_id="pdf-chat-1",
    )

    assert context["raw_content_visible"] is True
    assert "Projekt Alpha PDF Inhalt" in context["context"]
    assert "size_limit_exceeded" not in context["context"]
    assert context["api_model_allowed"] is True
    assert context["local_only_required"] is False


def test_sensitive_recent_attachment_requires_local_only(tmp_path):
    store = TelegramInboxStore(tmp_path)
    spool = tmp_path / "universal_inbox_telegram" / "spoolsensitive"
    spool.mkdir(parents=True)
    (spool / "telegram-attachment.txt").write_text("IBAN DE00 0000 0000 0000 Rechnung privat", encoding="utf-8")
    store.append_event(
        kind="universal_inbox_attachment",
        status="processed",
        chat_id="sensitive-chat-1",
        message_id=124,
        universal_inbox_status="go",
        memory_write_intent_status="review",
        attachment_family="document",
        attachment_suffix=".txt",
        spool_key="spoolsensitive",
        raw_content_visible=False,
        raw_identifiers_visible=False,
        filename_visible=False,
    )

    context = build_recent_telegram_attachment_context(
        data_dir=tmp_path,
        store=store,
        chat_id="sensitive-chat-1",
    )
    bridge = build_agent_bridge_request(
        {"kind": "text", "chat_id": "sensitive-chat-1", "message_id": 125, "text": "Worum geht es?"},
        raw_chat_id="sensitive-chat-1",
        recent_attachment_context=context,
    )

    assert context["raw_content_visible"] is True
    assert context["api_model_allowed"] is False
    assert context["local_only_required"] is True
    assert bridge["local_only_required"] is True
    assert bridge["attachment_local_only_required"] is True
    assert bridge["security_mode"] == "secure"
    assert bridge["sensitivity_delegation"]["mode"] == "local_raw_worker"
    assert bridge["sensitivity_delegation"]["local_worker_required"] is True
    assert bridge["sensitivity_delegation"]["external_raw_allowed"] is False


def test_polling_cycle_followup_export_request_sends_recent_attachment_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "document-chat-999")
    replies = []
    documents = []
    turns = []

    first = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 52,
            "message": {
                "message_id": 62,
                "chat": {"id": "document-chat-999"},
                "document": {
                    "file_id": "export-document-file-id",
                    "file_unique_id": "export-document-unique",
                    "file_name": "source.md",
                    "mime_type": "text/markdown",
                    "file_size": 18,
                },
            },
        }],
        attachment_bytes_provider=lambda _message, max_bytes=None: b"# Export me\n",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert first["processed"] == 1

    second = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 53,
            "message": {
                "message_id": 63,
                "chat": {"id": "document-chat-999"},
                "text": "Mach daraus bitte ein PDF.",
            },
        }],
        session_creator=lambda **_kwargs: {"session_id": "sess-export"},
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"reply_text": "should not run"},
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
        document_reply_handler=lambda chat_id, file_path, filename, caption, source_message_id=None: documents.append(
            (chat_id, file_path, filename, caption, source_message_id)
        ) or {"ok": True, "delivery_mode": "document"},
    )

    assert second["processed"] == 1
    assert second["agent_turns"] == 0
    assert turns == []
    assert len(documents) == 1
    assert documents[0][0] == "document-chat-999"
    assert documents[0][2].endswith(".pdf")
    assert Path(documents[0][1]).read_bytes().startswith(b"%PDF-")
    assert "PDF-Datei geschickt" in documents[0][3]
    history = TelegramInboxStore(tmp_path).history(limit=40)
    event = next(item for item in history if item.get("kind") == "universal_inbox_export_plan")
    assert event["status"] == "exported"
    assert event["target_format"] == "pdf"
    assert event["required_tool"] == "builtin_text_pdf"
    assert any(item.get("kind") == "universal_inbox_export_delivery" for item in history)
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "export-document-file-id" not in persisted_text
    assert "source.md" not in persisted_text
    assert "Export me" not in persisted_text


def test_polling_cycle_project_intake_preview_for_mobile_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "project-chat-1")
    registry = ServerProjectRegistry()
    registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-29T10:00:00Z",
    )
    registry.save_json(tmp_path / "server_project_registry.json")
    replies = []
    turns = []

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 60,
            "message": {
                "message_id": 70,
                "chat": {"id": "project-chat-1"},
                "text": "#project:kundenportal-mvp TODO: Login als MVP Slice aufnehmen.",
            },
        }],
        session_creator=lambda **_kwargs: {"session_id": "should-not-run"},
        agent_turn_handler=lambda bridge: turns.append(bridge) or {"reply_text": "should not run"},
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert result["status"] == "poll_ok"
    assert result["processed"] == 1
    assert result["agent_turns"] == 0
    assert turns == []
    assert any("Project-Intake erkannt fuer kundenportal-mvp" in reply[1] for reply in replies)
    history = TelegramInboxStore(tmp_path).history(limit=30)
    event = next(item for item in history if item.get("kind") == "project_intake_review")
    assert event["status"] == "review"
    assert event["project_slug"] == "kundenportal-mvp"
    assert event["task_count"] == 1
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "project-chat-1" not in persisted_text
    assert '"chat_id":' not in persisted_text
    assert "TOKEN=" not in persisted_text


def test_project_commands_report_and_confirm_latest_intake_review(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "project-chat-1")
    registry = ServerProjectRegistry()
    registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-29T10:00:00Z",
    )
    registry.save_json(tmp_path / "server_project_registry.json")
    from src.project_intake import build_project_intake_preview

    proposal = build_project_intake_preview(
        registry=registry,
        text="#project:kundenportal-mvp TODO: Login als MVP Slice aufnehmen.",
        source_channel="telegram",
    ).to_dict()
    store = TelegramInboxStore(tmp_path)
    store.append_event(
        kind="project_intake_review",
        status="review",
        chat_id="project-chat-1",
        source_message_id=70,
        project_slug="kundenportal-mvp",
        task_count=1,
        decision_count=0,
        risk_count=0,
        roadmap_update_count=0,
        raw_content_visible=False,
        raw_identifiers_visible=False,
        project_intake_apply_performed=False,
        project_intake_proposal=proposal,
    )
    replies = []

    status = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 61,
            "message": {
                "message_id": 71,
                "chat": {"id": "project-chat-1"},
                "text": "/project status",
            },
        }],
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )
    assert status["control_commands"] == 1
    assert any("Offene Project-Intake-Review fuer kundenportal-mvp" in reply[1] for reply in replies)

    confirmed = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 62,
            "message": {
                "message_id": 72,
                "chat": {"id": "project-chat-1"},
                "text": "/project ok",
            },
        }],
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert confirmed["control_commands"] == 1
    history = TelegramInboxStore(tmp_path).history(limit=40)
    assert any(item.get("kind") == "project_intake_review" and item.get("status") == "confirmed" for item in history)
    assert any("Intake-Ledger uebernommen" in reply[1] for reply in replies)
    ledger_path = tmp_path / "server_projects" / "kundenportal-mvp" / ".odysseus" / "project_intake_ledger.json"
    assert ledger_path.is_file()
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["events"][0]["task_count"] == 1
    assert ledger["events"][0]["project_state_write_performed"] is False
    state_path = tmp_path / "server_projects" / "kundenportal-mvp" / ".odysseus" / "project_state.json"
    assert state_path.is_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["tasks"][0]["title"] == "Login als MVP Slice aufnehmen."
    assert any("Integriert: 1 neue Tasks" in reply[1] for reply in replies)
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "project-chat-1" not in persisted_text
    assert "project_intake_apply_performed" in persisted_text


def test_review_ok_confirms_latest_partial_universal_inbox_attachment(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "document-chat-999")
    replies = []

    first = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 40,
            "message": {
                "message_id": 44,
                "chat": {"id": "document-chat-999"},
                "document": {
                    "file_id": "partial-document-file-id",
                    "file_unique_id": "partial-document-unique",
                    "file_name": "scan.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 15,
                },
            },
        }],
        attachment_bytes_provider=lambda _message, max_bytes=None: b"%PDF-1.4 no text",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )
    assert first["processed"] == 1
    assert any("Review nötig" in reply[1] for reply in replies)

    second = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 41,
            "message": {
                "message_id": 45,
                "chat": {"id": "document-chat-999"},
                "text": "/review ok",
            },
        }],
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert second["control_commands"] == 1
    assert any("Nextcloud-Ablage ist vorbereitet" in reply[1] for reply in replies)
    history = TelegramInboxStore(tmp_path).history(limit=30)
    assert any(item.get("kind") == "universal_inbox_review" and item.get("status") == "confirmed" for item in history)
    assert any(
        item.get("kind") == "universal_inbox_nextcloud_transfer"
        and item.get("status") == "dry_run_ready"
        and item.get("nextcloud_transfer_status") == "dry_run_ready"
        and item.get("dry_run") is True
        and item.get("writes_performed") is False
        and item.get("review_approved") is True
        and item.get("target_path_visible") is False
        for item in history
    )
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "partial-document-file-id" not in persisted_text
    assert "scan.pdf" not in persisted_text
    assert "AI Inbox/Needs Review" not in persisted_text


def test_review_ok_blocks_nextcloud_live_copy_without_chat_credentials(tmp_path, monkeypatch):
    from src.nextcloud_webdav_client import NextcloudWebDAVClientError

    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "document-chat-999")
    monkeypatch.setenv("UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED", "true")
    monkeypatch.setenv("UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO", "true")
    monkeypatch.setattr(
        "src.nextcloud_webdav_client.build_nextcloud_webdav_client_from_env",
        lambda: (_ for _ in ()).throw(NextcloudWebDAVClientError("missing runtime config")),
    )
    replies = []

    first = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 40,
            "message": {
                "message_id": 44,
                "chat": {"id": "document-chat-999"},
                "document": {
                    "file_id": "partial-document-file-id",
                    "file_unique_id": "partial-document-unique",
                    "file_name": "scan.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 15,
                },
            },
        }],
        attachment_bytes_provider=lambda _message, max_bytes=None: b"%PDF-1.4 no text",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )
    assert first["processed"] == 1

    second = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 41,
            "message": {
                "message_id": 45,
                "chat": {"id": "document-chat-999"},
                "text": "/review ok",
            },
        }],
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert second["control_commands"] == 1
    reply_text = replies[-1][1]
    assert "serverseitige Nextcloud-Konfiguration" in reply_text
    assert "Bitte keine Zugangsdaten in Telegram senden" in reply_text
    assert "NEXTCLOUD_WEBDAV" not in reply_text
    history = TelegramInboxStore(tmp_path).history(limit=30)
    transfer = next(item for item in history if item.get("kind") == "universal_inbox_nextcloud_transfer")
    assert transfer["status"] == "blocked"
    assert transfer["reason"] == "nextcloud_server_config_missing"
    assert transfer["dry_run"] is True
    assert transfer["writes_performed"] is False
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "NEXTCLOUD_WEBDAV_APP_PASSWORD" not in persisted_text
    assert "partial-document-file-id" not in persisted_text
    assert "scan.pdf" not in persisted_text


def test_review_ok_executes_nextcloud_copy_only_with_explicit_live_gates(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "document-chat-999")
    monkeypatch.setenv("UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED", "true")
    monkeypatch.setenv("UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO", "true")
    replies = []

    class FakeNextcloudClient:
        def __init__(self):
            self.files = {}
            self.sidecars = {}
            self.closed = False

        def stat(self, relative_path):
            if relative_path not in self.files:
                return None
            return {"size_bytes": len(self.files[relative_path]), "etag": "fake-etag"}

        def put_file(self, source_path, relative_path):
            payload = source_path.read_bytes()
            self.files[relative_path] = payload
            return {"size_bytes": len(payload), "etag": "fake-upload"}

        def put_text(self, relative_path, text):
            self.sidecars[relative_path] = text
            return {"size_bytes": len(text.encode("utf-8")), "etag": "fake-sidecar"}

        def close(self):
            self.closed = True

    fake_client = FakeNextcloudClient()
    monkeypatch.setattr(
        "src.nextcloud_webdav_client.build_nextcloud_webdav_client_from_env",
        lambda: fake_client,
    )

    first = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 40,
            "message": {
                "message_id": 44,
                "chat": {"id": "document-chat-999"},
                "document": {
                    "file_id": "partial-document-file-id",
                    "file_unique_id": "partial-document-unique",
                    "file_name": "scan.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 15,
                },
            },
        }],
        attachment_bytes_provider=lambda _message, max_bytes=None: b"%PDF-1.4 no text",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )
    assert first["processed"] == 1

    second = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 41,
            "message": {
                "message_id": 45,
                "chat": {"id": "document-chat-999"},
                "text": "/review ok",
            },
        }],
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert second["control_commands"] == 1
    assert any("Nextcloud-Ablage wurde kopiert und verifiziert" in reply[1] for reply in replies)
    history = TelegramInboxStore(tmp_path).history(limit=30)
    transfer = next(item for item in history if item.get("kind") == "universal_inbox_nextcloud_transfer")
    assert transfer["status"] == "completed"
    assert transfer["dry_run"] is False
    assert transfer["writes_performed"] is True
    assert transfer["verified"] is True
    assert fake_client.files
    assert fake_client.sidecars
    assert fake_client.closed is True
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "partial-document-file-id" not in persisted_text
    assert "scan.pdf" not in persisted_text


def test_review_memory_ok_confirms_latest_memory_write_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "document-chat-999")
    replies = []

    class FakeMemoryManager:
        def __init__(self):
            self.saved = []

        def add_entry(self, text, source="user", category="fact", owner=None):
            return {
                "id": "mem-1",
                "text": text,
                "source": source,
                "category": category,
                "owner": owner,
            }

        def load_all(self):
            return []

        def save(self, memories):
            self.saved = list(memories)

    class FakeMemoryVector:
        healthy = True

        def __init__(self):
            self.added = []

        def add(self, memory_id, text):
            self.added.append((memory_id, text))

    memory_manager = FakeMemoryManager()
    memory_vector = FakeMemoryVector()

    first = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 52,
            "message": {
                "message_id": 62,
                "chat": {"id": "document-chat-999"},
                "document": {
                    "file_id": "memory-document-file-id",
                    "file_unique_id": "memory-document-unique",
                    "file_name": "reference.txt",
                    "mime_type": "text/plain",
                    "file_size": 15,
                },
            },
        }],
        attachment_bytes_provider=lambda _message, max_bytes=None: b"Memory candidate",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert first["processed"] == 1
    history = TelegramInboxStore(tmp_path).history(limit=20)
    attachment_event = next(item for item in history if item.get("kind") == "universal_inbox_attachment")
    assert attachment_event["memory_write_intent_status"] == "ready"
    assert attachment_event["queue_status"] == "completed"
    assert attachment_event["queue_concurrency"] == 1
    assert attachment_event["maintenance_model_ref"] == "gemma4:e4b"
    assert attachment_event["maintenance_provider"] == "local_ollama"
    assert attachment_event["maintenance_action"] == "stay_on_maintenance_model"
    assert attachment_event["memory_records_planned"] == 1
    assert attachment_event["raptorgraph_events_planned"] == 1
    assert any("Memory/Raptor-Intent: ready" in reply[1] for reply in replies)
    assert any("Automatischer Memory-Write blockiert" in reply[1] for reply in replies)

    second = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 53,
            "message": {
                "message_id": 63,
                "chat": {"id": "document-chat-999"},
                "text": "/review memory ok",
            },
        }],
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
        memory_manager=memory_manager,
        memory_vector=memory_vector,
        memory_owner="homebase",
    )

    assert second["control_commands"] == 1
    assert any("Langzeitgedaechtnis geschrieben" in reply[1] for reply in replies)
    history = TelegramInboxStore(tmp_path).history(limit=40)
    assert any(
        item.get("kind") == "universal_inbox_memory_review"
        and item.get("status") == "confirmed"
        and item.get("memory_write_intent_status") == "ready"
        for item in history
    )
    assert any(
        item.get("kind") == "universal_inbox_memory_write"
        and item.get("status") == "written"
        and item.get("memory_records_written") == 1
        and item.get("raptorgraph_events_written") == 1
        and item.get("writes_performed") is True
        for item in history
    )
    assert len(memory_manager.saved) == 1
    saved = memory_manager.saved[0]
    assert saved["source"] == "universal_inbox"
    assert saved["category"] == "document"
    assert saved["owner"] == "homebase"
    assert saved["metadata"]["classification"] == "private"
    assert saved["metadata"]["raw_content_stored"] is False
    assert "Universal Inbox memory:" in saved["text"]
    assert memory_vector.added == [("mem-1", saved["text"])]
    graph_log = tmp_path / "universal_inbox_raptorgraph" / "events.jsonl"
    assert graph_log.exists()
    graph_rows = [json.loads(line) for line in graph_log.read_text(encoding="utf-8").splitlines()]
    assert len(graph_rows) == 1
    assert graph_rows[0]["event"] == "universal_inbox_memory_write"
    assert graph_rows[0]["memory_record_ids"]
    assert graph_rows[0]["raw_content_visible"] is False
    assert graph_rows[0]["raw_content_stored"] is False
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "memory-document-file-id" not in persisted_text
    assert "reference.txt" not in persisted_text
    assert "Memory candidate" not in persisted_text


def test_review_memory_ok_reports_blocked_when_memory_writer_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "document-chat-999")
    replies = []

    run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 52,
            "message": {
                "message_id": 62,
                "chat": {"id": "document-chat-999"},
                "document": {
                    "file_id": "blocked-memory-document-file-id",
                    "file_unique_id": "blocked-memory-document-unique",
                    "file_name": "blocked-reference.txt",
                    "mime_type": "text/plain",
                    "file_size": 15,
                },
            },
        }],
        attachment_bytes_provider=lambda _message, max_bytes=None: b"Blocked memory candidate",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    second = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 53,
            "message": {
                "message_id": 63,
                "chat": {"id": "document-chat-999"},
                "text": "/review memory ok",
            },
        }],
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
    )

    assert second["control_commands"] == 1
    assert any("Memory-Write wurde blockiert" in reply[1] for reply in replies)
    history = TelegramInboxStore(tmp_path).history(limit=40)
    assert any(
        item.get("kind") == "universal_inbox_memory_write"
        and item.get("status") == "blocked"
        and item.get("memory_records_written") == 0
        and item.get("writes_performed") is False
        for item in history
    )
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "blocked-memory-document-file-id" not in persisted_text
    assert "blocked-reference.txt" not in persisted_text
    assert "Blocked memory candidate" not in persisted_text


def test_ready_memory_intent_auto_writes_without_manual_review(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "document-chat-999")
    replies = []

    class FakeMemoryManager:
        def __init__(self):
            self.saved = []

        def add_entry(self, text, source="user", category="fact", owner=None):
            return {
                "id": "mem-auto",
                "text": text,
                "source": source,
                "category": category,
                "owner": owner,
            }

        def load_all(self):
            return []

        def save(self, memories):
            self.saved = list(memories)

    class FakeMemoryVector:
        healthy = True

        def __init__(self):
            self.added = []

        def add(self, memory_id, text):
            self.added.append((memory_id, text))

    memory_manager = FakeMemoryManager()
    memory_vector = FakeMemoryVector()

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [{
            "update_id": 72,
            "message": {
                "message_id": 82,
                "chat": {"id": "document-chat-999"},
                "document": {
                    "file_id": "auto-memory-document-file-id",
                    "file_unique_id": "auto-memory-document-unique",
                    "file_name": "auto-reference.txt",
                    "mime_type": "text/plain",
                    "file_size": 15,
                },
            },
        }],
        attachment_bytes_provider=lambda _message, max_bytes=None: b"Auto memory candidate",
        reply_handler=lambda chat_id, text, source_message_id=None: replies.append((chat_id, text, source_message_id)) or {"ok": True},
        memory_manager=memory_manager,
        memory_vector=memory_vector,
        memory_owner="homebase",
    )

    assert result["processed"] == 1
    assert len(memory_manager.saved) == 1
    assert memory_manager.saved[0]["owner"] == "homebase"
    assert memory_vector.added == [("mem-auto", memory_manager.saved[0]["text"])]
    assert any("Redigierte Abstraktion automatisch ins Memory/RaptorGraph geschrieben" in reply[1] for reply in replies)
    history = TelegramInboxStore(tmp_path).history(limit=40)
    assert any(
        item.get("kind") == "universal_inbox_memory_auto_write"
        and item.get("status") == "written"
        and item.get("memory_records_written") == 1
        and item.get("raptorgraph_events_written") == 1
        and item.get("writes_performed") is True
        for item in history
    )
    assert not any(item.get("kind") == "universal_inbox_memory_review" for item in history)
    graph_log = tmp_path / "universal_inbox_raptorgraph" / "events.jsonl"
    assert graph_log.exists()
    persisted_text = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "auto-memory-document-file-id" not in persisted_text
    assert "auto-reference.txt" not in persisted_text
    assert "Auto memory candidate" not in persisted_text


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
    assert readiness["voice_boundary"]["stt_gate_names"] == ["TELEGRAM_VOICE_STT_ENABLED", "TELEGRAM_STT_ENABLED"]
    assert readiness["network_enabled"] is False


def test_readiness_accepts_legacy_telegram_stt_gate_alias(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_VOICE_STT_ENABLED", raising=False)
    monkeypatch.setenv("TELEGRAM_STT_ENABLED", "true")

    readiness = build_telegram_readiness(tmp_path)

    assert readiness["voice_boundary"]["mode"] == "fakeable_pipeline"
    assert readiness["voice_boundary"]["stt_enabled"] is True
    assert readiness["voice_boundary"]["stt_gate_names"] == ["TELEGRAM_VOICE_STT_ENABLED", "TELEGRAM_STT_ENABLED"]


def test_voice_pipeline_accepts_legacy_telegram_stt_gate_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "voice-chat")
    monkeypatch.setenv("TELEGRAM_VOICE_DOWNLOAD_ENABLED", "true")
    monkeypatch.delenv("TELEGRAM_VOICE_STT_ENABLED", raising=False)
    monkeypatch.setenv("TELEGRAM_STT_ENABLED", "true")
    message = parse_telegram_update({
        "update_id": 44,
        "message": {
            "message_id": 77,
            "chat": {"id": "voice-chat"},
            "voice": {
                "file_id": "voice-file-id",
                "file_unique_id": "voice-unique-id",
                "duration": 4,
                "mime_type": "audio/ogg",
                "file_size": 1024,
            },
        },
    })

    stored = TelegramInboxStore(tmp_path).append_inbound(message)["message"]

    turn, pipeline = run_telegram_voice_pipeline(
        stored,
        stt_provider=lambda _local_ref: "legacy alias transcript",
    )

    assert turn is not None
    assert turn.status == "agent_ready"
    assert turn.ready_for_agent is True
    assert pipeline["stt"]["status"] == "transcribed"
    assert pipeline["stt"]["transcript_value_visible"] is False


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

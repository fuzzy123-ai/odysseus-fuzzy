import asyncio
import json

import pytest

from plugins.telegram.stores import TelegramInboxStore
from plugins.telegram.webhook_service import (
    TelegramWebhookIntakeError,
    build_webhook_agent_turn_event_payload,
    build_webhook_attachment_event_payload,
    build_webhook_control_command_event_payload,
    build_webhook_control_command_summary,
    build_webhook_export_delivery_event_payload,
    build_webhook_export_plan_event_payload,
    build_webhook_export_plan_summary,
    build_webhook_project_intake_summary,
    build_webhook_response_payload,
    parse_and_store_webhook_update,
    run_webhook_attachment_branch,
    run_webhook_attachment_export_branch,
    run_webhook_agent_turn_branch,
    run_webhook_control_command_branch,
    run_webhook_media_pipelines,
    run_webhook_project_intake_branch,
)


def test_parse_and_store_webhook_update_appends_redacted_inbound(tmp_path):
    store = TelegramInboxStore(tmp_path)

    message, stored = parse_and_store_webhook_update(
        {"message": {"chat": {"id": "raw-chat-id"}}},
        store=store,
        parse_update=lambda _update: {
            "plugin": "telegram",
            "direction": "inbound",
            "kind": "text",
            "text": "hello",
            "chat_id": "raw-chat-id",
            "chat_allowed": True,
            "intake_status": "ready",
        },
    )

    assert message["chat_id"] == "raw-chat-id"
    assert stored["stored"] is True
    history = store.history(limit=10)
    assert history[0]["chat_handle"].startswith("chat_")
    assert "raw-chat-id" not in json.dumps(history)


def test_parse_and_store_webhook_update_records_invalid_update_without_raw_payload(tmp_path):
    store = TelegramInboxStore(tmp_path)

    with pytest.raises(TelegramWebhookIntakeError):
        parse_and_store_webhook_update(
            {"raw_secret": "do-not-persist", "message": {"chat": {"id": "raw-chat-id"}}},
            store=store,
            parse_update=lambda _update: (_ for _ in ()).throw(ValueError("telegram update has no message")),
        )

    persisted = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    assert "invalid_update" in persisted
    assert "telegram update has no message" in persisted
    assert "do-not-persist" not in persisted
    assert "raw-chat-id" not in persisted


def test_run_webhook_media_pipelines_uses_injected_voice_provider_and_image_gate():
    calls = {}

    def _run_voice(stored, stt_provider):
        calls["voice"] = (stored, stt_provider)
        return "turn", {"status": "voice_ok"}

    def _run_image(stored, enabled, image_bytes_provider, worker_client):
        calls["image"] = (stored, enabled, image_bytes_provider, worker_client)
        return {"status": "image_ok"}

    turn, voice, image = run_webhook_media_pipelines(
        message={"chat_id": "chat-1"},
        stored_message={"kind": "text", "text": "hello"},
        voice_stt_provider=lambda _ref: "ready transcript",
        voice_bytes_provider="unused-voice-bytes",
        image_bytes_provider="image-bytes",
        image_worker_client="worker",
        image_actions_enabled=True,
        build_live_voice_stt_provider=lambda _message: (_ for _ in ()).throw(AssertionError("live provider unused")),
        run_voice_pipeline=_run_voice,
        run_image_action=_run_image,
    )

    assert turn == "turn"
    assert voice == {"status": "voice_ok"}
    assert image == {"status": "image_ok"}
    assert calls["voice"][0]["text"] == "hello"
    assert callable(calls["voice"][1])
    assert calls["image"] == ({"kind": "text", "text": "hello"}, True, "image-bytes", "worker")


def test_run_webhook_media_pipelines_builds_live_voice_provider_when_no_injected_provider():
    built = []

    run_webhook_media_pipelines(
        message={"chat_id": "chat-1"},
        stored_message={"kind": "voice"},
        voice_stt_provider=None,
        voice_bytes_provider="voice-bytes",
        image_bytes_provider=None,
        image_worker_client=None,
        image_actions_enabled=False,
        build_live_voice_stt_provider=lambda message: built.append(message) or "live-provider",
        run_voice_pipeline=lambda _stored, stt_provider: ("turn", {"provider": stt_provider}),
        run_image_action=lambda *_args, **_kwargs: {"status": "disabled"},
    )

    assert built == [{"chat_id": "chat-1"}]


def test_build_webhook_response_payload_preserves_public_shape():
    payload = build_webhook_response_payload(
        stored={"stored": True, "message": {"kind": "text", "chat_handle": "chat_safe"}},
        agent_bridge={"ready_for_agent": True},
        voice_pipeline={"status": "skipped"},
        image_action={"status": "disabled"},
        universal_inbox_attachment=None,
        agent_turn={"status": "accepted"},
        reply={"ok": True},
        extra={"control_command": {"status": "handled"}},
    )

    assert payload == {
        "stored": True,
        "message": {"kind": "text", "chat_handle": "chat_safe"},
        "agent_bridge": {"ready_for_agent": True},
        "voice_pipeline": {"status": "skipped"},
        "image_action": {"status": "disabled"},
        "universal_inbox_attachment": None,
        "control_command": {"status": "handled"},
        "agent_turn": {"status": "accepted"},
        "reply": {"ok": True},
        "token_value_visible": False,
    }


def test_build_webhook_attachment_event_payload_is_redacted_and_typed():
    payload = build_webhook_attachment_event_payload(
        message={"chat_id": "raw-chat-id", "update_id": 12, "message_id": 34},
        inbox_attachment={
            "status": "processed",
            "universal_inbox_status": "processed",
            "memory_write_intent_status": "planned",
            "discovered_count": "2",
            "processable_count": "1",
            "queue_status": "queued",
            "queue_concurrency": "3",
            "maintenance_review_required": True,
            "review_reason_count": "4",
            "no_go_reason_count": "0",
            "extraction_warning_codes": ["pdf_ocr_required"],
            "memory_records_planned": "5",
            "raptorgraph_events_planned": "6",
        },
        attachment_family="document",
        attachment_suffix=".pdf",
        spool_key="spool_safe",
    )

    assert payload["kind"] == "universal_inbox_attachment"
    assert payload["status"] == "processed"
    assert payload["chat_id"] == "raw-chat-id"
    assert payload["update_id"] == 12
    assert payload["message_id"] == 34
    assert payload["discovered_count"] == 2
    assert payload["processable_count"] == 1
    assert payload["queue_concurrency"] == 3
    assert payload["review_reason_count"] == 4
    assert payload["extraction_warning_codes"] == ("pdf_ocr_required",)
    assert payload["memory_records_planned"] == 5
    assert payload["raptorgraph_events_planned"] == 6
    assert payload["raw_content_visible"] is False
    assert payload["raw_identifiers_visible"] is False
    assert payload["filename_visible"] is False


def test_build_webhook_export_plan_event_payload_is_redacted_and_typed():
    payload = build_webhook_export_plan_event_payload(
        message={"chat_id": "raw-chat-id", "update_id": 12, "message_id": 34},
        export_plan={
            "status": "exported",
            "target_format": "pdf",
            "action": "deliver",
            "required_tool": "telegram_document_reply",
            "bytes_written": "1234",
            "delivery_ready": True,
            "output_path": "C:/private/export.pdf",
        },
    )

    assert payload == {
        "kind": "universal_inbox_export_plan",
        "status": "exported",
        "chat_id": "raw-chat-id",
        "update_id": 12,
        "message_id": 34,
        "target_format": "pdf",
        "action": "deliver",
        "required_tool": "telegram_document_reply",
        "bytes_written": 1234,
        "delivery_ready": True,
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "filename_visible": False,
    }
    assert "output_path" not in payload


def test_build_webhook_export_delivery_event_payload_hides_host_paths():
    payload = build_webhook_export_delivery_event_payload(
        message={"chat_id": "raw-chat-id", "update_id": 12, "message_id": 34},
        export_plan={
            "status": "sent",
            "target_format": "pdf",
            "bytes_written": "4321",
            "output_path": "C:/private/export.pdf",
        },
    )

    assert payload == {
        "kind": "universal_inbox_export_delivery",
        "status": "sent",
        "chat_id": "raw-chat-id",
        "update_id": 12,
        "message_id": 34,
        "target_format": "pdf",
        "bytes_written": 4321,
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "filename_visible": False,
        "host_paths_visible": False,
    }
    assert "output_path" not in payload


def test_build_webhook_export_plan_summary_is_public_only():
    summary = build_webhook_export_plan_summary(
        {
            "status": "sent",
            "target_format": "pdf",
            "action": "deliver",
            "output_path": "C:/private/export.pdf",
            "raw_content": "private",
        }
    )

    assert summary == {
        "status": "sent",
        "target_format": "pdf",
        "action": "deliver",
        "raw_content_visible": False,
    }


def test_build_webhook_project_intake_summary_is_public_only():
    summary = build_webhook_project_intake_summary(
        {
            "status": "ready",
            "project_slug": "demo",
            "task_count": 3,
            "raw_content": "private",
        }
    )

    assert summary == {
        "status": "ready",
        "project_slug": "demo",
        "task_count": 3,
        "raw_content_visible": False,
    }


def test_build_webhook_control_command_event_payload_is_redacted():
    payload = build_webhook_control_command_event_payload(
        message={"chat_id": "raw-chat-id"},
        control_result={
            "status": "handled",
            "command": "/privacy",
            "binding": {"session_id": "session-1", "secret": "private"},
            "reply": {"message": "private reply"},
        },
    )

    assert payload == {
        "kind": "control_command",
        "status": "handled",
        "chat_id": "raw-chat-id",
        "session_id": "session-1",
        "command": "/privacy",
    }
    assert "reply" not in payload
    assert "secret" not in payload


def test_build_webhook_control_command_summary_is_public_only():
    summary = build_webhook_control_command_summary(
        {
            "status": "handled",
            "command": "/privacy",
            "pin_status": "ok",
            "binding": {"session_id": "session-1", "secret": "private"},
            "reply": {"message": "private reply"},
        }
    )

    assert summary == {
        "command": "/privacy",
        "status": "handled",
        "pin_status": "ok",
        "session_id_present": True,
    }


def test_build_webhook_agent_turn_event_payload_is_redacted():
    payload = build_webhook_agent_turn_event_payload(
        bridge={
            "chat_id": "chat_safe",
            "session_id": "session_safe",
            "prompt": "private prompt",
        },
        agent_turn={
            "status": "accepted",
            "reply_text_present": True,
            "reply_text": "private reply",
        },
    )

    assert payload == {
        "kind": "agent_turn",
        "status": "accepted",
        "chat_id": "chat_safe",
        "session_id": "session_safe",
        "reply_text_present": True,
    }
    assert "prompt" not in payload
    assert "reply_text" not in payload


def test_run_webhook_control_command_branch_skips_when_unhandled():
    events = []

    class Store:
        def append_event(self, **payload):
            events.append(payload)

    result = run_webhook_control_command_branch(
        message={"chat_id": "raw-chat-id"},
        stored_message={"kind": "text", "text": "hello"},
        raw_chat_id="raw-chat-id",
        sessions="sessions",
        session_creator="creator",
        reply_handler=lambda *_args, **_kwargs: {"exit_code": 0},
        store=Store(),
        pin_store="pins",
        memory_manager="memory-manager",
        memory_vector="memory-vector",
        memory_owner="owner",
        project_registry_path="registry",
        detect_control_command=lambda _message: None,
        handle_control_command=lambda *_args, **_kwargs: None,
    )

    assert result is None
    assert events == []


def test_run_webhook_control_command_branch_handles_and_records_event():
    events = []
    handler_calls = []

    class Store:
        def append_event(self, **payload):
            events.append(payload)

    def _handle(command, **kwargs):
        handler_calls.append((command, kwargs))
        return {
            "status": "handled",
            "command": command,
            "binding": {"session_id": "session-1", "secret": "private"},
            "reply": {"message": "private"},
        }

    store = Store()
    result = run_webhook_control_command_branch(
        message={"chat_id": "raw-chat-id", "update_id": 12, "message_id": 34},
        stored_message={"kind": "text", "text": "/privacy"},
        raw_chat_id="raw-chat-id",
        sessions="sessions",
        session_creator="creator",
        reply_handler=lambda *_args, **_kwargs: {"exit_code": 0},
        store=store,
        pin_store="pins",
        memory_manager="memory-manager",
        memory_vector="memory-vector",
        memory_owner="owner",
        project_registry_path="registry",
        detect_control_command=lambda message: f"command:{message['text']}",
        handle_control_command=_handle,
    )

    assert result["status"] == "handled"
    assert handler_calls[0][0] == "command:/privacy"
    assert handler_calls[0][1]["raw_chat_id"] == "raw-chat-id"
    assert handler_calls[0][1]["sessions"] == "sessions"
    assert handler_calls[0][1]["session_creator"] == "creator"
    assert handler_calls[0][1]["store"] is store
    assert handler_calls[0][1]["pin_store"] == "pins"
    assert handler_calls[0][1]["memory_manager"] == "memory-manager"
    assert handler_calls[0][1]["memory_vector"] == "memory-vector"
    assert handler_calls[0][1]["memory_owner"] == "owner"
    assert handler_calls[0][1]["project_registry_path"] == "registry"
    assert events == [
        {
            "kind": "control_command",
            "status": "handled",
            "chat_id": "raw-chat-id",
            "session_id": "session-1",
            "command": "command:/privacy",
        }
    ]
    assert "private" not in json.dumps(events)


@pytest.mark.asyncio
async def test_run_webhook_agent_turn_branch_binds_runs_types_and_replies():
    events = []
    replies = []
    bind_calls = []
    bridge_calls = []
    run_calls = []
    typing_calls = []
    stops = []

    class Sessions:
        def bind_chat(self, **kwargs):
            bind_calls.append(kwargs)
            return {"session_id": "session-1", "last_selected_scope": kwargs["scope"]}

    class Store:
        def append_event(self, **payload):
            events.append(payload)

    class Stop:
        def __init__(self):
            self.stopped = False

        def set(self):
            self.stopped = True

    def _build_bridge(message, **kwargs):
        bridge_calls.append((message, kwargs))
        return {
            "ready_for_agent": True,
            "chat_id": "chat_safe",
            "session_alias": "alias",
            "recommended_session_name": "session name",
            "desired_session_scope": "secure",
            "session_id": kwargs["session_binding"]["session_id"],
            "source_message_id": 34,
        }

    async def _typing_pulse(chat_id, store):
        stop = Stop()
        stops.append(stop)
        typing_calls.append((chat_id, store))
        return stop, asyncio.create_task(asyncio.sleep(0))

    async def _run_agent(handler, bridge):
        run_calls.append((handler, bridge))
        return {"status": "accepted", "reply_text": "Agent reply", "reply_text_present": True}

    store = Store()
    bridge, agent_turn, reply = await run_webhook_agent_turn_branch(
        stored_message={"kind": "text", "text": "hello"},
        bridge={
            "ready_for_agent": True,
            "chat_id": "chat_safe",
            "session_alias": "alias",
            "recommended_session_name": "session name",
            "desired_session_scope": "secure",
        },
        raw_chat_id="raw-chat-id",
        sessions=Sessions(),
        session_creator="creator",
        store=store,
        voice_agent_turn="voice-turn",
        recent_attachment_context={"status": "available"},
        agent_turn_handler=lambda _bridge: {"reply_text": "unused"},
        build_agent_bridge_request=_build_bridge,
        deterministic_agent_turn=lambda _bridge: None,
        run_agent_turn_async=_run_agent,
        typing_pulse=_typing_pulse,
        agent_failure_reply=lambda _turn: "failure reply",
        reply_with_gate=lambda chat_id, text, source_message_id=None: replies.append(
            (chat_id, text, source_message_id)
        )
        or {"exit_code": 0},
    )

    assert bridge["session_id"] == "session-1"
    assert agent_turn["reply_text"] == "Agent reply"
    assert reply == {"exit_code": 0}
    assert bind_calls[0]["scope"] == "secure"
    assert bind_calls[0]["creator"] == "creator"
    assert bridge_calls[0][1]["raw_chat_id"] == "raw-chat-id"
    assert bridge_calls[0][1]["voice_agent_turn"] == "voice-turn"
    assert bridge_calls[0][1]["recent_attachment_context"] == {"status": "available"}
    assert run_calls[0][1]["session_id"] == "session-1"
    assert typing_calls == [("chat_safe", store)]
    assert stops[0].stopped is True
    assert events == [
        {
            "kind": "agent_turn",
            "status": "accepted",
            "chat_id": "chat_safe",
            "session_id": "session-1",
            "reply_text_present": True,
        }
    ]
    assert replies == [("chat_safe", "Agent reply", 34)]
    assert "Agent reply" not in json.dumps(events)


@pytest.mark.asyncio
async def test_run_webhook_agent_turn_branch_uses_deterministic_turn_without_typing_or_handler():
    events = []
    typing_calls = []
    run_calls = []

    class Sessions:
        def bind_chat(self, **kwargs):
            return {"session_id": "session-1", "last_selected_scope": kwargs["scope"]}

    class Store:
        def append_event(self, **payload):
            events.append(payload)

    async def _typing_pulse(*_args, **_kwargs):
        typing_calls.append("typing")
        return None, asyncio.create_task(asyncio.sleep(0))

    async def _run_agent(*_args, **_kwargs):
        run_calls.append("handler")
        return {"status": "accepted", "reply_text": "handler reply"}

    bridge, agent_turn, reply = await run_webhook_agent_turn_branch(
        stored_message={"kind": "text", "text": "diagnose"},
        bridge={
            "ready_for_agent": True,
            "chat_id": "chat_safe",
            "session_alias": "alias",
            "recommended_session_name": "session name",
        },
        raw_chat_id="raw-chat-id",
        sessions=Sessions(),
        session_creator="creator",
        store=Store(),
        voice_agent_turn=None,
        recent_attachment_context=None,
        agent_turn_handler=lambda _bridge: {"reply_text": "handler"},
        build_agent_bridge_request=lambda _message, **kwargs: {
            "ready_for_agent": True,
            "chat_id": "chat_safe",
            "session_alias": "alias",
            "recommended_session_name": "session name",
            "session_id": kwargs["session_binding"]["session_id"],
            "source_message_id": 34,
        },
        deterministic_agent_turn=lambda _bridge: {
            "status": "accepted",
            "reply_text": "",
            "reply_text_present": False,
        },
        run_agent_turn_async=_run_agent,
        typing_pulse=_typing_pulse,
        agent_failure_reply=lambda _turn: "failure reply",
        reply_with_gate=lambda chat_id, text, source_message_id=None: {
            "chat_id": chat_id,
            "text": text,
            "source_message_id": source_message_id,
        },
    )

    assert bridge["session_id"] == "session-1"
    assert agent_turn["reply_text"] == ""
    assert reply == {
        "chat_id": "chat_safe",
        "text": "failure reply",
        "source_message_id": 34,
    }
    assert typing_calls == []
    assert run_calls == []
    assert events[0]["reply_text_present"] is False


def test_run_webhook_project_intake_branch_skips_non_text():
    called = []

    project_intake, reply = run_webhook_project_intake_branch(
        message={"chat_id": "chat-1"},
        stored_message={"kind": "voice"},
        data_dir="data",
        store="store",
        sessions="sessions",
        project_registry_path="registry",
        build_project_intake_preview=lambda **_kwargs: called.append("preview") or {"status": "ready"},
        format_project_intake_reply=lambda _project: "reply",
        reply_with_gate=lambda *_args, **_kwargs: {"ok": True},
    )

    assert project_intake is None
    assert reply is None
    assert called == []


def test_run_webhook_project_intake_branch_skips_when_no_preview():
    replies = []

    project_intake, reply = run_webhook_project_intake_branch(
        message={"chat_id": "chat-1", "message_id": 7},
        stored_message={"kind": "text", "text": "/project"},
        data_dir="data",
        store="store",
        sessions="sessions",
        project_registry_path="registry",
        build_project_intake_preview=lambda **_kwargs: None,
        format_project_intake_reply=lambda _project: "reply",
        reply_with_gate=lambda *_args, **_kwargs: replies.append("reply") or {"ok": True},
    )

    assert project_intake is None
    assert reply is None
    assert replies == []


def test_run_webhook_project_intake_branch_replies_with_injected_helpers():
    preview_calls = []
    reply_calls = []

    def _build_preview(**kwargs):
        preview_calls.append(kwargs)
        return {"status": "ready", "project_slug": "demo", "raw_content": "private"}

    def _reply(chat_id, text, source_message_id=None):
        reply_calls.append((chat_id, text, source_message_id))
        return {"ok": True, "exit_code": 0}

    project_intake, reply = run_webhook_project_intake_branch(
        message={"chat_id": "chat-1", "message_id": 7},
        stored_message={"kind": "text", "text": "/project demo"},
        data_dir="data",
        store="store",
        sessions="sessions",
        project_registry_path="registry",
        build_project_intake_preview=_build_preview,
        format_project_intake_reply=lambda project: f"project:{project['project_slug']}",
        reply_with_gate=_reply,
    )

    assert project_intake == {"status": "ready", "project_slug": "demo", "raw_content": "private"}
    assert reply == {"ok": True, "exit_code": 0}
    assert preview_calls == [
        {
            "data_dir": "data",
            "store": "store",
            "sessions": "sessions",
            "chat_id": "chat-1",
            "text": "/project demo",
            "source_message_id": 7,
            "project_registry_path": "registry",
        }
    ]
    assert reply_calls == [("chat-1", "project:demo", 7)]


def test_run_webhook_attachment_export_branch_skips_non_text():
    calls = []

    export_plan, reply = run_webhook_attachment_export_branch(
        message={"chat_id": "chat-1"},
        stored_message={"kind": "document"},
        data_dir="data",
        store=object(),
        execute_attachment_export=lambda **_kwargs: calls.append("export") or {"status": "blocked"},
        document_reply_with_gate=lambda *_args, **_kwargs: {"exit_code": 0},
        reply_with_gate=lambda *_args, **_kwargs: {"exit_code": 0},
        format_export_reply=lambda _plan: "reply",
    )

    assert export_plan is None
    assert reply is None
    assert calls == []


def test_run_webhook_attachment_export_branch_skips_when_no_plan():
    replies = []

    export_plan, reply = run_webhook_attachment_export_branch(
        message={"chat_id": "chat-1"},
        stored_message={"kind": "text", "text": "/export"},
        data_dir="data",
        store=object(),
        execute_attachment_export=lambda **_kwargs: None,
        document_reply_with_gate=lambda *_args, **_kwargs: replies.append("document"),
        reply_with_gate=lambda *_args, **_kwargs: replies.append("text"),
        format_export_reply=lambda _plan: "reply",
    )

    assert export_plan is None
    assert reply is None
    assert replies == []


def test_run_webhook_attachment_export_branch_replies_to_blocked_plan():
    events = []
    replies = []

    class Store:
        def append_event(self, **payload):
            events.append(payload)

    def _execute(**kwargs):
        assert kwargs == {
            "data_dir": "data",
            "store": store,
            "chat_id": "raw-chat-id",
            "text": "/export pdf",
        }
        return {
            "status": "blocked",
            "target_format": "pdf",
            "action": "export_recent_attachment",
            "reason": "missing_attachment",
        }

    store = Store()
    export_plan, reply = run_webhook_attachment_export_branch(
        message={"chat_id": "raw-chat-id", "update_id": 12, "message_id": 34},
        stored_message={"kind": "text", "text": "/export pdf"},
        data_dir="data",
        store=store,
        execute_attachment_export=_execute,
        document_reply_with_gate=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("document delivery should not run")
        ),
        reply_with_gate=lambda chat_id, text, source_message_id=None: replies.append(
            (chat_id, text, source_message_id)
        )
        or {"exit_code": 0},
        format_export_reply=lambda plan: f"export:{plan['status']}",
    )

    assert export_plan["status"] == "blocked"
    assert reply == {"exit_code": 0}
    assert events[0]["kind"] == "universal_inbox_export_plan"
    assert events[0]["raw_content_visible"] is False
    assert events[0]["filename_visible"] is False
    assert replies == [("raw-chat-id", "export:blocked", 34)]


def test_run_webhook_attachment_export_branch_sends_exported_document():
    events = []
    document_calls = []

    class Store:
        def append_event(self, **payload):
            events.append(payload)

    export_plan, reply = run_webhook_attachment_export_branch(
        message={"chat_id": "raw-chat-id", "update_id": 12, "message_id": 34},
        stored_message={"kind": "text", "text": "/export pdf"},
        data_dir="data",
        store=Store(),
        execute_attachment_export=lambda **_kwargs: {
            "status": "exported",
            "target_format": "pdf",
            "action": "export_recent_attachment",
            "bytes_written": 123,
            "output_path": "C:/private/export.pdf",
            "output_filename": "telegram-export.pdf",
        },
        document_reply_with_gate=lambda chat_id, path, filename, caption, source_message_id=None: document_calls.append(
            (chat_id, path, filename, caption, source_message_id)
        )
        or {"exit_code": 0},
        reply_with_gate=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("text reply should not run")
        ),
        format_export_reply=lambda plan: f"export:{plan['status']}",
    )

    assert export_plan["status"] == "sent"
    assert reply == {"exit_code": 0}
    assert events[0]["kind"] == "universal_inbox_export_plan"
    assert events[1]["kind"] == "universal_inbox_export_delivery"
    assert events[1]["host_paths_visible"] is False
    assert document_calls == [
        (
            "raw-chat-id",
            "C:/private/export.pdf",
            "telegram-export.pdf",
            "export:sent",
            34,
        )
    ]


def test_run_webhook_attachment_export_branch_reports_document_delivery_failure():
    events = []

    class Store:
        def append_event(self, **payload):
            events.append(payload)

    export_plan, reply = run_webhook_attachment_export_branch(
        message={"chat_id": "raw-chat-id", "update_id": 12, "message_id": 34},
        stored_message={"kind": "text", "text": "/export pdf"},
        data_dir="data",
        store=Store(),
        execute_attachment_export=lambda **_kwargs: {
            "status": "exported",
            "target_format": "pdf",
            "action": "export_recent_attachment",
            "output_path": "C:/private/export.pdf",
        },
        document_reply_with_gate=lambda *_args, **_kwargs: {
            "exit_code": 1,
            "error": "transport unavailable with private details",
        },
        reply_with_gate=lambda *_args, **_kwargs: {"exit_code": 0},
        format_export_reply=lambda plan: f"export:{plan['status']}",
    )

    assert export_plan["status"] == "exported"
    assert export_plan["reason"] == "document_delivery_failed:transport unavailable with private details"
    assert reply["exit_code"] == 1
    assert [event["kind"] for event in events] == ["universal_inbox_export_plan"]


def test_run_webhook_attachment_branch_skips_when_no_attachment():
    calls = []

    inbox_attachment, reply = run_webhook_attachment_branch(
        message={"chat_id": "chat-1"},
        stored={"message": {"kind": "text"}},
        data_dir="data",
        store=object(),
        attachment_bytes_provider="bytes",
        memory_manager=None,
        memory_vector=None,
        memory_owner=None,
        run_attachment_pipeline=lambda *_args, **_kwargs: None,
        attachment_spool_key=lambda _message: calls.append("spool") or "spool",
        attachment_family=lambda _message: "text",
        attachment_suffix=lambda _message: "",
        execute_memory_auto_write_if_ready=lambda **_kwargs: {"status": "written"},
        format_attachment_reply=lambda _attachment: "reply",
        reply_with_gate=lambda *_args, **_kwargs: {"ok": True},
    )

    assert inbox_attachment is None
    assert reply is None
    assert calls == []


def test_run_webhook_attachment_branch_updates_status_event_memory_and_reply():
    events = []
    updates = []
    replies = []
    memory_calls = []

    class Store:
        def update_inbound_status(self, message, **kwargs):
            updates.append((message, kwargs))
            return {**message, "intake_status": kwargs["intake_status"], "refreshed": True}

        def append_event(self, **payload):
            events.append(payload)

    def _memory_auto_write(**kwargs):
        memory_calls.append(kwargs)
        return {"status": "written", "reason": "ready", "writes_performed": True}

    stored = {"stored": True, "message": {"kind": "document", "chat_handle": "chat_safe"}}

    inbox_attachment, reply = run_webhook_attachment_branch(
        message={"chat_id": "raw-chat-id", "update_id": 12, "message_id": 34},
        stored=stored,
        data_dir="data",
        store=Store(),
        attachment_bytes_provider="bytes",
        memory_manager="memory-manager",
        memory_vector="memory-vector",
        memory_owner="alice",
        run_attachment_pipeline=lambda message, **kwargs: {
            "status": "processed",
            "universal_inbox_status": "processed",
            "memory_write_intent_status": "planned",
            "discovered_count": 2,
            "processable_count": 1,
        },
        attachment_spool_key=lambda message: "spool_safe" if message.get("chat_handle") else "bad",
        attachment_family=lambda message: message["kind"],
        attachment_suffix=lambda _message: ".pdf",
        execute_memory_auto_write_if_ready=_memory_auto_write,
        format_attachment_reply=lambda attachment: f"status:{attachment['memory_auto_write_status']}",
        reply_with_gate=lambda chat_id, text, source_message_id=None: replies.append(
            (chat_id, text, source_message_id)
        )
        or {"ok": True, "exit_code": 0},
    )

    assert stored["message"]["refreshed"] is True
    assert inbox_attachment == {
        "status": "processed",
        "universal_inbox_status": "processed",
        "memory_write_intent_status": "planned",
        "discovered_count": 2,
        "processable_count": 1,
        "memory_auto_write_status": "written",
        "memory_auto_write_reason": "ready",
        "memory_auto_writes_performed": True,
    }
    assert reply == {"ok": True, "exit_code": 0}
    assert updates == [
        (
            {"kind": "document", "chat_handle": "chat_safe"},
            {
                "universal_inbox_status": "processed",
                "intake_status": "universal_inbox_processed",
            },
        )
    ]
    assert events[0]["kind"] == "universal_inbox_attachment"
    assert events[0]["attachment_family"] == "document"
    assert events[0]["attachment_suffix"] == ".pdf"
    assert events[0]["spool_key"] == "spool_safe"
    assert events[0]["raw_content_visible"] is False
    assert events[0]["raw_identifiers_visible"] is False
    assert events[0]["filename_visible"] is False
    assert memory_calls[0]["chat_id"] == "raw-chat-id"
    assert memory_calls[0]["memory_manager"] == "memory-manager"
    assert memory_calls[0]["memory_vector"] == "memory-vector"
    assert memory_calls[0]["memory_owner"] == "alice"
    assert replies == [("raw-chat-id", "status:written", 34)]

import asyncio
import json
from types import SimpleNamespace

import pytest

from plugins.telegram.polling import _deliver_agent_reply as deliver_polling_agent_reply
from plugins.telegram.stores import TelegramInboxStore
from plugins.telegram.webhook_service import (
    _deliver_agent_reply,
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
    run_durable_webhook_agent_turn_branch,
    run_webhook_media_pipelines,
    run_webhook_project_intake_branch,
)
from src.tool_transaction_ledger import TOOL_TRANSACTION_LEDGER_SCHEMA
from src.telegram_session_rollover import RolloverConfig


def _todo_transaction() -> dict:
    return {
        "schema": TOOL_TRANSACTION_LEDGER_SCHEMA,
        "transaction_id": "agent:0:manage_todos:todo_item_created",
        "surface": "agent",
        "tool": "manage_todos",
        "claim_type": "todo_item_created",
        "status": "verified",
        "evidence_refs": [
            "owner:0123456789abcdef",
            "list:fedcba9876543210",
            "item:0011223344556677",
            "operation:add",
        ],
        "exit_code": 0,
        "artifact_refs": [],
        "command_hash": "sha256:e3b0c44298fc1c149afbf4c8996fb924",
        "verified_done": True,
        "raw_content_visible": False,
    }


def test_webhook_legacy_reply_handler_receives_one_delivery_with_todo_carrier():
    deliveries = []

    def _legacy(chat_id, text, *, source_message_id=None):
        deliveries.append((chat_id, text, source_message_id))
        return {"ok": True}

    result = _deliver_agent_reply(
        _legacy,
        "chat_safe",
        "I created the todo item.",
        5,
        [_todo_transaction()],
    )

    assert result == {"ok": True}
    assert deliveries == [("chat_safe", "I created the todo item.", 5)]


def test_hostile_signature_reply_handlers_fall_back_to_one_legacy_delivery():
    webhook_deliveries = []
    polling_deliveries = []

    class HostileWebhookHandler:
        @property
        def __signature__(self):
            raise RuntimeError("signature lookup must not escape")

        def __call__(self, chat_id, text, *, source_message_id=None):
            webhook_deliveries.append((chat_id, text, source_message_id))
            return {"ok": True}

    class HostilePollingHandler:
        @property
        def __signature__(self):
            raise ValueError("signature lookup must not escape")

        def __call__(self, chat_id, text, source_message_id=None):
            polling_deliveries.append((chat_id, text, source_message_id))
            return {"ok": True}

    assert _deliver_agent_reply(
        HostileWebhookHandler(), "chat_safe", "I created the todo item.", 5, [_todo_transaction()]
    ) == {"ok": True}
    assert deliver_polling_agent_reply(
        HostilePollingHandler(), "chat_safe", "I created the todo item.", 6, [_todo_transaction()]
    ) == {"ok": True}
    assert webhook_deliveries == [("chat_safe", "I created the todo item.", 5)]
    assert polling_deliveries == [("chat_safe", "I created the todo item.", 6)]


@pytest.mark.asyncio
async def test_durable_webhook_busy_is_retryable_without_agent_or_reply_work():
    class Coordinator:
        def acquire_turn(self, **kwargs):
            assert kwargs["owner"] == "runtime-owner"
            assert kwargs["stable_chat_handle"] == "chat_stable"
            return SimpleNamespace(status="lease_busy", lease=None, intake=None)

    runtime = SimpleNamespace(telegram_owner="runtime-owner", turn_coordinator=Coordinator())
    status, _bridge, agent_turn, reply = await run_durable_webhook_agent_turn_branch(
        runtime=runtime,
        stored_message={"update_id": 77, "message_id": 88, "kind": "text", "intake_status": "ready"},
        bridge={
            "chat_handle": "chat_stable",
            "chat_id": "raw-chat",
            "desired_session_scope": "normal",
            "session_scope": "normal",
            "ready_for_agent": True,
        },
        raw_chat_id="raw-chat",
        store=SimpleNamespace(append_event=lambda **_kwargs: None),
        voice_agent_turn=None,
        recent_attachment_context=None,
        agent_turn_handler=lambda _bridge: pytest.fail("busy must not run agent work"),
        build_agent_bridge_request=lambda *_args, **_kwargs: pytest.fail("busy must not build a turn"),
        deterministic_agent_turn=lambda _bridge: pytest.fail("busy must not run deterministic work"),
        run_agent_turn_async=lambda *_args, **_kwargs: pytest.fail("busy must not run agent work"),
        typing_pulse=lambda *_args, **_kwargs: pytest.fail("busy must not type"),
        agent_failure_reply=lambda _turn: "",
        reply_with_gate=lambda *_args, **_kwargs: pytest.fail("busy must not reply"),
    )

    assert status == "lease_retry"
    assert agent_turn is None
    assert reply is None


@pytest.mark.asyncio
async def test_durable_webhook_acquired_turn_completes_only_after_explicit_reply_success():
    lease = object()

    class Coordinator:
        def __init__(self):
            self.marked = []
            self.completed = []
            self.released = []

        def acquire_turn(self, **_kwargs):
            return SimpleNamespace(
                status="acquired",
                lease=lease,
                intake=SimpleNamespace(expected_session_id="durable-session"),
            )

        def mark_reply_persisted(self, value):
            self.marked.append(value)

        def complete_and_release(self, value):
            self.completed.append(value)

        def release_turn(self, value):
            self.released.append(value)

    coordinator = Coordinator()
    runtime = SimpleNamespace(telegram_owner="runtime-owner", turn_coordinator=coordinator)
    bridge = {
        "chat_handle": "chat_stable",
        "chat_id": "raw-chat",
        "desired_session_scope": "normal",
        "session_scope": "normal",
        "ready_for_agent": True,
        "source_message_id": 88,
    }
    status, final_bridge, agent_turn, reply = await run_durable_webhook_agent_turn_branch(
        runtime=runtime,
        stored_message={"update_id": 77, "message_id": 88, "kind": "text", "intake_status": "ready"},
        bridge=bridge,
        raw_chat_id="raw-chat",
        store=SimpleNamespace(append_event=lambda **_kwargs: None),
        voice_agent_turn=None,
        recent_attachment_context=None,
        agent_turn_handler=None,
        build_agent_bridge_request=lambda _message, **kwargs: {**bridge, "session_id": kwargs["session_binding"]["session_id"]},
        deterministic_agent_turn=lambda _bridge: {"status": "accepted", "reply_text": "done", "reply_text_present": True},
        run_agent_turn_async=lambda *_args, **_kwargs: pytest.fail("deterministic turn must not call agent"),
        typing_pulse=lambda *_args, **_kwargs: pytest.fail("deterministic turn must not type"),
        agent_failure_reply=lambda _turn: "",
        reply_with_gate=lambda *_args, **_kwargs: {"ok": True},
    )

    assert status == "completed"
    assert final_bridge["session_id"] == "durable-session"
    assert agent_turn["status"] == "accepted"
    assert reply == {"ok": True}
    assert coordinator.marked == [lease]
    assert coordinator.completed == [lease]
    assert coordinator.released == []


@pytest.mark.asyncio
async def test_durable_webhook_reply_pending_requires_exact_sent_receipt_without_replay():
    class Coordinator:
        def __init__(self):
            self.evidence_calls = []

        def acquire_turn(self, **_kwargs):
            return SimpleNamespace(status="reply_pending_reconciliation_required", lease=None, intake=None)

        def complete_reply_pending_from_outbound_evidence(self, **kwargs):
            self.evidence_calls.append(kwargs)
            return SimpleNamespace(status="completed_from_outbound_evidence")

    coordinator = Coordinator()
    runtime = SimpleNamespace(telegram_owner="runtime-owner", turn_coordinator=coordinator)
    store = SimpleNamespace(history=lambda limit: [{
        "direction": "outbound",
        "chat_handle": "chat_stable",
        "source_message_id": 88,
        "delivery_status": "sent",
    }])
    status, _bridge, agent_turn, reply = await run_durable_webhook_agent_turn_branch(
        runtime=runtime,
        stored_message={"update_id": 77, "message_id": 88, "kind": "text"},
        bridge={"chat_handle": "chat_stable", "chat_id": "raw-chat", "desired_session_scope": "normal"},
        raw_chat_id="raw-chat",
        store=store,
        voice_agent_turn=None,
        recent_attachment_context=None,
        agent_turn_handler=lambda _bridge: pytest.fail("evidence completion must not replay"),
        build_agent_bridge_request=lambda *_args, **_kwargs: pytest.fail("evidence completion must not rebuild"),
        deterministic_agent_turn=lambda _bridge: pytest.fail("evidence completion must not run"),
        run_agent_turn_async=lambda *_args, **_kwargs: pytest.fail("evidence completion must not run"),
        typing_pulse=lambda *_args, **_kwargs: pytest.fail("evidence completion must not type"),
        agent_failure_reply=lambda _turn: "",
        reply_with_gate=lambda *_args, **_kwargs: pytest.fail("evidence completion must not reply"),
    )

    assert status == "duplicate_completed"
    assert agent_turn is None and reply is None
    assert coordinator.evidence_calls == [{
        "owner": "runtime-owner",
        "stable_chat_handle": "chat_stable",
        "update_id": 77,
        "message_id": 88,
        "outbound_sent": True,
    }]


@pytest.mark.asyncio
async def test_durable_webhook_reply_pending_without_receipt_reuses_exact_persisted_reply():
    class Coordinator:
        def __init__(self):
            self.completed = []

        def acquire_turn(self, **_kwargs):
            return SimpleNamespace(
                status="reply_pending_reconciliation_required",
                intake=SimpleNamespace(id="t1_" + "a" * 32, expected_session_id="durable-session"),
                lease=None,
            )

        def complete_reply_pending_from_outbound_evidence(self, **kwargs):
            self.completed.append(kwargs)
            return SimpleNamespace(status="completed_from_outbound_evidence")

    coordinator = Coordinator()
    runtime = SimpleNamespace(
        telegram_owner="runtime-owner",
        turn_coordinator=coordinator,
        turn_recovery_provider=lambda **_kwargs: {
            "markers": (SimpleNamespace(role="user"), SimpleNamespace(role="assistant")),
            "assistant_reply": "persisted reply",
        },
    )
    replies = []
    status, _bridge, turn, reply = await run_durable_webhook_agent_turn_branch(
        runtime=runtime,
        stored_message={"update_id": 77, "message_id": 88, "kind": "text"},
        bridge={"chat_handle": "chat_stable", "chat_id": "raw-chat", "desired_session_scope": "normal", "source_message_id": 88},
        raw_chat_id="raw-chat",
        store=SimpleNamespace(history=lambda _limit: []),
        voice_agent_turn=None,
        recent_attachment_context=None,
        agent_turn_handler=lambda _bridge: pytest.fail("recovery must not invoke the model"),
        build_agent_bridge_request=lambda *_args, **_kwargs: pytest.fail("recovery must not rebuild"),
        deterministic_agent_turn=lambda _bridge: pytest.fail("recovery must not run a new turn"),
        run_agent_turn_async=lambda *_args, **_kwargs: pytest.fail("recovery must not invoke the model"),
        typing_pulse=lambda *_args, **_kwargs: pytest.fail("recovery must not type"),
        agent_failure_reply=lambda _turn: "",
        reply_with_gate=lambda *_args, **_kwargs: replies.append(True) or {"ok": True},
    )
    assert status == "completed" and turn == {"status": "recovered"} and reply == {"ok": True}
    assert replies == [True] and len(coordinator.completed) == 1


@pytest.mark.asyncio
async def test_durable_webhook_busy_retry_rebuilds_ready_bridge_and_runs_one_turn():
    lease = object()

    class Coordinator:
        def __init__(self):
            self.calls = 0
            self.marked = []
            self.completed = []

        def acquire_turn(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(status="lease_busy", lease=None, intake=None)
            return SimpleNamespace(
                status="acquired",
                lease=lease,
                intake=SimpleNamespace(expected_session_id="durable-session"),
            )

        def mark_reply_persisted(self, value):
            self.marked.append(value)

        def complete_and_release(self, value):
            self.completed.append(value)

        def release_turn(self, _value):
            pytest.fail("accepted reply must complete rather than release")

    coordinator = Coordinator()
    runtime = SimpleNamespace(telegram_owner="runtime-owner", turn_coordinator=coordinator)
    built_messages = []
    agent_turns = []
    replies = []
    store = SimpleNamespace(append_event=lambda **_kwargs: None)
    bridge = {
        "chat_handle": "chat_stable",
        "chat_id": "raw-chat",
        "desired_session_scope": "normal",
        "ready_for_agent": False,
        "source_message_id": 88,
    }

    first = await run_durable_webhook_agent_turn_branch(
        runtime=runtime,
        stored_message={"update_id": 77, "message_id": 88, "kind": "text", "intake_status": "ready"},
        bridge=bridge,
        raw_chat_id="raw-chat",
        store=store,
        voice_agent_turn=None,
        recent_attachment_context=None,
        agent_turn_handler=lambda _bridge: pytest.fail("busy attempt must not run agent work"),
        build_agent_bridge_request=lambda *_args, **_kwargs: pytest.fail("busy attempt must not rebuild"),
        deterministic_agent_turn=lambda _bridge: pytest.fail("busy attempt must not run deterministic work"),
        run_agent_turn_async=lambda *_args, **_kwargs: pytest.fail("busy attempt must not run agent work"),
        typing_pulse=lambda *_args, **_kwargs: pytest.fail("busy attempt must not type"),
        agent_failure_reply=lambda _turn: "",
        reply_with_gate=lambda *_args, **_kwargs: pytest.fail("busy attempt must not reply"),
    )
    assert first[0] == "lease_retry"

    def build_retry_bridge(message, **kwargs):
        built_messages.append(dict(message))
        assert kwargs["session_binding"]["session_id"] == "durable-session"
        return {
            **bridge,
            "session_id": "durable-session",
            "ready_for_agent": True,
            "reply_required": True,
        }

    def deterministic_turn(final_bridge):
        agent_turns.append(final_bridge)
        return {"status": "accepted", "reply_text": "done", "reply_text_present": True}

    second = await run_durable_webhook_agent_turn_branch(
        runtime=runtime,
        stored_message={"update_id": 77, "message_id": 88, "kind": "text", "intake_status": "lease_retry"},
        bridge=bridge,
        raw_chat_id="raw-chat",
        store=store,
        voice_agent_turn=None,
        recent_attachment_context=None,
        agent_turn_handler=None,
        build_agent_bridge_request=build_retry_bridge,
        deterministic_agent_turn=deterministic_turn,
        run_agent_turn_async=lambda *_args, **_kwargs: pytest.fail("deterministic turn must not call agent"),
        typing_pulse=lambda *_args, **_kwargs: pytest.fail("deterministic turn must not type"),
        agent_failure_reply=lambda _turn: "",
        reply_with_gate=lambda *_args, **_kwargs: replies.append(True) or {"ok": True},
    )
    assert second[0] == "completed"
    assert built_messages == [{"update_id": 77, "message_id": 88, "kind": "text", "intake_status": "ready"}]
    assert len(agent_turns) == 1 and replies == [True]
    assert coordinator.marked == [lease] and coordinator.completed == [lease]


@pytest.mark.asyncio
async def test_rollover_coordinator_busy_webhook_is_retryable_and_feature_default_off():
    from plugins.telegram.plugin import _rollover_retry_http_exception

    assert RolloverConfig.from_mapping({}).enabled is False
    retry = _rollover_retry_http_exception(SimpleNamespace(retry_seconds=999))
    assert retry.status_code == 503
    assert retry.headers == {"Retry-After": "300"}
    await test_durable_webhook_busy_is_retryable_without_agent_or_reply_work()


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

    raw_store = (tmp_path / "telegram_history.json").read_text(encoding="utf-8")
    raw_record = json.loads(raw_store)["messages"][0]
    audit_store = store.audit_store.path.read_text(encoding="utf-8")
    audit_entry = json.loads(audit_store)["current"][0]
    audit_record = audit_entry["receipt"]

    assert raw_record["kind"] == "invalid_update"
    assert raw_record["status"] == "invalid_update"
    assert audit_entry["scope_ref"] == ""
    assert set(audit_record) == {
        "schema",
        "record_class",
        "direction",
        "kind",
        "status",
        "recorded_at",
        "raw_content_visible",
        "raw_identifiers_visible",
        "token_value_visible",
    }
    assert audit_record["schema"] == "odysseus.telegram.audit_receipt.v1"
    assert audit_record["record_class"] == "raw_bearing"
    assert audit_record["direction"] == "system"
    assert audit_record["kind"] == "unclassified"
    assert audit_record["status"] == "unknown"
    assert audit_record["raw_content_visible"] is False
    assert audit_record["raw_identifiers_visible"] is False
    assert audit_record["token_value_visible"] is False
    assert "error" not in audit_record
    assert "telegram update has no message" not in audit_store
    assert "do-not-persist" not in raw_store + audit_store
    assert "raw-chat-id" not in raw_store + audit_store


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


def test_build_webhook_response_payload_is_a_content_free_receipt():
    payload = build_webhook_response_payload(
        stored={"stored": True, "message": {"kind": "text", "text": "private input", "chat_handle": "chat_safe"}},
        agent_bridge={"ready_for_agent": True, "prompt": "private prompt"},
        voice_pipeline={"status": "skipped", "transcript": "private transcript"},
        image_action={"status": "disabled"},
        universal_inbox_attachment=None,
        agent_turn={"status": "accepted", "reply_text": "private reply"},
        reply={"ok": True, "error": "private failure"},
        extra={"control_command": {"status": "handled", "secret": "private"}},
    )

    assert payload == {
        "stored": True,
        "receipt": {
            "schema": "odysseus.telegram.audit_receipt.v1",
            "record_class": "raw_bearing",
            "direction": "unknown",
            "kind": "text",
            "status": "unknown",
            "recorded_at": 0,
            "raw_content_visible": False,
            "raw_identifiers_visible": False,
            "token_value_visible": False,
        },
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
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
async def test_webhook_agent_turn_forwards_closed_todo_transactions_without_audit_leak():
    events = []
    replies = []
    source_transaction = _todo_transaction()

    class Sessions:
        def bind_chat(self, **_kwargs):
            return {"session_id": "session-1"}

    class Store:
        def append_event(self, **payload):
            events.append(payload)

    async def _run_agent(_handler, _bridge):
        return {
            "status": "accepted",
            "reply_text": "I created the todo item.",
            "reply_text_present": True,
            "todo_transactions": [source_transaction],
        }

    class Stop:
        def set(self):
            return None

    async def _typing(*_args, **_kwargs):
        return Stop(), asyncio.create_task(asyncio.sleep(0))

    def _reply(chat_id, text, *, source_message_id=None, todo_transactions=()):
        replies.append((chat_id, text, source_message_id, todo_transactions))
        return {"exit_code": 0}

    _, _, reply = await run_webhook_agent_turn_branch(
        stored_message={"kind": "text"},
        bridge={
            "ready_for_agent": True,
            "chat_id": "chat_safe",
            "session_alias": "alias",
            "recommended_session_name": "name",
            "desired_session_scope": "normal",
        },
        raw_chat_id="raw-chat",
        sessions=Sessions(),
        session_creator=None,
        store=Store(),
        voice_agent_turn=None,
        recent_attachment_context=None,
        agent_turn_handler=lambda _bridge: None,
        build_agent_bridge_request=lambda _message, **kwargs: {
            "ready_for_agent": True,
            "chat_id": "chat_safe",
            "session_id": kwargs["session_binding"]["session_id"],
            "source_message_id": 7,
        },
        deterministic_agent_turn=lambda _bridge: None,
        run_agent_turn_async=_run_agent,
        typing_pulse=_typing,
        agent_failure_reply=lambda _turn: "failed",
        reply_with_gate=_reply,
    )

    assert reply == {"exit_code": 0}
    assert replies[0][:3] == ("chat_safe", "I created the todo item.", 7)
    assert replies[0][3][0] is not source_transaction
    assert replies[0][3][0]["claim_type"] == "todo_item_created"
    assert all("todo_transactions" not in event for event in events)


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

"""Webhook service helpers for Telegram intake."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from plugins.telegram.formatting import format_agent_turn_reply
from plugins.telegram.history_privacy import project_telegram_audit_record
from plugins.telegram.polling import TelegramTurnRenewalPulse
from src.telegram_todo_truth import telegram_todo_truth_envelope_public_summary
from src.telegram_truth_gate import project_telegram_todo_transactions


class TelegramWebhookIntakeError(ValueError):
    """Raised when a Telegram webhook update cannot be parsed safely."""


ParseUpdate = Callable[[dict[str, Any]], dict[str, Any]]
BuildVoiceProvider = Callable[[dict[str, Any]], Any]
RunVoicePipeline = Callable[..., tuple[Any, dict[str, Any]]]
RunImageAction = Callable[..., dict[str, Any]]
BuildProjectIntakePreview = Callable[..., dict[str, Any] | None]
FormatProjectIntakeReply = Callable[[dict[str, Any]], str]
ReplyWithGate = Callable[..., dict[str, Any] | None]
RunAttachmentPipeline = Callable[..., dict[str, Any] | None]
AttachmentValue = Callable[[dict[str, Any]], str]
ExecuteMemoryAutoWrite = Callable[..., dict[str, Any] | None]
ExecuteNextcloudAutoTransfer = Callable[..., dict[str, Any] | None]
FormatAttachmentReply = Callable[[dict[str, Any]], str]
ExecuteAttachmentExport = Callable[..., dict[str, Any] | None]
DocumentReplyWithGate = Callable[..., dict[str, Any] | None]
FormatExportReply = Callable[[dict[str, Any]], str]
DetectControlCommand = Callable[[dict[str, Any]], str | None]
HandleControlCommand = Callable[..., dict[str, Any] | None]
BuildAgentBridgeRequest = Callable[..., dict[str, Any]]
DeterministicAgentTurn = Callable[[dict[str, Any]], dict[str, Any] | None]
RunAgentTurnAsync = Callable[..., Any]
TypingPulse = Callable[..., Any]
AgentFailureReply = Callable[[dict[str, Any]], str]


def _reply_handler_supports_todo_transactions(handler: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(handler).parameters.values()
    except Exception:
        return False
    return any(
        parameter.name == "todo_transactions"
        and parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _reply_handler_supports_todo_truth_envelope(handler: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(handler).parameters.values()
    except Exception:
        return False
    return any(
        parameter.name == "todo_truth_envelope"
        and parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _deliver_agent_reply(
    reply_with_gate: ReplyWithGate,
    chat_id: str,
    text: str,
    source_message_id: Any,
    todo_transactions: Any = (),
    todo_truth_envelope: Any = None,
) -> dict[str, Any] | None:
    """Deliver once, forwarding only an already closed Todo carrier."""

    projected = project_telegram_todo_transactions(todo_transactions)
    kwargs: dict[str, Any] = {"source_message_id": source_message_id}
    if projected and _reply_handler_supports_todo_transactions(reply_with_gate):
        kwargs["todo_transactions"] = projected
    if isinstance(todo_truth_envelope, dict) and _reply_handler_supports_todo_truth_envelope(
        reply_with_gate
    ):
        kwargs["todo_truth_envelope"] = todo_truth_envelope
    if len(kwargs) > 1:
        return reply_with_gate(chat_id, text, **kwargs)
    return reply_with_gate(chat_id, text, source_message_id=source_message_id)


def parse_and_store_webhook_update(
    update: dict[str, Any],
    *,
    store: Any,
    parse_update: ParseUpdate,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse a Telegram update and store the redacted inbound message."""

    try:
        message = parse_update(update)
    except ValueError as exc:
        store.append_event(kind="invalid_update", status="invalid_update", error=str(exc)[:120])
        raise TelegramWebhookIntakeError("invalid telegram update") from exc
    return message, store.append_inbound(message)


def run_webhook_media_pipelines(
    *,
    message: dict[str, Any],
    stored_message: dict[str, Any],
    voice_stt_provider: Any,
    voice_bytes_provider: Any,
    image_bytes_provider: Any,
    image_worker_client: Any,
    image_actions_enabled: bool,
    build_live_voice_stt_provider: BuildVoiceProvider,
    run_voice_pipeline: RunVoicePipeline,
    run_image_action: RunImageAction,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Run fakeable voice and image webhook pipelines without owning transport."""

    selected_voice_provider = voice_stt_provider or build_live_voice_stt_provider(message)
    voice_agent_turn, voice_pipeline = run_voice_pipeline(
        stored_message,
        stt_provider=selected_voice_provider,
    )
    image_action = run_image_action(
        stored_message,
        enabled=image_actions_enabled,
        image_bytes_provider=image_bytes_provider,
        worker_client=image_worker_client,
    )
    return voice_agent_turn, voice_pipeline, image_action


def build_webhook_response_payload(
    *,
    stored: dict[str, Any],
    agent_bridge: dict[str, Any],
    voice_pipeline: dict[str, Any] | None,
    image_action: dict[str, Any] | None,
    universal_inbox_attachment: dict[str, Any] | None,
    agent_turn: dict[str, Any] | None,
    reply: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the public webhook response shape shared by webhook branches."""

    return {
        "stored": stored["stored"],
        "receipt": project_telegram_audit_record(stored.get("message")),
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "token_value_visible": False,
    }


def build_webhook_attachment_event_payload(
    *,
    message: dict[str, Any],
    inbox_attachment: dict[str, Any],
    attachment_family: str,
    attachment_suffix: str,
    spool_key: str,
) -> dict[str, Any]:
    """Build the redacted event payload for webhook attachment processing."""

    return {
        "kind": "universal_inbox_attachment",
        "status": str(inbox_attachment.get("status") or "failed"),
        "chat_id": str(message.get("chat_id") or ""),
        "update_id": message.get("update_id"),
        "message_id": message.get("message_id"),
        "universal_inbox_status": str(inbox_attachment.get("universal_inbox_status") or ""),
        "memory_write_intent_status": str(inbox_attachment.get("memory_write_intent_status") or ""),
        "attachment_family": attachment_family,
        "attachment_suffix": attachment_suffix,
        "discovered_count": int(inbox_attachment.get("discovered_count") or 0),
        "processable_count": int(inbox_attachment.get("processable_count") or 0),
        "queue_status": str(inbox_attachment.get("queue_status") or ""),
        "queue_concurrency": int(inbox_attachment.get("queue_concurrency") or 1),
        "maintenance_model_ref": str(inbox_attachment.get("maintenance_model_ref") or ""),
        "maintenance_provider": str(inbox_attachment.get("maintenance_provider") or ""),
        "maintenance_action": str(inbox_attachment.get("maintenance_action") or ""),
        "maintenance_review_required": bool(inbox_attachment.get("maintenance_review_required")),
        "review_reason_count": int(inbox_attachment.get("review_reason_count") or 0),
        "no_go_reason_count": int(inbox_attachment.get("no_go_reason_count") or 0),
        "extraction_status": str(inbox_attachment.get("extraction_status") or ""),
        "extraction_warning_codes": tuple(inbox_attachment.get("extraction_warning_codes") or ()),
        "memory_records_planned": int(inbox_attachment.get("memory_records_planned") or 0),
        "raptorgraph_events_planned": int(inbox_attachment.get("raptorgraph_events_planned") or 0),
        "spool_key": spool_key,
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "filename_visible": False,
    }


def build_webhook_export_plan_event_payload(
    *,
    message: dict[str, Any],
    export_plan: dict[str, Any],
) -> dict[str, Any]:
    """Build the redacted event payload for webhook attachment export plans."""

    return {
        "kind": "universal_inbox_export_plan",
        "status": str(export_plan.get("status") or "blocked"),
        "chat_id": str(message.get("chat_id") or ""),
        "update_id": message.get("update_id"),
        "message_id": message.get("message_id"),
        "target_format": str(export_plan.get("target_format") or ""),
        "action": str(export_plan.get("action") or ""),
        "required_tool": str(export_plan.get("required_tool") or ""),
        "bytes_written": int(export_plan.get("bytes_written") or 0),
        "delivery_ready": bool(export_plan.get("delivery_ready")),
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "filename_visible": False,
    }


def build_webhook_export_delivery_event_payload(
    *,
    message: dict[str, Any],
    export_plan: dict[str, Any],
) -> dict[str, Any]:
    """Build the redacted event payload for webhook export delivery."""

    return {
        "kind": "universal_inbox_export_delivery",
        "status": "sent",
        "chat_id": str(message.get("chat_id") or ""),
        "update_id": message.get("update_id"),
        "message_id": message.get("message_id"),
        "target_format": str(export_plan.get("target_format") or ""),
        "bytes_written": int(export_plan.get("bytes_written") or 0),
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "filename_visible": False,
        "host_paths_visible": False,
    }


def build_webhook_export_plan_summary(export_plan: dict[str, Any]) -> dict[str, Any]:
    """Build the public export-plan summary returned by webhook endpoints."""

    return {
        "status": export_plan.get("status"),
        "target_format": export_plan.get("target_format"),
        "action": export_plan.get("action"),
        "raw_content_visible": False,
    }


def build_webhook_project_intake_summary(project_intake: dict[str, Any]) -> dict[str, Any]:
    """Build the public project-intake summary returned by webhook endpoints."""

    return {
        "status": project_intake.get("status"),
        "project_slug": project_intake.get("project_slug"),
        "task_count": project_intake.get("task_count"),
        "raw_content_visible": False,
    }


def build_webhook_control_command_event_payload(
    *,
    message: dict[str, Any],
    control_result: dict[str, Any],
) -> dict[str, Any]:
    """Build the redacted event payload for handled webhook control commands."""

    return {
        "kind": "control_command",
        "status": str(control_result.get("status") or "handled"),
        "chat_id": str(message.get("chat_id") or ""),
        "session_id": str((control_result.get("binding") or {}).get("session_id") or ""),
        "command": str(control_result.get("command") or ""),
    }


def build_webhook_control_command_summary(control_result: dict[str, Any]) -> dict[str, Any]:
    """Build the public control-command summary returned by webhook endpoints."""

    return {
        "command": control_result.get("command"),
        "status": control_result.get("status"),
        "pin_status": control_result.get("pin_status"),
        "session_id_present": bool((control_result.get("binding") or {}).get("session_id")),
    }


def build_webhook_agent_turn_event_payload(
    *,
    bridge: dict[str, Any],
    agent_turn: dict[str, Any],
) -> dict[str, Any]:
    """Build the redacted event payload for webhook agent turn execution."""

    payload = {
        "kind": "agent_turn",
        "status": str(agent_turn.get("status") or "accepted"),
        "chat_id": str(bridge.get("chat_id") or ""),
        "session_id": str(bridge.get("session_id") or ""),
        "reply_text_present": bool(agent_turn.get("reply_text_present")),
    }
    envelope = agent_turn.get("todo_truth_envelope")
    if isinstance(envelope, dict):
        payload["todo_truth_envelope"] = telegram_todo_truth_envelope_public_summary(
            envelope
        )
    return payload


def _durable_reply_succeeded(result: Any) -> bool:
    """Accept only explicit success from the reply gate's public result shape."""

    if not isinstance(result, dict):
        return False
    if "exit_code" in result and result.get("exit_code") != 0:
        return False
    if result.get("ok") is True:
        return True
    if result.get("exit_code") != 0:
        return False
    output = result.get("output")
    if not isinstance(output, str):
        return False
    try:
        import json

        sent = (json.loads(output) or {}).get("sent")
    except (TypeError, ValueError):
        return False
    return bool(isinstance(sent, dict) and sent.get("ok") is True)


def _has_exact_outbound_sent_evidence(store: Any, *, stable_chat_handle: str, message_id: Any) -> bool:
    """Read only the bounded, content-free delivery fields needed for completion."""

    try:
        history = store.history(limit=50)
    except Exception:
        return False
    return any(
        isinstance(item, dict)
        and item.get("direction") == "outbound"
        and item.get("chat_handle") == stable_chat_handle
        and item.get("source_message_id") == message_id
        and item.get("delivery_status") == "sent"
        for item in history
    )


async def run_durable_webhook_agent_turn_branch(
    *,
    runtime: Any,
    stored_message: dict[str, Any],
    bridge: dict[str, Any],
    raw_chat_id: str,
    store: Any,
    voice_agent_turn: Any,
    recent_attachment_context: dict[str, Any] | None,
    agent_turn_handler: Any,
    build_agent_bridge_request: BuildAgentBridgeRequest,
    deterministic_agent_turn: DeterministicAgentTurn,
    run_agent_turn_async: RunAgentTurnAsync,
    typing_pulse: TypingPulse,
    agent_failure_reply: AgentFailureReply,
    reply_with_gate: ReplyWithGate,
) -> tuple[str, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Run one webhook turn behind the runtime's shared durable lease."""

    coordinator = getattr(runtime, "turn_coordinator", None)
    owner = str(getattr(runtime, "telegram_owner", "") or "").strip()
    if coordinator is None or not owner:
        return "reconciliation_required", bridge, None, None
    try:
        acquired = coordinator.acquire_turn(
            owner=owner,
            stable_chat_handle=str(bridge.get("chat_handle") or ""),
            update_id=stored_message.get("update_id"),
            message_id=stored_message.get("message_id"),
            scope=str(bridge.get("desired_session_scope") or bridge.get("session_scope") or "normal"),
        )
    except Exception:
        return "reconciliation_required", bridge, None, None
    status = str(getattr(acquired, "status", ""))
    if status == "duplicate_completed":
        return "duplicate_completed", bridge, None, None
    if status == "terminal":
        if _has_exact_outbound_sent_evidence(store, stable_chat_handle=str(bridge.get("chat_handle") or ""), message_id=stored_message.get("message_id")):
            return "completed", bridge, None, None
        reply = _deliver_agent_reply(reply_with_gate, bridge["chat_id"], "Diese Nachricht konnte nach einer Unterbrechung nicht sicher wiederhergestellt werden.", bridge.get("source_message_id"))
        return ("completed" if _durable_reply_succeeded(reply) else "reconciliation_required"), bridge, None, reply
    if status == "reply_pending_reconciliation_required":
        sent = _has_exact_outbound_sent_evidence(
            store,
            stable_chat_handle=str(bridge.get("chat_handle") or ""),
            message_id=stored_message.get("message_id"),
        )
        if sent:
            try:
                completed = coordinator.complete_reply_pending_from_outbound_evidence(
                    owner=owner,
                    stable_chat_handle=str(bridge.get("chat_handle") or ""),
                    update_id=stored_message.get("update_id"),
                    message_id=stored_message.get("message_id"),
                    outbound_sent=True,
                )
            except Exception:
                completed = None
            if str(getattr(completed, "status", "")) == "completed_from_outbound_evidence":
                return "duplicate_completed", bridge, None, None
        provider = getattr(runtime, "turn_recovery_provider", None)
        expected_session_id = str(getattr(getattr(acquired, "intake", None), "expected_session_id", "") or "")
        recovery = provider(session_id=expected_session_id, durable_turn_ref=str(getattr(acquired.intake, "id", "") or "")) if callable(provider) else {}
        markers = tuple((recovery or {}).get("markers") or ())
        reply_text = str((recovery or {}).get("assistant_reply") or "")
        if [getattr(marker, "role", "") for marker in markers] == ["user", "assistant"] and reply_text:
            reply = _deliver_agent_reply(reply_with_gate, bridge["chat_id"], reply_text, bridge.get("source_message_id"))
            if _durable_reply_succeeded(reply):
                completed = coordinator.complete_reply_pending_from_outbound_evidence(
                    owner=owner, stable_chat_handle=str(bridge.get("chat_handle") or ""),
                    update_id=stored_message.get("update_id"), message_id=stored_message.get("message_id"), outbound_sent=True,
                )
                if str(getattr(completed, "status", "")) == "completed_from_outbound_evidence":
                    return "completed", bridge, {"status": "recovered"}, reply
        return "reconciliation_required", bridge, None, None
    if status == "running_reconciliation_required":
        provider = getattr(runtime, "turn_recovery_provider", None)
        expected_session_id = str(getattr(getattr(acquired, "intake", None), "expected_session_id", "") or "")
        recovery = provider(session_id=expected_session_id, durable_turn_ref=str(getattr(acquired.intake, "id", "") or "")) if callable(provider) else {}
        try:
            reconciled = coordinator.reconcile_crashed_turn(
                owner=owner,
                stable_chat_handle=str(bridge.get("chat_handle") or ""),
                update_id=stored_message.get("update_id"),
                message_id=stored_message.get("message_id"),
                markers=tuple((recovery or {}).get("markers") or ()),
            )
        except Exception:
            return "reconciliation_required", bridge, None, None
        if str(getattr(reconciled, "status", "")) == "reconciled_reply_pending":
            reply_text = str((recovery or {}).get("assistant_reply") or "")
            if reply_text:
                reply = _deliver_agent_reply(reply_with_gate, bridge["chat_id"], reply_text, bridge.get("source_message_id"))
                if _durable_reply_succeeded(reply):
                    completed = coordinator.complete_reply_pending_from_outbound_evidence(
                        owner=owner,
                        stable_chat_handle=str(bridge.get("chat_handle") or ""),
                        update_id=stored_message.get("update_id"),
                        message_id=stored_message.get("message_id"),
                        outbound_sent=True,
                    )
                    if str(getattr(completed, "status", "")) == "completed_from_outbound_evidence":
                        return "completed", bridge, {"status": "recovered"}, reply
            return "reconciliation_required", bridge, None, None
        if str(getattr(reconciled, "status", "")) == "reconciled_indeterminate":
            if _has_exact_outbound_sent_evidence(store, stable_chat_handle=str(bridge.get("chat_handle") or ""), message_id=stored_message.get("message_id")):
                return "completed", bridge, None, None
            reply = _deliver_agent_reply(
                reply_with_gate,
                bridge["chat_id"],
                "Diese Nachricht konnte nach einer Unterbrechung nicht sicher wiederhergestellt werden.",
                bridge.get("source_message_id"),
            )
            return ("completed" if _durable_reply_succeeded(reply) else "reconciliation_required"), bridge, None, reply
        return "reconciliation_required", bridge, None, None
    if status in {"lease_busy", "lease_busy_local_active", "lease_retry", "retry_not_due"}:
        return "lease_retry", bridge, None, None
    if status != "acquired" or getattr(acquired, "lease", None) is None or getattr(acquired, "intake", None) is None:
        return "reconciliation_required", bridge, None, None

    lease = acquired.lease
    completed = False
    reply_persisted = False
    renewal: TelegramTurnRenewalPulse | None = None
    typing_stop = None
    typing_task = None
    final_bridge = dict(bridge)
    try:
        session_id = str(getattr(acquired.intake, "expected_session_id", "") or "")
        if not session_id:
            return "reconciliation_required", final_bridge, None, None
        final_bridge = build_agent_bridge_request(
            {**stored_message, "intake_status": "ready"},
            session_binding={"session_id": session_id, "last_selected_scope": bridge.get("desired_session_scope") or "normal"},
            raw_chat_id=raw_chat_id,
            voice_agent_turn=voice_agent_turn,
            recent_attachment_context=recent_attachment_context,
        )
        final_bridge["durable_turn_ref"] = str(getattr(acquired.intake, "id", "") or "")
        agent_turn = deterministic_agent_turn(final_bridge)
        if agent_turn is None and final_bridge.get("ready_for_agent") and callable(agent_turn_handler):
            renewal = TelegramTurnRenewalPulse(coordinator=coordinator, lease=lease)
            if not renewal.start():
                return "reconciliation_required", final_bridge, None, None
            typing_stop, typing_task = await typing_pulse(final_bridge["chat_id"], store=store)
            agent_turn = await run_agent_turn_async(agent_turn_handler, final_bridge)
            lease = renewal.stop()
            if renewal.failed:
                return "reconciliation_required", final_bridge, agent_turn, None
        if not isinstance(agent_turn, dict) or str(agent_turn.get("status") or "") != "accepted":
            return "reconciliation_required", final_bridge, agent_turn, None
        durable_event = build_webhook_agent_turn_event_payload(bridge=final_bridge, agent_turn=agent_turn)
        durable_event.pop("session_id", None)
        durable_event["session_id_present"] = bool(session_id)
        store.append_event(**durable_event)
        coordinator.mark_reply_persisted(lease)
        reply_persisted = True
        reply_text = format_agent_turn_reply(agent_turn, failure_reply=agent_failure_reply)
        if not reply_text:
            return "reconciliation_required", final_bridge, agent_turn, None
        reply = _deliver_agent_reply(
            reply_with_gate,
            final_bridge["chat_id"],
            reply_text,
            final_bridge.get("source_message_id"),
            agent_turn.get("todo_transactions"),
            agent_turn.get("todo_truth_envelope"),
        )
        if not _durable_reply_succeeded(reply):
            return "reconciliation_required", final_bridge, agent_turn, reply
        coordinator.complete_and_release(lease)
        completed = True
        return "completed", final_bridge, agent_turn, reply
    except Exception:
        return "reconciliation_required", final_bridge, None, None
    finally:
        if typing_stop is not None:
            typing_stop.set()
        if typing_task is not None:
            try:
                await asyncio.wait_for(typing_task, timeout=0.5)
            except asyncio.TimeoutError:
                typing_task.cancel()
        if renewal is not None:
            lease = renewal.stop()
        if not completed and not reply_persisted:
            try:
                coordinator.release_turn(lease)
            except Exception:
                pass


def run_webhook_control_command_branch(
    *,
    message: dict[str, Any],
    stored_message: dict[str, Any],
    raw_chat_id: str,
    sessions: Any,
    session_creator: Any,
    reply_handler: ReplyWithGate,
    store: Any,
    pin_store: Any,
    memory_manager: Any,
    memory_vector: Any,
    memory_owner: str | None,
    project_registry_path: Any,
    detect_control_command: DetectControlCommand,
    handle_control_command: HandleControlCommand,
    telegram_rollover_runtime: Any = None,
    rollover_session_creator: Any = None,
    rollover_security_validator: Any = None,
) -> dict[str, Any] | None:
    """Run the webhook control-command branch and append its redacted event."""

    control_result = handle_control_command(
        detect_control_command(stored_message),
        message=stored_message,
        raw_chat_id=raw_chat_id,
        sessions=sessions,
        session_creator=session_creator,
        reply_handler=reply_handler,
        store=store,
        pin_store=pin_store,
        memory_manager=memory_manager,
        memory_vector=memory_vector,
        memory_owner=memory_owner,
        project_registry_path=project_registry_path,
        telegram_rollover_runtime=telegram_rollover_runtime,
        rollover_session_creator=rollover_session_creator,
        rollover_security_validator=rollover_security_validator,
    )
    if control_result is None:
        return None

    store.append_event(
        **build_webhook_control_command_event_payload(
            message=message,
            control_result=control_result,
        )
    )
    return control_result


async def run_webhook_agent_turn_branch(
    *,
    stored_message: dict[str, Any],
    bridge: dict[str, Any],
    raw_chat_id: str,
    sessions: Any,
    session_creator: Any,
    store: Any,
    voice_agent_turn: Any,
    recent_attachment_context: dict[str, Any] | None,
    agent_turn_handler: Any,
    build_agent_bridge_request: BuildAgentBridgeRequest,
    deterministic_agent_turn: DeterministicAgentTurn,
    run_agent_turn_async: RunAgentTurnAsync,
    typing_pulse: TypingPulse,
    agent_failure_reply: AgentFailureReply,
    reply_with_gate: ReplyWithGate,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Run the webhook agent-turn branch while keeping transport helpers injected."""

    session_binding = None
    if bridge["ready_for_agent"]:
        session_binding = sessions.bind_chat(
            chat_id=bridge["chat_id"],
            session_alias=bridge["session_alias"],
            recommended_session_name=bridge["recommended_session_name"],
            scope=str(bridge.get("desired_session_scope") or "normal"),
            creator=session_creator,
        )
    final_bridge = build_agent_bridge_request(
        stored_message,
        session_binding=session_binding,
        raw_chat_id=raw_chat_id,
        voice_agent_turn=voice_agent_turn,
        recent_attachment_context=recent_attachment_context,
    )
    typing_stop = None
    typing_task = None
    reply_result = None
    agent_turn = deterministic_agent_turn(final_bridge)
    if agent_turn is None and final_bridge["ready_for_agent"] and callable(agent_turn_handler):
        typing_stop, typing_task = await typing_pulse(final_bridge["chat_id"], store=store)
    try:
        if agent_turn is None:
            agent_turn = await run_agent_turn_async(agent_turn_handler, final_bridge)
        if agent_turn is not None:
            store.append_event(
                **build_webhook_agent_turn_event_payload(
                    bridge=final_bridge,
                    agent_turn=agent_turn,
                )
            )
            reply_text = format_agent_turn_reply(agent_turn, failure_reply=agent_failure_reply)
            if reply_text:
                reply_result = _deliver_agent_reply(
                    reply_with_gate,
                    final_bridge["chat_id"],
                    reply_text,
                    final_bridge.get("source_message_id"),
                    agent_turn.get("todo_transactions"),
                    agent_turn.get("todo_truth_envelope"),
                )
    finally:
        if typing_stop is not None:
            typing_stop.set()
        if typing_task is not None:
            try:
                await asyncio.wait_for(typing_task, timeout=0.5)
            except asyncio.TimeoutError:
                typing_task.cancel()
    return final_bridge, agent_turn, reply_result


def run_webhook_project_intake_branch(
    *,
    message: dict[str, Any],
    stored_message: dict[str, Any],
    data_dir: Any,
    store: Any,
    sessions: Any,
    project_registry_path: Any,
    build_project_intake_preview: BuildProjectIntakePreview,
    format_project_intake_reply: FormatProjectIntakeReply,
    reply_with_gate: ReplyWithGate,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Run the text-only project-intake webhook branch behind injected helpers."""

    if stored_message.get("kind") != "text":
        return None, None

    project_intake = build_project_intake_preview(
        data_dir=data_dir,
        store=store,
        sessions=sessions,
        chat_id=str(message.get("chat_id") or ""),
        text=str(stored_message.get("text") or ""),
        source_message_id=message.get("message_id"),
        project_registry_path=project_registry_path,
    )
    if project_intake is None:
        return None, None

    reply_result = reply_with_gate(
        str(message.get("chat_id") or ""),
        format_project_intake_reply(project_intake),
        source_message_id=message.get("message_id"),
    )
    return project_intake, reply_result


def run_webhook_attachment_export_branch(
    *,
    message: dict[str, Any],
    stored_message: dict[str, Any],
    data_dir: Any,
    store: Any,
    execute_attachment_export: ExecuteAttachmentExport,
    document_reply_with_gate: DocumentReplyWithGate,
    reply_with_gate: ReplyWithGate,
    format_export_reply: FormatExportReply,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Run the text-only recent attachment export branch behind injected helpers."""

    if stored_message.get("kind") != "text":
        return None, None

    export_plan = execute_attachment_export(
        data_dir=data_dir,
        store=store,
        chat_id=str(message.get("chat_id") or ""),
        text=str(stored_message.get("text") or ""),
    )
    if export_plan is None:
        return None, None

    store.append_event(
        **build_webhook_export_plan_event_payload(
            message=message,
            export_plan=export_plan,
        )
    )
    if str(export_plan.get("status") or "") == "exported":
        reply_result = document_reply_with_gate(
            str(message.get("chat_id") or ""),
            str(export_plan.get("output_path") or ""),
            str(export_plan.get("output_filename") or "telegram-export.pdf"),
            format_export_reply({**export_plan, "status": "sent"}),
            source_message_id=message.get("message_id"),
        )
        if (reply_result or {}).get("exit_code") == 0:
            export_plan = {**export_plan, "status": "sent"}
            store.append_event(
                **build_webhook_export_delivery_event_payload(
                    message=message,
                    export_plan=export_plan,
                )
            )
        else:
            export_plan = {
                **export_plan,
                "reason": f"document_delivery_failed:{str((reply_result or {}).get('error') or '')[:80]}",
            }
    else:
        reply_result = reply_with_gate(
            str(message.get("chat_id") or ""),
            format_export_reply(export_plan),
            source_message_id=message.get("message_id"),
        )
    return export_plan, reply_result


def run_webhook_attachment_branch(
    *,
    message: dict[str, Any],
    stored: dict[str, Any],
    data_dir: Any,
    store: Any,
    attachment_bytes_provider: Any,
    memory_manager: Any,
    memory_vector: Any,
    memory_owner: str | None,
    run_attachment_pipeline: RunAttachmentPipeline,
    attachment_spool_key: AttachmentValue,
    attachment_family: AttachmentValue,
    attachment_suffix: AttachmentValue,
    execute_memory_auto_write_if_ready: ExecuteMemoryAutoWrite,
    format_attachment_reply: FormatAttachmentReply,
    reply_with_gate: ReplyWithGate,
    execute_nextcloud_auto_transfer_if_ready: ExecuteNextcloudAutoTransfer | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Run the webhook attachment branch and mutate stored when status refreshes."""

    inbox_attachment = run_attachment_pipeline(
        message,
        data_dir=data_dir,
        file_bytes_provider=attachment_bytes_provider,
    )
    if inbox_attachment is None:
        return None, None

    spool_key = attachment_spool_key(stored["message"])
    refreshed = store.update_inbound_status(
        stored["message"],
        universal_inbox_status=str(inbox_attachment.get("status") or "failed"),
        intake_status="universal_inbox_processed"
        if inbox_attachment.get("status") == "processed"
        else str(inbox_attachment.get("status") or "failed"),
    )
    if refreshed is not None:
        stored["message"] = refreshed

    attachment_event = store.append_event(
        **build_webhook_attachment_event_payload(
            message=message,
            inbox_attachment=inbox_attachment,
            attachment_family=attachment_family(stored["message"]),
            attachment_suffix=attachment_suffix(stored["message"]),
            spool_key=spool_key,
        )
    )

    memory_auto_write = execute_memory_auto_write_if_ready(
        data_dir=data_dir,
        store=store,
        chat_id=str(message.get("chat_id") or ""),
        inbox_attachment=inbox_attachment,
        source_message_id=message.get("message_id"),
        memory_manager=memory_manager,
        memory_vector=memory_vector,
        memory_owner=memory_owner,
    )
    if memory_auto_write is not None:
        inbox_attachment = dict(inbox_attachment)
        inbox_attachment["memory_auto_write_status"] = str(memory_auto_write.get("status") or "")
        inbox_attachment["memory_auto_write_reason"] = str(memory_auto_write.get("reason") or "")
        inbox_attachment["memory_auto_writes_performed"] = bool(memory_auto_write.get("writes_performed"))

    nextcloud_transfer = (
        execute_nextcloud_auto_transfer_if_ready(
            data_dir=data_dir,
            store=store,
            chat_id=str(message.get("chat_id") or ""),
            inbox_attachment=inbox_attachment,
            attachment_event=attachment_event,
        )
        if callable(execute_nextcloud_auto_transfer_if_ready)
        else None
    )
    if nextcloud_transfer is not None:
        inbox_attachment = dict(inbox_attachment)
        inbox_attachment["nextcloud_transfer_status"] = str(nextcloud_transfer.get("status") or "")
        inbox_attachment["nextcloud_transfer_reason"] = str(nextcloud_transfer.get("reason") or "")
        inbox_attachment["nextcloud_writes_performed"] = bool(nextcloud_transfer.get("writes_performed"))
        inbox_attachment["nextcloud_verified"] = bool(nextcloud_transfer.get("verified"))

    reply_result = reply_with_gate(
        str(message.get("chat_id") or ""),
        format_attachment_reply(inbox_attachment),
        source_message_id=message.get("message_id"),
    )
    return inbox_attachment, reply_result

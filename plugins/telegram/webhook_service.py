"""Webhook service helpers for Telegram intake."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from plugins.telegram.formatting import format_agent_turn_reply
from src.telegram_todo_truth import telegram_todo_truth_envelope_public_summary


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

    payload: dict[str, Any] = {
        "stored": stored["stored"],
        "message": stored["message"],
        "agent_bridge": agent_bridge,
        "voice_pipeline": voice_pipeline,
        "image_action": image_action,
        "universal_inbox_attachment": universal_inbox_attachment,
    }
    if extra:
        payload.update(extra)
    payload.update(
        {
            "agent_turn": agent_turn,
            "reply": reply,
            "token_value_visible": False,
        }
    )
    return payload


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

    envelope = agent_turn.get("todo_truth_envelope")
    payload = {
        "kind": "agent_turn",
        "status": str(agent_turn.get("status") or "accepted"),
        "chat_id": str(bridge.get("chat_id") or ""),
        "session_id": str(bridge.get("session_id") or ""),
        "reply_text_present": bool(agent_turn.get("reply_text_present")),
    }
    if isinstance(envelope, dict):
        payload["todo_truth_envelope"] = telegram_todo_truth_envelope_public_summary(envelope)
    return payload


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
                envelope = agent_turn.get("todo_truth_envelope")
                if isinstance(envelope, dict):
                    reply_result = reply_with_gate(
                        final_bridge["chat_id"],
                        reply_text,
                        source_message_id=final_bridge.get("source_message_id"),
                        todo_truth_envelope=envelope,
                    )
                else:
                    reply_result = reply_with_gate(
                        final_bridge["chat_id"],
                        reply_text,
                        source_message_id=final_bridge.get("source_message_id"),
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

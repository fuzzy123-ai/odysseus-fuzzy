"""Telegram polling support helpers.

This module handles local agent-turn invocation and public result shaping only.
It must not call Telegram, mutate settings, or persist raw chat identifiers.
"""

from __future__ import annotations

from pathlib import Path
import asyncio
import json
import os
import threading
import urllib.parse
import urllib.request
from typing import Any, Callable

from plugins.telegram.formatting import format_agent_failure_reply, format_agent_turn_reply
from plugins.telegram.stores import (
    TelegramInboxStore,
    TelegramPollingStateStore,
    TelegramPrivacyPinStore,
    TelegramSessionBridgeStore,
)
from src.telegram_todo_truth import telegram_todo_truth_envelope_public_summary
from src.telegram_session_rollover import (
    TelegramRolloverConfig,
    begin_telegram_turn,
    consume_continuity,
    continuity_binding,
    end_telegram_turn,
    execute_telegram_session_rollover,
)


def _run_agent_turn(
    handler: Callable[[dict[str, Any]], Any] | None,
    bridge: dict[str, Any],
) -> dict[str, Any] | None:
    if not callable(handler) or not bridge.get("ready_for_agent"):
        return None
    try:
        result = handler(dict(bridge))
    except Exception as exc:
        return {
            "status": "failed",
            "reply_text": "",
            "reply_text_present": False,
            "error": str(exc)[:240],
        }
    if isinstance(result, dict):
        reply_text = str(result.get("reply_text") or result.get("text") or "")
        status = str(result.get("status") or "accepted")
        todo_truth_envelope = result.get("todo_truth_envelope")
    else:
        reply_text = str(result or "")
        status = "accepted"
        todo_truth_envelope = None
    public = {
        "status": status,
        "reply_text": reply_text,
        "reply_text_present": bool(reply_text.strip()),
    }
    if isinstance(todo_truth_envelope, dict):
        public["todo_truth_envelope"] = todo_truth_envelope
    if result.get("telegram_rollover_continuity_used") is True:
        public["telegram_rollover_continuity_used"] = True
    return public


def deterministic_telegram_agent_turn(bridge: dict[str, Any]) -> dict[str, Any] | None:
    """Answer trusted runtime capability questions from diagnostics, not model memory."""

    if not bridge.get("ready_for_agent"):
        return None
    prompt = str(bridge.get("prompt") or bridge.get("persisted_prompt") or "")
    try:
        from routes.chat_helpers import build_deterministic_capability_self_report

        reply_text = build_deterministic_capability_self_report(prompt)
    except Exception:
        reply_text = None
    if not reply_text:
        return None
    return {
        "status": "accepted",
        "reply_text": reply_text,
        "reply_text_present": True,
        "source": "tool_capability_diagnostics",
    }


def telegram_typing_keepalive_seconds() -> float:
    try:
        value = float((os.getenv("TELEGRAM_TYPING_KEEPALIVE_SECONDS") or "").strip())
    except ValueError:
        value = 4.0
    return max(0.05, min(value or 4.0, 5.0))


class TelegramTypingPulse:
    def __init__(
        self,
        *,
        chat_id: str,
        send_typing_indicator: Callable[..., Any],
        store: TelegramInboxStore | None = None,
    ) -> None:
        self.chat_id = str(chat_id or "")
        self.send_typing_indicator = send_typing_indicator
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "TelegramTypingPulse":
        if not self.chat_id or not callable(self.send_typing_indicator):
            return self
        self._thread = threading.Thread(target=self._run, name="telegram-typing-pulse", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def _run(self) -> None:
        interval = telegram_typing_keepalive_seconds()
        while not self._stop.is_set():
            try:
                self.send_typing_indicator(self.chat_id, store=self.store)
            except Exception:
                pass
            self._stop.wait(interval)


async def _run_agent_turn_async(
    handler: Callable[[dict[str, Any]], Any] | None,
    bridge: dict[str, Any],
) -> dict[str, Any] | None:
    if not callable(handler) or not bridge.get("ready_for_agent"):
        return None
    try:
        result = await asyncio.to_thread(handler, dict(bridge))
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as exc:
        return {
            "status": "failed",
            "reply_text": "",
            "reply_text_present": False,
            "error": str(exc)[:240],
        }
    if isinstance(result, dict):
        reply_text = str(result.get("reply_text") or result.get("text") or "")
        status = str(result.get("status") or "accepted")
        todo_truth_envelope = result.get("todo_truth_envelope")
    else:
        reply_text = str(result or "")
        status = "accepted"
        todo_truth_envelope = None
    public = {
        "status": status,
        "reply_text": reply_text,
        "reply_text_present": bool(reply_text.strip()),
    }
    if isinstance(todo_truth_envelope, dict):
        public["todo_truth_envelope"] = todo_truth_envelope
    return public


def _public_agent_turn_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    envelope = result.get("todo_truth_envelope")
    public = {
        key: value
        for key, value in result.items()
        if key not in {"reply_text", "todo_truth_envelope"}
    }
    if isinstance(envelope, dict):
        public["todo_truth_envelope"] = telegram_todo_truth_envelope_public_summary(envelope)
    public["reply_text_value_visible"] = False
    return public


def _public_reply_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    output = result.get("output")
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return result


def _reply_result_telegram_message_id(result: dict[str, Any] | None) -> int | None:
    public = _public_reply_result(result)
    if not isinstance(public, dict):
        return None
    sent = public.get("sent")
    if not isinstance(sent, dict):
        sent = public
    candidate = sent.get("telegram_message_id")
    if candidate in ("", None):
        ids = sent.get("telegram_message_ids")
        if isinstance(ids, list) and ids:
            candidate = ids[0]
    try:
        value = int(candidate)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _agent_failure_reply(agent_turn: dict[str, Any] | None) -> str:
    return format_agent_failure_reply(agent_turn)


def fetch_telegram_updates(offset: int) -> list[dict[str, Any]]:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise ValueError("telegram token is missing")
    params: dict[str, Any] = {
        "timeout": 0,
        "limit": 50,
        "allowed_updates": json.dumps(["message"]),
    }
    if offset:
        params["offset"] = int(offset)
    url = f"https://api.telegram.org/bot{token}/getUpdates?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=20) as response:  # nosec: token-gated Telegram API endpoint
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise ValueError(str(payload.get("description") or "telegram getUpdates failed"))
    result = payload.get("result") or []
    if not isinstance(result, list):
        raise ValueError("telegram getUpdates returned an invalid result")
    return result


def run_telegram_polling_cycle_impl(
    *,
    data_dir: str | Path,
    fetch_updates: Callable[[int], list[dict[str, Any]]] | None = None,
    session_creator: Callable[..., Any] | None = None,
    session_archiver: Callable[[str], Any] | None = None,
    agent_turn_handler: Callable[[dict[str, Any]], Any] | None = None,
    voice_stt_provider: Callable[[str], str] | None = None,
    voice_bytes_provider: Callable[..., bytes] | None = None,
    image_bytes_provider: Callable[[str], bytes] | None = None,
    attachment_bytes_provider: Callable[..., bytes] | None = None,
    image_worker_client: Any | None = None,
    reply_handler: Callable[[str, str, int | None], dict[str, Any]] | None = None,
    document_reply_handler: Callable[[str, str, str, str, int | None], dict[str, Any]] | None = None,
    memory_manager: Any | None = None,
    memory_vector: Any | None = None,
    memory_owner: str | None = None,
    project_registry_path: str | Path | None = None,
    polling_enabled: Callable[[str], bool],
    parse_update: Callable[[dict[str, Any]], dict[str, Any]],
    control_command: Callable[[dict[str, Any]], str],
    handle_control_command: Callable[..., dict[str, Any] | None],
    build_live_voice_stt_provider: Callable[..., Callable[[str], str] | None],
    run_voice_pipeline: Callable[..., tuple[Any | None, dict[str, Any] | None]],
    run_image_action: Callable[..., Any],
    run_attachment_pipeline: Callable[..., dict[str, Any] | None],
    attachment_spool_key: Callable[[dict[str, Any]], str],
    attachment_family: Callable[[dict[str, Any]], str],
    attachment_suffix: Callable[[dict[str, Any]], str],
    format_attachment_reply: Callable[[dict[str, Any]], str],
    execute_attachment_export: Callable[..., dict[str, Any] | None],
    format_attachment_export_reply: Callable[[dict[str, Any]], str],
    build_project_intake_preview: Callable[..., dict[str, Any] | None],
    format_project_intake_reply: Callable[[dict[str, Any]], str],
    build_recent_attachment_context: Callable[..., dict[str, Any] | None],
    build_agent_bridge_request: Callable[..., dict[str, Any]],
    send_typing_indicator: Callable[..., Any],
    execute_memory_auto_write: Callable[..., dict[str, Any] | None] | None = None,
    execute_nextcloud_auto_transfer: Callable[..., dict[str, Any] | None] | None = None,
    rollover_now: Any | None = None,
) -> dict[str, Any]:
    store = TelegramInboxStore(data_dir)
    polling = TelegramPollingStateStore(data_dir)
    sessions = TelegramSessionBridgeStore(data_dir)
    privacy_pins = TelegramPrivacyPinStore(data_dir)
    if not polling_enabled("TELEGRAM_POLLING_ENABLED"):
        polling.record(status="polling_disabled", offset=polling.get_offset())
        return {"ok": False, "status": "polling_disabled", "processed": 0, "offset": polling.get_offset()}
    loader = fetch_updates or fetch_telegram_updates
    offset = polling.get_offset()
    processed = 0
    invalid = 0
    agent_turns = 0
    replies = 0
    pending_retries = 0
    control_commands = 0
    session_rollovers = 0
    hold_offset_for_retry = False
    last_update_id = offset - 1 if offset else 0
    try:
        updates = loader(offset)
    except Exception as exc:
        polling.record(status="poll_failed", offset=offset, error=str(exc)[:240])
        return {"ok": False, "status": "poll_failed", "processed": 0, "offset": offset, "error": str(exc)}
    for update in updates:
        last_update_id = max(last_update_id, int(update.get("update_id") or 0))
        try:
            message = parse_update(update)
        except ValueError as exc:
            invalid += 1
            store.append_event(kind="invalid_update", status="invalid_update", error=str(exc)[:120])
            continue
        stored = store.append_inbound(message)
        should_process = bool(stored["stored"]) or bool(stored.get("retry_pending_voice"))
        if should_process:
            control_result = handle_control_command(
                control_command(stored["message"]),
                message=stored["message"],
                raw_chat_id=str(message.get("chat_id") or ""),
                sessions=sessions,
                session_creator=session_creator,
                reply_handler=reply_handler,
                store=store,
                pin_store=privacy_pins,
                memory_manager=memory_manager,
                memory_vector=memory_vector,
                memory_owner=memory_owner,
                project_registry_path=project_registry_path,
            )
            if control_result is not None:
                control_commands += 1
                if control_result.get("reply") is not None:
                    replies += 1
                store.append_event(
                    kind="control_command",
                    status=str(control_result.get("status") or "handled"),
                    chat_id=str(message.get("chat_id") or ""),
                    session_id=str((control_result.get("binding") or {}).get("session_id") or ""),
                    command=str(control_result.get("command") or ""),
                )
                processed += 1
                continue
            message_voice_stt_provider = voice_stt_provider or build_live_voice_stt_provider(
                message,
                voice_bytes_provider=voice_bytes_provider,
            )
            voice_agent_turn, _voice_pipeline = run_voice_pipeline(
                stored["message"],
                stt_provider=message_voice_stt_provider,
            )
            run_image_action(
                stored["message"],
                enabled=polling_enabled("TELEGRAM_IMAGE_ACTIONS_ENABLED"),
                image_bytes_provider=image_bytes_provider,
                worker_client=image_worker_client,
            )
            inbox_attachment = run_attachment_pipeline(
                message,
                data_dir=data_dir,
                file_bytes_provider=attachment_bytes_provider,
            )
            if inbox_attachment is not None:
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
                    kind="universal_inbox_attachment",
                    status=str(inbox_attachment.get("status") or "failed"),
                    chat_id=str(message.get("chat_id") or ""),
                    update_id=message.get("update_id"),
                    message_id=message.get("message_id"),
                    universal_inbox_status=str(inbox_attachment.get("universal_inbox_status") or ""),
                    memory_write_intent_status=str(inbox_attachment.get("memory_write_intent_status") or ""),
                    attachment_family=attachment_family(stored["message"]),
                    attachment_suffix=attachment_suffix(stored["message"]),
                    discovered_count=int(inbox_attachment.get("discovered_count") or 0),
                    processable_count=int(inbox_attachment.get("processable_count") or 0),
                    queue_status=str(inbox_attachment.get("queue_status") or ""),
                    queue_concurrency=int(inbox_attachment.get("queue_concurrency") or 1),
                    maintenance_model_ref=str(inbox_attachment.get("maintenance_model_ref") or ""),
                    maintenance_provider=str(inbox_attachment.get("maintenance_provider") or ""),
                    maintenance_action=str(inbox_attachment.get("maintenance_action") or ""),
                    maintenance_review_required=bool(inbox_attachment.get("maintenance_review_required")),
                    review_reason_count=int(inbox_attachment.get("review_reason_count") or 0),
                    no_go_reason_count=int(inbox_attachment.get("no_go_reason_count") or 0),
                    extraction_status=str(inbox_attachment.get("extraction_status") or ""),
                    extraction_warning_codes=tuple(inbox_attachment.get("extraction_warning_codes") or ()),
                    memory_records_planned=int(inbox_attachment.get("memory_records_planned") or 0),
                    raptorgraph_events_planned=int(inbox_attachment.get("raptorgraph_events_planned") or 0),
                    spool_key=spool_key,
                    raw_content_visible=False,
                    raw_identifiers_visible=False,
                    filename_visible=False,
                )
                nextcloud_transfer = None
                memory_auto_write = None
                if callable(execute_memory_auto_write):
                    memory_auto_write = execute_memory_auto_write(
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
                if callable(execute_nextcloud_auto_transfer):
                    nextcloud_transfer = execute_nextcloud_auto_transfer(
                        data_dir=data_dir,
                        store=store,
                        chat_id=str(message.get("chat_id") or ""),
                        inbox_attachment=inbox_attachment,
                        attachment_event=attachment_event,
                    )
                    if nextcloud_transfer is not None:
                        inbox_attachment = dict(inbox_attachment)
                        inbox_attachment["nextcloud_transfer_status"] = str(nextcloud_transfer.get("status") or "")
                        inbox_attachment["nextcloud_transfer_reason"] = str(nextcloud_transfer.get("reason") or "")
                        inbox_attachment["nextcloud_writes_performed"] = bool(nextcloud_transfer.get("writes_performed"))
                        inbox_attachment["nextcloud_verified"] = bool(nextcloud_transfer.get("verified"))
                if reply_handler is not None:
                    reply_handler(
                        str(message.get("chat_id") or ""),
                        format_attachment_reply(inbox_attachment),
                        message.get("message_id"),
                    )
                    replies += 1
            if _voice_pipeline is not None:
                stt_status = str((_voice_pipeline.get("stt") or {}).get("status") or "")
                stt_reason = str((_voice_pipeline.get("stt") or {}).get("reason") or "")
                if voice_agent_turn is not None and voice_agent_turn.ready_for_agent:
                    refreshed = store.update_inbound_status(
                        stored["message"],
                        transcript_status="transcribed",
                        voice_status="transcribed",
                        intake_status="ready",
                    )
                    if refreshed is not None:
                        stored["message"] = refreshed
                elif stt_status == "pending_stt" or stt_reason in {"stt_provider_failed", "empty_transcript"}:
                    pending_retries += 1
                    hold_offset_for_retry = True
                    store.append_event(
                        kind="voice_retry",
                        status="pending_stt_retry_scheduled",
                        chat_id=str(message.get("chat_id") or ""),
                        update_id=message.get("update_id"),
                        message_id=message.get("message_id"),
                    )
            if stored["message"].get("kind") == "text":
                export_plan = execute_attachment_export(
                    data_dir=data_dir,
                    store=store,
                    chat_id=str(message.get("chat_id") or ""),
                    text=str(stored["message"].get("text") or ""),
                )
                if export_plan is not None:
                    store.append_event(
                        kind="universal_inbox_export_plan",
                        status=str(export_plan.get("status") or "blocked"),
                        chat_id=str(message.get("chat_id") or ""),
                        update_id=message.get("update_id"),
                        message_id=message.get("message_id"),
                        target_format=str(export_plan.get("target_format") or ""),
                        action=str(export_plan.get("action") or ""),
                        required_tool=str(export_plan.get("required_tool") or ""),
                        bytes_written=int(export_plan.get("bytes_written") or 0),
                        delivery_ready=bool(export_plan.get("delivery_ready")),
                        raw_content_visible=False,
                        raw_identifiers_visible=False,
                        filename_visible=False,
                    )
                    if str(export_plan.get("status") or "") == "exported" and document_reply_handler is not None:
                        try:
                            document_sent = document_reply_handler(
                                str(message.get("chat_id") or ""),
                                str(export_plan.get("output_path") or ""),
                                str(export_plan.get("output_filename") or "telegram-export.pdf"),
                                format_attachment_export_reply({**export_plan, "status": "sent"}),
                                message.get("message_id"),
                            )
                            delivered = bool(document_sent.get("ok", True))
                            export_plan = {
                                **export_plan,
                                "status": "sent" if delivered else "exported",
                                "document_delivery": _public_reply_result(document_sent),
                            }
                            store.append_event(
                                kind="universal_inbox_export_delivery",
                                status="sent" if delivered else "failed",
                                chat_id=str(message.get("chat_id") or ""),
                                update_id=message.get("update_id"),
                                message_id=message.get("message_id"),
                                target_format=str(export_plan.get("target_format") or ""),
                                bytes_written=int(export_plan.get("bytes_written") or 0),
                                raw_content_visible=False,
                                raw_identifiers_visible=False,
                                filename_visible=False,
                                host_paths_visible=False,
                            )
                            replies += 1
                        except Exception as exc:
                            export_plan = {**export_plan, "status": "exported", "reason": f"document_delivery_failed:{str(exc)[:80]}"}
                    if reply_handler is not None:
                        if str(export_plan.get("status") or "") != "sent":
                            reply_handler(
                                str(message.get("chat_id") or ""),
                                format_attachment_export_reply(export_plan),
                                message.get("message_id"),
                            )
                            replies += 1
                    processed += 1
                    continue
                project_intake = build_project_intake_preview(
                    data_dir=data_dir,
                    store=store,
                    sessions=sessions,
                    chat_id=str(message.get("chat_id") or ""),
                    text=str(stored["message"].get("text") or ""),
                    source_message_id=message.get("message_id"),
                    project_registry_path=project_registry_path,
                )
                if project_intake is not None:
                    if reply_handler is not None:
                        reply_handler(
                            str(message.get("chat_id") or ""),
                            format_project_intake_reply(project_intake),
                            message.get("message_id"),
                        )
                        replies += 1
                    processed += 1
                    continue
            recent_attachment_context = build_recent_attachment_context(
                data_dir=data_dir,
                store=store,
                chat_id=str(message.get("chat_id") or ""),
            ) if stored["message"].get("kind") == "text" else None
            bridge = build_agent_bridge_request(
                stored["message"],
                raw_chat_id=str(message.get("chat_id") or ""),
                voice_agent_turn=voice_agent_turn,
                recent_attachment_context=recent_attachment_context,
            )
            if bridge["ready_for_agent"]:
                desired_scope = str(bridge.get("desired_session_scope") or "normal")
                rollover = execute_telegram_session_rollover(
                    store=sessions,
                    chat_id=bridge["chat_id"],
                    scope=desired_scope,
                    creator=session_creator,
                    archiver=session_archiver,
                    config=TelegramRolloverConfig.from_environment(),
                    now=rollover_now,
                )
                if rollover.get("status") in {"rolled_over", "rolled_over_archive_pending"}:
                    session_rollovers += 1
                if rollover.get("status") not in {"disabled", "not_bound", "already_current"}:
                    store.append_event(
                        kind="session_rollover",
                        status=str(rollover.get("status") or "unknown"),
                        chat_id=bridge["chat_id"],
                        scope=desired_scope,
                        rollover_day=str(rollover.get("rollover_day") or ""),
                        session_ref=str(rollover.get("session_ref") or ""),
                        previous_session_ref=str(rollover.get("previous_session_ref") or ""),
                        raw_content_visible=False,
                        raw_identifiers_visible=False,
                    )
                binding = sessions.bind_chat(
                    chat_id=bridge["chat_id"],
                    session_alias=bridge["session_alias"],
                    recommended_session_name=bridge["recommended_session_name"],
                    scope=desired_scope,
                    creator=session_creator,
                )
                continuity = continuity_binding(
                    sessions,
                    bridge["chat_id"],
                    desired_scope,
                )
                if continuity is not None:
                    binding["telegram_rollover_continuity"] = continuity
                bridge = build_agent_bridge_request(
                    stored["message"],
                    session_binding=binding,
                    raw_chat_id=str(message.get("chat_id") or ""),
                    voice_agent_turn=voice_agent_turn,
                    recent_attachment_context=recent_attachment_context,
                )
                store.append_event(
                    kind="session_bridge",
                    status="bound" if binding.get("session_id") else "pending_bridge",
                    chat_id=bridge["chat_id"],
                    session_id=binding.get("session_id") or "",
                )
                agent_turn = deterministic_telegram_agent_turn(bridge)
                typing_pulse = TelegramTypingPulse(
                    chat_id=bridge["chat_id"],
                    send_typing_indicator=send_typing_indicator,
                    store=store,
                ).start() if agent_turn is None and callable(agent_turn_handler) else None
                begin_telegram_turn(
                    data_dir,
                    bridge["chat_id"],
                    desired_scope,
                )
                try:
                    if agent_turn is None:
                        agent_turn = _run_agent_turn(agent_turn_handler, bridge)
                    if agent_turn is not None:
                        agent_turns += 1
                        if agent_turn.get("telegram_rollover_continuity_used") is True:
                            consume_continuity(
                                sessions,
                                bridge["chat_id"],
                                desired_scope,
                            )
                        store.append_event(
                            kind="agent_turn",
                            status=str(agent_turn.get("status") or "accepted"),
                            chat_id=bridge["chat_id"],
                            session_id=bridge.get("session_id") or "",
                            reply_text_present=bool(agent_turn.get("reply_text_present")),
                        )
                        reply_text = format_agent_turn_reply(agent_turn, failure_reply=_agent_failure_reply)
                        if reply_text and reply_handler is not None:
                            envelope = agent_turn.get("todo_truth_envelope")
                            if isinstance(envelope, dict):
                                reply_handler(
                                    bridge["chat_id"],
                                    reply_text,
                                    bridge.get("source_message_id"),
                                    todo_truth_envelope=envelope,
                                )
                            else:
                                reply_handler(
                                    bridge["chat_id"],
                                    reply_text,
                                    bridge.get("source_message_id"),
                                )
                            replies += 1
                finally:
                    end_telegram_turn(data_dir, bridge["chat_id"], desired_scope)
                    if typing_pulse is not None:
                        typing_pulse.stop()
            processed += 1
    next_offset = offset if hold_offset_for_retry else (last_update_id + 1 if last_update_id else offset)
    polling.record(
        status="poll_ok",
        offset=next_offset,
        processed=processed,
        invalid=invalid,
        agent_turns=agent_turns,
        replies=replies,
        pending_retries=pending_retries,
        control_commands=control_commands,
        session_rollovers=session_rollovers,
        last_update_id=last_update_id,
    )
    return {
        "ok": True,
        "status": "poll_ok",
        "processed": processed,
        "invalid": invalid,
        "agent_turns": agent_turns,
        "replies": replies,
        "pending_retries": pending_retries,
        "control_commands": control_commands,
        "session_rollovers": session_rollovers,
        "offset": next_offset,
    }

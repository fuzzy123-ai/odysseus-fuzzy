"""Control-command service helpers for Telegram."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping

from src.agent_task_ledger import read_task_records, record_task_event
from plugins.telegram.formatting import (
    format_agenda_for_telegram,
    format_agent_task_action_reply,
    format_agent_task_help_reply,
    format_agent_task_missing_reply,
    format_agent_task_status_reply,
    format_agent_task_unknown_command_reply,
    format_calendar_command_error_reply,
    format_calendar_readiness_for_telegram,
    format_calendar_unknown_command_reply,
    format_calendar_write_for_telegram,
    format_new_chat_reply,
    format_project_intake_apply_result,
    format_project_intake_hold_reply,
    format_universal_inbox_memory_review_missing_reply,
    format_universal_inbox_memory_write_reply,
    format_universal_inbox_review_missing_reply,
    format_universal_inbox_transfer_confirm_reply,
)

ReplyHandler = Callable[..., dict[str, Any] | None]
BuildAgentBridgeRequest = Callable[..., dict[str, Any]]
SetDsgvoMode = Callable[[bool], dict[str, Any]]
DsgvoModeActive = Callable[[], bool]
DsgvoReplyText = Callable[[str, dict[str, Any] | None], str]
SyncDsgvoPinState = Callable[..., dict[str, Any]]
BuildCalendarReadiness = Callable[..., dict[str, Any]]
BuildAgendaPacket = Callable[..., dict[str, Any]]
WriteReminderNote = Callable[..., dict[str, Any]]
WriteTodoDigestSchedule = Callable[..., dict[str, Any]]
BuildUniversalInboxReadiness = Callable[..., dict[str, Any]]
FormatUniversalInboxReadiness = Callable[[dict[str, Any]], str]
FormatUniversalInboxReviewStatus = Callable[[dict[str, Any]], str]
BuildNextcloudTransferDryRun = Callable[..., dict[str, Any]]
FormatNextcloudTransferBlockedReply = Callable[[Mapping[str, Any]], str]
FormatUniversalInboxMemoryReviewStatus = Callable[[dict[str, Any]], str]
ExecuteTelegramMemoryReviewWrite = Callable[..., dict[str, Any]]
ApplyProjectIntakeReview = Callable[..., dict[str, Any]]
FormatProjectIntakeReviewStatus = Callable[[dict[str, Any] | None], str]


def public_agent_task_record(record: dict[str, Any]) -> dict[str, Any]:
    """Build the redacted public view of an agent-task ledger record."""

    return {
        "task_id": str(record.get("task_id") or ""),
        "task_type": str(record.get("task_type") or ""),
        "status": str(record.get("status") or ""),
        "target_ref": str(record.get("target_ref") or ""),
        "progress_percent": int(record.get("progress_percent") or 0),
        "gates_waiting": tuple(str(item) for item in record.get("gates_waiting") or ()),
        "raw_content_visible": False,
    }


def handle_agent_task_control_command(command: str) -> dict[str, Any]:
    """Handle /task control commands against the redacted agent-task ledger."""

    records = read_task_records(limit=5)
    latest = records.get("records", [None])[0] if records.get("records") else None
    if command == "agent_task_help":
        return {
            "status": "agent_task_help",
            "reply_text": format_agent_task_help_reply(),
            "agent_task": {"raw_content_visible": False},
        }
    if command == "agent_task_status":
        if not latest:
            return {
                "status": "agent_task_missing",
                "reply_text": format_agent_task_missing_reply(),
                "agent_task": {"raw_content_visible": False},
            }
        return {
            "status": "agent_task_status",
            "reply_text": format_agent_task_status_reply(latest),
            "agent_task": public_agent_task_record(latest),
        }
    if command not in {"agent_task_pause", "agent_task_resume", "agent_task_cancel"}:
        return {
            "status": "agent_task_unknown_command",
            "reply_text": format_agent_task_unknown_command_reply(),
            "agent_task": {"raw_content_visible": False},
        }
    if not latest:
        return {
            "status": "agent_task_missing",
            "reply_text": format_agent_task_missing_reply(for_action=True),
            "agent_task": {"raw_content_visible": False},
        }
    next_status = {
        "agent_task_pause": "pause_requested",
        "agent_task_resume": "resume_requested",
        "agent_task_cancel": "cancel_requested",
    }[command]
    action_text = {
        "agent_task_pause": "Pause angefordert",
        "agent_task_resume": "Fortsetzen angefordert",
        "agent_task_cancel": "Abbruch angefordert",
    }[command]
    record = record_task_event(
        task_id=str(latest.get("task_id") or ""),
        task_type=str(latest.get("task_type") or "unknown"),
        status=next_status,
        surface="telegram",
        correlation_id=str(latest.get("correlation_id") or ""),
        target_ref=str(latest.get("target_ref") or ""),
        progress_percent=int(latest.get("progress_percent") or 0),
        gates_waiting=tuple(str(item) for item in latest.get("gates_waiting") or ()),
        summary=action_text,
    )
    return {
        "status": next_status,
        "reply_text": format_agent_task_action_reply(action_text, record),
        "agent_task": public_agent_task_record(record),
    }


def handle_dsgvo_control_command(
    command: str,
    *,
    message: dict[str, Any],
    raw_chat_id: str,
    reply_handler: ReplyHandler | None,
    store: Any,
    pin_store: Any,
    set_dsgvo_mode: SetDsgvoMode,
    dsgvo_mode_active: DsgvoModeActive,
    dsgvo_reply_text: DsgvoReplyText,
    sync_dsgvo_pin_state: SyncDsgvoPinState,
    build_agent_bridge_request: BuildAgentBridgeRequest,
) -> dict[str, Any] | None:
    """Handle DSGVO control commands with all stateful helpers injected."""

    if not command.startswith("dsgvo_"):
        return None

    result = None
    if command == "dsgvo_enable":
        result = set_dsgvo_mode(True)
        status = "dsgvo_enabled" if result.get("after") else "dsgvo_enable_failed"
    elif command == "dsgvo_disable":
        result = set_dsgvo_mode(False)
        status = "dsgvo_forced_active" if result.get("forced_active") else "dsgvo_disabled"
    elif command == "dsgvo_toggle":
        result = set_dsgvo_mode(not dsgvo_mode_active())
        if result.get("forced_active"):
            status = "dsgvo_forced_active"
        else:
            status = "dsgvo_enabled" if result.get("after") else "dsgvo_disabled"
    else:
        status = "dsgvo_status" if command == "dsgvo_status" else "dsgvo_help"

    reply_text = dsgvo_reply_text(command, result)
    bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
    reply_result = None
    if reply_handler is not None and bridge["chat_id"]:
        reply_result = reply_handler(
            bridge["chat_id"],
            reply_text,
            bridge.get("source_message_id"),
        )
    pin_result = sync_dsgvo_pin_state(
        command=command,
        chat_id=bridge["chat_id"],
        result=result,
        reply_result=reply_result,
        store=store,
        pin_store=pin_store,
    )
    return {
        "command": command,
        "status": status,
        "binding": {},
        "reply_text": reply_text,
        "reply": reply_result,
        "dsgvo_mode": bool((result or {}).get("after") if result is not None else dsgvo_mode_active()),
        "pin_status": pin_result.get("status"),
    }


def telegram_control_owner(
    memory_owner: str | None,
    *,
    telegram_owner: str | None = None,
    rollover_enabled: bool = False,
) -> str | None:
    """Resolve the explicit A5 owner without changing legacy callers.

    Calendar/Todo controls historically received ``memory_owner``.  That
    remains the disabled-path compatibility input only; an enabled A5 caller
    must supply a real Telegram owner and may never fall back to the ambient
    ``telegram`` literal.
    """

    if rollover_enabled:
        if not isinstance(telegram_owner, str):
            return None
        owner = telegram_owner.strip().lower()
        return owner if owner and owner != "telegram" else None
    owner = str(memory_owner or "").strip()
    return owner or None


def telegram_command_tail(message: dict[str, Any]) -> str:
    text = str(message.get("text") or "").strip()
    return text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""


def strip_action_word(value: str, words: set[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = text.split(maxsplit=1)
    first = parts[0].lower()
    return parts[1].strip() if first in words and len(parts) > 1 else ("" if first in words else text)


def parse_reminder_tail(tail: str) -> dict[str, str]:
    text = str(tail or "").strip()
    if not text:
        return {}
    if "|" in text:
        due, title = (part.strip() for part in text.split("|", 1))
        return {"due_date": due, "title": title}
    iso_match = re.match(r"^(\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2})?)\s+(.+)$", text)
    if iso_match:
        return {"due_date": iso_match.group(1), "title": iso_match.group(2).strip()}
    time_match = re.match(r"^(\d{1,2}:\d{2})\s+(.+)$", text)
    if time_match:
        return {"due_date": f"today at {time_match.group(1)}", "title": time_match.group(2).strip()}
    return {"title": text}


def parse_reminder_update_tail(tail: str) -> dict[str, str]:
    text = strip_action_word(tail, {"update", "edit", "aendere"}).strip()
    if not text:
        return {}
    parts = text.split(maxsplit=1)
    note_id = parts[0].strip()
    parsed = parse_reminder_tail(parts[1] if len(parts) > 1 else "")
    parsed["note_id"] = note_id
    return parsed


def parse_todo_digest_tail(tail: str) -> dict[str, str]:
    text = strip_action_word(tail, {"digest", "liste", "todo", "todos"}).strip()
    if not text:
        return {"scheduled_time": "09:00", "weekdays": "mo-fr"}
    time_match = re.search(r"\b(\d{1,2}:\d{2})\b", text)
    weekdays_match = re.search(
        r"\b(mo(?:ntag)?|di(?:enstag)?|mi(?:ttwoch)?|do(?:nnerstag)?|fr(?:eitag)?|sa(?:mstag)?|so(?:nntag)?)(?:\s*-\s*(mo(?:ntag)?|di(?:enstag)?|mi(?:ttwoch)?|do(?:nnerstag)?|fr(?:eitag)?|sa(?:mstag)?|so(?:nntag)?))?\b",
        text,
        flags=re.IGNORECASE,
    )
    if weekdays_match and weekdays_match.group(2):
        weekdays = f"{weekdays_match.group(1)}-{weekdays_match.group(2)}"
    elif weekdays_match:
        weekdays = weekdays_match.group(1)
    else:
        weekdays = "mo-fr"
    return {"scheduled_time": time_match.group(1) if time_match else "09:00", "weekdays": weekdays.lower()}


def handle_calendar_control_command(
    command: str,
    *,
    message: dict[str, Any],
    raw_chat_id: str,
    reply_handler: ReplyHandler | None,
    memory_owner: str | None,
    build_agent_bridge_request: BuildAgentBridgeRequest,
    build_calendar_readiness: BuildCalendarReadiness,
    build_agenda_packet: BuildAgendaPacket,
    write_reminder_note: WriteReminderNote,
    write_todo_digest_schedule: WriteTodoDigestSchedule,
) -> dict[str, Any] | None:
    """Handle calendar control commands behind injected calendar service helpers."""

    if not command.startswith("calendar_"):
        return None

    owner = telegram_control_owner(memory_owner)
    bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
    now = datetime.now(UTC).replace(tzinfo=None)
    reply_text = ""
    payload: dict[str, Any] = {}
    status = command
    try:
        if command == "calendar_readiness":
            payload = build_calendar_readiness(owner=owner)
            reply_text = format_calendar_readiness_for_telegram(payload)
            status = "calendar_ready"
        elif command == "calendar_agenda":
            payload = build_agenda_packet(owner=owner, start=now, end=now + timedelta(days=7))
            reply_text = format_agenda_for_telegram(payload)
            status = "calendar_agenda"
        elif command in {"calendar_reminders_status", "calendar_todo_status"}:
            payload = build_agenda_packet(owner=owner, start=now, end=now + timedelta(days=30))
            reply_text = format_agenda_for_telegram(payload, reminders_only=True)
            status = command
        elif command == "calendar_reminder_create":
            parsed = parse_reminder_tail(strip_action_word(
                telegram_command_tail(message),
                {"add", "create", "new", "set", "erinnere"},
            ))
            payload = write_reminder_note(
                owner=owner,
                action="add",
                title=parsed.get("title", ""),
                due_date=parsed.get("due_date", ""),
            )
            reply_text = format_calendar_write_for_telegram(payload, noun="Erinnerung")
            status = f"calendar_reminder_{payload.get('status') or 'error'}"
        elif command == "calendar_reminder_update":
            parsed = parse_reminder_update_tail(telegram_command_tail(message))
            payload = write_reminder_note(
                owner=owner,
                action="update",
                note_id=parsed.get("note_id", ""),
                title=parsed.get("title", ""),
                due_date=parsed.get("due_date", ""),
            )
            reply_text = format_calendar_write_for_telegram(payload, noun="Erinnerung")
            status = f"calendar_reminder_{payload.get('status') or 'error'}"
        elif command == "calendar_todo_digest_create":
            parsed = parse_todo_digest_tail(telegram_command_tail(message))
            payload = write_todo_digest_schedule(
                owner=owner,
                scheduled_time=parsed.get("scheduled_time", "09:00"),
                weekdays=parsed.get("weekdays", "mo-fr"),
                output_target="telegram",
            )
            reply_text = format_calendar_write_for_telegram(payload, noun="Todo-Digest")
            status = f"calendar_todo_digest_{payload.get('status') or 'error'}"
        else:
            reply_text = format_calendar_unknown_command_reply()
            status = "calendar_unknown_command"
    except Exception as exc:
        payload = {"status": "error", "error": exc.__class__.__name__, "raw_content_visible": False}
        reply_text = format_calendar_command_error_reply(exc.__class__.__name__)
        status = "calendar_command_error"

    reply_result = None
    if reply_handler is not None and bridge["chat_id"]:
        reply_result = reply_handler(
            bridge["chat_id"],
            reply_text,
            bridge.get("source_message_id"),
        )
    return {
        "command": command,
        "status": status,
        "binding": {},
        "reply_text": reply_text,
        "reply": reply_result,
        "calendar": payload,
    }


def handle_universal_inbox_control_command(
    command: str,
    *,
    message: dict[str, Any],
    raw_chat_id: str,
    reply_handler: ReplyHandler | None,
    store: Any,
    memory_manager: Any,
    memory_vector: Any,
    memory_owner: str | None,
    build_agent_bridge_request: BuildAgentBridgeRequest,
    build_universal_inbox_readiness: BuildUniversalInboxReadiness,
    format_universal_inbox_readiness: FormatUniversalInboxReadiness,
    format_universal_inbox_review_status: FormatUniversalInboxReviewStatus,
    build_nextcloud_transfer_dry_run: BuildNextcloudTransferDryRun,
    format_nextcloud_transfer_blocked_reply: FormatNextcloudTransferBlockedReply,
    format_universal_inbox_memory_review_status: FormatUniversalInboxMemoryReviewStatus,
    execute_memory_review_write: ExecuteTelegramMemoryReviewWrite,
) -> dict[str, Any] | None:
    """Handle Universal Inbox control commands behind injected repo-only helpers."""

    if not (
        command == "universal_inbox_status"
        or command in {"universal_inbox_review_status", "universal_inbox_review_confirm"}
        or command in {"universal_inbox_memory_review_status", "universal_inbox_memory_review_confirm"}
    ):
        return None

    bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
    reply_text = ""
    status = command
    transfer: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    payload: dict[str, Any] = {}

    if command == "universal_inbox_status":
        payload = build_universal_inbox_readiness()
        reply_text = format_universal_inbox_readiness(payload)
        status = f"universal_inbox_{payload.get('status') or 'blocked'}"
    elif command in {"universal_inbox_review_status", "universal_inbox_review_confirm"}:
        review = store.latest_universal_inbox_review(chat_id=bridge["chat_id"]) if store is not None else None
        if review is None:
            reply_text = format_universal_inbox_review_missing_reply()
            status = "universal_inbox_review_missing"
        elif command == "universal_inbox_review_confirm":
            if store is not None:
                store.append_event(
                    kind="universal_inbox_review",
                    status="confirmed",
                    chat_id=bridge["chat_id"],
                    source_message_id=review.get("message_id"),
                    universal_inbox_status=str(review.get("universal_inbox_status") or ""),
                    raw_content_visible=False,
                    raw_identifiers_visible=False,
                    filename_visible=False,
                )
            transfer = (
                build_nextcloud_transfer_dry_run(
                    data_dir=store.data_dir,
                    store=store,
                    chat_id=bridge["chat_id"],
                    review=review,
                )
                if store is not None
                else {"status": "blocked", "reason": "store_missing", "writes_performed": False}
            )
            if store is not None:
                store.append_event(
                    kind="universal_inbox_nextcloud_transfer",
                    status=str(transfer.get("status") or "blocked"),
                    chat_id=bridge["chat_id"],
                    source_message_id=review.get("message_id"),
                    universal_inbox_status=str(review.get("universal_inbox_status") or ""),
                    nextcloud_transfer_status=str(transfer.get("status") or "blocked"),
                    reason=str(transfer.get("reason") or ""),
                    dry_run=bool(transfer.get("dry_run", True)),
                    writes_performed=bool(transfer.get("writes_performed")),
                    verified=bool(transfer.get("verified")),
                    review_approved=bool(transfer.get("review_approved")),
                    target_path_visible=False,
                    sidecar_path_visible=False,
                    raw_content_visible=False,
                    raw_identifiers_visible=False,
                    filename_visible=False,
                )
            transfer_status = str(transfer.get("status") or "")
            if transfer_status in {"completed", "copied_unverified", "dry_run_ready"}:
                reply_text = format_universal_inbox_transfer_confirm_reply(transfer)
            else:
                reply_text = format_nextcloud_transfer_blocked_reply(transfer)
            status = "universal_inbox_review_confirmed"
        else:
            reply_text = format_universal_inbox_review_status(review)
            status = "universal_inbox_review_status"
    else:
        review = store.latest_universal_inbox_memory_review(chat_id=bridge["chat_id"]) if store is not None else None
        if review is None:
            reply_text = format_universal_inbox_memory_review_missing_reply()
            status = "universal_inbox_memory_review_missing"
        elif command == "universal_inbox_memory_review_confirm":
            if store is not None:
                store.append_event(
                    kind="universal_inbox_memory_review",
                    status="confirmed",
                    chat_id=bridge["chat_id"],
                    source_message_id=review.get("message_id"),
                    memory_write_intent_status=str(review.get("memory_write_intent_status") or ""),
                    universal_inbox_status=str(review.get("universal_inbox_status") or ""),
                    raw_content_visible=False,
                    raw_identifiers_visible=False,
                    filename_visible=False,
                )
            execution = (
                execute_memory_review_write(
                    data_dir=store.data_dir,
                    store=store,
                    chat_id=bridge["chat_id"],
                    memory_manager=memory_manager,
                    memory_vector=memory_vector,
                    memory_owner=memory_owner,
                    dry_run=False,
                )
                if store is not None
                else {"status": "blocked", "reason": "store_missing", "writes_performed": False}
            )
            if store is not None:
                store.append_event(
                    kind="universal_inbox_memory_write",
                    status=str(execution.get("status") or "blocked"),
                    chat_id=bridge["chat_id"],
                    source_message_id=review.get("message_id"),
                    memory_records_written=int(execution.get("memory_records_written") or 0),
                    raptorgraph_events_written=int(execution.get("raptorgraph_events_written") or 0),
                    writes_performed=bool(execution.get("writes_performed")),
                    raw_content_visible=False,
                    raw_identifiers_visible=False,
                    filename_visible=False,
                )
            reply_text = format_universal_inbox_memory_write_reply(execution)
            status = "universal_inbox_memory_review_confirmed"
        else:
            reply_text = format_universal_inbox_memory_review_status(review)
            status = "universal_inbox_memory_review_status"

    reply_result = None
    if reply_handler is not None and bridge["chat_id"]:
        reply_result = reply_handler(
            bridge["chat_id"],
            reply_text,
            bridge.get("source_message_id"),
        )

    result = {
        "command": command,
        "status": status,
        "binding": {},
        "reply_text": reply_text,
        "reply": reply_result,
    }
    if command == "universal_inbox_status":
        result["universal_inbox"] = payload
    if command in {"universal_inbox_review_status", "universal_inbox_review_confirm"}:
        result["nextcloud_transfer"] = transfer
    if command in {"universal_inbox_memory_review_status", "universal_inbox_memory_review_confirm"}:
        result["memory_write"] = execution
    return result


def handle_project_intake_control_command(
    command: str,
    *,
    message: dict[str, Any],
    raw_chat_id: str,
    reply_handler: ReplyHandler | None,
    store: Any,
    project_registry_path: Any,
    build_agent_bridge_request: BuildAgentBridgeRequest,
    apply_project_intake_review: ApplyProjectIntakeReview,
    format_project_intake_review_status: FormatProjectIntakeReviewStatus,
) -> dict[str, Any] | None:
    """Handle Project-Intake control commands with stateful helpers injected."""

    if not command.startswith("project_intake_"):
        return None

    bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
    review = store.latest_project_intake_review(chat_id=bridge["chat_id"]) if store is not None else None
    status = "project_intake_review_status"
    if command == "project_intake_review_confirm":
        if review is None:
            reply_text = format_project_intake_review_status(None)
            status = "project_intake_review_missing"
        else:
            apply_report = apply_project_intake_review(
                data_dir=store.data_dir if store is not None else ".",
                review=review,
                project_registry_path=project_registry_path,
            )
            apply_performed = bool(apply_report.get("applied"))
            if store is not None:
                store.append_event(
                    kind="project_intake_review",
                    status="confirmed" if apply_performed else "blocked",
                    chat_id=bridge["chat_id"],
                    source_message_id=review.get("source_message_id"),
                    project_slug=str(review.get("project_slug") or ""),
                    task_count=int(review.get("task_count") or 0),
                    decision_count=int(review.get("decision_count") or 0),
                    risk_count=int(review.get("risk_count") or 0),
                    roadmap_update_count=int(review.get("roadmap_update_count") or 0),
                    raw_content_visible=False,
                    raw_identifiers_visible=False,
                    project_intake_apply_performed=apply_performed,
                    project_intake_apply_status=str(apply_report.get("status") or "blocked"),
                    project_intake_apply_blockers=tuple(apply_report.get("blockers") or ()),
                    project_intake_apply_event_id=apply_report.get("event_id"),
                )
            if apply_performed:
                reply_text = format_project_intake_apply_result(apply_report)
                status = "project_intake_review_confirmed"
            else:
                reply_text = format_project_intake_apply_result(apply_report)
                status = "project_intake_review_apply_blocked"
    elif command == "project_intake_review_hold":
        if review is None:
            reply_text = format_project_intake_review_status(None)
            status = "project_intake_review_missing"
        else:
            if store is not None:
                store.append_event(
                    kind="project_intake_review",
                    status="held",
                    chat_id=bridge["chat_id"],
                    source_message_id=review.get("source_message_id"),
                    project_slug=str(review.get("project_slug") or ""),
                    raw_content_visible=False,
                    raw_identifiers_visible=False,
                    project_intake_apply_performed=False,
                )
            reply_text = format_project_intake_hold_reply()
            status = "project_intake_review_held"
    else:
        reply_text = format_project_intake_review_status(review)
        status = "project_intake_review_status" if review is not None else "project_intake_review_missing"

    reply_result = None
    if reply_handler is not None and bridge["chat_id"]:
        reply_result = reply_handler(
            bridge["chat_id"],
            reply_text,
            bridge.get("source_message_id"),
        )
    return {
        "command": command,
        "status": status,
        "binding": {},
        "reply_text": reply_text,
        "reply": reply_result,
    }


def handle_new_chat_control_command(
    command: str,
    *,
    message: dict[str, Any],
    raw_chat_id: str,
    reply_handler: ReplyHandler | None,
    sessions: Any,
    session_creator: Callable[..., dict[str, Any]],
    build_agent_bridge_request: BuildAgentBridgeRequest,
) -> dict[str, Any] | None:
    """Handle /new chat rebinding behind injected session helpers."""

    if command != "new_chat":
        return None

    bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
    binding = sessions.rebind_chat(
        chat_id=bridge["chat_id"],
        session_alias=bridge["session_alias"],
        recommended_session_name=bridge["recommended_session_name"],
        scope=str(bridge.get("desired_session_scope") or "normal"),
        creator=session_creator,
    )
    created = bool(binding.get("session_id"))
    reply_text = format_new_chat_reply(created=created)
    reply_result = None
    if reply_handler is not None and bridge["chat_id"]:
        reply_result = reply_handler(
            bridge["chat_id"],
            reply_text,
            bridge.get("source_message_id"),
        )
    return {
        "command": command,
        "status": "new_chat_bound" if created else "new_chat_pending_bridge",
        "binding": binding,
        "reply_text": reply_text,
        "reply": reply_result,
    }

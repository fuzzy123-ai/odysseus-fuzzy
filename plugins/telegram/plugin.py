"""Standalone Telegram agent-chat plugin.

The plugin is safe-by-default: it stores a local redacted inbox/history and can
prepare agent-turn payloads without calling Telegram. Outbound replies require
an explicit local env gate and never expose token values.
"""

from __future__ import annotations

import asyncio
import json
import os
import hashlib
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from src.chat_security_state import ChatSecurityState
from src.memory_triage_contract import normalize_memory_write_intent_status
from src.privacy_runtime import is_dsgvo_mode_enabled, runtime_requires_local_only
from src.secure_channel_policy import ChannelContext, decide_channel_access
from src.telegram_truth_gate import gate_telegram_reply_text
from src.telegram_image_actions import run_telegram_image_action
from src.telegram_screenshot_delivery import (
    build_telegram_screenshot_delivery_packet,
    build_telegram_screenshot_live_gate_packet,
)
from src.telegram_voice_pipeline import (
    VoiceAgentTurn,
    build_voice_agent_turn,
    build_voice_local_file_ref,
    plan_voice_download,
    run_fakeable_stt,
)
from src.universal_inbox_readiness import (
    build_universal_inbox_readiness,
    format_universal_inbox_readiness_for_telegram,
)
from fastapi import APIRouter, HTTPException, Request
from src.user_notification_contract import (
    NotificationContractError,
    build_user_notification_decision,
)

try:
    from core.middleware import require_admin as _core_require_admin
except Exception:  # pragma: no cover - plugin file-loader imports may not have app context
    _core_require_admin = None


PLUGIN = {
    "name": "Telegram",
    "version": "0.2.1",
    "author": "Odysseus",
    "description": "Standalone Telegram agent-chat bridge with local inbox/history, gated replies, and voice intake metadata.",
    "category": "Communications",
    "manifest_version": "1.0",
    "permission": "admin",
    "kind": "ui",
    "capabilities": ["admin_route", "local_api"],
    "compatibility": {"min_odysseus": "1.0.0"},
    "lifecycle": "loadable",
    "ui": {"open": "/api/plugins/telegram/app", "label": "Open Telegram"},
}


_PROJECT_REGISTRY_FILE = "server_project_registry.json"


def _bool_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _voice_stt_enabled() -> bool:
    return _bool_env("TELEGRAM_VOICE_STT_ENABLED") or _bool_env("TELEGRAM_STT_ENABLED")


def _load_dsgvo_settings() -> dict[str, Any]:
    from src.settings import load_settings

    return dict(load_settings() or {})


def _save_dsgvo_settings(settings: dict[str, Any]) -> None:
    from src.settings import save_settings

    save_settings(settings)


def _dsgvo_mode_active(settings: dict[str, Any] | None = None) -> bool:
    return is_dsgvo_mode_enabled(settings=settings if settings is not None else _load_dsgvo_settings())


def _set_dsgvo_mode(enabled: bool) -> dict[str, Any]:
    settings = _load_dsgvo_settings()
    before = _dsgvo_mode_active(settings)
    settings["dsgvo_mode"] = bool(enabled)
    if not enabled:
        settings["gdpr_mode"] = False
    _save_dsgvo_settings(settings)
    after = _dsgvo_mode_active()
    return {
        "requested": bool(enabled),
        "before": before,
        "after": after,
        "changed": before != after,
        "forced_active": bool(not enabled and after),
    }


def _dsgvo_reply_text(command: str, result: dict[str, Any] | None = None) -> str:
    return format_dsgvo_reply_text(command, result, active=_dsgvo_mode_active())


def require_admin(request: Request) -> None:
    """Delegate to the app admin gate when the plugin runs inside Odysseus."""

    if _core_require_admin is not None:
        _core_require_admin(request)


def _allowed_chat_ids() -> tuple[str, ...]:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or ""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _chat_allowed(chat_id: str) -> bool:
    allowed = _allowed_chat_ids()
    return bool(chat_id and allowed and chat_id in allowed)


def _privacy_pin_enabled() -> bool:
    return not _bool_env("TELEGRAM_PRIVACY_PIN_DISABLED")


from plugins.telegram.admin import build_telegram_readiness as _build_telegram_readiness
from plugins.telegram.attachments import (
    _format_universal_inbox_memory_review_status,
    _format_universal_inbox_review_status,
    _telegram_attachment_context_max_chars,
    _telegram_attachment_context_max_extract_bytes,
    _telegram_attachment_context_ttl_seconds,
    _telegram_attachment_family,
    _telegram_attachment_spool_key,
    _telegram_attachment_suffix,
    format_telegram_attachment_inbox_reply,
)
from plugins.telegram.control_service import (
    handle_agent_task_control_command,
    handle_calendar_control_command,
    handle_dsgvo_control_command,
    handle_new_chat_control_command,
    handle_project_intake_control_command,
    handle_universal_inbox_control_command,
    public_agent_task_record,
)
from plugins.telegram.formatting import (
    format_dsgvo_reply_text,
    format_nextcloud_transfer_blocked_reply,
)
from plugins.telegram.export import (
    build_recent_telegram_attachment_export_plan,
    execute_recent_telegram_attachment_export,
    format_telegram_attachment_export_reply,
)
from plugins.telegram.live_pipeline import (
    build_telegram_live_voice_stt_provider as _build_telegram_live_voice_stt_provider,
    download_telegram_file_bytes,
    download_telegram_voice_bytes,
    run_telegram_universal_inbox_attachment_pipeline,
)
from plugins.telegram.outbound import (
    _telegram_http_post,
    _telegram_http_post_multipart,
    send_telegram_chat_action,
    send_telegram_document,
    send_telegram_pin_message,
    send_telegram_photo,
    send_telegram_rich_draft,
    send_telegram_rich_message,
    send_telegram_text,
    send_telegram_unpin_message,
)
from plugins.telegram.polling import (
    _agent_failure_reply,
    _public_agent_turn_result,
    _public_reply_result,
    _reply_result_telegram_message_id,
    _run_agent_turn,
    _run_agent_turn_async,
    deterministic_telegram_agent_turn,
    fetch_telegram_updates,
    run_telegram_polling_cycle_impl,
    telegram_typing_keepalive_seconds,
)
from plugins.telegram.project_intake import (
    _apply_telegram_project_intake_review,
    _format_project_intake_review_status,
    build_telegram_project_intake_preview,
    format_telegram_project_intake_reply,
)
from plugins.telegram.routes_admin import register_telegram_admin_routes
from plugins.telegram.routes_outbound import register_telegram_outbound_routes
from plugins.telegram.routes_polling import register_telegram_polling_routes
from plugins.telegram.routes_webhook import register_telegram_webhook_routes
from plugins.telegram.webhook_service import (
    TelegramWebhookIntakeError,
    build_webhook_control_command_summary,
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
from plugins.telegram.parsing import (
    _safe_workflow_suffix,
    _safe_workflow_token,
    _telegram_control_command,
    build_telegram_workflow_context,
    parse_telegram_update as _parse_telegram_update,
)
from plugins.telegram.stores import (
    TelegramInboxStore,
    TelegramPollingStateStore,
    TelegramPrivacyPinStore,
    TelegramSessionBridgeStore,
    _chat_handle,
    _stable_handle,
    build_telegram_draft_id,
)
from src.telegram_task_orchestrator import build_telegram_task_intent, build_telegram_task_status_message


def parse_telegram_update(
    update: dict[str, Any],
    *,
    chat_allowed: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Extract a redacted local-history message from a Telegram update."""

    return _parse_telegram_update(update, chat_allowed=chat_allowed or _chat_allowed)


def build_telegram_readiness(data_dir: str | Path | None = None) -> dict[str, Any]:
    return _build_telegram_readiness(data_dir, dsgvo_settings_loader=_load_dsgvo_settings)


def build_agent_bridge_request(
    message: dict[str, Any],
    *,
    session_binding: dict[str, Any] | None = None,
    raw_chat_id: str | None = None,
    voice_agent_turn: VoiceAgentTurn | None = None,
    recent_attachment_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the internal agent-turn envelope for a stored Telegram message."""

    chat_id = str(raw_chat_id or message.get("chat_id") or "")
    chat_handle = str(message.get("chat_handle") or _chat_handle(chat_id))
    sender = message.get("sender") or {}
    display_name = sender.get("username") or sender.get("first_name") or chat_handle
    kind = message.get("kind")
    if kind == "text":
        persisted_prompt = str(message.get("text") or "")
        attachment_text = str((recent_attachment_context or {}).get("context") or "").strip()
        prompt = (
            f"{attachment_text}\n\nAktuelle Telegram-Nachricht:\n{persisted_prompt}"
            if attachment_text
            else persisted_prompt
        )
        ready_for_agent = bool(prompt.strip())
        note = "text_ready"
    elif kind == "voice":
        media = message.get("media") or {}
        if voice_agent_turn is not None and voice_agent_turn.ready_for_agent:
            prompt = voice_agent_turn.prompt
            persisted_prompt = "[Telegram voice message transcribed for this turn.]"
            ready_for_agent = True
            note = "voice_transcribed"
        else:
            prompt = (
                "[Telegram voice message received. "
                f"file_handle={media.get('file_handle', '')}; duration={media.get('duration', 'unknown')}; "
                "transcription pending.]"
            )
            persisted_prompt = prompt
            ready_for_agent = False
            note = "voice_needs_transcription"
    elif kind == "image":
        media = message.get("media") or {}
        prompt = (
            "[Telegram image received. "
            f"file_handle={media.get('file_handle', '')}; size={media.get('file_size', 'unknown')}; "
            "image action pending.]"
        )
        persisted_prompt = prompt
        ready_for_agent = False
        note = "image_action_pending"
    elif kind == "document":
        media = message.get("media") or {}
        prompt = (
            "[Telegram document received. "
            f"file_handle={media.get('file_handle', '')}; size={media.get('file_size', 'unknown')}; "
            "universal inbox processing pending.]"
        )
        persisted_prompt = prompt
        ready_for_agent = False
        note = "universal_inbox_pending"
    else:
        prompt = "[Unsupported Telegram message received.]"
        persisted_prompt = prompt
        ready_for_agent = False
        note = "unsupported_message"

    dsgvo_mode = _dsgvo_mode_active()
    attachment_policy = (
        (recent_attachment_context or {}).get("analysis_policy")
        if isinstance((recent_attachment_context or {}).get("analysis_policy"), dict)
        else {}
    )
    attachment_local_only = bool((recent_attachment_context or {}).get("local_only_required"))
    from src.sensitivity_delegation_gate import decide_sensitivity_delegation

    sensitivity_delegation = decide_sensitivity_delegation(
        dsgvo_mode=dsgvo_mode,
        classification=attachment_policy.get("classification") or (
            "private" if kind in {"text", "voice"} else None
        ),
        raw_content_visible=bool((recent_attachment_context or {}).get("raw_content_visible"))
        or bool(kind == "voice" and note == "voice_transcribed"),
        api_model_allowed=bool((recent_attachment_context or {}).get("api_model_allowed"))
        or bool(kind == "text" and not recent_attachment_context),
        local_only_required=attachment_local_only,
        redacted_context_available=bool((recent_attachment_context or {}).get("memory_write_intent_status") == "written"),
    ).to_dict()
    voice_dsgvo_exempt = False
    local_only_required = bool(
        runtime_requires_local_only(settings={"dsgvo_mode": dsgvo_mode})
        or attachment_local_only
        or sensitivity_delegation.get("local_worker_required")
    )
    workflow_context = build_telegram_workflow_context(
        message,
        recent_attachment_context=recent_attachment_context,
        dsgvo_mode=dsgvo_mode,
    )
    task_intent = build_telegram_task_intent(message, workflow_context=workflow_context).to_dict()
    task_status_message = build_telegram_task_status_message(task_intent)
    return {
        "channel": "telegram",
        "session_alias": f"telegram:{chat_handle}",
        "recommended_session_name": f"Telegram {display_name}",
        "session_id": (session_binding or {}).get("session_id") or "",
        "session_scope": (session_binding or {}).get("last_selected_scope") or ("secure" if local_only_required else "normal"),
        "desired_session_scope": "secure" if local_only_required else "normal",
        "chat_id": chat_id,
        "chat_handle": chat_handle,
        "source_message_id": message.get("message_id"),
        "kind": kind,
        "prompt": prompt,
        "persisted_prompt": persisted_prompt,
        "ready_for_agent": ready_for_agent and _bridge_intake_ready(message, kind=kind, note=note),
        "reply_required": ready_for_agent and _bridge_intake_ready(message, kind=kind, note=note),
        "note": note,
        "recent_attachment_context": {
            "present": bool((recent_attachment_context or {}).get("context")),
            "status": str((recent_attachment_context or {}).get("status") or ""),
            "raw_content_visible": bool((recent_attachment_context or {}).get("raw_content_visible")),
            "host_paths_visible": False,
        },
        "workflow_context": workflow_context,
        "task_intent": task_intent,
        "task_status_message": task_status_message,
        "long_running_task": task_intent.get("task_type") in {
            "coding_agent_task",
            "website_research",
            "website_research_to_memory",
        },
        "intake_status": message.get("intake_status") or note,
        "dsgvo_mode": dsgvo_mode,
        "security_mode": "secure" if local_only_required else "normal",
        "local_only_required": local_only_required,
        "attachment_local_only_required": attachment_local_only,
        "telegram_voice_dsgvo_exempt": voice_dsgvo_exempt,
        "sensitivity_delegation": sensitivity_delegation,
    }


def _handle_agent_task_control_command(command: str) -> dict[str, Any]:
    return handle_agent_task_control_command(command)


def _public_agent_task_record(record: dict[str, Any]) -> dict[str, Any]:
    return public_agent_task_record(record)


def _handle_calendar_control_command(
    command: str,
    *,
    message: dict[str, Any],
    raw_chat_id: str,
    reply_handler: Callable[[str, str, int | None], dict[str, Any]] | None,
    memory_owner: str | None,
) -> dict[str, Any] | None:
    from src.calendar_capability_service import (
        build_agenda_packet,
        build_calendar_readiness,
        write_reminder_note,
        write_todo_digest_schedule,
    )

    return handle_calendar_control_command(
        command,
        message=message,
        raw_chat_id=raw_chat_id,
        reply_handler=reply_handler,
        memory_owner=memory_owner,
        build_agent_bridge_request=build_agent_bridge_request,
        build_calendar_readiness=build_calendar_readiness,
        build_agenda_packet=build_agenda_packet,
        write_reminder_note=write_reminder_note,
        write_todo_digest_schedule=write_todo_digest_schedule,
    )


def _handle_telegram_control_command(
    command: str,
    *,
    message: dict[str, Any],
    raw_chat_id: str,
    sessions: TelegramSessionBridgeStore,
    session_creator: Callable[..., Any] | None,
    reply_handler: Callable[[str, str, int | None], dict[str, Any]] | None = None,
    store: TelegramInboxStore | None = None,
    pin_store: TelegramPrivacyPinStore | None = None,
    memory_manager: Any = None,
    memory_vector: Any = None,
    memory_owner: str | None = None,
    project_registry_path: str | Path | None = None,
) -> dict[str, Any] | None:
    if not command:
        return None
    if message.get("chat_allowed") is not True:
        return {
            "command": command,
            "status": "control_chat_not_allowed",
            "binding": {},
            "reply_text": "",
            "reply": None,
        }
    if command.startswith("dsgvo_"):
        return handle_dsgvo_control_command(
            command,
            message=message,
            raw_chat_id=raw_chat_id,
            reply_handler=reply_handler,
            store=store,
            pin_store=pin_store,
            set_dsgvo_mode=_set_dsgvo_mode,
            dsgvo_mode_active=_dsgvo_mode_active,
            dsgvo_reply_text=_dsgvo_reply_text,
            sync_dsgvo_pin_state=_sync_dsgvo_pin_state,
            build_agent_bridge_request=build_agent_bridge_request,
        )
    if command.startswith("agent_task_"):
        bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
        result = _handle_agent_task_control_command(command)
        reply_text = str(result.get("reply_text") or "")
        reply_result = None
        if reply_handler is not None and bridge["chat_id"]:
            reply_result = reply_handler(
                bridge["chat_id"],
                reply_text,
                bridge.get("source_message_id"),
            )
        return {
            "command": command,
            "status": str(result.get("status") or command),
            "binding": {},
            "reply_text": reply_text,
            "reply": reply_result,
            "agent_task": result.get("agent_task") or {},
        }
    if command.startswith("calendar_"):
        return _handle_calendar_control_command(
            command,
            message=message,
            raw_chat_id=raw_chat_id,
            reply_handler=reply_handler,
            memory_owner=memory_owner,
        )
    universal_inbox_result = handle_universal_inbox_control_command(
        command,
        message=message,
        raw_chat_id=raw_chat_id,
        reply_handler=reply_handler,
        store=store,
        memory_manager=memory_manager,
        memory_vector=memory_vector,
        memory_owner=memory_owner,
        build_agent_bridge_request=build_agent_bridge_request,
        build_universal_inbox_readiness=build_universal_inbox_readiness,
        format_universal_inbox_readiness=format_universal_inbox_readiness_for_telegram,
        format_universal_inbox_review_status=_format_universal_inbox_review_status,
        build_nextcloud_transfer_dry_run=_build_recent_telegram_nextcloud_transfer_dry_run,
        format_nextcloud_transfer_blocked_reply=_format_nextcloud_transfer_blocked_reply,
        format_universal_inbox_memory_review_status=_format_universal_inbox_memory_review_status,
        execute_memory_review_write=_execute_telegram_memory_review_write,
    )
    if universal_inbox_result is not None:
        return universal_inbox_result
    project_intake_result = handle_project_intake_control_command(
        command,
        message=message,
        raw_chat_id=raw_chat_id,
        reply_handler=reply_handler,
        store=store,
        project_registry_path=project_registry_path,
        build_agent_bridge_request=build_agent_bridge_request,
        apply_project_intake_review=_apply_telegram_project_intake_review,
        format_project_intake_review_status=_format_project_intake_review_status,
    )
    if project_intake_result is not None:
        return project_intake_result
    return handle_new_chat_control_command(
        command,
        message=message,
        raw_chat_id=raw_chat_id,
        reply_handler=reply_handler,
        sessions=sessions,
        session_creator=session_creator,
        build_agent_bridge_request=build_agent_bridge_request,
    )


def _bridge_intake_ready(message: dict[str, Any], *, kind: Any, note: str) -> bool:
    if message.get("intake_status") == "ready":
        return True
    return kind == "voice" and note == "voice_transcribed" and message.get("chat_allowed") is True


def _telegram_voice_max_bytes() -> int:
    raw = os.getenv("TELEGRAM_VOICE_MAX_BYTES") or "10000000"
    try:
        value = int(raw)
    except ValueError:
        value = 10_000_000
    return max(1, min(value, 100_000_000))


def run_telegram_voice_pipeline(
    message: dict[str, Any],
    *,
    stt_provider: Callable[[str], str] | None = None,
    download_enabled: bool | None = None,
    stt_enabled: bool | None = None,
) -> tuple[VoiceAgentTurn | None, dict[str, Any] | None]:
    """Run the default-off offline voice pipeline without Telegram or provider IO."""

    if message.get("kind") != "voice":
        return None, None
    media = message.get("media") if isinstance(message.get("media"), dict) else {}
    download = plan_voice_download(
        message,
        download_enabled=_bool_env("TELEGRAM_VOICE_DOWNLOAD_ENABLED") if download_enabled is None else download_enabled,
        max_bytes=_telegram_voice_max_bytes(),
    )
    local_ref = build_voice_local_file_ref(download, mime_type=str(media.get("mime_type") or "audio/ogg"))
    stt = run_fakeable_stt(
        local_file_ref=local_ref.local_file_ref,
        stt_enabled=_voice_stt_enabled() if stt_enabled is None else stt_enabled,
        stt_provider=stt_provider,
    )
    chat_handle = str(message.get("chat_handle") or _chat_handle(message.get("chat_id")))
    turn = build_voice_agent_turn(stt, chat_handle=chat_handle)
    return turn, {
        "download": {
            "allowed": download.allowed,
            "status": download.status,
            "reason": download.reason,
            "file_handle_present": bool(download.file_handle),
            "raw_identifiers_visible": download.raw_identifiers_visible,
        },
        "local_file_ref": {
            "ready": local_ref.ready,
            "status": local_ref.status,
            "reason": local_ref.reason,
            "local_file_ref_present": bool(local_ref.local_file_ref),
            "raw_identifiers_visible": local_ref.raw_identifiers_visible,
        },
        "stt": {
            "allowed": stt.allowed,
            "status": stt.status,
            "reason": stt.reason,
            "transcript_present": bool(stt.transcript),
            "transcript_value_visible": False,
            "raw_identifiers_visible": stt.raw_identifiers_visible,
        },
        "agent_turn": {
            "ready_for_agent": turn.ready_for_agent,
            "status": turn.status,
            "reason": turn.reason,
            "prompt_value_visible": False,
            "raw_identifiers_visible": turn.raw_identifiers_visible,
        },
    }


def build_recent_telegram_attachment_context(
    *,
    data_dir: str | Path,
    store: TelegramInboxStore,
    chat_id: str,
) -> dict[str, Any] | None:
    event = store.latest_universal_inbox_attachment(
        chat_id=chat_id,
        max_age_seconds=_telegram_attachment_context_ttl_seconds(),
    )
    if not event:
        return None
    spool_key = str(event.get("spool_key") or "").strip()
    if not spool_key:
        chat_handle = str(event.get("chat_handle") or _chat_handle(chat_id) or "chat")
        message_id = str(event.get("message_id") or "")
        if message_id:
            spool_key = hashlib.sha256(f"{chat_handle}:{message_id}".encode("utf-8")).hexdigest()[:16]
    if not spool_key:
        return None
    spool_dir = Path(data_dir) / "universal_inbox_telegram" / spool_key
    family = _safe_workflow_token(event.get("attachment_family") or "document")
    suffix = _safe_workflow_suffix(event.get("attachment_suffix") or "")
    memory_status = normalize_memory_write_intent_status(
        event.get("memory_write_intent_status") or ""
    )
    nextcloud_transfer = (
        store.latest_universal_inbox_nextcloud_transfer(
            chat_id=chat_id,
            source_message_id=event.get("message_id"),
        )
        if hasattr(store, "latest_universal_inbox_nextcloud_transfer")
        else None
    )
    nextcloud_status = str((nextcloud_transfer or {}).get("nextcloud_transfer_status") or (nextcloud_transfer or {}).get("status") or "")
    nextcloud_reason = str((nextcloud_transfer or {}).get("reason") or "")
    if not spool_dir.exists() or not spool_dir.is_dir():
        return {
            "status": "missing_spool",
            "family": family,
            "suffix": suffix,
            "universal_inbox_status": str(event.get("universal_inbox_status") or ""),
            "memory_write_intent_status": memory_status,
            "nextcloud_transfer_status": nextcloud_status,
            "nextcloud_transfer_reason": nextcloud_reason,
            "context": (
                "[Letzter Telegram-Anhang: verarbeitet, aber die lokale Datei ist "
                "nicht mehr im Attachment-Spool verfuegbar.]"
            ),
            "raw_content_visible": False,
            "host_paths_visible": False,
        }
    try:
        files = [path for path in spool_dir.iterdir() if path.is_file() and not path.is_symlink()]
    except OSError:
        files = []
    if not files:
        return {
            "status": "missing_spool_file",
            "family": family,
            "suffix": suffix,
            "universal_inbox_status": str(event.get("universal_inbox_status") or ""),
            "memory_write_intent_status": memory_status,
            "nextcloud_transfer_status": nextcloud_status,
            "nextcloud_transfer_reason": nextcloud_reason,
            "context": "[Letzter Telegram-Anhang: verarbeitet, aber keine lokale Spool-Datei gefunden.]",
            "raw_content_visible": False,
            "host_paths_visible": False,
        }

    if not suffix and files:
        suffix = _safe_workflow_suffix(files[0].suffix)
    try:
        from src.universal_inbox_extraction import extract_universal_inbox_content

        packet = extract_universal_inbox_content(
            files[0],
            root=spool_dir,
            max_extract_bytes=_telegram_attachment_context_max_extract_bytes(),
        )
    except Exception as exc:
        return {
            "status": "context_extract_failed",
            "family": family,
            "suffix": suffix,
            "universal_inbox_status": str(event.get("universal_inbox_status") or ""),
            "memory_write_intent_status": memory_status,
            "nextcloud_transfer_status": nextcloud_status,
            "nextcloud_transfer_reason": nextcloud_reason,
            "context": f"[Letzter Telegram-Anhang: Kontext-Extraktion fehlgeschlagen: {str(exc)[:120]}]",
            "raw_content_visible": False,
            "host_paths_visible": False,
        }

    packet_text = packet.raw_text
    packet_status = packet.status
    packet_metadata = dict(packet.metadata)
    if not packet_text and (suffix or packet.suffix) == ".pdf":
        try:
            from src import personal_docs as _personal_docs

            packet_text = _personal_docs.extract_pdf_text(str(files[0]))
        except Exception:
            packet_text = ""
        if packet_text:
            packet_status = "completed"
            packet_metadata = {
                **packet_metadata,
                "extractor": "personal_docs_pdf_text_fallback",
                "char_count": len(packet_text),
                "line_count": len(packet_text.splitlines()),
            }

    analysis_policy: dict[str, Any] = {}
    if packet_text:
        try:
            from src.universal_inbox_analysis import build_universal_inbox_file_analysis_packet

            analysis = build_universal_inbox_file_analysis_packet(
                {
                    "status": packet_status,
                    "extraction_status": packet_status,
                    "suffix": packet.suffix,
                    "filename": "telegram-attachment",
                    "source_channel": "telegram",
                    "extractor": packet_metadata.get("extractor") or "",
                },
                text_sample=packet_text[: min(len(packet_text), 4000)],
                requested_classification="private",
                settings={"dsgvo_mode": _dsgvo_mode_active()},
            ).to_dict()
            analysis_policy = dict(analysis.get("policy") or {})
        except Exception:
            analysis_policy = {
                "status": "review",
                "api_model_allowed": False,
                "local_only_required": True,
                "review_reasons": ("local_policy_analysis_failed",),
            }
    warnings = ", ".join(warning.code for warning in packet.warnings) or "none"
    header = (
        "[Letzter Telegram-Anhang fuer diese Unterhaltung]\n"
        f"- Status: {packet_status}\n"
        f"- Typ: {packet.suffix or 'unknown'}\n"
        f"- Extractor: {packet_metadata.get('extractor') or 'unknown'}\n"
        f"- Warnungen: {warnings}\n"
        f"- Lokale Vorpruefung: {analysis_policy.get('status') or 'metadata_only'}\n"
        f"- Nextcloud-Ablage: {nextcloud_status or 'nicht ausgefuehrt'}"
        f"{f' ({nextcloud_reason})' if nextcloud_reason else ''}\n"
    )
    if packet_text:
        text = packet_text[: _telegram_attachment_context_max_chars()]
        truncated = "\n[... gekuerzt ...]" if len(packet_text) > len(text) else ""
        context = f"{header}\nInhalt, nur fuer diesen Modellaufruf:\n{text}{truncated}"
        raw_visible = True
    else:
        context = f"{header}\nEs ist kein extrahierbarer Text verfuegbar; nutze die Metadaten und Review-Hinweise."
        raw_visible = False
    return {
        "status": "ready",
        "family": family,
        "suffix": suffix or _safe_workflow_suffix(packet.suffix),
        "universal_inbox_status": str(event.get("universal_inbox_status") or ""),
        "memory_write_intent_status": memory_status,
        "nextcloud_transfer_status": nextcloud_status,
        "nextcloud_transfer_reason": nextcloud_reason,
        "context": context,
        "raw_content_visible": raw_visible,
        "host_paths_visible": False,
        "analysis_policy": analysis_policy,
        "api_model_allowed": bool(analysis_policy.get("api_model_allowed")) if analysis_policy else False,
        "local_only_required": bool(analysis_policy.get("local_only_required")),
        "source_message_id": event.get("message_id"),
    }


def build_recent_telegram_memory_write_intent(
    *,
    data_dir: str | Path,
    store: TelegramInboxStore,
    chat_id: str,
) -> dict[str, Any] | None:
    review = store.latest_universal_inbox_memory_review(chat_id=chat_id)
    if not review:
        return None
    spool_key = str(review.get("spool_key") or "").strip()
    if not spool_key:
        chat_handle = str(review.get("chat_handle") or _chat_handle(chat_id) or "chat")
        message_id = str(review.get("message_id") or "")
        if message_id:
            spool_key = hashlib.sha256(f"{chat_handle}:{message_id}".encode("utf-8")).hexdigest()[:16]
    if not spool_key:
        return None
    spool_dir = Path(data_dir) / "universal_inbox_telegram" / spool_key
    if not spool_dir.exists() or not spool_dir.is_dir():
        return None
    snapshot = build_universal_inbox_readiness(spool_dir)
    try:
        from src.universal_inbox_worker import run_universal_inbox_dry_run

        report = run_universal_inbox_dry_run(spool_dir).to_dict()
        items = tuple(report.get("items") or ())
        first = items[0] if items else {}
        pipeline = first.get("pipeline_report") if isinstance(first, dict) else {}
        intent = pipeline.get("memory_write_intent") if isinstance(pipeline, dict) else {}
        return dict(intent) if isinstance(intent, dict) else None
    except Exception:
        # The public readiness snapshot still gives the caller a redacted status.
        status = normalize_memory_write_intent_status(snapshot.get("memory_write_intent_status") or "")
        return {"status": status, "ready_to_write": False, "memory_records": (), "raptorgraph_event": {}}


def _build_recent_telegram_nextcloud_transfer_dry_run(
    *,
    data_dir: str | Path,
    store: TelegramInboxStore,
    chat_id: str,
    review: Mapping[str, Any],
) -> dict[str, Any]:
    spool_key = str(review.get("spool_key") or "").strip()
    if not spool_key:
        chat_handle = str(review.get("chat_handle") or _chat_handle(chat_id) or "chat")
        message_id = str(review.get("message_id") or "")
        if message_id:
            spool_key = hashlib.sha256(f"{chat_handle}:{message_id}".encode("utf-8")).hexdigest()[:16]
    if not spool_key:
        return {"status": "blocked", "reason": "spool_key_missing", "dry_run": True, "writes_performed": False}

    spool_dir = Path(data_dir) / "universal_inbox_telegram" / spool_key
    try:
        files = [path for path in spool_dir.iterdir() if path.is_file() and not path.is_symlink()]
    except OSError:
        files = []
    if not files:
        return {"status": "blocked", "reason": "spool_file_missing", "dry_run": True, "writes_performed": False}

    try:
        from src.nextcloud_webdav_client import NextcloudWebDAVClientError, build_nextcloud_webdav_client_from_env
        from src.universal_inbox_nextcloud_transfer import (
            UniversalInboxNextcloudTransferRequest,
            execute_universal_inbox_nextcloud_transfer,
        )
        from src.universal_inbox_worker import run_universal_inbox_dry_run

        report = run_universal_inbox_dry_run(spool_dir).to_dict()
        items = tuple(report.get("items") or ())
        first = items[0] if items else {}
        placement = first.get("placement_plan") if isinstance(first, dict) else {}
        source_hash = str(first.get("source_hash") or "") if isinstance(first, dict) else ""
        operator_live_go = _telegram_nextcloud_live_write_enabled()
        request = UniversalInboxNextcloudTransferRequest.from_placement_plan(
            placement if isinstance(placement, Mapping) else {},
            source_path=files[0],
            source_hash=source_hash,
            review_approved=True,
            operator_live_go=operator_live_go,
            dry_run=not operator_live_go,
            actor="telegram",
        )
        try:
            client = build_nextcloud_webdav_client_from_env() if operator_live_go else None
        except NextcloudWebDAVClientError:
            return _nextcloud_server_config_missing_transfer()
        try:
            return execute_universal_inbox_nextcloud_transfer(request, client=client).to_dict()
        finally:
            if client is not None:
                client.close()
    except Exception as exc:
        return {
            "status": "blocked",
            "reason": "nextcloud_transfer_plan_failed",
            "error_class": exc.__class__.__name__,
            "dry_run": True,
            "writes_performed": False,
        }


def _execute_telegram_nextcloud_auto_transfer_if_ready(
    *,
    data_dir: str | Path,
    store: TelegramInboxStore,
    chat_id: str,
    inbox_attachment: Mapping[str, Any],
    attachment_event: Mapping[str, Any],
) -> dict[str, Any] | None:
    if str(inbox_attachment.get("status") or "") != "processed":
        return None
    if str(inbox_attachment.get("universal_inbox_status") or "") != "go":
        return None
    transfer = _build_recent_telegram_nextcloud_transfer_dry_run(
        data_dir=data_dir,
        store=store,
        chat_id=chat_id,
        review=attachment_event,
    )
    store.append_event(
        kind="universal_inbox_nextcloud_transfer",
        status=str(transfer.get("status") or "blocked"),
        chat_id=chat_id,
        source_message_id=attachment_event.get("message_id"),
        universal_inbox_status=str(inbox_attachment.get("universal_inbox_status") or ""),
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
    return transfer


def _nextcloud_server_config_missing_transfer() -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "nextcloud_server_config_missing",
        "dry_run": True,
        "writes_performed": False,
        "server_config_required": True,
        "secret_input_allowed": False,
    }


def _format_nextcloud_transfer_blocked_reply(transfer: Mapping[str, Any]) -> str:
    return format_nextcloud_transfer_blocked_reply(dict(transfer))


def _telegram_nextcloud_live_write_enabled() -> bool:
    """Return true only when both live-write runtime gates are explicit."""

    enabled = (os.getenv("UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED") or "").strip().lower()
    operator_go = (os.getenv("UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO") or "").strip().lower()
    return enabled in {"1", "true", "yes", "on"} and operator_go in {"1", "true", "yes", "on"}


def _build_native_memory_writer(memory_manager: Any, memory_vector: Any = None, *, owner: str = "telegram") -> Callable[[dict[str, Any]], Any] | None:
    if memory_manager is None:
        return None

    def _writer(record: Mapping[str, Any]) -> Any:
        text = str(record.get("text") or "").strip()
        if not text:
            raise ValueError("memory record text is empty")
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        entry = memory_manager.add_entry(
            text,
            source=str(record.get("source") or "universal_inbox"),
            category=str(record.get("category") or "document"),
            owner=owner,
        )
        entry["metadata"] = dict(metadata)
        memories = memory_manager.load_all()
        memories.append(entry)
        memory_manager.save(memories)
        if memory_vector is not None and getattr(memory_vector, "healthy", True):
            memory_vector.add(entry["id"], entry["text"])
        return entry

    return _writer


def _build_raptorgraph_event_writer(data_dir: str | Path) -> Callable[[Mapping[str, Any]], Any]:
    from src.universal_inbox_raptorgraph_store import build_universal_inbox_raptorgraph_writer

    root = Path(data_dir) / "universal_inbox_raptorgraph"
    return build_universal_inbox_raptorgraph_writer(root)


def _execute_telegram_memory_review_write(
    *,
    data_dir: str | Path,
    store: TelegramInboxStore,
    chat_id: str,
    memory_manager: Any = None,
    memory_vector: Any = None,
    memory_owner: str | None = None,
    dry_run: bool = False,
    confirmation_source: str = "manual_review",
) -> dict[str, Any]:
    try:
        from src.universal_inbox_memory_write_executor import execute_universal_inbox_memory_write_intent
    except Exception as exc:
        return {"status": "blocked", "reason": f"executor_unavailable:{str(exc)[:80]}"}
    intent = build_recent_telegram_memory_write_intent(data_dir=data_dir, store=store, chat_id=chat_id)
    if not intent:
        return {"status": "blocked", "reason": "memory_write_intent_missing"}
    writer = _build_native_memory_writer(memory_manager, memory_vector, owner=memory_owner or "telegram")
    raptorgraph_writer = _build_raptorgraph_event_writer(data_dir)
    try:
        report = execute_universal_inbox_memory_write_intent(
            intent,
            review_confirmed=True,
            confirmation_source=confirmation_source,
            dry_run=dry_run,
            memory_writer=writer,
            raptorgraph_writer=raptorgraph_writer,
        ).to_dict()
    except Exception as exc:
        return {
            "status": "blocked",
            "reason": str(exc)[:120],
            "writes_performed": False,
            "memory_records_written": 0,
        }
    return report


def _execute_telegram_memory_auto_write_if_ready(
    *,
    data_dir: str | Path,
    store: TelegramInboxStore,
    chat_id: str,
    inbox_attachment: Mapping[str, Any] | None,
    source_message_id: int | None = None,
    memory_manager: Any = None,
    memory_vector: Any = None,
    memory_owner: str | None = None,
) -> dict[str, Any] | None:
    if inbox_attachment is None:
        return None
    status = normalize_memory_write_intent_status(
        inbox_attachment.get("memory_write_intent_status") or "",
        fallback="unknown",
    )
    if status != "ready":
        return None
    if bool(inbox_attachment.get("maintenance_review_required")) and not _telegram_memory_auto_write_gate_is_clean(
        inbox_attachment
    ):
        return None
    execution = _execute_telegram_memory_review_write(
        data_dir=data_dir,
        store=store,
        chat_id=chat_id,
        memory_manager=memory_manager,
        memory_vector=memory_vector,
        memory_owner=memory_owner,
        dry_run=False,
        confirmation_source="auto_ready",
    )
    store.append_event(
        kind="universal_inbox_memory_auto_write",
        status=str(execution.get("status") or "blocked"),
        chat_id=chat_id,
        source_message_id=source_message_id,
        memory_write_intent_status=status,
        memory_records_written=int(execution.get("memory_records_written") or 0),
        raptorgraph_events_written=int(execution.get("raptorgraph_events_written") or 0),
        writes_performed=bool(execution.get("writes_performed")),
        raw_content_visible=False,
        raw_identifiers_visible=False,
        filename_visible=False,
    )
    return execution


def _telegram_memory_auto_write_gate_is_clean(inbox_attachment: Mapping[str, Any]) -> bool:
    """Allow ready redacted writes when no Inbox or extraction blocker remains."""

    if normalize_memory_write_intent_status(
        inbox_attachment.get("memory_write_intent_status") or "",
        fallback="unknown",
    ) != "ready":
        return False
    if str(inbox_attachment.get("universal_inbox_status") or "") != "go":
        return False
    if str(inbox_attachment.get("extraction_status") or "") != "completed":
        return False
    if tuple(inbox_attachment.get("extraction_warning_codes") or ()):
        return False
    if int(inbox_attachment.get("review_reason_count") or 0) > 0:
        return False
    if int(inbox_attachment.get("no_go_reason_count") or 0) > 0:
        return False
    return True


def build_telegram_live_voice_stt_provider(
    raw_message: dict[str, Any],
    *,
    voice_bytes_provider: Callable[..., bytes] | None = None,
) -> Callable[[str], str] | None:
    return _build_telegram_live_voice_stt_provider(
        raw_message,
        voice_bytes_provider=voice_bytes_provider,
        enabled=_bool_env("TELEGRAM_VOICE_DOWNLOAD_ENABLED") and _voice_stt_enabled(),
    )


def _sync_dsgvo_pin_state(
    *,
    command: str,
    chat_id: str,
    result: dict[str, Any] | None,
    reply_result: dict[str, Any] | None,
    store: TelegramInboxStore | None,
    pin_store: TelegramPrivacyPinStore | None,
) -> dict[str, Any]:
    if command not in {"dsgvo_enable", "dsgvo_disable", "dsgvo_status", "dsgvo_toggle"}:
        return {"status": "not_applicable"}
    if pin_store is None:
        return {"status": "pin_store_missing"}
    if not _privacy_pin_enabled():
        return {"status": "pin_disabled"}

    active_after = bool((result or {}).get("after") if result is not None else _dsgvo_mode_active())
    if active_after:
        if command not in {"dsgvo_enable", "dsgvo_status", "dsgvo_toggle"}:
            return {"status": "still_active"}
        existing = pin_store.get_pin(chat_id)
        if existing and int(existing.get("message_id") or 0) > 0:
            return {"status": "already_pinned", "message_id": int(existing["message_id"])}
        message_id = _reply_result_telegram_message_id(reply_result)
        if message_id is None:
            if store is not None:
                store.append_event(kind="privacy_pin", status="message_id_missing", chat_id=chat_id)
            return {"status": "message_id_missing"}
        try:
            sent = send_telegram_pin_message(chat_id, message_id)
        except Exception:
            if store is not None:
                store.append_event(kind="privacy_pin", status="pin_failed", chat_id=chat_id, message_id=message_id)
            return {"status": "pin_failed", "message_id": message_id}
        if not sent.get("ok"):
            if store is not None:
                store.append_event(kind="privacy_pin", status="pin_rejected", chat_id=chat_id, message_id=message_id)
            return {"status": "pin_rejected", "message_id": message_id}
        pin_store.set_pin(chat_id, message_id)
        if store is not None:
            store.append_event(kind="privacy_pin", status="pinned", chat_id=chat_id, message_id=message_id)
        return {"status": "pinned", "message_id": message_id}

    existing = pin_store.get_pin(chat_id)
    if not existing:
        return {"status": "no_pin_state"}
    message_id = int(existing.get("message_id") or 0)
    if message_id <= 0:
        pin_store.clear_pin(chat_id)
        return {"status": "pin_state_invalid"}
    try:
        sent = send_telegram_unpin_message(chat_id, message_id)
    except Exception:
        if store is not None:
            store.append_event(kind="privacy_pin", status="unpin_failed", chat_id=chat_id, message_id=message_id)
        return {"status": "unpin_failed", "message_id": message_id}
    if not sent.get("ok"):
        if store is not None:
            store.append_event(kind="privacy_pin", status="unpin_rejected", chat_id=chat_id, message_id=message_id)
        return {"status": "unpin_rejected", "message_id": message_id}
    pin_store.clear_pin(chat_id)
    if store is not None:
        store.append_event(kind="privacy_pin", status="unpinned", chat_id=chat_id, message_id=message_id)
    return {"status": "unpinned", "message_id": message_id}


def run_telegram_polling_cycle(
    *,
    data_dir: str | Path,
    fetch_updates: Callable[[int], list[dict[str, Any]]] | None = None,
    session_creator: Callable[..., Any] | None = None,
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
) -> dict[str, Any]:
    return run_telegram_polling_cycle_impl(
        data_dir=data_dir,
        fetch_updates=fetch_updates,
        session_creator=session_creator,
        agent_turn_handler=agent_turn_handler,
        voice_stt_provider=voice_stt_provider,
        voice_bytes_provider=voice_bytes_provider,
        image_bytes_provider=image_bytes_provider,
        attachment_bytes_provider=attachment_bytes_provider,
        image_worker_client=image_worker_client,
        reply_handler=reply_handler,
        document_reply_handler=document_reply_handler,
        memory_manager=memory_manager,
        memory_vector=memory_vector,
        memory_owner=memory_owner,
        project_registry_path=project_registry_path,
        polling_enabled=_bool_env,
        parse_update=lambda update: parse_telegram_update(update, chat_allowed=_chat_allowed),
        control_command=_telegram_control_command,
        handle_control_command=_handle_telegram_control_command,
        build_live_voice_stt_provider=build_telegram_live_voice_stt_provider,
        run_voice_pipeline=run_telegram_voice_pipeline,
        run_image_action=run_telegram_image_action,
        run_attachment_pipeline=run_telegram_universal_inbox_attachment_pipeline,
        attachment_spool_key=_telegram_attachment_spool_key,
        attachment_family=_telegram_attachment_family,
        attachment_suffix=_telegram_attachment_suffix,
        format_attachment_reply=format_telegram_attachment_inbox_reply,
        execute_memory_auto_write=_execute_telegram_memory_auto_write_if_ready,
        execute_attachment_export=execute_recent_telegram_attachment_export,
        format_attachment_export_reply=format_telegram_attachment_export_reply,
        build_project_intake_preview=build_telegram_project_intake_preview,
        format_project_intake_reply=format_telegram_project_intake_reply,
        build_recent_attachment_context=build_recent_telegram_attachment_context,
        build_agent_bridge_request=build_agent_bridge_request,
        send_typing_indicator=send_telegram_typing_indicator,
        execute_nextcloud_auto_transfer=_execute_telegram_nextcloud_auto_transfer_if_ready,
    )

def send_telegram_typing_indicator(
    chat_id: str,
    *,
    store: TelegramInboxStore | None = None,
) -> dict[str, Any] | None:
    if not _bool_env("TELEGRAM_AGENT_REPLY_ENABLED") or not _chat_allowed(chat_id):
        return None
    try:
        return send_telegram_chat_action(chat_id, "typing")
    except Exception as exc:
        if store is not None:
            store.append_event(
                kind="chat_action",
                status="failed",
                chat_id=chat_id,
                action="typing",
                error=str(exc)[:120],
            )
        return None


async def _telegram_typing_pulse_async(
    chat_id: str,
    *,
    store: TelegramInboxStore | None = None,
) -> tuple[asyncio.Event, asyncio.Task[None]]:
    stop = asyncio.Event()

    async def _pulse() -> None:
        interval = telegram_typing_keepalive_seconds()
        while not stop.is_set():
            try:
                await asyncio.to_thread(send_telegram_typing_indicator, chat_id, store=store)
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(_pulse())
    return stop, task


def _parse_tool_payload(content: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str) and content.strip():
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"text": content}
    return {}


def setup(ctx):
    router = APIRouter(prefix="/api/plugins/telegram", tags=["plugin:telegram"])
    store = TelegramInboxStore(ctx.data_dir)
    sessions = TelegramSessionBridgeStore(ctx.data_dir)
    privacy_pins = TelegramPrivacyPinStore(ctx.data_dir)

    def _ctx_attr(name: str, default: Any = None) -> Any:
        value = getattr(ctx, name, None)
        if value is not None:
            return value
        app_state = getattr(getattr(ctx, "app", None), "state", None)
        return getattr(app_state, name, default)

    session_creator = _ctx_attr("telegram_session_bridge")
    agent_turn_handler = _ctx_attr("telegram_agent_turn_handler")
    voice_stt_provider = _ctx_attr("telegram_voice_stt_provider")
    voice_bytes_provider = _ctx_attr("telegram_voice_bytes_provider")
    image_bytes_provider = _ctx_attr("telegram_image_bytes_provider")
    attachment_bytes_provider = _ctx_attr("telegram_attachment_bytes_provider")
    image_worker_client = _ctx_attr("telegram_image_worker_client")
    memory_manager = _ctx_attr("memory_manager")
    memory_vector = _ctx_attr("memory_vector")
    memory_owner = str(_ctx_attr("telegram_owner") or "telegram").strip() or "telegram"
    admin_gate = _ctx_attr("require_admin", require_admin) or require_admin

    def _require_admin(request: Request) -> None:
        admin_gate(request)

    def _reply_with_gate(
        chat_id: str,
        text: str,
        *,
        source_message_id: int | None = None,
        classification: Any = None,
        security_mode: Any = "",
        secure_transport: bool = False,
        can_start_secure_flow: bool = False,
    ) -> dict[str, Any]:
        if not _bool_env("TELEGRAM_AGENT_REPLY_ENABLED"):
            outbound = store.append_outbound(
                chat_id,
                text,
                source_message_id=source_message_id,
                delivery_status="blocked",
                failure_reason="reply_gate_disabled",
                delivery_mode="blocked",
                formatting_mode="html",
            )
            return {"error": "Telegram reply gate is disabled", "exit_code": 1, "message": outbound}
        if not _chat_allowed(chat_id):
            outbound = store.append_outbound(
                chat_id,
                text,
                source_message_id=source_message_id,
                delivery_status="blocked",
                failure_reason="chat_not_allowed",
                delivery_mode="blocked",
                formatting_mode="html",
            )
            return {"error": "Telegram chat id is not allowed", "exit_code": 1, "message": outbound}
        if classification is not None or str(security_mode or "").strip():
            state = ChatSecurityState.create(
                chat_id=f"telegram-{_stable_handle('chat', chat_id)}",
                thread_id=f"telegram-{_stable_handle('chat', chat_id)}",
                security_mode=str(security_mode or "normal"),
                created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                requested_by="telegram-runtime",
            )
            channel = ChannelContext.create(
                channel_id=f"telegram-{_stable_handle('chat', chat_id)}",
                channel_kind="telegram",
                secure_transport=secure_transport,
                user_allowlisted=True,
                can_start_secure_flow=can_start_secure_flow,
            )
            policy = decide_channel_access(
                state=state,
                channel=channel,
                classification=classification,
            )
            if not policy.allowed:
                outbound = store.append_outbound(
                    chat_id,
                    text,
                    source_message_id=source_message_id,
                    delivery_status="blocked",
                    failure_reason=policy.block_reason,
                    delivery_mode="blocked",
                    formatting_mode="html",
                )
                return {"error": policy.block_reason, "exit_code": 1, "message": outbound}
        truth_gate = gate_telegram_reply_text(text, repo_root=Path.cwd())
        text = truth_gate.text
        try:
            if _bool_env("TELEGRAM_RICH_MESSAGES_ENABLED"):
                try:
                    sent = send_telegram_rich_message(chat_id, text)
                except Exception as rich_exc:
                    sent = send_telegram_text(chat_id, text)
                    sent["delivery_mode"] = f"{sent.get('delivery_mode')}_fallback"
                    sent["rich_fallback_reason"] = str(rich_exc)[:120]
            else:
                sent = send_telegram_text(chat_id, text)
        except Exception as exc:
            outbound = store.append_outbound(
                chat_id,
                text,
                source_message_id=source_message_id,
                delivery_status="failed",
                failure_reason=str(exc),
                delivery_mode="classic",
                formatting_mode="html",
                truth_gate=truth_gate.to_dict(),
            )
            return {"error": str(exc), "exit_code": 1, "message": outbound}
        outbound = store.append_outbound(
            chat_id,
            text,
            source_message_id=source_message_id,
            delivery_status="sent",
            delivery_mode=str(sent.get("delivery_mode") or "classic"),
            formatting_mode=str(sent.get("formatting_mode") or "plaintext"),
            truth_gate=truth_gate.to_dict(),
        )
        return {
            "output": json.dumps({"sent": sent, "message": outbound}, ensure_ascii=False),
            "exit_code": 0,
        }

    def _document_reply_with_gate(
        chat_id: str,
        file_path: str,
        filename: str,
        caption: str,
        source_message_id: int | None = None,
    ) -> dict[str, Any]:
        if not _bool_env("TELEGRAM_AGENT_REPLY_ENABLED"):
            outbound = store.append_outbound(
                chat_id,
                caption or "Dokument-Export blockiert.",
                source_message_id=source_message_id,
                delivery_status="blocked",
                failure_reason="reply_gate_disabled",
                delivery_mode="document_blocked",
                formatting_mode="document_caption",
            )
            return {"error": "Telegram reply gate is disabled", "exit_code": 1, "message": outbound}
        if not _chat_allowed(chat_id):
            outbound = store.append_outbound(
                chat_id,
                caption or "Dokument-Export blockiert.",
                source_message_id=source_message_id,
                delivery_status="blocked",
                failure_reason="chat_not_allowed",
                delivery_mode="document_blocked",
                formatting_mode="document_caption",
            )
            return {"error": "Telegram chat id is not allowed", "exit_code": 1, "message": outbound}
        try:
            if _is_telegram_photo_artifact(filename or file_path):
                sent = send_telegram_photo(chat_id, file_path, filename=filename, caption=caption)
            else:
                sent = send_telegram_document(chat_id, file_path, filename=filename, caption=caption)
        except Exception as exc:
            outbound = store.append_outbound(
                chat_id,
                caption or "Dokument-Export fehlgeschlagen.",
                source_message_id=source_message_id,
                delivery_status="failed",
                failure_reason=str(exc),
                delivery_mode="document",
                formatting_mode="document_caption",
            )
            return {"error": str(exc), "exit_code": 1, "message": outbound}
        if not bool(sent.get("ok")):
            outbound = store.append_outbound(
                chat_id,
                caption or "Dokument-Export fehlgeschlagen.",
                source_message_id=source_message_id,
                delivery_status="failed",
                failure_reason="telegram_document_not_ok",
                delivery_mode="document",
                formatting_mode="document_caption",
            )
            return {"error": "telegram_document_not_ok", "exit_code": 1, "message": outbound, "ok": False}
        outbound = store.append_outbound(
            chat_id,
            caption or "Dokument-Export gesendet.",
            source_message_id=source_message_id,
            delivery_status="sent",
            delivery_mode=str(sent.get("delivery_mode") or "document"),
            formatting_mode=str(sent.get("formatting_mode") or "document_caption"),
        )
        return {
            "output": json.dumps({"sent": sent, "message": outbound}, ensure_ascii=False),
            "exit_code": 0,
            "ok": bool(sent.get("ok")),
        }

    def _is_telegram_photo_artifact(value: str) -> bool:
        suffix = Path(str(value or "")).suffix.lower()
        return suffix in {".jpg", ".jpeg", ".png", ".webp"}

    def _resolve_telegram_artifact_ref(artifact_ref: str) -> Path:
        ref = str(artifact_ref or "").strip().replace("\\", "/")
        if (
            not ref
            or ref.startswith("/")
            or re.match(r"^[A-Za-z]:", ref)
            or ".." in ref.split("/")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,220}", ref)
        ):
            raise ValueError("telegram artifact_ref is unsafe")
        if not (ref.startswith("data/reports/autonomous_coding_agent/") or ref.startswith("reports/")):
            raise ValueError("telegram artifact_ref is outside allowed artifact roots")
        root = Path.cwd().resolve()
        path = (Path.cwd() / ref).resolve()
        if root != path and root not in path.parents:
            raise ValueError("telegram artifact_ref escapes workspace")
        if not path.exists() or not path.is_file() or path.is_symlink():
            raise ValueError("telegram artifact_ref does not exist")
        return path

    def _telegram_nextcloud_max_download_bytes() -> int:
        try:
            value = int(str(os.getenv("TELEGRAM_NEXTCLOUD_MAX_FILE_BYTES") or "").strip())
        except ValueError:
            value = 50 * 1024 * 1024
        return max(1, min(value, 50 * 1024 * 1024))

    def _safe_telegram_download_filename(value: Any, *, fallback: str = "nextcloud-file.bin") -> str:
        name = Path(str(value or fallback or "nextcloud-file.bin").replace("\\", "/")).name
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
        if not safe:
            safe = fallback
        if "." not in safe:
            safe = f"{safe}.bin"
        return safe[:120] or "nextcloud-file.bin"

    def _spool_nextcloud_telegram_artifact(relative_path: str, *, filename: Any = "") -> str:
        raw_path = str(relative_path or "").strip()
        if not raw_path:
            raise ValueError("telegram nextcloud_path is required")
        from src.nextcloud_webdav_client import build_nextcloud_webdav_client_from_env

        client = _ctx_attr("telegram_nextcloud_webdav_client")
        owns_client = client is None
        if client is None:
            client = build_nextcloud_webdav_client_from_env()
        try:
            payload = client.get_file_bytes(
                raw_path,
                max_bytes=_telegram_nextcloud_max_download_bytes(),
            )
        finally:
            if owns_client and hasattr(client, "close"):
                client.close()

        safe_filename = _safe_telegram_download_filename(
            filename,
            fallback=_safe_telegram_download_filename(raw_path),
        )
        digest = hashlib.sha256(raw_path.encode("utf-8", errors="replace")).hexdigest()[:16]
        artifact_ref = f"data/reports/autonomous_coding_agent/telegram_nextcloud/{digest}/{safe_filename}"
        destination = (Path.cwd() / artifact_ref).resolve()
        root = Path.cwd().resolve()
        if root != destination and root not in destination.parents:
            raise ValueError("telegram nextcloud artifact escapes workspace")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return artifact_ref

    def _notification_target() -> str:
        return str(_ctx_attr("telegram_notification_target") or os.getenv("TELEGRAM_NOTIFICATION_CHAT_ID") or "")

    async def _telegram_reply_tool(content: str, **kwargs):
        payload = _parse_tool_payload(content)
        chat_id = str(payload.get("chat_id") or "")
        text = str(payload.get("text") or "")
        source_message_id = payload.get("source_message_id")
        return _reply_with_gate(
            chat_id,
            text,
            source_message_id=source_message_id,
            classification=payload.get("classification"),
            security_mode=payload.get("security_mode") or "",
            secure_transport=bool(payload.get("secure_transport")),
            can_start_secure_flow=bool(payload.get("can_start_secure_flow")),
        )

    async def _telegram_document_reply_tool(content: str, **kwargs):
        payload = _parse_tool_payload(content)
        if kwargs:
            payload = {**payload, **kwargs}
        chat_id = str(payload.get("chat_id") or "")
        artifact_ref = str(payload.get("artifact_ref") or "")
        nextcloud_path = str(payload.get("nextcloud_path") or "")
        preview_only = bool(payload.get("preview_only"))
        if artifact_ref and nextcloud_path:
            raise ValueError("telegram document reply accepts artifact_ref or nextcloud_path, not both")
        if nextcloud_path and preview_only:
            return {
                "output": json.dumps(
                    {
                        "preview_only": True,
                        "source": "nextcloud",
                        "delivery_mode": "document",
                        "dispatch_allowed": _bool_env("TELEGRAM_AGENT_REPLY_ENABLED") and _chat_allowed(chat_id),
                        "live_fetch_required": True,
                        "raw_nextcloud_path_visible": False,
                        "token_value_visible": False,
                        "chat_target_value_visible": False,
                    },
                    ensure_ascii=False,
                ),
                "exit_code": 0,
            }
        if nextcloud_path:
            artifact_ref = _spool_nextcloud_telegram_artifact(
                nextcloud_path,
                filename=payload.get("filename") or "",
            )
            payload = {**payload, "artifact_ref": artifact_ref}
        if not artifact_ref:
            raise ValueError("telegram document reply requires artifact_ref or nextcloud_path")
        artifact_path = _resolve_telegram_artifact_ref(artifact_ref)
        delivery_packet = None
        if _is_telegram_photo_artifact(artifact_ref):
            delivery_packet = build_telegram_screenshot_delivery_packet(
                artifact_ref,
                repo_root=Path.cwd(),
                filename=payload.get("filename") or artifact_path.name,
                caption=payload.get("caption") or "Sandbox-Artefakt",
                reply_enabled=_bool_env("TELEGRAM_AGENT_REPLY_ENABLED"),
                target_configured=_chat_allowed(chat_id),
            )
            if preview_only:
                return {
                    "output": json.dumps(
                        {
                            "preview_only": True,
                            "delivery_packet": delivery_packet.to_dict(),
                            "token_value_visible": False,
                            "chat_target_value_visible": False,
                        },
                        ensure_ascii=False,
                    ),
                    "exit_code": 0,
                    "delivery_packet": delivery_packet.to_dict(),
                }
            if delivery_packet.integrity_status != "verified":
                return {
                    "error": delivery_packet.blocker or "telegram screenshot artifact failed integrity",
                    "exit_code": 1,
                    "delivery_packet": delivery_packet.to_dict(),
                }
        elif preview_only:
            return {
                "output": json.dumps(
                    {
                        "preview_only": True,
                        "artifact_ref": artifact_ref,
                        "delivery_mode": "document",
                        "dispatch_allowed": False,
                        "blocker": "document_preview_only",
                        "token_value_visible": False,
                        "chat_target_value_visible": False,
                    },
                    ensure_ascii=False,
                ),
                "exit_code": 0,
            }
        result = _document_reply_with_gate(
            chat_id,
            str(artifact_path),
            str(payload.get("filename") or artifact_path.name),
            str(payload.get("caption") or "Sandbox-Artefakt"),
            source_message_id=payload.get("source_message_id"),
        )
        if delivery_packet and result.get("output"):
            output = json.loads(str(result["output"]))
            output["delivery_packet"] = delivery_packet.to_dict()
            result["output"] = json.dumps(output, ensure_ascii=False)
        if delivery_packet:
            result["delivery_packet"] = delivery_packet.to_dict()
        return result

    async def _odysseus_notify_user_tool(content: str, **kwargs):
        payload = _parse_tool_payload(content)
        if kwargs:
            payload = {**payload, **kwargs}
        target = _notification_target()
        try:
            decision = build_user_notification_decision(
                payload,
                configured_channels=("telegram",),
                live_dispatch_enabled=_bool_env("TELEGRAM_AGENT_REPLY_ENABLED"),
                target_configured=bool(target),
            )
        except NotificationContractError as exc:
            return {
                "error": str(exc),
                "exit_code": 1,
                "token_value_visible": False,
                "chat_target_value_visible": False,
            }
        public = decision.as_public_dict()
        if not decision.dispatch_allowed:
            return {
                "output": json.dumps(public, ensure_ascii=False),
                "exit_code": 0,
            }
        result = _reply_with_gate(target, decision.rendered_text)
        if result.get("exit_code") != 0:
            public["status"] = "blocked"
            public["dispatch_allowed"] = False
            public["reason"] = str(result.get("error") or "telegram_dispatch_refused")
            return {
                "output": json.dumps(public, ensure_ascii=False),
                "exit_code": 0,
            }
        public["status"] = "sent"
        public["dispatch_allowed"] = True
        public["reason"] = "server_side_dispatch_sent"
        return {
            "output": json.dumps(public, ensure_ascii=False),
            "exit_code": 0,
        }

    register_telegram_admin_routes(
        router,
        data_dir=ctx.data_dir,
        inbox_store=store,
        require_admin=_require_admin,
        build_readiness=build_telegram_readiness,
    )

    register_telegram_polling_routes(
        router,
        data_dir=ctx.data_dir,
        require_admin=_require_admin,
        run_polling_cycle=run_telegram_polling_cycle,
        fetch_updates=_ctx_attr("telegram_fetch_updates"),
        session_creator=session_creator,
        agent_turn_handler=agent_turn_handler,
        voice_stt_provider=voice_stt_provider,
        voice_bytes_provider=voice_bytes_provider,
        image_bytes_provider=image_bytes_provider,
        attachment_bytes_provider=attachment_bytes_provider,
        image_worker_client=image_worker_client,
        reply_handler=lambda chat_id, text, source_message_id=None: _reply_with_gate(
            chat_id,
            text,
            source_message_id=source_message_id,
        ),
        document_reply_handler=lambda chat_id, file_path, filename, caption, source_message_id=None: _document_reply_with_gate(
            chat_id,
            file_path,
            filename,
            caption,
            source_message_id=source_message_id,
        ),
        memory_manager=memory_manager,
        memory_vector=memory_vector,
        memory_owner=memory_owner,
        project_registry_path=Path(ctx.data_dir) / _PROJECT_REGISTRY_FILE,
    )

    async def _handle_telegram_webhook(request: Request):
        update = await request.json()
        try:
            message, stored = parse_and_store_webhook_update(
                update,
                store=store,
                parse_update=lambda item: parse_telegram_update(item, chat_allowed=_chat_allowed),
            )
        except TelegramWebhookIntakeError as exc:
            raise HTTPException(400, "invalid telegram update") from exc
        voice_agent_turn, voice_pipeline, image_action = run_webhook_media_pipelines(
            message=message,
            stored_message=stored["message"],
            voice_stt_provider=voice_stt_provider,
            voice_bytes_provider=voice_bytes_provider,
            image_bytes_provider=image_bytes_provider,
            image_worker_client=image_worker_client,
            image_actions_enabled=_bool_env("TELEGRAM_IMAGE_ACTIONS_ENABLED"),
            build_live_voice_stt_provider=lambda item: build_telegram_live_voice_stt_provider(
                item,
                voice_bytes_provider=voice_bytes_provider,
            ),
            run_voice_pipeline=run_telegram_voice_pipeline,
            run_image_action=run_telegram_image_action,
        )
        inbox_attachment, _attachment_reply = run_webhook_attachment_branch(
            message=message,
            stored=stored,
            data_dir=ctx.data_dir,
            store=store,
            attachment_bytes_provider=attachment_bytes_provider,
            memory_manager=_ctx_attr("memory_manager"),
            memory_vector=_ctx_attr("memory_vector"),
            memory_owner=_ctx_attr("memory_owner"),
            run_attachment_pipeline=run_telegram_universal_inbox_attachment_pipeline,
            attachment_spool_key=_telegram_attachment_spool_key,
            attachment_family=_telegram_attachment_family,
            attachment_suffix=_telegram_attachment_suffix,
            execute_memory_auto_write_if_ready=_execute_telegram_memory_auto_write_if_ready,
            execute_nextcloud_auto_transfer_if_ready=_execute_telegram_nextcloud_auto_transfer_if_ready,
            format_attachment_reply=format_telegram_attachment_inbox_reply,
            reply_with_gate=_reply_with_gate,
        )
        recent_attachment_context = build_recent_telegram_attachment_context(
            data_dir=ctx.data_dir,
            store=store,
            chat_id=str(message.get("chat_id") or ""),
        ) if stored["message"].get("kind") == "text" else None
        bridge = build_agent_bridge_request(
            stored["message"],
            raw_chat_id=str(message.get("chat_id") or ""),
            voice_agent_turn=voice_agent_turn,
            recent_attachment_context=recent_attachment_context,
        )
        control_result = run_webhook_control_command_branch(
            message=message,
            stored_message=stored["message"],
            raw_chat_id=str(message.get("chat_id") or ""),
            sessions=sessions,
            session_creator=session_creator,
            reply_handler=lambda chat_id, text, source_message_id=None: _reply_with_gate(
                chat_id,
                text,
                source_message_id=source_message_id,
            ),
            store=store,
            pin_store=privacy_pins,
            memory_manager=memory_manager,
            memory_vector=memory_vector,
            memory_owner=memory_owner,
            project_registry_path=Path(ctx.data_dir) / _PROJECT_REGISTRY_FILE,
            detect_control_command=_telegram_control_command,
            handle_control_command=_handle_telegram_control_command,
        )
        if control_result is not None:
            return build_webhook_response_payload(
                stored=stored,
                agent_bridge=bridge,
                voice_pipeline=voice_pipeline,
                image_action=image_action,
                universal_inbox_attachment=inbox_attachment,
                agent_turn=None,
                reply=_public_reply_result(control_result.get("reply")),
                extra={
                    "control_command": build_webhook_control_command_summary(control_result)
                },
            )
        export_plan, reply_result = run_webhook_attachment_export_branch(
            message=message,
            stored_message=stored["message"],
            data_dir=ctx.data_dir,
            store=store,
            execute_attachment_export=execute_recent_telegram_attachment_export,
            document_reply_with_gate=_document_reply_with_gate,
            reply_with_gate=_reply_with_gate,
            format_export_reply=format_telegram_attachment_export_reply,
        )
        if export_plan is not None:
            return build_webhook_response_payload(
                stored=stored,
                agent_bridge=bridge,
                voice_pipeline=voice_pipeline,
                image_action=image_action,
                universal_inbox_attachment=inbox_attachment,
                agent_turn=None,
                reply=_public_reply_result(reply_result),
                extra={
                    "universal_inbox_export_plan": build_webhook_export_plan_summary(export_plan)
                },
            )
        project_intake, reply_result = run_webhook_project_intake_branch(
            message=message,
            stored_message=stored["message"],
            data_dir=ctx.data_dir,
            store=store,
            sessions=sessions,
            project_registry_path=Path(ctx.data_dir) / _PROJECT_REGISTRY_FILE,
            build_project_intake_preview=build_telegram_project_intake_preview,
            format_project_intake_reply=format_telegram_project_intake_reply,
            reply_with_gate=_reply_with_gate,
        )
        if project_intake is not None:
            return build_webhook_response_payload(
                stored=stored,
                agent_bridge=bridge,
                voice_pipeline=voice_pipeline,
                image_action=image_action,
                universal_inbox_attachment=inbox_attachment,
                agent_turn=None,
                reply=_public_reply_result(reply_result),
                extra={
                    "project_intake": build_webhook_project_intake_summary(project_intake)
                },
            )
        bridge, agent_turn, reply_result = await run_webhook_agent_turn_branch(
            stored_message=stored["message"],
            bridge=bridge,
            raw_chat_id=str(message.get("chat_id") or ""),
            sessions=sessions,
            session_creator=session_creator,
            store=store,
            voice_agent_turn=voice_agent_turn,
            recent_attachment_context=recent_attachment_context,
            agent_turn_handler=agent_turn_handler,
            build_agent_bridge_request=build_agent_bridge_request,
            deterministic_agent_turn=deterministic_telegram_agent_turn,
            run_agent_turn_async=_run_agent_turn_async,
            typing_pulse=_telegram_typing_pulse_async,
            agent_failure_reply=_agent_failure_reply,
            reply_with_gate=_reply_with_gate,
        )
        return build_webhook_response_payload(
            stored=stored,
            agent_bridge=bridge,
            voice_pipeline=voice_pipeline,
            image_action=image_action,
            universal_inbox_attachment=inbox_attachment,
            agent_turn=_public_agent_turn_result(agent_turn),
            reply=_public_reply_result(reply_result),
        )

    register_telegram_webhook_routes(
        router,
        require_admin=_require_admin,
        handle_webhook=_handle_telegram_webhook,
    )

    register_telegram_outbound_routes(
        router,
        require_admin=_require_admin,
        reply_with_gate=_reply_with_gate,
        document_reply_tool=_telegram_document_reply_tool,
        build_screenshot_live_gate_packet=build_telegram_screenshot_live_gate_packet,
    )

    ctx.add_router(router)
    try:
        from src.tool_registry import ToolSpec

        ctx.register_tool(ToolSpec(
            name="telegram_reply",
            description=(
                "Send a Telegram reply through the bot for an inbound Telegram agent chat. "
                "Requires TELEGRAM_AGENT_REPLY_ENABLED and an allowed chat id; never expose token values."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string", "description": "Telegram chat id from the stored inbound message."},
                    "text": {"type": "string", "description": "Reply text to send through the Telegram bot."},
                    "source_message_id": {"type": "integer", "description": "Optional Telegram source message id."},
                    "classification": {"type": "string", "enum": ["public", "private", "sensitive", "secret"], "description": "Optional data classification for secure channel policy."},
                    "security_mode": {"type": "string", "enum": ["normal", "secure"], "description": "Optional chat security mode for secure channel policy."},
                },
                "required": ["chat_id", "text"],
            },
            execute=_telegram_reply_tool,
            permission="admin",
        ))
        ctx.register_tool(ToolSpec(
            name="telegram_document_reply",
            description=(
                "Send a reviewed sandbox/export artifact through Telegram. "
                "Accepts repo-relative artifact_ref values under data/reports/autonomous_coding_agent or reports, "
                "or a server-side nextcloud_path fetched through configured WebDAV. PNG/JPG/WebP artifacts are sent as Telegram photos."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "chat_id": {"type": "string", "description": "Telegram chat id from the stored inbound message."},
                    "artifact_ref": {"type": "string", "description": "Repo-relative artifact path, for example data/reports/autonomous_coding_agent/demo/screen.png."},
                    "nextcloud_path": {"type": "string", "description": "Optional server-side Nextcloud/WebDAV relative path to fetch and send as a Telegram document."},
                    "filename": {"type": "string", "description": "Optional safe filename for Telegram."},
                    "caption": {"type": "string", "description": "Optional short caption."},
                    "source_message_id": {"type": "integer", "description": "Optional Telegram source message id."},
                    "preview_only": {"type": "boolean", "description": "Build a redacted delivery preview without sending to Telegram."},
                },
                "required": ["chat_id"],
            },
            execute=_telegram_document_reply_tool,
            permission="admin",
        ))
        ctx.register_tool(ToolSpec(
            name="odysseus_notify_user",
            description=(
                "Request a user-facing Odysseus notification. Delivery targets stay server-side; "
                "the tool defaults to dry-run and rejects token, secret, or chat target arguments."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "event": {"type": "string", "description": "Short event name, for example roadmap_completed."},
                    "message": {"type": "string", "description": "Redacted user-facing message."},
                    "severity": {"type": "string", "enum": ["info", "success", "warning", "error"]},
                    "channel": {"type": "string", "enum": ["auto", "telegram"]},
                    "dry_run": {"type": "boolean", "description": "Defaults to true; false still requires server gates."},
                    "metadata": {"type": "object", "description": "Optional redacted metadata only."},
                },
                "required": ["message"],
            },
            execute=_odysseus_notify_user_tool,
            permission="admin",
        ))
    except Exception as exc:
        ctx.logger.warning("telegram tool registration skipped: %s", exc)
    ctx.logger.info("telegram plugin ready")

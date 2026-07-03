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
from src.telegram_image_actions import run_telegram_image_action
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
from fastapi.responses import HTMLResponse
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
    "permission": "admin",
    "kind": "ui",
    "capabilities": ["local_api"],
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
    active = bool((result or {}).get("after") if result is not None else _dsgvo_mode_active())
    if command == "dsgvo_help":
        return "Nutze /dsgvo zum Umschalten, oder /dsgvo status fuer den aktuellen Zustand."
    if command == "dsgvo_enable":
        return (
            "DSGVO-Modus ist jetzt aktiv. Telegram laeuft local-only; "
            "externe Web-, Provider- und Tool-I/O ist gesperrt."
        )
    if command == "dsgvo_disable" and (result or {}).get("forced_active"):
        return (
            "DSGVO-Modus bleibt aktiv, weil ein Server- oder Kompatibilitaets-Gate "
            "ihn erzwingt."
        )
    if command == "dsgvo_disable":
        return "DSGVO-Modus ist jetzt aus. Normale Provider- und Tool-Regeln gelten wieder."
    if command == "dsgvo_toggle":
        if (result or {}).get("forced_active"):
            return (
                "DSGVO-Modus bleibt aktiv, weil ein Server- oder Kompatibilitaets-Gate "
                "ihn erzwingt."
            )
        return (
            "DSGVO-Modus ist jetzt aktiv. Telegram laeuft local-only; "
            "externe Web-, Provider- und Tool-I/O ist gesperrt."
        ) if active else "DSGVO-Modus ist jetzt aus. Normale Provider- und Tool-Regeln gelten wieder."
    return (
        "DSGVO-Modus ist aktiv. Telegram nutzt local-only Verarbeitung."
        if active
        else "DSGVO-Modus ist aus."
    )


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


from plugins.telegram.admin import app_html as _app_html, build_telegram_readiness as _build_telegram_readiness
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
    fetch_telegram_updates,
    run_telegram_polling_cycle_impl,
)
from plugins.telegram.project_intake import (
    _apply_telegram_project_intake_review,
    _format_project_intake_review_status,
    build_telegram_project_intake_preview,
    format_telegram_project_intake_reply,
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
from src.agent_task_ledger import read_task_records, record_task_event
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
    records = read_task_records(limit=5)
    latest = records.get("records", [None])[0] if records.get("records") else None
    if command == "agent_task_help":
        return {
            "status": "agent_task_help",
            "reply_text": "Task-Kommandos: /task status, /task pause, /task resume, /task cancel.",
            "agent_task": {"raw_content_visible": False},
        }
    if command == "agent_task_status":
        if not latest:
            return {
                "status": "agent_task_missing",
                "reply_text": "Ich finde aktuell keinen laufenden Agent-Task.",
                "agent_task": {"raw_content_visible": False},
            }
        task_id = str(latest.get("task_id") or "")
        task_type = str(latest.get("task_type") or "unknown")
        status = str(latest.get("status") or "unknown")
        progress = int(latest.get("progress_percent") or 0)
        gates = tuple(str(item) for item in latest.get("gates_waiting") or ())
        gate_text = f" Gates: {', '.join(gates[:3])}." if gates else ""
        return {
            "status": "agent_task_status",
            "reply_text": f"Letzter Task {task_id}: {task_type}, Status {status}, Fortschritt {progress}%.{gate_text}",
            "agent_task": _public_agent_task_record(latest),
        }
    if command not in {"agent_task_pause", "agent_task_resume", "agent_task_cancel"}:
        return {
            "status": "agent_task_unknown_command",
            "reply_text": "Task-Kommando nicht erkannt. Nutze /task status.",
            "agent_task": {"raw_content_visible": False},
        }
    if not latest:
        return {
            "status": "agent_task_missing",
            "reply_text": "Ich finde keinen Agent-Task, auf den ich das anwenden kann.",
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
        "reply_text": f"{action_text} fuer Task {record.get('task_id')}.",
        "agent_task": _public_agent_task_record(record),
    }


def _public_agent_task_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(record.get("task_id") or ""),
        "task_type": str(record.get("task_type") or ""),
        "status": str(record.get("status") or ""),
        "target_ref": str(record.get("target_ref") or ""),
        "progress_percent": int(record.get("progress_percent") or 0),
        "gates_waiting": tuple(str(item) for item in record.get("gates_waiting") or ()),
        "raw_content_visible": False,
    }


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
        result = None
        if command == "dsgvo_enable":
            result = _set_dsgvo_mode(True)
            status = "dsgvo_enabled" if result.get("after") else "dsgvo_enable_failed"
        elif command == "dsgvo_disable":
            result = _set_dsgvo_mode(False)
            status = "dsgvo_forced_active" if result.get("forced_active") else "dsgvo_disabled"
        elif command == "dsgvo_toggle":
            result = _set_dsgvo_mode(not _dsgvo_mode_active())
            if result.get("forced_active"):
                status = "dsgvo_forced_active"
            else:
                status = "dsgvo_enabled" if result.get("after") else "dsgvo_disabled"
        else:
            status = "dsgvo_status" if command == "dsgvo_status" else "dsgvo_help"
        reply_text = _dsgvo_reply_text(command, result)
        bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
        reply_result = None
        if reply_handler is not None and bridge["chat_id"]:
            reply_result = reply_handler(
                bridge["chat_id"],
                reply_text,
                bridge.get("source_message_id"),
            )
        pin_result = _sync_dsgvo_pin_state(
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
            "dsgvo_mode": bool((result or {}).get("after") if result is not None else _dsgvo_mode_active()),
            "pin_status": pin_result.get("status"),
        }
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
    if command == "universal_inbox_status":
        snapshot = build_universal_inbox_readiness()
        reply_text = format_universal_inbox_readiness_for_telegram(snapshot)
        bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
        reply_result = None
        if reply_handler is not None and bridge["chat_id"]:
            reply_result = reply_handler(
                bridge["chat_id"],
                reply_text,
                bridge.get("source_message_id"),
            )
        return {
            "command": command,
            "status": f"universal_inbox_{snapshot.get('status') or 'blocked'}",
            "binding": {},
            "reply_text": reply_text,
            "reply": reply_result,
            "universal_inbox": snapshot,
        }
    if command in {"universal_inbox_review_status", "universal_inbox_review_confirm"}:
        bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
        review = store.latest_universal_inbox_review(chat_id=bridge["chat_id"]) if store is not None else None
        if review is None:
            reply_text = "Keine offene Universal-Inbox-Review gefunden."
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
                _build_recent_telegram_nextcloud_transfer_dry_run(
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
            if transfer_status == "completed":
                reply_text = "Review bestaetigt. Nextcloud-Ablage wurde kopiert und verifiziert."
            elif transfer_status == "copied_unverified":
                reply_text = "Review bestaetigt. Nextcloud-Ablage wurde kopiert, braucht aber Verifikation."
            elif transfer_status == "dry_run_ready":
                reply_text = (
                    "Review bestaetigt. Nextcloud-Ablage ist vorbereitet, aber noch Dry-run. "
                    "Live-Copy wartet auf Operator-Go."
                )
            else:
                reason = str(transfer.get("reason") or transfer.get("status") or "unknown")
                reply_text = f"Review bestaetigt. Nextcloud-Ablage ist noch blockiert: {reason}."
            status = "universal_inbox_review_confirmed"
        else:
            reply_text = _format_universal_inbox_review_status(review)
            status = "universal_inbox_review_status"
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
            "nextcloud_transfer": transfer if command == "universal_inbox_review_confirm" and review is not None else None,
        }
    if command in {"universal_inbox_memory_review_status", "universal_inbox_memory_review_confirm"}:
        bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
        review = store.latest_universal_inbox_memory_review(chat_id=bridge["chat_id"]) if store is not None else None
        if review is None:
            reply_text = "Keine offene Universal-Inbox-Memory-Review gefunden."
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
                _execute_telegram_memory_review_write(
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
            if str(execution.get("status") or "") == "written":
                reply_text = "Memory-Review bestaetigt. Die redaktierte Abstraktion wurde ins Langzeitgedaechtnis geschrieben."
            else:
                reason = str(execution.get("reason") or execution.get("status") or "unknown")
                reply_text = f"Memory-Review bestaetigt, aber der Memory-Write wurde blockiert: {reason}."
            status = "universal_inbox_memory_review_confirmed"
        else:
            reply_text = _format_universal_inbox_memory_review_status(review)
            status = "universal_inbox_memory_review_status"
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
            "memory_write": execution if command == "universal_inbox_memory_review_confirm" and review is not None else None,
        }
    if command.startswith("project_intake_"):
        bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
        review = store.latest_project_intake_review(chat_id=bridge["chat_id"]) if store is not None else None
        status = "project_intake_review_status"
        if command == "project_intake_review_confirm":
            if review is None:
                reply_text = "Keine offene Project-Intake-Review gefunden."
                status = "project_intake_review_missing"
            else:
                apply_report = _apply_telegram_project_intake_review(
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
                    merge_report = apply_report.get("intake_merge") if isinstance(apply_report.get("intake_merge"), dict) else {}
                    reply_text = (
                        "Project-Intake bestaetigt und ins Projekt-Intake-Ledger uebernommen. "
                        f"Integriert: {int(merge_report.get('added_task_count') or 0)} neue Tasks, "
                        f"{int(merge_report.get('added_risk_count') or 0)} Risiken, "
                        f"{int(merge_report.get('added_roadmap_update_count') or 0)} Roadmap-Updates."
                    )
                    status = "project_intake_review_confirmed"
                else:
                    blockers = ", ".join(str(item) for item in apply_report.get("blockers") or ("apply_blocked",))
                    reply_text = f"Project-Intake bestaetigt, aber Apply ist blockiert: {blockers}."
                    status = "project_intake_review_apply_blocked"
        elif command == "project_intake_review_hold":
            if review is None:
                reply_text = "Keine offene Project-Intake-Review gefunden."
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
                reply_text = "Project-Intake pausiert. Ich schreibe nichts in das Projekt."
                status = "project_intake_review_held"
        else:
            reply_text = _format_project_intake_review_status(review)
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
    reply_text = "Neuer Chat gestartet." if created else "Neuer Chat konnte nicht gestartet werden."
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
    if not spool_dir.exists() or not spool_dir.is_dir():
        return {
            "status": "missing_spool",
            "family": family,
            "suffix": suffix,
            "universal_inbox_status": str(event.get("universal_inbox_status") or ""),
            "memory_write_intent_status": memory_status,
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
        from src.nextcloud_webdav_client import build_nextcloud_webdav_client_from_env
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
        client = build_nextcloud_webdav_client_from_env() if operator_live_go else None
        try:
            return execute_universal_inbox_nextcloud_transfer(request, client=client).to_dict()
        finally:
            if client is not None:
                client.close()
    except Exception as exc:
        return {
            "status": "blocked",
            "reason": f"nextcloud_transfer_plan_failed:{str(exc)[:80]}",
            "dry_run": True,
            "writes_performed": False,
        }


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
            )
            return {"error": str(exc), "exit_code": 1, "message": outbound}
        outbound = store.append_outbound(
            chat_id,
            text,
            source_message_id=source_message_id,
            delivery_status="sent",
            delivery_mode=str(sent.get("delivery_mode") or "classic"),
            formatting_mode=str(sent.get("formatting_mode") or "plaintext"),
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
            delivery_mode="document",
            formatting_mode="document_caption",
        )
        return {
            "output": json.dumps({"sent": sent, "message": outbound}, ensure_ascii=False),
            "exit_code": 0,
            "ok": bool(sent.get("ok")),
        }

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

    @router.get("/status")
    async def status(request: Request):
        _require_admin(request)
        return build_telegram_readiness(ctx.data_dir)

    @router.get("/history")
    async def history(request: Request, chat_id: str | None = None, limit: int = 50):
        _require_admin(request)
        return {"messages": store.history(chat_id=chat_id, limit=limit)}

    @router.post("/poll")
    async def poll(request: Request):
        _require_admin(request)
        result = await asyncio.to_thread(
            run_telegram_polling_cycle,
            data_dir=ctx.data_dir,
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
        if not result["ok"]:
            raise HTTPException(403, result["status"])
        return result

    @router.post("/webhook")
    async def webhook(request: Request):
        _require_admin(request)
        update = await request.json()
        try:
            message = parse_telegram_update(update, chat_allowed=_chat_allowed)
        except ValueError as exc:
            store.append_event(kind="invalid_update", status="invalid_update", error=str(exc)[:120])
            raise HTTPException(400, "invalid telegram update") from exc
        stored = store.append_inbound(message)
        message_voice_stt_provider = voice_stt_provider or build_telegram_live_voice_stt_provider(
            message,
            voice_bytes_provider=voice_bytes_provider,
        )
        voice_agent_turn, voice_pipeline = run_telegram_voice_pipeline(
            stored["message"],
            stt_provider=message_voice_stt_provider,
        )
        image_action = run_telegram_image_action(
            stored["message"],
            enabled=_bool_env("TELEGRAM_IMAGE_ACTIONS_ENABLED"),
            image_bytes_provider=image_bytes_provider,
            worker_client=image_worker_client,
        )
        inbox_attachment = run_telegram_universal_inbox_attachment_pipeline(
            message,
            data_dir=ctx.data_dir,
            file_bytes_provider=attachment_bytes_provider,
        )
        if inbox_attachment is not None:
            spool_key = _telegram_attachment_spool_key(stored["message"])
            refreshed = store.update_inbound_status(
                stored["message"],
                universal_inbox_status=str(inbox_attachment.get("status") or "failed"),
                intake_status="universal_inbox_processed"
                if inbox_attachment.get("status") == "processed"
                else str(inbox_attachment.get("status") or "failed"),
            )
            if refreshed is not None:
                stored["message"] = refreshed
            store.append_event(
                kind="universal_inbox_attachment",
                status=str(inbox_attachment.get("status") or "failed"),
                chat_id=str(message.get("chat_id") or ""),
                update_id=message.get("update_id"),
                message_id=message.get("message_id"),
                universal_inbox_status=str(inbox_attachment.get("universal_inbox_status") or ""),
                memory_write_intent_status=str(inbox_attachment.get("memory_write_intent_status") or ""),
                attachment_family=_telegram_attachment_family(stored["message"]),
                attachment_suffix=_telegram_attachment_suffix(stored["message"]),
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
            memory_auto_write = _execute_telegram_memory_auto_write_if_ready(
                data_dir=ctx.data_dir,
                store=store,
                chat_id=str(message.get("chat_id") or ""),
                inbox_attachment=inbox_attachment,
                source_message_id=message.get("message_id"),
                memory_manager=_ctx_attr("memory_manager"),
                memory_vector=_ctx_attr("memory_vector"),
                memory_owner=_ctx_attr("memory_owner"),
            )
            if memory_auto_write is not None:
                inbox_attachment = dict(inbox_attachment)
                inbox_attachment["memory_auto_write_status"] = str(memory_auto_write.get("status") or "")
                inbox_attachment["memory_auto_write_reason"] = str(memory_auto_write.get("reason") or "")
                inbox_attachment["memory_auto_writes_performed"] = bool(memory_auto_write.get("writes_performed"))
            _reply_with_gate(
                str(message.get("chat_id") or ""),
                format_telegram_attachment_inbox_reply(inbox_attachment),
                source_message_id=message.get("message_id"),
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
        session_binding = None
        agent_turn = None
        reply_result = None
        control_result = _handle_telegram_control_command(
            _telegram_control_command(stored["message"]),
            message=stored["message"],
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
        )
        if control_result is not None:
            store.append_event(
                kind="control_command",
                status=str(control_result.get("status") or "handled"),
                chat_id=str(message.get("chat_id") or ""),
                session_id=str((control_result.get("binding") or {}).get("session_id") or ""),
                command=str(control_result.get("command") or ""),
            )
            return {
                "stored": stored["stored"],
                "message": stored["message"],
                "agent_bridge": bridge,
                "voice_pipeline": voice_pipeline,
                "image_action": image_action,
                "universal_inbox_attachment": inbox_attachment,
                "agent_turn": None,
                "reply": _public_reply_result(control_result.get("reply")),
                "control_command": {
                    "command": control_result.get("command"),
                    "status": control_result.get("status"),
                    "pin_status": control_result.get("pin_status"),
                    "session_id_present": bool((control_result.get("binding") or {}).get("session_id")),
                },
                "token_value_visible": False,
            }
        export_plan = None
        if stored["message"].get("kind") == "text":
            export_plan = execute_recent_telegram_attachment_export(
                data_dir=ctx.data_dir,
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
            if str(export_plan.get("status") or "") == "exported":
                reply_result = _document_reply_with_gate(
                    str(message.get("chat_id") or ""),
                    str(export_plan.get("output_path") or ""),
                    str(export_plan.get("output_filename") or "telegram-export.pdf"),
                    format_telegram_attachment_export_reply({**export_plan, "status": "sent"}),
                    source_message_id=message.get("message_id"),
                )
                if reply_result.get("exit_code") == 0:
                    export_plan = {**export_plan, "status": "sent"}
                    store.append_event(
                        kind="universal_inbox_export_delivery",
                        status="sent",
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
                else:
                    export_plan = {**export_plan, "reason": f"document_delivery_failed:{str(reply_result.get('error') or '')[:80]}"}
            else:
                reply_result = _reply_with_gate(
                    str(message.get("chat_id") or ""),
                    format_telegram_attachment_export_reply(export_plan),
                    source_message_id=message.get("message_id"),
                )
            return {
                "stored": stored["stored"],
                "message": stored["message"],
                "agent_bridge": bridge,
                "voice_pipeline": voice_pipeline,
                "image_action": image_action,
                "universal_inbox_attachment": inbox_attachment,
                "universal_inbox_export_plan": {
                    "status": export_plan.get("status"),
                    "target_format": export_plan.get("target_format"),
                    "action": export_plan.get("action"),
                    "raw_content_visible": False,
                },
                "agent_turn": None,
                "reply": _public_reply_result(reply_result),
                "token_value_visible": False,
            }
        project_intake = None
        if stored["message"].get("kind") == "text":
            project_intake = build_telegram_project_intake_preview(
                data_dir=ctx.data_dir,
                store=store,
                sessions=sessions,
                chat_id=str(message.get("chat_id") or ""),
                text=str(stored["message"].get("text") or ""),
                source_message_id=message.get("message_id"),
                project_registry_path=Path(ctx.data_dir) / _PROJECT_REGISTRY_FILE,
            )
        if project_intake is not None:
            reply_result = _reply_with_gate(
                str(message.get("chat_id") or ""),
                format_telegram_project_intake_reply(project_intake),
                source_message_id=message.get("message_id"),
            )
            return {
                "stored": stored["stored"],
                "message": stored["message"],
                "agent_bridge": bridge,
                "voice_pipeline": voice_pipeline,
                "image_action": image_action,
                "universal_inbox_attachment": inbox_attachment,
                "project_intake": {
                    "status": project_intake.get("status"),
                    "project_slug": project_intake.get("project_slug"),
                    "task_count": project_intake.get("task_count"),
                    "raw_content_visible": False,
                },
                "agent_turn": None,
                "reply": _public_reply_result(reply_result),
                "token_value_visible": False,
            }
        if bridge["ready_for_agent"]:
            session_binding = sessions.bind_chat(
                chat_id=bridge["chat_id"],
                session_alias=bridge["session_alias"],
                recommended_session_name=bridge["recommended_session_name"],
                scope=str(bridge.get("desired_session_scope") or "normal"),
                creator=session_creator,
            )
        bridge = build_agent_bridge_request(
            stored["message"],
            session_binding=session_binding,
            raw_chat_id=str(message.get("chat_id") or ""),
            voice_agent_turn=voice_agent_turn,
            recent_attachment_context=recent_attachment_context,
        )
        if bridge["ready_for_agent"]:
            send_telegram_typing_indicator(bridge["chat_id"], store=store)
        agent_turn = await _run_agent_turn_async(agent_turn_handler, bridge)
        if agent_turn is not None:
            store.append_event(
                kind="agent_turn",
                status=str(agent_turn.get("status") or "accepted"),
                chat_id=bridge["chat_id"],
                session_id=bridge.get("session_id") or "",
                reply_text_present=bool(agent_turn.get("reply_text_present")),
            )
            reply_text = str(agent_turn.get("reply_text") or _agent_failure_reply(agent_turn))
            if reply_text:
                reply_result = _reply_with_gate(
                    bridge["chat_id"],
                    reply_text,
                    source_message_id=bridge.get("source_message_id"),
                )
        return {
            "stored": stored["stored"],
            "message": stored["message"],
            "agent_bridge": bridge,
            "voice_pipeline": voice_pipeline,
            "image_action": image_action,
            "universal_inbox_attachment": inbox_attachment,
            "agent_turn": _public_agent_turn_result(agent_turn),
            "reply": _public_reply_result(reply_result),
            "token_value_visible": False,
        }

    @router.post("/reply")
    async def reply(request: Request):
        _require_admin(request)
        body = await request.json()
        chat_id = str(body.get("chat_id") or "")
        text = str(body.get("text") or "")
        result = _reply_with_gate(
            chat_id,
            text,
            source_message_id=body.get("source_message_id"),
            classification=body.get("classification"),
            security_mode=body.get("security_mode") or "",
            secure_transport=bool(body.get("secure_transport")),
            can_start_secure_flow=bool(body.get("can_start_secure_flow")),
        )
        if result.get("exit_code") != 0:
            raise HTTPException(403, str(result.get("error") or "Telegram reply refused"))
        return json.loads(str(result["output"]))

    @router.get("/app")
    async def app_page(request: Request):
        _require_admin(request)
        return HTMLResponse(_app_html(getattr(request.state, "csp_nonce", "")))

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

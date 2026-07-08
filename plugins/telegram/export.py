"""Telegram attachment export planning and execution helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from src.privacy_runtime import is_dsgvo_mode_enabled

from plugins.telegram.attachments import _telegram_attachment_context_ttl_seconds
from plugins.telegram.formatting import (
    format_telegram_attachment_export_reply as _format_telegram_attachment_export_reply,
)
from plugins.telegram.stores import TelegramInboxStore, _chat_handle


def build_recent_telegram_attachment_export_plan(
    *,
    data_dir: str | Path,
    store: TelegramInboxStore,
    chat_id: str,
    text: str,
) -> dict[str, Any] | None:
    try:
        from src.universal_export import build_universal_export_plan_from_intent, parse_universal_export_intent
    except Exception as exc:
        return {"status": "blocked", "reason": f"export_planner_unavailable:{str(exc)[:80]}", "raw_content_visible": False}

    event = store.latest_universal_inbox_attachment(
        chat_id=chat_id,
        max_age_seconds=_telegram_attachment_context_ttl_seconds(),
    )
    intent = parse_universal_export_intent(text, recent_input_available=event is not None)
    if intent.status == "not_export_intent":
        return None
    if event is None:
        return {"status": "blocked", "reason": "recent_attachment_missing", "intent": intent.to_dict(), "raw_content_visible": False}

    spool_key = str(event.get("spool_key") or "").strip()
    if not spool_key:
        chat_handle = str(event.get("chat_handle") or _chat_handle(chat_id) or "chat")
        message_id = str(event.get("message_id") or "")
        if message_id:
            spool_key = hashlib.sha256(f"{chat_handle}:{message_id}".encode("utf-8")).hexdigest()[:16]
    if not spool_key:
        return {"status": "blocked", "reason": "spool_key_missing", "intent": intent.to_dict(), "raw_content_visible": False}

    spool_dir = Path(data_dir) / "universal_inbox_telegram" / spool_key
    try:
        files = [path for path in spool_dir.iterdir() if path.is_file() and not path.is_symlink()]
    except OSError:
        files = []
    if not files:
        return {"status": "blocked", "reason": "spool_file_missing", "intent": intent.to_dict(), "raw_content_visible": False}

    plan = build_universal_export_plan_from_intent(files[0], intent).to_dict()
    return {
        "status": str(plan.get("status") or "blocked"),
        "reason": str(plan.get("reason") or ""),
        "target_format": str(plan.get("target_format") or ""),
        "action": str(plan.get("action") or ""),
        "required_tool": str(plan.get("required_tool") or ""),
        "local_only": bool(plan.get("local_only")),
        "review_required": bool(plan.get("review_required", True)),
        "intent": intent.to_dict(),
        "plan": plan,
        "raw_content_visible": False,
        "host_paths_visible": False,
        "filename_visible": False,
    }


def execute_recent_telegram_attachment_export(
    *,
    data_dir: str | Path,
    store: TelegramInboxStore,
    chat_id: str,
    text: str,
) -> dict[str, Any] | None:
    plan = build_recent_telegram_attachment_export_plan(
        data_dir=data_dir,
        store=store,
        chat_id=chat_id,
        text=text,
    )
    if plan is None:
        return None
    if str(plan.get("status") or "") != "ready":
        return plan
    try:
        from src.universal_export_executor import execute_universal_export
    except Exception as exc:
        result = dict(plan)
        result.update(
            {
                "status": "blocked",
                "reason": f"export_executor_unavailable:{str(exc)[:80]}",
                "execution": {"status": "blocked", "reason": "export_executor_unavailable", "raw_content_visible": False},
            }
        )
        return result

    event = store.latest_universal_inbox_attachment(
        chat_id=chat_id,
        max_age_seconds=_telegram_attachment_context_ttl_seconds(),
    )
    spool_key = str((event or {}).get("spool_key") or "").strip()
    if not spool_key:
        chat_handle = str((event or {}).get("chat_handle") or _chat_handle(chat_id) or "chat")
        message_id = str((event or {}).get("message_id") or "")
        if message_id:
            spool_key = hashlib.sha256(f"{chat_handle}:{message_id}".encode("utf-8")).hexdigest()[:16]
    if not spool_key:
        blocked = dict(plan)
        blocked.update({"status": "blocked", "reason": "spool_key_missing"})
        return blocked

    spool_dir = Path(data_dir) / "universal_inbox_telegram" / spool_key
    try:
        files = [path for path in spool_dir.iterdir() if path.is_file() and not path.is_symlink()]
    except OSError:
        files = []
    if not files:
        blocked = dict(plan)
        blocked.update({"status": "blocked", "reason": "spool_file_missing"})
        return blocked

    output_dir = Path(data_dir) / "universal_inbox_exports" / spool_key
    execution = execute_universal_export(
        files[0],
        str(plan.get("target_format") or ""),
        output_dir,
        dsgvo_mode=is_dsgvo_mode_enabled(),
        output_basename="telegram-export",
    )
    result = dict(plan)
    result["execution"] = execution.to_dict()
    result["delivery_ready"] = execution.delivery_ready
    if execution.ok:
        result.update(
            {
                "status": "exported",
                "reason": execution.reason,
                "output_path": execution.output_path,
                "output_filename": execution.output_filename,
                "mime_type": execution.mime_type,
                "bytes_written": execution.bytes_written,
                "filename_visible": False,
                "host_paths_visible": False,
                "raw_content_visible": False,
            }
        )
    else:
        result.update({"status": "blocked", "reason": execution.reason})
    return result


def format_telegram_attachment_export_reply(result: dict[str, Any]) -> str:
    return _format_telegram_attachment_export_reply(result)

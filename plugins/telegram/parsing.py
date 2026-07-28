"""Pure Telegram update parsing and control-command detection.

This module contains deterministic parsing helpers only. It must not call the
Telegram API, persist identifiers, mutate settings, or dispatch replies.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from src.memory_triage_contract import normalize_memory_write_intent_status
from src.telegram_image_actions import select_telegram_photo_variant
from plugins.telegram.security_incident_commands import command_key, parse_security_incident_command


def parse_telegram_update(
    update: dict[str, Any],
    *,
    chat_allowed: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Extract a local-history message from a Telegram update."""

    if not isinstance(update, dict):
        raise ValueError("telegram update must be an object")
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        raise ValueError("telegram update has no message")
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        raise ValueError("telegram message has no chat id")

    is_allowed = bool(chat_allowed(chat_id)) if chat_allowed is not None else False
    sender = message.get("from") or {}
    base = {
        "plugin": "telegram",
        "direction": "inbound",
        "update_id": update.get("update_id"),
        "message_id": message.get("message_id"),
        "chat_id": chat_id,
        "chat_allowed": is_allowed,
        "date": message.get("date"),
        "sender": {
            "id": str(sender.get("id") or ""),
            "is_bot": bool(sender.get("is_bot", False)),
            "first_name": sender.get("first_name") or "",
            "username": sender.get("username") or "",
        },
        "token_value_visible": False,
    }

    if isinstance(message.get("text"), str):
        text = message["text"]
        first_token = text.strip().split(maxsplit=1)[0].split("@", 1)[0].lower() if text.strip() else ""
        incident_attempt = first_token == "/incident"
        incident_command = parse_security_incident_command(text) if incident_attempt else None
        base.update(
            {
                "kind": "text",
                # A recognized incident command remains available only for the
                # current control flow.  Its original message is never placed
                # in the persistent Telegram history record.
                "text": "[security incident command]" if incident_attempt else text,
                "intake_status": "ready" if is_allowed else "blocked_chat",
            }
        )
        if incident_command:
            base["security_incident_command"] = incident_command
    elif isinstance(message.get("voice"), dict):
        voice = message["voice"]
        transcript_status = "pending_stt"
        base.update(
            {
                "kind": "voice",
                "text": "",
                "transcript_status": transcript_status,
                "voice_status": transcript_status,
                "intake_status": transcript_status if is_allowed else "blocked_chat",
                "media": {
                    "type": "voice",
                    "file_id": voice.get("file_id") or "",
                    "file_unique_id": voice.get("file_unique_id") or "",
                    "duration": voice.get("duration"),
                    "mime_type": voice.get("mime_type") or "",
                    "file_size": voice.get("file_size"),
                },
            }
        )
    elif isinstance(message.get("photo"), list):
        photo = select_telegram_photo_variant(message.get("photo"))
        base.update(
            {
                "kind": "image",
                "text": "",
                "image_action_status": "pending_image_action",
                "universal_inbox_status": "pending_universal_inbox",
                "intake_status": "pending_image_action" if is_allowed else "blocked_chat",
                "media": {
                    "type": "image",
                    "file_id": photo.get("file_id") or "",
                    "file_unique_id": photo.get("file_unique_id") or "",
                    "width": photo.get("width"),
                    "height": photo.get("height"),
                    "file_size": photo.get("file_size"),
                    "mime_type": "image/jpeg",
                },
            }
        )
    elif isinstance(message.get("document"), dict):
        document = message["document"]
        base.update(
            {
                "kind": "document",
                "text": str(message.get("caption") or ""),
                "universal_inbox_status": "pending_universal_inbox",
                "intake_status": "pending_universal_inbox" if is_allowed else "blocked_chat",
                "media": {
                    "type": "document",
                    "file_id": document.get("file_id") or "",
                    "file_unique_id": document.get("file_unique_id") or "",
                    "file_name": document.get("file_name") or "",
                    "mime_type": document.get("mime_type") or "",
                    "file_size": document.get("file_size"),
                },
            }
        )
    else:
        base.update(
            {
                "kind": "unsupported",
                "text": "",
                "intake_status": "unsupported",
                "unsupported_keys": sorted(message.keys()),
            }
        )
    return base


def _safe_workflow_token(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return default
    if re.fullmatch(r"^[a-z][a-z0-9_.:-]{0,79}$", text):
        return text
    return default


def _safe_workflow_suffix(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not text.startswith("."):
        text = f".{text}"
    return text if re.fullmatch(r"\.[a-z0-9]{1,16}", text) else ""


def _telegram_workflow_intent_from_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return ""
    web_terms = ("http://", "https://", "homepage", "website", "webseite", "seite", "hilfeseite")
    memory_terms = ("gedaechtnis", "gedächtnis", "memory", "raptor", "langzeit")
    research_terms = ("analys", "recherch", "untersuch", "crawl", "zusammenfass")
    coding_terms = ("baue", "implement", "code", "feature", "fix", "bug", "teste", "pytest", "repo", "projekt", "project")
    coding_actions = ("mach", "baue", "implement", "fix", "teste", "pruef", "prüf", "ändere", "aendere")
    if any(term in normalized for term in coding_terms) and any(term in normalized for term in coding_actions):
        return "coding_agent_task"
    if (
        any(term in normalized for term in web_terms)
        and any(term in normalized for term in memory_terms)
        and any(term in normalized for term in research_terms)
    ):
        return "bounded_site_research_to_memory"
    export_terms = (
        "export",
        "convert",
        "konvert",
        "umwand",
        "pdf",
        "docx",
        "xlsx",
        "pptx",
        "png",
        "jpg",
        "jpeg",
        "wav",
        "mp3",
    )
    if any(term in normalized for term in export_terms):
        return "export"
    if any(term in normalized for term in ("review", "pruef", "prüf", "freig", "route", "einsort", "warum")):
        return "review"
    if any(term in normalized for term in ("zusammenfass", "summary", "summar")):
        return "summarize"
    if any(term in normalized for term in ("frage", "was", "worum", "wie", "warum", "?")):
        return "question_answer"
    if any(term in normalized for term in ("analys", "inspect", "untersuch", "auswert")):
        return "analyze"
    return "follow_up"


def build_telegram_workflow_context(
    message: dict[str, Any],
    *,
    recent_attachment_context: dict[str, Any] | None = None,
    dsgvo_mode: bool = False,
) -> dict[str, Any]:
    """Return trusted Telegram workflow routing metadata without raw content."""

    recent = recent_attachment_context if isinstance(recent_attachment_context, dict) else {}
    present = bool(recent.get("context") or recent.get("present"))
    return {
        "channel": "telegram",
        "message_kind": _safe_workflow_token(message.get("kind") or "", default="unknown"),
        "intent": _safe_workflow_token(_telegram_workflow_intent_from_text(str(message.get("text") or ""))),
        "dsgvo_mode": "on" if dsgvo_mode else "off",
        "security_mode": "secure" if dsgvo_mode else "normal",
        "recent_attachment": {
            "present": present,
            "family": _safe_workflow_token(recent.get("family") or "", default="unknown") if present else "",
            "suffix": _safe_workflow_suffix(recent.get("suffix") or ""),
            "universal_inbox_status": _safe_workflow_token(
                recent.get("universal_inbox_status") or recent.get("status") or "",
                default="",
            ),
            "memory_write_intent_status": _safe_workflow_token(
                normalize_memory_write_intent_status(recent.get("memory_write_intent_status") or ""),
                default="",
            ),
        },
    }


def _telegram_control_command(message: dict[str, Any]) -> str:
    if message.get("kind") != "text":
        return ""
    incident_key = command_key(message.get("security_incident_command"))
    if incident_key:
        return incident_key
    if message.get("text") == "[security incident command]":
        return "security_incident_control"
    text = str(message.get("text") or "").strip()
    parts = text.split(maxsplit=1)
    first = parts[0].lower() if parts else ""
    arg = parts[1].split(maxsplit=1)[0].strip().lower() if len(parts) > 1 and parts[1].strip() else ""
    command = first.split("@", 1)[0]
    if command == "/new":
        return "new_chat"
    if command in {"/inbox", "/universal_inbox", "/universalinbox"}:
        return "universal_inbox_status"
    if command in {"/review", "/inboxreview", "/inbox_review"}:
        args = parts[1].strip().lower().split() if len(parts) > 1 and parts[1].strip() else []
        if args and args[0] in {"memory", "gedaechtnis", "gedächtnis", "raptor"}:
            if len(args) > 1 and args[1] in {"ok", "yes", "ja", "confirm", "bestaetigen", "approve", "freigeben"}:
                return "universal_inbox_memory_review_confirm"
            return "universal_inbox_memory_review_status"
        if arg in {"ok", "yes", "ja", "confirm", "bestätigen", "bestaetigen", "approve", "freigeben"}:
            return "universal_inbox_review_confirm"
        return "universal_inbox_review_status"
    if command in {"/project", "/projekt"}:
        args = parts[1].strip().split() if len(parts) > 1 and parts[1].strip() else []
        first_arg = args[0].lower() if args else ""
        if first_arg in {"ok", "yes", "ja", "confirm", "bestaetigen", "bestätigen", "approve", "freigeben"}:
            return "project_intake_review_confirm"
        if first_arg in {"hold", "pause", "stop", "later", "spaeter", "später"}:
            return "project_intake_review_hold"
        if first_arg in {"status", "state", "info", ""}:
            return "project_intake_review_status"
        return ""
    if command in {"/task", "/tasks", "/lauf", "/job"}:
        args = parts[1].strip().split() if len(parts) > 1 and parts[1].strip() else []
        first_arg = args[0].lower() if args else ""
        if first_arg in {"pause", "pausieren", "hold", "stop"}:
            return "agent_task_pause"
        if first_arg in {"resume", "weiter", "fortsetzen", "continue"}:
            return "agent_task_resume"
        if first_arg in {"cancel", "abbrechen", "canceln"}:
            return "agent_task_cancel"
        if first_arg in {"status", "state", "info", ""}:
            return "agent_task_status"
        return "agent_task_help"
    if command in {"/calendar", "/kalender"}:
        if arg in {"agenda", "today", "heute", "morgen"}:
            return "calendar_agenda"
        if arg in {"reminder", "reminders", "erinnerung", "erinnerungen"}:
            return "calendar_reminders_status"
        return "calendar_readiness"
    if command in {"/agenda", "/termine"}:
        return "calendar_agenda"
    if command in {"/remind", "/reminder", "/reminders", "/erinnerung", "/erinnerungen"}:
        if arg in {"status", "state", "info", "list", "liste", ""}:
            return "calendar_reminders_status"
        if arg in {"update", "edit", "aendere"}:
            return "calendar_reminder_update"
        return "calendar_reminder_create"
    if command in {"/todo", "/todos", "/todo_digest", "/tododigest"}:
        if arg in {"status", "state", "info", "list", "liste", ""}:
            return "calendar_todo_status"
        return "calendar_todo_digest_create"
    if command in {"/dsgvo", "/gdpr", "/privacy", "/datenschutz"}:
        if arg in {"on", "an", "1", "true", "aktiv", "active", "enable", "enabled", "aktivieren"}:
            return "dsgvo_enable"
        if arg in {"off", "aus", "0", "false", "inaktiv", "inactive", "disable", "disabled", "deaktivieren"}:
            return "dsgvo_disable"
        if arg == "":
            return "dsgvo_toggle"
        if arg in {"status", "state", "info", "show"}:
            return "dsgvo_status"
        return "dsgvo_help"
    return ""

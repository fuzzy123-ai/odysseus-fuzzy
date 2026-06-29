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
import mimetypes
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from src.chat_security_state import ChatSecurityState
from src.privacy_runtime import is_dsgvo_mode_enabled, runtime_requires_local_only
from src.secure_channel_policy import ChannelContext, decide_channel_access
from src.telegram_formatting import chunk_telegram_html, render_telegram_markdown
from src.telegram_image_actions import run_telegram_image_action, select_telegram_photo_variant
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


_CHEVRON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>'
)
_HISTORY_FILE = "telegram_history.json"
_POLLING_FILE = "telegram_polling_state.json"
_SESSION_FILE = "telegram_session_bridge.json"
_PINNED_PRIVACY_FILE = "telegram_privacy_pin_state.json"
_THINKING_BLOCK_RE = re.compile(r"<tg-thinking>.*?</tg-thinking>", re.IGNORECASE | re.DOTALL)


def _bool_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


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


def _stable_handle(prefix: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    digest = hashlib.sha256(f"{prefix}:{raw}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _chat_handle(chat_id: Any) -> str:
    return _stable_handle("chat", chat_id)


def _sender_handle(sender_id: Any) -> str:
    return _stable_handle("sender", sender_id)


def _voice_file_handle(file_id: Any) -> str:
    return _stable_handle("voice_file", file_id)


def _voice_unique_handle(file_unique_id: Any) -> str:
    return _stable_handle("voice_unique", file_unique_id)


def _image_file_handle(file_id: Any) -> str:
    return _stable_handle("image_file", file_id)


def _image_unique_handle(file_unique_id: Any) -> str:
    return _stable_handle("image_unique", file_unique_id)


def _document_file_handle(file_id: Any) -> str:
    return _stable_handle("document_file", file_id)


def _document_unique_handle(file_unique_id: Any) -> str:
    return _stable_handle("document_unique", file_unique_id)


def build_telegram_draft_id(*, chat_id: str, source_message_id: Any = "") -> int:
    digest = hashlib.sha256(f"telegram-draft:{chat_id}:{source_message_id}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) or 1


def _draft_interval_ms() -> int:
    raw = os.getenv("TELEGRAM_DRAFT_INTERVAL_MS") or "750"
    try:
        value = int(raw)
    except ValueError:
        value = 750
    return max(250, min(value, 10000))


def _privacy_pin_enabled() -> bool:
    return not _bool_env("TELEGRAM_PRIVACY_PIN_DISABLED")


def _strip_draft_thinking(markdown: str) -> str:
    return _THINKING_BLOCK_RE.sub("", str(markdown or "")).strip()


def _sanitize_persisted_message(message: dict[str, Any]) -> dict[str, Any]:
    stored = dict(message)
    raw_chat_id = str(stored.pop("chat_id", "") or "")
    if raw_chat_id:
        stored["chat_handle"] = _chat_handle(raw_chat_id)
    sender = stored.get("sender")
    if isinstance(sender, dict):
        sanitized_sender = dict(sender)
        raw_sender_id = sanitized_sender.pop("id", "")
        if raw_sender_id:
            sanitized_sender["handle"] = _sender_handle(raw_sender_id)
        stored["sender"] = sanitized_sender
    media = stored.get("media")
    if isinstance(media, dict):
        sanitized_media = dict(media)
        raw_file_id = sanitized_media.pop("file_id", "")
        raw_unique_id = sanitized_media.pop("file_unique_id", "")
        raw_file_name = sanitized_media.pop("file_name", "")
        if raw_file_name:
            suffix = Path(str(raw_file_name)).suffix.lower()
            if suffix:
                sanitized_media["file_suffix"] = suffix[:16]
        media_type = str(sanitized_media.get("type") or "")
        if raw_file_id:
            if media_type == "image":
                sanitized_media["file_handle"] = _image_file_handle(raw_file_id)
            elif media_type == "document":
                sanitized_media["file_handle"] = _document_file_handle(raw_file_id)
            else:
                sanitized_media["file_handle"] = _voice_file_handle(raw_file_id)
        if raw_unique_id:
            if media_type == "image":
                sanitized_media["file_unique_handle"] = _image_unique_handle(raw_unique_id)
            elif media_type == "document":
                sanitized_media["file_unique_handle"] = _document_unique_handle(raw_unique_id)
            else:
                sanitized_media["file_unique_handle"] = _voice_unique_handle(raw_unique_id)
        stored["media"] = sanitized_media
    stored["chat_id_value_visible"] = False
    stored["sender_id_value_visible"] = False
    stored["voice_file_id_value_visible"] = False
    stored["voice_file_unique_id_value_visible"] = False
    stored["image_file_id_value_visible"] = False
    stored["image_file_unique_id_value_visible"] = False
    stored["document_file_id_value_visible"] = False
    stored["document_file_unique_id_value_visible"] = False
    return stored


class TelegramInboxStore:
    """Small JSON store under the plugin data dir."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / _HISTORY_FILE

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"messages": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"messages": []}
        if not isinstance(data, dict):
            return {"messages": []}
        messages = data.get("messages")
        if not isinstance(messages, list):
            data["messages"] = []
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def append_event(self, *, kind: str, status: str, chat_id: str = "", **extra: Any) -> dict[str, Any]:
        data = self._read()
        event = {
            "direction": "system",
            "kind": kind,
            "status": status,
            "chat_handle": _chat_handle(chat_id),
            "stored_at": int(time.time()),
            "token_value_visible": False,
            "chat_id_value_visible": False,
        }
        event.update({key: value for key, value in extra.items() if value is not None})
        data["messages"].append(event)
        self._write(data)
        return event

    def append_inbound(self, message: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        messages = data["messages"]
        raw_chat_id = str(message.get("chat_id") or "")
        key = (message.get("direction"), message.get("update_id"), message.get("message_id"))
        for existing in messages:
            existing_key = (
                existing.get("direction"),
                existing.get("update_id"),
                existing.get("message_id"),
            )
            if existing_key == key:
                retry_pending_voice = (
                    existing.get("kind") == "voice"
                    and existing.get("transcript_status") == "pending_stt"
                    and message.get("kind") == "voice"
                )
                self.append_event(
                    kind="duplicate",
                    status="duplicate_pending_stt_retry" if retry_pending_voice else "duplicate_ignored",
                    chat_id=str(message.get("chat_id") or ""),
                    update_id=message.get("update_id"),
                    message_id=message.get("message_id"),
                )
                return {"stored": False, "message": existing, "retry_pending_voice": retry_pending_voice}

        stored = _sanitize_persisted_message(message)
        stored["stored_at"] = int(time.time())
        messages.append(stored)
        self._write(data)
        if stored.get("intake_status") == "blocked_chat":
            self.append_event(
                kind="blocked",
                status="chat_not_allowed",
                chat_id=raw_chat_id,
                update_id=stored.get("update_id"),
                message_id=stored.get("message_id"),
            )
        elif stored.get("kind") == "unsupported":
            self.append_event(
                kind="unsupported",
                status="unsupported_message",
                chat_id=raw_chat_id,
                update_id=stored.get("update_id"),
                message_id=stored.get("message_id"),
            )
        return {"stored": True, "message": stored, "retry_pending_voice": False}

    def update_inbound_status(
        self,
        message: dict[str, Any],
        *,
        transcript_status: str | None = None,
        voice_status: str | None = None,
        intake_status: str | None = None,
        universal_inbox_status: str | None = None,
    ) -> dict[str, Any] | None:
        data = self._read()
        key = (message.get("direction"), message.get("update_id"), message.get("message_id"))
        for existing in data["messages"]:
            existing_key = (
                existing.get("direction"),
                existing.get("update_id"),
                existing.get("message_id"),
            )
            if existing_key != key:
                continue
            if transcript_status is not None:
                existing["transcript_status"] = transcript_status
            if voice_status is not None:
                existing["voice_status"] = voice_status
            if intake_status is not None:
                existing["intake_status"] = intake_status
            if universal_inbox_status is not None:
                existing["universal_inbox_status"] = universal_inbox_status
            existing["updated_at"] = int(time.time())
            self._write(data)
            return dict(existing)
        return None

    def append_outbound(
        self,
        chat_id: str,
        text: str,
        *,
        source_message_id: int | None = None,
        delivery_status: str = "sent",
        failure_reason: str | None = None,
        delivery_mode: str = "",
        formatting_mode: str = "",
    ) -> dict[str, Any]:
        data = self._read()
        message = {
            "direction": "outbound",
            "kind": "text",
            "chat_handle": _chat_handle(chat_id),
            "message_id": f"local-{int(time.time() * 1000)}",
            "source_message_id": source_message_id,
            "text": text,
            "delivery_status": delivery_status,
            "failure_reason": failure_reason or "",
            "delivery_mode": delivery_mode,
            "formatting_mode": formatting_mode,
            "stored_at": int(time.time()),
            "token_value_visible": False,
            "chat_id_value_visible": False,
            "raw_rich_payload_visible": False,
        }
        data["messages"].append(message)
        self._write(data)
        return message

    def history(self, *, chat_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 200))
        messages = self._read()["messages"]
        if chat_id:
            chat_handle = _chat_handle(chat_id)
            messages = [
                m for m in messages
                if str(m.get("chat_handle") or "") == chat_handle or str(m.get("chat_id") or "") == str(chat_id)
            ]
        return list(reversed(messages[-limit:]))

    def counts(self) -> dict[str, int]:
        messages = self._read()["messages"]
        return {
            "total": len(messages),
            "inbound": sum(1 for m in messages if m.get("direction") == "inbound"),
            "outbound": sum(1 for m in messages if m.get("direction") == "outbound"),
            "voice": sum(1 for m in messages if m.get("kind") == "voice"),
            "image": sum(1 for m in messages if m.get("kind") == "image"),
            "document": sum(1 for m in messages if m.get("kind") == "document"),
            "blocked": sum(1 for m in messages if m.get("kind") == "blocked"),
            "duplicates": sum(1 for m in messages if m.get("kind") == "duplicate"),
            "pending_stt": sum(1 for m in messages if m.get("transcript_status") == "pending_stt"),
            "pending_image_action": sum(1 for m in messages if m.get("image_action_status") == "pending_image_action"),
            "pending_universal_inbox": sum(1 for m in messages if m.get("universal_inbox_status") == "pending_universal_inbox"),
        }

    def last_delivery_summary(self) -> dict[str, Any]:
        for message in reversed(self._read()["messages"]):
            if message.get("direction") == "outbound":
                return {
                    "last_delivery_mode": str(message.get("delivery_mode") or "classic"),
                    "last_delivery_status": str(message.get("delivery_status") or ""),
                    "formatting_mode": str(message.get("formatting_mode") or "plaintext"),
                    "raw_rich_payload_visible": False,
                }
        return {
            "last_delivery_mode": "",
            "last_delivery_status": "",
            "formatting_mode": "html",
            "raw_rich_payload_visible": False,
        }

    def latest_universal_inbox_review(self, *, chat_id: str | None = None) -> dict[str, Any] | None:
        chat_handle = _chat_handle(chat_id) if chat_id else ""
        for message in reversed(self._read()["messages"]):
            if message.get("kind") != "universal_inbox_attachment":
                continue
            if chat_handle and message.get("chat_handle") != chat_handle:
                continue
            status = str(message.get("status") or "")
            inbox_status = str(message.get("universal_inbox_status") or "")
            if status in {"processed", "blocked", "failed"} and inbox_status != "go":
                return dict(message)
        return None


class TelegramPollingStateStore:
    """Small JSON store for polling offsets and dry-run state."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / _POLLING_FILE

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"offset": 0, "last_status": "idle", "history": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"offset": 0, "last_status": "idle", "history": []}
        if not isinstance(data, dict):
            return {"offset": 0, "last_status": "idle", "history": []}
        if not isinstance(data.get("history"), list):
            data["history"] = []
        data["offset"] = int(data.get("offset") or 0)
        data["last_status"] = str(data.get("last_status") or "idle")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_offset(self) -> int:
        return self._read()["offset"]

    def record(self, *, offset: int | None = None, status: str, **extra: Any) -> dict[str, Any]:
        data = self._read()
        if offset is not None:
            data["offset"] = int(offset)
        data["last_status"] = status
        event = {"status": status, "recorded_at": int(time.time())}
        event.update({key: value for key, value in extra.items() if value is not None})
        data["history"].append(event)
        self._write(data)
        return event


class TelegramPrivacyPinStore:
    """Persist DSGVO pinned-message state without raw Telegram chat ids."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / _PINNED_PRIVACY_FILE

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"pins": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"pins": {}}
        if not isinstance(data, dict):
            return {"pins": {}}
        pins = data.get("pins")
        if not isinstance(pins, dict):
            data["pins"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_pin(self, chat_id: str) -> dict[str, Any] | None:
        handle = _chat_handle(chat_id)
        if not handle:
            return None
        pin = self._read()["pins"].get(handle)
        return dict(pin) if isinstance(pin, dict) else None

    def set_pin(self, chat_id: str, message_id: int) -> dict[str, Any]:
        handle = _chat_handle(chat_id)
        if not handle:
            raise ValueError("telegram chat id is missing")
        if int(message_id or 0) <= 0:
            raise ValueError("telegram message id is missing")
        data = self._read()
        pin = {
            "chat_handle": handle,
            "message_id": int(message_id),
            "pinned_at": int(time.time()),
            "token_value_visible": False,
            "chat_id_value_visible": False,
            "raw_chat_id_value_visible": False,
        }
        data["pins"][handle] = pin
        self._write(data)
        return dict(pin)

    def clear_pin(self, chat_id: str) -> dict[str, Any] | None:
        handle = _chat_handle(chat_id)
        if not handle:
            return None
        data = self._read()
        pin = data["pins"].pop(handle, None)
        self._write(data)
        return dict(pin) if isinstance(pin, dict) else None

    def active_count(self) -> int:
        return len(self._read()["pins"])


class TelegramSessionBridgeStore:
    """Persist chat->session bindings without creating a second runtime."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / _SESSION_FILE

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"sessions": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"sessions": {}}
        if not isinstance(data, dict):
            return {"sessions": {}}
        sessions = data.get("sessions")
        if not isinstance(sessions, dict):
            data["sessions"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, chat_id: str) -> dict[str, Any] | None:
        data = self._read()
        sessions = data["sessions"]
        mapping = sessions.get(_chat_handle(chat_id)) or sessions.get(str(chat_id))
        return dict(mapping) if isinstance(mapping, dict) else None

    def bind_chat(
        self,
        *,
        chat_id: str,
        session_alias: str,
        recommended_session_name: str,
        creator: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        data = self._read()
        sessions = data["sessions"]
        handle = _chat_handle(chat_id)
        safe_session_alias = f"telegram:{handle}" if handle else session_alias
        existing = sessions.get(handle) or sessions.get(str(chat_id))
        if isinstance(existing, dict) and existing.get("session_id"):
            if str(chat_id) in sessions and handle not in sessions:
                sessions[handle] = dict(existing)
                sessions.pop(str(chat_id), None)
            existing["last_seen_at"] = int(time.time())
            self._write(data)
            return {"session_id": existing["session_id"], "created": False, "mapping": dict(existing)}

        session_id = None
        if creator is not None:
            created = creator(
                chat_id=str(chat_id),
                session_alias=session_alias,
                recommended_session_name=recommended_session_name,
            )
            if isinstance(created, dict):
                session_id = created.get("session_id") or created.get("id")
            else:
                session_id = created
        mapping = {
            "chat_handle": handle,
            "session_id": str(session_id or ""),
            "session_alias": safe_session_alias,
            "recommended_session_name": recommended_session_name,
            "created_at": int(time.time()),
            "last_seen_at": int(time.time()),
        }
        sessions.pop(str(chat_id), None)
        sessions[handle] = mapping
        self._write(data)
        return {"session_id": mapping["session_id"], "created": bool(session_id), "mapping": dict(mapping)}

    def rebind_chat(
        self,
        *,
        chat_id: str,
        session_alias: str,
        recommended_session_name: str,
        creator: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        data = self._read()
        sessions = data["sessions"]
        handle = _chat_handle(chat_id)
        safe_session_alias = f"telegram:{handle}" if handle else session_alias
        session_id = None
        if creator is not None:
            created = creator(
                chat_id=str(chat_id),
                session_alias=session_alias,
                recommended_session_name=recommended_session_name,
            )
            if isinstance(created, dict):
                session_id = created.get("session_id") or created.get("id")
            else:
                session_id = created
        mapping = {
            "chat_handle": handle,
            "session_id": str(session_id or ""),
            "session_alias": safe_session_alias,
            "recommended_session_name": recommended_session_name,
            "created_at": int(time.time()),
            "last_seen_at": int(time.time()),
            "rebound_at": int(time.time()),
        }
        sessions.pop(str(chat_id), None)
        sessions[handle] = mapping
        self._write(data)
        return {"session_id": mapping["session_id"], "created": bool(session_id), "mapping": dict(mapping)}


def parse_telegram_update(update: dict[str, Any]) -> dict[str, Any]:
    """Extract a redacted local-history message from a Telegram update."""

    if not isinstance(update, dict):
        raise ValueError("telegram update must be an object")
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        raise ValueError("telegram update has no message")
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        raise ValueError("telegram message has no chat id")

    sender = message.get("from") or {}
    base = {
        "plugin": "telegram",
        "direction": "inbound",
        "update_id": update.get("update_id"),
        "message_id": message.get("message_id"),
        "chat_id": chat_id,
        "chat_allowed": _chat_allowed(chat_id),
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
        base.update({
            "kind": "text",
            "text": message["text"],
            "intake_status": "ready" if base["chat_allowed"] else "blocked_chat",
        })
    elif isinstance(message.get("voice"), dict):
        voice = message["voice"]
        transcript_status = "pending_stt"
        base.update({
            "kind": "voice",
            "text": "",
            "transcript_status": transcript_status,
            "voice_status": transcript_status,
            "intake_status": transcript_status if base["chat_allowed"] else "blocked_chat",
            "media": {
                "type": "voice",
                "file_id": voice.get("file_id") or "",
                "file_unique_id": voice.get("file_unique_id") or "",
                "duration": voice.get("duration"),
                "mime_type": voice.get("mime_type") or "",
                "file_size": voice.get("file_size"),
            },
        })
    elif isinstance(message.get("photo"), list):
        photo = select_telegram_photo_variant(message.get("photo"))
        base.update({
            "kind": "image",
            "text": "",
            "image_action_status": "pending_image_action",
            "universal_inbox_status": "pending_universal_inbox",
            "intake_status": "pending_image_action" if base["chat_allowed"] else "blocked_chat",
            "media": {
                "type": "image",
                "file_id": photo.get("file_id") or "",
                "file_unique_id": photo.get("file_unique_id") or "",
                "width": photo.get("width"),
                "height": photo.get("height"),
                "file_size": photo.get("file_size"),
                "mime_type": "image/jpeg",
            },
        })
    elif isinstance(message.get("document"), dict):
        document = message["document"]
        base.update({
            "kind": "document",
            "text": str(message.get("caption") or ""),
            "universal_inbox_status": "pending_universal_inbox",
            "intake_status": "pending_universal_inbox" if base["chat_allowed"] else "blocked_chat",
            "media": {
                "type": "document",
                "file_id": document.get("file_id") or "",
                "file_unique_id": document.get("file_unique_id") or "",
                "file_name": document.get("file_name") or "",
                "mime_type": document.get("mime_type") or "",
                "file_size": document.get("file_size"),
            },
        })
    else:
        base.update({
            "kind": "unsupported",
            "text": "",
            "intake_status": "unsupported",
            "unsupported_keys": sorted(message.keys()),
        })
    return base


def build_agent_bridge_request(
    message: dict[str, Any],
    *,
    session_binding: dict[str, Any] | None = None,
    raw_chat_id: str | None = None,
    voice_agent_turn: VoiceAgentTurn | None = None,
) -> dict[str, Any]:
    """Build the internal agent-turn envelope for a stored Telegram message."""

    chat_id = str(raw_chat_id or message.get("chat_id") or "")
    chat_handle = str(message.get("chat_handle") or _chat_handle(chat_id))
    sender = message.get("sender") or {}
    display_name = sender.get("username") or sender.get("first_name") or chat_handle
    kind = message.get("kind")
    if kind == "text":
        prompt = str(message.get("text") or "")
        ready_for_agent = bool(prompt.strip())
        note = "text_ready"
    elif kind == "voice":
        media = message.get("media") or {}
        if voice_agent_turn is not None and voice_agent_turn.ready_for_agent:
            prompt = voice_agent_turn.prompt
            ready_for_agent = True
            note = "voice_transcribed"
        else:
            prompt = (
                "[Telegram voice message received. "
                f"file_handle={media.get('file_handle', '')}; duration={media.get('duration', 'unknown')}; "
                "transcription pending.]"
            )
            ready_for_agent = False
            note = "voice_needs_transcription"
    elif kind == "image":
        media = message.get("media") or {}
        prompt = (
            "[Telegram image received. "
            f"file_handle={media.get('file_handle', '')}; size={media.get('file_size', 'unknown')}; "
            "image action pending.]"
        )
        ready_for_agent = False
        note = "image_action_pending"
    elif kind == "document":
        media = message.get("media") or {}
        prompt = (
            "[Telegram document received. "
            f"file_handle={media.get('file_handle', '')}; size={media.get('file_size', 'unknown')}; "
            "universal inbox processing pending.]"
        )
        ready_for_agent = False
        note = "universal_inbox_pending"
    else:
        prompt = "[Unsupported Telegram message received.]"
        ready_for_agent = False
        note = "unsupported_message"

    dsgvo_mode = _dsgvo_mode_active()
    return {
        "channel": "telegram",
        "session_alias": f"telegram:{chat_handle}",
        "recommended_session_name": f"Telegram {display_name}",
        "session_id": (session_binding or {}).get("session_id") or "",
        "chat_id": chat_id,
        "chat_handle": chat_handle,
        "source_message_id": message.get("message_id"),
        "kind": kind,
        "prompt": prompt,
        "ready_for_agent": ready_for_agent and _bridge_intake_ready(message, kind=kind, note=note),
        "reply_required": ready_for_agent and _bridge_intake_ready(message, kind=kind, note=note),
        "note": note,
        "intake_status": message.get("intake_status") or note,
        "dsgvo_mode": dsgvo_mode,
        "security_mode": "secure" if dsgvo_mode else "normal",
        "local_only_required": runtime_requires_local_only(settings={"dsgvo_mode": dsgvo_mode}),
    }


def _telegram_control_command(message: dict[str, Any]) -> str:
    if message.get("kind") != "text":
        return ""
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
        if arg in {"ok", "yes", "ja", "confirm", "bestätigen", "bestaetigen", "approve", "freigeben"}:
            return "universal_inbox_review_confirm"
        return "universal_inbox_review_status"
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
            reply_text = "Review bestätigt. Der Anhang bleibt in der Universal-Inbox-Review Queue vorgemerkt."
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
        }
    if command != "new_chat":
        return None
    bridge = build_agent_bridge_request(message, raw_chat_id=raw_chat_id)
    binding = sessions.rebind_chat(
        chat_id=bridge["chat_id"],
        session_alias=bridge["session_alias"],
        recommended_session_name=bridge["recommended_session_name"],
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


def _telegram_attachment_max_bytes() -> int:
    raw = os.getenv("TELEGRAM_ATTACHMENT_MAX_BYTES") or "25000000"
    try:
        value = int(raw)
    except ValueError:
        value = 25_000_000
    return max(1, min(value, 100_000_000))


def _format_universal_inbox_review_status(review: dict[str, Any]) -> str:
    status = str(review.get("universal_inbox_status") or review.get("status") or "unknown")
    processable = int(review.get("processable_count") or 0)
    if status == "go":
        return f"Universal Inbox: verarbeitet. Items: {processable}. Keine Review nötig."
    return (
        "Universal Inbox: Review nötig.\n"
        f"Status: {status}\n"
        f"Items: {processable}\n"
        "Zum Bestätigen antworte mit /review ok."
    )


def format_telegram_attachment_inbox_reply(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "failed")
    inbox_status = str(result.get("universal_inbox_status") or "")
    processable = int(result.get("processable_count") or 0)
    if status == "processed" and inbox_status == "go":
        return f"Anhang verarbeitet. Items: {processable}. Keine Review nötig."
    if status == "processed":
        return (
            "Anhang empfangen und geprüft. Review nötig.\n"
            f"Universal-Inbox-Status: {inbox_status or 'partial'}\n"
            f"Items: {processable}\n"
            "Zum Bestätigen antworte mit /review ok."
        )
    if status == "blocked":
        return f"Anhang empfangen, aber blockiert: {result.get('reason') or 'policy_gate'}."
    return f"Anhang empfangen, aber Verarbeitung fehlgeschlagen: {result.get('reason') or 'unknown'}."


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
        stt_enabled=_bool_env("TELEGRAM_VOICE_STT_ENABLED") if stt_enabled is None else stt_enabled,
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


def _message_file_id(message: dict[str, Any]) -> str:
    media = message.get("media") if isinstance(message.get("media"), dict) else {}
    return str(media.get("file_id") or "").strip()


def download_telegram_file_bytes(
    message: dict[str, Any],
    *,
    max_bytes: int | None = None,
    token: str | None = None,
    urlopen: Callable[..., Any] | None = None,
) -> bytes:
    """Download a Telegram file for local Universal Inbox processing."""

    file_id = _message_file_id(message)
    if not file_id:
        raise ValueError("telegram file id is missing")
    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise ValueError("telegram token is missing")
    limit = max(1, min(int(max_bytes or _telegram_attachment_max_bytes()), 100_000_000))
    open_url = urlopen or urllib.request.urlopen

    metadata_url = f"https://api.telegram.org/bot{token}/getFile?{urllib.parse.urlencode({'file_id': file_id})}"
    with open_url(metadata_url, timeout=20) as response:  # nosec: token-gated Telegram API endpoint
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise ValueError("telegram getFile failed")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    declared_size = result.get("file_size")
    if isinstance(declared_size, int) and declared_size > limit:
        raise ValueError("telegram file too large")
    file_path = str(result.get("file_path") or "").strip()
    if not file_path:
        raise ValueError("telegram getFile returned no file path")

    quoted_path = urllib.parse.quote(file_path, safe="/")
    download_url = f"https://api.telegram.org/file/bot{token}/{quoted_path}"
    with open_url(download_url, timeout=30) as response:  # nosec: file path returned by token-gated Telegram API
        header_size = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
        try:
            if header_size and int(header_size) > limit:
                raise ValueError("telegram file too large")
        except ValueError:
            raise ValueError("telegram file too large") from None
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("telegram file too large")
    if not data:
        raise ValueError("telegram file is empty")
    return data


def _telegram_attachment_suffix(message: dict[str, Any]) -> str:
    media = message.get("media") if isinstance(message.get("media"), dict) else {}
    filename = str(media.get("file_name") or "").strip()
    suffix = Path(filename).suffix.lower()
    if suffix:
        return suffix[:16]
    mime_type = str(media.get("mime_type") or "").strip().lower()
    guessed = mimetypes.guess_extension(mime_type) if mime_type else ""
    if guessed:
        return guessed.lower()[:16]
    if message.get("kind") == "image":
        return ".jpg"
    return ".bin"


def _telegram_attachment_spool_dir(data_dir: str | Path, message: dict[str, Any]) -> Path:
    chat_handle = str(message.get("chat_handle") or _chat_handle(message.get("chat_id")) or "chat")
    message_id = str(message.get("message_id") or int(time.time() * 1000))
    digest = hashlib.sha256(f"{chat_handle}:{message_id}".encode("utf-8")).hexdigest()[:16]
    return Path(data_dir) / "universal_inbox_telegram" / digest


def _call_file_bytes_provider(
    provider: Callable[..., bytes],
    message: dict[str, Any],
    *,
    max_bytes: int,
) -> bytes:
    try:
        return bytes(provider(message, max_bytes=max_bytes))
    except TypeError:
        return bytes(provider(message))


def run_telegram_universal_inbox_attachment_pipeline(
    message: dict[str, Any],
    *,
    data_dir: str | Path,
    file_bytes_provider: Callable[..., bytes] | None = None,
) -> dict[str, Any] | None:
    """Download a Telegram attachment into a local spool and run Universal Inbox."""

    if message.get("kind") not in {"document", "image"}:
        return None
    if message.get("chat_allowed") is not True:
        return {"status": "blocked", "reason": "chat_not_allowed", "raw_content_visible": False}
    limit = _telegram_attachment_max_bytes()
    media = message.get("media") if isinstance(message.get("media"), dict) else {}
    declared_size = media.get("file_size")
    if isinstance(declared_size, int) and declared_size > limit:
        return {"status": "blocked", "reason": "size_limit_exceeded", "raw_content_visible": False}

    provider = file_bytes_provider or download_telegram_file_bytes
    try:
        payload = _call_file_bytes_provider(provider, message, max_bytes=limit)
    except Exception as exc:
        return {
            "status": "failed",
            "reason": str(exc)[:120],
            "raw_content_visible": False,
            "raw_identifiers_visible": False,
        }
    if len(payload) > limit:
        return {"status": "blocked", "reason": "size_limit_exceeded", "raw_content_visible": False}
    if not payload:
        return {"status": "failed", "reason": "empty_file", "raw_content_visible": False}

    spool_dir = _telegram_attachment_spool_dir(data_dir, message)
    spool_dir.mkdir(parents=True, exist_ok=True)
    suffix = _telegram_attachment_suffix(message)
    target = spool_dir / f"telegram-attachment{suffix}"
    target.write_bytes(payload)

    snapshot = build_universal_inbox_readiness(spool_dir)
    return {
        "status": "processed" if snapshot.get("ready") else "blocked",
        "reason": snapshot.get("reason") or "",
        "universal_inbox_status": snapshot.get("status"),
        "discovered_count": snapshot.get("discovered_count"),
        "processable_count": snapshot.get("processable_count"),
        "spooled": True,
        "spool_path_visible": False,
        "raw_content_visible": False,
        "raw_identifiers_visible": False,
        "filename_visible": False,
    }


def download_telegram_voice_bytes(
    message: dict[str, Any],
    *,
    max_bytes: int | None = None,
    token: str | None = None,
    urlopen: Callable[..., Any] | None = None,
) -> bytes:
    """Download a Telegram voice file for immediate STT without persisting it."""

    media = message.get("media") if isinstance(message.get("media"), dict) else {}
    file_id = str(media.get("file_id") or "").strip()
    if not file_id:
        raise ValueError("telegram voice file id is missing")
    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise ValueError("telegram token is missing")
    limit = max(1, min(int(max_bytes or _telegram_voice_max_bytes()), 100_000_000))
    open_url = urlopen or urllib.request.urlopen

    metadata_url = f"https://api.telegram.org/bot{token}/getFile?{urllib.parse.urlencode({'file_id': file_id})}"
    with open_url(metadata_url, timeout=20) as response:  # nosec: token-gated Telegram API endpoint
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise ValueError("telegram getFile failed")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    declared_size = result.get("file_size")
    if isinstance(declared_size, int) and declared_size > limit:
        raise ValueError("telegram voice file too large")
    file_path = str(result.get("file_path") or "").strip()
    if not file_path:
        raise ValueError("telegram getFile returned no file path")

    quoted_path = urllib.parse.quote(file_path, safe="/")
    download_url = f"https://api.telegram.org/file/bot{token}/{quoted_path}"
    with open_url(download_url, timeout=30) as response:  # nosec: file path returned by token-gated Telegram API
        header_size = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
        try:
            if header_size and int(header_size) > limit:
                raise ValueError("telegram voice file too large")
        except ValueError:
            raise ValueError("telegram voice file too large") from None
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("telegram voice file too large")
    if not data:
        raise ValueError("telegram voice file is empty")
    return data


def _call_voice_bytes_provider(
    provider: Callable[..., bytes],
    message: dict[str, Any],
    *,
    max_bytes: int,
) -> bytes:
    try:
        return bytes(provider(message, max_bytes=max_bytes))
    except TypeError:
        return bytes(provider(message))


def build_telegram_live_voice_stt_provider(
    raw_message: dict[str, Any],
    *,
    voice_bytes_provider: Callable[..., bytes] | None = None,
) -> Callable[[str], str] | None:
    """Build the default live Telegram voice STT provider for one raw update."""

    if not _bool_env("TELEGRAM_VOICE_DOWNLOAD_ENABLED") or not _bool_env("TELEGRAM_VOICE_STT_ENABLED"):
        return None

    def _provider(_local_file_ref: str) -> str:
        provider = voice_bytes_provider or download_telegram_voice_bytes
        audio_bytes = _call_voice_bytes_provider(provider, raw_message, max_bytes=_telegram_voice_max_bytes())
        from services.stt import get_stt_service

        return str(get_stt_service().transcribe(audio_bytes) or "")

    return _provider


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
    else:
        reply_text = str(result or "")
        status = "accepted"
    return {
        "status": status,
        "reply_text": reply_text,
        "reply_text_present": bool(reply_text.strip()),
    }


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
    else:
        reply_text = str(result or "")
        status = "accepted"
    return {
        "status": status,
        "reply_text": reply_text,
        "reply_text_present": bool(reply_text.strip()),
    }


def _public_agent_turn_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    public = {key: value for key, value in result.items() if key != "reply_text"}
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


def _agent_failure_reply(agent_turn: dict[str, Any] | None) -> str:
    if not agent_turn or str(agent_turn.get("status") or "").lower() != "failed":
        return ""
    return (
        "Ich habe deine Nachricht erhalten und arbeite, aber das Sprachmodell "
        "konnte gerade nicht antworten. Bitte prüfe den Modell-Zugang in Odysseus."
    )


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
) -> dict[str, Any]:
    store = TelegramInboxStore(data_dir)
    polling = TelegramPollingStateStore(data_dir)
    sessions = TelegramSessionBridgeStore(data_dir)
    privacy_pins = TelegramPrivacyPinStore(data_dir)
    if not _bool_env("TELEGRAM_POLLING_ENABLED"):
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
            message = parse_telegram_update(update)
        except ValueError as exc:
            invalid += 1
            store.append_event(kind="invalid_update", status="invalid_update", error=str(exc)[:120])
            continue
        stored = store.append_inbound(message)
        should_process = bool(stored["stored"]) or bool(stored.get("retry_pending_voice"))
        if should_process:
            control_result = _handle_telegram_control_command(
                _telegram_control_command(stored["message"]),
                message=stored["message"],
                raw_chat_id=str(message.get("chat_id") or ""),
                sessions=sessions,
                session_creator=session_creator,
                reply_handler=reply_handler,
                store=store,
                pin_store=privacy_pins,
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
            message_voice_stt_provider = voice_stt_provider or build_telegram_live_voice_stt_provider(
                message,
                voice_bytes_provider=voice_bytes_provider,
            )
            voice_agent_turn, _voice_pipeline = run_telegram_voice_pipeline(
                stored["message"],
                stt_provider=message_voice_stt_provider,
            )
            run_telegram_image_action(
                stored["message"],
                enabled=_bool_env("TELEGRAM_IMAGE_ACTIONS_ENABLED"),
                image_bytes_provider=image_bytes_provider,
                worker_client=image_worker_client,
            )
            inbox_attachment = run_telegram_universal_inbox_attachment_pipeline(
                message,
                data_dir=data_dir,
                file_bytes_provider=attachment_bytes_provider,
            )
            if inbox_attachment is not None:
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
                    discovered_count=int(inbox_attachment.get("discovered_count") or 0),
                    processable_count=int(inbox_attachment.get("processable_count") or 0),
                    raw_content_visible=False,
                    raw_identifiers_visible=False,
                    filename_visible=False,
                )
                if reply_handler is not None:
                    reply_handler(
                        str(message.get("chat_id") or ""),
                        format_telegram_attachment_inbox_reply(inbox_attachment),
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
            bridge = build_agent_bridge_request(
                stored["message"],
                raw_chat_id=str(message.get("chat_id") or ""),
                voice_agent_turn=voice_agent_turn,
            )
            if bridge["ready_for_agent"]:
                binding = sessions.bind_chat(
                    chat_id=bridge["chat_id"],
                    session_alias=bridge["session_alias"],
                    recommended_session_name=bridge["recommended_session_name"],
                    creator=session_creator,
                )
                bridge = build_agent_bridge_request(
                    stored["message"],
                    session_binding=binding,
                    raw_chat_id=str(message.get("chat_id") or ""),
                    voice_agent_turn=voice_agent_turn,
                )
                store.append_event(
                    kind="session_bridge",
                    status="bound" if binding.get("session_id") else "pending_bridge",
                    chat_id=bridge["chat_id"],
                    session_id=binding.get("session_id") or "",
                )
                send_telegram_typing_indicator(bridge["chat_id"], store=store)
                agent_turn = _run_agent_turn(agent_turn_handler, bridge)
                if agent_turn is not None:
                    agent_turns += 1
                    store.append_event(
                        kind="agent_turn",
                        status=str(agent_turn.get("status") or "accepted"),
                        chat_id=bridge["chat_id"],
                        session_id=bridge.get("session_id") or "",
                        reply_text_present=bool(agent_turn.get("reply_text_present")),
                    )
                    reply_text = str(agent_turn.get("reply_text") or _agent_failure_reply(agent_turn))
                    if reply_text and reply_handler is not None:
                        reply_handler(
                            bridge["chat_id"],
                            reply_text,
                            bridge.get("source_message_id"),
                        )
                        replies += 1
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
        "offset": next_offset,
    }


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


def _telegram_http_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:  # nosec: gated by env and explicit route call
        return json.loads(response.read().decode("utf-8"))


def _rich_message_payload(markdown: str) -> tuple[dict[str, Any], str]:
    rendered = render_telegram_markdown(markdown)
    if not rendered.parse_mode:
        raise ValueError("telegram rich rendering fell back to plaintext")
    return {"html": rendered.html}, rendered.formatting_mode


def send_telegram_rich_draft(
    chat_id: str,
    partial_markdown: str,
    *,
    draft_id: int | None = None,
    source_message_id: Any = "",
    token: str | None = None,
    http_post: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send an env-gated Telegram rich draft preview."""

    if not _bool_env("TELEGRAM_RICH_MESSAGES_ENABLED"):
        raise ValueError("telegram rich messages are disabled")
    if not _bool_env("TELEGRAM_RICH_DRAFTS_ENABLED"):
        raise ValueError("telegram rich drafts are disabled")
    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise ValueError("telegram token is missing")
    if not chat_id:
        raise ValueError("telegram chat id is missing")
    if not partial_markdown.strip():
        raise ValueError("telegram draft text is empty")
    resolved_draft_id = int(draft_id or build_telegram_draft_id(chat_id=chat_id, source_message_id=source_message_id))
    if resolved_draft_id <= 0:
        raise ValueError("telegram draft id must be nonzero")
    rich_message, formatting_mode = _rich_message_payload(partial_markdown)
    url = f"https://api.telegram.org/bot{token}/sendRichMessageDraft"
    post = http_post or _telegram_http_post
    result = post(
        url,
        {
            "chat_id": str(chat_id),
            "draft_id": resolved_draft_id,
            "rich_message": json.dumps(rich_message, ensure_ascii=False),
        },
    )
    return {
        "ok": bool(result.get("ok")),
        "delivery_mode": "rich_draft",
        "formatting_mode": formatting_mode,
        "draft_id": resolved_draft_id,
        "draft_id_value_visible": False,
        "token_value_visible": False,
        "raw_rich_payload_visible": False,
    }


def send_telegram_rich_message(
    chat_id: str,
    final_markdown: str,
    *,
    token: str | None = None,
    http_post: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send an env-gated final Telegram rich message."""

    if not _bool_env("TELEGRAM_RICH_MESSAGES_ENABLED"):
        raise ValueError("telegram rich messages are disabled")
    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise ValueError("telegram token is missing")
    if not chat_id:
        raise ValueError("telegram chat id is missing")
    final_text = _strip_draft_thinking(final_markdown)
    if not final_text:
        raise ValueError("telegram rich message text is empty")
    rich_message, formatting_mode = _rich_message_payload(final_text)
    url = f"https://api.telegram.org/bot{token}/sendRichMessage"
    post = http_post or _telegram_http_post
    result = post(url, {"chat_id": str(chat_id), "rich_message": json.dumps(rich_message, ensure_ascii=False)})
    return {
        "ok": bool(result.get("ok")),
        "telegram_message_id": ((result.get("result") or {}).get("message_id")),
        "delivery_mode": "rich_final",
        "formatting_mode": formatting_mode,
        "token_value_visible": False,
        "raw_rich_payload_visible": False,
    }


def send_telegram_text(
    chat_id: str,
    text: str,
    *,
    token: str | None = None,
    http_post: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send a Telegram text message. Callers must enforce env gates first."""

    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise ValueError("telegram token is missing")
    if not chat_id:
        raise ValueError("telegram chat id is missing")
    if not text.strip():
        raise ValueError("telegram reply text is empty")
    rendered = render_telegram_markdown(text)
    chunks = chunk_telegram_html(rendered.html)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    post = http_post or _telegram_http_post
    message_ids: list[Any] = []
    ok = True
    for chunk in chunks:
        payload = {"chat_id": str(chat_id), "text": chunk}
        if rendered.parse_mode:
            payload["parse_mode"] = rendered.parse_mode
        result = post(url, payload)
        ok = ok and bool(result.get("ok"))
        message_ids.append((result.get("result") or {}).get("message_id"))
    return {
        "ok": ok,
        "telegram_message_id": message_ids[0] if message_ids else None,
        "telegram_message_ids": message_ids,
        "delivery_mode": "classic_html" if rendered.parse_mode else "classic_plaintext",
        "formatting_mode": rendered.formatting_mode,
        "parse_mode": rendered.parse_mode,
        "message_count": len(chunks),
        "token_value_visible": False,
        "raw_rich_payload_visible": False,
    }


def send_telegram_pin_message(
    chat_id: str,
    message_id: int,
    *,
    disable_notification: bool = True,
    token: str | None = None,
    http_post: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pin a Telegram message after callers have enforced chat/reply gates."""

    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise ValueError("telegram token is missing")
    if not chat_id:
        raise ValueError("telegram chat id is missing")
    if int(message_id or 0) <= 0:
        raise ValueError("telegram message id is missing")
    url = f"https://api.telegram.org/bot{token}/pinChatMessage"
    post = http_post or _telegram_http_post
    result = post(
        url,
        {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
            "disable_notification": bool(disable_notification),
        },
    )
    return {
        "ok": bool(result.get("ok")),
        "telegram_message_id": int(message_id),
        "pin_status": "pinned" if result.get("ok") else "rejected",
        "token_value_visible": False,
        "chat_id_value_visible": False,
    }


def send_telegram_unpin_message(
    chat_id: str,
    message_id: int,
    *,
    token: str | None = None,
    http_post: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Unpin a tracked Telegram message after callers have enforced chat gates."""

    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise ValueError("telegram token is missing")
    if not chat_id:
        raise ValueError("telegram chat id is missing")
    if int(message_id or 0) <= 0:
        raise ValueError("telegram message id is missing")
    url = f"https://api.telegram.org/bot{token}/unpinChatMessage"
    post = http_post or _telegram_http_post
    result = post(url, {"chat_id": str(chat_id), "message_id": int(message_id)})
    return {
        "ok": bool(result.get("ok")),
        "telegram_message_id": int(message_id),
        "pin_status": "unpinned" if result.get("ok") else "rejected",
        "token_value_visible": False,
        "chat_id_value_visible": False,
    }


def send_telegram_chat_action(
    chat_id: str,
    action: str = "typing",
    *,
    token: str | None = None,
    http_post: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Show a short Telegram chat action, e.g. the typing indicator."""

    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise ValueError("telegram token is missing")
    if not chat_id:
        raise ValueError("telegram chat id is missing")
    url = f"https://api.telegram.org/bot{token}/sendChatAction"
    post = http_post or _telegram_http_post
    result = post(url, {"chat_id": str(chat_id), "action": action or "typing"})
    return {
        "ok": bool(result.get("ok")),
        "action": action or "typing",
        "token_value_visible": False,
    }


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


def build_telegram_readiness(data_dir: str | Path | None = None) -> dict[str, Any]:
    token_present = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_present = bool(os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
    agent_chat_enabled = _bool_env("TELEGRAM_AGENT_CHAT_ENABLED")
    reply_enabled = _bool_env("TELEGRAM_AGENT_REPLY_ENABLED")
    polling_enabled = _bool_env("TELEGRAM_POLLING_ENABLED")
    rich_messages_enabled = _bool_env("TELEGRAM_RICH_MESSAGES_ENABLED")
    rich_drafts_enabled = _bool_env("TELEGRAM_RICH_DRAFTS_ENABLED")
    dsgvo_mode = _dsgvo_mode_active()

    if token_present and chat_present and agent_chat_enabled and reply_enabled:
        state = "agent_reply_ready"
        summary = "Telegram agent chat and replies are locally enabled."
    elif token_present and agent_chat_enabled:
        state = "agent_receive_ready"
        summary = "Telegram agent intake is enabled; replies still require allowed chat ids and reply gate."
    elif token_present:
        state = "token_ready"
        summary = "Telegram token env marker is present; agent chat is not enabled yet."
    else:
        state = "needs_token"
        summary = "Telegram token env marker is missing."

    if data_dir is not None:
        inbox_store = TelegramInboxStore(data_dir)
        pin_store = TelegramPrivacyPinStore(data_dir)
        counts = inbox_store.counts()
        delivery = inbox_store.last_delivery_summary()
        active_privacy_pins = pin_store.active_count()
    else:
        counts = {"total": 0, "inbound": 0, "outbound": 0, "voice": 0, "image": 0, "pending_stt": 0, "pending_image_action": 0}
        delivery = {
            "last_delivery_mode": "",
            "last_delivery_status": "",
            "formatting_mode": "html",
            "raw_rich_payload_visible": False,
        }
        active_privacy_pins = 0
    return {
        "plugin": "telegram",
        "state": state,
        "summary": summary,
        "token_env_present": token_present,
        "chat_id_env_present": chat_present,
        "agent_chat_enabled": agent_chat_enabled,
        "reply_gate_enabled": reply_enabled,
        "polling_enabled": polling_enabled,
        "rich_messages_enabled": rich_messages_enabled,
        "rich_drafts_enabled": rich_drafts_enabled,
        "draft_interval_ms": _draft_interval_ms(),
        "formatting_mode": delivery["formatting_mode"],
        "last_delivery_mode": delivery["last_delivery_mode"],
        "last_delivery_status": delivery["last_delivery_status"],
        "token_value_visible": False,
        "chat_id_value_visible": False,
        "raw_rich_payload_visible": delivery["raw_rich_payload_visible"],
        "network_enabled": bool(token_present and reply_enabled),
        "send_enabled": bool(token_present and chat_present and reply_enabled),
        "history_counts": counts,
        "voice_boundary": {
            "mode": "fakeable_pipeline" if _bool_env("TELEGRAM_VOICE_DOWNLOAD_ENABLED") or _bool_env("TELEGRAM_VOICE_STT_ENABLED") else "metadata_only",
            "pending_stt_count": int(counts.get("pending_stt") or 0),
            "download_enabled": _bool_env("TELEGRAM_VOICE_DOWNLOAD_ENABLED"),
            "stt_enabled": _bool_env("TELEGRAM_VOICE_STT_ENABLED"),
            "raw_voice_ids_visible": False,
        },
        "image_boundary": {
            "mode": "worker_client_ready" if _bool_env("TELEGRAM_IMAGE_ACTIONS_ENABLED") else "metadata_only",
            "pending_image_action_count": int(counts.get("pending_image_action") or 0),
            "image_actions_enabled": _bool_env("TELEGRAM_IMAGE_ACTIONS_ENABLED"),
            "raw_image_ids_visible": False,
        },
        "privacy_boundary": {
            "dsgvo_mode": dsgvo_mode,
            "local_only_required": runtime_requires_local_only(settings={"dsgvo_mode": dsgvo_mode}),
            "telegram_control_enabled": True,
            "telegram_commands": ["/dsgvo", "/privacy", "/gdpr", "/inbox"],
            "settings_values_visible": False,
            "pinned_status_enabled": _privacy_pin_enabled(),
            "active_pinned_status_count": active_privacy_pins,
            "pin_message_id_value_visible": False,
            "chat_feedback_modes": ["reply_message", "typing_indicator", "pinned_status_message", "status_endpoint"],
        },
        "next_allowed_action": "Enable TELEGRAM_AGENT_CHAT_ENABLED for intake and TELEGRAM_AGENT_REPLY_ENABLED for bot replies.",
    }


def _app_html(nonce: str) -> str:
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telegram</title>
<link rel="stylesheet" href="/static/plugin-theme.css">
<script src="/static/js/plugin-theme.js"></script>
</head><body>
<header class="od-header">
  <a class="brand" href="/" title="Back to Odysseus">{_CHEVRON}<span>Odysseus</span></a>
  <span class="od-title">Telegram</span>
</header>
<main class="od-wrap">
  <h1>Telegram agent chat</h1>
  <section class="od-card">
    <p class="muted">Standalone plugin for Telegram intake, local history, agent bridge payloads, and gated replies.</p>
    <div id="telegram-status" class="badge warn">Loading Telegram readiness...</div>
    <pre id="telegram-details" class="muted" style="white-space:pre-wrap;margin-top:12px"></pre>
  </section>
</main>
<script nonce="{nonce}">
(async () => {{
  const status = document.getElementById("telegram-status");
  const details = document.getElementById("telegram-details");
  try {{
    const response = await fetch("/api/plugins/telegram/status", {{ credentials: "same-origin" }});
    const snapshot = await response.json();
    status.textContent = `Telegram: ${{snapshot.state}}`;
    status.className = snapshot.send_enabled ? "badge ok" : "badge warn";
    details.textContent = [
      snapshot.summary,
      `Token env present: ${{snapshot.token_env_present ? "yes" : "no"}}`,
      `Allowed chat marker present: ${{snapshot.chat_id_env_present ? "yes" : "no"}}`,
      `Agent intake enabled: ${{snapshot.agent_chat_enabled ? "yes" : "no"}}`,
      `Reply gate enabled: ${{snapshot.reply_gate_enabled ? "yes" : "no"}}`,
      `History total: ${{snapshot.history_counts.total}}`,
      `Voice messages pending/seen: ${{snapshot.history_counts.voice}}`
    ].join("\\n");
  }} catch (error) {{
    status.textContent = "Telegram readiness unavailable";
    status.className = "badge warn";
    details.textContent = String(error && error.message ? error.message : error);
  }}
}})();
</script>
</body></html>"""


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
        )
        if not result["ok"]:
            raise HTTPException(403, result["status"])
        return result

    @router.post("/webhook")
    async def webhook(request: Request):
        _require_admin(request)
        update = await request.json()
        try:
            message = parse_telegram_update(update)
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
                discovered_count=int(inbox_attachment.get("discovered_count") or 0),
                processable_count=int(inbox_attachment.get("processable_count") or 0),
                raw_content_visible=False,
                raw_identifiers_visible=False,
                filename_visible=False,
            )
            _reply_with_gate(
                str(message.get("chat_id") or ""),
                format_telegram_attachment_inbox_reply(inbox_attachment),
                source_message_id=message.get("message_id"),
            )
        bridge = build_agent_bridge_request(
            stored["message"],
            raw_chat_id=str(message.get("chat_id") or ""),
            voice_agent_turn=voice_agent_turn,
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
        if bridge["ready_for_agent"]:
            session_binding = sessions.bind_chat(
                chat_id=bridge["chat_id"],
                session_alias=bridge["session_alias"],
                recommended_session_name=bridge["recommended_session_name"],
                creator=session_creator,
            )
        bridge = build_agent_bridge_request(
            stored["message"],
            session_binding=session_binding,
            raw_chat_id=str(message.get("chat_id") or ""),
            voice_agent_turn=voice_agent_turn,
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

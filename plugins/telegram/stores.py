"""Redacted Telegram persistence helpers.

This module owns local JSON state and stable redacted handles only. It must not
perform live Telegram API calls or persist raw chat ids, sender ids, file ids,
tokens, or private provider content.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

_HISTORY_FILE = "telegram_history.json"
_POLLING_FILE = "telegram_polling_state.json"
_SESSION_FILE = "telegram_session_bridge.json"
_PINNED_PRIVACY_FILE = "telegram_privacy_pin_state.json"


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

    def latest_universal_inbox_attachment(self, *, chat_id: str | None = None, max_age_seconds: int | None = None) -> dict[str, Any] | None:
        chat_handle = _chat_handle(chat_id) if chat_id else ""
        now = int(time.time())
        max_age = int(max_age_seconds or 0)
        for message in reversed(self._read()["messages"]):
            if message.get("kind") != "universal_inbox_attachment":
                continue
            if chat_handle and message.get("chat_handle") != chat_handle:
                continue
            if max_age > 0 and now - int(message.get("stored_at") or 0) > max_age:
                continue
            return dict(message)
        return None

    def latest_universal_inbox_memory_review(self, *, chat_id: str | None = None) -> dict[str, Any] | None:
        chat_handle = _chat_handle(chat_id) if chat_id else ""
        for message in reversed(self._read()["messages"]):
            if message.get("kind") != "universal_inbox_attachment":
                continue
            if chat_handle and message.get("chat_handle") != chat_handle:
                continue
            status = str(message.get("memory_write_intent_status") or "")
            if status in {"review", "ready"}:
                return dict(message)
        return None

    def latest_project_intake_review(self, *, chat_id: str | None = None) -> dict[str, Any] | None:
        chat_handle = _chat_handle(chat_id) if chat_id else ""
        for message in reversed(self._read()["messages"]):
            if message.get("kind") != "project_intake_review":
                continue
            if chat_handle and message.get("chat_handle") != chat_handle:
                continue
            status = str(message.get("status") or "")
            if status in {"confirmed", "held"}:
                return None
            if status in {"review", "blocked"}:
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

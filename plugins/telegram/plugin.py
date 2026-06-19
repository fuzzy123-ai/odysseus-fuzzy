"""Standalone Telegram agent-chat plugin.

The plugin is safe-by-default: it stores a local redacted inbox/history and can
prepare agent-turn payloads without calling Telegram. Outbound replies require
an explicit local env gate and never expose token values.
"""

from __future__ import annotations

import json
import os
import hashlib
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

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


def _bool_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


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
        if raw_file_id:
            sanitized_media["file_handle"] = _voice_file_handle(raw_file_id)
        if raw_unique_id:
            sanitized_media["file_unique_handle"] = _voice_unique_handle(raw_unique_id)
        stored["media"] = sanitized_media
    stored["chat_id_value_visible"] = False
    stored["sender_id_value_visible"] = False
    stored["voice_file_id_value_visible"] = False
    stored["voice_file_unique_id_value_visible"] = False
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
                self.append_event(
                    kind="duplicate",
                    status="duplicate_ignored",
                    chat_id=str(message.get("chat_id") or ""),
                    update_id=message.get("update_id"),
                    message_id=message.get("message_id"),
                )
                return {"stored": False, "message": existing}

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
        return {"stored": True, "message": stored}

    def append_outbound(
        self,
        chat_id: str,
        text: str,
        *,
        source_message_id: int | None = None,
        delivery_status: str = "sent",
        failure_reason: str | None = None,
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
            "stored_at": int(time.time()),
            "token_value_visible": False,
            "chat_id_value_visible": False,
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
            "blocked": sum(1 for m in messages if m.get("kind") == "blocked"),
            "duplicates": sum(1 for m in messages if m.get("kind") == "duplicate"),
            "pending_stt": sum(1 for m in messages if m.get("transcript_status") == "pending_stt"),
        }


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
        prompt = (
            "[Telegram voice message received. "
            f"file_handle={media.get('file_handle', '')}; duration={media.get('duration', 'unknown')}; "
            "transcription pending.]"
        )
        ready_for_agent = False
        note = "voice_needs_transcription"
    else:
        prompt = "[Unsupported Telegram message received.]"
        ready_for_agent = False
        note = "unsupported_message"

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
        "ready_for_agent": ready_for_agent and message.get("intake_status") == "ready",
        "reply_required": ready_for_agent and message.get("intake_status") == "ready",
        "note": note,
        "intake_status": message.get("intake_status") or note,
    }


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


def run_telegram_polling_cycle(
    *,
    data_dir: str | Path,
    fetch_updates: Callable[[int], list[dict[str, Any]]] | None = None,
    session_creator: Callable[..., Any] | None = None,
    agent_turn_handler: Callable[[dict[str, Any]], Any] | None = None,
    reply_handler: Callable[[str, str, int | None], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    store = TelegramInboxStore(data_dir)
    polling = TelegramPollingStateStore(data_dir)
    sessions = TelegramSessionBridgeStore(data_dir)
    if not _bool_env("TELEGRAM_POLLING_ENABLED"):
        polling.record(status="polling_disabled", offset=polling.get_offset())
        return {"ok": False, "status": "polling_disabled", "processed": 0, "offset": polling.get_offset()}
    loader = fetch_updates or fetch_telegram_updates
    offset = polling.get_offset()
    processed = 0
    invalid = 0
    agent_turns = 0
    replies = 0
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
        if stored["stored"]:
            bridge = build_agent_bridge_request(stored["message"], raw_chat_id=str(message.get("chat_id") or ""))
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
                )
                store.append_event(
                    kind="session_bridge",
                    status="bound" if binding.get("session_id") else "pending_bridge",
                    chat_id=bridge["chat_id"],
                    session_id=binding.get("session_id") or "",
                )
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
                    if agent_turn.get("reply_text") and reply_handler is not None:
                        reply_handler(
                            bridge["chat_id"],
                            str(agent_turn["reply_text"]),
                            bridge.get("source_message_id"),
                        )
                        replies += 1
            processed += 1
    next_offset = last_update_id + 1 if last_update_id else offset
    polling.record(
        status="poll_ok",
        offset=next_offset,
        processed=processed,
        invalid=invalid,
        agent_turns=agent_turns,
        replies=replies,
        last_update_id=last_update_id,
    )
    return {
        "ok": True,
        "status": "poll_ok",
        "processed": processed,
        "invalid": invalid,
        "agent_turns": agent_turns,
        "replies": replies,
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
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    post = http_post or _telegram_http_post
    result = post(url, {"chat_id": str(chat_id), "text": text})
    return {
        "ok": bool(result.get("ok")),
        "telegram_message_id": ((result.get("result") or {}).get("message_id")),
        "token_value_visible": False,
    }


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

    counts = (
        TelegramInboxStore(data_dir).counts()
        if data_dir is not None
        else {"total": 0, "inbound": 0, "outbound": 0, "voice": 0, "pending_stt": 0}
    )
    return {
        "plugin": "telegram",
        "state": state,
        "summary": summary,
        "token_env_present": token_present,
        "chat_id_env_present": chat_present,
        "agent_chat_enabled": agent_chat_enabled,
        "reply_gate_enabled": reply_enabled,
        "polling_enabled": polling_enabled,
        "token_value_visible": False,
        "chat_id_value_visible": False,
        "network_enabled": bool(token_present and reply_enabled),
        "send_enabled": bool(token_present and chat_present and reply_enabled),
        "history_counts": counts,
        "voice_boundary": {
            "mode": "metadata_only",
            "pending_stt_count": int(counts.get("pending_stt") or 0),
            "download_enabled": False,
            "stt_enabled": False,
            "raw_voice_ids_visible": False,
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
    session_creator = getattr(ctx, "telegram_session_bridge", None)
    agent_turn_handler = getattr(ctx, "telegram_agent_turn_handler", None)
    admin_gate = getattr(ctx, "require_admin", None) or require_admin

    def _require_admin(request: Request) -> None:
        admin_gate(request)

    def _reply_with_gate(chat_id: str, text: str, *, source_message_id: int | None = None) -> dict[str, Any]:
        if not _bool_env("TELEGRAM_AGENT_REPLY_ENABLED"):
            outbound = store.append_outbound(
                chat_id,
                text,
                source_message_id=source_message_id,
                delivery_status="blocked",
                failure_reason="reply_gate_disabled",
            )
            return {"error": "Telegram reply gate is disabled", "exit_code": 1, "message": outbound}
        if not _chat_allowed(chat_id):
            outbound = store.append_outbound(
                chat_id,
                text,
                source_message_id=source_message_id,
                delivery_status="blocked",
                failure_reason="chat_not_allowed",
            )
            return {"error": "Telegram chat id is not allowed", "exit_code": 1, "message": outbound}
        try:
            sent = send_telegram_text(chat_id, text)
        except Exception as exc:
            outbound = store.append_outbound(
                chat_id,
                text,
                source_message_id=source_message_id,
                delivery_status="failed",
                failure_reason=str(exc),
            )
            return {"error": str(exc), "exit_code": 1, "message": outbound}
        outbound = store.append_outbound(
            chat_id,
            text,
            source_message_id=source_message_id,
            delivery_status="sent",
        )
        return {
            "output": json.dumps({"sent": sent, "message": outbound}, ensure_ascii=False),
            "exit_code": 0,
        }

    async def _telegram_reply_tool(content: str, **kwargs):
        payload = _parse_tool_payload(content)
        chat_id = str(payload.get("chat_id") or "")
        text = str(payload.get("text") or "")
        source_message_id = payload.get("source_message_id")
        return _reply_with_gate(chat_id, text, source_message_id=source_message_id)

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
        result = run_telegram_polling_cycle(
            data_dir=ctx.data_dir,
            fetch_updates=getattr(ctx, "telegram_fetch_updates", None),
            session_creator=session_creator,
            agent_turn_handler=agent_turn_handler,
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
        bridge = build_agent_bridge_request(stored["message"], raw_chat_id=str(message.get("chat_id") or ""))
        session_binding = None
        agent_turn = None
        reply_result = None
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
        )
        agent_turn = _run_agent_turn(agent_turn_handler, bridge)
        if agent_turn is not None:
            store.append_event(
                kind="agent_turn",
                status=str(agent_turn.get("status") or "accepted"),
                chat_id=bridge["chat_id"],
                session_id=bridge.get("session_id") or "",
                reply_text_present=bool(agent_turn.get("reply_text_present")),
            )
            if agent_turn.get("reply_text"):
                reply_result = _reply_with_gate(
                    bridge["chat_id"],
                    str(agent_turn["reply_text"]),
                    source_message_id=bridge.get("source_message_id"),
                )
        return {
            "stored": stored["stored"],
            "message": stored["message"],
            "agent_bridge": bridge,
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
        result = _reply_with_gate(chat_id, text, source_message_id=body.get("source_message_id"))
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
                },
                "required": ["chat_id", "text"],
            },
            execute=_telegram_reply_tool,
            permission="admin",
        ))
    except Exception as exc:
        ctx.logger.warning("telegram_reply tool registration skipped: %s", exc)
    ctx.logger.info("telegram plugin ready")

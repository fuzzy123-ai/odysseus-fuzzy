"""Standalone Telegram agent-chat plugin.

The plugin is safe-by-default: it stores a local redacted inbox/history and can
prepare agent-turn payloads without calling Telegram. Outbound replies require
an explicit local env gate and never expose token values.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse


PLUGIN = {
    "name": "Telegram",
    "version": "0.2.0",
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


def _bool_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _allowed_chat_ids() -> tuple[str, ...]:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or ""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _chat_allowed(chat_id: str) -> bool:
    allowed = _allowed_chat_ids()
    return bool(chat_id and allowed and chat_id in allowed)


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

    def append_inbound(self, message: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        messages = data["messages"]
        key = (message.get("direction"), message.get("update_id"), message.get("message_id"))
        for existing in messages:
            existing_key = (
                existing.get("direction"),
                existing.get("update_id"),
                existing.get("message_id"),
            )
            if existing_key == key:
                return {"stored": False, "message": existing}

        stored = dict(message)
        stored["stored_at"] = int(time.time())
        messages.append(stored)
        self._write(data)
        return {"stored": True, "message": stored}

    def append_outbound(self, chat_id: str, text: str, *, source_message_id: int | None = None) -> dict[str, Any]:
        data = self._read()
        message = {
            "direction": "outbound",
            "kind": "text",
            "chat_id": str(chat_id),
            "message_id": f"local-{int(time.time() * 1000)}",
            "source_message_id": source_message_id,
            "text": text,
            "stored_at": int(time.time()),
            "token_value_visible": False,
        }
        data["messages"].append(message)
        self._write(data)
        return message

    def history(self, *, chat_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 200))
        messages = self._read()["messages"]
        if chat_id:
            messages = [m for m in messages if str(m.get("chat_id")) == str(chat_id)]
        return list(reversed(messages[-limit:]))

    def counts(self) -> dict[str, int]:
        messages = self._read()["messages"]
        return {
            "total": len(messages),
            "inbound": sum(1 for m in messages if m.get("direction") == "inbound"),
            "outbound": sum(1 for m in messages if m.get("direction") == "outbound"),
            "voice": sum(1 for m in messages if m.get("kind") == "voice"),
        }


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
        base.update({"kind": "text", "text": message["text"]})
    elif isinstance(message.get("voice"), dict):
        voice = message["voice"]
        base.update({
            "kind": "voice",
            "text": "",
            "transcript_status": "pending_stt",
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
        base.update({"kind": "unsupported", "text": "", "unsupported_keys": sorted(message.keys())})
    return base


def build_agent_bridge_request(message: dict[str, Any]) -> dict[str, Any]:
    """Build the internal agent-turn envelope for a stored Telegram message."""

    chat_id = str(message.get("chat_id") or "")
    sender = message.get("sender") or {}
    display_name = sender.get("username") or sender.get("first_name") or chat_id
    kind = message.get("kind")
    if kind == "text":
        prompt = str(message.get("text") or "")
        ready_for_agent = bool(prompt.strip())
        note = "text_ready"
    elif kind == "voice":
        media = message.get("media") or {}
        prompt = (
            "[Telegram voice message received. "
            f"file_id={media.get('file_id', '')}; duration={media.get('duration', 'unknown')}; "
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
        "session_alias": f"telegram:{chat_id}",
        "recommended_session_name": f"Telegram {display_name}",
        "chat_id": chat_id,
        "source_message_id": message.get("message_id"),
        "kind": kind,
        "prompt": prompt,
        "ready_for_agent": ready_for_agent,
        "reply_required": ready_for_agent,
        "note": note,
    }


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

    counts = TelegramInboxStore(data_dir).counts() if data_dir is not None else {"total": 0, "inbound": 0, "outbound": 0, "voice": 0}
    return {
        "plugin": "telegram",
        "state": state,
        "summary": summary,
        "token_env_present": token_present,
        "chat_id_env_present": chat_present,
        "agent_chat_enabled": agent_chat_enabled,
        "reply_gate_enabled": reply_enabled,
        "token_value_visible": False,
        "chat_id_value_visible": False,
        "network_enabled": bool(token_present and reply_enabled),
        "send_enabled": bool(token_present and chat_present and reply_enabled),
        "history_counts": counts,
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

    def _reply_with_gate(chat_id: str, text: str, *, source_message_id: int | None = None) -> dict[str, Any]:
        if not _bool_env("TELEGRAM_AGENT_REPLY_ENABLED"):
            return {"error": "Telegram reply gate is disabled", "exit_code": 1}
        if not _chat_allowed(chat_id):
            return {"error": "Telegram chat id is not allowed", "exit_code": 1}
        sent = send_telegram_text(chat_id, text)
        outbound = store.append_outbound(chat_id, text, source_message_id=source_message_id)
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
        return build_telegram_readiness(ctx.data_dir)

    @router.get("/history")
    async def history(request: Request, chat_id: str | None = None, limit: int = 50):
        return {"messages": store.history(chat_id=chat_id, limit=limit)}

    @router.post("/webhook")
    async def webhook(request: Request):
        update = await request.json()
        message = parse_telegram_update(update)
        stored = store.append_inbound(message)
        return {
            "stored": stored["stored"],
            "message": stored["message"],
            "agent_bridge": build_agent_bridge_request(stored["message"]),
            "token_value_visible": False,
        }

    @router.post("/reply")
    async def reply(request: Request):
        body = await request.json()
        chat_id = str(body.get("chat_id") or "")
        text = str(body.get("text") or "")
        result = _reply_with_gate(chat_id, text, source_message_id=body.get("source_message_id"))
        if result.get("exit_code") != 0:
            raise HTTPException(403, str(result.get("error") or "Telegram reply refused"))
        return json.loads(str(result["output"]))

    @router.get("/app")
    async def app_page(request: Request):
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

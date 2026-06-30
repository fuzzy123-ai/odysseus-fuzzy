"""Telegram outbound transport helpers.

This module contains the low-level Telegram API send helpers. Callers remain
responsible for policy gates, allowed-chat checks, and audit/store events.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from src.telegram_formatting import chunk_telegram_html, render_telegram_markdown

from plugins.telegram.stores import build_telegram_draft_id


_THINKING_BLOCK_RE = re.compile(r"<tg-thinking>.*?</tg-thinking>", re.IGNORECASE | re.DOTALL)


def _bool_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _strip_draft_thinking(markdown: str) -> str:
    return _THINKING_BLOCK_RE.sub("", str(markdown or "")).strip()


def _telegram_http_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:  # nosec: gated by env and explicit route call
        return json.loads(response.read().decode("utf-8"))


def _telegram_http_post_multipart(
    url: str,
    payload: dict[str, Any],
    file_field: str,
    file_path: str | Path,
    *,
    filename: str,
    mime_type: str = "application/octet-stream",
) -> dict[str, Any]:
    boundary = f"----OdysseusTelegram{hashlib.sha256(os.urandom(16)).hexdigest()[:24]}"
    body = bytearray()
    for key, value in payload.items():
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("ascii"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("ascii"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(Path(file_path).read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("ascii"))
    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec: token-gated Telegram API endpoint
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


def send_telegram_document(
    chat_id: str,
    file_path: str | Path,
    *,
    filename: str = "telegram-export.pdf",
    caption: str = "",
    token: str | None = None,
    http_post_multipart: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send a Telegram document. Callers must enforce env and chat gates first."""

    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise ValueError("telegram token is missing")
    if not chat_id:
        raise ValueError("telegram chat id is missing")
    path = Path(file_path)
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise ValueError("telegram document path is invalid")
    safe_filename = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(filename or "telegram-export.pdf")).strip(".-")
    if not safe_filename:
        safe_filename = "telegram-export.pdf"
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    payload: dict[str, Any] = {"chat_id": str(chat_id)}
    if caption.strip():
        payload["caption"] = caption.strip()[:1024]
    post = http_post_multipart or _telegram_http_post_multipart
    result = post(
        url,
        payload,
        "document",
        path,
        filename=safe_filename,
        mime_type=mimetypes.guess_type(safe_filename)[0] or "application/octet-stream",
    )
    return {
        "ok": bool(result.get("ok")),
        "telegram_message_id": ((result.get("result") or {}).get("message_id")),
        "delivery_mode": "document",
        "formatting_mode": "document_caption",
        "token_value_visible": False,
        "raw_file_payload_visible": False,
        "filename_visible": False,
        "host_paths_visible": False,
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

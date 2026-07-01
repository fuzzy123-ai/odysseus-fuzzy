"""Telegram live-capable file and Universal Inbox pipeline helpers.

The helpers in this module are inert until called by gated plugin routes or
tests. They do not persist tokens, chat IDs, raw identifiers, or raw content in
their public reports.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from src.memory_triage_contract import normalize_memory_write_intent_status
from src.universal_inbox_readiness import build_universal_inbox_readiness

from plugins.telegram.attachments import (
    _telegram_attachment_max_bytes,
    _telegram_attachment_spool_dir,
    _telegram_attachment_suffix,
)


def _telegram_voice_max_bytes() -> int:
    raw = os.getenv("TELEGRAM_VOICE_MAX_BYTES") or "10000000"
    try:
        value = int(raw)
    except ValueError:
        value = 10_000_000
    return max(1, min(value, 100_000_000))


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
    memory_write_intent_status = normalize_memory_write_intent_status(
        snapshot.get("memory_write_intent_status") or ""
    )
    return {
        "status": "processed" if snapshot.get("ready") else "blocked",
        "reason": snapshot.get("reason") or "",
        "universal_inbox_status": snapshot.get("status"),
        "memory_write_intent_status": memory_write_intent_status,
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
    enabled: bool = False,
) -> Callable[[str], str] | None:
    """Build the default live Telegram voice STT provider for one raw update."""

    if not enabled:
        return None

    def _provider(_local_file_ref: str) -> str:
        provider = voice_bytes_provider or download_telegram_voice_bytes
        audio_bytes = _call_voice_bytes_provider(provider, raw_message, max_bytes=_telegram_voice_max_bytes())
        from services.stt import get_stt_service

        return str(get_stt_service().transcribe(audio_bytes) or "")

    return _provider

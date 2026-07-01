"""Telegram attachment helper functions.

These helpers are local and deterministic: they do not call Telegram, write to
Nextcloud, persist raw content, or dispatch replies.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

from src.memory_triage_contract import normalize_memory_write_intent_status
from plugins.telegram.stores import _chat_handle


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


def _format_universal_inbox_memory_review_status(review: dict[str, Any]) -> str:
    status = normalize_memory_write_intent_status(
        review.get("memory_write_intent_status") or "",
        fallback="unknown",
    )
    inbox_status = str(review.get("universal_inbox_status") or "unknown")
    if status == "ready":
        return (
            "Universal Inbox Memory: bereit zur Freigabe.\n"
            f"Inbox-Status: {inbox_status}\n"
            "Es wird nur eine redaktierte Abstraktion vorgemerkt, kein Rohinhalt.\n"
            "Zum Bestaetigen antworte mit /review memory ok."
        )
    return (
        "Universal Inbox Memory: Review nötig.\n"
        f"Memory-Status: {status}\n"
        f"Inbox-Status: {inbox_status}\n"
        "Zum Bestätigen antworte mit /review memory ok."
    )


def format_telegram_attachment_inbox_reply(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "failed")
    inbox_status = str(result.get("universal_inbox_status") or "")
    memory_status = normalize_memory_write_intent_status(
        result.get("memory_write_intent_status") or "",
        fallback="unknown",
    )
    maintenance_action = str(result.get("maintenance_action") or "").strip()
    processable = int(result.get("processable_count") or 0)
    if status == "processed" and inbox_status == "go" and memory_status:
        lines = [
            f"Anhang verarbeitet. Items: {processable}. Keine Inbox-Review noetig.",
            f"Memory/Raptor-Intent: {memory_status}.",
        ]
        if maintenance_action:
            lines.append(f"Maintenance: {maintenance_action}.")
        if memory_status == "ready":
            lines.append("Zum Schreiben der redigierten Abstraktion antworte mit /review memory ok.")
        return "\n".join(lines)
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


def _telegram_attachment_family(message: dict[str, Any]) -> str:
    kind = str(message.get("kind") or "").strip().lower()
    if kind == "image":
        return "image"
    if kind == "document":
        return "document"
    return "unknown"


def _telegram_attachment_spool_dir(data_dir: str | Path, message: dict[str, Any]) -> Path:
    chat_handle = str(message.get("chat_handle") or _chat_handle(message.get("chat_id")) or "chat")
    message_id = str(message.get("message_id") or int(time.time() * 1000))
    digest = hashlib.sha256(f"{chat_handle}:{message_id}".encode("utf-8")).hexdigest()[:16]
    return Path(data_dir) / "universal_inbox_telegram" / digest


def _telegram_attachment_context_ttl_seconds() -> int:
    raw = os.getenv("TELEGRAM_ATTACHMENT_CONTEXT_TTL_SECONDS") or "21600"
    try:
        value = int(raw)
    except ValueError:
        value = 21600
    return max(60, min(value, 86400))


def _telegram_attachment_context_max_chars() -> int:
    raw = os.getenv("TELEGRAM_ATTACHMENT_CONTEXT_MAX_CHARS") or "12000"
    try:
        value = int(raw)
    except ValueError:
        value = 12000
    return max(512, min(value, 50000))


def _telegram_attachment_context_max_extract_bytes() -> int:
    raw = os.getenv("TELEGRAM_ATTACHMENT_CONTEXT_MAX_EXTRACT_BYTES") or "4194304"
    try:
        value = int(raw)
    except ValueError:
        value = 4 * 1024 * 1024
    return max(64 * 1024, min(value, 64 * 1024 * 1024))


def _telegram_attachment_spool_key(message: dict[str, Any]) -> str:
    chat_handle = str(message.get("chat_handle") or _chat_handle(message.get("chat_id")) or "chat")
    message_id = str(message.get("message_id") or "")
    if not message_id:
        return ""
    return hashlib.sha256(f"{chat_handle}:{message_id}".encode("utf-8")).hexdigest()[:16]

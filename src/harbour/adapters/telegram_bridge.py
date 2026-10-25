"""Harbour Periphery Adapter: Telegram Bridge for Universal Inbox.

Provides DTO parsing, identifier masking, secret-scrubbing, filename
sanitization, and mapping into canonical Harbour Universal Inbox captures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping

MAX_INBOX_DOCUMENT_BYTES = 256 * 1024

_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+([a-zA-Z0-9_\-\.~+/]+=*)")
_GITHUB_TOKEN_PATTERN = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b")
_OPENAI_STRIPE_KEY_PATTERN = re.compile(r"\bsk-[a-zA-Z0-9_\-]{16,}\b")
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,50}\b")
_KEY_VALUE_SECRET_PATTERN = re.compile(
    r"""(?i)\b(api[_-]?key|token|secret|password|passwd|auth[_-]?token|access[_-]?token)\b(\s*[:=]\s*)(['"][^'"]+['"]|[^\s,;]+)"""
)


@dataclass(frozen=True, slots=True)
class TelegramUpdateDto:
    """Sanitized and masked data transfer object for incoming Telegram updates."""

    update_id: int | str
    message_id: int | str
    chat_id_masked: str
    user_id_masked: str
    update_type: str  # "text", "document", "photo", "unsupported"
    text: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    file_id: str | None = None
    file_size: int | None = None
    raw_content: bytes | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def mask_telegram_identifier(identifier: Any) -> str:
    """Mask a Telegram identifier (chat_id, user_id) deterministically using SHA-256."""
    if identifier is None:
        return ""
    raw = str(identifier).strip()
    if not raw:
        return ""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"masked_{digest[:16]}"


def sanitize_telegram_text(text: str | None) -> str:
    """Scrub credentials and secrets from Telegram message text."""
    if not text or not isinstance(text, str):
        return ""
    result = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    result = _GITHUB_TOKEN_PATTERN.sub("[REDACTED]", result)
    result = _OPENAI_STRIPE_KEY_PATTERN.sub("[REDACTED]", result)
    result = _TELEGRAM_BOT_TOKEN_PATTERN.sub("[REDACTED]", result)
    result = _KEY_VALUE_SECRET_PATTERN.sub(r"\1\2[REDACTED]", result)
    return result


def sanitize_telegram_filename(
    filename: str | None,
    default_name: str = "telegram_attachment.bin",
) -> str:
    """Sanitize filename to prevent path traversal and unsafe character injection."""
    if not filename or not isinstance(filename, str):
        return default_name
    clean = Path(filename.replace("\\", "/")).name
    clean = re.sub(r"[^A-Za-z0-9._-]", "_", clean)
    clean = clean.lstrip("._")
    if not clean or clean in {".", ".."}:
        return default_name
    return clean


def parse_telegram_update_to_dto(update: Mapping[str, Any]) -> TelegramUpdateDto:
    """Parse raw Telegram update dictionary into a sanitized TelegramUpdateDto.

    Handles text, document, photo, and unsupported message variants.
    """
    if not isinstance(update, Mapping):
        raise ValueError("Telegram update must be a dictionary")

    update_id = update.get("update_id", "")
    message = (
        update.get("message")
        or update.get("edited_message")
        or update.get("channel_post")
        or update.get("edited_channel_post")
    )

    if not isinstance(message, Mapping):
        return TelegramUpdateDto(
            update_id=update_id,
            message_id="",
            chat_id_masked="",
            user_id_masked="",
            update_type="unsupported",
            metadata={"reason": "no_message_found"},
        )

    message_id = message.get("message_id", "")
    chat = message.get("chat") if isinstance(message.get("chat"), Mapping) else {}
    sender = message.get("from") if isinstance(message.get("from"), Mapping) else {}

    chat_id_masked = mask_telegram_identifier(chat.get("id"))
    user_id_masked = mask_telegram_identifier(sender.get("id"))

    date_val = message.get("date")
    timestamp = None
    if date_val is not None:
        try:
            timestamp = datetime.fromtimestamp(float(date_val), tz=timezone.utc).isoformat()
        except (ValueError, TypeError, OSError):
            timestamp = None

    # 1. Text message
    if "text" in message and isinstance(message["text"], str):
        clean_text = sanitize_telegram_text(message["text"])
        return TelegramUpdateDto(
            update_id=update_id,
            message_id=message_id,
            chat_id_masked=chat_id_masked,
            user_id_masked=user_id_masked,
            update_type="text",
            text=clean_text,
            timestamp=timestamp,
        )

    # 2. Document message
    if "document" in message and isinstance(message["document"], Mapping):
        doc = message["document"]
        raw_filename = doc.get("file_name")
        filename = sanitize_telegram_filename(raw_filename)
        caption = message.get("caption")
        clean_caption = sanitize_telegram_text(caption) if isinstance(caption, str) else None
        return TelegramUpdateDto(
            update_id=update_id,
            message_id=message_id,
            chat_id_masked=chat_id_masked,
            user_id_masked=user_id_masked,
            update_type="document",
            text=clean_caption,
            filename=filename,
            mime_type=doc.get("mime_type"),
            file_id=doc.get("file_id"),
            file_size=doc.get("file_size"),
            timestamp=timestamp,
        )

    # 3. Photo message
    if ("photo" in message and isinstance(message["photo"], list) and message["photo"]) or (
        "image" in message and isinstance(message["image"], Mapping)
    ):
        if "photo" in message:
            photos = message["photo"]
            best_photo = photos[-1] if isinstance(photos[-1], Mapping) else {}
        else:
            best_photo = message["image"]
        file_id = best_photo.get("file_id")
        file_size = best_photo.get("file_size")
        caption = message.get("caption")
        clean_caption = sanitize_telegram_text(caption) if isinstance(caption, str) else None
        filename = f"telegram_photo_{file_id or 'img'}.jpg"
        return TelegramUpdateDto(
            update_id=update_id,
            message_id=message_id,
            chat_id_masked=chat_id_masked,
            user_id_masked=user_id_masked,
            update_type="photo",
            text=clean_caption,
            filename=filename,
            mime_type="image/jpeg",
            file_id=file_id,
            file_size=file_size,
            timestamp=timestamp,
        )

    # 4. Unsupported
    return TelegramUpdateDto(
        update_id=update_id,
        message_id=message_id,
        chat_id_masked=chat_id_masked,
        user_id_masked=user_id_masked,
        update_type="unsupported",
        timestamp=timestamp,
        metadata={"message_keys": list(message.keys())},
    )


def map_telegram_dto_to_inbox_capture(
    dto: TelegramUpdateDto,
    content_override: bytes | str | None = None,
) -> dict[str, Any]:
    """Map a TelegramUpdateDto to the canonical Harbour inbox capture schema with 256 KB budget guard."""
    if content_override is not None:
        raw_bytes = content_override.encode("utf-8") if isinstance(content_override, str) else content_override
    elif dto.raw_content is not None:
        raw_bytes = dto.raw_content
    elif dto.text is not None:
        raw_bytes = dto.text.encode("utf-8")
    else:
        raw_bytes = b""

    if len(raw_bytes) > MAX_INBOX_DOCUMENT_BYTES:
        raise ValueError(
            f"Telegram payload exceeds 256 KB budget (got {len(raw_bytes)} bytes)"
        )

    filename = dto.filename or (
        f"telegram_{dto.message_id or 'msg'}.txt" if dto.update_type == "text" else "telegram_item.bin"
    )
    sanitized_filename = sanitize_telegram_filename(filename)
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    doc_id = f"doc_{content_hash[:16]}"
    now_utc = datetime.now(timezone.utc).isoformat()

    return {
        "schema": "odysseus.harbour.inbox_captured_document.v1",
        "document_id": doc_id,
        "filename": sanitized_filename,
        "size_bytes": len(raw_bytes),
        "content_hash": f"sha256:{content_hash}",
        "owner_subject_id": dto.user_id_masked or "telegram:anonymous",
        "captured_at": now_utc,
        "stamped": False,
        "source_adapter": "telegram",
        "metadata": {
            "update_id": dto.update_id,
            "message_id": dto.message_id,
            "chat_id_masked": dto.chat_id_masked,
            "user_id_masked": dto.user_id_masked,
            "update_type": dto.update_type,
            "mime_type": dto.mime_type,
            "file_id": dto.file_id,
        },
    }

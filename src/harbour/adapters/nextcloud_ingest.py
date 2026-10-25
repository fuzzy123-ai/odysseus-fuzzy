"""Harbour Periphery Adapter: Nextcloud Ingestion for Universal Inbox.

Provides Nextcloud notification parsing, path traversal rejection,
budget validation, and mapping into canonical Harbour Universal Inbox captures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Mapping

MAX_INBOX_DOCUMENT_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class NextcloudNotificationDto:
    """Data transfer object representing an incoming Nextcloud notification or sync event."""

    notification_id: str | int
    path: str
    file_id: str | int | None = None
    mimetype: str | None = None
    size_bytes: int = 0
    etag: str | None = None
    user_id: str | None = None
    event_type: str = "change"
    timestamp: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def sanitize_nextcloud_path(path: str) -> str:
    """Sanitize Nextcloud path and reject directory traversal."""
    if not path or not isinstance(path, str):
        raise ValueError("Path must be a non-empty string")

    if "\x00" in path:
        raise ValueError("Path contains null bytes")

    normalized = path.replace("\\", "/").strip()
    parts = [p for p in normalized.split("/") if p]
    if any(p == ".." for p in parts) or ":" in normalized:
        raise ValueError(f"Directory traversal detected in Nextcloud path: {path}")

    clean_path = "/" + "/".join(parts)
    return clean_path


def parse_nextcloud_notification(payload: Mapping[str, Any]) -> NextcloudNotificationDto:
    """Parse and validate a Nextcloud notification or webhook payload."""
    if not isinstance(payload, Mapping):
        raise ValueError("Nextcloud payload must be a dictionary")

    path_raw = (
        payload.get("path")
        or payload.get("file", {}).get("path")
        or payload.get("data", {}).get("path")
        or payload.get("subject")
    )
    if not path_raw:
        raise ValueError("Nextcloud notification missing 'path'")

    sanitized_path = sanitize_nextcloud_path(path_raw)

    notification_id = payload.get("notification_id") or payload.get("id") or ""
    file_info = payload.get("file") if isinstance(payload.get("file"), Mapping) else {}
    data_info = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}

    file_id = (
        payload.get("file_id")
        or file_info.get("id")
        or data_info.get("file_id")
    )
    mimetype = (
        payload.get("mimetype")
        or payload.get("mime")
        or file_info.get("mimetype")
        or data_info.get("mimetype")
    )
    size = (
        payload.get("size_bytes")
        or payload.get("size")
        or file_info.get("size")
        or data_info.get("size")
        or 0
    )
    try:
        size_bytes = int(size)
    except (ValueError, TypeError):
        size_bytes = 0

    etag = (
        payload.get("etag")
        or file_info.get("etag")
        or data_info.get("etag")
    )
    user_id = (
        payload.get("user_id")
        or payload.get("user")
        or data_info.get("user")
    )
    event_type = (
        payload.get("event_type")
        or payload.get("event")
        or data_info.get("event")
        or "change"
    )
    timestamp = payload.get("timestamp") or payload.get("datetime")
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    return NextcloudNotificationDto(
        notification_id=notification_id,
        path=sanitized_path,
        file_id=str(file_id) if file_id is not None else None,
        mimetype=mimetype,
        size_bytes=size_bytes,
        etag=etag,
        user_id=str(user_id) if user_id is not None else None,
        event_type=event_type,
        timestamp=timestamp,
    )


def validate_nextcloud_budget(
    dto: NextcloudNotificationDto,
    content: bytes | str | None = None,
) -> bool:
    """Validate that Nextcloud notification/content does not exceed the 256 KB budget."""
    if content is not None:
        size = len(content.encode("utf-8") if isinstance(content, str) else content)
    else:
        size = dto.size_bytes

    if size > MAX_INBOX_DOCUMENT_BYTES:
        raise ValueError(
            f"Nextcloud payload exceeds 256 KB budget: {size} bytes (limit: {MAX_INBOX_DOCUMENT_BYTES})"
        )
    return True


def map_nextcloud_dto_to_inbox_capture(
    dto: NextcloudNotificationDto,
    content_override: bytes | str | None = None,
) -> dict[str, Any]:
    """Map a NextcloudNotificationDto to the canonical Harbour inbox capture schema with 256 KB budget guard."""
    validate_nextcloud_budget(dto, content=content_override)

    if content_override is not None:
        raw_bytes = content_override.encode("utf-8") if isinstance(content_override, str) else content_override
        size_bytes = len(raw_bytes)
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
    else:
        size_bytes = dto.size_bytes
        path_seed = f"{dto.path}:{dto.etag or ''}:{dto.file_id or ''}".encode("utf-8")
        content_hash = hashlib.sha256(path_seed).hexdigest()

    filename = Path(dto.path).name or "nextcloud_document.bin"
    doc_id = f"doc_{content_hash[:16]}"
    now_utc = datetime.now(timezone.utc).isoformat()

    return {
        "schema": "odysseus.harbour.inbox_captured_document.v1",
        "document_id": doc_id,
        "filename": filename,
        "size_bytes": size_bytes,
        "content_hash": f"sha256:{content_hash}",
        "owner_subject_id": dto.user_id or "nextcloud:system",
        "captured_at": now_utc,
        "stamped": False,
        "source_adapter": "nextcloud",
        "metadata": {
            "notification_id": dto.notification_id,
            "nextcloud_path": dto.path,
            "file_id": dto.file_id,
            "etag": dto.etag,
            "mimetype": dto.mimetype,
            "event_type": dto.event_type,
        },
    }

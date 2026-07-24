"""Owner-scoped Universal Inbox browse projections.

This module is the narrow boundary between the file-backed upload index and
browser-facing inbox data. It never reads upload bytes and projects only an
explicit metadata allowlist; storage paths, hashes, raw content, and owner
identifiers are intentionally absent from every returned item.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Mapping, Protocol

from src.upload_handler import is_valid_upload_id
from src.universal_inbox_file_types import classify_universal_inbox_file
from src.universal_inbox_workbench import (
    build_universal_inbox_workbench_capability,
)


UNIVERSAL_INBOX_ITEMS_SCHEMA = "odysseus.universal_inbox.items.v1"
UNIVERSAL_INBOX_ITEM_SCHEMA = "odysseus.universal_inbox.item.v1"
DEFAULT_PAGE_LIMIT = 25
MAX_PAGE_LIMIT = 100

_CURSOR_PREFIX = "v1:"
_MAX_CURSOR_OFFSET = 1_000_000_000
_MIME_RE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)


class UniversalInboxBrowseError(ValueError):
    """Base error for invalid Universal Inbox browse input."""


class UniversalInboxAuthenticationRequired(PermissionError):
    """Raised when configured authentication has no effective user."""


class UniversalInboxOwnerScopeDenied(PermissionError):
    """Raised when a caller requests another owner's scope without admin access."""


class UniversalInboxIndexUnavailable(RuntimeError):
    """Raised when no safe upload-index metadata reader is available."""


class UploadMetadataSource(Protocol):
    """Minimal metadata-only source required by the browse service."""

    def list_upload_metadata(self) -> tuple[Mapping[str, Any], ...]:
        """Return upload metadata rows without reading upload bytes."""


@dataclass(frozen=True, slots=True)
class UploadHandlerMetadataSource:
    """Adapt the existing UploadHandler to a metadata-only browse boundary."""

    upload_handler: Any

    def list_upload_metadata(self) -> tuple[Mapping[str, Any], ...]:
        if self.upload_handler is None:
            raise UniversalInboxIndexUnavailable(
                "Upload browse backend is not available"
            )

        public_reader = getattr(
            self.upload_handler,
            "list_upload_metadata",
            None,
        )
        if callable(public_reader):
            rows = public_reader()
            return _normalize_metadata_rows(rows)

        # UploadHandler predates this browse contract. Keep its private index
        # shape contained here so routes and browser models never couple to
        # uploads.json or its storage keys.
        legacy_reader = getattr(self.upload_handler, "_load_upload_index", None)
        if not callable(legacy_reader):
            raise UniversalInboxIndexUnavailable(
                "Upload browse backend is not available"
            )
        index = legacy_reader()
        if not isinstance(index, Mapping):
            raise UniversalInboxIndexUnavailable(
                "Upload browse backend returned an invalid index"
            )
        return _normalize_metadata_rows(index.values())


def browse_universal_inbox_items(
    upload_handler: Any,
    *,
    caller_owner: str | None,
    auth_manager: Any = None,
    requested_owner: str | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return one owner-scoped, metadata-only Universal Inbox page."""

    target_owner, admin_override = resolve_universal_inbox_owner_scope(
        caller_owner=caller_owner,
        auth_manager=auth_manager,
        requested_owner=requested_owner,
    )
    items = load_owner_scoped_universal_inbox_items(
        UploadHandlerMetadataSource(upload_handler),
        target_owner=target_owner,
    )
    return paginate_universal_inbox_items(
        items,
        limit=limit,
        cursor=cursor,
        admin_override=admin_override,
    )


def load_owner_scoped_universal_inbox_items(
    source: UploadMetadataSource,
    *,
    target_owner: str | None,
) -> tuple[dict[str, Any], ...]:
    """Load and project all rows for one already-authorized owner scope."""

    rows = source.list_upload_metadata()
    projected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _row_belongs_to_owner(row, target_owner):
            continue
        item = project_universal_inbox_upload_item(row)
        if item is None:
            continue
        projected[item["source_ref"]] = item

    return tuple(
        sorted(
            projected.values(),
            key=lambda item: (
                str(item["metadata"].get("uploaded_at") or ""),
                str(item["source_ref"]),
            ),
            reverse=True,
        )
    )


def project_universal_inbox_upload_item(
    row: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project one upload-index row into the browser-safe item contract."""

    upload_id = str(row.get("id") or "").strip()
    if not is_valid_upload_id(upload_id):
        return None

    display_name = _safe_display_name(
        row.get("original_name") or row.get("name") or upload_id
    )
    mime_type = _safe_mime_type(row.get("mime"))
    decision = classify_universal_inbox_file(
        display_name,
        mime_type=mime_type,
    )
    capability = build_universal_inbox_workbench_capability(
        decision,
        owner_authorized=True,
        has_working_copy=False,
        browser_download_allowed=True,
        provider_write_requested=False,
    )
    status = _status_from_decision(decision)

    return {
        "schema": UNIVERSAL_INBOX_ITEM_SCHEMA,
        "source_ref": f"upload:{upload_id}",
        "source_kind": "upload",
        "display_name": display_name,
        "status": status,
        "metadata": {
            "suffix": decision.suffix,
            "mime_type": decision.mime_type,
            "family": decision.family,
            "category": decision.category,
            "size_bytes": _safe_size(row.get("size")),
            "uploaded_at": _safe_timestamp(row.get("uploaded_at")),
            "extractable_now": decision.extractable_now,
            "review_required": decision.review_required,
            "blocked": decision.blocked,
            "reason_codes": list(decision.reason_codes),
        },
        "capability": capability.to_dict(),
        "absolute_path_visible": False,
        "raw_content_visible": False,
        "owner_identifier_visible": False,
        "hash_visible": False,
    }


def paginate_universal_inbox_items(
    items: tuple[dict[str, Any], ...],
    *,
    limit: int,
    cursor: str | None,
    admin_override: bool,
) -> dict[str, Any]:
    """Build a bounded page from an already sorted item tuple."""

    if isinstance(limit, bool) or not isinstance(limit, int):
        raise UniversalInboxBrowseError("limit must be an integer")
    if limit < 1 or limit > MAX_PAGE_LIMIT:
        raise UniversalInboxBrowseError(
            f"limit must be between 1 and {MAX_PAGE_LIMIT}"
        )

    offset = decode_universal_inbox_cursor(cursor)
    page_items = list(items[offset : offset + limit])
    next_offset = offset + len(page_items)
    has_more = next_offset < len(items)
    next_cursor = encode_universal_inbox_cursor(next_offset) if has_more else None

    return {
        "schema": UNIVERSAL_INBOX_ITEMS_SCHEMA,
        "scope": {
            "source_kind": "upload",
            "owner_scoped": True,
            "admin_override": admin_override,
            "owner_identifier_visible": False,
        },
        "items": page_items,
        "page": {
            "limit": limit,
            "returned_count": len(page_items),
            "has_more": has_more,
            "next_cursor": next_cursor,
        },
        "absolute_paths_visible": False,
        "raw_content_visible": False,
    }


def resolve_universal_inbox_owner_scope(
    *,
    caller_owner: str | None,
    auth_manager: Any = None,
    requested_owner: str | None = None,
) -> tuple[str | None, bool]:
    """Resolve one explicit scope without ever falling back to a global list."""

    caller = _normalize_owner(caller_owner, field_name="caller owner")
    requested = _normalize_owner(
        requested_owner,
        field_name="requested owner",
        allow_none=True,
    )
    auth_configured = bool(
        auth_manager and getattr(auth_manager, "is_configured", False)
    )
    if auth_configured and not caller:
        raise UniversalInboxAuthenticationRequired("Not authenticated")

    if requested is None:
        return caller, False
    if requested == caller:
        return caller, False
    if not caller or not _is_admin(auth_manager, caller):
        raise UniversalInboxOwnerScopeDenied(
            "Universal Inbox owner scope not found"
        )
    return requested, True


def encode_universal_inbox_cursor(offset: int) -> str:
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise UniversalInboxBrowseError("cursor offset must be an integer")
    if offset < 0 or offset > _MAX_CURSOR_OFFSET:
        raise UniversalInboxBrowseError("cursor offset is out of range")
    raw = f"{_CURSOR_PREFIX}{offset}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_universal_inbox_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    value = str(cursor).strip()
    if not value or len(value) > 64:
        raise UniversalInboxBrowseError("Invalid Universal Inbox cursor")
    try:
        padded = value + ("=" * (-len(value) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        prefix, raw_offset = decoded.split(":", 1)
        if f"{prefix}:" != _CURSOR_PREFIX or not raw_offset.isdigit():
            raise ValueError
        offset = int(raw_offset)
    except (UnicodeError, ValueError) as exc:
        raise UniversalInboxBrowseError(
            "Invalid Universal Inbox cursor"
        ) from exc
    if offset > _MAX_CURSOR_OFFSET:
        raise UniversalInboxBrowseError("Invalid Universal Inbox cursor")
    return offset


def _normalize_metadata_rows(rows: Any) -> tuple[Mapping[str, Any], ...]:
    if rows is None:
        return ()
    if isinstance(rows, Mapping):
        values = rows.values()
    else:
        try:
            values = iter(rows)
        except TypeError as exc:
            raise UniversalInboxIndexUnavailable(
                "Upload browse backend returned invalid metadata"
            ) from exc
    return tuple(dict(row) for row in values if isinstance(row, Mapping))


def _row_belongs_to_owner(
    row: Mapping[str, Any],
    target_owner: str | None,
) -> bool:
    row_owner = row.get("owner")
    if target_owner is None:
        return row_owner is None
    return isinstance(row_owner, str) and row_owner == target_owner


def _safe_display_name(value: Any) -> str:
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char >= " " and char != "\x7f")
    name = name.strip()
    if not name:
        return "unnamed"
    return name[:255]


def _safe_mime_type(value: Any) -> str:
    mime_type = str(value or "").strip().lower()
    if not _MIME_RE.fullmatch(mime_type):
        return "application/octet-stream"
    return mime_type


def _safe_size(value: Any) -> int | None:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    return size if size >= 0 else None


def _safe_timestamp(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw or len(raw) > 64:
        return None
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.isoformat()


def _status_from_decision(decision: Any) -> str:
    if decision.blocked:
        return "blocked"
    if decision.category == "unsupported":
        return "unsupported"
    if decision.review_required:
        return "needs_review"
    return "uploaded"


def _normalize_owner(
    value: str | None,
    *,
    field_name: str,
    allow_none: bool = True,
) -> str | None:
    if value is None:
        return None if allow_none else ""
    normalized = str(value).strip()
    if not normalized or len(normalized) > 128:
        raise UniversalInboxBrowseError(f"Invalid {field_name}")
    return normalized


def _is_admin(auth_manager: Any, caller_owner: str) -> bool:
    check = getattr(auth_manager, "is_admin", None)
    if not callable(check):
        return False
    try:
        return bool(check(caller_owner))
    except Exception:
        return False


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_LIMIT",
    "UNIVERSAL_INBOX_ITEM_SCHEMA",
    "UNIVERSAL_INBOX_ITEMS_SCHEMA",
    "UniversalInboxAuthenticationRequired",
    "UniversalInboxBrowseError",
    "UniversalInboxIndexUnavailable",
    "UniversalInboxOwnerScopeDenied",
    "UploadHandlerMetadataSource",
    "UploadMetadataSource",
    "browse_universal_inbox_items",
    "decode_universal_inbox_cursor",
    "encode_universal_inbox_cursor",
    "load_owner_scoped_universal_inbox_items",
    "paginate_universal_inbox_items",
    "project_universal_inbox_upload_item",
    "resolve_universal_inbox_owner_scope",
]

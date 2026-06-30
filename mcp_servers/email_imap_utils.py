"""Small IMAP and email-header utilities for the email MCP server."""

from __future__ import annotations

import re

from mcp.types import TextContent


def _b(value) -> bytes:
    return str(value).encode()


def _q(name: str) -> str:
    """Quote an IMAP mailbox name for commands that take mailbox args."""
    return '"' + (name or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _uid_fetch_rows(data) -> list:
    return [item for item in (data or []) if isinstance(item, bytes) and b"UID " in item]


def _confirmed(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on", "confirmed"}
    return False


def _email_delete_confirmation_required(
    reason: str,
    *,
    uid: str | None = None,
    count: int | None = None,
) -> TextContent:
    target = f" UID {uid}" if uid else ""
    if count is not None:
        target = f" {count} email(s)"
    return TextContent(
        type="text",
        text=(
            f"Confirmation required: {reason}{target}. "
            "Repeat the tool call with confirmed=true after explicit user confirmation."
        ),
    )


def _clean_header_value(value) -> str:
    """EmailMessage rejects CR/LF in assigned header values; unfold safely."""
    if value is None:
        return ""
    return re.sub(r"[\r\n]+[ \t]*", " ", str(value)).strip()

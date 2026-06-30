"""
Helpers for Email MCP agent-draft approval storage.

The MCP server keeps thin wrappers for compatibility with existing tests and
monkeypatch hooks; this module owns the storage/schema details.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Callable


def read_agent_email_confirm_setting() -> bool:
    """Return whether agent email sends must be staged for manual approval."""
    try:
        from src.settings import get_setting

        return bool(get_setting("agent_email_confirm", True))
    except Exception:
        return True


def _join_recipients(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return ", ".join(value)


def _join_references(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return " ".join(value)


def stash_agent_draft(
    *,
    to,
    subject,
    body,
    in_reply_to=None,
    references=None,
    cc=None,
    bcc=None,
    account=None,
    current_owner_func: Callable[[], str | None],
) -> dict:
    """Insert a composed email as a pending agent draft."""
    try:
        from src.constants import SCHEDULED_EMAILS_DB
    except Exception:
        return {"success": False, "error": "Pending-email storage unavailable"}

    pending_id = uuid.uuid4().hex[:16]
    far_future = "9999-12-31T00:00:00"
    now = datetime.utcnow().isoformat()
    to_addr = _join_recipients(to) or ""
    cc_addr = _join_recipients(cc)
    bcc_addr = _join_recipients(bcc)
    references_hdr = _join_references(references)

    try:
        conn = sqlite3.connect(SCHEDULED_EMAILS_DB)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_emails (
                id TEXT PRIMARY KEY,
                to_addr TEXT NOT NULL,
                cc TEXT,
                bcc TEXT,
                subject TEXT,
                body TEXT NOT NULL,
                in_reply_to TEXT,
                references_hdr TEXT,
                attachments TEXT,
                send_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                owner TEXT DEFAULT '',
                account_id TEXT,
                odysseus_kind TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO scheduled_emails
            (id, to_addr, cc, bcc, subject, body, in_reply_to, references_hdr,
             attachments, send_at, created_at, status, account_id, odysseus_kind, owner)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'agent_draft', ?, ?, ?)
            """,
            (
                pending_id,
                to_addr,
                cc_addr,
                bcc_addr,
                subject or "",
                body or "",
                in_reply_to or None,
                references_hdr,
                "[]",
                far_future,
                now,
                account or None,
                "agent_draft",
                current_owner_func(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        return {"success": False, "error": f"Failed to stash draft: {e}"}

    return {
        "success": True,
        "pending": True,
        "pending_id": pending_id,
        "to": to_addr,
        "subject": subject or "",
        "body": body or "",
        "message": (
            "Draft staged for your approval - nothing has been sent yet.\n"
            "Review the To/Subject/Body above. Reply 'send' to deliver, or "
            "'cancel' to discard."
        ),
    }

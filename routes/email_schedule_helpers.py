"""Scheduled email and agent-draft data helpers."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def normalize_scheduled_send_at(send_at: str | None) -> str:
    if not send_at:
        raise ValueError("send_at required (ISO8601 UTC)")
    try:
        parsed_at = datetime.fromisoformat(send_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("send_at must be ISO8601") from exc

    now_utc = datetime.now(timezone.utc) if parsed_at.tzinfo else datetime.utcnow()
    # Tiny 30s grace is intentionally not subtracted; the legacy route comment
    # used this wording, but behavior has always rejected already-past times.
    if parsed_at < now_utc:
        raise ValueError("send_at must be in the future")
    if parsed_at.tzinfo:
        parsed_at = parsed_at.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed_at.isoformat()


def schedule_email_row(req: dict, *, owner: str, db_path: str | Path) -> dict:
    send_at = normalize_scheduled_send_at(req.get("send_at"))
    sid = uuid.uuid4().hex[:16]
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO scheduled_emails
            (id, to_addr, cc, bcc, subject, body, in_reply_to, references_hdr, attachments, send_at, created_at, status, account_id, odysseus_kind, owner)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                sid,
                req.get("to", ""),
                req.get("cc") or None,
                req.get("bcc") or None,
                req.get("subject") or "",
                req.get("body") or "",
                req.get("in_reply_to") or None,
                req.get("references") or None,
                json.dumps(req.get("attachments") or []),
                send_at,
                datetime.utcnow().isoformat(),
                req.get("account_id") or None,
                req.get("odysseus_kind") or "scheduled",
                owner or "",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "id": sid, "send_at": send_at}


def list_scheduled_email_rows(*, owner: str, db_path: str | Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, to_addr, cc, subject, send_at, created_at, status, error
            FROM scheduled_emails
            WHERE status IN ('pending', 'failed') AND owner = ?
            ORDER BY send_at ASC
            """,
            (owner or "",),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row[0],
            "to": row[1],
            "cc": row[2],
            "subject": row[3],
            "send_at": row[4],
            "created_at": row[5],
            "status": row[6],
            "error": row[7],
        }
        for row in rows
    ]


def cancel_scheduled_email_row(sid: str, *, owner: str, db_path: str | Path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "DELETE FROM scheduled_emails WHERE id = ? AND status = 'pending' AND owner = ?",
            (sid, owner or ""),
        )
        conn.commit()
    finally:
        conn.close()


def list_pending_agent_draft_rows(*, owner: str, db_path: str | Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, to_addr, subject, body, created_at, account_id
               FROM scheduled_emails
               WHERE status = 'agent_draft' AND owner = ?
               ORDER BY created_at DESC""",
            (owner or "",),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def approve_agent_draft_row(sid: str, *, owner: str, db_path: str | Path) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """UPDATE scheduled_emails
               SET status = 'pending', send_at = ?
               WHERE id = ? AND status = 'agent_draft' AND owner = ?""",
            (datetime.utcnow().isoformat(), sid, owner or ""),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def cancel_agent_draft_row(sid: str, *, owner: str, db_path: str | Path) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """UPDATE scheduled_emails SET status = 'cancelled'
               WHERE id = ? AND status = 'agent_draft' AND owner = ?""",
            (sid, owner or ""),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()

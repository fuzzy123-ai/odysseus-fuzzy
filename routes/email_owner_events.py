"""Owner-scope and event helpers for email routes."""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from routes.email_helpers import _get_email_config

logger = logging.getLogger(__name__)


def email_tag_owner_aliases(account_id: str | None, owner: str = "") -> list[str]:
    aliases = [owner or ""]
    try:
        from core.database import EmailAccount as _EA
        from core.database import SessionLocal as _SL

        db = _SL()
        try:
            resolved_account_id = account_id
            if not resolved_account_id:
                try:
                    cfg = _get_email_config(None, owner=owner)
                    resolved_account_id = cfg.get("account_id") or None
                    aliases.extend([
                        cfg.get("imap_user") or "",
                        cfg.get("smtp_user") or "",
                        cfg.get("from_address") or "",
                    ])
                except Exception as exc:
                    logger.warning("Failed to resolve email account alias", exc_info=exc)
                    resolved_account_id = None
            row = db.get(_EA, resolved_account_id) if resolved_account_id else None
            if row:
                aliases.extend([row.owner or "", row.imap_user or "", row.from_address or ""])
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Failed to load email aliases", exc_info=exc)

    out = []
    for alias in aliases:
        alias = (alias or "").strip()
        if alias not in out:
            out.append(alias)
    return out or [""]


def email_tag_owner_clause_from_aliases(aliases: list[str], owner: str = "") -> tuple[str, list[str]]:
    aliases = aliases or [""]
    placeholders = ",".join("?" * len(aliases))
    # In configured multi-user mode, do not treat legacy owner='' rows as
    # visible to everyone. Single-user/unconfigured mode keeps legacy rows.
    if owner:
        return f"owner IN ({placeholders})", aliases
    return f"(owner IN ({placeholders}) OR owner IS NULL)", aliases


def record_email_received_events(
    owner: str,
    account_id: str | None,
    folder: str,
    emails: list[dict],
    *,
    db_path: str | Path,
):
    """Baseline inbox messages, then fire `email_received` for new arrivals."""
    if not owner or (folder or "INBOX").upper() != "INBOX" or not emails:
        return
    try:
        from src.event_bus import fire_event

        account_key = (account_id or "default").strip() or "default"
        now = datetime.utcnow().isoformat() + "Z"
        keys = []
        for email in emails:
            key = (email.get("message_id") or email.get("uid") or "").strip()
            if key and key not in keys:
                keys.append(key)
        if not keys:
            return

        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS email_event_seen ("
                "owner TEXT NOT NULL, account_key TEXT NOT NULL, folder TEXT NOT NULL, "
                "message_key TEXT NOT NULL, first_seen_at TEXT NOT NULL, "
                "PRIMARY KEY (owner, account_key, folder, message_key))"
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM email_event_seen WHERE owner=? AND account_key=? AND folder=?",
                (owner, account_key, folder),
            ).fetchone()[0]
            existing = set()
            if count:
                placeholders = ",".join("?" * len(keys))
                rows = conn.execute(
                    f"SELECT message_key FROM email_event_seen "
                    f"WHERE owner=? AND account_key=? AND folder=? AND message_key IN ({placeholders})",
                    (owner, account_key, folder, *keys),
                ).fetchall()
                existing = {row[0] for row in rows}
            new_keys = [key for key in keys if key not in existing]
            conn.executemany(
                "INSERT OR IGNORE INTO email_event_seen "
                "(owner, account_key, folder, message_key, first_seen_at) VALUES (?, ?, ?, ?, ?)",
                [(owner, account_key, folder, key, now) for key in keys],
            )
            conn.commit()
        finally:
            conn.close()

        if count and new_keys:
            for _ in new_keys[:50]:
                fire_event("email_received", owner)
            logger.info("Fired email_received for %d new message(s)", min(len(new_keys), 50))
    except Exception:
        logger.debug("email_received event detection skipped", exc_info=True)

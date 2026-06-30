"""Email cache and sorting helpers for the email MCP server."""

from __future__ import annotations

import email.utils
import os
import sqlite3
from datetime import datetime
from typing import Callable


def load_cached_summaries(load_config: Callable[[], dict]) -> dict:
    """Read pre-computed AI summaries from the configured SQLite cache."""
    cfg = load_config()
    db_path = cfg["cache_db"]
    if not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT subject, sender, summary, suggested_reply FROM email_ai"
        ).fetchall()
        conn.close()
        result = {}
        for subj, sender, summary, reply in rows:
            result[subj] = {"sender": sender, "summary": summary, "reply": reply}
        return result
    except Exception:
        return {}


def _result_sort_time(result: dict) -> datetime:
    try:
        parsed = email.utils.parsedate_to_datetime(result.get("date") or "")
        if parsed:
            if parsed.tzinfo:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
    except Exception:
        pass
    return datetime.min

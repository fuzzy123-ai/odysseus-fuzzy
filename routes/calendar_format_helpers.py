"""Pure calendar formatting helpers."""

import re
from datetime import date, datetime


def _ics_naive_dtstart(dt):
    """Naive value matching how import_ics stores CalendarEvent.dtstart."""
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            from datetime import timezone as _tz
            return dt.astimezone(_tz.utc).replace(tzinfo=None)
        return dt
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day)
    return dt


def _ics_escape(text: str) -> str:
    """Escape a value for an iCalendar TEXT field."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _safe_ics_filename(name: str) -> str:
    """Return a conservative .ics filename safe for Content-Disposition."""
    stem = name if isinstance(name, str) else ""
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._-")
    if not stem:
        stem = "calendar"
    return f"{stem[:128]}.ics"


def _resolve_base_uid(uid: str) -> str:
    """Extract the base series UID from a compound occurrence UID."""
    if not uid:
        raise ValueError("empty uid")
    idx = uid.find("::")
    if idx == -1:
        return uid
    base = uid[:idx]
    if not base:
        raise ValueError("malformed compound UID: missing base before ::")
    return base

"""IMAP folder discovery and resolution helpers for the email MCP server."""

from __future__ import annotations

import re


def _folder_name_from_list_line(line) -> str | None:
    decoded = line.decode() if isinstance(line, bytes) else str(line)
    m = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
    if not m:
        return None
    return m.group(1) or m.group(2)


def _list_folder_lines(conn) -> list:
    try:
        status, folders = conn.list()
        if status != "OK" or not folders:
            return []
        return folders
    except Exception:
        return []


def _detect_sent_folder(conn):
    """Find the account's Sent folder name; fall back to 'Sent'."""
    candidates = ("Sent", "[Gmail]/Sent Mail", "Sent Mail", "Sent Items", "INBOX.Sent")
    folders = _list_folder_lines(conn)
    names = [name for name in (_folder_name_from_list_line(f) for f in folders) if name]

    for f in folders:
        decoded = f.decode() if isinstance(f, bytes) else str(f)
        if r"\Sent" in decoded:
            name = _folder_name_from_list_line(f)
            if name:
                return name

    for candidate in candidates:
        if candidate in names:
            return candidate
    return "Sent"


def _resolve_folder(conn, preferred: str, role: str) -> str:
    """Resolve provider-specific folder names like Gmail's [Gmail]/Trash."""
    folders = _list_folder_lines(conn)
    names = [name for name in (_folder_name_from_list_line(f) for f in folders) if name]
    if preferred and preferred in names:
        return preferred

    role_flags = {
        "trash": ("\\Trash",),
        "archive": ("\\Archive", "\\All"),
        "junk": ("\\Junk",),
    }.get(role, ())
    for f in folders:
        decoded = f.decode() if isinstance(f, bytes) else str(f)
        if any(flag in decoded for flag in role_flags):
            name = _folder_name_from_list_line(f)
            if name:
                return name

    candidates = {
        "trash": ("Trash", "[Gmail]/Trash", "[Google Mail]/Trash", "Bin", "Deleted Messages", "Deleted Items"),
        "archive": ("Archive", "Archives", "[Gmail]/All Mail", "[Google Mail]/All Mail"),
        "junk": ("Junk", "Spam", "[Gmail]/Spam", "[Google Mail]/Spam"),
    }.get(role, ())
    lower_map = {n.lower(): n for n in names}
    for candidate in candidates:
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]
    return preferred


def _folder_role_from_name(name: str) -> str:
    lower = (name or "").lower()
    if "trash" in lower or "bin" in lower or "deleted" in lower:
        return "trash"
    if "junk" in lower or "spam" in lower:
        return "junk"
    if "archive" in lower or "all mail" in lower:
        return "archive"
    return ""

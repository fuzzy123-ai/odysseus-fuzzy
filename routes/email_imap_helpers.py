"""IMAP folder, UID and message-move helpers for email routes."""

import re

from routes.email_helpers import _q


def folder_name_from_list_line(line) -> str | None:
    decoded = line.decode() if isinstance(line, bytes) else str(line)
    match = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
    if not match:
        return None
    return match.group(1) or match.group(2)


def list_imap_folders(conn) -> tuple[list, list[str]]:
    try:
        status, folders = conn.list()
        if status != "OK" or not folders:
            return [], []
        names = [name for name in (folder_name_from_list_line(f) for f in folders) if name]
        return folders, names
    except Exception:
        return [], []


def resolve_mail_folder(conn, preferred: str, role: str = "") -> str:
    """Resolve provider-specific names such as Gmail's [Gmail]/Bin/Spam."""
    folders, names = list_imap_folders(conn)
    if preferred and preferred in names:
        return preferred
    role_flags = {
        "trash": ("\\Trash",),
        "archive": ("\\Archive", "\\All"),
        "junk": ("\\Junk",),
    }.get(role, ())
    for folder in folders:
        decoded = folder.decode() if isinstance(folder, bytes) else str(folder)
        if any(flag in decoded for flag in role_flags):
            name = folder_name_from_list_line(folder)
            if name:
                return name
    candidates = {
        "trash": (
            "Trash",
            "[Gmail]/Trash",
            "[Google Mail]/Trash",
            "Bin",
            "[Gmail]/Bin",
            "Deleted Messages",
            "Deleted Items",
        ),
        "archive": ("Archive", "Archives", "[Gmail]/All Mail", "[Google Mail]/All Mail", "All Mail"),
        "junk": ("Junk", "Spam", "[Gmail]/Spam", "[Google Mail]/Spam"),
    }.get(role, ())
    lower_map = {name.lower(): name for name in names}
    for candidate in candidates:
        found = lower_map.get(candidate.lower())
        if found:
            return found
    return preferred


def folder_role_from_name(name: str) -> str:
    lower = (name or "").lower()
    if "trash" in lower or "bin" in lower or "deleted" in lower:
        return "trash"
    if "spam" in lower or "junk" in lower:
        return "junk"
    if "archive" in lower or "all mail" in lower:
        return "archive"
    return ""


def uid_bytes(uid: str | bytes) -> bytes:
    return uid if isinstance(uid, bytes) else str(uid).encode()


def uid_exists(conn, uid: str) -> bool:
    try:
        status, data = conn.uid("FETCH", uid_bytes(uid), "(UID)")
        if status != "OK":
            return False
        for part in data or []:
            meta = part[0] if isinstance(part, tuple) else part
            meta_b = meta if isinstance(meta, bytes) else str(meta).encode()
            if re.search(rb"\bUID\s+\d+\b", meta_b):
                return True
        return False
    except Exception:
        return False


def imap_uid_search(conn, criteria: str):
    return conn.uid("SEARCH", None, criteria)


def imap_uid_fetch(conn, uid_set: str | bytes, query: str):
    return conn.uid("FETCH", uid_bytes(uid_set), query)


def uid_from_fetch_meta(meta_b: bytes) -> str:
    match = re.search(rb"\bUID\s+(\d+)\b", meta_b)
    return match.group(1).decode() if match else ""


_FETCH_SEQ_RE = re.compile(rb"^(\d+)\s+\(")


def group_uid_fetch_records(msg_data) -> list:
    """Group an imaplib UID FETCH response into per-message records.

    Gmail can send FLAGS after the literal payload as a bare bytes element;
    Dovecot often sends FLAGS before the literal. Fold both shapes into the
    current record so flags, UID and size stay attached to the right payload.
    """
    grouped: list = []
    for part in msg_data or []:
        if isinstance(part, tuple):
            meta_b = part[0] if isinstance(part[0], (bytes, bytearray)) else str(part[0]).encode()
            if _FETCH_SEQ_RE.match(meta_b):
                grouped.append((meta_b, part[1]))
            elif grouped:
                cur_meta, cur_payload = grouped[-1]
                grouped[-1] = (cur_meta + b" " + meta_b, cur_payload or part[1])
        elif isinstance(part, (bytes, bytearray)) and grouped:
            cur_meta, cur_payload = grouped[-1]
            grouped[-1] = (cur_meta + b" " + bytes(part), cur_payload)
    return grouped


def store_email_flag(conn, uid: str, flag: str, add: bool = True) -> bool:
    op = "+FLAGS" if add else "-FLAGS"
    if uid_exists(conn, uid):
        status, _ = conn.uid("STORE", uid_bytes(uid), op, flag)
    else:
        status, _ = conn.store(uid_bytes(uid), op, flag)
    return status == "OK"


def move_email_message(conn, uid: str, dest: str, role: str = "") -> bool:
    dest = resolve_mail_folder(conn, dest, role or folder_role_from_name(dest))
    if uid_exists(conn, uid):
        status, _ = conn.uid("MOVE", uid_bytes(uid), _q(dest))
        if status == "OK":
            return True
        status, _ = conn.uid("COPY", uid_bytes(uid), _q(dest))
        if status != "OK":
            return False
        status, _ = conn.uid("STORE", uid_bytes(uid), "+FLAGS", "\\Deleted")
    else:
        status, _ = conn.copy(uid_bytes(uid), _q(dest))
        if status != "OK":
            return False
        status, _ = conn.store(uid_bytes(uid), "+FLAGS", "\\Deleted")
    if status == "OK":
        conn.expunge()
        return True
    return False

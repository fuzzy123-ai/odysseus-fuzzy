"""Email MCP IMAP flag, move, delete/archive and UID-search helpers."""

from __future__ import annotations


def set_flag(uid, folder, flag, *, add=True, account=None, imap_connect_func, quote_folder_func, bytes_func) -> bool:
    conn = imap_connect_func(account)
    conn.select(quote_folder_func(folder))
    op = "+FLAGS" if add else "-FLAGS"
    try:
        status, data = conn.uid("STORE", bytes_func(uid), op, flag)
        if add and flag == "\\Deleted":
            conn.expunge()
        return status == "OK" and bool(data and data[0])
    except Exception:
        return False
    finally:
        conn.logout()


def bulk_set_flag(uids, folder, flag, *, add=True, account=None, imap_connect_func, quote_folder_func, bytes_func, uid_fetch_rows_func) -> int:
    if not uids:
        return 0
    conn = imap_connect_func(account)
    touched = []
    try:
        conn.select(quote_folder_func(folder))
        op = "+FLAGS" if add else "-FLAGS"
        msg_set = ",".join(str(u) for u in uids)
        try:
            status, data = conn.uid("FETCH", bytes_func(msg_set), "(UID)")
        except Exception:
            return 0
        touched = uid_fetch_rows_func(data)
        if status != "OK" or not touched:
            return 0
        status, _data = conn.uid("STORE", bytes_func(msg_set), op, flag)
        if add and flag == "\\Deleted":
            conn.expunge()
        if status != "OK":
            return 0
    finally:
        conn.logout()
    return len(touched)


def bulk_move(uids, source_folder, dest_folder, *, account=None, role="", imap_connect_func, quote_folder_func, bytes_func, resolve_folder_func, folder_role_from_name_func, uid_fetch_rows_func) -> int:
    if not uids:
        return 0
    conn = imap_connect_func(account)
    moved = 0
    try:
        conn.select(quote_folder_func(source_folder))
        dest_folder = resolve_folder_func(conn, dest_folder, role or folder_role_from_name_func(dest_folder))
        msg_set = ",".join(str(u) for u in uids)
        try:
            status, data = conn.uid("FETCH", bytes_func(msg_set), "(UID)")
        except Exception:
            return 0
        existing = uid_fetch_rows_func(data)
        if not existing:
            return 0
        moved = len(existing)
        dest_arg = quote_folder_func(dest_folder)
        status, _ = conn.uid("MOVE", bytes_func(msg_set), dest_arg)
        if status != "OK":
            status, _ = conn.uid("COPY", bytes_func(msg_set), dest_arg)
            if status != "OK":
                return 0
            status, _ = conn.uid("STORE", bytes_func(msg_set), "+FLAGS", "\\Deleted")
            if status != "OK":
                return 0
            conn.expunge()
    finally:
        conn.logout()
    return moved


def search_uids(*, folder="INBOX", criteria="UNSEEN", account=None, imap_connect_func, quote_folder_func) -> list:
    conn = imap_connect_func(account)
    try:
        conn.select(quote_folder_func(folder), readonly=True)
        status, data = conn.uid("SEARCH", None, criteria)
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()
    finally:
        conn.logout()


def move_message(uid, source_folder, dest_folder, *, account=None, role="", imap_connect_func, quote_folder_func, bytes_func, resolve_folder_func, folder_role_from_name_func, uid_fetch_rows_func) -> bool:
    conn = imap_connect_func(account)
    conn.select(quote_folder_func(source_folder))
    try:
        dest_folder = resolve_folder_func(conn, dest_folder, role or folder_role_from_name_func(dest_folder))
        try:
            status, data = conn.uid("FETCH", bytes_func(uid), "(UID)")
        except Exception:
            return False
        existing = uid_fetch_rows_func(data)
        if status != "OK" or not existing:
            return False
        dest_arg = quote_folder_func(dest_folder)
        status, _ = conn.uid("MOVE", bytes_func(uid), dest_arg)
        if status == "OK":
            return True
        status, _ = conn.uid("COPY", bytes_func(uid), dest_arg)
        if status != "OK":
            return False
        status, _ = conn.uid("STORE", bytes_func(uid), "+FLAGS", "\\Deleted")
        if status != "OK":
            return False
        conn.expunge()
        return True
    finally:
        conn.logout()


def delete_email(uid, *, folder="INBOX", permanent=False, account=None, load_config_func, set_flag_func, move_message_func) -> bool:
    cfg = load_config_func(account)
    if permanent:
        return set_flag_func(uid, folder, "\\Deleted", add=True, account=account)
    return move_message_func(uid, folder, cfg["trash_folder"], account=account, role="trash")


def archive_email(uid, *, folder="INBOX", account=None, load_config_func, move_message_func) -> bool:
    cfg = load_config_func(account)
    return move_message_func(uid, folder, cfg["archive_folder"], account=account, role="archive")

"""Read/list/search helpers for the Email MCP server."""

from __future__ import annotations

import email
import email.utils


def list_emails(
    *,
    folder: str = "INBOX",
    max_results: int = 20,
    unresponded_only: bool = False,
    unread_only: bool = False,
    account: str | None = None,
    imap_connect,
    quote_folder,
    decode_header,
    get_cached_summaries,
) -> list[dict]:
    """List emails newest-first using injected server dependencies."""
    conn = None
    try:
        conn = imap_connect(account)
        select_status, _ = conn.select(quote_folder(folder), readonly=True)
        if select_status != "OK":
            raise ValueError(f"IMAP folder not found: {folder}")

        if unread_only and unresponded_only:
            status, data = conn.uid("SEARCH", None, "(UNSEEN UNANSWERED)")
        elif unread_only:
            status, data = conn.uid("SEARCH", None, "(UNSEEN)")
        elif unresponded_only:
            status, data = conn.uid("SEARCH", None, "(UNANSWERED)")
        else:
            status, data = conn.uid("SEARCH", None, "ALL")

        if status != "OK" or not data[0]:
            return []

        uid_list = list(reversed(data[0].split()))[:max_results]
        cache = get_cached_summaries()
        results = []

        for uid in uid_list:
            try:
                status, msg_data = conn.uid("FETCH", uid, "(RFC822.HEADER)")
                if status != "OK":
                    continue
                raw_header = msg_data[0][1]
                msg = email.message_from_bytes(raw_header)

                subject = decode_header(msg.get("Subject", "(no subject)"))
                sender = decode_header(msg.get("From", "unknown"))
                date_str = msg.get("Date", "")
                message_id = msg.get("Message-ID", "")

                sender_name, sender_addr = email.utils.parseaddr(sender)
                sender_display = sender_name or sender_addr

                cached = cache.get(subject, {})
                summary = cached.get("summary", "")

                results.append(
                    {
                        "uid": uid.decode(),
                        "message_id": message_id,
                        "subject": subject,
                        "from": sender_display,
                        "from_address": sender_addr,
                        "date": date_str,
                        "summary": summary,
                    }
                )
            except Exception:
                continue

        return results
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass


def list_emails_across_accounts(
    *,
    folder: str = "INBOX",
    max_results: int = 20,
    unresponded_only: bool = False,
    unread_only: bool = False,
    list_accounts_raw,
    list_emails_func,
    result_sort_time,
) -> tuple[list[dict], list[str]]:
    rows = list_accounts_raw()
    combined = []
    errors = []
    for row in rows:
        account_selector = row.get("id") or row.get("name") or row.get("imap_user")
        account_name = row.get("name") or row.get("imap_user") or row.get("id") or "unknown"
        account_email = row.get("imap_user") or row.get("from_address") or ""
        try:
            account_results = list_emails_func(
                folder=folder,
                max_results=max_results,
                unresponded_only=unresponded_only,
                unread_only=unread_only,
                account=account_selector,
            )
            for item in account_results:
                item["_account"] = account_name
                item["_account_email"] = account_email
                item["_account_id"] = row.get("id")
            combined.extend(account_results)
        except Exception as exc:
            errors.append(f"{account_name} ({account_email}): {exc}")
    combined.sort(key=result_sort_time, reverse=True)
    return combined[:max_results], errors


def search_emails(
    *,
    query,
    folders=None,
    max_results: int = 20,
    account: str | None = None,
    imap_connect,
    quote_folder,
    decode_header,
    get_cached_summaries,
) -> list[dict]:
    if not query or not str(query).strip():
        return []
    q = str(query).replace("\\", "\\\\").replace('"', '\\"')
    search_cmd = f'(OR OR FROM "{q}" SUBJECT "{q}" TEXT "{q}")'
    if folders is None:
        folders = ["INBOX", "Sent", "Archive"]
    cache = get_cached_summaries()
    out = []
    conn = imap_connect(account)
    try:
        for folder in folders:
            try:
                status, _ = conn.select(quote_folder(folder), readonly=True)
                if status != "OK":
                    continue
                status, data = conn.uid("SEARCH", None, search_cmd)
                if status != "OK" or not data or not data[0]:
                    continue
                uid_list = list(reversed(data[0].split()))[:max_results]
                for uid in uid_list:
                    try:
                        status, msg_data = conn.uid("FETCH", uid, "(RFC822.HEADER)")
                        if status != "OK":
                            continue
                        raw_header = msg_data[0][1]
                        msg = email.message_from_bytes(raw_header)
                        subject = decode_header(msg.get("Subject", "(no subject)"))
                        sender = decode_header(msg.get("From", "unknown"))
                        date_str = msg.get("Date", "")
                        message_id = msg.get("Message-ID", "")
                        to_str = decode_header(msg.get("To", ""))
                        cc_str = decode_header(msg.get("Cc", ""))
                        sender_name, sender_addr = email.utils.parseaddr(sender)
                        sender_display = sender_name or sender_addr
                        cached = cache.get(subject, {})
                        out.append(
                            {
                                "uid": uid.decode(),
                                "message_id": message_id,
                                "subject": subject,
                                "from": sender_display,
                                "from_address": sender_addr,
                                "to": to_str,
                                "cc": cc_str,
                                "date": date_str,
                                "_folder": folder,
                                "summary": cached.get("summary", ""),
                            }
                        )
                    except Exception:
                        continue
            except Exception:
                continue
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return out[: max_results * len(folders)]


def read_email(
    *,
    uid=None,
    message_id=None,
    folder: str = "INBOX",
    account: str | None = None,
    load_config,
    imap_connect,
    quote_folder,
    bytes_func,
    decode_header,
    extract_text,
    list_attachments_from_msg,
) -> dict:
    """Read full email content by UID or message-id."""
    cfg = load_config(account)
    conn = None
    try:
        conn = imap_connect(account)
        conn.select(quote_folder(folder), readonly=True)

        if message_id and not uid:
            status, data = conn.uid("SEARCH", None, f'(HEADER Message-ID "{message_id}")')
            if status != "OK" or not data[0]:
                return {"error": f"Email not found with Message-ID: {message_id}"}
            uid = data[0].split()[-1]

        if not uid:
            return {"error": "No UID or Message-ID provided"}

        status, msg_data = conn.uid("FETCH", bytes_func(uid), "(BODY.PEEK[])")
        if status != "OK":
            return {"error": f"Failed to fetch email UID {uid}"}
        if not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple) or len(msg_data[0]) < 2:
            return {"error": f"Email not found with UID {uid}"}

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = decode_header(msg.get("Subject", "(no subject)"))
        sender = decode_header(msg.get("From", "unknown"))
        date_str = msg.get("Date", "")
        message_id_header = msg.get("Message-ID", "")
        body = extract_text(msg)
        attachments = list_attachments_from_msg(msg)

        sender_name, sender_addr = email.utils.parseaddr(sender)

        return {
            "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
            "account": cfg.get("account_name") or cfg.get("imap_user") or "default",
            "account_email": cfg.get("imap_user") or cfg.get("from_address") or "",
            "account_id": cfg.get("account_id"),
            "message_id": message_id_header,
            "subject": subject,
            "from": sender_name or sender_addr,
            "from_address": sender_addr,
            "date": date_str,
            "body": body[:8000],
            "attachments": attachments,
        }
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass


def read_email_across_accounts(
    *,
    uid=None,
    message_id=None,
    folder: str = "INBOX",
    list_accounts_raw,
    read_email_func,
) -> dict:
    rows = list_accounts_raw()
    matches = []
    errors = []
    for row in rows:
        account_selector = row.get("id") or row.get("name") or row.get("imap_user")
        account_name = row.get("name") or row.get("imap_user") or row.get("id") or "unknown"
        account_email = row.get("imap_user") or row.get("from_address") or ""
        result = read_email_func(
            uid=uid,
            message_id=message_id,
            folder=folder,
            account=account_selector,
        )
        if "error" in result:
            errors.append(f"{account_name} <{account_email}>: {result['error']}")
            continue
        matches.append(result)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        accounts = ", ".join(
            f"{m.get('account')} <{m.get('account_email')}>" for m in matches
        )
        return {
            "error": (
                f"UID {uid or message_id} exists in multiple accounts: {accounts}. "
                "Call read_email again with the account name/email."
            )
        }
    return {"error": f"Email not found in any configured account. Checked: {'; '.join(errors)}"}

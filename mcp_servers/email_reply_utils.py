"""Email MCP reply orchestration helpers."""

from __future__ import annotations

import email
import email.utils


def reply_to_email(
    uid,
    body,
    *,
    folder="INBOX",
    reply_all=False,
    account=None,
    imap_connect_func,
    quote_folder_func,
    bytes_func,
    decode_header_func,
    send_email_func,
) -> dict:
    """Reply to an existing email by UID."""
    conn = None
    try:
        conn = imap_connect_func(account)
        conn.select(quote_folder_func(folder), readonly=True)
        status, msg_data = conn.uid("FETCH", bytes_func(uid), "(BODY.PEEK[])")
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass
    if status != "OK" or not msg_data or not msg_data[0]:
        return {"error": f"Failed to fetch email UID {uid}"}
    raw = msg_data[0][1]
    orig = email.message_from_bytes(raw)

    orig_subject = decode_header_func(orig.get("Subject", ""))
    reply_subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
    orig_message_id = orig.get("Message-ID", "")
    orig_references = orig.get("References", "")
    new_references = (orig_references + " " + orig_message_id).strip() if orig_references else orig_message_id

    sender = decode_header_func(orig.get("From", ""))
    _, sender_addr = email.utils.parseaddr(sender)
    to_addrs = sender_addr

    cc = None
    if reply_all:
        cc_addrs = []
        for header_name in ("To", "Cc"):
            for _, addr in email.utils.getaddresses([orig.get(header_name, "")]):
                if addr and addr != sender_addr:
                    cc_addrs.append(addr)
        if cc_addrs:
            cc = ", ".join(cc_addrs)

    return send_email_func(
        to=to_addrs,
        subject=reply_subject,
        body=body,
        in_reply_to=orig_message_id,
        references=new_references,
        cc=cc,
        account=account,
    )

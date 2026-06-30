"""
Email MCP send orchestration helpers.

This module owns MIME assembly, SMTP delivery and best-effort Sent-folder copy.
The MCP server keeps a wrapper so existing monkeypatch tests can still patch
the private dependency functions.
"""

from __future__ import annotations

import email
import re
from email.message import EmailMessage


def _recipient_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [addr.strip() for addr in value.split(",") if addr.strip()]
    return list(value)


def send_email(
    *,
    to,
    subject,
    body,
    in_reply_to=None,
    references=None,
    cc=None,
    bcc=None,
    account=None,
    read_confirm_setting_func,
    stash_draft_func,
    resolve_send_config_func,
    smtp_connect_func,
    clean_header_value_func,
    imap_connect_func,
    detect_sent_folder_func,
    quote_folder_func,
) -> dict:
    """Send email or stage an agent draft when confirmation is enabled."""
    if read_confirm_setting_func():
        return stash_draft_func(
            to=to,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            references=references,
            cc=cc,
            bcc=bcc,
            account=account,
        )

    send_account, cfg = resolve_send_config_func(account)
    msg = EmailMessage()
    msg["From"] = clean_header_value_func(cfg["from_address"])
    msg["To"] = clean_header_value_func(to if isinstance(to, str) else ", ".join(to))
    msg["Subject"] = clean_header_value_func(subject)
    if cc:
        msg["Cc"] = clean_header_value_func(cc if isinstance(cc, str) else ", ".join(cc))
    if in_reply_to:
        msg["In-Reply-To"] = clean_header_value_func(in_reply_to)
    if references:
        refs = references if isinstance(references, str) else " ".join(references)
        msg["References"] = clean_header_value_func(refs)
    if "Date" not in msg:
        msg["Date"] = email.utils.formatdate(localtime=True)
    if "Message-ID" not in msg:
        msg["Message-ID"] = email.utils.make_msgid()
    msg.set_content(body)

    recipients = _recipient_list(to) + _recipient_list(cc) + _recipient_list(bcc)

    conn = smtp_connect_func(send_account, cfg=cfg)
    try:
        conn.send_message(msg, from_addr=cfg["from_address"], to_addrs=recipients)
    finally:
        conn.quit()

    sent_folder = None
    sent_uid = None
    try:
        imap = imap_connect_func(send_account)
        try:
            sent_folder = detect_sent_folder_func(imap)
            append_st, append_data = imap.append(
                quote_folder_func(sent_folder),
                "\\Seen",
                None,
                msg.as_bytes(),
            )
            if append_st == "OK" and append_data:
                m = re.search(rb"APPENDUID\s+\d+\s+(\d+)", append_data[0] or b"")
                if m:
                    sent_uid = m.group(1).decode("ascii", errors="ignore")
        finally:
            imap.logout()
    except Exception:
        # SMTP delivery already succeeded; failing to copy to Sent is non-fatal.
        pass

    return {
        "sent": True,
        "to": recipients,
        "subject": subject,
        "account": cfg.get("account_name"),
        "account_id": cfg.get("account_id"),
        "sent_folder": sent_folder,
        "sent_uid": sent_uid,
        "message_id": msg.get("Message-ID", ""),
    }

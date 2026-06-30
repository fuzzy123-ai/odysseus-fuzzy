"""Email message shape helpers for list/search/read route responses."""

from __future__ import annotations

import email.utils
import re
from collections.abc import Callable
from datetime import timezone
from email.message import Message
from typing import Any


DecodeHeader = Callable[[str], str]


def parse_email_datetime(date_str: str, *, normalize_naive_utc: bool = True):
    parsed_date = email.utils.parsedate_to_datetime(date_str) if date_str else None
    if parsed_date and normalize_naive_utc and parsed_date.tzinfo is None:
        parsed_date = parsed_date.replace(tzinfo=timezone.utc)
    return parsed_date


def fetch_flags_from_meta(meta: bytes | str) -> str:
    meta_text = meta.decode(errors="replace") if isinstance(meta, bytes) else str(meta or "")
    flag_m = re.search(r"FLAGS \(([^)]*)\)", meta_text)
    return flag_m.group(1) if flag_m else ""


def fetch_size_from_meta(meta: bytes | str) -> int:
    meta_text = meta.decode(errors="replace") if isinstance(meta, bytes) else str(meta or "")
    size_m = re.search(r"RFC822\.SIZE (\d+)", meta_text)
    return int(size_m.group(1)) if size_m else 0


def message_has_attachments(msg: Message) -> bool:
    content_type = (msg.get("Content-Type", "") or "").lower()
    return "multipart/mixed" in content_type or "multipart/related" in content_type


def header_summary_from_message(msg: Message, *, decode_header: DecodeHeader) -> dict[str, Any]:
    subject = decode_header(msg.get("Subject", "(no subject)"))
    sender = decode_header(msg.get("From", "unknown"))
    sender_name, sender_addr = email.utils.parseaddr(sender)
    return {
        "subject": subject,
        "sender": sender,
        "from_name": sender_name or sender_addr,
        "from_address": sender_addr,
        "to": decode_header(msg.get("To", "")),
        "cc": decode_header(msg.get("Cc", "")),
        "date_display": msg.get("Date", ""),
        "message_id": (msg.get("Message-ID", "") or "").strip(),
    }


def list_email_row_from_header(
    uid: str,
    msg: Message,
    *,
    flags: str = "",
    size: int | None = None,
    folder: str | None = None,
    tag_entry: dict[str, Any] | None = None,
    decode_header: DecodeHeader,
) -> dict[str, Any]:
    summary = header_summary_from_message(msg, decode_header=decode_header)
    parsed_date = parse_email_datetime(summary["date_display"])
    row = {
        "uid": uid,
        "message_id": summary["message_id"],
        "subject": summary["subject"],
        "from_name": summary["from_name"],
        "from_address": summary["from_address"],
        "to": summary["to"],
        "cc": summary["cc"],
        "date": parsed_date.isoformat() if parsed_date else "",
        "date_display": summary["date_display"],
        "date_epoch": parsed_date.timestamp() if parsed_date else 0.0,
        "is_read": "\\Seen" in flags,
        "is_answered": "\\Answered" in flags,
        "is_flagged": "\\Flagged" in flags,
        "flags": flags,
        "has_attachments": message_has_attachments(msg),
    }
    if size is not None:
        row["size"] = size
    if folder is not None:
        row["folder"] = folder
    if tag_entry is not None:
        row["tags"] = tag_entry.get("tags", [])
        row["is_spam_verdict"] = tag_entry.get("spam", False)
    return row


def read_email_response_base(
    uid: str,
    folder: str,
    msg: Message,
    *,
    body: str,
    body_html: str,
    attachments: list[dict[str, Any]],
    decode_header: DecodeHeader,
) -> dict[str, Any]:
    summary = header_summary_from_message(msg, decode_header=decode_header)
    parsed_date = parse_email_datetime(summary["date_display"], normalize_naive_utc=False)
    return {
        "uid": uid,
        "folder": folder,
        "message_id": summary["message_id"],
        "subject": summary["subject"],
        "from_name": summary["from_name"],
        "from_address": summary["from_address"],
        "to": summary["to"],
        "cc": summary["cc"],
        "date": parsed_date.isoformat() if parsed_date else "",
        "in_reply_to": (msg.get("In-Reply-To", "") or "").strip(),
        "references": (msg.get("References", "") or "").strip(),
        "body": body,
        "body_html": body_html,
        "attachments": attachments,
    }

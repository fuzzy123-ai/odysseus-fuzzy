"""Attachment metadata and extraction helpers for the email MCP server."""

from __future__ import annotations

import os
import re
import email

from mcp_servers.email_message_utils import _decode_header


def _list_attachments_from_msg(msg):
    """Return attachment metadata."""
    if not msg.is_multipart():
        return []
    attachments = []
    idx = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        cd = str(part.get("Content-Disposition", ""))
        ct = part.get_content_type()
        if ct in ("text/plain", "text/html") and "attachment" not in cd:
            continue
        filename = part.get_filename()
        if filename:
            filename = _decode_header(filename)
        else:
            filename = f"attachment_{idx}"
        payload = part.get_payload(decode=True)
        size = len(payload) if payload else 0
        attachments.append({
            "index": idx,
            "filename": filename,
            "content_type": ct,
            "size": size,
        })
        idx += 1
    return attachments


def _extract_attachment_to_disk(msg, index, target_dir):
    """Extract a specific attachment to disk."""
    if not msg.is_multipart():
        return None
    idx = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        cd = str(part.get("Content-Disposition", ""))
        ct = part.get_content_type()
        if ct in ("text/plain", "text/html") and "attachment" not in cd:
            continue
        if idx == index:
            filename = part.get_filename()
            if filename:
                filename = _decode_header(filename)
            else:
                filename = f"attachment_{idx}"
            safe_name = re.sub(r"[^\w\s\-.]", "_", filename).strip()
            payload = part.get_payload(decode=True)
            if not payload:
                return None
            os.makedirs(target_dir, exist_ok=True)
            filepath = os.path.join(target_dir, safe_name)
            with open(filepath, "wb") as f:
                f.write(payload)
            return filepath
        idx += 1
    return None


def download_attachment_to_disk(
    uid,
    index,
    *,
    folder="INBOX",
    account=None,
    imap_connect_func,
    quote_folder_func,
    bytes_func,
    attachments_dir,
) -> dict:
    """Fetch an email and extract one attachment to disk."""
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
    if status != "OK":
        return {"error": f"Failed to fetch email UID {uid}"}
    raw = msg_data[0][1]
    msg = email.message_from_bytes(raw)

    target_dir = os.path.join(str(attachments_dir), f"{folder}_{uid}")
    filepath = _extract_attachment_to_disk(msg, index, target_dir)
    if not filepath:
        return {"error": f"Attachment index {index} not found"}
    size = os.path.getsize(filepath)
    return {"path": filepath, "filename": os.path.basename(filepath), "size": size}

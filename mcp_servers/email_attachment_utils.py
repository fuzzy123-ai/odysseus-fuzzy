"""Attachment metadata and extraction helpers for the email MCP server."""

from __future__ import annotations

import os
import re

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

"""
Helpers for Email MCP draft-document creation.

These helpers create Odysseus document drafts for review. They do not send
email or perform live IMAP/SMTP work.
"""

from __future__ import annotations

import uuid
from typing import Callable


def build_email_document_content(
    to,
    subject,
    body,
    *,
    cc=None,
    bcc=None,
    in_reply_to=None,
    references=None,
    source_uid=None,
    source_folder=None,
) -> str:
    header_lines = [f"To: {to or ''}"]
    if cc:
        header_lines.append(f"Cc: {cc}")
    if bcc:
        header_lines.append(f"Bcc: {bcc}")
    header_lines.append(f"Subject: {subject or ''}")
    if in_reply_to:
        header_lines.append(f"In-Reply-To: {in_reply_to}")
    if references:
        header_lines.append(f"References: {references}")
    if source_uid:
        header_lines.append(f"X-Source-UID: {source_uid}")
    if source_folder:
        header_lines.append(f"X-Source-Folder: {source_folder}")
    return "\n".join(header_lines) + "\n---\n" + (body or "")


def merge_email_reply_body(existing_content: str, reply_body: str) -> str:
    """Preserve email headers and quoted chain while replacing reply body."""
    if "\n---\n" not in (existing_content or ""):
        return reply_body or ""
    head, body = existing_content.split("\n---\n", 1)
    quote_markers = (
        "---------- Previous message ----------",
        "-----Original Message-----",
        "----- Original Message -----",
    )
    quote_index = -1
    for marker in quote_markers:
        idx = body.find(marker)
        if idx != -1 and (quote_index == -1 or idx < quote_index):
            quote_index = idx
    quote = body[quote_index:].strip() if quote_index != -1 else ""
    merged_body = (reply_body or "").strip()
    if quote:
        merged_body = f"{merged_body}\n\n{quote}" if merged_body else quote
    return f"{head}\n---\n{merged_body}"


def create_email_draft_document(
    *,
    to,
    subject,
    body,
    title=None,
    cc=None,
    bcc=None,
    in_reply_to=None,
    references=None,
    source_uid=None,
    source_folder=None,
    account=None,
    source_message_id=None,
    load_config_func: Callable,
    current_owner_func: Callable[[], str | None],
    default_document_owner_func: Callable[[], str | None],
) -> dict:
    """Create an Odysseus email compose document for user review."""
    from core.database import SessionLocal, Document, DocumentVersion

    try:
        from src.event_bus import fire_event
    except Exception:
        fire_event = None

    cfg = load_config_func(account) if account else load_config_func(None)
    content = build_email_document_content(
        to,
        subject,
        body,
        cc=cc,
        bcc=bcc,
        in_reply_to=in_reply_to,
        references=references,
        source_uid=source_uid,
        source_folder=source_folder,
    )
    doc_id = str(uuid.uuid4())
    ver_id = str(uuid.uuid4())
    doc_title = (title or subject or "Email draft").strip() or "Email draft"
    doc_owner = current_owner_func() or default_document_owner_func()

    db = SessionLocal()
    try:
        if source_uid and source_folder:
            existing = (
                db.query(Document)
                .filter(Document.is_active == True)
                .filter(Document.language == "email")
                .filter(Document.owner == doc_owner)
                .filter(Document.source_email_uid == str(source_uid))
                .filter(Document.source_email_folder == source_folder)
                .order_by(Document.updated_at.desc())
                .first()
            )
            if existing and "\n---\n" in (existing.current_content or ""):
                existing.current_content = merge_email_reply_body(
                    existing.current_content,
                    body or "",
                )
                existing.version_count = (existing.version_count or 0) + 1
                ver = DocumentVersion(
                    id=ver_id,
                    document_id=existing.id,
                    version_number=existing.version_count,
                    content=existing.current_content,
                    summary="Updated by email MCP draft tool",
                    source="ai",
                )
                db.add(ver)
                db.commit()
                if fire_event:
                    try:
                        fire_event("document_updated", doc_owner)
                    except Exception:
                        pass
                return {
                    "draft": True,
                    "updated": True,
                    "doc_id": existing.id,
                    "title": existing.title,
                    "language": existing.language,
                    "account": cfg.get("account_name"),
                    "account_id": cfg.get("account_id"),
                    "to": to,
                    "subject": subject,
                }

        doc = Document(
            id=doc_id,
            session_id=None,
            title=doc_title,
            language="email",
            current_content=content,
            version_count=1,
            is_active=True,
            owner=doc_owner,
            source_email_uid=source_uid,
            source_email_folder=source_folder,
            source_email_account_id=cfg.get("account_id"),
            source_email_message_id=source_message_id,
        )
        ver = DocumentVersion(
            id=ver_id,
            document_id=doc_id,
            version_number=1,
            content=content,
            summary="Created by email MCP draft tool",
            source="ai",
        )
        db.add(doc)
        db.add(ver)
        db.commit()
        if fire_event:
            try:
                fire_event("document_created", doc_owner)
            except Exception:
                pass
        return {
            "draft": True,
            "doc_id": doc_id,
            "title": doc_title,
            "language": "email",
            "account": cfg.get("account_name"),
            "account_id": cfg.get("account_id"),
            "to": to,
            "subject": subject,
        }
    finally:
        db.close()

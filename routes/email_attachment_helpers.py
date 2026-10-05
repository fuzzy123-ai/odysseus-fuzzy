"""Email attachment document-import helpers."""

from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request


@contextmanager
def _document_db(document_authority):
    from src.database import SessionLocal
    from src.document_subject_scope import current_document_owner_alias, guard_document_request_authority
    guard = guard_document_request_authority(document_authority) if document_authority is not None else None
    lease = guard.__enter__() if guard is not None else None
    try:
        db = SessionLocal()
        try:
            yield db, lease, (current_document_owner_alias(lease) if lease is not None else None)
        finally:
            db.close()
    finally:
        if guard is not None:
            guard.__exit__(None, None, None)


def attachment_basename(filepath: Path) -> tuple[str, str, str] | dict[str, str]:
    base = Path(filepath).name
    if base.startswith("."):
        return {"error": "Invalid filename", "filename": base}
    ext = Path(base).suffix.lower()
    title = os.path.splitext(base)[0]
    return base, ext, title


def tag_document_with_source(
    doc_id: str,
    *,
    uid: str,
    folder: str,
    account_id: str | None,
    message_id: str,
    logger: Any = None,
    document_authority=None,
    owner: str | None = None,
    source_email: dict[str, Any] | None = None,
) -> None:
    if not doc_id:
        return
    try:
        from src.database import Document as DbDocument
        from src.document_subject_scope import scope_document_web_query

        with _document_db(document_authority) as (db, lease, alias):
            query = db.query(DbDocument).filter(DbDocument.id == doc_id)
            if lease is not None:
                query = scope_document_web_query(query, lease, model=DbDocument)
            else:
                query = query.filter(DbDocument.creator_subject_id.is_(None))
                if owner:
                    query = query.filter(DbDocument.owner == owner)
            doc = query.first()
            if doc:
                doc.source_email_uid = str(uid)
                doc.source_email_folder = folder
                doc.source_email_account_id = account_id or ""
                doc.source_email_message_id = message_id
                db.commit()
    except Exception:
        if logger is not None:
            logger.warning("tag doc source-email failed")


def resolve_document_session(request: Request, logger: Any = None, *, document_authority=None) -> str | None:
    try:
        from src.database import Session as DbSession
        from src.auth_helpers import get_current_user
        with _document_db(document_authority) as (db, lease, alias):
            doc_user = alias if lease is not None else get_current_user(request)
            query = db.query(DbSession)
            if doc_user:
                query = query.filter(DbSession.owner == doc_user)
            session = query.order_by(DbSession.updated_at.desc()).first()
            return session.id if session else None
    except Exception:
        if logger is not None:
            logger.warning("resolve doc session failed")
        return None


def docx_to_markdown(filepath: Path) -> str | dict[str, str]:
    try:
        from docx import Document as DocxDocument
    except ImportError:
        return {"error": "python-docx not installed", "filename": filepath.name}
    try:
        docx = DocxDocument(str(filepath))
    except Exception:
        return {"error": "Failed to read docx", "filename": filepath.name}

    lines: list[str] = []
    for paragraph in docx.paragraphs:
        text = paragraph.text or ""
        style = (paragraph.style.name if paragraph.style else "") or ""
        if not text.strip():
            lines.append("")
            continue
        if style.startswith("Heading 1"):
            lines.append(f"# {text}")
        elif style.startswith("Heading 2"):
            lines.append(f"## {text}")
        elif style.startswith("Heading 3"):
            lines.append(f"### {text}")
        elif style.startswith("Heading "):
            lines.append(f"#### {text}")
        elif style.startswith("List Bullet"):
            lines.append(f"- {text}")
        elif style.startswith("List Number"):
            lines.append(f"1. {text}")
        else:
            lines.append(text)

    for table in docx.tables:
        lines.append("")
        for row_index, row in enumerate(table.rows):
            cells = [
                (cell.text or "").replace("|", "\\|").replace("\n", " ").strip()
                for cell in row.cells
            ]
            lines.append("| " + " | ".join(cells) + " |")
            if row_index == 0:
                lines.append("|" + "|".join(["---"] * len(cells)) + "|")
        lines.append("")

    return "\n".join(lines).strip() or f"_(empty {filepath.name})_"


def create_markdown_attachment_document(
    *,
    title: str,
    content: str,
    session_id: str | None,
    summary: str,
    document_authority=None,
    owner: str | None = None,
    source_email: dict[str, Any] | None = None,
) -> str:
    from src.database import Document as DbDocument
    from src.database import DocumentVersion
    from src.document_subject_scope import scope_document_web_query, stamp_new_document

    doc_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    with _document_db(document_authority) as (db, lease, alias):
      try:
        active = db.query(DbDocument).filter(DbDocument.is_active == True)
        if lease is not None:
            active = scope_document_web_query(active, lease, model=DbDocument)
        else:
            active = active.filter(DbDocument.creator_subject_id.is_(None))
            if owner:
                active = active.filter(DbDocument.owner == owner)
        active.update({"is_active": False}, synchronize_session=False)
        local_owner = alias if lease is not None else owner
        document = DbDocument(
            id=doc_id,
            session_id=session_id,
            title=title,
            language="markdown",
            current_content=content,
            version_count=1,
            is_active=True,
            owner=local_owner,
            source_email_uid=(source_email or {}).get("uid"),
            source_email_folder=(source_email or {}).get("folder"),
            source_email_account_id=(source_email or {}).get("account_id"),
            source_email_message_id=(source_email or {}).get("message_id"),
        )
        if lease is not None:
            stamp_new_document(document, lease, local_owner=local_owner)
        db.add(document)
        db.add(
            DocumentVersion(
                id=version_id,
                document_id=doc_id,
                version_number=1,
                content=content,
                summary=summary,
                source="upload",
            )
        )
        db.commit()
      except Exception:
        db.rollback()
        raise
    return doc_id


def create_pdf_attachment_document(filepath: Path, *, title: str, session_id: str | None, logger: Any = None, document_authority=None, source_email: dict[str, Any] | None = None) -> str | None:
    from src.constants import UPLOAD_DIR
    from src.pdf_form_doc import (
        create_form_markdown_document,
        create_plain_pdf_document,
        save_field_sidecar,
    )
    from src.pdf_forms import extract_fields, has_form_fields

    upload_id = f"{uuid.uuid4().hex}.pdf"
    today = datetime.utcnow().strftime("%Y/%m/%d")
    dated_dir = os.path.join(UPLOAD_DIR, today)
    os.makedirs(dated_dir, exist_ok=True)
    dest_path = os.path.join(dated_dir, upload_id)
    shutil.copyfile(str(filepath), dest_path)

    is_form = False
    try:
        is_form = has_form_fields(dest_path)
    except Exception as exc:
        if logger is not None:
            logger.warning(f"has_form_fields failed for attachment PDF: {exc}")

    if is_form:
        fields = extract_fields(dest_path)
        save_field_sidecar(dest_path, fields)
        return create_form_markdown_document(
            session_id=session_id,
            fields=fields,
            upload_id=upload_id,
            title=title,
            intro_text=None,
            document_authority=document_authority,
            source_email=source_email,
        )

    return create_plain_pdf_document(
        session_id=session_id,
        upload_id=upload_id,
        title=title,
        document_authority=document_authority,
        source_email=source_email,
    )


def attachment_as_document_response(
    filepath: Path,
    msg,
    *,
    uid: str,
    folder: str,
    account_id: str | None,
    request: Request,
    logger: Any = None,
    document_authority=None,
    owner: str | None = None,
) -> dict[str, Any]:
    parsed = attachment_basename(filepath)
    if isinstance(parsed, dict):
        return parsed
    base, ext, title = parsed
    source_message_id = (msg.get("Message-ID") or "").strip()
    source_email = {
        "uid": str(uid),
        "folder": folder,
        "account_id": account_id or "",
        "message_id": source_message_id,
    }

    if ext not in (".pdf", ".docx", ".txt", ".md", ".markdown"):
        return {"error": f"Unsupported attachment type: {ext}", "filename": base}

    doc_session_id = resolve_document_session(
        request, logger=logger, document_authority=document_authority
    )

    if ext == ".pdf":
        doc_id = create_pdf_attachment_document(filepath, title=title, session_id=doc_session_id, logger=logger, document_authority=document_authority, source_email=source_email)
        if not doc_id:
            return {"error": "Failed to create document"}
        return {"doc_id": doc_id, "filename": filepath.name}

    if ext == ".docx":
        content = docx_to_markdown(filepath)
        if isinstance(content, dict):
            return content
        doc_id = create_markdown_attachment_document(
            title=title,
            content=content,
            session_id=doc_session_id,
            summary="Imported from DOCX",
            document_authority=document_authority,
            owner=owner,
            source_email=source_email,
        )
    else:
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"error": "Failed to read text file", "filename": base}
        doc_id = create_markdown_attachment_document(
            title=title,
            content=content,
            session_id=doc_session_id,
            summary="Imported from email attachment",
            document_authority=document_authority,
            owner=owner,
            source_email=source_email,
        )

    return {"doc_id": doc_id, "filename": filepath.name}

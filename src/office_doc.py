"""Auto-create a Document row from an Office attachment.

When a .docx (and friends) lands in chat, the full extracted text is stored
as a Document so the agent can page through it with `manage_documents
action=read offset=…` even after the inline chat payload was capped. Mirrors
the PDF auto-doc pattern in `src.pdf_form_doc`.
"""

import logging
import uuid
from contextlib import contextmanager
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


@contextmanager
def _document_creation_db(document_authority):
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


def create_office_document(
    session_id: str,
    upload_id: str,
    title: str,
    body_text: Optional[str] = None,
    *,
    document_authority=None,
) -> Optional[str]:
    """Create a markdown Document for an Office attachment and set it active.

    Returns the new doc_id, or None on failure / empty body. The full
    extracted body lives in `current_content`, so the agent can fetch
    arbitrary windows via `manage_documents action=read` even when the
    inline chat copy was truncated.
    """
    from src.database import (
        Document,
        DocumentVersion,
        Session as DbSession,
    )
    from src.agent_tools.document_tools import set_active_document
    from src.document_subject_scope import stamp_new_document

    if not body_text or not body_text.strip():
        return None

    with _document_creation_db(document_authority) as (db, lease, authority_owner):
      try:
        doc_id = str(uuid.uuid4())
        ver_id = str(uuid.uuid4())
        session_q = db.query(DbSession).filter(DbSession.id == session_id)
        if authority_owner is not None:
            session_q = session_q.filter(DbSession.owner == authority_owner)
        sess = session_q.first()
        if session_id and sess is None:
            return None
        local_owner = authority_owner if authority_owner is not None else (sess.owner if sess else None)
        doc = Document(
            id=doc_id,
            session_id=session_id,
            title=title,
            language="markdown",
            current_content=body_text,
            version_count=1,
            is_active=True,
            owner=local_owner,
        )
        if lease is not None:
            stamp_new_document(doc, lease, local_owner=local_owner)
        ver = DocumentVersion(
            id=ver_id,
            document_id=doc_id,
            version_number=1,
            content=body_text,
            summary="Imported from Office attachment",
            source="upload",
        )
        db.add(doc)
        db.add(ver)
        db.commit()
        set_active_document(doc_id)
        return doc_id
      except HTTPException:
        db.rollback()
        raise
      except Exception:
        db.rollback()
        logger.error("Failed to create office document")
        return None

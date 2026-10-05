"""Owner-scoped Universal Inbox source-to-Document working-copy bridge."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError

from core.database import Document, DocumentVersion
from src.pdf_form_doc import render_plain_pdf_markdown
from src.universal_inbox_extraction import (
    UniversalInboxExtractionError,
    extract_universal_inbox_content,
)
from src.universal_inbox_source_access import (
    UniversalInboxSourceAccessError,
    normalize_universal_inbox_source_ref,
    read_selected_universal_inbox_source,
)
from src.upload_handler import is_valid_upload_id


WORKING_COPY_SCHEMA = "odysseus.universal_inbox.working_copy.v1"
WORKING_COPY_ERROR_SCHEMA = "odysseus.universal_inbox.working_copy_error.v1"
MAX_WORKING_COPY_SOURCE_BYTES = 8 * 1024 * 1024
LOCAL_OWNER_SCOPE = "__local_universal_inbox__"


class UniversalInboxWorkingCopyError(RuntimeError):
    """Content-free failure suitable for a browser API response."""

    def __init__(self, status_code: int, state: str, reason_code: str) -> None:
        super().__init__(reason_code)
        self.status_code = status_code
        self.state = state
        self.reason_code = reason_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKING_COPY_ERROR_SCHEMA,
            "state": self.state,
            "reason_code": self.reason_code,
            "content_included": False,
            "source_ref_visible": False,
            "absolute_path_visible": False,
        }


@dataclass(frozen=True, slots=True)
class UniversalInboxWorkingCopyResult:
    working_copy_id: str
    version: int
    created: bool
    revision_created: bool

    def status_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKING_COPY_SCHEMA,
            "created": self.created,
            "revision_created": self.revision_created,
            "working_copy_id": self.working_copy_id,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class _WorkingCopyRevisionToken:
    document_id: str
    version_count: int | None
    updated_at: str | None
    is_active: bool
    content_sha256: str


def create_or_get_universal_inbox_working_copy(
    session_factory: Any,
    upload_handler: Any,
    source_ref: str,
    *,
    owner: str | None,
    auth_manager: Any = None,
    new_revision: bool = False,
    document_authority=None,
) -> UniversalInboxWorkingCopyResult:
    """Return one stable working copy, or append an explicitly requested revision."""
    from src.document_subject_scope import (
        guard_document_request_authority,
        stamp_new_document,
    )

    source_kind, upload_id = _normalized_upload_ref(source_ref)
    owner_scope = _owner_scope(owner)
    source_ref_hash = _source_ref_hash(source_kind, upload_id)

    def _guard():
        if document_authority is None:
            return nullcontext(None)
        return guard_document_request_authority(document_authority)

    first_db = session_factory()
    existing = None
    expected = None
    try:
        with _guard() as first_lease:
            existing = _find_working_copy(
                first_db,
                owner=owner_scope,
                source_kind=source_kind,
                source_ref_hash=source_ref_hash,
                lease=first_lease,
            )
            if existing is not None and not new_revision:
                if not existing.is_active:
                    raise UniversalInboxWorkingCopyError(
                        409, "inactive", "working_copy_inactive"
                    )
                return UniversalInboxWorkingCopyResult(
                    str(existing.id), int(existing.version_count or 0), False, False
                )
            expected = (
                _working_copy_revision_token(existing)
                if existing is not None
                else None
            )
    finally:
        try:
            first_db.close()
        finally:
            first_db = None
            existing = None
    first_lease = None

    title, language, content = _build_working_copy_content(
        upload_handler,
        upload_id=upload_id,
        source_ref=f"{source_kind}:{upload_id}",
        owner=owner,
        auth_manager=auth_manager,
    )

    write_db = session_factory()
    try:
        with _guard() as final_lease:
            current = _find_working_copy(
                write_db,
                owner=owner_scope,
                source_kind=source_kind,
                source_ref_hash=source_ref_hash,
                lease=final_lease,
            )
            if expected is not None:
                if current is None or _working_copy_revision_token(current) != expected:
                    raise UniversalInboxWorkingCopyError(
                        409, "conflict", "working_copy_changed"
                    )
                if not current.is_active:
                    raise UniversalInboxWorkingCopyError(
                        409, "inactive", "working_copy_inactive"
                    )

                next_version = int(expected.version_count or 1) + 1
                expected_id = expected.document_id
                expected_updated_at = (
                    datetime.fromisoformat(expected.updated_at)
                    if expected.updated_at is not None
                    else None
                )
                version_predicate = (
                    Document.version_count.is_(None)
                    if expected.version_count is None
                    else Document.version_count == expected.version_count
                )
                updated_at_predicate = (
                    Document.updated_at.is_(None)
                    if expected_updated_at is None
                    else Document.updated_at == expected_updated_at
                )
                current = None
                updated = (
                    _working_copy_query(
                        write_db,
                        owner=owner_scope,
                        source_kind=source_kind,
                        source_ref_hash=source_ref_hash,
                        lease=final_lease,
                    )
                    .filter(
                        Document.id == expected_id,
                        version_predicate,
                        updated_at_predicate,
                        Document.is_active.is_(True),
                    )
                    .update(
                        {
                            "title": title,
                            "language": language,
                            "current_content": content,
                            "version_count": next_version,
                        },
                        synchronize_session=False,
                    )
                )
                if updated != 1:
                    write_db.rollback()
                    raise UniversalInboxWorkingCopyError(
                        409, "conflict", "working_copy_changed"
                    )
                write_db.add(DocumentVersion(
                    id=str(uuid.uuid4()), document_id=expected_id,
                    version_number=next_version, content=content,
                    summary="Explicit Universal Inbox source revision", source="upload",
                ))
                write_db.commit()
                return UniversalInboxWorkingCopyResult(
                    expected_id, next_version, False, True
                )

            if current is not None:
                if not new_revision and current.is_active:
                    return UniversalInboxWorkingCopyResult(
                        str(current.id), int(current.version_count or 0), False, False
                    )
                raise UniversalInboxWorkingCopyError(
                    409, "conflict", "working_copy_changed"
                )

            document_id = str(uuid.uuid4())
            document = Document(
                id=document_id, session_id=None, title=title, language=language,
                current_content=content, version_count=1, is_active=True,
                owner=owner_scope, source_kind=source_kind, source_ref_hash=source_ref_hash,
            )
            if final_lease is not None:
                stamp_new_document(document, final_lease, local_owner=owner_scope)
            write_db.add(document)
            write_db.add(DocumentVersion(
                id=str(uuid.uuid4()), document_id=document_id, version_number=1,
                content=content, summary="Initial Universal Inbox working copy", source="upload",
            ))
            try:
                write_db.commit()
            except IntegrityError:
                write_db.rollback()
                raced = _find_working_copy(
                    write_db, owner=owner_scope, source_kind=source_kind,
                    source_ref_hash=source_ref_hash, lease=final_lease,
                )
                if raced is not None and not new_revision and raced.is_active:
                    return UniversalInboxWorkingCopyResult(
                        str(raced.id), int(raced.version_count or 0), False, False
                    )
                raise
            return UniversalInboxWorkingCopyResult(document_id, 1, True, False)
    finally:
        write_db.close()


def _working_copy_revision_token(document: Document) -> _WorkingCopyRevisionToken:
    content = str(document.current_content or "").encode("utf-8", "strict")
    return _WorkingCopyRevisionToken(
        document_id=str(document.id),
        version_count=(
            int(document.version_count) if document.version_count is not None else None
        ),
        updated_at=(document.updated_at.isoformat() if document.updated_at else None),
        is_active=bool(document.is_active),
        content_sha256=hashlib.sha256(content).hexdigest(),
    )


def _normalized_upload_ref(source_ref: str) -> tuple[str, str]:
    try:
        source_kind, source_id = normalize_universal_inbox_source_ref(source_ref)
    except UniversalInboxSourceAccessError as exc:
        raise _working_copy_access_error(exc) from exc
    if source_kind != "upload":
        raise UniversalInboxWorkingCopyError(
            415, "unsupported", "unsupported_source_kind"
        )
    if not is_valid_upload_id(source_id):
        raise UniversalInboxWorkingCopyError(
            400, "invalid", "invalid_upload_source_ref"
        )
    return source_kind, source_id


def _owner_scope(owner: str | None) -> str:
    return str(owner or "").strip() or LOCAL_OWNER_SCOPE


def _source_ref_hash(source_kind: str, source_id: str) -> str:
    canonical = f"{source_kind}:{source_id}".encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _find_working_copy(
    db: Any,
    *,
    owner: str,
    source_kind: str,
    source_ref_hash: str,
    lease=None,
) -> Document | None:
    return _working_copy_query(
        db,
        owner=owner,
        source_kind=source_kind,
        source_ref_hash=source_ref_hash,
        lease=lease,
    ).first()


def _working_copy_query(
    db: Any,
    *,
    owner: str,
    source_kind: str,
    source_ref_hash: str,
    lease=None,
):
    from src.document_subject_scope import scope_document_ingress_key_query

    query = db.query(Document)
    if lease is not None:
        query = scope_document_ingress_key_query(
            query, lease, local_owner=owner, model=Document
        )
    else:
        query = query.filter(
            Document.creator_subject_id.is_(None),
            Document.owner == owner,
        )
    return query.filter(
        Document.source_kind == source_kind,
        Document.source_ref_hash == source_ref_hash,
    )


def _build_working_copy_content(
    upload_handler: Any,
    *,
    upload_id: str,
    source_ref: str,
    owner: str | None,
    auth_manager: Any,
) -> tuple[str, str, str]:
    if upload_handler is None or not callable(
        getattr(upload_handler, "resolve_upload", None)
    ):
        raise UniversalInboxWorkingCopyError(
            503, "unavailable", "upload_source_backend_unavailable"
        )

    info = upload_handler.resolve_upload(
        upload_id,
        owner=str(owner or "").strip() or None,
        auth_manager=auth_manager,
        allow_admin=True,
    )
    if not isinstance(info, dict):
        raise UniversalInboxWorkingCopyError(404, "not_found", "source_not_found")

    try:
        selected = read_selected_universal_inbox_source(
            upload_handler,
            source_ref,
            owner=owner,
            auth_manager=auth_manager,
            max_response_bytes=MAX_WORKING_COPY_SOURCE_BYTES,
            max_source_bytes=MAX_WORKING_COPY_SOURCE_BYTES,
        )
    except UniversalInboxSourceAccessError as exc:
        raise _working_copy_access_error(exc) from exc
    if selected.state != "complete":
        raise UniversalInboxWorkingCopyError(
            413, "oversized", "source_size_limit_exceeded"
        )

    display_name = _safe_source_name(
        info.get("original_name") or info.get("name") or upload_id
    )
    suffix = Path(display_name).suffix.lower() or Path(upload_id).suffix.lower()
    title = Path(display_name).stem.strip() or "Untitled"

    if suffix in {".md", ".markdown", ".txt"}:
        return title, "markdown" if suffix != ".txt" else "text", _decode_text(
            selected.body
        )

    source_path = _confined_source_path(upload_handler, info)
    try:
        extraction = extract_universal_inbox_content(
            source_path,
            relative_path=display_name,
            max_extract_bytes=MAX_WORKING_COPY_SOURCE_BYTES,
        )
    except UniversalInboxExtractionError as exc:
        raise UniversalInboxWorkingCopyError(
            422, "failed", "source_extraction_failed"
        ) from exc

    if suffix == ".docx":
        if not extraction.raw_text:
            raise UniversalInboxWorkingCopyError(
                422, "failed", "source_extraction_failed"
            )
        return title, "markdown", extraction.raw_text
    if suffix == ".pdf":
        return (
            title,
            "markdown",
            render_plain_pdf_markdown(
                upload_id,
                title,
                body_text=extraction.raw_text or None,
            ),
        )
    raise UniversalInboxWorkingCopyError(
        415, "unsupported", "source_format_not_supported"
    )


def _safe_source_name(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    return os.path.basename(raw) or "Untitled"


def _decode_text(body: bytes) -> str:
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode("latin-1")


def _confined_source_path(upload_handler: Any, info: dict[str, Any]) -> str:
    root = os.path.realpath(str(getattr(upload_handler, "upload_dir", "") or ""))
    path = os.path.realpath(str(info.get("path") or ""))
    try:
        confined = bool(root and path and os.path.commonpath([root, path]) == root)
    except (OSError, ValueError):
        confined = False
    if not confined or not os.path.isfile(path):
        raise UniversalInboxWorkingCopyError(404, "not_found", "source_not_found")
    return path


def _working_copy_access_error(
    exc: UniversalInboxSourceAccessError,
) -> UniversalInboxWorkingCopyError:
    return UniversalInboxWorkingCopyError(
        exc.status_code,
        exc.state,
        exc.reason_code,
    )

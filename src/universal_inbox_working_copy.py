"""Owner-scoped Universal Inbox source-to-Document working-copy bridge."""

from __future__ import annotations

from dataclasses import dataclass
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
    document: Document
    created: bool
    revision_created: bool

    def status_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKING_COPY_SCHEMA,
            "created": self.created,
            "revision_created": self.revision_created,
            "working_copy_id": self.document.id,
            "version": self.document.version_count,
        }


def create_or_get_universal_inbox_working_copy(
    db: Any,
    upload_handler: Any,
    source_ref: str,
    *,
    owner: str | None,
    auth_manager: Any = None,
    new_revision: bool = False,
) -> UniversalInboxWorkingCopyResult:
    """Return one stable working copy, or append an explicitly requested revision."""

    source_kind, upload_id = _normalized_upload_ref(source_ref)
    owner_scope = _owner_scope(owner)
    source_ref_hash = _source_ref_hash(source_kind, upload_id)
    existing = _find_working_copy(
        db,
        owner=owner_scope,
        source_kind=source_kind,
        source_ref_hash=source_ref_hash,
    )
    if existing is not None and not new_revision:
        if not existing.is_active:
            raise UniversalInboxWorkingCopyError(
                409, "inactive", "working_copy_inactive"
            )
        return UniversalInboxWorkingCopyResult(existing, False, False)

    title, language, content = _build_working_copy_content(
        upload_handler,
        upload_id=upload_id,
        source_ref=f"{source_kind}:{upload_id}",
        owner=owner,
        auth_manager=auth_manager,
    )

    if existing is not None:
        if not existing.is_active:
            raise UniversalInboxWorkingCopyError(
                409, "inactive", "working_copy_inactive"
            )
        next_version = int(existing.version_count or 1) + 1
        existing.title = title
        existing.language = language
        existing.current_content = content
        existing.version_count = next_version
        db.add(
            DocumentVersion(
                id=str(uuid.uuid4()),
                document_id=existing.id,
                version_number=next_version,
                content=content,
                summary="Explicit Universal Inbox source revision",
                source="upload",
            )
        )
        db.commit()
        db.refresh(existing)
        return UniversalInboxWorkingCopyResult(existing, False, True)

    document = Document(
        id=str(uuid.uuid4()),
        session_id=None,
        title=title,
        language=language,
        current_content=content,
        version_count=1,
        is_active=True,
        owner=owner_scope,
        source_kind=source_kind,
        source_ref_hash=source_ref_hash,
    )
    version = DocumentVersion(
        id=str(uuid.uuid4()),
        document_id=document.id,
        version_number=1,
        content=content,
        summary="Initial Universal Inbox working copy",
        source="upload",
    )
    db.add(document)
    db.add(version)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = _find_working_copy(
            db,
            owner=owner_scope,
            source_kind=source_kind,
            source_ref_hash=source_ref_hash,
        )
        if raced is not None and not new_revision and raced.is_active:
            return UniversalInboxWorkingCopyResult(raced, False, False)
        raise
    db.refresh(document)
    return UniversalInboxWorkingCopyResult(document, True, False)


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
) -> Document | None:
    return (
        db.query(Document)
        .filter(
            Document.owner == owner,
            Document.source_kind == source_kind,
            Document.source_ref_hash == source_ref_hash,
        )
        .first()
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

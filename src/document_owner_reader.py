"""Bounded exact reader for documents owned by one sealed browser subject."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, TypeVar

from fastapi import HTTPException
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as SqlAlchemySession
from sqlalchemy.orm import load_only, sessionmaker

from core.database import Document, DocumentVersion, SessionLocal
from src.document_subject_scope import (
    DocumentRequestAuthority,
    _document_subject_binding,
    guard_document_request_authority,
    scope_document_bound_query,
)


DOCUMENT_OWNER_READER_SCHEMA = "odysseus.document_owner_reader.v1"
MAX_DOCUMENTS = 1_000
MAX_TITLE_UTF8_BYTES = 4_096
MAX_LANGUAGE_UTF8_BYTES = 256
MAX_DOCUMENT_BYTES = 8 * 1_024 * 1_024
MAX_TOTAL_DOCUMENT_BYTES = 64 * 1_024 * 1_024
MAX_METADATA_BYTES = 1 * 1_024 * 1_024

_SHA256_REF_PREFIX = "sha256:"
_SOURCE_REF_PREFIX = "document:sha256:"
_ERROR_CODES = frozenset(
    {
        "invalid_request",
        "invalid_descriptor",
        "invalid_snapshot",
        "operation_in_progress",
        "authority_unavailable",
        "unauthorized",
        "limit_exceeded",
        "capture_failed",
        "storage_failed",
        "not_found",
        "changed",
        "read_failed",
    }
)


class DocumentOwnerReaderError(ValueError):
    """Content-free public reader failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in _ERROR_CODES else "read_failed"
        self.code = safe
        super().__init__(safe)

    def __repr__(self) -> str:
        return f"DocumentOwnerReaderError({self.code!r})"


class _Failure(Exception):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__()


def _fail(reason: str) -> None:
    raise _Failure(reason)


def _raise_public(reason: str) -> None:
    raise DocumentOwnerReaderError(reason) from None


def _part(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _digest(domain: str, *parts: bytes) -> str:
    raw = _part(domain.encode("ascii")) + b"".join(_part(item) for item in parts)
    return _SHA256_REF_PREFIX + hashlib.sha256(raw).hexdigest()


def _strict_text(value: object, maximum: int, reason: str, *, allow_none: bool = False) -> tuple[str | None, bytes]:
    if value is None and allow_none:
        return None, b""
    if type(value) is not str:
        _fail(reason)
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        _fail(reason)
    if len(encoded) > maximum:
        _fail("limit_exceeded")
    return value, encoded


def _integer(value: int) -> bytes:
    if type(value) is not int or value < 0:
        _fail("capture_failed")
    return str(value).encode("ascii")


def _boolean(value: bool) -> bytes:
    if type(value) is not bool:
        _fail("capture_failed")
    return b"1" if value else b"0"


def _timestamp(value: object) -> tuple[str, bytes]:
    if type(value) is not datetime:
        _fail("capture_failed")
    current = value
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    rendered = current.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return rendered, rendered.encode("ascii")


def _content(value: object) -> tuple[str, bytes]:
    text, encoded = _strict_text(value, MAX_DOCUMENT_BYTES, "capture_failed")
    if text is None:
        _fail("capture_failed")
    return text, encoded


def _engine_binding(engine: Engine) -> str:
    if type(engine) is not Engine:
        _fail("invalid_request")
    url = engine.url
    dialect = str(url.get_backend_name() or "")
    driver = str(url.get_driver_name() or "")
    host = str(url.host or "")
    port = 0 if url.port is None else url.port
    database = str(url.database or "")
    if dialect == "sqlite" and database not in {"", ":memory:"}:
        database = str(Path(database).resolve())
    if type(port) is not int or port < 0 or port > 65535:
        _fail("invalid_request")
    # Username, password, query, and fragment are intentionally never read.
    return _digest(
        "document_owner_reader.database.v1",
        dialect.encode("utf-8", "strict"),
        driver.encode("utf-8", "strict"),
        host.encode("utf-8", "strict"),
        str(port).encode("ascii"),
        database.encode("utf-8", "strict"),
    )


@dataclass(frozen=True, slots=True, repr=False)
class DocumentOwnerDescriptor:
    schema_id: str
    source_ref: str
    revision_ref: str
    title: str
    language: str | None
    version_count: int
    is_active: bool
    archived: bool
    created_at: str
    updated_at: str
    byte_size: int
    char_size: int
    content_hash: str
    descriptor_digest: str

    def __repr__(self) -> str:
        return (
            "DocumentOwnerDescriptor("
            f"source_ref={self.source_ref!r}, descriptor_digest={self.descriptor_digest!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class DocumentOwnerSnapshot:
    schema_id: str
    owner_binding_digest: str
    descriptors: tuple[DocumentOwnerDescriptor, ...]
    snapshot_digest: str

    def __repr__(self) -> str:
        return "DocumentOwnerSnapshot(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DocumentExactRead:
    schema_id: str
    source_ref: str
    revision_ref: str
    content: str = field(repr=False)
    content_hash: str
    descriptor_digest: str
    byte_size: int
    char_size: int

    def __repr__(self) -> str:
        return "DocumentExactRead(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _RowFingerprint:
    document_id: str
    source_ref: str
    revision_ref: str
    title: str
    language: str | None
    version_count: int
    is_active: bool
    archived: bool
    created_at: str
    updated_at: str
    byte_size: int
    char_size: int
    content_hash: str
    provenance_digest: str
    descriptor_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class _DescriptorSeal:
    descriptor: DocumentOwnerDescriptor
    values: tuple[object, ...]
    fingerprint: _RowFingerprint


@dataclass(frozen=True, slots=True, repr=False)
class _SnapshotSeal:
    snapshot: DocumentOwnerSnapshot
    descriptors: tuple[DocumentOwnerDescriptor, ...]
    snapshot_digest: str


_Result = TypeVar("_Result")


def _descriptor_values(value: object) -> tuple[object, ...]:
    if type(value) is not DocumentOwnerDescriptor:
        _fail("invalid_descriptor")
    return (
        value.schema_id,
        value.source_ref,
        value.revision_ref,
        value.title,
        value.language,
        value.version_count,
        value.is_active,
        value.archived,
        value.created_at,
        value.updated_at,
        value.byte_size,
        value.char_size,
        value.content_hash,
        value.descriptor_digest,
    )


class DocumentOwnerReader:
    """Sequential, process-local exact reader for one sealed request authority."""

    __slots__ = (
        "_authority",
        "_session_factory",
        "_session_class",
        "_engine",
        "_factory_config",
        "_database_binding_digest",
        "_operation_lock",
        "_operation_active",
        "_snapshot_seal",
        "_descriptor_seals",
    )

    def __init__(
        self,
        *,
        authority: DocumentRequestAuthority,
        session_factory: sessionmaker = SessionLocal,
    ) -> None:
        failure: str | None = None
        try:
            self._validate_factory(session_factory)
            with guard_document_request_authority(authority):
                pass
            engine = session_factory.kw.get("bind")
            if type(engine) is not Engine:
                _fail("invalid_request")
            self._authority = authority
            self._session_factory = session_factory
            self._session_class = session_factory.class_
            self._engine = engine
            self._factory_config = self._factory_snapshot(session_factory)
            self._database_binding_digest = _engine_binding(engine)
        except _Failure as error:
            failure = error.reason
        except HTTPException:
            failure = "unauthorized"
        except Exception:
            failure = "invalid_request"
        if failure is not None:
            _raise_public(failure)
        self._operation_lock = threading.RLock()
        self._operation_active = False
        self._snapshot_seal: _SnapshotSeal | None = None
        self._descriptor_seals: dict[int, _DescriptorSeal] = {}

    def __repr__(self) -> str:
        return "DocumentOwnerReader(<redacted>)"

    @staticmethod
    def _factory_snapshot(factory: sessionmaker) -> tuple[object, ...]:
        return (
            factory.class_,
            factory.kw.get("bind"),
            factory.kw.get("autocommit", False),
            factory.kw.get("autoflush", True),
            factory.kw.get("expire_on_commit", True),
            tuple(sorted((str(key), id(value)) for key, value in factory.kw.items())),
        )

    @classmethod
    def _validate_factory(cls, factory: object) -> None:
        if type(factory) is not sessionmaker:
            _fail("invalid_request")
        engine = factory.kw.get("bind")
        session_class = factory.class_
        if (
            type(engine) is not Engine
            or type(session_class) is not type
            or session_class.__base__ is not SqlAlchemySession
            or factory.kw.get("autocommit", False) is not False
            or factory.kw.get("autoflush", True) is not False
        ):
            _fail("invalid_request")

    def _validate_factory_pin(self) -> None:
        self._validate_factory(self._session_factory)
        if (
            self._session_factory.class_ is not self._session_class
            or self._session_factory.kw.get("bind") is not self._engine
            or self._factory_snapshot(self._session_factory) != self._factory_config
            or _engine_binding(self._engine) != self._database_binding_digest
        ):
            _fail("authority_unavailable")

    def _open_session(self) -> SqlAlchemySession:
        self._validate_factory_pin()
        session = self._session_factory()
        if type(session) is not self._session_class:
            try:
                session.close()
            finally:
                _fail("authority_unavailable")
        if session.get_bind() is not self._engine or session.in_transaction():
            try:
                session.close()
            finally:
                _fail("authority_unavailable")
        return session

    @staticmethod
    def _close_session(session: SqlAlchemySession) -> None:
        try:
            session.rollback()
        finally:
            session.close()

    def _invalidate_capture(self) -> None:
        self._snapshot_seal = None
        self._descriptor_seals = {}

    def _run(self, operation: str, callback: Any) -> Any:
        with self._operation_lock:
            if self._operation_active:
                if operation == "capture":
                    self._invalidate_capture()
                _raise_public("operation_in_progress")
            self._operation_active = True
        failure: str | None = None
        result: Any = None
        try:
            result = callback()
        except _Failure as error:
            failure = error.reason
        except DocumentOwnerReaderError as error:
            failure = error.code
        except HTTPException:
            failure = "unauthorized"
        except SQLAlchemyError:
            failure = "storage_failed"
        except Exception:
            failure = "capture_failed" if operation == "capture" else "read_failed"
        finally:
            with self._operation_lock:
                self._operation_active = False
        if failure is not None:
            if operation == "capture" or failure in {
                "invalid_descriptor",
                "invalid_snapshot",
                "authority_unavailable",
                "unauthorized",
                "storage_failed",
                "not_found",
                "changed",
            }:
                self._invalidate_capture()
            _raise_public(failure)
        return result

    def capture(
        self,
        *,
        limit: int = MAX_DOCUMENTS,
        expected_snapshot_digest: str | None = None,
    ) -> DocumentOwnerSnapshot:
        if type(limit) is not int or not 1 <= limit <= MAX_DOCUMENTS:
            _raise_public("invalid_request")
        if expected_snapshot_digest is not None and (
            type(expected_snapshot_digest) is not str
            or len(expected_snapshot_digest) != 71
            or not expected_snapshot_digest.startswith(_SHA256_REF_PREFIX)
            or any(character not in "0123456789abcdef" for character in expected_snapshot_digest[7:])
        ):
            _raise_public("invalid_snapshot")
        self._invalidate_capture()
        return self._run(
            "capture",
            lambda: self._capture(limit, expected_snapshot_digest),
        )

    def _capture(self, limit: int, expected_snapshot_digest: str | None) -> DocumentOwnerSnapshot:
        descriptors: list[DocumentOwnerDescriptor] = []
        seals: dict[int, _DescriptorSeal] = {}
        with guard_document_request_authority(self._authority) as lease:
            binding = _document_subject_binding(lease)
            if binding.local or binding.creator_subject_id is None:
                _fail("unauthorized")
            session: SqlAlchemySession | None = None
            try:
                session = self._open_session()
                query = session.query(Document).options(
                    load_only(
                        Document.id,
                        Document.session_id,
                        Document.title,
                        Document.language,
                        Document.current_content,
                        Document.version_count,
                        Document.is_active,
                        Document.archived,
                        Document.created_at,
                        Document.updated_at,
                        Document.creator_subject_id,
                        Document.tidy_verdict,
                        Document.source_email_uid,
                        Document.source_email_folder,
                        Document.source_email_account_id,
                        Document.source_email_message_id,
                        Document.source_kind,
                        Document.source_ref_hash,
                    )
                )
                rows = scope_document_bound_query(query, lease).order_by(Document.id.asc()).limit(limit + 1).all()
                if len(rows) > limit:
                    _fail("limit_exceeded")
                total_content_bytes = 0
                total_metadata_bytes = 0
                owner_binding = _digest(
                    "document_owner_reader.owner.v1",
                    self._database_binding_digest.encode("ascii"),
                    binding.creator_subject_id.encode("ascii"),
                )
                for row in rows:
                    state = sqlalchemy_inspect(row)
                    if state.persistent is not True or state.session is not session:
                        _fail("storage_failed")
                    descriptor, fingerprint = self._row_descriptor(
                        session,
                        row,
                        binding.creator_subject_id,
                        owner_binding,
                    )
                    total_content_bytes += fingerprint.byte_size
                    total_metadata_bytes += len(
                        json.dumps(
                            _descriptor_values(descriptor),
                            ensure_ascii=True,
                            separators=(",", ":"),
                        ).encode("ascii")
                    )
                    if total_content_bytes > MAX_TOTAL_DOCUMENT_BYTES or total_metadata_bytes > MAX_METADATA_BYTES:
                        _fail("limit_exceeded")
                    descriptors.append(descriptor)
                    seals[id(descriptor)] = _DescriptorSeal(
                        descriptor,
                        _descriptor_values(descriptor),
                        fingerprint,
                    )
                snapshot_digest = _digest(
                    "document_owner_reader.snapshot.v1",
                    DOCUMENT_OWNER_READER_SCHEMA.encode("ascii"),
                    owner_binding.encode("ascii"),
                    *(item.descriptor_digest.encode("ascii") for item in descriptors),
                )
                snapshot = DocumentOwnerSnapshot(
                    DOCUMENT_OWNER_READER_SCHEMA,
                    owner_binding,
                    tuple(descriptors),
                    snapshot_digest,
                )
                if expected_snapshot_digest is not None and snapshot_digest != expected_snapshot_digest:
                    _fail("changed")
                self._validate_factory_pin()
            finally:
                if session is not None:
                    self._close_session(session)
                self._validate_factory_pin()
        self._snapshot_seal = _SnapshotSeal(
            snapshot,
            snapshot.descriptors,
            snapshot.snapshot_digest,
        )
        self._descriptor_seals = seals
        return snapshot

    def _row_descriptor(
        self,
        session: SqlAlchemySession,
        row: Document,
        subject_id: str,
        owner_binding: str,
    ) -> tuple[DocumentOwnerDescriptor, _RowFingerprint]:
        document_id, document_id_bytes = _strict_text(row.id, 512, "capture_failed")
        title, title_bytes = _strict_text(row.title, MAX_TITLE_UTF8_BYTES, "capture_failed")
        language, language_bytes = _strict_text(
            row.language,
            MAX_LANGUAGE_UTF8_BYTES,
            "capture_failed",
            allow_none=True,
        )
        content, content_bytes = _content(row.current_content)
        if document_id is None or title is None:
            _fail("capture_failed")
        version_count = row.version_count
        if type(version_count) is not int or version_count < 1:
            _fail("changed")
        if type(row.is_active) is not bool or type(row.archived) is not bool:
            _fail("capture_failed")
        created_at, created_bytes = _timestamp(row.created_at)
        updated_at, updated_bytes = _timestamp(row.updated_at)
        content_hash = _SHA256_REF_PREFIX + hashlib.sha256(content_bytes).hexdigest()
        provenance_values = (
            row.session_id,
            row.tidy_verdict,
            row.source_email_uid,
            row.source_email_folder,
            row.source_email_account_id,
            row.source_email_message_id,
            row.source_kind,
            row.source_ref_hash,
        )
        normalized_provenance: list[str | None] = []
        for value in provenance_values:
            normalized, _ = _strict_text(
                value,
                4_096,
                "capture_failed",
                allow_none=True,
            )
            normalized_provenance.append(normalized)
        provenance_digest = _digest(
            "document_owner_reader.provenance.v1",
            json.dumps(
                normalized_provenance,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii"),
        )
        source_ref = _SOURCE_REF_PREFIX + _digest(
            "document_owner_reader.source.v1",
            self._database_binding_digest.encode("ascii"),
            subject_id.encode("ascii"),
            document_id_bytes,
        ).removeprefix(_SHA256_REF_PREFIX)
        byte_size = len(content_bytes)
        char_size = len(content)
        language_part = b"null" if language is None else b"text:" + language_bytes
        revision_ref = _digest(
            "document_owner_reader.revision.v1",
            source_ref.encode("ascii"),
            title_bytes,
            language_part,
            _integer(version_count),
            _boolean(row.is_active),
            _boolean(row.archived),
            created_bytes,
            updated_bytes,
            _integer(byte_size),
            _integer(char_size),
            content_hash.encode("ascii"),
            provenance_digest.encode("ascii"),
        )
        self._validate_latest_version(session, document_id, version_count, content)
        descriptor_without_digest = (
            DOCUMENT_OWNER_READER_SCHEMA,
            source_ref,
            revision_ref,
            title,
            language,
            version_count,
            row.is_active,
            row.archived,
            created_at,
            updated_at,
            byte_size,
            char_size,
            content_hash,
        )
        descriptor_digest = _digest(
            "document_owner_reader.descriptor.v1",
            owner_binding.encode("ascii"),
            json.dumps(
                descriptor_without_digest,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii"),
        )
        descriptor = DocumentOwnerDescriptor(*descriptor_without_digest, descriptor_digest)
        fingerprint = _RowFingerprint(
            document_id,
            source_ref,
            revision_ref,
            title,
            language,
            version_count,
            row.is_active,
            row.archived,
            created_at,
            updated_at,
            byte_size,
            char_size,
            content_hash,
            provenance_digest,
            descriptor_digest,
        )
        return descriptor, fingerprint

    @staticmethod
    def _validate_latest_version(
        session: SqlAlchemySession,
        document_id: str,
        version_count: int,
        current_content: str,
    ) -> None:
        candidates = (
            session.query(DocumentVersion.version_number, DocumentVersion.content)
            .filter(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version_number >= version_count,
            )
            .order_by(DocumentVersion.version_number.asc())
            .limit(2)
            .all()
        )
        if (
            len(candidates) != 1
            or type(candidates[0][0]) is not int
            or candidates[0][0] != version_count
            or type(candidates[0][1]) is not str
            or candidates[0][1] != current_content
        ):
            _fail("changed")

    def read_exact(self, descriptor: DocumentOwnerDescriptor) -> DocumentExactRead:
        return self._run("read_exact", lambda: self._read_exact(descriptor))

    def _current_descriptor_seal(self, descriptor: object) -> _DescriptorSeal:
        if type(descriptor) is not DocumentOwnerDescriptor:
            _fail("invalid_descriptor")
        snapshot_seal = self._snapshot_seal
        seal = self._descriptor_seals.get(id(descriptor))
        if snapshot_seal is None:
            _fail("authority_unavailable")
        if (
            seal is None
            or seal.descriptor is not descriptor
            or descriptor not in snapshot_seal.descriptors
            or _descriptor_values(descriptor) != seal.values
        ):
            _fail("invalid_descriptor")
        return seal

    def _read_exact(self, descriptor: DocumentOwnerDescriptor) -> DocumentExactRead:
        seal = self._current_descriptor_seal(descriptor)
        expected = seal.fingerprint
        content: str | None = None
        with guard_document_request_authority(self._authority) as lease:
            binding = _document_subject_binding(lease)
            if binding.local or binding.creator_subject_id is None:
                _fail("unauthorized")
            owner_binding = _digest(
                "document_owner_reader.owner.v1",
                self._database_binding_digest.encode("ascii"),
                binding.creator_subject_id.encode("ascii"),
            )
            session: SqlAlchemySession | None = None
            try:
                session = self._open_session()
                query = session.query(Document).options(
                    load_only(
                        Document.id,
                        Document.session_id,
                        Document.title,
                        Document.language,
                        Document.current_content,
                        Document.version_count,
                        Document.is_active,
                        Document.archived,
                        Document.created_at,
                        Document.updated_at,
                        Document.creator_subject_id,
                        Document.tidy_verdict,
                        Document.source_email_uid,
                        Document.source_email_folder,
                        Document.source_email_account_id,
                        Document.source_email_message_id,
                        Document.source_kind,
                        Document.source_ref_hash,
                    )
                ).filter(Document.id == expected.document_id)
                row = scope_document_bound_query(query, lease).one_or_none()
                if row is None:
                    _fail("not_found")
                state = sqlalchemy_inspect(row)
                if state.persistent is not True or state.session is not session:
                    _fail("storage_failed")
                _, current = self._row_descriptor(
                    session,
                    row,
                    binding.creator_subject_id,
                    owner_binding,
                )
                if current != expected:
                    _fail("changed")
                # Re-fetch and re-fingerprint inside the same held authority
                # immediately before detaching bytes.  populate_existing avoids
                # accepting the identity-map object as the freshness check.
                session.expire_all()
                fresh_query = session.query(Document).options(
                    load_only(
                        Document.id,
                        Document.session_id,
                        Document.title,
                        Document.language,
                        Document.current_content,
                        Document.version_count,
                        Document.is_active,
                        Document.archived,
                        Document.created_at,
                        Document.updated_at,
                        Document.creator_subject_id,
                        Document.tidy_verdict,
                        Document.source_email_uid,
                        Document.source_email_folder,
                        Document.source_email_account_id,
                        Document.source_email_message_id,
                        Document.source_kind,
                        Document.source_ref_hash,
                    )
                ).populate_existing().filter(Document.id == expected.document_id)
                fresh = scope_document_bound_query(fresh_query, lease).one_or_none()
                if fresh is None:
                    _fail("not_found")
                fresh_state = sqlalchemy_inspect(fresh)
                if fresh_state.persistent is not True or fresh_state.session is not session:
                    _fail("storage_failed")
                _, fresh_fingerprint = self._row_descriptor(
                    session,
                    fresh,
                    binding.creator_subject_id,
                    owner_binding,
                )
                if fresh_fingerprint != expected:
                    _fail("changed")
                content, content_bytes = _content(fresh.current_content)
                if (
                    len(content_bytes) != expected.byte_size
                    or len(content) != expected.char_size
                    or _SHA256_REF_PREFIX + hashlib.sha256(content_bytes).hexdigest() != expected.content_hash
                ):
                    _fail("changed")
                self._validate_factory_pin()
            finally:
                if session is not None:
                    self._close_session(session)
                self._validate_factory_pin()
        if content is None or self._current_descriptor_seal(descriptor) is not seal:
            _fail("changed")
        return DocumentExactRead(
            DOCUMENT_OWNER_READER_SCHEMA,
            expected.source_ref,
            expected.revision_ref,
            content,
            expected.content_hash,
            expected.descriptor_digest,
            expected.byte_size,
            expected.char_size,
        )


__all__ = [
    "DOCUMENT_OWNER_READER_SCHEMA",
    "MAX_DOCUMENTS",
    "MAX_DOCUMENT_BYTES",
    "MAX_LANGUAGE_UTF8_BYTES",
    "MAX_METADATA_BYTES",
    "MAX_TITLE_UTF8_BYTES",
    "MAX_TOTAL_DOCUMENT_BYTES",
    "DocumentExactRead",
    "DocumentOwnerDescriptor",
    "DocumentOwnerReader",
    "DocumentOwnerReaderError",
    "DocumentOwnerSnapshot",
]

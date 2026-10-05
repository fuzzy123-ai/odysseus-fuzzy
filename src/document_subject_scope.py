"""Sealed browser authority for subject-bound Document operations.

This module is intentionally inert: routes opt in explicitly, and no source or
runtime registers it during import.  Mutable aliases remain a compatibility
boundary for legacy ``creator_subject_id IS NULL`` rows only.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import threading
from typing import Any, Iterator, NoReturn
from weakref import WeakKeyDictionary

from fastapi import HTTPException, Request
from sqlalchemy import and_, false, inspect as sqlalchemy_inspect, or_
from sqlalchemy.orm import Query

from core.auth import AuthManager
from core.database import Document
from src.auth_helpers import (
    SessionPrincipal,
    guard_trusted_session_principal,
    require_trusted_session_principal,
)


class DocumentRequestAuthority:
    """Opaque authority captured from one real browser request."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> "DocumentRequestAuthority":
        raise TypeError("DocumentRequestAuthority cannot be constructed directly")

    def __repr__(self) -> str:
        return "<DocumentRequestAuthority redacted>"


class DocumentAuthorityLease:
    """Ephemeral authority valid only inside its guard context."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> "DocumentAuthorityLease":
        raise TypeError("DocumentAuthorityLease cannot be constructed directly")

    def __repr__(self) -> str:
        return "<DocumentAuthorityLease redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class _AuthoritySeal:
    request: Request
    principal: SessionPrincipal
    manager: AuthManager | None


@dataclass(frozen=True, slots=True, repr=False)
class _LeaseSeal:
    authority: DocumentRequestAuthority
    principal: SessionPrincipal
    issuing_thread_id: int


@dataclass(frozen=True, slots=True, repr=False)
class _DocumentSubjectBinding:
    owner: str
    creator_subject_id: str | None
    local: bool


_SEAL_LOCK = threading.RLock()
_AUTHORITY_SEALS: WeakKeyDictionary[DocumentRequestAuthority, _AuthoritySeal] = WeakKeyDictionary()
_LEASE_SEALS: WeakKeyDictionary[DocumentAuthorityLease, _LeaseSeal] = WeakKeyDictionary()
_STAMPED_DOCUMENTS: WeakKeyDictionary[Document, bool] = WeakKeyDictionary()


def _unauthenticated() -> NoReturn:
    raise HTTPException(status_code=401, detail="Not authenticated")


def _new_opaque(cls: type[Any]) -> Any:
    return object.__new__(cls)


def _request_manager(request: Request) -> AuthManager | None:
    try:
        manager = getattr(request.app.state, "auth_manager", None)
    except Exception:
        _unauthenticated()
    if manager is not None and type(manager) is not AuthManager:
        _unauthenticated()
    return manager


def capture_document_request_authority(request: Request) -> DocumentRequestAuthority:
    """Capture only the existing strict browser/local request authority."""

    if type(request) is not Request:
        _unauthenticated()
    manager = _request_manager(request)
    try:
        principal = require_trusted_session_principal(request)
    except HTTPException:
        raise
    except Exception:
        _unauthenticated()
    if type(principal) is not SessionPrincipal:
        _unauthenticated()
    if principal.authority == "browser_session":
        if type(manager) is not AuthManager:
            _unauthenticated()
    elif principal.authority == "local":
        if manager is not None:
            _unauthenticated()
    else:
        _unauthenticated()
    authority = _new_opaque(DocumentRequestAuthority)
    with _SEAL_LOCK:
        _AUTHORITY_SEALS[authority] = _AuthoritySeal(request, principal, manager)
    return authority


def _authority_seal(authority: object) -> _AuthoritySeal:
    if type(authority) is not DocumentRequestAuthority:
        _unauthenticated()
    with _SEAL_LOCK:
        seal = _AUTHORITY_SEALS.get(authority)
    if seal is None or seal.request is None or type(seal.principal) is not SessionPrincipal:
        _unauthenticated()
    return seal


def _lease_seal(lease: object) -> _LeaseSeal:
    if type(lease) is not DocumentAuthorityLease:
        _unauthenticated()
    with _SEAL_LOCK:
        seal = _LEASE_SEALS.get(lease)
        authority_live = seal is not None and _AUTHORITY_SEALS.get(seal.authority) is not None
    if (
        seal is None
        or not authority_live
        or type(seal.principal) is not SessionPrincipal
        or seal.issuing_thread_id != threading.get_ident()
    ):
        _unauthenticated()
    return seal


@contextmanager
def guard_document_request_authority(
    authority: DocumentRequestAuthority,
) -> Iterator[DocumentAuthorityLease]:
    """Revalidate the captured request and hold auth across one sync DB phase."""

    seal = _authority_seal(authority)
    manager = _request_manager(seal.request)
    if manager is not seal.manager or (
        seal.principal.authority == "browser_session" and type(manager) is not AuthManager
    ) or (
        seal.principal.authority == "local" and manager is not None
    ):
        _unauthenticated()
    with guard_trusted_session_principal(seal.request, seal.principal) as principal:
        if type(principal) is not SessionPrincipal or principal != seal.principal:
            _unauthenticated()
        if _request_manager(seal.request) is not seal.manager:
            _unauthenticated()
        lease = _new_opaque(DocumentAuthorityLease)
        with _SEAL_LOCK:
            _LEASE_SEALS[lease] = _LeaseSeal(authority, principal, threading.get_ident())
        try:
            yield lease
        finally:
            with _SEAL_LOCK:
                _LEASE_SEALS.pop(lease, None)


def _document_subject_binding(lease: DocumentAuthorityLease) -> _DocumentSubjectBinding:
    """Private exact binding for the reader while a lease is active."""

    principal = _lease_seal(lease).principal
    if principal.authority == "browser_session":
        if not isinstance(principal.creator_subject_id, str):
            _unauthenticated()
        return _DocumentSubjectBinding(principal.owner, principal.creator_subject_id, False)
    if principal.authority == "local" and principal.creator_subject_id is None:
        return _DocumentSubjectBinding(principal.owner, None, True)
    _unauthenticated()


def _document_query(query: object, model: object) -> Query:
    if type(query) is not Query or model is not Document:
        _unauthenticated()
    return query


def scope_document_bound_query(
    query: Query,
    lease: DocumentAuthorityLease,
    model: type[Document] = Document,
) -> Query:
    """Restrict a query to current SID-bound rows; local authority sees none."""

    scoped = _document_query(query, model)
    binding = _document_subject_binding(lease)
    if binding.local:
        return scoped.filter(false())
    return scoped.filter(Document.creator_subject_id == binding.creator_subject_id)


def scope_document_ingress_key_query(
    query: Query,
    lease: DocumentAuthorityLease,
    *,
    local_owner: str | None,
    model: type[Document] = Document,
) -> Query:
    """Scope a new-ingress identity lookup without alias fallback."""

    scoped = _document_query(query, model)
    binding = _document_subject_binding(lease)
    if local_owner is not None and type(local_owner) is not str:
        _unauthenticated()
    if binding.local:
        return scoped.filter(
            and_(
                Document.creator_subject_id.is_(None),
                Document.owner == local_owner,
            )
        )
    return scoped.filter(Document.creator_subject_id == binding.creator_subject_id)


def scope_document_web_query(
    query: Query,
    lease: DocumentAuthorityLease,
    model: type[Document] = Document,
) -> Query:
    """Preserve legacy web access without allowing alias access to bound rows."""

    scoped = _document_query(query, model)
    binding = _document_subject_binding(lease)
    if binding.local:
        return scoped.filter(Document.creator_subject_id.is_(None))
    return scoped.filter(
        or_(
            Document.creator_subject_id == binding.creator_subject_id,
            and_(
                Document.creator_subject_id.is_(None),
                Document.owner == binding.owner,
            ),
        )
    )


def current_document_owner_alias(lease: DocumentAuthorityLease) -> str:
    """Return the held current alias for existing non-SID policy checks."""

    return _document_subject_binding(lease).owner


def stamp_new_document(
    document: Document,
    lease: DocumentAuthorityLease,
    *,
    local_owner: str | None,
) -> None:
    """Certify only one new transient Document from a held request lease."""

    binding = _document_subject_binding(lease)
    if type(document) is not Document or sqlalchemy_inspect(document).transient is not True:
        _unauthenticated()
    if document.creator_subject_id is not None:
        _unauthenticated()
    if local_owner is not None and type(local_owner) is not str:
        _unauthenticated()
    with _SEAL_LOCK:
        if _STAMPED_DOCUMENTS.get(document) is not None:
            _unauthenticated()
        _STAMPED_DOCUMENTS[document] = True
    if binding.local:
        document.owner = local_owner
        document.creator_subject_id = None
        return
    document.owner = binding.owner
    document.creator_subject_id = binding.creator_subject_id


__all__ = [
    "DocumentAuthorityLease",
    "DocumentRequestAuthority",
    "capture_document_request_authority",
    "current_document_owner_alias",
    "guard_document_request_authority",
    "scope_document_bound_query",
    "scope_document_ingress_key_query",
    "scope_document_web_query",
    "stamp_new_document",
]

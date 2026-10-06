"""MCP-only, recipient-bound metadata admission.

This module deliberately has no relationship with the cookie disclosure
binding.  It turns an already-live Foundation lease into a small immutable
value that may be used solely by the metadata capture ports.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
import struct
import threading
import weakref
from typing import Iterator

from src.mcp_context_authority import McpContextAuthority, McpContextReadLease, McpContextBinding

_SCHEMA = "odysseus.context.mcp_metadata_binding.v1"
_AUDIENCE = "authenticated_mcp_context_read"
_SCOPE = "mcp:harbour:context:read"
_LOCK = threading.RLock()
_ADMISSION_CAP = 64
_ROWS: dict[int, tuple[weakref.ReferenceType[object], weakref.ReferenceType[object], weakref.ReferenceType[object], weakref.ReferenceType[object], weakref.ReferenceType[object], weakref.ReferenceType[threading.Thread], object, int, int]] = {}


class McpContextMetadataAdmissionError(ValueError):
    """A forged, expired, or cross-thread metadata admission."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("invalid_binding")


def _admission_fail() -> None:
    raise McpContextMetadataAdmissionError() from None


@dataclass(frozen=True, slots=True, weakref_slot=True, repr=False)
class McpContextMetadataBinding:
    schema: str
    audience: str
    owner_sid: str
    chat_session_id: str
    recipient_token_id: str
    scope: str
    cache_generation: int
    security_revision: int
    app_epoch: str

    def __post_init__(self) -> None:
        strings = (self.schema, self.audience, self.owner_sid, self.chat_session_id,
                   self.recipient_token_id, self.scope, self.app_epoch)
        integers = (self.cache_generation, self.security_revision)
        if any(type(value) is not str for value in strings) or any(type(value) is not int for value in integers):
            raise ValueError("invalid_binding")
        if (self.schema != _SCHEMA or self.audience != _AUDIENCE or self.scope != _SCOPE
                or any(not value for value in (self.owner_sid, self.chat_session_id, self.recipient_token_id, self.app_epoch))
                or any(value < 0 for value in integers)):
            raise ValueError("invalid_binding")

    def canonical(self) -> dict[str, object]:
        return {"schema": self.schema, "audience": self.audience, "owner_sid": self.owner_sid,
                "chat_session_id": self.chat_session_id, "recipient_token_id": self.recipient_token_id,
                "scope": self.scope, "cache_generation": self.cache_generation,
                "security_revision": self.security_revision, "app_epoch": self.app_epoch}

    def __repr__(self) -> str:
        return "McpContextMetadataBinding(<redacted>)"


class _McpMetadataAdmission:
    __slots__ = ("__weakref__",)
    def __setattr__(self, name: str, value: object) -> None: raise AttributeError("metadata admission is immutable")
    def __init_subclass__(cls, **kw: object) -> None: raise TypeError("metadata admission cannot be subclassed")
    def __copy__(self): raise TypeError("metadata admission cannot be copied")
    def __deepcopy__(self, memo: object): raise TypeError("metadata admission cannot be copied")
    def __reduce__(self): raise TypeError("metadata admission cannot be pickled")
    def __reduce_ex__(self, protocol: object): raise TypeError("metadata admission cannot be pickled")
    def __repr__(self) -> str: return "<_McpMetadataAdmission>"


def _canonical(binding: McpContextBinding) -> tuple[object, ...]:
    values = (binding.owner_subject_id, binding.chat_session_id, binding.token_id, binding.scope,
              binding.cache_generation, binding.security_action_revision, binding.process_epoch, binding.audience)
    if values[3] != _SCOPE or values[7] != _AUDIENCE:
        _admission_fail()
    return values


def _metadata(binding: McpContextBinding) -> McpContextMetadataBinding:
    owner, chat, token, scope, cache, revision, epoch, audience = _canonical(binding)
    return McpContextMetadataBinding(_SCHEMA, audience, owner, chat, token, scope, cache, revision, epoch)


def _row(admission: object, metadata_binding: object) -> tuple[object, ...]:
    if type(admission) is not _McpMetadataAdmission or type(metadata_binding) is not McpContextMetadataBinding:
        _admission_fail()
    with _LOCK:
        row = _ROWS.get(id(admission))
    if row is None or row[0]() is not admission:
        _admission_fail()
    authority, lease, foundation, metadata, creator, canonical, thread_id, process_id, factory = (row[1](), row[2](), row[3](), row[4](), row[5](), row[6], row[7], row[8], row[9])
    if (type(authority) is not McpContextAuthority or type(lease) is not McpContextReadLease
            or type(foundation) is not McpContextBinding or metadata is not metadata_binding
            or creator is not threading.current_thread() or thread_id != threading.get_ident() or process_id != os.getpid()):
        _admission_fail()
    if authority._session_factory is not factory or authority._session_factory_pin is not factory:
        _admission_fail()
    return authority, lease, foundation, metadata, canonical


def _require_mcp_metadata_admission(admission: object, metadata_binding: object) -> McpContextBinding:
    authority, lease, foundation, metadata, canonical = _row(admission, metadata_binding)
    live = authority.recheck_lease(lease)
    if live is not foundation or lease.binding is not foundation or _canonical(live) != canonical or _metadata(live) != metadata:
        _admission_fail()
    return live


@contextmanager
def _open_mcp_metadata_admission(authority: object, lease: object) -> Iterator[tuple[_McpMetadataAdmission, McpContextMetadataBinding]]:
    if type(authority) is not McpContextAuthority or type(lease) is not McpContextReadLease:
        _admission_fail()
    admission = _McpMetadataAdmission()
    factory = authority._session_factory
    if factory is not authority._session_factory_pin:
        _admission_fail()
    with authority.live_lease(lease) as foundation:
        if foundation is not lease.binding:
            _admission_fail()
        metadata = _metadata(foundation)
        canonical = _canonical(foundation)
        with _LOCK:
            for key, row in tuple(_ROWS.items()):
                if any(reference() is None for reference in row[:6]):
                    _ROWS.pop(key, None)
            if authority._session_factory is not factory or authority._session_factory_pin is not factory:
                _admission_fail()
            if len(_ROWS) >= _ADMISSION_CAP or any(row[2]() is lease or row[3]() is foundation for row in _ROWS.values()):
                _admission_fail()
            key = id(admission)
            def cleanup(dead_ref: object, row_key: int = key) -> None:
                with _LOCK:
                    row = _ROWS.get(row_key)
                    if row is not None and row[0] is dead_ref:
                        _ROWS.pop(row_key, None)
            _ROWS[key] = (weakref.ref(admission, cleanup), weakref.ref(authority), weakref.ref(lease), weakref.ref(foundation), weakref.ref(metadata), weakref.ref(threading.current_thread()), canonical, threading.get_ident(), os.getpid(), factory)
        try:
            _require_mcp_metadata_admission(admission, metadata)
            yield admission, metadata
            _require_mcp_metadata_admission(admission, metadata)
        finally:
            with _LOCK:
                _ROWS.pop(id(admission), None)


def _mcp_recipient_digest(domain: object, metadata_binding: object, body: object) -> str:
    allowed = {
        "odysseus.context.mcp_metadata.forge.boundary.v1", "odysseus.context.mcp_metadata.forge.record.v1", "odysseus.context.mcp_metadata.forge.target.v1",
        "odysseus.context.mcp_metadata.planning.principal.v1", "odysseus.context.mcp_metadata.planning.boundary.v1", "odysseus.context.mcp_metadata.planning.record.v1", "odysseus.context.mcp_metadata.planning.target.v1",
        "odysseus.context.mcp_metadata.knowledge.principal.v1", "odysseus.context.mcp_metadata.knowledge.boundary.v1", "odysseus.context.mcp_metadata.knowledge.record.v1", "odysseus.context.mcp_metadata.knowledge.target.v1",
    }
    if type(domain) is not str or domain not in allowed:
        raise ValueError("invalid_context")
    if type(metadata_binding) is not McpContextMetadataBinding:
        raise ValueError("invalid_binding")
    try:
        encoded = json.dumps({"schema": "odysseus.context.mcp_metadata_digest.v1", "domain": domain,
                              "recipient": metadata_binding.canonical(), "body": body}, sort_keys=True,
                             separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except Exception:
        raise ValueError("invalid_context") from None
    frame = struct.pack(">I", len(domain)) + domain.encode("ascii") + struct.pack(">I", len(encoded)) + encoded
    return hashlib.sha256(frame).hexdigest()


__all__ = ["McpContextMetadataBinding", "McpContextMetadataAdmissionError", "_open_mcp_metadata_admission", "_require_mcp_metadata_admission", "_mcp_recipient_digest"]

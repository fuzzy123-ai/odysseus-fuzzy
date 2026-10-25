"""Standalone authority fences for the future MCP context read bridge.

Nothing in this module registers a tool, creates a token, grants a private
disclosure, converts a bearer to a cookie, or releases source content.  A
binding is issued only after the current persisted bearer row and owned chat
are rechecked under the real AuthManager authority lock.  A lease repeats the
same checks while it is live.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math
import os
import re
import threading
import time
import weakref
from typing import Any, Callable, Iterator

import bcrypt

from core.database import ApiToken, Session as DbSession, SessionLocal
from core.auth import SUBJECT_ID_PATTERN
from src.mcp_authenticated_dispatch import (
    McpAuthenticatedIdentity,
    McpBearerProof,
    _BEARER_PROOF_ISSUER,
)
from src.mcp_context_contract import MCP_CONTEXT_AUDIENCE, MCP_CONTEXT_SCOPE


_ERROR_CODES = {
    "unauthorized",
    "stale_authentication",
    "disabled",
    "context_unavailable",
    "cancelled",
    "timeout",
    "invalid_request",
}

_STATE_MISSING = object()
_PROOF_LOCK = threading.RLock()
_PROOFS: dict[int, tuple[weakref.ReferenceType["McpContextBearerProof"], str, float, weakref.ReferenceType[object] | None, tuple[str, str, str, str] | None, tuple[int, int]]] = {}
_PROOF_CAP = 64
_PER_SID_CAP = 8
_PENDING_TTL = 300.0
_METADATA_VERIFIER_ISSUER = object()
_METADATA_VERIFIER_REQUIRE = object()
_METADATA_VERIFIER_OPEN = object()
_METADATA_ADMISSION_CONTEXT: ContextVar[object | None] = ContextVar(
    "mcp_metadata_admission_context", default=None,
)
_CONTENT_PUBLICATION_ISSUER = object()
_CONTENT_PUBLICATION_CONTEXT: ContextVar[object | None] = ContextVar(
    "mcp_content_publication_context", default=None,
)
_CONTENT_PUBLICATION_OWNER_CONTEXT: ContextVar[object | None] = ContextVar(
    "mcp_content_publication_owner_context", default=None,
)
_CONTENT_PUBLICATION_LOCK = threading.RLock()
_CONTENT_PUBLICATIONS: dict[
    int,
    tuple[weakref.ReferenceType[object], tuple[object, ...], list[object]],
] = {}
_CONTENT_PUBLICATION_CAP = 64
_CONTENT_PUBLICATION_ENTRY = object()
_CONTENT_PUBLICATION_REUSE = object()
_CONTENT_PUBLICATION_FINAL = object()
_CONTENT_PUBLICATION_POISONED = object()
_CONTENT_PUBLICATION_CLOSED = object()


def _make_content_publication_guard(
    registry: dict[int, tuple[weakref.ReferenceType[object], tuple[object, ...], list[object]]] = _CONTENT_PUBLICATIONS,
    publication_context: ContextVar[object | None] = _CONTENT_PUBLICATION_CONTEXT,
    owner_context: ContextVar[object | None] = _CONTENT_PUBLICATION_OWNER_CONTEXT,
    entry: object = _CONTENT_PUBLICATION_ENTRY,
    reuse: object = _CONTENT_PUBLICATION_REUSE,
    final: object = _CONTENT_PUBLICATION_FINAL,
    poisoned_state: object = _CONTENT_PUBLICATION_POISONED,
    closed_state: object = _CONTENT_PUBLICATION_CLOSED,
    getpid: Callable[[], int] = os.getpid,
    current_thread: Callable[[], threading.Thread] = threading.current_thread,
    get_ident: Callable[[], int] = threading.get_ident,
):
    """Build the one private authority for active publication state."""
    private_lock = threading.RLock()
    owners: dict[tuple[object, object, object], object] = {}
    verifiers: dict[int, tuple[weakref.ReferenceType[object], object]] = {}
    bound_validator = None
    bound_recorder = None

    def owner_key() -> tuple[object, object, object]:
        return getpid(), current_thread(), get_ident()

    def poison_registered(verifier: object) -> None:
        try:
            with private_lock:
                registered = dict.get(verifiers, id(verifier))
                control = (
                    registered[1]
                    if type(registered) is tuple
                    and len(registered) == 2
                    and type(registered[0]) is weakref.ReferenceType
                    and registered[0]() is verifier
                    else None
                )
            if control is not None:
                control("poison")
        except BaseException:
            pass

    def poison_active() -> None:
        try:
            with private_lock:
                control = dict.get(owners, owner_key())
            if control is None:
                verifier = owner_context.get()
                if verifier is None:
                    verifier = publication_context.get()
                with private_lock:
                    registered = dict.get(verifiers, id(verifier))
                    control = (
                        registered[1]
                        if type(registered) is tuple
                        and len(registered) == 2
                        and type(registered[0]) is weakref.ReferenceType
                        and registered[0]() is verifier
                        else None
                    )
            if control is not None:
                control("poison")
        except BaseException:
            pass

    def register(
        verifier: object,
        anchors: tuple[object, ...],
        mirror_row: tuple[object, ...],
        capacity: int,
    ) -> bool:
        if (
            type(anchors) is not tuple
            or len(anchors) != 21
            or type(mirror_row) is not tuple
            or len(mirror_row) != 3
            or type(mirror_row[0]) is not weakref.ReferenceType
            or mirror_row[0]() is not verifier
            or type(mirror_row[2]) is not list
            or len(mirror_row[2]) != 2
            or type(capacity) is not int
            or capacity <= 0
        ):
            return False
        owner = owner_key()
        verifier_key = id(verifier)
        verifier_ref = weakref.ref(verifier)
        mirror_state = mirror_row[2]
        phase = entry
        cached_hash = None
        raw_anchor = anchors[14]
        poisoned = False
        immutable_handles: tuple[object, ...] | None = anchors
        closed = False

        def control(action: str, value: object = None):
            nonlocal phase, cached_hash, raw_anchor, poisoned
            nonlocal immutable_handles, closed
            with private_lock:
                active = (
                    not closed
                    and dict.get(owners, owner) is control
                    and (
                        type(dict.get(verifiers, verifier_key)) is tuple
                        and dict.get(verifiers, verifier_key)[0]() is not None
                        and dict.get(verifiers, verifier_key)[0]() is verifier_ref()
                        and dict.get(verifiers, verifier_key)[1] is control
                    )
                )
                if action == "poison":
                    if not closed:
                        poisoned = True
                        mirror_state[0] = poisoned_state
                        current = verifier_ref()
                        if current is not None:
                            try:
                                object.__setattr__(
                                    current,
                                    "_McpContentPublicationVerifier__state",
                                    "poisoned",
                                )
                            except BaseException:
                                pass
                    return None
                if action == "snapshot":
                    return (
                        phase, cached_hash, raw_anchor, poisoned,
                        immutable_handles, active, owner,
                    )
                if action == "seed":
                    if (
                        not active
                        or poisoned
                        or phase is not entry
                        or cached_hash is not None
                        or type(value) is not str
                    ):
                        poisoned = True
                        return False
                    cached_hash = value
                    phase = reuse
                    mirror_state[0] = reuse
                    mirror_state[1] = value
                    current = verifier_ref()
                    if current is not None:
                        object.__setattr__(
                            current,
                            "_McpContentPublicationVerifier__token_hash",
                            value,
                        )
                        object.__setattr__(
                            current,
                            "_McpContentPublicationVerifier__state",
                            "reuse",
                        )
                    return True
                if action == "finalize":
                    if (
                        not active
                        or poisoned
                        or phase is not reuse
                        or type(cached_hash) is not str
                    ):
                        poisoned = True
                        return False
                    phase = final
                    mirror_state[0] = final
                    current = verifier_ref()
                    if current is not None:
                        object.__setattr__(
                            current,
                            "_McpContentPublicationVerifier__state",
                            "final",
                        )
                    return True
                if action == "close":
                    if dict.get(owners, owner) is control:
                        dict.pop(owners, owner, None)
                    registered = dict.get(verifiers, verifier_key)
                    if (
                        type(registered) is tuple
                        and len(registered) == 2
                        and registered[1] is control
                    ):
                        dict.pop(verifiers, verifier_key, None)
                    phase = closed_state
                    cached_hash = None
                    raw_anchor = None
                    immutable_handles = None
                    closed = True
                    return True
                return None

        with private_lock:
            for stale_key, registered in tuple(verifiers.items()):
                if registered[0]() is None:
                    dict.pop(verifiers, stale_key, None)
            existing = dict.get(owners, owner)
            collision = (
                existing is not None
                or len(verifiers) >= capacity
                or dict.get(verifiers, verifier_key) is not None
                or dict.get(registry, verifier_key) is not mirror_row
            )
            if not collision:
                owners[owner] = control
                verifiers[verifier_key] = (verifier_ref, control)
        if collision:
            if existing is not None:
                existing("poison")
            return False
        return True

    def snapshot(verifier: object):
        with private_lock:
            registered = dict.get(verifiers, id(verifier))
            control = (
                registered[1]
                if type(registered) is tuple
                and len(registered) == 2
                and registered[0]() is verifier
                else None
            )
        return None if control is None else control("snapshot")

    def seed(verifier: object, persisted_hash: object) -> bool:
        with private_lock:
            registered = dict.get(verifiers, id(verifier))
            control = (
                registered[1]
                if type(registered) is tuple
                and len(registered) == 2
                and registered[0]() is verifier
                else None
            )
        return False if control is None else control("seed", persisted_hash) is True

    def finalize_scope(verifier: object) -> bool:
        with private_lock:
            registered = dict.get(verifiers, id(verifier))
            control = (
                registered[1]
                if type(registered) is tuple
                and len(registered) == 2
                and registered[0]() is verifier
                else None
            )
        return False if control is None else control("finalize") is True

    def close_scope(verifier: object) -> None:
        with private_lock:
            registered = dict.get(verifiers, id(verifier))
            control = (
                registered[1]
                if type(registered) is tuple
                and len(registered) == 2
                and registered[0]() is verifier
                else None
            )
        if control is not None:
            control("close")

    def bind_dispatch(validator: object, recorder: object) -> None:
        nonlocal bound_validator, bound_recorder
        if (
            bound_validator is not None
            or bound_recorder is not None
            or not callable(validator)
            or not callable(recorder)
        ):
            raise RuntimeError("content publication guard already bound")
        bound_validator = validator
        bound_recorder = recorder

    def dispatch(action: str, *args: object):
        if action == "validate" and bound_validator is not None:
            return bound_validator(*args)
        if action == "record" and bound_recorder is not None:
            return bound_recorder(*args)
        raise RuntimeError("content publication guard unavailable")

    return (
        register, snapshot, seed, finalize_scope, close_scope,
        poison_registered, poison_active, bind_dispatch, dispatch,
    )


(
    _register_content_publication,
    _content_publication_control_snapshot,
    _seed_content_publication_hash,
    _finalize_content_publication,
    _close_content_publication_control,
    _poison_registered_content_publication,
    _poison_active_registered_content_publication,
    _bind_content_publication_dispatch,
    _content_publication_dispatch,
) = _make_content_publication_guard()
del _make_content_publication_guard

_poison_content_publication = _poison_registered_content_publication
_poison_active_content_publication = _poison_active_registered_content_publication


@dataclass(frozen=True, slots=True, repr=False)
class _NativeLeaseState:
    lock: object
    leases: dict[int, list[object]]
    cancelled_leases: dict[int, list[object]]


class _NativeLeaseStateSlot:
    __slots__ = ("__native_lease_state", "__weakref__")

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_NativeLeaseStateSlot__native_lease_state":
            raise AttributeError("native lease state is write-once")
        object.__setattr__(self, name, value)


_NATIVE_LEASE_STATE_SLOT = _NativeLeaseStateSlot.__dict__["_NativeLeaseStateSlot__native_lease_state"]
_NATIVE_LEASE_CANCELLED = object()


def _read_native_lease_state(
    authority: object,
    descriptor=_NATIVE_LEASE_STATE_SLOT,
) -> _NativeLeaseState:
    return descriptor.__get__(authority, _NativeLeaseStateSlot)


def _write_native_lease_state(
    authority: object,
    state: _NativeLeaseState,
    descriptor=_NATIVE_LEASE_STATE_SLOT,
) -> None:
    try:
        descriptor.__get__(authority, _NativeLeaseStateSlot)
    except AttributeError:
        descriptor.__set__(authority, state)
        return
    raise AttributeError("native lease state is write-once")


class McpContextAuthorityError(PermissionError):
    """Fresh fixed-code authority failure without identity or secret details."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in _ERROR_CODES else "unauthorized"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"McpContextAuthorityError({self.code!r})"


def _fail(
    code: str,
    _publication_poison: Callable[[], None] = _poison_active_registered_content_publication,
    /,
) -> None:
    _publication_poison()
    raise McpContextAuthorityError(code) from None


class McpContextBearerProof:
    """Opaque, final, weakrefable owner for an unchanged native proof."""
    __slots__ = ("__native", "__issuer", "__weakref__")
    def __init__(self, native: McpBearerProof, issuer: object) -> None:
        object.__setattr__(self, "_McpContextBearerProof__native", native)
        object.__setattr__(self, "_McpContextBearerProof__issuer", issuer)
    def __setattr__(self, _n: str, _v: object) -> None: raise AttributeError("context proof is immutable")
    def __init_subclass__(cls, **_kw: object) -> None: raise TypeError("context proof cannot be subclassed")
    def __repr__(self) -> str: return "<McpContextBearerProof redacted>"


def _proof_gc(key: int, ref: weakref.ReferenceType[object]) -> None:
    with _PROOF_LOCK:
        row = _PROOFS.get(key)
        if row is not None and row[0] is ref: _PROOFS.pop(key, None)

def _owner_gc(owner_ref: weakref.ReferenceType[object], method: str, key: int, ref: weakref.ReferenceType[object]) -> None:
    owner = owner_ref()
    if owner is not None:
        getattr(owner, method)(key, ref)


def issue_mcp_bearer_proof(raw_token: object) -> McpContextBearerProof:
    """Issue a request proof for an already supplied bearer value.

    This is only an immutable in-memory proof.  It never inserts or updates an
    ApiToken row and therefore cannot enable token issuance.
    """
    if type(raw_token) is not str or not raw_token.isascii() or not 8 <= len(raw_token) <= 512:
        _fail("unauthorized")
    if re.fullmatch(r"ody_[A-Za-z0-9_-]+", raw_token) is None:
        _fail("unauthorized")
    try:
        native = McpBearerProof(raw_token.encode("ascii"), _issuer=_BEARER_PROOF_ISSUER)
        proof = McpContextBearerProof(native, object())
        try:
            now = float(time.monotonic())
        except Exception:
            _fail("context_unavailable")
        if not math.isfinite(now): _fail("context_unavailable")
        with _PROOF_LOCK:
            for key, row in tuple(_PROOFS.items()):
                if (row[0]() is None or (row[3] is None and now - row[2] >= _PENDING_TTL)
                        or (row[1] == "claimed" and row[3] is not None and row[3]() is None)):
                    _PROOFS.pop(key, None)
            if len(_PROOFS) >= _PROOF_CAP: _fail("context_unavailable")
            key = id(proof); ref = weakref.ref(proof, lambda r, k=key: _proof_gc(k, r))
            _PROOFS[key] = (ref, "pending", now, None, None, (id(native), id(object.__getattribute__(proof, "_McpContextBearerProof__issuer"))))
        return proof
    except McpContextAuthorityError:
        raise
    except Exception:
        raise McpContextAuthorityError("unauthorized") from None


def release_mcp_bearer_proof(proof: object) -> None:
    """Release only an exact still-pending proof; claimed proofs belong to authority cleanup."""
    if type(proof) is not McpContextBearerProof:
        _fail("unauthorized")
    with _PROOF_LOCK:
        row = _PROOFS.get(id(proof))
        if row is None or row[0]() is not proof or row[1] != "pending":
            _fail("unauthorized")
        _PROOFS.pop(id(proof), None)


def _safe_request_id(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 256 or not value.isascii():
        _fail("invalid_request")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value) is None:
        _fail("invalid_request")
    return value


def _exact_nonnegative_int(value: object) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        _fail("unauthorized")
    return value


class McpContextBinding:
    """Manager-issued immutable proof of one bearer, owner, and chat."""

    __slots__ = (
        "__issuer", "__authority", "__proof", "__token_id", "__owner_alias",
        "__owner_subject_id", "__scope", "__cache_generation",
        "__security_action_revision", "__chat_session_id", "__process_epoch",
        "__audience",
        "__weakref__",
    )

    def __init__(
        self,
        token_id: str,
        owner_alias: str,
        owner_subject_id: str,
        scope: str,
        cache_generation: int,
        security_action_revision: int,
        chat_session_id: str,
        process_epoch: str,
        proof: McpBearerProof,
        *,
        audience: str = MCP_CONTEXT_AUDIENCE,
        _issuer: object,
    ) -> None:
        if _issuer is None:
            raise ValueError("invalid binding")
        object.__setattr__(self, "_McpContextBinding__issuer", _issuer)
        object.__setattr__(self, "_McpContextBinding__authority", _issuer)
        object.__setattr__(self, "_McpContextBinding__proof", proof)
        object.__setattr__(self, "_McpContextBinding__token_id", token_id)
        object.__setattr__(self, "_McpContextBinding__owner_alias", owner_alias)
        object.__setattr__(self, "_McpContextBinding__owner_subject_id", owner_subject_id)
        object.__setattr__(self, "_McpContextBinding__scope", scope)
        object.__setattr__(self, "_McpContextBinding__cache_generation", cache_generation)
        object.__setattr__(self, "_McpContextBinding__security_action_revision", security_action_revision)
        object.__setattr__(self, "_McpContextBinding__chat_session_id", chat_session_id)
        object.__setattr__(self, "_McpContextBinding__process_epoch", process_epoch)
        object.__setattr__(self, "_McpContextBinding__audience", audience)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("context binding is immutable")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("context binding cannot be subclassed")

    @property
    def token_id(self) -> str:
        return self.__token_id

    @property
    def owner_alias(self) -> str:
        return self.__owner_alias

    @property
    def owner_subject_id(self) -> str:
        return self.__owner_subject_id

    @property
    def scope(self) -> str:
        return self.__scope

    @property
    def cache_generation(self) -> int:
        return self.__cache_generation

    @property
    def security_action_revision(self) -> int:
        return self.__security_action_revision

    @property
    def chat_session_id(self) -> str:
        return self.__chat_session_id

    @property
    def process_epoch(self) -> str:
        return self.__process_epoch

    @property
    def bearer_proof(self) -> McpBearerProof:
        return self.__proof

    @property
    def audience(self) -> str:
        return self.__audience

    def __repr__(self) -> str:
        return "<McpContextBinding redacted>"


class McpContextReadLease:
    """Manager-issued live read lease; it carries no source content."""

    __slots__ = (
        "__issuer", "__authority", "__binding", "__request_id", "__issued_monotonic",
        "__deadline_monotonic", "__issued_wall", "__deadline_wall", "__weakref__",
    )

    def __init__(
        self,
        authority: "McpContextAuthority",
        binding: McpContextBinding,
        request_id: str,
        issued_monotonic: float,
        deadline_monotonic: float,
        issued_wall: float,
        deadline_wall: float,
        *,
        _issuer: object,
    ) -> None:
        if _issuer is None:
            raise ValueError("invalid lease")
        object.__setattr__(self, "_McpContextReadLease__issuer", _issuer)
        object.__setattr__(self, "_McpContextReadLease__authority", authority)
        object.__setattr__(self, "_McpContextReadLease__binding", binding)
        object.__setattr__(self, "_McpContextReadLease__request_id", request_id)
        object.__setattr__(self, "_McpContextReadLease__issued_monotonic", issued_monotonic)
        object.__setattr__(self, "_McpContextReadLease__deadline_monotonic", deadline_monotonic)
        object.__setattr__(self, "_McpContextReadLease__issued_wall", issued_wall)
        object.__setattr__(self, "_McpContextReadLease__deadline_wall", deadline_wall)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("context lease is immutable")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("context lease cannot be subclassed")

    @property
    def binding(self) -> McpContextBinding:
        return self.__binding

    @property
    def request_id(self) -> str:
        return self.__request_id

    @property
    def expires_at(self) -> float:
        return self.__deadline_wall

    def __repr__(self) -> str:
        return "<McpContextReadLease redacted>"


class _McpMetadataAdmissionVerifier:
    """One admission's private exact-hash reuse fence."""

    __slots__ = (
        "__issuer", "__authority_id", "__lease_id", "__binding_id",
        "__admission_id", "__proof_ids", "__values", "__thread_id",
        "__process_id", "__finalizer_id", "__token_hash", "__state", "__depth",
        "__weakref__",
    )

    def __init__(
        self,
        authority_id: int,
        lease_id: int,
        binding_id: int,
        admission_id: int,
        proof_ids: tuple[int, int, int],
        values: tuple[object, ...],
        thread_id: int,
        process_id: int,
        finalizer_id: int,
        *,
        _issuer: object,
    ) -> None:
        if _issuer is not _METADATA_VERIFIER_ISSUER:
            raise ValueError("invalid metadata verifier")
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__issuer", _issuer)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__authority_id", authority_id)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__lease_id", lease_id)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__binding_id", binding_id)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__admission_id", admission_id)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__proof_ids", proof_ids)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__values", values)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__thread_id", thread_id)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__process_id", process_id)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__finalizer_id", finalizer_id)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__token_hash", None)
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__state", "entry")
        object.__setattr__(self, "_McpMetadataAdmissionVerifier__depth", 0)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("metadata verifier is immutable")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("metadata verifier cannot be subclassed")

    def __copy__(self):
        raise TypeError("metadata verifier cannot be copied")

    def __deepcopy__(self, _memo: object):
        raise TypeError("metadata verifier cannot be copied")

    def __reduce__(self):
        raise TypeError("metadata verifier cannot be pickled")

    def __reduce_ex__(self, _protocol: object):
        raise TypeError("metadata verifier cannot be pickled")

    def __repr__(self) -> str:
        return "<_McpMetadataAdmissionVerifier redacted>"


class _McpContentPublicationVerifier:
    """One synchronous CONTENT publication's private exact-proof fence."""

    __slots__ = (
        "__issuer", "__authority", "__lease", "__binding", "__capability",
        "__native_state", "__native_lock", "__native_leases",
        "__native_cancelled_leases", "__native_key", "__native_row",
        "__wrapper", "__native", "__proof_issuer", "__raw",
        "__call_nonce", "__record_claim_token", "__values", "__thread",
        "__thread_id", "__process_id", "__token_hash", "__state", "__weakref__",
    )

    def __init__(self, *values: object, _issuer: object) -> None:
        if _issuer is not _CONTENT_PUBLICATION_ISSUER or len(values) != 21:
            raise ValueError("invalid content publication verifier")
        names = (
            "authority", "lease", "binding", "capability", "native_state",
            "native_lock", "native_leases", "native_cancelled_leases",
            "native_key", "native_row", "wrapper", "native", "proof_issuer",
            "raw", "call_nonce", "record_claim_token", "values", "thread",
            "thread_id", "process_id", "token_hash",
        )
        object.__setattr__(self, "_McpContentPublicationVerifier__issuer", _issuer)
        for name, value in zip(names, values):
            object.__setattr__(
                self, "_McpContentPublicationVerifier__" + name, value,
            )
        object.__setattr__(self, "_McpContentPublicationVerifier__state", "entry")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("content publication verifier is immutable")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("content publication verifier cannot be subclassed")

    def __copy__(self):
        raise TypeError("content publication verifier cannot be copied")

    def __deepcopy__(self, _memo: object):
        raise TypeError("content publication verifier cannot be copied")

    def __reduce__(self):
        raise TypeError("content publication verifier cannot be pickled")

    def __reduce_ex__(self, _protocol: object):
        raise TypeError("content publication verifier cannot be pickled")

    def __repr__(self) -> str:
        return "<_McpContentPublicationVerifier redacted>"


def _metadata_verifier_field(
    verifier: object,
    name: str,
) -> object:
    try:
        return object.__getattribute__(
            verifier, "_McpMetadataAdmissionVerifier__" + name,
        )
    except Exception:
        _fail("unauthorized")


def _metadata_verifier_handles_match(
    verifier: object,
    authority: object,
    lease: object,
    binding: object,
    admission: object,
) -> bool:
    return (
        type(verifier) is _McpMetadataAdmissionVerifier
        and _metadata_verifier_field(verifier, "issuer") is _METADATA_VERIFIER_ISSUER
        and _metadata_verifier_field(verifier, "authority_id") == id(authority)
        and _metadata_verifier_field(verifier, "lease_id") == id(lease)
        and _metadata_verifier_field(verifier, "binding_id") == id(binding)
        and _metadata_verifier_field(verifier, "admission_id") == id(admission)
        and _metadata_verifier_field(verifier, "thread_id") == threading.get_ident()
        and _metadata_verifier_field(verifier, "process_id") == os.getpid()
    )


def _new_mcp_metadata_admission_verifier(
    authority: object,
    lease: object,
    binding: object,
    admission: object,
    canonical: object,
    finalizer: object,
) -> _McpMetadataAdmissionVerifier:
    if (
        type(authority) is not McpContextAuthority
        or type(lease) is not McpContextReadLease
        or type(binding) is not McpContextBinding
        or type(finalizer) is not _LiveLease
        or lease.binding is not binding
        or finalizer._authority is not authority
        or finalizer._lease is not lease
        or type(canonical) is not tuple
        or len(canonical) != 8
    ):
        _fail("unauthorized")
    expected = (
        binding.owner_subject_id, binding.chat_session_id, binding.token_id,
        binding.scope, binding.cache_generation,
        binding.security_action_revision, binding.process_epoch,
        binding.audience,
    )
    if canonical != expected:
        _fail("unauthorized")
    wrapper = binding.bearer_proof
    try:
        native = object.__getattribute__(wrapper, "_McpContextBearerProof__native")
        proof_issuer = object.__getattribute__(wrapper, "_McpContextBearerProof__issuer")
    except Exception:
        _fail("unauthorized")
    if type(native) is not McpBearerProof:
        _fail("unauthorized")
    values = (
        binding.token_id, binding.owner_alias, binding.owner_subject_id,
        binding.cache_generation, binding.security_action_revision,
        binding.chat_session_id, binding.process_epoch,
    )
    return _McpMetadataAdmissionVerifier(
        id(authority), id(lease), id(binding), id(admission),
        (id(wrapper), id(native), id(proof_issuer)), values,
        threading.get_ident(), os.getpid(), id(finalizer),
        _issuer=_METADATA_VERIFIER_ISSUER,
    )


@contextmanager
def _hold_mcp_metadata_admission_verifier(
    verifier: object,
    authority: object,
    lease: object,
    binding: object,
    admission: object,
    *,
    _mode: object = _METADATA_VERIFIER_REQUIRE,
) -> Iterator[None]:
    if not _metadata_verifier_handles_match(
        verifier, authority, lease, binding, admission,
    ):
        _fail("unauthorized")
    depth = _metadata_verifier_field(verifier, "depth")
    state = _metadata_verifier_field(verifier, "state")
    parent = _METADATA_ADMISSION_CONTEXT.get()
    opening = _mode is _METADATA_VERIFIER_OPEN
    if _mode is _METADATA_VERIFIER_REQUIRE:
        if (
            type(parent) is not tuple
            or len(parent) != 6
            or parent[0] is not verifier
            or parent[1] is not authority
            or parent[2] is not lease
            or parent[3] is not binding
            or parent[4] is not admission
            or type(depth) is not int
            or depth <= 0
            or state == "closed"
        ):
            _fail("unauthorized")
        frame = parent
    elif opening:
        if (
            type(depth) is not int
            or depth != 0
            or state != "entry"
            or _metadata_verifier_field(verifier, "token_hash") is not None
        ):
            _fail("unauthorized")
        ancestor = parent
        seen = 0
        while ancestor is not None:
            if type(ancestor) is not tuple or len(ancestor) != 6:
                _fail("unauthorized")
            if (
                ancestor[0] is verifier
                or ancestor[2] is lease
                or ancestor[3] is binding
                or ancestor[4] is admission
            ):
                _fail("unauthorized")
            ancestor = ancestor[5]
            seen += 1
            if seen > 64:
                _fail("unauthorized")
        frame = (verifier, authority, lease, binding, admission, parent)
    else:
        _fail("unauthorized")
    prior_depth = depth
    object.__setattr__(verifier, "_McpMetadataAdmissionVerifier__depth", prior_depth + 1)
    token = _METADATA_ADMISSION_CONTEXT.set(frame)
    try:
        yield
    finally:
        _METADATA_ADMISSION_CONTEXT.reset(token)
        object.__setattr__(verifier, "_McpMetadataAdmissionVerifier__depth", prior_depth)
        if opening:
            object.__setattr__(
                verifier, "_McpMetadataAdmissionVerifier__token_hash", None,
            )
            object.__setattr__(
                verifier, "_McpMetadataAdmissionVerifier__state", "closed",
            )


@contextmanager
def _open_mcp_metadata_admission_verifier(
    verifier: object,
    authority: object,
    lease: object,
    binding: object,
    admission: object,
) -> Iterator[None]:
    with _hold_mcp_metadata_admission_verifier(
        verifier, authority, lease, binding, admission,
        _mode=_METADATA_VERIFIER_OPEN,
    ):
        yield


@dataclass(frozen=True, slots=True, repr=False)
class _NativeLeaseCapability:
    state: _NativeLeaseState
    lease: object
    key: int
    record: list[object]

    def terminal_reason(self) -> object | tuple[type[BaseException], str] | None:
        with self.state.lock:
            if self.record[0]() is not self.lease:
                _fail("unauthorized")
            reason = self.record[2]
            if reason is not None:
                return reason
            if self.state.leases.get(self.key) is not self.record:
                _fail("unauthorized")
            return None

    def require_live(self) -> None:
        reason = self.terminal_reason()
        if reason is None:
            return
        if reason is _NATIVE_LEASE_CANCELLED:
            _fail("cancelled")
        error_type, code = reason
        raise error_type(code) from None

    def cancel(self, reason: tuple[type[BaseException], str] | None = None) -> None:
        stored_reason: object = _NATIVE_LEASE_CANCELLED if reason is None else reason
        if reason is not None and (
            type(reason) is not tuple
            or len(reason) != 2
            or type(reason[0]) is not type
            or not issubclass(reason[0], BaseException)
            or type(reason[1]) is not str
        ):
            _fail("context_unavailable")
        with self.state.lock:
            if self.record[0]() is not self.lease:
                _fail("unauthorized")
            live_record = self.state.leases.get(self.key)
            cancelled_record = self.state.cancelled_leases.get(self.key)
            if live_record is not self.record and cancelled_record is self.record:
                _fail("unauthorized")
            if live_record is not self.record and cancelled_record is not self.record:
                if self.record[2] is not None:
                    return
            if self.record[2] is None:
                self.record[2] = stored_reason
            if live_record is self.record:
                self.state.leases.pop(self.key, None)
                if len(self.state.cancelled_leases) >= 64:
                    _fail("context_unavailable")
                self.state.cancelled_leases[self.key] = self.record


def _acquire_native_lease_capability(
    authority: object,
    lease: object,
    state_reader=_read_native_lease_state,
) -> _NativeLeaseCapability:
    if type(authority) is not McpContextAuthority or type(lease) is not McpContextReadLease:
        _fail("unauthorized")
    try:
        state = state_reader(authority)
    except Exception:
        _fail("unauthorized")
    key = id(lease)
    with state.lock:
        record = state.leases.get(key)
        if record is None:
            record = state.cancelled_leases.get(key)
        if record is None or record[0]() is not lease:
            _fail("unauthorized")
    capability_type = _NativeLeaseCapability
    capability = capability_type.__new__(capability_type)
    capability_type.__init__(capability, state, lease, key, record)
    return capability


class McpContextAuthority(_NativeLeaseStateSlot):
    """Issue and recheck context bindings against current application state."""

    def __init__(
        self,
        auth_manager: object,
        session_factory: Callable[[], object] | None = None,
        *,
        app_state: object,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        if auth_manager is None or app_state is None:
            raise ValueError("invalid authority")
        if not callable(monotonic) or not callable(wall_time):
            raise ValueError("invalid authority")
        self._auth_manager = auth_manager
        self._session_factory = session_factory if session_factory is not None else SessionLocal
        self._session_factory_pin = self._session_factory
        self._app_state = app_state
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._binding_issuer = object()
        self._lease_issuer = object()
        native_lock = threading.RLock()
        native_leases: dict[int, list[object]] = {}
        native_cancelled_leases: dict[int, list[object]] = {}
        native_dict = object.__getattribute__(self, "__dict__")
        native_dict["_lock"] = native_lock
        native_dict["_leases"] = native_leases
        native_dict["_cancelled_leases"] = native_cancelled_leases
        _write_native_lease_state(
            self,
            _NativeLeaseState(native_lock, native_leases, native_cancelled_leases),
        )
        self._closed = False
        self._bindings: dict[int, tuple[weakref.ReferenceType[McpContextBinding], tuple[object, ...]]] = {}

    def _state_value(self, name: str) -> object:
        state = self._app_state
        if state is None:
            return _STATE_MISSING
        try:
            if type(state) is dict:
                return state[name] if name in state else _STATE_MISSING
            return getattr(state, name)
        except (AttributeError, KeyError):
            return _STATE_MISSING
        except Exception:
            _fail("stale_authentication")

    def _gates(self) -> None:
        plugin = self._state_value("mcp_context_plugin_enabled")
        allow = self._state_value("allow_context_reads")
        if type(plugin) is not bool or type(allow) is not bool or plugin is not True or allow is not True:
            _fail("disabled")

    def _cache(self, expected: int) -> None:
        current = self._state_value("_token_cache_generation")
        dirty = self._state_value("_token_cache_dirty")
        if type(current) is not int or isinstance(current, bool) or current != expected or dirty is not False:
            _fail("stale_authentication")

    def _epoch(self, expected: str) -> None:
        current = self._state_value("_process_epoch")
        if type(current) is not str or re.fullmatch(r"epoch_[0-9a-f]{32}", current) is None or current != expected:
            _fail("stale_authentication")

    def _live_snapshot(self) -> tuple[bool, bool, int, bool, str]:
        plugin = self._state_value("mcp_context_plugin_enabled")
        allow = self._state_value("allow_context_reads")
        cache = self._state_value("_token_cache_generation")
        dirty = self._state_value("_token_cache_dirty")
        epoch = self._state_value("_process_epoch")
        if type(plugin) is not bool or type(allow) is not bool or plugin is not True or allow is not True:
            _fail("disabled")
        if type(cache) is not int or isinstance(cache, bool) or cache < 0 or dirty is not False or type(epoch) is not str or re.fullmatch(r"epoch_[0-9a-f]{32}", epoch) is None:
            _fail("stale_authentication")
        return plugin, allow, cache, dirty, epoch

    @staticmethod
    def _identity_shape(identity: object) -> tuple[str, str, str, int, int, str]:
        if type(identity) is not McpAuthenticatedIdentity:
            _fail("unauthorized")
        try:
            token_id = identity.token_id
            alias = identity.owner_alias
            sid = identity.owner_subject_id
            cache = identity.cache_generation
            security = identity.security_action_revision
            scope = identity.scope
        except McpContextAuthorityError:
            raise
        except Exception:
            raise McpContextAuthorityError("unauthorized") from None
        if (
            type(token_id) is not str or not 1 <= len(token_id) <= 256
            or type(alias) is not str or not 1 <= len(alias) <= 256
            or type(sid) is not str or SUBJECT_ID_PATTERN.fullmatch(sid) is None
            or type(cache) is not int or isinstance(cache, bool) or cache < 0
            or type(security) is not int or isinstance(security, bool) or security < 0
            or scope != MCP_CONTEXT_SCOPE or type(scope) is not str
        ):
            _fail("unauthorized")
        return token_id, alias, sid, cache, security, scope

    def _proof_shape(self, proof: object, token_id: str, alias: str, sid: str) -> McpBearerProof:
        if type(proof) is not McpContextBearerProof:
            _fail("unauthorized")
        try:
            native = object.__getattribute__(proof, "_McpContextBearerProof__native")
            issuer = object.__getattribute__(proof, "_McpContextBearerProof__issuer")
            if type(native) is not McpBearerProof or native._is_issued() is not True: _fail("unauthorized")
            try:
                now = float(time.monotonic())
            except Exception:
                _fail("context_unavailable")
            if not math.isfinite(now):
                _fail("context_unavailable")
            with _PROOF_LOCK:
                row = _PROOFS.get(id(proof))
                if row is None or row[0]() is not proof or row[5] != (id(native), id(issuer)):
                    _fail("unauthorized")
                if row[1] == "pending" and (now < row[2] or now - row[2] >= _PENDING_TTL):
                    _PROOFS.pop(id(proof), None)
                    _fail("context_unavailable")
        except McpContextAuthorityError:
            raise
        except Exception:
            raise McpContextAuthorityError("unauthorized") from None
        return native

    def _row_and_chat(
        self,
        proof: McpBearerProof,
        token_id: str,
        alias: str,
        sid: str,
        cache: int,
        security: int,
        chat: str,
        epoch: str,
        _publication_context: ContextVar[object | None] = _CONTENT_PUBLICATION_CONTEXT,
        _publication_owner_context: ContextVar[object | None] = _CONTENT_PUBLICATION_OWNER_CONTEXT,
        _publication_poison: Callable[[], None] = _poison_active_registered_content_publication,
        _publication_dispatch: Callable[..., object] = _content_publication_dispatch,
        /,
    ) -> None:
        manager = self._auth_manager
        if self._session_factory is not self._session_factory_pin:
            _fail("stale_authentication")
        try:
            with manager.guard_current_admin_subject(sid) as current_alias:
                if type(current_alias) is not str or current_alias != alias:
                    _fail("unauthorized")
                current_security = manager.security_action_revision()
                if type(current_security) is not int or isinstance(current_security, bool) or current_security != security:
                    _fail("stale_authentication")
                db = self._session_factory()
                try:
                    row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
                    frame = _METADATA_ADMISSION_CONTEXT.get()
                    verifier = None
                    proof_mode = None
                    if frame is not None:
                        try:
                            if type(frame) is not tuple or len(frame) != 6:
                                _fail("unauthorized")
                            verifier = frame[0]
                            if type(verifier) is not _McpMetadataAdmissionVerifier:
                                _fail("unauthorized")
                            captured_lease = frame[2]
                            captured_binding = frame[3]
                            wrapper = captured_binding.bearer_proof
                            captured_native = object.__getattribute__(
                                wrapper, "_McpContextBearerProof__native",
                            )
                            if (
                                frame[1] is self
                                and type(captured_lease) is McpContextReadLease
                                and type(captured_binding) is McpContextBinding
                                and captured_lease.binding is captured_binding
                                and captured_native is proof
                                and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__issuer") is _METADATA_VERIFIER_ISSUER
                                and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__authority_id") == id(self)
                                and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__lease_id") == id(captured_lease)
                                and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__binding_id") == id(captured_binding)
                                and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__admission_id") == id(frame[4])
                                and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__thread_id") == threading.get_ident()
                                and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__process_id") == os.getpid()
                                and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__values") == (token_id, alias, sid, cache, security, chat, epoch)
                                and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__proof_ids") == (id(wrapper), id(proof), id(object.__getattribute__(wrapper, "_McpContextBearerProof__issuer")))
                            ):
                                state = object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__state")
                                if state in {"entry", "reuse", "final"}:
                                    proof_mode = state
                            if proof_mode is None:
                                _fail("unauthorized")
                        except McpContextAuthorityError:
                            raise
                        except Exception:
                            _fail("unauthorized")
                    publication = _publication_context.get()
                    publication_owner = _publication_owner_context.get()
                    if publication_owner is not None:
                        if publication is not publication_owner:
                            _fail("unauthorized")
                        publication = publication_owner
                    elif publication is not None:
                        _fail("unauthorized")
                    publication_mode = None
                    publication_hash = None
                    if publication is not None:
                        publication_mode, publication_hash = _publication_dispatch(
                            "validate",
                            publication, self, proof,
                            (token_id, alias, sid, cache, security, chat, epoch),
                            frame,
                        )
                    persisted_hash = row.token_hash if row is not None else None
                    proof_matches = False
                    if type(persisted_hash) is str:
                        if publication_mode is not None:
                            if publication_mode == "entry":
                                proof_matches = proof.matches_hash(persisted_hash) is True
                            elif publication_mode == "reuse":
                                proof_matches = publication_hash == persisted_hash
                            elif publication_mode == "final":
                                proof_matches = (
                                    publication_hash == persisted_hash
                                    and proof.matches_hash(persisted_hash) is True
                                )
                            if proof_mode in {"reuse", "final"}:
                                proof_matches = (
                                    proof_matches
                                    and object.__getattribute__(
                                        verifier,
                                        "_McpMetadataAdmissionVerifier__token_hash",
                                    ) == persisted_hash
                                )
                        elif proof_mode == "entry":
                            proof_matches = proof.matches_hash(persisted_hash) is True
                        elif proof_mode in {"reuse", "final"}:
                            proof_matches = object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__token_hash") == persisted_hash
                            if proof_mode == "final":
                                proof_matches = (
                                    proof.matches_hash(persisted_hash) is True
                                    and proof_matches
                                )
                        else:
                            proof_matches = proof.matches_hash(persisted_hash) is True
                    if (
                        row is None
                        or type(row.id) is not str
                        or row.id != token_id
                        or row.is_active is not True
                        or type(row.scopes) is not str
                        or row.scopes != MCP_CONTEXT_SCOPE
                        or type(row.owner) is not str
                        or row.owner != alias
                        or type(row.owner_subject_id) is not str
                        or row.owner_subject_id != sid
                        or type(row.token_hash) is not str
                        or proof_matches is not True
                    ):
                        _fail("unauthorized")
                    chat_row = db.query(DbSession).filter(DbSession.id == chat).first()
                    if chat_row is None or type(chat_row.owner) is not str or chat_row.owner != alias:
                        _fail("unauthorized")
                    if publication_mode == "entry":
                        _publication_dispatch(
                            "record", publication, persisted_hash,
                        )
                    if proof_mode == "entry":
                        object.__setattr__(
                            verifier,
                            "_McpMetadataAdmissionVerifier__token_hash",
                            persisted_hash,
                        )
                        object.__setattr__(
                            verifier,
                            "_McpMetadataAdmissionVerifier__state",
                            "reuse",
                        )
                finally:
                    db.close()
        except McpContextAuthorityError:
            _publication_poison()
            raise
        except Exception:
            _publication_poison()
            raise McpContextAuthorityError("unauthorized") from None

    def _recheck_values(
        self,
        proof: McpBearerProof,
        token_id: str,
        alias: str,
        sid: str,
        cache: int,
        security: int,
        chat: str,
        epoch: str,
    ) -> None:
        self._gates()
        self._epoch(epoch)
        self._cache(cache)
        self._row_and_chat(
            proof, token_id, alias, sid, cache, security, chat, epoch,
        )

    def _issue_binding(
        self,
        identity: McpAuthenticatedIdentity,
        proof: McpBearerProof,
        chat_session_id: str,
    ) -> McpContextBinding:
        """Issue one binding after revalidating every authority input."""
        token_id, alias, sid, cache, security, scope = self._identity_shape(identity)
        wrapper = proof
        proof = self._proof_shape(wrapper, token_id, alias, sid)
        if type(chat_session_id) is not str or not 1 <= len(chat_session_id) <= 256:
            _fail("invalid_request")
        before = self._live_snapshot()
        if before[2] != cache: _fail("stale_authentication")
        self._recheck_values(proof, token_id, alias, sid, cache, security, chat_session_id, before[4])
        # External validation is deliberately complete before the short atomic claim.
        after = self._live_snapshot()
        if after != before: _fail("stale_authentication")
        proof = self._proof_shape(wrapper, token_id, alias, sid)
        snapshot = (token_id, alias, sid, scope, cache, security, chat_session_id, before[4], MCP_CONTEXT_AUDIENCE)
        # Claim and registration are one critical transition.  A failed local
        # admission therefore leaves the proof pending (and never destroys a
        # concurrent winner), while no binding can observe an unclaimed proof.
        with _PROOF_LOCK:
            for stale_key, stale in tuple(_PROOFS.items()):
                if stale[1] == "claimed" and stale[3] is not None and stale[3]() is None:
                    _PROOFS.pop(stale_key, None)
            row = _PROOFS.get(id(wrapper))
            if (row is None or row[0]() is not wrapper or row[1] != "pending"
                    or row[5] != (id(object.__getattribute__(wrapper, "_McpContextBearerProof__native")), id(object.__getattribute__(wrapper, "_McpContextBearerProof__issuer")))):
                _fail("unauthorized")
            if sum(1 for item in _PROOFS.values() if item[1] == "claimed" and item[4] is not None and item[4][2] == sid) >= _PER_SID_CAP:
                _fail("context_unavailable")
            if self._closed or len(self._bindings) >= 64: _fail("context_unavailable")
            binding = McpContextBinding(token_id, alias, sid, scope, cache, security, chat_session_id, before[4], wrapper, audience=MCP_CONTEXT_AUDIENCE, _issuer=self._binding_issuer)
            key = id(binding)
            with self._lock:
                if self._closed or len(self._bindings) >= 64: _fail("context_unavailable")
                snapshot = snapshot + (id(wrapper), row[5][0], row[5][1])
                owner_ref = weakref.ref(self)
                self._bindings[key] = (weakref.ref(binding, lambda r, k=key, o=owner_ref: _owner_gc(o, "_binding_gc", k, r)), snapshot)
                _PROOFS[id(wrapper)] = (row[0], "claimed", row[2], weakref.ref(self), (token_id, alias, sid, chat_session_id), row[5])
        return binding

    @staticmethod
    def _discard_exact_pending_proof(wrapper: object) -> None:
        if type(wrapper) is not McpContextBearerProof:
            return
        with _PROOF_LOCK:
            row = _PROOFS.get(id(wrapper))
            if row is not None and row[0]() is wrapper and row[1] == "pending":
                _PROOFS.pop(id(wrapper), None)

    def issue_binding(
        self, identity: McpAuthenticatedIdentity, proof: McpBearerProof, chat_session_id: str,
    ) -> McpContextBinding:
        try:
            return self._issue_binding(identity, proof, chat_session_id)
        except BaseException:
            self._discard_exact_pending_proof(proof)
            raise

    def _binding_gc(self, key: int, ref: weakref.ReferenceType[object]) -> None:
        try:
            with _PROOF_LOCK:
                with self._lock:
                    row = self._bindings.get(key)
                    if row is None or row[0] is not ref:
                        return
                    snapshot = row[1]
                    self._bindings.pop(key, None)
                    for lease_key, lease_row in tuple(self._leases.items()):
                        if lease_row[1][0] == key:
                            self._leases.pop(lease_key, None)
                    for lease_key, lease_row in tuple(self._cancelled_leases.items()):
                        if lease_row[1][0] == key:
                            self._cancelled_leases.pop(lease_key, None)
                    proof_key, native_id, issuer_id = snapshot[9:12]
                    proof_row = _PROOFS.get(proof_key)
                    if (proof_row is not None and proof_row[1] == "claimed"
                            and proof_row[3] is not None and proof_row[3]() is self
                            and proof_row[4] == (snapshot[0], snapshot[1], snapshot[2], snapshot[6])
                            and proof_row[5] == (native_id, issuer_id)):
                        _PROOFS.pop(proof_key, None)
        except Exception:
            return

    def _lease_gc(self, key: int, ref: weakref.ReferenceType[object]) -> None:
        with self._lock:
            row = self._leases.get(key)
            if row is not None and row[0] is ref: self._leases.pop(key, None)
            row = self._cancelled_leases.get(key)
            if row is not None and row[0] is ref: self._cancelled_leases.pop(key, None)

    bind = issue_binding
    issue_context_binding = issue_binding

    def _binding_snapshot(
        self,
        binding: object,
        _binding_type: type = McpContextBinding,
        _wrapper_type: type = McpContextBearerProof,
        _binding_proof_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__proof"],
        _binding_token_id_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__token_id"],
        _binding_owner_alias_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__owner_alias"],
        _binding_owner_subject_id_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__owner_subject_id"],
        _binding_scope_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__scope"],
        _binding_cache_generation_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__cache_generation"],
        _binding_security_action_revision_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__security_action_revision"],
        _binding_chat_session_id_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__chat_session_id"],
        _binding_process_epoch_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__process_epoch"],
        _binding_audience_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__audience"],
        _wrapper_native_descriptor: object = McpContextBearerProof.__dict__["_McpContextBearerProof__native"],
        _wrapper_issuer_descriptor: object = McpContextBearerProof.__dict__["_McpContextBearerProof__issuer"],
        /,
    ) -> tuple[object, ...]:
        if type(binding) is not _binding_type:
            _fail("unauthorized")
        try:
            with self._lock: entry = self._bindings.get(id(binding))
            if entry is None or entry[0]() is not binding:
                _fail("unauthorized")
            wrapper = _binding_proof_descriptor.__get__(binding, _binding_type)
            if type(wrapper) is not _wrapper_type:
                _fail("unauthorized")
            native = _wrapper_native_descriptor.__get__(wrapper, _wrapper_type)
            issuer = _wrapper_issuer_descriptor.__get__(wrapper, _wrapper_type)
            values = (
                _binding_token_id_descriptor.__get__(binding, _binding_type),
                _binding_owner_alias_descriptor.__get__(binding, _binding_type),
                _binding_owner_subject_id_descriptor.__get__(binding, _binding_type),
                _binding_scope_descriptor.__get__(binding, _binding_type),
                _binding_cache_generation_descriptor.__get__(binding, _binding_type),
                _binding_security_action_revision_descriptor.__get__(
                    binding, _binding_type,
                ),
                _binding_chat_session_id_descriptor.__get__(binding, _binding_type),
                _binding_process_epoch_descriptor.__get__(binding, _binding_type),
                _binding_audience_descriptor.__get__(binding, _binding_type),
                id(wrapper), id(native), id(issuer),
            )
        except McpContextAuthorityError:
            raise
        except Exception:
            raise McpContextAuthorityError("unauthorized") from None
        if values != entry[1] or values[3] != MCP_CONTEXT_SCOPE or values[8] != MCP_CONTEXT_AUDIENCE:
            _fail("unauthorized")
        return values

    def recheck_binding(self, binding: McpContextBinding) -> None:
        values = self._binding_snapshot(binding)
        try:
            if (object.__getattribute__(binding, "_McpContextBinding__issuer") is not self._binding_issuer
                    or object.__getattribute__(binding, "_McpContextBinding__authority") is not self._binding_issuer):
                _fail("unauthorized")
        except McpContextAuthorityError:
            raise
        except Exception:
            _fail("unauthorized")
        wrapper = binding.bearer_proof
        with _PROOF_LOCK:
            claimed = _PROOFS.get(id(wrapper))
            if (claimed is None or claimed[0]() is not wrapper or claimed[1] != "claimed"
                    or claimed[3] is None or claimed[3]() is not self
                    or claimed[4] != (values[0], values[1], values[2], values[6])
                    or claimed[5] != values[10:12]):
                _fail("unauthorized")
        before = self._live_snapshot()
        if before[2] != values[4] or before[4] != values[7]: _fail("stale_authentication")
        native = self._proof_shape(wrapper, values[0], values[1], values[2])
        self._recheck_values(native, values[0], values[1], values[2], values[4], values[5], values[6], values[7])
        if self._live_snapshot() != before: _fail("stale_authentication")
        # External Auth/DB work ran without registry locks; fence ownership again.
        with _PROOF_LOCK:
            with self._lock:
                entry = self._bindings.get(id(binding))
                row = _PROOFS.get(id(wrapper))
                current = self._binding_snapshot(binding)
                if (entry is None or entry[0]() is not binding or row is None
                        or row[0]() is not wrapper or row[1] != "claimed"
                        or row[3] is None or row[3]() is not self
                        or current != values or current != entry[1]
                        or row[4] != (current[0], current[1], current[2], current[6])
                        or row[5] != current[10:12]):
                    _fail("unauthorized")

    require_current_binding = recheck_binding

    def issue_read_lease(
        self,
        binding: McpContextBinding,
        request_id: str,
        *,
        ttl_seconds: int | float = 300,
    ) -> McpContextReadLease:
        _safe_request_id(request_id)
        if type(ttl_seconds) not in {int, float} or isinstance(ttl_seconds, bool) or not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= 900:
            _fail("invalid_request")
        self.recheck_binding(binding)
        try:
            issued_mono = float(self._monotonic()); issued_wall = float(self._wall_time())
        except Exception: _fail("context_unavailable")
        if not math.isfinite(issued_mono) or not math.isfinite(issued_wall):
            _fail("context_unavailable")
        lease = McpContextReadLease(
            self,
            binding,
            request_id,
            issued_mono,
            issued_mono + float(ttl_seconds),
            issued_wall,
            issued_wall + float(ttl_seconds),
            _issuer=self._lease_issuer,
        )
        binding_snapshot = self._binding_snapshot(binding)
        snapshot = (id(binding), request_id, issued_mono, issued_mono + float(ttl_seconds), issued_wall, issued_wall + float(ttl_seconds), binding_snapshot[9:12])
        with self._lock:
            if self._closed or len(self._leases) >= 64 or any(item[1][0] == id(binding) for item in self._leases.values()): _fail("context_unavailable")
            key = id(lease)
            owner_ref = weakref.ref(self)
            self._leases[key] = [weakref.ref(lease, lambda r, k=key, o=owner_ref: _owner_gc(o, "_lease_gc", k, r)), snapshot, None]
        return lease

    issue_lease = issue_read_lease
    issue_context_read_lease = issue_read_lease

    def _lease_snapshot(self, lease: object) -> tuple[object, ...]:
        if type(lease) is not McpContextReadLease:
            _fail("unauthorized")
        try:
            if (object.__getattribute__(lease, "_McpContextReadLease__issuer") is not self._lease_issuer
                    or object.__getattribute__(lease, "_McpContextReadLease__authority") is not self):
                _fail("unauthorized")
            with self._lock: entry = self._leases.get(id(lease)) or self._cancelled_leases.get(id(lease))
            if entry is None or entry[0]() is not lease:
                _fail("unauthorized")
            values = (
                id(lease.binding),
                lease.request_id,
                lease._McpContextReadLease__issued_monotonic,
                lease._McpContextReadLease__deadline_monotonic,
                lease._McpContextReadLease__issued_wall,
                lease._McpContextReadLease__deadline_wall,
                self._binding_snapshot(lease.binding)[9:12],
            )
        except McpContextAuthorityError:
            raise
        except Exception:
            raise McpContextAuthorityError("unauthorized") from None
        if values != entry[1]:
            _fail("unauthorized")
        return values

    def recheck_lease(
        self,
        lease: McpContextReadLease,
        _publication_poison: Callable[[], None] = _poison_active_registered_content_publication,
        /,
    ) -> McpContextBinding:
        try:
            values = self._lease_snapshot(lease)
            with self._lock: entry = self._leases.get(id(lease))
            if entry is None:
                with self._lock:
                    marker = self._cancelled_leases.get(id(lease))
                if marker is not None and marker[0]() is lease:
                    _fail("cancelled")
                _fail("unauthorized")
            if entry[2] is not None:
                _fail("cancelled")
            try:
                now_mono = float(self._monotonic())
                now_wall = float(self._wall_time())
            except Exception:
                self.release_binding(lease.binding)
                raise McpContextAuthorityError("context_unavailable") from None
            if (not math.isfinite(now_mono) or not math.isfinite(now_wall) or now_mono < values[2] or now_wall < values[4]):
                self.release_binding(lease.binding); _fail("context_unavailable")
            if now_mono >= values[3] or now_wall >= values[5]:
                self.release_binding(lease.binding)
                _fail("timeout")
            self.recheck_binding(lease.binding)
            return lease.binding
        except McpContextAuthorityError:
            _publication_poison()
            raise
        except Exception:
            _publication_poison()
            raise McpContextAuthorityError("unauthorized") from None

    require_live_lease = recheck_lease

    def cancel_lease(self, lease: McpContextReadLease) -> None:
        _acquire_native_lease_capability(self, lease).cancel()

    def release_read_lease(self, lease: McpContextReadLease) -> None:
        if type(lease) is not McpContextReadLease: _fail("unauthorized")
        with self._lock:
            entry = self._leases.get(id(lease)) or self._cancelled_leases.get(id(lease))
            if entry is None or entry[0]() is not lease:
                _fail("unauthorized")
            self._leases.pop(id(lease), None)
            self._cancelled_leases.pop(id(lease), None)

    def release_binding(
        self,
        binding: McpContextBinding,
        _binding_type: type = McpContextBinding,
        _binding_proof_descriptor: object = McpContextBinding.__dict__["_McpContextBinding__proof"],
        /,
    ) -> None:
        if type(binding) is not _binding_type: _fail("unauthorized")
        self._binding_snapshot(binding)
        with self._lock:
            self._bindings.pop(id(binding), None)
            for key, row in tuple(self._leases.items()):
                if row[1][0] == id(binding): self._leases.pop(key, None)
            for key, row in tuple(self._cancelled_leases.items()):
                if row[1][0] == id(binding): self._cancelled_leases.pop(key, None)
        wrapper = _binding_proof_descriptor.__get__(binding, _binding_type)
        with _PROOF_LOCK:
            row = _PROOFS.get(id(wrapper))
            if row is not None and row[0]() is wrapper: _PROOFS.pop(id(wrapper), None)

    def close(self) -> None:
        with self._lock:
            wrappers = [row[0]() for row in self._bindings.values() if row[0]() is not None]
            self._closed = True; self._leases.clear(); self._bindings.clear()
            self._cancelled_leases.clear()
        with _PROOF_LOCK:
            for binding in wrappers:
                wrapper = binding.bearer_proof
                row = _PROOFS.get(id(wrapper))
                if row is not None and row[0]() is wrapper and row[3] is not None and row[3]() is self:
                    _PROOFS.pop(id(wrapper), None)

    cancel = cancel_lease

    def live_lease(self, lease: McpContextReadLease):
        return _LiveLease(self, lease)


class _LiveLease:
    __slots__ = ("_authority", "_lease", "_binding", "_native_capability")
    def __init__(self, authority: McpContextAuthority, lease: McpContextReadLease) -> None:
        self._authority, self._lease, self._binding = authority, lease, None
        self._native_capability = _acquire_native_lease_capability(authority, lease)
    def __enter__(self) -> McpContextBinding:
        self._native_capability.require_live()
        self._binding = self._authority.recheck_lease(self._lease)
        return self._binding
    def __exit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool:
        original = exc if type(exc) is McpContextAuthorityError else None
        code = None
        reason = None
        frame = _METADATA_ADMISSION_CONTEXT.get()
        verifier = frame[0] if type(frame) is tuple and len(frame) == 6 else None
        try:
            try:
                reason = self._native_capability.terminal_reason()
            except McpContextAuthorityError as error:
                code = error.code
            if reason is not None:
                if (
                    type(reason) is tuple
                    and exc is not None
                    and type(exc) is reason[0]
                    and object.__getattribute__(exc, "code") == reason[1]
                ):
                    exc.__cause__ = None
                    exc.__context__ = None
                    return False
                code = "cancelled"
            elif code is None:
                try:
                    if frame is not None:
                        if not (
                            type(frame) is tuple
                            and len(frame) == 6
                            and type(verifier) is _McpMetadataAdmissionVerifier
                            and frame[1] is self._authority
                            and frame[2] is self._lease
                            and frame[3] is self._binding
                            and self._lease.binding is self._binding
                            and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__issuer") is _METADATA_VERIFIER_ISSUER
                            and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__authority_id") == id(self._authority)
                            and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__lease_id") == id(self._lease)
                            and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__binding_id") == id(self._binding)
                            and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__admission_id") == id(frame[4])
                            and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__finalizer_id") == id(self)
                            and object.__getattribute__(verifier, "_McpMetadataAdmissionVerifier__state") == "reuse"
                        ):
                            _fail("unauthorized")
                        object.__setattr__(verifier, "_McpMetadataAdmissionVerifier__state", "final")
                    self._authority.recheck_lease(self._lease)
                except McpContextAuthorityError as error:
                    code = error.code
            if original is not None and reason is None:
                original.__cause__ = None; original.__context__ = None
                return False
            if code is None:
                return False
            exc_type = exc = tb = None
            error = McpContextAuthorityError(code)
            try:
                raise error from None
            finally:
                error.__cause__ = None
                error.__context__ = None
        finally:
            self._binding = None


def _content_publication_field(verifier: object, name: str) -> object:
    try:
        return object.__getattribute__(
            verifier, "_McpContentPublicationVerifier__" + name,
        )
    except Exception:
        _fail("unauthorized")


def _content_publication_registry_row(
    verifier: object,
    _verifier_type: type = _McpContentPublicationVerifier,
    _registry: dict[int, tuple[weakref.ReferenceType[object], tuple[object, ...], list[object]]] = _CONTENT_PUBLICATIONS,
    _lock: object = _CONTENT_PUBLICATION_LOCK,
    /,
) -> tuple[weakref.ReferenceType[object], tuple[object, ...], list[object]]:
    if type(verifier) is not _verifier_type:
        _fail("unauthorized")
    with _lock:
        row = dict.get(_registry, id(verifier))
    if (
        type(row) is not tuple
        or len(row) != 3
        or type(row[0]) is not weakref.ReferenceType
        or row[0]() is not verifier
        or type(row[1]) is not tuple
        or len(row[1]) != 21
        or type(row[2]) is not list
        or len(row[2]) != 2
    ):
        _fail("unauthorized")
    return row


def _content_publication_anchor_fields_match(
    verifier: object,
    anchors: tuple[object, ...],
    state: list[object],
    _entry: object = _CONTENT_PUBLICATION_ENTRY,
    _reuse: object = _CONTENT_PUBLICATION_REUSE,
    _final: object = _CONTENT_PUBLICATION_FINAL,
    _field: Callable[[object, str], object] = _content_publication_field,
    /,
) -> bool:
    try:
        (
            issuer, authority, lease, binding, capability, native_state,
            native_lock, native_leases, native_cancelled, native_key,
            native_row, wrapper, native, proof_issuer, raw, call_nonce,
            record_claim_token, values, thread, thread_id, process_id,
        ) = anchors
        external_state = state[0]
        state_name = (
            "entry" if external_state is _entry else
            "reuse" if external_state is _reuse else
            "final" if external_state is _final else None
        )
        token_hash = state[1]
        return (
            state_name is not None
            and _field(verifier, "issuer") is issuer
            and _field(verifier, "authority") is authority
            and _field(verifier, "lease") is lease
            and _field(verifier, "binding") is binding
            and _field(verifier, "capability") is capability
            and _field(verifier, "native_state") is native_state
            and _field(verifier, "native_lock") is native_lock
            and _field(verifier, "native_leases") is native_leases
            and _field(verifier, "native_cancelled_leases") is native_cancelled
            and _field(verifier, "native_key") == native_key
            and _field(verifier, "native_row") is native_row
            and _field(verifier, "wrapper") is wrapper
            and _field(verifier, "native") is native
            and _field(verifier, "proof_issuer") is proof_issuer
            and _field(verifier, "raw") is raw
            and _field(verifier, "call_nonce") is call_nonce
            and _field(verifier, "record_claim_token") is record_claim_token
            and _field(verifier, "values") is values
            and _field(verifier, "thread") is thread
            and _field(verifier, "thread_id") == thread_id
            and _field(verifier, "process_id") == process_id
            and _field(verifier, "token_hash") is token_hash
            and _field(verifier, "state") == state_name
        )
    except BaseException:
        return False


def _record_content_publication_hash(
    verifier: object,
    persisted_hash: object,
    _row: Callable[[object], tuple[weakref.ReferenceType[object], tuple[object, ...], list[object]]] = _content_publication_registry_row,
    _fields_match: Callable[[object, tuple[object, ...], list[object]], bool] = _content_publication_anchor_fields_match,
    _registry: dict[int, tuple[weakref.ReferenceType[object], tuple[object, ...], list[object]]] = _CONTENT_PUBLICATIONS,
    _lock: object = _CONTENT_PUBLICATION_LOCK,
    _entry: object = _CONTENT_PUBLICATION_ENTRY,
    _reuse: object = _CONTENT_PUBLICATION_REUSE,
    _poison: Callable[[object], None] = _poison_registered_content_publication,
    _snapshot: Callable[[object], object] = _content_publication_control_snapshot,
    _seed: Callable[[object, object], bool] = _seed_content_publication_hash,
    /,
) -> None:
    registry_row = _row(verifier)
    anchors = registry_row[1]
    state = registry_row[2]
    private = _snapshot(verifier)
    with _lock:
        if (
            dict.get(_registry, id(verifier)) is not registry_row
            or type(persisted_hash) is not str
            or state[0] is not _entry
            or state[1] is not None
            or type(private) is not tuple
            or len(private) != 7
            or private[0] is not _entry
            or private[1] is not None
            or private[2] is not anchors[14]
            or private[3] is not False
            or private[4] is not anchors
            or private[5] is not True
            or not _fields_match(verifier, anchors, state)
        ):
            _poison(verifier)
            _fail("unauthorized")
        if not _seed(verifier, persisted_hash):
            _poison(verifier)
            _fail("unauthorized")
        private = _snapshot(verifier)
        if (
            type(private) is not tuple
            or len(private) != 7
            or private[0] is not _reuse
            or private[1] is not persisted_hash
            or private[2] is not anchors[14]
            or private[3] is not False
            or private[4] is not anchors
            or private[5] is not True
            or state[0] is not _reuse
            or state[1] is not persisted_hash
            or not _fields_match(verifier, anchors, state)
        ):
            _poison(verifier)
            _fail("unauthorized")


_CONTENT_PUBLICATION_RAW_CLASS_PINS = tuple(
    (
        cls,
        tuple(
            (
                name,
                type.__getattribute__(cls, "__dict__").get(name, _STATE_MISSING),
            )
            for name in names
        ),
    )
    for cls, names in (
        (
            _NativeLeaseCapability,
            (
                "state", "lease", "key", "record", "terminal_reason",
                "require_live", "cancel", "__init__", "__new__",
                "__getattribute__", "__getattr__",
            ),
        ),
        (
            _NativeLeaseState,
            (
                "lock", "leases", "cancelled_leases", "__getattribute__",
                "__getattr__",
            ),
        ),
        (
            _NativeLeaseStateSlot,
            (
                "_NativeLeaseStateSlot__native_lease_state",
                "__getattribute__", "__getattr__",
            ),
        ),
        (
            McpContextReadLease,
            (
                "binding", "request_id", "expires_at",
                "_McpContextReadLease__issuer",
                "_McpContextReadLease__authority",
                "_McpContextReadLease__binding",
                "_McpContextReadLease__request_id",
                "_McpContextReadLease__issued_monotonic",
                "_McpContextReadLease__deadline_monotonic",
                "_McpContextReadLease__issued_wall",
                "_McpContextReadLease__deadline_wall",
                "__getattribute__", "__getattr__",
            ),
        ),
        (
            McpContextBinding,
            (
                "token_id", "owner_alias", "owner_subject_id", "scope",
                "cache_generation", "security_action_revision",
                "chat_session_id", "process_epoch", "bearer_proof", "audience",
                "_McpContextBinding__issuer", "_McpContextBinding__authority",
                "_McpContextBinding__proof", "_McpContextBinding__token_id",
                "_McpContextBinding__owner_alias",
                "_McpContextBinding__owner_subject_id",
                "_McpContextBinding__scope",
                "_McpContextBinding__cache_generation",
                "_McpContextBinding__security_action_revision",
                "_McpContextBinding__chat_session_id",
                "_McpContextBinding__process_epoch",
                "_McpContextBinding__audience",
                "__getattribute__", "__getattr__",
            ),
        ),
        (
            McpContextBearerProof,
            (
                "_McpContextBearerProof__native",
                "_McpContextBearerProof__issuer",
                "__getattribute__", "__getattr__",
            ),
        ),
        (
            McpBearerProof,
            (
                "_McpBearerProof__raw", "_McpBearerProof__issuer",
                "__getattribute__", "__getattr__", "_is_issued",
            ),
        ),
    )
)


def _content_publication_proof_mode(
    verifier: object,
    authority: object,
    proof: object,
    expected_values: tuple[object, ...],
    metadata_frame: object,
    _publication_context: ContextVar[object | None] = _CONTENT_PUBLICATION_CONTEXT,
    _owner_context: ContextVar[object | None] = _CONTENT_PUBLICATION_OWNER_CONTEXT,
    _row: Callable[[object], tuple[weakref.ReferenceType[object], tuple[object, ...], list[object]]] = _content_publication_registry_row,
    _fields_match: Callable[[object, tuple[object, ...], list[object]], bool] = _content_publication_anchor_fields_match,
    _entry: object = _CONTENT_PUBLICATION_ENTRY,
    _reuse: object = _CONTENT_PUBLICATION_REUSE,
    _final: object = _CONTENT_PUBLICATION_FINAL,
    _verifier_type: type = _McpContentPublicationVerifier,
    _issuer: object = _CONTENT_PUBLICATION_ISSUER,
    _poison: Callable[[object], None] = _poison_registered_content_publication,
    _snapshot: Callable[[object], object] = _content_publication_control_snapshot,
    _capability_state_descriptor: object = _NativeLeaseCapability.state,
    _capability_lease_descriptor: object = _NativeLeaseCapability.lease,
    _capability_key_descriptor: object = _NativeLeaseCapability.key,
    _capability_record_descriptor: object = _NativeLeaseCapability.record,
    _native_state_lock_descriptor: object = _NativeLeaseState.__dict__["lock"],
    _native_state_leases_descriptor: object = _NativeLeaseState.__dict__["leases"],
    _native_state_cancelled_descriptor: object = _NativeLeaseState.__dict__["cancelled_leases"],
    _native_state_type: type = _NativeLeaseState,
    _raw_class_pins: tuple[tuple[type, tuple[tuple[str, object], ...]], ...] = _CONTENT_PUBLICATION_RAW_CLASS_PINS,
    _raw_type_getattribute: object = type.__getattribute__,
    _missing: object = _STATE_MISSING,
    /,
) -> tuple[str, str | None]:
    try:
        if any(
            any(
                namespace.get(name, _missing) is not expected
                for name, expected in pin
            )
            for cls, pin in _raw_class_pins
            for namespace in (_raw_type_getattribute(cls, "__dict__"),)
        ):
            _fail("unauthorized")
        registry_row = _row(verifier)
        anchors = registry_row[1]
        external_state = registry_row[2]
        private = _snapshot(verifier)
        if (
            type(verifier) is not _verifier_type
            or _publication_context.get() is not verifier
            or _owner_context.get() is not verifier
            or type(private) is not tuple
            or len(private) != 7
            or private[3] is not False
            or private[4] is not anchors
            or private[5] is not True
            or not _fields_match(verifier, anchors, external_state)
            or type(authority) is not McpContextAuthority
            or type(expected_values) is not tuple
            or len(expected_values) != 7
        ):
            _fail("unauthorized")
        (
            issuer, captured_authority, lease, binding, capability,
            native_state, native_lock, native_leases, native_cancelled,
            native_key, native_row, wrapper, native, proof_issuer, raw,
            call_nonce, record_claim_token, values, thread, thread_id,
            process_id,
        ) = anchors
        status = private[0]
        token_hash = private[1]
        mode = (
            "entry" if status is _entry else
            "reuse" if status is _reuse else
            "final" if status is _final else None
        )
        if (
            issuer is not _issuer
            or captured_authority is not authority
            or type(lease) is not McpContextReadLease
            or type(binding) is not McpContextBinding
            or lease.binding is not binding
            or type(capability) is not _NativeLeaseCapability
            or _capability_state_descriptor.__get__(
                capability, _NativeLeaseCapability,
            ) is not native_state
            or _capability_lease_descriptor.__get__(
                capability, _NativeLeaseCapability,
            ) is not lease
            or _capability_key_descriptor.__get__(
                capability, _NativeLeaseCapability,
            ) != native_key
            or _capability_record_descriptor.__get__(
                capability, _NativeLeaseCapability,
            ) is not native_row
            or type(native_state) is not _native_state_type
            or _native_state_lock_descriptor.__get__(
                native_state, _native_state_type,
            ) is not native_lock
            or _native_state_leases_descriptor.__get__(
                native_state, _native_state_type,
            ) is not native_leases
            or _native_state_cancelled_descriptor.__get__(
                native_state, _native_state_type,
            ) is not native_cancelled
            or type(native_leases) is not dict
            or type(native_cancelled) is not dict
            or native_leases is native_cancelled
            or type(native_key) is not int
            or native_key != id(lease)
            or type(native_row) is not list
            or len(native_row) != 3
            or type(native_row[0]) is not weakref.ReferenceType
            or type(native_row[1]) is not tuple
            or type(wrapper) is not McpContextBearerProof
            or binding.bearer_proof is not wrapper
            or type(native) is not McpBearerProof
            or proof is not native
            or type(raw) is not bytes
            or type(call_nonce) is not object
            or type(record_claim_token) is not object
            or call_nonce is record_claim_token
            or thread is not threading.current_thread()
            or thread_id != threading.get_ident()
            or process_id != os.getpid()
            or private[2] is not raw
            or private[6] != (process_id, thread, thread_id)
            or external_state[0] is not status
            or external_state[1] is not token_hash
            or mode is None
        ):
            _fail("unauthorized")
        try:
            if (
                object.__getattribute__(wrapper, "_McpContextBearerProof__native")
                is not native
                or object.__getattribute__(
                    wrapper, "_McpContextBearerProof__issuer",
                ) is not proof_issuer
                or object.__getattribute__(native, "_McpBearerProof__issuer")
                is not _BEARER_PROOF_ISSUER
                or object.__getattribute__(native, "_McpBearerProof__raw") is not raw
            ):
                _fail("unauthorized")
        except McpContextAuthorityError:
            raise
        except Exception:
            _fail("unauthorized")
        if type(values) is not tuple or len(values) != 2:
            _fail("unauthorized")
        binding_values = authority._binding_snapshot(binding)
        lease_values = authority._lease_snapshot(lease)
        if (
            values != (binding_values, lease_values)
            or expected_values != (
                binding_values[0], binding_values[1], binding_values[2],
                binding_values[4], binding_values[5], binding_values[6],
                binding_values[7],
            )
        ):
            _fail("unauthorized")
        if metadata_frame is not None:
            if (
                type(metadata_frame) is not tuple
                or len(metadata_frame) != 6
                or metadata_frame[1] is not authority
                or metadata_frame[2] is not lease
                or metadata_frame[3] is not binding
                or type(metadata_frame[0]) is not _McpMetadataAdmissionVerifier
            ):
                _fail("unauthorized")
        with native_lock:
            if (
                dict.get(native_leases, native_key) is not native_row
                or dict.get(native_cancelled, native_key) is not None
                or native_row[0]() is not lease
                or native_row[2] is not None
            ):
                _fail("unauthorized")
        if (
            (mode == "entry" and token_hash is not None)
            or (mode in {"reuse", "final"} and type(token_hash) is not str)
        ):
            _fail("unauthorized")
        return mode, token_hash
    except McpContextAuthorityError:
        _poison(verifier)
        raise
    except BaseException:
        _poison(verifier)
        _fail("unauthorized")


_bind_content_publication_dispatch(
    _content_publication_proof_mode,
    _record_content_publication_hash,
)
del _bind_content_publication_dispatch


def _clear_content_publication(
    verifier: object,
    expected_row: object = None,
    _registry: dict[int, tuple[weakref.ReferenceType[object], tuple[object, ...], list[object]]] = _CONTENT_PUBLICATIONS,
    _lock: object = _CONTENT_PUBLICATION_LOCK,
    _closed: object = _CONTENT_PUBLICATION_CLOSED,
    _verifier_type: type = _McpContentPublicationVerifier,
    _poison: Callable[[object], None] = _poison_registered_content_publication,
    _close_private: Callable[[object], None] = _close_content_publication_control,
    /,
) -> None:
    if type(verifier) is not _verifier_type:
        return
    with _lock:
        row = dict.get(_registry, id(verifier))
        if expected_row is not None and row is not expected_row:
            _poison(verifier)
        if type(expected_row) is tuple and len(expected_row) == 3:
            state = expected_row[2]
            if type(state) is list and len(state) == 2:
                state[0] = _closed
                state[1] = None
        dict.pop(_registry, id(verifier), None)
    _close_private(verifier)
    for name in (
        "issuer", "authority", "lease", "binding", "capability", "native_state",
        "native_lock", "native_leases", "native_cancelled_leases",
        "native_key", "native_row", "wrapper", "native", "proof_issuer", "raw",
        "call_nonce", "record_claim_token", "values", "thread", "thread_id",
        "process_id", "token_hash",
    ):
        object.__setattr__(
            verifier, "_McpContentPublicationVerifier__" + name, None,
        )
    object.__setattr__(
        verifier, "_McpContentPublicationVerifier__state", "closed",
    )


@contextmanager
def _open_mcp_content_publication(
    authority: object,
    lease: object,
    binding: object,
    capability: object,
    call_nonce: object,
    record_claim_token: object,
    _publication_context: ContextVar[object | None] = _CONTENT_PUBLICATION_CONTEXT,
    _owner_context: ContextVar[object | None] = _CONTENT_PUBLICATION_OWNER_CONTEXT,
    _registry: dict[int, tuple[weakref.ReferenceType[object], tuple[object, ...], list[object]]] = _CONTENT_PUBLICATIONS,
    _lock: object = _CONTENT_PUBLICATION_LOCK,
    _capacity: int = _CONTENT_PUBLICATION_CAP,
    _entry: object = _CONTENT_PUBLICATION_ENTRY,
    _reuse: object = _CONTENT_PUBLICATION_REUSE,
    _final: object = _CONTENT_PUBLICATION_FINAL,
    _verifier_type: type = _McpContentPublicationVerifier,
    _issuer: object = _CONTENT_PUBLICATION_ISSUER,
    _state_reader: Callable[[object], _NativeLeaseState] = _read_native_lease_state,
    _fields_match: Callable[[object, tuple[object, ...], list[object]], bool] = _content_publication_anchor_fields_match,
    _poison: Callable[[object], None] = _poison_registered_content_publication,
    _clear: Callable[[object, object], None] = _clear_content_publication,
    _register: Callable[[object, tuple[object, ...], tuple[object, ...], int], bool] = _register_content_publication,
    _snapshot: Callable[[object], object] = _content_publication_control_snapshot,
    _finalize: Callable[[object], bool] = _finalize_content_publication,
    _capability_state_descriptor: object = _NativeLeaseCapability.state,
    _capability_lease_descriptor: object = _NativeLeaseCapability.lease,
    _capability_key_descriptor: object = _NativeLeaseCapability.key,
    _capability_record_descriptor: object = _NativeLeaseCapability.record,
    _native_state_lock_descriptor: object = _NativeLeaseState.__dict__["lock"],
    _native_state_leases_descriptor: object = _NativeLeaseState.__dict__["leases"],
    _native_state_cancelled_descriptor: object = _NativeLeaseState.__dict__["cancelled_leases"],
    _native_state_type: type = _NativeLeaseState,
    _raw_class_pins: tuple[tuple[type, tuple[tuple[str, object], ...]], ...] = _CONTENT_PUBLICATION_RAW_CLASS_PINS,
    _raw_type_getattribute: object = type.__getattribute__,
    _missing: object = _STATE_MISSING,
    /,
) -> Iterator[None]:
    verifier = None

    def raw_drift_fence() -> None:
        try:
            drifted = any(
                any(
                    namespace.get(name, _missing) is not expected
                    for name, expected in pin
                )
                for cls, pin in _raw_class_pins
                for namespace in (_raw_type_getattribute(cls, "__dict__"),)
            )
        except BaseException:
            drifted = True
        if drifted:
            if verifier is not None:
                _poison(verifier)
            _fail("unauthorized")

    raw_drift_fence()
    owner_ancestor = _owner_context.get()
    public_ancestor = _publication_context.get()
    if owner_ancestor is not None or public_ancestor is not None:
        ancestor = owner_ancestor if owner_ancestor is not None else public_ancestor
        _poison(ancestor)
        _fail("unauthorized")
    if (
        type(authority) is not McpContextAuthority
        or type(lease) is not McpContextReadLease
        or type(binding) is not McpContextBinding
        or lease.binding is not binding
        or type(capability) is not _NativeLeaseCapability
        or type(call_nonce) is not object
        or type(record_claim_token) is not object
        or call_nonce is record_claim_token
    ):
        _fail("unauthorized")
    try:
        native_state = _capability_state_descriptor.__get__(
            capability, _NativeLeaseCapability,
        )
        if type(native_state) is not _native_state_type:
            _fail("unauthorized")
        native_lock = _native_state_lock_descriptor.__get__(
            native_state, _native_state_type,
        )
        native_leases = _native_state_leases_descriptor.__get__(
            native_state, _native_state_type,
        )
        native_cancelled = _native_state_cancelled_descriptor.__get__(
            native_state, _native_state_type,
        )
        native_key = _capability_key_descriptor.__get__(
            capability, _NativeLeaseCapability,
        )
        native_row = _capability_record_descriptor.__get__(
            capability, _NativeLeaseCapability,
        )
        if (
            _capability_lease_descriptor.__get__(
                capability, _NativeLeaseCapability,
            ) is not lease
            or type(native_state) is not _native_state_type
            or native_state is not _state_reader(authority)
            or type(native_leases) is not dict
            or type(native_cancelled) is not dict
            or native_leases is native_cancelled
            or type(native_key) is not int
            or native_key != id(lease)
            or type(native_row) is not list
            or len(native_row) != 3
            or type(native_row[0]) is not weakref.ReferenceType
            or type(native_row[1]) is not tuple
        ):
            _fail("unauthorized")
        with native_lock:
            if (
                dict.get(native_leases, native_key) is not native_row
                or dict.get(native_cancelled, native_key) is not None
                or native_row[0]() is not lease
                or native_row[2] is not None
            ):
                _fail("unauthorized")
        wrapper = binding.bearer_proof
        native = object.__getattribute__(
            wrapper, "_McpContextBearerProof__native",
        )
        proof_issuer = object.__getattribute__(
            wrapper, "_McpContextBearerProof__issuer",
        )
        raw = object.__getattribute__(native, "_McpBearerProof__raw")
        if (
            type(wrapper) is not McpContextBearerProof
            or type(native) is not McpBearerProof
            or type(raw) is not bytes
            or object.__getattribute__(native, "_McpBearerProof__issuer")
            is not _BEARER_PROOF_ISSUER
        ):
            _fail("unauthorized")
        values = (
            authority._binding_snapshot(binding),
            authority._lease_snapshot(lease),
        )
        thread = threading.current_thread()
        thread_id = threading.get_ident()
        process_id = os.getpid()
        verifier = _verifier_type(
            authority, lease, binding, capability, native_state, native_lock,
            native_leases, native_cancelled, native_key, native_row, wrapper,
            native, proof_issuer, raw, call_nonce, record_claim_token, values,
            thread, thread_id, process_id, None,
            _issuer=_issuer,
        )
        anchors = (
            _issuer, authority, lease, binding, capability, native_state,
            native_lock, native_leases, native_cancelled, native_key,
            native_row, wrapper, native, proof_issuer, raw, call_nonce,
            record_claim_token, values, thread, thread_id, process_id,
        )
        external_state = [_entry, None]
        key = id(verifier)

        def cleanup(
            dead_ref: object,
            row_key: int = key,
            registry: object = _registry,
            registry_lock: object = _lock,
        ) -> None:
            with registry_lock:
                row = dict.get(registry, row_key)
                if row is not None and row[0] is dead_ref:
                    dict.pop(registry, row_key, None)

        verifier_ref = weakref.ref(verifier, cleanup)
        registry_row = (verifier_ref, anchors, external_state)
        with _lock:
            for stale_key, stale_row in tuple(_registry.items()):
                if (
                    type(stale_row) is tuple
                    and len(stale_row) == 3
                    and type(stale_row[0]) is weakref.ReferenceType
                    and stale_row[0]() is None
                ):
                    dict.pop(_registry, stale_key, None)
            if len(_registry) >= _capacity or dict.get(_registry, key) is not None:
                _fail("context_unavailable")
            _registry[key] = registry_row
        if not _register(verifier, anchors, registry_row, _capacity):
            with _lock:
                if dict.get(_registry, key) is registry_row:
                    dict.pop(_registry, key, None)
            _fail("unauthorized")
    except McpContextAuthorityError:
        raise
    except Exception:
        raise McpContextAuthorityError("unauthorized") from None
    owner_token = None
    publication_token = None
    try:
        owner_token = _owner_context.set(verifier)
        publication_token = _publication_context.set(verifier)
        live = authority.recheck_lease(lease)
        raw_drift_fence()
        private = _snapshot(verifier)
        if (
            _publication_context.get() is not verifier
            or _owner_context.get() is not verifier
            or live is not binding
            or type(private) is not tuple
            or len(private) != 7
            or private[0] is not _reuse
            or private[1] is not external_state[1]
            or private[2] is not raw
            or private[3] is not False
            or private[4] is not anchors
            or private[5] is not True
            or private[6] != (process_id, thread, thread_id)
            or external_state[0] is not _reuse
            or not _fields_match(verifier, anchors, external_state)
        ):
            _poison(verifier)
            _fail("unauthorized")
        try:
            yield
        finally:
            raw_drift_fence()
            with _lock:
                private = _snapshot(verifier)
                if (
                    dict.get(_registry, key) is not registry_row
                    or _publication_context.get() is not verifier
                    or _owner_context.get() is not verifier
                    or type(private) is not tuple
                    or len(private) != 7
                    or private[0] is not _reuse
                    or private[1] is not external_state[1]
                    or private[2] is not raw
                    or private[3] is not False
                    or private[4] is not anchors
                    or private[5] is not True
                    or private[6] != (process_id, thread, thread_id)
                    or external_state[0] is not _reuse
                    or type(external_state[1]) is not str
                    or not _fields_match(verifier, anchors, external_state)
                ):
                    _poison(verifier)
                    _fail("unauthorized")
                if not _finalize(verifier):
                    _poison(verifier)
                    _fail("unauthorized")
            live = authority.recheck_lease(lease)
            private = _snapshot(verifier)
            if (
                live is not binding
                or type(private) is not tuple
                or len(private) != 7
                or private[0] is not _final
                or private[1] is not external_state[1]
                or private[2] is not raw
                or private[3] is not False
                or private[4] is not anchors
                or private[5] is not True
                or private[6] != (process_id, thread, thread_id)
                or external_state[0] is not _final
                or not _fields_match(verifier, anchors, external_state)
            ):
                _poison(verifier)
                _fail("unauthorized")
    finally:
        try:
            _clear(verifier, registry_row)
        finally:
            try:
                if publication_token is not None:
                    _publication_context.reset(publication_token)
            finally:
                if owner_token is not None:
                    _owner_context.reset(owner_token)



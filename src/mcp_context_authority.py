"""Standalone authority fences for the future MCP context read bridge.

Nothing in this module registers a tool, creates a token, grants a private
disclosure, converts a bearer to a cookie, or releases source content.  A
binding is issued only after the current persisted bearer row and owned chat
are rechecked under the real AuthManager authority lock.  A lease repeats the
same checks while it is live.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
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


class McpContextAuthorityError(PermissionError):
    """Fresh fixed-code authority failure without identity or secret details."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in _ERROR_CODES else "unauthorized"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"McpContextAuthorityError({self.code!r})"


def _fail(code: str) -> None:
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


class McpContextAuthority:
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
        self._lock = threading.RLock()
        self._closed = False
        self._bindings: dict[int, tuple[weakref.ReferenceType[McpContextBinding], tuple[object, ...]]] = {}
        self._leases: dict[int, tuple[weakref.ReferenceType[McpContextReadLease], tuple[object, ...], bool]] = {}
        self._cancelled_leases: dict[int, tuple[weakref.ReferenceType[McpContextReadLease], tuple[object, ...], bool]] = {}

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
        security: int,
        chat: str,
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
                        or proof.matches_hash(row.token_hash) is not True
                    ):
                        _fail("unauthorized")
                    chat_row = db.query(DbSession).filter(DbSession.id == chat).first()
                    if chat_row is None or type(chat_row.owner) is not str or chat_row.owner != alias:
                        _fail("unauthorized")
                finally:
                    db.close()
        except McpContextAuthorityError:
            raise
        except Exception:
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
        self._row_and_chat(proof, token_id, alias, sid, security, chat)

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

    def _binding_snapshot(self, binding: object) -> tuple[object, ...]:
        if type(binding) is not McpContextBinding:
            _fail("unauthorized")
        try:
            with self._lock: entry = self._bindings.get(id(binding))
            if entry is None or entry[0]() is not binding:
                _fail("unauthorized")
            wrapper = binding.bearer_proof
            try:
                native = object.__getattribute__(wrapper, "_McpContextBearerProof__native")
                issuer = object.__getattribute__(wrapper, "_McpContextBearerProof__issuer")
            except Exception:
                _fail("unauthorized")
            values = (
                binding.token_id,
                binding.owner_alias,
                binding.owner_subject_id,
                binding.scope,
                binding.cache_generation,
                binding.security_action_revision,
                binding.chat_session_id,
                binding.process_epoch,
                binding.audience,
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
            self._leases[key] = (weakref.ref(lease, lambda r, k=key, o=owner_ref: _owner_gc(o, "_lease_gc", k, r)), snapshot, False)
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

    def recheck_lease(self, lease: McpContextReadLease) -> McpContextBinding:
        values = self._lease_snapshot(lease)
        with self._lock: entry = self._leases.get(id(lease))
        if entry is None:
            with self._lock:
                marker = self._cancelled_leases.get(id(lease))
            if marker is not None and marker[0]() is lease:
                _fail("cancelled")
            _fail("unauthorized")
        if entry[2] is True:
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

    require_live_lease = recheck_lease

    def cancel_lease(self, lease: McpContextReadLease) -> None:
        self._lease_snapshot(lease)
        with self._lock:
            entry = self._leases.get(id(lease))
            if entry is None or entry[0]() is not lease:
                _fail("unauthorized")
            if len(self._cancelled_leases) >= 64:
                # A retained cancelled handle cannot become a capacity bypass.
                self._leases.pop(id(lease), None)
                _fail("context_unavailable")
            # Keep only a weak cancelled marker: it prevents a retained exact
            # handle from becoming usable again, but consumes no live capacity.
            self._leases.pop(id(lease), None)
            self._cancelled_leases[id(lease)] = (entry[0], entry[1], True)

    def release_read_lease(self, lease: McpContextReadLease) -> None:
        if type(lease) is not McpContextReadLease: _fail("unauthorized")
        with self._lock:
            entry = self._leases.get(id(lease)) or self._cancelled_leases.get(id(lease))
            if entry is None or entry[0]() is not lease:
                _fail("unauthorized")
            self._leases.pop(id(lease), None)
            self._cancelled_leases.pop(id(lease), None)

    def release_binding(self, binding: McpContextBinding) -> None:
        if type(binding) is not McpContextBinding: _fail("unauthorized")
        self._binding_snapshot(binding)
        with self._lock:
            self._bindings.pop(id(binding), None)
            for key, row in tuple(self._leases.items()):
                if row[1][0] == id(binding): self._leases.pop(key, None)
            for key, row in tuple(self._cancelled_leases.items()):
                if row[1][0] == id(binding): self._cancelled_leases.pop(key, None)
        wrapper = binding.bearer_proof
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
    __slots__ = ("_authority", "_lease", "_binding")
    def __init__(self, authority: McpContextAuthority, lease: McpContextReadLease) -> None:
        self._authority, self._lease, self._binding = authority, lease, None
    def __enter__(self) -> McpContextBinding:
        self._binding = self._authority.recheck_lease(self._lease)
        return self._binding
    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        original = exc if type(exc) is McpContextAuthorityError else None
        code = None
        try:
            try:
                self._authority.recheck_lease(self._lease)
            except McpContextAuthorityError as error:
                code = error.code
            if original is not None:
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



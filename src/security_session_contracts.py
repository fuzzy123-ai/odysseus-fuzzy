"""Redacted, fail-closed contracts for typed session invalidation.

Request data describes an operation but cannot reconstruct its authority.  The
only offline authority registry is deliberately test-only and has no production
issuer implementation in this slice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import threading
from typing import Any, Protocol

from core.auth import AuthManager


SESSION_INVALIDATION_GATE = "security-incident-session-invalidation-go"
SESSION_INVALIDATION_ACTION_TYPE = "session_invalidate_prepare"
SESSION_INVALIDATION_SCOPE_SCHEMA = "odysseus.session_invalidation_scope.v1"
SESSION_INVALIDATION_RECEIPT_SCHEMA = "odysseus.session_invalidation_receipt.v1"
SESSION_INVALIDATION_READBACK_SCHEMA = "odysseus.session_invalidation_readback.v1"
MAX_ACCOUNT_SESSION_SET = 64
_ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_OPAQUE_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}:sha256:[0-9a-f]{64}$")
_STORE_CONSTRUCTION_SEAL = object()


class SecuritySessionContractError(ValueError):
    """Content-free rejection for an unsafe session-invalidation request."""


def _account(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not _ACCOUNT_RE.fullmatch(normalized):
        raise SecuritySessionContractError("session invalidation unavailable")
    return normalized


def _handle(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise SecuritySessionContractError("session invalidation unavailable")
    return value


def account_binding(account_id: Any) -> str:
    return "account:sha256:" + hashlib.sha256(_account(account_id).encode("utf-8")).hexdigest()


def _scope_fingerprint(kind: str, binding: str, session_handle: str | None, maximum: int) -> str:
    handle_binding = "set" if session_handle is None else hashlib.sha256(session_handle.encode("utf-8")).hexdigest()
    return "scope:sha256:" + hashlib.sha256("|".join((SESSION_INVALIDATION_SCOPE_SCHEMA, kind, binding, handle_binding, str(maximum))).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class SessionInvalidationScope:
    scope_kind: str
    account_binding: str
    scope_fingerprint: str
    max_sessions: int
    _account_id: str = field(repr=False, compare=False)
    _session_handle: str | None = field(default=None, repr=False, compare=False)
    schema: str = SESSION_INVALIDATION_SCOPE_SCHEMA

    @classmethod
    def single_session(cls, *, account_id: Any, session_handle: Any) -> "SessionInvalidationScope":
        account, handle = _account(account_id), _handle(session_handle)
        binding = account_binding(account)
        return cls("single_session", binding, _scope_fingerprint("single_session", binding, handle, 1), 1, account, handle)

    @classmethod
    def account_session_set(cls, *, account_id: Any, max_sessions: Any) -> "SessionInvalidationScope":
        account = _account(account_id)
        if isinstance(max_sessions, bool) or not isinstance(max_sessions, int) or not 1 <= max_sessions <= MAX_ACCOUNT_SESSION_SET:
            raise SecuritySessionContractError("session invalidation unavailable")
        binding = account_binding(account)
        return cls("account_session_set", binding, _scope_fingerprint("account_session_set", binding, None, max_sessions), max_sessions, account)

    def validate(self) -> None:
        if self.schema != SESSION_INVALIDATION_SCOPE_SCHEMA or self.scope_kind not in {"single_session", "account_session_set"}:
            raise SecuritySessionContractError("session invalidation unavailable")
        if not isinstance(self.account_binding, str) or not _OPAQUE_REF_RE.fullmatch(self.account_binding):
            raise SecuritySessionContractError("session invalidation unavailable")
        if not isinstance(self.scope_fingerprint, str) or not _OPAQUE_REF_RE.fullmatch(self.scope_fingerprint):
            raise SecuritySessionContractError("session invalidation unavailable")
        account = _account(self._account_id)
        if account_binding(account) != self.account_binding:
            raise SecuritySessionContractError("session invalidation unavailable")
        if self.scope_kind == "single_session":
            if self.max_sessions != 1 or self._session_handle is None or _scope_fingerprint(self.scope_kind, self.account_binding, self._session_handle, 1) != self.scope_fingerprint:
                raise SecuritySessionContractError("session invalidation unavailable")
        elif self._session_handle is not None or not isinstance(self.max_sessions, int) or not 1 <= self.max_sessions <= MAX_ACCOUNT_SESSION_SET or _scope_fingerprint(self.scope_kind, self.account_binding, None, self.max_sessions) != self.scope_fingerprint:
            raise SecuritySessionContractError("session invalidation unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class OperatorSessionProtection:
    account_binding: str
    _account_id: str = field(repr=False, compare=False)
    _session_handle: str = field(repr=False, compare=False)

    @classmethod
    def current_session(cls, *, account_id: Any, session_handle: Any) -> "OperatorSessionProtection":
        account = _account(account_id)
        return cls(account_binding(account), account, _handle(session_handle))

    def validate(self) -> None:
        if account_binding(_account(self._account_id)) != self.account_binding:
            raise SecuritySessionContractError("session invalidation unavailable")
        _handle(self._session_handle)


class IsolatedSessionTestAuthManager(AuthManager):
    """Exact ephemeral test-store type; marker identity is not a caller flag."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.__session_invalidation_test_store_marker = object()


class IsolatedSessionStore:
    """Sealed wrapper for exactly one marked temporary AuthManager."""

    __slots__ = ("__manager", "__marker")

    def __init__(self, manager: Any, marker: Any, seal: Any) -> None:
        if seal is not _STORE_CONSTRUCTION_SEAL:
            raise SecuritySessionContractError("session invalidation unavailable")
        self.__manager, self.__marker = manager, marker

    @classmethod
    def from_isolated_test_manager(cls, manager: Any) -> "IsolatedSessionStore":
        if type(manager) is not IsolatedSessionTestAuthManager:
            raise SecuritySessionContractError("session invalidation unavailable")
        marker = getattr(manager, "_IsolatedSessionTestAuthManager__session_invalidation_test_store_marker", None)
        if marker is None:
            raise SecuritySessionContractError("session invalidation unavailable")
        return cls(manager, marker, _STORE_CONSTRUCTION_SEAL)

    def _unwrap(self) -> IsolatedSessionTestAuthManager:
        marker = getattr(self.__manager, "_IsolatedSessionTestAuthManager__session_invalidation_test_store_marker", None)
        if type(self.__manager) is not IsolatedSessionTestAuthManager or marker is not self.__marker:
            raise SecuritySessionContractError("session invalidation unavailable")
        return self.__manager


def _request_binding(request: Any) -> str:
    try:
        request.validate()
        fields = (request.action_id, request.action_version, request.action_type, request.scope_fingerprint, request.policy_revision, request.policy_gate, request.timeout_seconds, request.idempotency_key, request.rollback_descriptor, request.expires_at)
    except Exception:
        raise SecuritySessionContractError("session invalidation unavailable") from None
    if request.action_type != SESSION_INVALIDATION_ACTION_TYPE or request.policy_gate != SESSION_INVALIDATION_GATE:
        raise SecuritySessionContractError("session invalidation unavailable")
    return hashlib.sha256("|".join(map(str, fields)).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SessionInvalidationReceipt:
    action_id: str
    action_version: int
    policy_gate: str
    request_binding: str
    scope_fingerprint: str
    receipt_ref: str
    verification_authority: str
    invalidated_count: int
    acknowledgement_received: bool = True
    execution_state: str = "acknowledged"
    verification_state: str = "not_verified"
    verified: bool = False
    raw_content_visible: bool = False
    schema: str = SESSION_INVALIDATION_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        if (
            self.schema != SESSION_INVALIDATION_RECEIPT_SCHEMA or self.policy_gate != SESSION_INVALIDATION_GATE
            or not isinstance(self.action_id, str) or not self.action_id or type(self.action_version) is not int or self.action_version < 1
            or not all(isinstance(value, str) and _OPAQUE_REF_RE.fullmatch(value) for value in (self.scope_fingerprint, self.receipt_ref, self.verification_authority))
            or not isinstance(self.request_binding, str) or not re.fullmatch(r"[0-9a-f]{64}", self.request_binding)
            or isinstance(self.invalidated_count, bool) or not isinstance(self.invalidated_count, int) or not 1 <= self.invalidated_count <= MAX_ACCOUNT_SESSION_SET
            or self.acknowledgement_received is not True or self.execution_state != "acknowledged" or self.verification_state != "not_verified" or self.verified is not False or self.raw_content_visible is not False
        ):
            raise SecuritySessionContractError("session invalidation unavailable")

    def projection(self) -> dict[str, Any]:
        return {"schema": self.schema, "status": "success", "reason": "session_invalidation_acknowledged", "action_id": self.action_id, "action_version": self.action_version, "policy_gate": self.policy_gate, "scope_fingerprint": self.scope_fingerprint, "receipt_ref": self.receipt_ref, "invalidated_count": self.invalidated_count, "execution_state": self.execution_state, "acknowledgement_received": True, "verification_state": "not_verified", "verified": False, "raw_content_visible": False}


@dataclass(frozen=True, slots=True)
class SessionInvalidationReadback:
    scope_fingerprint: str
    verification_ref: str
    target_state: str
    remaining_target_count: int
    verified: bool
    raw_content_visible: bool = False
    schema: str = SESSION_INVALIDATION_READBACK_SCHEMA

    def __post_init__(self) -> None:
        if (
            self.schema != SESSION_INVALIDATION_READBACK_SCHEMA or not isinstance(self.scope_fingerprint, str) or not _OPAQUE_REF_RE.fullmatch(self.scope_fingerprint)
            or not isinstance(self.verification_ref, str) or not _OPAQUE_REF_RE.fullmatch(self.verification_ref) or self.target_state not in {"invalidated", "not_invalidated", "unavailable"}
            or isinstance(self.remaining_target_count, bool) or not isinstance(self.remaining_target_count, int) or not 0 <= self.remaining_target_count <= MAX_ACCOUNT_SESSION_SET
            or self.verified is not (self.target_state == "invalidated") or self.raw_content_visible is not False
        ):
            raise SecuritySessionContractError("session invalidation unavailable")

    def projection(self) -> dict[str, Any]:
        return {"schema": self.schema, "status": "success" if self.verified else "blocked", "scope_fingerprint": self.scope_fingerprint, "verification_ref": self.verification_ref, "target_state": self.target_state, "remaining_target_count": self.remaining_target_count, "verified": self.verified, "raw_content_visible": False}


class SessionInvalidationExecutionAuthority:
    """Opaque empty authority; only an issuer-owned registry recognizes it."""

    __slots__ = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise SecuritySessionContractError("session invalidation unavailable")

    def __copy__(self) -> "SessionInvalidationExecutionAuthority":
        raise SecuritySessionContractError("session invalidation unavailable")

    def __deepcopy__(self, memo: dict[int, Any]) -> "SessionInvalidationExecutionAuthority":
        raise SecuritySessionContractError("session invalidation unavailable")

    def __reduce_ex__(self, protocol: int) -> object:
        raise SecuritySessionContractError("session invalidation unavailable")


class SessionInvalidationAuthorityIssuer(Protocol):
    """Future SIRP-06 composition contract; no production issuer exists here."""

    def issue_after_consumed_sirp06_authorization(self, *, request: Any, scope: SessionInvalidationScope) -> SessionInvalidationExecutionAuthority:
        ...


class _SessionInvalidationTestOnlyIssuer:
    """Identity marker required by the isolated offline test issuer."""


SESSION_INVALIDATION_TEST_ONLY_ISSUER = _SessionInvalidationTestOnlyIssuer()


@dataclass(slots=True)
class _AuthorityRecord:
    authority: SessionInvalidationExecutionAuthority
    action_id: str
    action_version: int
    policy_gate: str
    request_binding: str
    scope_fingerprint: str
    receipt_ref: str
    verification_authority: str
    store: IsolatedSessionStore
    invalidated_count: int = 0
    consumed: bool = False
    receipt: SessionInvalidationReceipt | None = None
    acknowledgement: Any = None


def _build_test_authority_api() -> tuple[Any, Any, Any, Any]:
    """Keep offline issuer state private and keyed by issued object identity."""

    records: dict[SessionInvalidationExecutionAuthority, _AuthorityRecord] = {}
    lock = threading.Lock()

    def issue(request: Any, scope: Any, store: Any, *, test_issuer: object) -> SessionInvalidationExecutionAuthority:
        if test_issuer is not SESSION_INVALIDATION_TEST_ONLY_ISSUER or type(store) is not IsolatedSessionStore or type(scope) is not SessionInvalidationScope:
            raise SecuritySessionContractError("session invalidation unavailable")
        store._unwrap(); scope.validate()
        binding = _request_binding(request)
        if request.scope_fingerprint != scope.scope_fingerprint:
            raise SecuritySessionContractError("session invalidation unavailable")
        authority = object.__new__(SessionInvalidationExecutionAuthority)
        receipt_seed = hashlib.sha256((binding + "|" + scope.scope_fingerprint + "|" + str(id(authority))).encode("utf-8")).hexdigest()
        with lock:
            records[authority] = _AuthorityRecord(authority, request.action_id, request.action_version, request.policy_gate, binding, scope.scope_fingerprint, "receipt:sha256:" + receipt_seed, "verification:sha256:" + hashlib.sha256((receipt_seed + "|verification").encode("utf-8")).hexdigest(), store)
        return authority

    def consume(authority: Any, request: Any, scope: Any, store: Any, invalidated_count: Any) -> SessionInvalidationReceipt:
        if type(authority) is not SessionInvalidationExecutionAuthority or type(store) is not IsolatedSessionStore or type(scope) is not SessionInvalidationScope:
            raise SecuritySessionContractError("session invalidation unavailable")
        store._unwrap(); scope.validate()
        binding = _request_binding(request)
        if isinstance(invalidated_count, bool) or not isinstance(invalidated_count, int) or not 1 <= invalidated_count <= MAX_ACCOUNT_SESSION_SET:
            raise SecuritySessionContractError("session invalidation unavailable")
        with lock:
            record = records.get(authority)
            if record is None or record.consumed or record.store is not store or (record.action_id, record.action_version, record.policy_gate, record.request_binding, record.scope_fingerprint) != (request.action_id, request.action_version, request.policy_gate, binding, scope.scope_fingerprint):
                raise SecuritySessionContractError("session invalidation unavailable")
            receipt = SessionInvalidationReceipt(record.action_id, record.action_version, record.policy_gate, record.request_binding, record.scope_fingerprint, record.receipt_ref, record.verification_authority, invalidated_count)
            record.invalidated_count, record.consumed, record.receipt = invalidated_count, True, receipt
            return receipt

    def bind_acknowledgement(receipt: Any, acknowledgement: Any) -> None:
        if type(receipt) is not SessionInvalidationReceipt:
            raise SecuritySessionContractError("session invalidation unavailable")
        with lock:
            record = next((item for item in records.values() if item.receipt is receipt), None)
            if record is None or record.acknowledgement is not None:
                raise SecuritySessionContractError("session invalidation unavailable")
            record.acknowledgement = acknowledgement

    def validate_readback(receipt: Any, acknowledgement: Any, scope: Any) -> _AuthorityRecord:
        if type(receipt) is not SessionInvalidationReceipt or type(scope) is not SessionInvalidationScope:
            raise SecuritySessionContractError("session invalidation unavailable")
        with lock:
            record = next((item for item in records.values() if item.receipt is receipt and item.acknowledgement is acknowledgement), None)
            if record is None or not record.consumed or scope.scope_fingerprint != record.scope_fingerprint:
                raise SecuritySessionContractError("session invalidation unavailable")
            if (receipt.action_id, receipt.action_version, receipt.policy_gate, receipt.request_binding, receipt.scope_fingerprint, receipt.receipt_ref, receipt.verification_authority, receipt.invalidated_count) != (record.action_id, record.action_version, record.policy_gate, record.request_binding, record.scope_fingerprint, record.receipt_ref, record.verification_authority, record.invalidated_count):
                raise SecuritySessionContractError("session invalidation unavailable")
            return record

    return issue, consume, bind_acknowledgement, validate_readback


issue_test_session_invalidation_authority, _consume_test_session_invalidation_authority, _bind_test_session_invalidation_acknowledgement, _validate_test_session_invalidation_readback = _build_test_authority_api()


__all__ = [
    "IsolatedSessionStore", "IsolatedSessionTestAuthManager", "MAX_ACCOUNT_SESSION_SET", "OperatorSessionProtection",
    "SESSION_INVALIDATION_ACTION_TYPE", "SESSION_INVALIDATION_GATE", "SESSION_INVALIDATION_TEST_ONLY_ISSUER",
    "SecuritySessionContractError", "SessionInvalidationAuthorityIssuer", "SessionInvalidationExecutionAuthority",
    "SessionInvalidationReadback", "SessionInvalidationReceipt", "SessionInvalidationScope", "account_binding",
    "issue_test_session_invalidation_authority",
]

"""Default-disabled adapter for isolated typed session invalidation tests only."""

from __future__ import annotations

import hashlib
import threading
from typing import Any

from src.security_session_contracts import (
    IsolatedSessionStore, MAX_ACCOUNT_SESSION_SET, OperatorSessionProtection,
    SESSION_INVALIDATION_ACTION_TYPE, SESSION_INVALIDATION_GATE, SessionInvalidationExecutionAuthority,
    SessionInvalidationReadback, SessionInvalidationReceipt, SessionInvalidationScope,
    SecuritySessionContractError, _bind_test_session_invalidation_acknowledgement,
    _consume_test_session_invalidation_authority, _validate_test_session_invalidation_readback,
)


class SessionInvalidationAcknowledgement(dict):
    """Redacted response mapping retaining only an in-process receipt identity."""

    __slots__ = ("__receipt",)

    def __init__(self, receipt: SessionInvalidationReceipt) -> None:
        super().__init__(receipt.projection())
        self.__receipt = receipt

    def _receipt_for_readback(self) -> SessionInvalidationReceipt:
        return self.__receipt


class SessionInvalidationExecutor:
    """Execute only exact, bounded scope through a sealed temporary-store adapter."""

    def __init__(self, store: Any, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = enabled is True
        self._lock = threading.RLock()

    def readiness(self, *, request: Any, scope: Any, operator_protection: Any, recovery: Any = None) -> dict[str, Any]:
        try:
            targets = self._preflight(request=request, scope=scope, operator_protection=operator_protection, recovery=recovery)
        except Exception:
            return self._blocked("session_invalidation_unavailable")
        return {"schema": "odysseus.session_invalidation_readiness.v1", "status": "ready", "scope_fingerprint": scope.scope_fingerprint, "scope_kind": scope.scope_kind, "target_count": len(targets), "mutation_performed": False, "raw_content_visible": False}

    def execute(self, *, request: Any, scope: Any, operator_protection: Any, capability: Any, recovery: Any = None) -> dict[str, Any]:
        try:
            with self._lock:
                targets = self._preflight(request=request, scope=scope, operator_protection=operator_protection, recovery=recovery)
                if not isinstance(capability, SessionInvalidationExecutionAuthority):
                    raise SecuritySessionContractError("session invalidation unavailable")
                # The issuer consumes the single-use authority before the store
                # can be mutated, and binds action/version/gate/scope/receipt.
                receipt = _consume_test_session_invalidation_authority(capability, request, scope, self._isolated_store(), len(targets))
                acknowledgement = SessionInvalidationAcknowledgement(receipt)
                _bind_test_session_invalidation_acknowledgement(receipt, acknowledgement)
                current = self._targets_locked(scope, operator_protection)
                if current != targets:
                    raise SecuritySessionContractError("session invalidation unavailable")
                manager = self._manager()
                for handle in current:
                    manager._sessions.pop(handle, None)
                manager._save_sessions()
            return acknowledgement
        except Exception:
            return self._blocked("session_invalidation_unavailable")

    def readback(self, *, receipt: Any, scope: Any, operator_protection: Any) -> dict[str, Any]:
        try:
            if not self._enabled or not isinstance(receipt, SessionInvalidationAcknowledgement) or not isinstance(scope, SessionInvalidationScope):
                raise SecuritySessionContractError("session invalidation unavailable")
            durable_receipt = receipt._receipt_for_readback()
            record = self._validate_receipt(durable_receipt, receipt, scope)
            scope.validate(); operator_protection.validate()
            with self._session_lock():
                self._validate_operator_locked(operator_protection)
                remaining = self._remaining_locked(scope)
            state = "invalidated" if remaining == 0 else "not_invalidated"
            readback = SessionInvalidationReadback(
                scope_fingerprint=scope.scope_fingerprint,
                verification_ref=self._verification_ref(record.verification_authority, durable_receipt, remaining),
                target_state=state, remaining_target_count=remaining, verified=remaining == 0,
            )
            return readback.projection()
        except Exception:
            return self._blocked("session_invalidation_unavailable")

    def _preflight(self, *, request: Any, scope: Any, operator_protection: Any, recovery: Any) -> tuple[str, ...]:
        if not self._enabled or recovery is not None or not isinstance(scope, SessionInvalidationScope) or not isinstance(operator_protection, OperatorSessionProtection):
            raise SecuritySessionContractError("session invalidation unavailable")
        self._isolated_store(); scope.validate(); operator_protection.validate()
        try:
            request.validate()
        except Exception:
            raise SecuritySessionContractError("session invalidation unavailable") from None
        if request.action_type != SESSION_INVALIDATION_ACTION_TYPE or request.policy_gate != SESSION_INVALIDATION_GATE or request.scope_fingerprint != scope.scope_fingerprint:
            raise SecuritySessionContractError("session invalidation unavailable")
        with self._session_lock():
            return self._targets_locked(scope, operator_protection)

    def _isolated_store(self) -> IsolatedSessionStore:
        if not isinstance(self._store, IsolatedSessionStore):
            raise SecuritySessionContractError("session invalidation unavailable")
        self._store._unwrap()
        return self._store

    def _manager(self):
        return self._isolated_store()._unwrap()

    def _session_lock(self):
        manager = self._manager()
        lock, sessions, saver = getattr(manager, "_sessions_lock", None), getattr(manager, "_sessions", None), getattr(manager, "_save_sessions", None)
        if not hasattr(lock, "__enter__") or not isinstance(sessions, dict) or not callable(saver):
            raise SecuritySessionContractError("session invalidation unavailable")
        return lock

    def _validate_operator_locked(self, operator: OperatorSessionProtection) -> None:
        session = self._manager()._sessions.get(operator._session_handle)
        if not isinstance(session, dict) or str(session.get("username") or "").strip().lower() != operator._account_id:
            raise SecuritySessionContractError("session invalidation unavailable")

    def _targets_locked(self, scope: SessionInvalidationScope, operator: OperatorSessionProtection) -> tuple[str, ...]:
        manager = self._manager(); self._validate_operator_locked(operator)
        if scope.scope_kind == "single_session":
            session = manager._sessions.get(scope._session_handle)
            if not isinstance(session, dict) or str(session.get("username") or "").strip().lower() != scope._account_id:
                raise SecuritySessionContractError("session invalidation unavailable")
            targets = (scope._session_handle,)
        else:
            targets = tuple(handle for handle, session in manager._sessions.items() if isinstance(handle, str) and isinstance(session, dict) and str(session.get("username") or "").strip().lower() == scope._account_id)
            if not targets or len(targets) > scope.max_sessions or len(targets) > MAX_ACCOUNT_SESSION_SET:
                raise SecuritySessionContractError("session invalidation unavailable")
        if operator._session_handle in targets:
            raise SecuritySessionContractError("session invalidation unavailable")
        return tuple(sorted(targets))

    def _remaining_locked(self, scope: SessionInvalidationScope) -> int:
        sessions = self._manager()._sessions
        if scope.scope_kind == "single_session":
            return int(scope._session_handle in sessions)
        remaining = sum(1 for session in sessions.values() if isinstance(session, dict) and str(session.get("username") or "").strip().lower() == scope._account_id)
        if remaining > scope.max_sessions or remaining > MAX_ACCOUNT_SESSION_SET:
            raise SecuritySessionContractError("session invalidation unavailable")
        return remaining

    @staticmethod
    def _validate_receipt(receipt: SessionInvalidationReceipt, acknowledgement: SessionInvalidationAcknowledgement, scope: SessionInvalidationScope):
        return _validate_test_session_invalidation_readback(receipt, acknowledgement, scope)

    @staticmethod
    def _verification_ref(authority: str, receipt: SessionInvalidationReceipt, remaining: int) -> str:
        body = "|".join((authority, receipt.action_id, str(receipt.action_version), receipt.request_binding, receipt.scope_fingerprint, receipt.receipt_ref, str(receipt.invalidated_count), str(remaining)))
        return "verification:sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()

    @staticmethod
    def _blocked(reason: str) -> dict[str, Any]:
        return {"status": "blocked", "reason": reason, "executed": False, "verified": False, "raw_content_visible": False}


__all__ = ["SessionInvalidationAcknowledgement", "SessionInvalidationExecutor"]

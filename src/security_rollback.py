"""Sealed offline rollback/expiry contracts for durable security actions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import threading
import time
from typing import Any, Callable, Mapping

from src.security_executor_contracts import SecurityExecutionRequest, request_fingerprint


SECURITY_ROLLBACK_SCHEMA = "odysseus.security_rollback.v1"
_OPAQUE_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}:sha256:[0-9a-f]{64}$")
_OPERATION_LOCKS: dict[str, threading.RLock] = {}
_OPERATION_LOCKS_GUARD = threading.Lock()


class SecurityRollbackError(ValueError):
    """Content-free fail-closed rollback rejection."""


@dataclass(frozen=True, slots=True)
class RollbackOperation:
    """Exact opaque operation visible to a sealed test adapter only."""

    action_ref: str
    action_type: str
    scope_fingerprint: str
    receipt_ref: str
    rollback_descriptor: str
    mode: str
    schema: str = SECURITY_ROLLBACK_SCHEMA

    def validate(self) -> None:
        if (
            self.schema != SECURITY_ROLLBACK_SCHEMA or self.mode not in {"manual", "expiry"}
            or not isinstance(self.action_ref, str) or not _OPAQUE_REF_RE.fullmatch(self.action_ref)
            or not isinstance(self.action_type, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", self.action_type)
            or not isinstance(self.scope_fingerprint, str) or not re.fullmatch(r"scope:sha256:[0-9a-f]{64}", self.scope_fingerprint)
            or not isinstance(self.receipt_ref, str) or not _OPAQUE_REF_RE.fullmatch(self.receipt_ref)
            or not isinstance(self.rollback_descriptor, str) or not re.fullmatch(r"rollback:sha256:[0-9a-f]{64}", self.rollback_descriptor)
        ):
            raise SecurityRollbackError("rollback unavailable")


class RollbackAcknowledgement:
    """Issuer-sealed positive acknowledgement for one exact operation."""

    __slots__ = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise SecurityRollbackError("rollback unavailable")

    def __copy__(self) -> "RollbackAcknowledgement":
        raise SecurityRollbackError("rollback unavailable")

    def __deepcopy__(self, memo: dict[int, Any]) -> "RollbackAcknowledgement":
        raise SecurityRollbackError("rollback unavailable")

    def __reduce_ex__(self, protocol: int) -> object:
        raise SecurityRollbackError("rollback unavailable")


class IssuedRollbackAdapter:
    """Unforgeable in-process authority for an injected offline fake adapter."""

    __slots__ = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise SecurityRollbackError("rollback unavailable")

    def __copy__(self) -> "IssuedRollbackAdapter":
        raise SecurityRollbackError("rollback unavailable")

    def __deepcopy__(self, memo: dict[int, Any]) -> "IssuedRollbackAdapter":
        raise SecurityRollbackError("rollback unavailable")

    def __reduce_ex__(self, protocol: int) -> object:
        raise SecurityRollbackError("rollback unavailable")


class _RollbackTestIssuer:
    """Identity-only marker for isolated offline test fixture issuance."""


ROLLBACK_TEST_ONLY_ISSUER = _RollbackTestIssuer()


@dataclass(slots=True)
class _AdapterRecord:
    invoke: Callable[[RollbackOperation], Any]


@dataclass(slots=True)
class _AcknowledgementRecord:
    adapter: IssuedRollbackAdapter
    operation: RollbackOperation
    consumed: bool = False


def _build_test_adapter_api() -> tuple[Any, Any, Any]:
    adapters: dict[IssuedRollbackAdapter, _AdapterRecord] = {}
    acknowledgements: dict[RollbackAcknowledgement, _AcknowledgementRecord] = {}
    lock = threading.RLock()

    def issue(invoke: Any, *, test_issuer: object) -> IssuedRollbackAdapter:
        if test_issuer is not ROLLBACK_TEST_ONLY_ISSUER or not callable(invoke):
            raise SecurityRollbackError("rollback unavailable")
        adapter = object.__new__(IssuedRollbackAdapter)
        with lock:
            adapters[adapter] = _AdapterRecord(invoke)
        return adapter

    def issue_acknowledgement(adapter: Any, operation: Any, *, test_issuer: object) -> RollbackAcknowledgement:
        if test_issuer is not ROLLBACK_TEST_ONLY_ISSUER or type(adapter) is not IssuedRollbackAdapter or type(operation) is not RollbackOperation:
            raise SecurityRollbackError("rollback unavailable")
        operation.validate()
        with lock:
            if adapter not in adapters:
                raise SecurityRollbackError("rollback unavailable")
            acknowledgement = object.__new__(RollbackAcknowledgement)
            acknowledgements[acknowledgement] = _AcknowledgementRecord(adapter, operation)
        return acknowledgement

    def invoke(adapter: Any, operation: Any) -> None:
        if type(adapter) is not IssuedRollbackAdapter or type(operation) is not RollbackOperation:
            raise SecurityRollbackError("rollback unavailable")
        operation.validate()
        with lock:
            adapter_record = adapters.get(adapter)
        if adapter_record is None:
            raise SecurityRollbackError("rollback unavailable")
        acknowledgement = adapter_record.invoke(operation)
        if type(acknowledgement) is not RollbackAcknowledgement:
            raise SecurityRollbackError("rollback acknowledgement rejected")
        with lock:
            record = acknowledgements.get(acknowledgement)
            if record is None or record.consumed or record.adapter is not adapter or record.operation != operation:
                raise SecurityRollbackError("rollback acknowledgement rejected")
            record.consumed = True

    return issue, issue_acknowledgement, invoke


issue_test_rollback_adapter, issue_test_rollback_acknowledgement, _invoke_test_rollback_adapter = _build_test_adapter_api()


class SecurityRollbackManager:
    """One in-process effect per exact descriptor; no multi-process claim."""

    def __init__(self, store: Any, *, fake_adapters: Mapping[str, IssuedRollbackAdapter], clock: Callable[[], float] = time.time) -> None:
        if not isinstance(fake_adapters, Mapping) or not all(isinstance(action_type, str) and type(adapter) is IssuedRollbackAdapter for action_type, adapter in fake_adapters.items()):
            raise SecurityRollbackError("rollback unavailable")
        self._store = store
        self._adapters = dict(fake_adapters)
        self._clock = clock

    def rollback(self, request_value: Any, *, mode: str = "manual") -> dict[str, Any]:
        try:
            request = request_value if isinstance(request_value, SecurityExecutionRequest) else SecurityExecutionRequest.from_mapping(request_value)
            request.validate()
            if mode not in {"manual", "expiry"}:
                raise SecurityRollbackError("rollback unavailable")
        except (TypeError, ValueError):
            return self._blocked("invalid_rollback_request")
        # This lock is deliberately process-local.  It prevents two managers
        # from invoking a fake twice; durable replay handles later restarts.
        with self._operation_lock(request.rollback_descriptor):
            return self._rollback_locked(request, mode)

    def _rollback_locked(self, request: SecurityExecutionRequest, mode: str) -> dict[str, Any]:
        try:
            action = self._store.get_action(request.action_id)
        except Exception:
            return self._blocked("durable_action_unavailable")
        if not self._matches_action(action, request) or not self._has_execution_audit(request):
            return self._blocked("durable_receipt_mismatch")
        if getattr(action, "state", "") == "rolled_back":
            return self._replay(action, request, mode)
        if getattr(action, "state", "") not in {"executed", "verified", "failed"}:
            return self._blocked("rollback_not_available")
        if mode == "expiry" and not self._expired(request):
            return self._blocked("expiry_not_due")
        adapter = self._adapters.get(request.action_type)
        if adapter is None:
            return self._blocked("missing_fake_rollback_adapter")
        operation = RollbackOperation(action_ref=self._action_ref(request.action_id), action_type=request.action_type, scope_fingerprint=request.scope_fingerprint, receipt_ref=action.receipt_ref, rollback_descriptor=request.rollback_descriptor, mode=mode)
        try:
            operation.validate()
            _invoke_test_rollback_adapter(adapter, operation)
        except Exception:
            return self._blocked("rollback_acknowledgement_rejected")
        rollback_ref = self._rollback_ref(request, mode)
        try:
            self._store.transition(action_id=request.action_id, expected_version=action.version, target_state="rolled_back", audit_ref=self._audit_ref(request, mode, rollback_ref), rollback_ref=rollback_ref)
        except Exception:
            return self._blocked("concurrent_rollback_conflict")
        return self._result(rollback_ref, mode, idempotent_replay=False)

    def expire(self, request_value: Any) -> dict[str, Any]:
        return self.rollback(request_value, mode="expiry")

    def _replay(self, action: Any, request: SecurityExecutionRequest, mode: str) -> dict[str, Any]:
        expected_ref = self._rollback_ref(request, mode)
        expected_audit = self._audit_ref(request, mode, expected_ref)
        if getattr(action, "rollback_ref", "") != expected_ref:
            return self._blocked("durable_rollback_mismatch")
        try:
            events = tuple(self._store.audit_events(request.action_id))
        except Exception:
            return self._blocked("durable_audit_unavailable")
        if not any(getattr(event, "event_type", "") == "action_rolled_back" and getattr(event, "reference", "") == expected_audit for event in events):
            return self._blocked("durable_rollback_mismatch")
        return self._result(expected_ref, mode, idempotent_replay=True)

    def _has_execution_audit(self, request: SecurityExecutionRequest) -> bool:
        try:
            events = tuple(self._store.audit_events(request.action_id))
        except Exception:
            return False
        expected = "audit:sha256:" + hashlib.sha256(("acknowledged|" + request_fingerprint(request)).encode("utf-8")).hexdigest()
        return any(getattr(event, "event_type", "") == "action_executed" and getattr(event, "reference", "") == expected for event in events)

    @staticmethod
    def _matches_action(action: Any, request: SecurityExecutionRequest) -> bool:
        expected_receipt = "receipt:sha256:" + hashlib.sha256(("receipt|" + request_fingerprint(request)).encode("utf-8")).hexdigest()
        return bool(getattr(action, "action_type", None) == request.action_type and getattr(action, "scope_fingerprint", None) == request.scope_fingerprint and getattr(action, "policy_revision", None) == request.policy_revision and getattr(action, "idempotency_key", None) == request.idempotency_key and getattr(action, "receipt_ref", None) == expected_receipt)

    def _expired(self, request: SecurityExecutionRequest) -> bool:
        try:
            return float(self._clock()) >= float(request.expires_at)
        except Exception:
            return False

    @staticmethod
    def _operation_lock(descriptor: str) -> threading.RLock:
        with _OPERATION_LOCKS_GUARD:
            return _OPERATION_LOCKS.setdefault(descriptor, threading.RLock())

    @staticmethod
    def _action_ref(action_id: str) -> str:
        return "action:sha256:" + hashlib.sha256(action_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _rollback_ref(request: SecurityExecutionRequest, mode: str) -> str:
        return "rollback:sha256:" + hashlib.sha256(("rollback|" + request_fingerprint(request) + "|" + mode).encode("utf-8")).hexdigest()

    @staticmethod
    def _audit_ref(request: SecurityExecutionRequest, mode: str, rollback_ref: str) -> str:
        return "audit:sha256:" + hashlib.sha256(("rollback-audit|" + request_fingerprint(request) + "|" + mode + "|" + rollback_ref).encode("utf-8")).hexdigest()

    @staticmethod
    def _result(rollback_ref: str, mode: str, *, idempotent_replay: bool) -> dict[str, Any]:
        return {"schema": SECURITY_ROLLBACK_SCHEMA, "status": "success", "mode": mode, "rollback_ref": rollback_ref, "idempotent_replay": idempotent_replay, "raw_content_visible": False}

    @staticmethod
    def _blocked(reason: str) -> dict[str, Any]:
        return {"schema": SECURITY_ROLLBACK_SCHEMA, "status": "blocked", "reason": reason, "raw_content_visible": False}


__all__ = ["IssuedRollbackAdapter", "ROLLBACK_TEST_ONLY_ISSUER", "RollbackAcknowledgement", "RollbackOperation", "SECURITY_ROLLBACK_SCHEMA", "SecurityRollbackError", "SecurityRollbackManager", "issue_test_rollback_acknowledgement", "issue_test_rollback_adapter"]

"""Default-disabled, typed security action executor kernel.

The kernel accepts only explicitly injected test fakes.  It has no live
executor, provider, transport, shell, command, or environment integration.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable, Mapping

from src.security_executor_contracts import (
    SecurityExecutionReceipt, SecurityExecutionRequest, SecurityExecutorContractError, build_rollback_descriptor,
    request_fingerprint,
)
from src.security_response_policy import action_policy_disposition, typed_executor_action_types


# Policy is the sole authority for this closed dispatch set.
TYPED_EXECUTOR_ACTION_TYPES = typed_executor_action_types()


class SecurityExecutorKernelError(RuntimeError):
    """Content-free fail-closed kernel error."""


class SecurityExecutorKernel:
    """Bind one durable approval to one injected fake typed side effect."""

    def __init__(self, store: Any, *, fake_executors: Mapping[str, Callable[[SecurityExecutionRequest], Any]] | None = None, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.RLock()
        self._executors = self._validate_fake_executors(fake_executors or {})

    @staticmethod
    def _validate_fake_executors(executors: Mapping[str, Callable[[SecurityExecutionRequest], Any]]) -> dict[str, Callable[[SecurityExecutionRequest], Any]]:
        if not isinstance(executors, Mapping):
            raise SecurityExecutorKernelError("executor kernel unavailable")
        normalized: dict[str, Callable[[SecurityExecutionRequest], Any]] = {}
        for action_type, executor in executors.items():
            if action_type not in TYPED_EXECUTOR_ACTION_TYPES or not callable(executor) or getattr(executor, "security_executor_test_fake", False) is not True:
                raise SecurityExecutorKernelError("executor kernel unavailable")
            normalized[action_type] = executor
        return normalized

    def execute(self, request_value: Any) -> dict[str, Any]:
        try:
            request = request_value if isinstance(request_value, SecurityExecutionRequest) else SecurityExecutionRequest.from_mapping(request_value)
            request.validate()
        except (SecurityExecutorContractError, TypeError, ValueError):
            return self._blocked("invalid_execution_request")
        # An action is never replayable merely because a durable store can
        # represent it.  Closed typed dispatch precedes every store lookup.
        if request.action_type not in TYPED_EXECUTOR_ACTION_TYPES:
            return self._blocked("unknown_action_type")
        with self._lock:
            durable_replay = self._durable_replay(request)
            if durable_replay is not None:
                return durable_replay
            executor = self._executors.get(request.action_type)
            if executor is None:
                return self._blocked("missing_fake_executor")
            if not self._preflight(request):
                return self._blocked("durable_preflight_rejected")
            # This atomic durable transition consumes the single-use approval
            # before the fake executor can observe the request.
            try:
                executing = self._store.transition(
                    action_id=request.action_id, expected_version=request.action_version,
                    target_state="executing", audit_ref=self._audit_ref("executing", request),
                )
            except Exception:
                return self._blocked("durable_preflight_rejected")
            try:
                # This repo-only kernel validates and passes the bounded
                # timeout contract to injected fakes.  It does not claim to
                # enforce a transport timeout; typed adapter slices own that.
                executor(request)
            except Exception:
                self._fail_safely(executing, request)
                return self._blocked("fake_executor_failed")
            receipt = self._receipt(request)
            try:
                self._store.transition(
                    action_id=request.action_id, expected_version=executing.version,
                    target_state="executed", audit_ref=self._audit_ref("acknowledged", request),
                    receipt_ref=receipt.receipt_ref,
                )
            except Exception:
                # A side effect cannot be silently represented as verified or
                # retried.  The caller receives only a content-free failure.
                return self._blocked("durable_receipt_unavailable")
            return self._success(receipt, idempotent_replay=False)

    def _durable_replay(self, request: SecurityExecutionRequest) -> dict[str, Any] | None:
        """Resolve replay exclusively from durable action, approval and audit state.

        The durable receipt and any matching execution acknowledgement audit
        reference bind every request field (including timeout, rollback and
        expiry), even if later verification, rollback or failure transitions
        happened.  This never calls an executor after a process restart.
        """

        try:
            action = self._store.get_action(request.action_id)
        except Exception:
            return None
        if action.state == "executing":
            return self._blocked("durable_nonreplayable_state")
        if action.state not in {"executed", "verified", "rolled_back", "failed"}:
            return None
        try:
            approval = self._store.get_approval(request.action_id)
            events = tuple(self._store.audit_events(request.action_id))
        except Exception:
            return self._blocked("durable_receipt_unavailable")
        if approval is None or approval.consumed_at is None:
            return self._blocked("durable_receipt_unavailable")
        durable_binding_matches = (
            action.action_type == request.action_type and action.scope_fingerprint == request.scope_fingerprint
            and action.policy_revision == request.policy_revision and action.idempotency_key == request.idempotency_key
            and approval.action_id == request.action_id and approval.action_version == request.action_version
            and approval.scope_fingerprint == request.scope_fingerprint and approval.policy_revision == request.policy_revision
        )
        if not durable_binding_matches:
            return self._blocked("idempotency_key_conflict")
        expected = self._receipt(request)
        audit_matches = any(
            getattr(event, "event_type", "") == "action_executed"
            and getattr(event, "reference", "") == self._audit_ref("acknowledged", request)
            for event in events
        )
        if not audit_matches:
            if action.state == "failed" and not action.receipt_ref:
                return self._blocked("durable_nonreplayable_state")
            return self._blocked("idempotency_key_conflict")
        if action.receipt_ref != expected.receipt_ref:
            return self._blocked("durable_receipt_mismatch")
        return self._success(expected, idempotent_replay=True)

    def _preflight(self, request: SecurityExecutionRequest) -> bool:
        try:
            now = float(self._clock())
            action = self._store.get_action(request.action_id)
            approval = self._store.get_approval(request.action_id)
        except Exception:
            return False
        disposition, gate = action_policy_disposition(request.action_type)
        return bool(
            action.state == "approved" and action.version == request.action_version
            and action.action_type == request.action_type and action.scope_fingerprint == request.scope_fingerprint
            and action.policy_revision == request.policy_revision and action.idempotency_key == request.idempotency_key
            and disposition == "typed_executor" and gate == request.policy_gate
            and approval is not None and approval.consumed_at is None and approval.action_id == request.action_id
            and approval.action_version == request.action_version and approval.scope_fingerprint == request.scope_fingerprint
            and approval.policy_revision == request.policy_revision
            and request.rollback_descriptor == build_rollback_descriptor(request)
            # Inclusive completion boundary: a bounded fake may complete at
            # exactly the request expiry, but never after either authority.
            and now + request.timeout_seconds <= request.expires_at <= action.expires_at
        )

    def _fail_safely(self, executing: Any, request: SecurityExecutionRequest) -> None:
        try:
            self._store.transition(
                action_id=request.action_id, expected_version=executing.version, target_state="failed",
                audit_ref=self._audit_ref("failed", request), failure_ref="failure:sha256:" + request_fingerprint(request),
            )
        except Exception:
            pass

    @staticmethod
    def _receipt(request: SecurityExecutionRequest) -> SecurityExecutionReceipt:
        digest = hashlib.sha256(("receipt|" + request_fingerprint(request)).encode("utf-8")).hexdigest()
        return SecurityExecutionReceipt(
            action_id=request.action_id, action_version=request.action_version, action_type=request.action_type,
            idempotency_key=request.idempotency_key, receipt_ref="receipt:sha256:" + digest,
        )

    @staticmethod
    def _audit_ref(event: str, request: SecurityExecutionRequest) -> str:
        digest = hashlib.sha256((event + "|" + request_fingerprint(request)).encode("utf-8")).hexdigest()
        return "audit:sha256:" + digest

    @staticmethod
    def _blocked(reason: str) -> dict[str, Any]:
        return {"status": "blocked", "reason": reason, "executed": False, "verified": False, "raw_content_visible": False}

    @staticmethod
    def _success(receipt: SecurityExecutionReceipt, *, idempotent_replay: bool) -> dict[str, Any]:
        result = receipt.projection(idempotent_replay=idempotent_replay)
        result.update({"status": "success", "reason": "execution_acknowledged", "executed": True})
        return result


__all__ = ["SecurityExecutorKernel", "SecurityExecutorKernelError", "TYPED_EXECUTOR_ACTION_TYPES"]

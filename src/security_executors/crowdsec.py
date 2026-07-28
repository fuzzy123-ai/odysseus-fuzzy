"""Default-disabled typed CrowdSec adapter.

There is intentionally no network client, environment switch, endpoint, shell
integration, or production registration in this module.  SIRP-06 calls this
adapter only after it has consumed the durable single-use approval.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping, Protocol

from src.security_crowdsec_contracts import (
    _consume_execution_authority, CrowdSecAcknowledgement, CrowdSecContractError,
    CrowdSecExecutionAuthority, CrowdSecOperation, CrowdSecRemediation,
    validate_request_binding,
)
from src.security_executor_contracts import SecurityExecutionRequest, request_fingerprint


class CrowdSecExecutorError(RuntimeError):
    """Content-free adapter failure; never serialize a transport error."""


class CrowdSecTypedTransport(Protocol):
    """Future production boundary: only a typed fixed operation can cross it.

    This repository deliberately supplies no implementation or registration of
    this protocol.  Offline injection is restricted below to marked fakes.
    """

    def execute_typed(self, operation: CrowdSecOperation) -> Any:
        """Acknowledge one already-authorized fixed CrowdSec operation."""


class CrowdSecTypedExecutor:
    """Dispatch only pre-bound operations through an explicit marked fake.

    A default instance is disabled.  The marker is deliberately narrower than
    the kernel marker: a callable must opt into *both* test boundaries before
    this adapter can invoke it.
    """

    def __init__(self, remediations: Mapping[str, CrowdSecRemediation] | None = None, *, fake_transport: CrowdSecTypedTransport | Callable[[CrowdSecOperation], Any] | None = None) -> None:
        self._remediations = self._validate_remediations(remediations or {})
        self._transport = self._validate_fake_transport(fake_transport)
        # SIRP-06 accepts only explicitly marked injected test executors.
        self.security_executor_test_fake = self._transport is not None

    @staticmethod
    def _validate_remediations(value: Mapping[str, CrowdSecRemediation]) -> dict[str, CrowdSecRemediation]:
        if not isinstance(value, Mapping):
            raise CrowdSecExecutorError("CrowdSec executor unavailable")
        result: dict[str, CrowdSecRemediation] = {}
        for action_id, remediation in value.items():
            if not isinstance(action_id, str) or not isinstance(remediation, CrowdSecRemediation):
                raise CrowdSecExecutorError("CrowdSec executor unavailable")
            valid_remediation = True
            try:
                remediation.validate()
            except CrowdSecContractError:
                valid_remediation = False
            if not valid_remediation:
                raise CrowdSecExecutorError("CrowdSec executor unavailable")
            if action_id != remediation.action_id or action_id in result:
                raise CrowdSecExecutorError("CrowdSec executor unavailable")
            result[action_id] = remediation
        return result

    @staticmethod
    def _validate_fake_transport(transport: CrowdSecTypedTransport | Callable[[CrowdSecOperation], Any] | None) -> CrowdSecTypedTransport | Callable[[CrowdSecOperation], Any] | None:
        if transport is None:
            return None
        if (not callable(getattr(transport, "execute_typed", None)) and not callable(transport)) or getattr(transport, "security_crowdsec_test_fake", False) is not True:
            raise CrowdSecExecutorError("CrowdSec executor unavailable")
        return transport

    def __call__(self, request: SecurityExecutionRequest) -> dict[str, Any]:
        return self.execute(request)

    def execute(
        self,
        request: SecurityExecutionRequest,
        authority: CrowdSecExecutionAuthority | None = None,
    ) -> dict[str, Any]:
        """Return a redacted acknowledgement or fail before transport effect."""

        if self._transport is None:
            raise CrowdSecExecutorError("CrowdSec executor disabled")
        rejected = False
        try:
            if not isinstance(request, SecurityExecutionRequest):
                raise CrowdSecContractError("invalid kernel request")
            request.validate()
            remediation = self._remediations.get(request.action_id)
            if remediation is None:
                raise CrowdSecContractError("missing CrowdSec remediation")
            validate_request_binding(remediation, request)
            # The sealed authority represents SIRP-06's already-consumed
            # durable approval and is itself consumed before fake transport.
            _consume_execution_authority(
                authority, request, remediation, fake_transport=True,
            )
            operation = CrowdSecOperation(
                operation=remediation.operation(), scope_handle=remediation.scope_handle,
                ttl_seconds=remediation.ttl_seconds, expiry_descriptor=remediation.expiry_descriptor,
            )
        except (CrowdSecContractError, TypeError, ValueError):
            rejected = True
        if rejected:
            raise CrowdSecExecutorError("CrowdSec preflight rejected")

        transport_failed = False
        try:
            execute_typed = getattr(self._transport, "execute_typed", None)
            if callable(execute_typed):
                execute_typed(operation)
            else:
                self._transport(operation)
        except Exception:
            # Do not raise while handling the transport exception: callers
            # must not retain raw text, object, cause, or context.
            transport_failed = True
        if transport_failed:
            raise CrowdSecExecutorError("CrowdSec transport acknowledgement unavailable")
        digest = hashlib.sha256(("crowdsec-receipt|" + request_fingerprint(request)).encode()).hexdigest()
        acknowledgement = CrowdSecAcknowledgement(
            action_id=request.action_id, action_type=request.action_type,
            receipt_ref="crowdsec-receipt:sha256:" + digest,
        )
        return acknowledgement.projection()


__all__ = ["CrowdSecExecutorError", "CrowdSecTypedExecutor", "CrowdSecTypedTransport"]

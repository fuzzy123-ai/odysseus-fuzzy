"""Strict, redacted contracts for the default-disabled security executor.

These contracts intentionally model opaque durable authorities only.  They do
not carry commands, targets, credentials, provider responses, or transport
details.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping


SECURITY_EXECUTION_REQUEST_SCHEMA = "odysseus.security_execution_request.v1"
SECURITY_EXECUTION_RECEIPT_SCHEMA = "odysseus.security_execution_receipt.v1"
MAX_EXECUTION_TIMEOUT_SECONDS = 300
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,127}$")
_OPAQUE_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}:sha256:[0-9a-f]{64}$")
_SCOPE_RE = re.compile(r"^scope:sha256:[0-9a-f]{64}$")
_ROLLBACK_RE = re.compile(r"^rollback:sha256:[0-9a-f]{64}$")


class SecurityExecutorContractError(ValueError):
    """Content-free rejection for malformed execution data."""


@dataclass(frozen=True, slots=True)
class SecurityExecutionRequest:
    action_id: str
    action_version: int
    action_type: str
    scope_fingerprint: str
    policy_revision: str
    policy_gate: str
    timeout_seconds: int
    idempotency_key: str
    rollback_descriptor: str
    expires_at: float
    schema: str = SECURITY_EXECUTION_REQUEST_SCHEMA

    @classmethod
    def from_mapping(cls, value: Any) -> "SecurityExecutionRequest":
        if not isinstance(value, Mapping) or set(value) != {
            "schema", "action_id", "action_version", "action_type", "scope_fingerprint",
            "policy_revision", "policy_gate", "timeout_seconds", "idempotency_key",
            "rollback_descriptor", "expires_at",
        }:
            raise SecurityExecutorContractError("invalid execution request")
        request = cls(**dict(value))
        request.validate()
        return request

    def validate(self) -> None:
        if self.schema != SECURITY_EXECUTION_REQUEST_SCHEMA:
            raise SecurityExecutorContractError("invalid execution request")
        if not all(isinstance(value, str) and _ID_RE.fullmatch(value) for value in (self.action_id, self.action_type, self.idempotency_key)):
            raise SecurityExecutorContractError("invalid execution request")
        if type(self.action_version) is not int or self.action_version < 1:
            raise SecurityExecutorContractError("invalid execution request")
        if not isinstance(self.scope_fingerprint, str) or not _SCOPE_RE.fullmatch(self.scope_fingerprint):
            raise SecurityExecutorContractError("invalid execution request")
        if not isinstance(self.policy_revision, str) or not _OPAQUE_REF_RE.fullmatch(self.policy_revision):
            raise SecurityExecutorContractError("invalid execution request")
        if not isinstance(self.policy_gate, str) or not _ID_RE.fullmatch(self.policy_gate):
            raise SecurityExecutorContractError("invalid execution request")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= MAX_EXECUTION_TIMEOUT_SECONDS:
            raise SecurityExecutorContractError("invalid execution request")
        if not isinstance(self.rollback_descriptor, str) or not _ROLLBACK_RE.fullmatch(self.rollback_descriptor):
            raise SecurityExecutorContractError("invalid execution request")
        if isinstance(self.expires_at, bool) or not isinstance(self.expires_at, (int, float)) or not math.isfinite(float(self.expires_at)) or float(self.expires_at) < 0:
            raise SecurityExecutorContractError("invalid execution request")


@dataclass(frozen=True, slots=True)
class SecurityExecutionReceipt:
    action_id: str
    action_version: int
    action_type: str
    idempotency_key: str
    receipt_ref: str
    execution_state: str = "acknowledged"
    acknowledgement_received: bool = True
    verification_state: str = "not_verified"
    verified: bool = False
    raw_content_visible: bool = False
    schema: str = SECURITY_EXECUTION_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        if (
            self.schema != SECURITY_EXECUTION_RECEIPT_SCHEMA
            or self.execution_state != "acknowledged"
            or self.acknowledgement_received is not True
            or self.verification_state != "not_verified"
            or self.verified is not False
            or self.raw_content_visible is not False
            or not all(isinstance(value, str) and _ID_RE.fullmatch(value) for value in (self.action_id, self.action_type, self.idempotency_key))
            or type(self.action_version) is not int or self.action_version < 1
            or not isinstance(self.receipt_ref, str) or not _OPAQUE_REF_RE.fullmatch(self.receipt_ref)
        ):
            raise SecurityExecutorContractError("invalid execution receipt")

    def projection(self, *, idempotent_replay: bool) -> dict[str, Any]:
        return {
            "schema": self.schema, "action_id": self.action_id,
            "action_version": self.action_version, "action_type": self.action_type,
            "idempotency_key": self.idempotency_key, "receipt_ref": self.receipt_ref,
            "execution_state": self.execution_state,
            "acknowledgement_received": self.acknowledgement_received,
            "verification_state": self.verification_state, "verified": False,
            "idempotent_replay": idempotent_replay, "raw_content_visible": False,
        }


def request_fingerprint(request: SecurityExecutionRequest) -> str:
    """Return a content-free stable identity for conflict-safe replay checks."""

    body = "|".join(str(getattr(request, name)) for name in (
        "action_id", "action_version", "action_type", "scope_fingerprint", "policy_revision",
        "policy_gate", "timeout_seconds", "idempotency_key", "rollback_descriptor", "expires_at",
    ))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_rollback_descriptor(request: SecurityExecutionRequest) -> str:
    """Return the opaque rollback identity for one exact authorized attempt.

    This binds attempt identity only.  It does not describe, select, or invoke
    a rollback transport.
    """

    request.validate()
    authority = {
        "action_id": request.action_id,
        "action_version": request.action_version,
        "action_type": request.action_type,
        "scope_fingerprint": request.scope_fingerprint,
        "policy_revision": request.policy_revision,
        "policy_gate": request.policy_gate,
        "timeout_seconds": request.timeout_seconds,
        "idempotency_key": request.idempotency_key,
        "expires_at": request.expires_at,
    }
    body = json.dumps(authority, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "rollback:sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


__all__ = [
    "MAX_EXECUTION_TIMEOUT_SECONDS", "SECURITY_EXECUTION_RECEIPT_SCHEMA",
    "SECURITY_EXECUTION_REQUEST_SCHEMA", "SecurityExecutionReceipt", "SecurityExecutionRequest",
    "SecurityExecutorContractError", "build_rollback_descriptor", "request_fingerprint",
]

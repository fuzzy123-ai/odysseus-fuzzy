"""Closed, redacted contracts for the default-off CrowdSec executor.

These values deliberately carry opaque handles only.  They cannot describe an
endpoint, command, firewall rule, or a target address.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping, Protocol

from src.security_executor_contracts import SecurityExecutionRequest, request_fingerprint


CROWDSEC_REMEDIATION_SCHEMA = "odysseus.crowdsec_remediation.v1"
CROWDSEC_ACKNOWLEDGEMENT_SCHEMA = "odysseus.crowdsec_acknowledgement.v1"
CROWDSEC_POLICY_GATE = "crowdsec-remediation-go"
CROWDSEC_ACTION_TYPES = frozenset({"crowdsec_temp_block", "crowdsec_unblock"})
MIN_CROWDSEC_TTL_SECONDS = 30
MAX_CROWDSEC_TTL_SECONDS = 300
_SCOPE = re.compile(r"^scope:sha256:[0-9a-f]{64}$")
_BINDING = re.compile(r"^execution:sha256:[0-9a-f]{64}$")
_EXPIRY = re.compile(r"^crowdsec-(?:expiry|unban):sha256:[0-9a-f]{64}$")
_RECEIPT = re.compile(r"^crowdsec-receipt:sha256:[0-9a-f]{64}$")
class _CrowdSecTestOnlyIssuer:
    """Unmistakable marker for offline fixtures; never a production issuer."""


CROWDSEC_TEST_ONLY_ISSUER = _CrowdSecTestOnlyIssuer()


class CrowdSecContractError(ValueError):
    """Content-free rejection for invalid CrowdSec authority."""


@dataclass(frozen=True, slots=True)
class CrowdSecRemediation:
    """One pre-bound, opaque request that a kernel may dispatch once."""

    action_id: str
    action_type: str
    scope_handle: str
    ttl_seconds: int
    expiry_descriptor: str
    policy_gate: str
    execution_binding: str
    false_positive_risk: bool = False
    operator_lockout_risk: bool = False
    schema: str = CROWDSEC_REMEDIATION_SCHEMA

    @classmethod
    def from_mapping(cls, value: Any) -> "CrowdSecRemediation":
        required = {
            "schema", "action_id", "action_type", "scope_handle", "ttl_seconds",
            "expiry_descriptor", "policy_gate", "execution_binding",
            "false_positive_risk", "operator_lockout_risk",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise CrowdSecContractError("invalid CrowdSec remediation")
        result = cls(**dict(value))
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema != CROWDSEC_REMEDIATION_SCHEMA or self.action_type not in CROWDSEC_ACTION_TYPES:
            raise CrowdSecContractError("invalid CrowdSec remediation")
        if not isinstance(self.action_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,127}", self.action_id):
            raise CrowdSecContractError("invalid CrowdSec remediation")
        if not isinstance(self.scope_handle, str) or not _SCOPE.fullmatch(self.scope_handle):
            raise CrowdSecContractError("invalid CrowdSec remediation")
        if self.policy_gate != CROWDSEC_POLICY_GATE or not isinstance(self.execution_binding, str) or not _BINDING.fullmatch(self.execution_binding):
            raise CrowdSecContractError("invalid CrowdSec remediation")
        if type(self.false_positive_risk) is not bool or type(self.operator_lockout_risk) is not bool:
            raise CrowdSecContractError("invalid CrowdSec remediation")
        if not isinstance(self.expiry_descriptor, str) or not _EXPIRY.fullmatch(self.expiry_descriptor):
            raise CrowdSecContractError("invalid CrowdSec remediation")
        if self.action_type == "crowdsec_temp_block":
            valid_ttl = type(self.ttl_seconds) is int and MIN_CROWDSEC_TTL_SECONDS <= self.ttl_seconds <= MAX_CROWDSEC_TTL_SECONDS
            valid_descriptor = self.expiry_descriptor.startswith("crowdsec-expiry:")
        else:
            valid_ttl = type(self.ttl_seconds) is int and self.ttl_seconds == 0
            valid_descriptor = self.expiry_descriptor.startswith("crowdsec-unban:")
        if not valid_ttl or not valid_descriptor:
            raise CrowdSecContractError("invalid CrowdSec remediation")

    def operation(self) -> str:
        return "temporary_block" if self.action_type == "crowdsec_temp_block" else "explicit_unblock"


class CrowdSecExecutionAuthority:
    """Opaque, issuer-bound proof of an already-consumed SIRP-06 approval.

    This type has no public constructor, serializer, or request-only minting
    path.  This slice provides no production issuer.  Offline tests can issue
    only a distinctly test-only authority, and the executor accepts that form
    solely with an injected marked fake transport.
    """

    __slots__ = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise CrowdSecContractError("CrowdSec execution authority unavailable")

    def __copy__(self) -> "CrowdSecExecutionAuthority":
        raise CrowdSecContractError("CrowdSec execution authority unavailable")

    def __deepcopy__(self, memo: dict[int, Any]) -> "CrowdSecExecutionAuthority":
        raise CrowdSecContractError("CrowdSec execution authority unavailable")

    def __reduce_ex__(self, protocol: int) -> object:
        raise CrowdSecContractError("CrowdSec execution authority unavailable")


class CrowdSecExecutionAuthorityIssuer(Protocol):
    """Future SIRP-06-only composition contract; no issuer exists in this slice."""

    def issue_after_consumed_sirp06_approval(
        self,
        request: SecurityExecutionRequest,
        remediation: CrowdSecRemediation,
    ) -> CrowdSecExecutionAuthority:
        """Return one sealed authority only after durable approval consumption."""


def _remediation_fingerprint(remediation: CrowdSecRemediation) -> str:
    body = "|".join(str(getattr(remediation, field)) for field in (
        "action_id", "action_type", "scope_handle", "ttl_seconds",
        "expiry_descriptor", "policy_gate", "execution_binding",
        "false_positive_risk", "operator_lockout_risk", "schema",
    ))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CrowdSecOperation:
    """The entire fixed transport vocabulary: no endpoint or command field."""

    operation: str
    scope_handle: str
    ttl_seconds: int
    expiry_descriptor: str

    def __post_init__(self) -> None:
        if self.operation not in {"temporary_block", "explicit_unblock"} or not _SCOPE.fullmatch(self.scope_handle):
            raise CrowdSecContractError("invalid CrowdSec operation")
        if self.operation == "temporary_block":
            valid = type(self.ttl_seconds) is int and MIN_CROWDSEC_TTL_SECONDS <= self.ttl_seconds <= MAX_CROWDSEC_TTL_SECONDS and self.expiry_descriptor.startswith("crowdsec-expiry:")
        else:
            valid = type(self.ttl_seconds) is int and self.ttl_seconds == 0 and self.expiry_descriptor.startswith("crowdsec-unban:")
        if not valid or not _EXPIRY.fullmatch(self.expiry_descriptor):
            raise CrowdSecContractError("invalid CrowdSec operation")


@dataclass(frozen=True, slots=True)
class CrowdSecAcknowledgement:
    """Fixed bounded result; acknowledgement is explicitly not verification."""

    action_id: str
    action_type: str
    receipt_ref: str
    schema: str = CROWDSEC_ACKNOWLEDGEMENT_SCHEMA
    acknowledgement_received: bool = True
    verification_state: str = "not_verified"
    verified: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        if (
            self.schema != CROWDSEC_ACKNOWLEDGEMENT_SCHEMA or self.action_type not in CROWDSEC_ACTION_TYPES
            or not isinstance(self.action_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,127}", self.action_id)
            or not isinstance(self.receipt_ref, str) or not _RECEIPT.fullmatch(self.receipt_ref)
            or self.acknowledgement_received is not True or self.verification_state != "not_verified"
            or self.verified is not False or self.raw_content_visible is not False
        ):
            raise CrowdSecContractError("invalid CrowdSec acknowledgement")

    def projection(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "action_id": self.action_id, "action_type": self.action_type,
            "receipt_ref": self.receipt_ref, "acknowledgement_received": True,
            "verification_state": "not_verified", "verified": False, "raw_content_visible": False,
        }


def validate_request_binding(remediation: CrowdSecRemediation, request: SecurityExecutionRequest) -> None:
    """Fail closed unless remediation is exactly bound to the typed request."""

    remediation.validate()
    request.validate()
    digest = request_fingerprint(request)
    if not (
        request.action_type in CROWDSEC_ACTION_TYPES and request.policy_gate == CROWDSEC_POLICY_GATE
        and remediation.action_id == request.action_id and remediation.action_type == request.action_type
        and remediation.scope_handle == request.scope_fingerprint and remediation.policy_gate == request.policy_gate
        and remediation.execution_binding == "execution:sha256:" + digest
        and (
            remediation.ttl_seconds <= request.timeout_seconds
            if remediation.action_type == "crowdsec_temp_block"
            else remediation.ttl_seconds == 0
        )
    ):
        raise CrowdSecContractError("CrowdSec kernel binding rejected")
    if remediation.false_positive_risk or remediation.operator_lockout_risk:
        raise CrowdSecContractError("CrowdSec preflight rejected")


def _build_test_authority_api() -> tuple[Any, Any]:
    """Keep issuer records private and keyed by the issued object identity."""

    records: dict[CrowdSecExecutionAuthority, dict[str, Any]] = {}

    def issue(
        request: SecurityExecutionRequest,
        remediation: CrowdSecRemediation,
        *,
        test_issuer: object,
    ) -> CrowdSecExecutionAuthority:
        if test_issuer is not CROWDSEC_TEST_ONLY_ISSUER:
            raise CrowdSecContractError("CrowdSec execution authority unavailable")
        request.validate()
        validate_request_binding(remediation, request)
        authority = object.__new__(CrowdSecExecutionAuthority)
        records[authority] = {
            "request_digest": request_fingerprint(request),
            "action_version": request.action_version,
            "policy_gate": request.policy_gate,
            "scope_handle": request.scope_fingerprint,
            "remediation_digest": _remediation_fingerprint(remediation),
            "consumed": False,
        }
        return authority

    def consume(
        authority: object,
        request: SecurityExecutionRequest,
        remediation: CrowdSecRemediation,
        *,
        fake_transport: bool,
    ) -> None:
        if not isinstance(authority, CrowdSecExecutionAuthority) or not fake_transport:
            raise CrowdSecContractError("CrowdSec execution authority rejected")
        record = records.get(authority)
        if (
            record is None
            or record["consumed"]
            or record["request_digest"] != request_fingerprint(request)
            or record["action_version"] != request.action_version
            or record["policy_gate"] != request.policy_gate
            or record["scope_handle"] != request.scope_fingerprint
            or record["remediation_digest"] != _remediation_fingerprint(remediation)
        ):
            raise CrowdSecContractError("CrowdSec execution authority rejected")
        record["consumed"] = True

    return issue, consume


issue_test_crowdsec_execution_authority, _consume_execution_authority = _build_test_authority_api()


__all__ = [
    "CROWDSEC_ACKNOWLEDGEMENT_SCHEMA", "CROWDSEC_ACTION_TYPES", "CROWDSEC_POLICY_GATE",
    "CROWDSEC_REMEDIATION_SCHEMA", "CROWDSEC_TEST_ONLY_ISSUER", "MAX_CROWDSEC_TTL_SECONDS",
    "MIN_CROWDSEC_TTL_SECONDS", "CrowdSecAcknowledgement", "CrowdSecContractError",
    "CrowdSecExecutionAuthority", "CrowdSecExecutionAuthorityIssuer", "CrowdSecOperation", "CrowdSecRemediation",
    "issue_test_crowdsec_execution_authority", "validate_request_binding",
]

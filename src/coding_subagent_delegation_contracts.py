"""Content-free contracts for bounded local/fake subagent delegation.

The contracts describe identities and receipts only. They never dispatch a
worker, execute a tool, read source content, close a gate, or write repository,
graph, memory, provider, network, or live state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any

from src.coding_loop_controller import CodingLoopControllerState
from src.coding_subagent_capsule import (
    CodingSubagentCapsule,
    CodingSubagentLifecycleDescriptor,
)
from src.runtime_event_envelope import stable_payload_hash


MAX_FANOUT = 2
MAX_DEPTH = 1
MAX_DELEGATION_REFS = 32
MAX_DELEGATION_HISTORY = 64
MAX_TOKEN_BUDGET = 200_000
MAX_CONTEXT_REF_BUDGET = 64
MAX_TIME_BUDGET_SECONDS = 86_400
MAX_REPAIR_BUDGET = 2

_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+\-=]{1,180}$")
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


class DelegationError(ValueError):
    """Raised when a delegation contract is unsafe or inconsistent."""


class DelegationStatus(StrEnum):
    PLANNED = "planned"
    COMPLETED = "completed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DelegationRole(StrEnum):
    IMPLEMENTER = "implementer"
    TESTER = "tester"
    REVIEWER = "reviewer"


class CancellationOrigin(StrEnum):
    PARENT = "parent"
    CHILD = "child"


def validate_identifier(value: Any, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not _ID_RE.fullmatch(value)
    ):
        raise DelegationError(f"{field_name} must be a safe identifier")
    return value


def validate_sha256(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise DelegationError(f"{field_name} must be sha256")
    return value


def _canonical_refs(
    values: tuple[str, ...],
    field_name: str,
    *,
    minimum: int = 0,
) -> tuple[str, ...]:
    if (
        not isinstance(values, tuple)
        or not minimum <= len(values) <= MAX_DELEGATION_REFS
        or len(set(values)) != len(values)
        or values != tuple(sorted(values))
    ):
        raise DelegationError(f"{field_name} must be bounded canonical refs")
    for value in values:
        validate_identifier(value, field_name)
    return values


def _budget(value: Any, field_name: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise DelegationError(f"{field_name} must be in its bounded range")
    return value


@dataclass(frozen=True, slots=True)
class DelegationBinding:
    controller_state_id: str
    parent_envelope_id: str
    capsule_id: str
    actor_id: str
    role: DelegationRole
    planning_item_id: str
    planning_revision: str
    claim_id: str
    claim_owner: str
    scope_digest: str
    input_revision: str
    exact_read_refs: tuple[str, ...]
    provenance_refs: tuple[str, ...]
    retrieval_snapshot_refs: tuple[str, ...]
    implementer_actor_id: str
    retrieval_identity_ref: str
    implementer_retrieval_identity_ref: str
    implementer_snapshot_refs: tuple[str, ...]
    acceptance_check_refs: tuple[str, ...]
    tool_capability_refs: tuple[str, ...]
    budget_policy_refs: tuple[str, ...]
    stop_rule_refs: tuple[str, ...]
    cancellation_descriptor_ref: str
    expiry_descriptor_ref: str
    resume_descriptor_ref: str
    token_budget: int
    context_ref_budget: int
    time_budget_seconds: int
    repair_budget: int
    reviewer_identity_ref: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "controller_state_id",
            "parent_envelope_id",
            "capsule_id",
            "scope_digest",
        ):
            validate_sha256(getattr(self, field_name), field_name)
        for field_name in (
            "actor_id",
            "planning_item_id",
            "planning_revision",
            "claim_id",
            "claim_owner",
            "input_revision",
            "implementer_actor_id",
            "retrieval_identity_ref",
            "implementer_retrieval_identity_ref",
            "cancellation_descriptor_ref",
            "expiry_descriptor_ref",
            "resume_descriptor_ref",
        ):
            validate_identifier(getattr(self, field_name), field_name)
        object.__setattr__(self, "role", DelegationRole(self.role))
        _canonical_refs(self.exact_read_refs, "exact_read_refs", minimum=1)
        _canonical_refs(self.provenance_refs, "provenance_refs", minimum=1)
        _canonical_refs(
            self.retrieval_snapshot_refs,
            "retrieval_snapshot_refs",
            minimum=1,
        )
        _canonical_refs(
            self.implementer_snapshot_refs,
            "implementer_snapshot_refs",
            minimum=1,
        )
        _canonical_refs(self.acceptance_check_refs, "acceptance_check_refs")
        _canonical_refs(self.tool_capability_refs, "tool_capability_refs")
        _canonical_refs(self.budget_policy_refs, "budget_policy_refs", minimum=1)
        _canonical_refs(self.stop_rule_refs, "stop_rule_refs", minimum=1)
        _budget(self.token_budget, "token_budget", 1, MAX_TOKEN_BUDGET)
        _budget(
            self.context_ref_budget,
            "context_ref_budget",
            1,
            MAX_CONTEXT_REF_BUDGET,
        )
        _budget(
            self.time_budget_seconds,
            "time_budget_seconds",
            1,
            MAX_TIME_BUDGET_SECONDS,
        )
        _budget(self.repair_budget, "repair_budget", 0, MAX_REPAIR_BUDGET)

        if self.role is DelegationRole.IMPLEMENTER:
            if (
                self.actor_id != self.implementer_actor_id
                or self.retrieval_identity_ref
                != self.implementer_retrieval_identity_ref
                or self.retrieval_snapshot_refs != self.implementer_snapshot_refs
                or self.repair_budget < 1
            ):
                raise DelegationError(
                    "implementer identity, retrieval, and repair binding is invalid"
                )
            if self.reviewer_identity_ref:
                raise DelegationError(
                    "reviewer_identity_ref belongs only to the reviewer"
                )
        elif self.repair_budget != 0:
            raise DelegationError("tester and reviewer repair budget must be zero")

        if self.role is DelegationRole.TESTER:
            if not self.acceptance_check_refs:
                raise DelegationError("tester requires acceptance checks")
            if self.reviewer_identity_ref:
                raise DelegationError(
                    "reviewer_identity_ref belongs only to the reviewer"
                )
        elif self.role is DelegationRole.REVIEWER:
            validate_identifier(self.reviewer_identity_ref, "reviewer_identity_ref")
            if (
                self.actor_id in {self.implementer_actor_id, self.claim_owner}
                or self.retrieval_identity_ref
                == self.implementer_retrieval_identity_ref
                or set(self.retrieval_snapshot_refs)
                & set(self.implementer_snapshot_refs)
                or not self.acceptance_check_refs
            ):
                raise DelegationError("reviewer isolation is invalid")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "controller_state_id": self.controller_state_id,
            "parent_envelope_id": self.parent_envelope_id,
            "capsule_id": self.capsule_id,
            "actor_id": self.actor_id,
            "role": self.role.value,
            "planning_item_id": self.planning_item_id,
            "planning_revision": self.planning_revision,
            "claim_id": self.claim_id,
            "claim_owner": self.claim_owner,
            "scope_digest": self.scope_digest,
            "input_revision": self.input_revision,
            "exact_read_refs": self.exact_read_refs,
            "provenance_refs": self.provenance_refs,
            "retrieval_snapshot_refs": self.retrieval_snapshot_refs,
            "implementer_actor_id": self.implementer_actor_id,
            "retrieval_identity_ref": self.retrieval_identity_ref,
            "implementer_retrieval_identity_ref": (
                self.implementer_retrieval_identity_ref
            ),
            "implementer_snapshot_refs": self.implementer_snapshot_refs,
            "acceptance_check_refs": self.acceptance_check_refs,
            "tool_capability_refs": self.tool_capability_refs,
            "budget_policy_refs": self.budget_policy_refs,
            "stop_rule_refs": self.stop_rule_refs,
            "cancellation_descriptor_ref": self.cancellation_descriptor_ref,
            "expiry_descriptor_ref": self.expiry_descriptor_ref,
            "resume_descriptor_ref": self.resume_descriptor_ref,
            "token_budget": self.token_budget,
            "context_ref_budget": self.context_ref_budget,
            "time_budget_seconds": self.time_budget_seconds,
            "repair_budget": self.repair_budget,
            "reviewer_identity_ref": self.reviewer_identity_ref,
        }

    @property
    def digest(self) -> str:
        return stable_payload_hash(self.semantic_dict())


def child_id(binding: DelegationBinding, child_slot: int) -> str:
    if not isinstance(binding, DelegationBinding):
        raise DelegationError("binding must be typed")
    if type(child_slot) is not int or not 1 <= child_slot <= MAX_FANOUT:
        raise DelegationError("child_slot is outside the declared fanout")
    return stable_payload_hash(
        {
            "controller_state_id": binding.controller_state_id,
            "parent_envelope_id": binding.parent_envelope_id,
            "planning_item_id": binding.planning_item_id,
            "planning_revision": binding.planning_revision,
            "claim_id": binding.claim_id,
            "claim_owner": binding.claim_owner,
            "scope_digest": binding.scope_digest,
            "input_revision": binding.input_revision,
            "child_slot": child_slot,
            "role": binding.role.value,
        }
    )


def attempt_id(child: str, fence: int, binding_digest: str) -> str:
    validate_sha256(child, "child_id")
    validate_sha256(binding_digest, "binding_digest")
    if type(fence) is not int or fence < 1:
        raise DelegationError("fence must be positive")
    return stable_payload_hash(
        {"child_id": child, "fence": fence, "binding_digest": binding_digest}
    )


@dataclass(frozen=True, slots=True)
class DelegationAttempt:
    child_id: str
    attempt_id: str
    binding: DelegationBinding
    child_slot: int
    depth: int
    fence: int
    lease_until: int
    idempotency_key: str
    status: DelegationStatus = DelegationStatus.PLANNED
    dispatch_performed: bool = False
    execution_allowed: bool = False
    edit_allowed: bool = False
    write_allowed: bool = False
    dispatch_allowed: bool = False
    gate_close_allowed: bool = False
    network_allowed: bool = False
    live_effect_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.binding, DelegationBinding):
            raise DelegationError("binding must be typed")
        validate_sha256(self.child_id, "child_id")
        validate_sha256(self.attempt_id, "attempt_id")
        validate_identifier(self.idempotency_key, "idempotency_key")
        if self.child_id != child_id(self.binding, self.child_slot):
            raise DelegationError("child identity does not match binding and slot")
        if self.attempt_id != attempt_id(
            self.child_id,
            self.fence,
            self.binding.digest,
        ):
            raise DelegationError("attempt identity does not match current facts")
        if self.depth != MAX_DEPTH:
            raise DelegationError("delegation depth must be exactly one")
        if type(self.fence) is not int or self.fence < 1:
            raise DelegationError("fence must be positive")
        if type(self.lease_until) is not int or self.lease_until < 1:
            raise DelegationError("lease_until must be positive")
        object.__setattr__(self, "status", DelegationStatus(self.status))
        if self.dispatch_performed is not False:
            raise DelegationError("dispatch_performed must remain false")
        for field_name in (
            "execution_allowed",
            "edit_allowed",
            "write_allowed",
            "dispatch_allowed",
            "gate_close_allowed",
            "network_allowed",
            "live_effect_allowed",
            "raw_content_visible",
        ):
            if getattr(self, field_name) is not False:
                raise DelegationError(f"{field_name} must remain false")


def delegation_event_id(
    *,
    child: str,
    attempt: str,
    fence: int,
    actor_id: str,
    status: DelegationStatus,
    event_ref: str,
    handoff_refs: tuple[str, ...] = (),
    cancellation_origin: CancellationOrigin | None = None,
    cancellation_ref: str = "",
) -> str:
    validate_sha256(child, "child_id")
    validate_sha256(attempt, "attempt_id")
    validate_identifier(actor_id, "actor_id")
    validate_identifier(event_ref, "event_ref")
    if type(fence) is not int or fence < 1:
        raise DelegationError("fence must be positive")
    normalized_status = DelegationStatus(status)
    normalized_origin = (
        CancellationOrigin(cancellation_origin).value
        if cancellation_origin is not None
        else ""
    )
    return stable_payload_hash(
        {
            "child_id": child,
            "attempt_id": attempt,
            "fence": fence,
            "actor_id": actor_id,
            "status": normalized_status.value,
            "event_ref": event_ref,
            "handoff_refs": handoff_refs,
            "cancellation_origin": normalized_origin,
            "cancellation_ref": cancellation_ref,
        }
    )


@dataclass(frozen=True, slots=True)
class DelegationEvent:
    event_id: str
    child_id: str
    attempt_id: str
    fence: int
    actor_id: str
    status: DelegationStatus
    event_ref: str
    handoff_refs: tuple[str, ...] = ()
    cancellation_origin: CancellationOrigin | None = None
    cancellation_ref: str = ""
    side_effects: tuple[str, ...] = ("none",)
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        validate_sha256(self.event_id, "event_id")
        validate_sha256(self.child_id, "child_id")
        validate_sha256(self.attempt_id, "attempt_id")
        validate_identifier(self.actor_id, "actor_id")
        validate_identifier(self.event_ref, "event_ref")
        if type(self.fence) is not int or self.fence < 1:
            raise DelegationError("fence must be positive")
        object.__setattr__(self, "status", DelegationStatus(self.status))
        if self.cancellation_origin is not None:
            object.__setattr__(
                self,
                "cancellation_origin",
                CancellationOrigin(self.cancellation_origin),
            )
        _canonical_refs(self.handoff_refs, "handoff_refs")
        if self.status is DelegationStatus.COMPLETED:
            if not self.handoff_refs or self.cancellation_origin or self.cancellation_ref:
                raise DelegationError(
                    "completed event requires only bounded handoff refs"
                )
        elif self.status is DelegationStatus.CANCELLED:
            if (
                self.cancellation_origin is None
                or not self.cancellation_ref
                or self.handoff_refs
            ):
                raise DelegationError(
                    "cancelled event requires origin and cancellation receipt"
                )
            validate_identifier(self.cancellation_ref, "cancellation_ref")
        elif self.status is DelegationStatus.EXPIRED:
            if self.handoff_refs or self.cancellation_origin or self.cancellation_ref:
                raise DelegationError("expired event cannot carry handoff or cancel data")
        else:
            raise DelegationError("child event status must be terminal receipt state")
        expected = delegation_event_id(
            child=self.child_id,
            attempt=self.attempt_id,
            fence=self.fence,
            actor_id=self.actor_id,
            status=self.status,
            event_ref=self.event_ref,
            handoff_refs=self.handoff_refs,
            cancellation_origin=self.cancellation_origin,
            cancellation_ref=self.cancellation_ref,
        )
        if self.event_id != expected:
            raise DelegationError("event identity does not match current facts")
        if self.side_effects != ("none",) or self.raw_content_visible is not False:
            raise DelegationError("delegation event must remain content-free")


def parent_decision_id(
    *,
    controller_state_id: str,
    parent_actor_id: str,
    child: str,
    attempt: str,
    fence: int,
    completion_event_id: str,
    decision_ref: str,
    outcome: DelegationStatus,
) -> str:
    validate_sha256(controller_state_id, "controller_state_id")
    validate_identifier(parent_actor_id, "parent_actor_id")
    validate_sha256(child, "child_id")
    validate_sha256(attempt, "attempt_id")
    validate_sha256(completion_event_id, "completion_event_id")
    validate_identifier(decision_ref, "decision_ref")
    if type(fence) is not int or fence < 1:
        raise DelegationError("fence must be positive")
    normalized_outcome = DelegationStatus(outcome)
    if normalized_outcome not in {
        DelegationStatus.ACCEPTED,
        DelegationStatus.REJECTED,
    }:
        raise DelegationError("parent outcome must be accepted or rejected")
    return stable_payload_hash(
        {
            "controller_state_id": controller_state_id,
            "parent_actor_id": parent_actor_id,
            "child_id": child,
            "attempt_id": attempt,
            "fence": fence,
            "completion_event_id": completion_event_id,
            "decision_ref": decision_ref,
            "outcome": normalized_outcome.value,
        }
    )


@dataclass(frozen=True, slots=True)
class ParentDecision:
    decision_id: str
    controller_state_id: str
    parent_actor_id: str
    child_id: str
    attempt_id: str
    fence: int
    completion_event_id: str
    decision_ref: str
    outcome: DelegationStatus
    gate_close_allowed: bool = False
    write_allowed: bool = False
    live_effect_allowed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", DelegationStatus(self.outcome))
        expected = parent_decision_id(
            controller_state_id=self.controller_state_id,
            parent_actor_id=self.parent_actor_id,
            child=self.child_id,
            attempt=self.attempt_id,
            fence=self.fence,
            completion_event_id=self.completion_event_id,
            decision_ref=self.decision_ref,
            outcome=self.outcome,
        )
        validate_sha256(self.decision_id, "decision_id")
        if self.decision_id != expected:
            raise DelegationError("parent decision identity does not match facts")
        for field_name in (
            "gate_close_allowed",
            "write_allowed",
            "live_effect_allowed",
        ):
            if getattr(self, field_name) is not False:
                raise DelegationError(f"{field_name} must remain false")


def binding_from_controller_capsule(
    controller: CodingLoopControllerState,
    capsule: CodingSubagentCapsule,
) -> DelegationBinding:
    """Bind one READY B1 capsule to one acting CAO-C controller without I/O."""
    if not isinstance(controller, CodingLoopControllerState):
        raise DelegationError("controller must be typed")
    if not isinstance(capsule, CodingSubagentCapsule):
        raise DelegationError("capsule must be typed")
    if (
        controller.lifecycle.state != "acting"
        or capsule.lifecycle_descriptor
        is not CodingSubagentLifecycleDescriptor.CAPSULE_READY
        or controller.parent_envelope is None
    ):
        raise DelegationError("acting controller and READY capsule are required")
    authority = controller.lifecycle.authority
    envelope = controller.parent_envelope
    if capsule not in controller.capsules:
        raise DelegationError("capsule is not owned by the controller")
    for field_name in (
        "planning_item_id",
        "planning_revision",
        "claim_id",
        "claim_owner",
        "input_revision",
    ):
        if getattr(capsule, field_name) != getattr(authority, field_name):
            raise DelegationError("capsule authority binding mismatch")
    if (
        capsule.scope_digest != authority.claim_scope_digest
        or capsule.parent_envelope_id != envelope.envelope_id
    ):
        raise DelegationError("capsule scope or parent binding mismatch")
    return DelegationBinding(
        controller_state_id=controller.state_id,
        parent_envelope_id=envelope.envelope_id,
        capsule_id=capsule.capsule_id,
        actor_id=capsule.actor_id,
        role=DelegationRole(capsule.role.value),
        planning_item_id=authority.planning_item_id,
        planning_revision=authority.planning_revision,
        claim_id=authority.claim_id,
        claim_owner=authority.claim_owner,
        scope_digest=authority.claim_scope_digest,
        input_revision=authority.input_revision,
        exact_read_refs=capsule.exact_read_refs,
        provenance_refs=capsule.provenance_refs,
        retrieval_snapshot_refs=capsule.retrieval_snapshot_refs,
        implementer_actor_id=capsule.implementer_actor_id,
        retrieval_identity_ref=capsule.retrieval_identity_ref,
        implementer_retrieval_identity_ref=(
            capsule.implementer_retrieval_identity_ref
        ),
        implementer_snapshot_refs=capsule.implementer_retrieval_snapshot_refs,
        acceptance_check_refs=capsule.acceptance_check_refs,
        tool_capability_refs=capsule.tool_capability_refs,
        budget_policy_refs=capsule.budget_policy_refs,
        stop_rule_refs=capsule.stop_rule_refs,
        cancellation_descriptor_ref=capsule.cancellation_descriptor_ref,
        expiry_descriptor_ref=capsule.expiry_descriptor_ref,
        resume_descriptor_ref=capsule.resume_descriptor_ref,
        token_budget=capsule.token_budget,
        context_ref_budget=capsule.context_ref_budget,
        time_budget_seconds=capsule.time_budget_seconds,
        repair_budget=capsule.repair_budget,
        reviewer_identity_ref=capsule.independent_reviewer_ref,
    )


__all__ = [
    "MAX_DEPTH",
    "MAX_FANOUT",
    "CancellationOrigin",
    "DelegationAttempt",
    "DelegationBinding",
    "DelegationError",
    "DelegationEvent",
    "DelegationRole",
    "DelegationStatus",
    "ParentDecision",
    "attempt_id",
    "binding_from_controller_capsule",
    "child_id",
    "delegation_event_id",
    "parent_decision_id",
    "validate_identifier",
    "validate_sha256",
]

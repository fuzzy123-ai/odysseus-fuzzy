"""Immutable, content-free role capsules for the autonomous coding loop.

Capsules are descriptors only.  They do not dispatch a child, execute a tool,
authorize an edit, close a gate, or write graph, memory, repository, or live
state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import re
from typing import Any

from src.runtime_event_envelope import stable_payload_hash


CODING_SUBAGENT_CAPSULE_SCHEMA = "odysseus.coding_subagent_capsule.v1"
MAX_CAPSULE_REFS = 64
MAX_TOKEN_BUDGET = 200_000
MAX_CONTEXT_REF_BUDGET = 64
MAX_TIME_BUDGET_SECONDS = 86_400
MAX_REPAIR_BUDGET = 2

_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+\-=]{1,180}$")
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|bearer|chat[_-]?id|cookie|credential|password|passwd|secret|token|api[_-]?key)\b"
)
_PRIVATE_PATH_RE = re.compile(
    r"(^|[\s'\"=])([A-Za-z]:[\\/]|/(home|Users|var|opt|mnt|srv)/|~[\\/])",
    re.IGNORECASE,
)
_RAW_KEYS = {
    "authorization", "body", "bytes", "chain_of_thought", "content",
    "conversation", "credential", "diff", "env", "html", "log", "message",
    "output", "password", "patch", "prompt", "raw", "raw_content", "secret",
    "snippet", "stderr", "stdout", "text", "token",
}


class CodingSubagentCapsuleError(ValueError):
    """Raised when a role capsule is unsafe or internally inconsistent."""


class CodingSubagentRole(StrEnum):
    IMPLEMENTER = "implementer"
    TESTER = "tester"
    REVIEWER = "reviewer"


class CodingSubagentLifecycleDescriptor(StrEnum):
    CAPSULE_READY = "capsule_ready"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    RESUME_REQUIRED = "resume_required"


@dataclass(frozen=True, slots=True)
class CodingSubagentCapsule:
    capsule_id: str
    role: CodingSubagentRole
    lifecycle_descriptor: CodingSubagentLifecycleDescriptor
    parent_envelope_id: str
    parent_run_id: str
    parent_slice_id: str
    planning_item_id: str
    planning_revision: str
    claim_id: str
    claim_owner: str
    scope_digest: str
    input_revision: str
    actor_id: str
    implementer_actor_id: str
    retrieval_identity_ref: str
    implementer_retrieval_identity_ref: str
    independent_reviewer_ref: str
    graph_semantic_digest: str
    graph_ref_ids: tuple[str, ...]
    provenance_refs: tuple[str, ...]
    retrieval_snapshot_refs: tuple[str, ...]
    implementer_retrieval_snapshot_refs: tuple[str, ...]
    acceptance_check_refs: tuple[str, ...]
    tool_capability_refs: tuple[str, ...]
    budget_policy_refs: tuple[str, ...]
    stop_rule_refs: tuple[str, ...]
    exact_read_refs: tuple[str, ...]
    cancellation_descriptor_ref: str
    expiry_descriptor_ref: str
    resume_descriptor_ref: str
    token_budget: int
    context_ref_budget: int
    time_budget_seconds: int
    repair_budget: int
    authority_effect: str = "none"
    execution_allowed: bool = False
    edit_allowed: bool = False
    write_allowed: bool = False
    dispatch_allowed: bool = False
    recursive_delegation_allowed: bool = False
    gate_close_allowed: bool = False
    graph_write_allowed: bool = False
    memory_write_allowed: bool = False
    network_allowed: bool = False
    secrets_allowed: bool = False
    live_effect_allowed: bool = False
    side_effects: tuple[str, ...] = ("none",)
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _enum(self.role, CodingSubagentRole, "role"))
        object.__setattr__(
            self,
            "lifecycle_descriptor",
            _enum(
                self.lifecycle_descriptor,
                CodingSubagentLifecycleDescriptor,
                "lifecycle_descriptor",
            ),
        )
        for field_name in (
            "parent_envelope_id", "parent_run_id", "parent_slice_id",
            "planning_item_id", "planning_revision", "claim_id", "claim_owner",
            "input_revision", "actor_id", "implementer_actor_id",
            "retrieval_identity_ref", "implementer_retrieval_identity_ref",
            "cancellation_descriptor_ref", "expiry_descriptor_ref",
            "resume_descriptor_ref",
        ):
            _strict_id(getattr(self, field_name), field_name)
        if self.parent_run_id != self.claim_id:
            raise CodingSubagentCapsuleError(
                "parent_run_id must equal authoritative claim_id"
            )
        if self.parent_slice_id != self.planning_item_id:
            raise CodingSubagentCapsuleError(
                "parent_slice_id must equal authoritative planning_item_id"
            )
        if self.independent_reviewer_ref:
            _strict_id(self.independent_reviewer_ref, "independent_reviewer_ref")
        for field_name in ("capsule_id", "scope_digest", "graph_semantic_digest"):
            _sha256(getattr(self, field_name), field_name)
        for field_name in (
            "graph_ref_ids", "provenance_refs", "retrieval_snapshot_refs",
            "implementer_retrieval_snapshot_refs", "acceptance_check_refs",
            "tool_capability_refs", "budget_policy_refs", "stop_rule_refs",
            "exact_read_refs",
        ):
            _canonical_refs(getattr(self, field_name), field_name)
        if (
            not self.graph_ref_ids
            or not self.provenance_refs
            or not self.retrieval_snapshot_refs
            or not self.implementer_retrieval_snapshot_refs
        ):
            raise CodingSubagentCapsuleError(
                "capsule requires bounded graph provenance and retrieval snapshots"
            )
        if not set(self.exact_read_refs).issubset(self.graph_ref_ids):
            raise CodingSubagentCapsuleError(
                "exact_read_refs must be selected graph refs"
            )
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
        context_count = len(
            set(
                (*self.graph_ref_ids, *self.acceptance_check_refs,
                 *self.tool_capability_refs, *self.budget_policy_refs,
                 *self.stop_rule_refs, *self.exact_read_refs)
            )
        )
        if context_count > self.context_ref_budget:
            raise CodingSubagentCapsuleError(
                "selected context exceeds context_ref_budget"
            )
        if self.role is CodingSubagentRole.IMPLEMENTER:
            if (
                self.actor_id != self.implementer_actor_id
                or self.retrieval_identity_ref
                != self.implementer_retrieval_identity_ref
                or self.retrieval_snapshot_refs
                != self.implementer_retrieval_snapshot_refs
            ):
                raise CodingSubagentCapsuleError(
                    "implementer capsule must match implementer identity and retrieval"
                )
            if self.repair_budget < 1 or not self.exact_read_refs:
                raise CodingSubagentCapsuleError(
                    "implementer requires repair budget and exact-read refs"
                )
            if self.independent_reviewer_ref:
                raise CodingSubagentCapsuleError(
                    "implementer cannot carry independent reviewer authority"
                )
        elif self.repair_budget != 0:
            raise CodingSubagentCapsuleError(
                "tester and reviewer capsules cannot carry repair budget"
            )
        if self.role is CodingSubagentRole.REVIEWER:
            if (
                self.actor_id in {self.implementer_actor_id, self.claim_owner}
                or not self.independent_reviewer_ref
                or self.retrieval_identity_ref
                == self.implementer_retrieval_identity_ref
                or set(self.retrieval_snapshot_refs)
                & set(self.implementer_retrieval_snapshot_refs)
                or not self.acceptance_check_refs
                or not self.exact_read_refs
            ):
                raise CodingSubagentCapsuleError(
                    "reviewer identity and retrieval must be independent"
                )
        elif self.independent_reviewer_ref:
            raise CodingSubagentCapsuleError(
                "independent_reviewer_ref belongs only to reviewer capsules"
            )
        if self.role is CodingSubagentRole.TESTER and not self.acceptance_check_refs:
            raise CodingSubagentCapsuleError(
                "tester capsule requires acceptance checks"
            )
        if self.authority_effect != "none" or self.side_effects != ("none",):
            raise CodingSubagentCapsuleError("capsule cannot change state")
        for field_name in (
            "execution_allowed", "edit_allowed", "write_allowed",
            "dispatch_allowed", "recursive_delegation_allowed",
            "gate_close_allowed", "graph_write_allowed", "memory_write_allowed",
            "network_allowed", "secrets_allowed", "live_effect_allowed",
        ):
            if getattr(self, field_name) is not False:
                raise CodingSubagentCapsuleError(
                    f"{field_name} must remain false"
                )
        if self.raw_content_visible is not False:
            raise CodingSubagentCapsuleError("capsule cannot expose raw content")
        if self.capsule_id != stable_payload_hash(self.semantic_dict()):
            raise CodingSubagentCapsuleError(
                "capsule_id does not match canonical capsule facts"
            )

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "lifecycle_descriptor": self.lifecycle_descriptor.value,
            "parent_envelope_id": self.parent_envelope_id,
            "parent_run_id": self.parent_run_id,
            "parent_slice_id": self.parent_slice_id,
            "planning_item_id": self.planning_item_id,
            "planning_revision": self.planning_revision,
            "claim_id": self.claim_id,
            "claim_owner": self.claim_owner,
            "scope_digest": self.scope_digest,
            "input_revision": self.input_revision,
            "actor_id": self.actor_id,
            "implementer_actor_id": self.implementer_actor_id,
            "retrieval_identity_ref": self.retrieval_identity_ref,
            "implementer_retrieval_identity_ref": self.implementer_retrieval_identity_ref,
            "independent_reviewer_ref": self.independent_reviewer_ref,
            "graph_semantic_digest": self.graph_semantic_digest,
            "graph_ref_ids": self.graph_ref_ids,
            "provenance_refs": self.provenance_refs,
            "retrieval_snapshot_refs": self.retrieval_snapshot_refs,
            "implementer_retrieval_snapshot_refs": self.implementer_retrieval_snapshot_refs,
            "acceptance_check_refs": self.acceptance_check_refs,
            "tool_capability_refs": self.tool_capability_refs,
            "budget_policy_refs": self.budget_policy_refs,
            "stop_rule_refs": self.stop_rule_refs,
            "exact_read_refs": self.exact_read_refs,
            "cancellation_descriptor_ref": self.cancellation_descriptor_ref,
            "expiry_descriptor_ref": self.expiry_descriptor_ref,
            "resume_descriptor_ref": self.resume_descriptor_ref,
            "token_budget": self.token_budget,
            "context_ref_budget": self.context_ref_budget,
            "time_budget_seconds": self.time_budget_seconds,
            "repair_budget": self.repair_budget,
            "authority_effect": "none",
            "execution_allowed": False,
            "edit_allowed": False,
            "write_allowed": False,
            "dispatch_allowed": False,
            "recursive_delegation_allowed": False,
            "gate_close_allowed": False,
            "graph_write_allowed": False,
            "memory_write_allowed": False,
            "network_allowed": False,
            "secrets_allowed": False,
            "live_effect_allowed": False,
            "side_effects": ("none",),
            "raw_content_visible": False,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": CODING_SUBAGENT_CAPSULE_SCHEMA,
            "capsule_id": self.capsule_id,
            **self.semantic_dict(),
        }
        _reject_unsafe_payload(payload)
        return payload


def create_coding_subagent_capsule(**facts: Any) -> CodingSubagentCapsule:
    """Create a capsule whose identifier binds every semantic field."""

    core = dict(facts)
    core.pop("capsule_id", None)
    for field_name, default in (
        ("authority_effect", "none"),
        ("execution_allowed", False),
        ("edit_allowed", False),
        ("write_allowed", False),
        ("dispatch_allowed", False),
        ("recursive_delegation_allowed", False),
        ("gate_close_allowed", False),
        ("graph_write_allowed", False),
        ("memory_write_allowed", False),
        ("network_allowed", False),
        ("secrets_allowed", False),
        ("live_effect_allowed", False),
        ("side_effects", ("none",)),
        ("raw_content_visible", False),
    ):
        core.setdefault(field_name, default)
    role = _enum(core.get("role"), CodingSubagentRole, "role")
    descriptor = _enum(
        core.get("lifecycle_descriptor"),
        CodingSubagentLifecycleDescriptor,
        "lifecycle_descriptor",
    )
    core["role"] = role.value
    core["lifecycle_descriptor"] = descriptor.value
    capsule_id = stable_payload_hash(core)
    return CodingSubagentCapsule(
        capsule_id=capsule_id,
        **facts,
    )


def _strict_id(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not _ID_RE.fullmatch(value)
        or _SECRET_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
    ):
        raise CodingSubagentCapsuleError(
            f"{field} must be a safe bounded identifier"
        )
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise CodingSubagentCapsuleError(f"{field} must be canonical SHA-256")
    return value


def _canonical_refs(values: Any, field: str) -> tuple[str, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > MAX_CAPSULE_REFS
        or values != tuple(sorted(values))
    ):
        raise CodingSubagentCapsuleError(
            f"{field} must be a canonical bounded tuple"
        )
    for value in values:
        _strict_id(value, field)
    if len(set(values)) != len(values):
        raise CodingSubagentCapsuleError(f"{field} contains duplicates")
    return values


def _budget(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CodingSubagentCapsuleError(f"{field} is outside its bounded range")
    return value


def _enum(value: Any, enum_type: type[StrEnum], field: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise CodingSubagentCapsuleError(f"{field} is invalid") from exc


def _reject_unsafe_payload(value: Any, *, key: str = "") -> None:
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in _RAW_KEYS:
        raise CodingSubagentCapsuleError("capsule contains a raw field")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _reject_unsafe_payload(child, key=str(child_key))
    elif isinstance(value, (tuple, list)):
        for child in value:
            _reject_unsafe_payload(child, key=key)
    elif isinstance(value, str) and (
        _SECRET_RE.search(value) or _PRIVATE_PATH_RE.search(value)
    ):
        raise CodingSubagentCapsuleError("capsule contains private material")
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise CodingSubagentCapsuleError(
            "capsule must be JSON serializable"
        ) from exc


__all__ = [
    "CODING_SUBAGENT_CAPSULE_SCHEMA",
    "MAX_CAPSULE_REFS",
    "MAX_CONTEXT_REF_BUDGET",
    "MAX_REPAIR_BUDGET",
    "MAX_TIME_BUDGET_SECONDS",
    "MAX_TOKEN_BUDGET",
    "CodingSubagentCapsule",
    "CodingSubagentCapsuleError",
    "CodingSubagentLifecycleDescriptor",
    "CodingSubagentRole",
    "create_coding_subagent_capsule",
]

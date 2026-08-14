"""Pure role-scoped broker for accepted coding context envelopes."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from src.coding_context_envelope import (
    CodingContextCheckpoint,
    CodingContextDisposition,
    CodingContextEnvelope,
)
from src.coding_graph_boundary import (
    CodingGraphConflict,
    CodingGraphFreshness,
    CodingGraphRef,
    CodingGraphStatus,
)
from src.coding_subagent_capsule import (
    MAX_CAPSULE_REFS,
    MAX_CONTEXT_REF_BUDGET,
    MAX_REPAIR_BUDGET,
    MAX_TIME_BUDGET_SECONDS,
    MAX_TOKEN_BUDGET,
    CodingSubagentCapsule,
    CodingSubagentCapsuleError,
    CodingSubagentLifecycleDescriptor,
    CodingSubagentRole,
    create_coding_subagent_capsule,
)
from src.runtime_event_envelope import stable_payload_hash


_ROLE_ORDER = (
    CodingSubagentRole.IMPLEMENTER,
    CodingSubagentRole.TESTER,
    CodingSubagentRole.REVIEWER,
)
_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+\-=]{1,180}$")
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|bearer|chat[_-]?id|cookie|credential|password|passwd|secret|token|api[_-]?key)\b"
)
_PRIVATE_PATH_RE = re.compile(
    r"(^|[\s'\"=])([A-Za-z]:[\\/]|/(home|Users|var|opt|mnt|srv)/|~[\\/])",
    re.IGNORECASE,
)


class CodingSubagentContextBrokerError(ValueError):
    """Raised when a role policy cannot be safely derived from its parent."""


@dataclass(frozen=True, slots=True)
class RolePolicy:
    role: CodingSubagentRole
    actor_id: str
    graph_ref_ids: tuple[str, ...]
    acceptance_check_refs: tuple[str, ...]
    tool_capability_refs: tuple[str, ...]
    budget_policy_refs: tuple[str, ...]
    stop_rule_refs: tuple[str, ...]
    exact_read_refs: tuple[str, ...]
    retrieval_identity_ref: str
    cancellation_descriptor_ref: str
    expiry_descriptor_ref: str
    resume_descriptor_ref: str
    token_budget: int
    context_ref_budget: int
    time_budget_seconds: int
    repair_budget: int
    lifecycle_descriptor: CodingSubagentLifecycleDescriptor = (
        CodingSubagentLifecycleDescriptor.CAPSULE_READY
    )
    independent_reviewer_ref: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _role(self.role))
        object.__setattr__(
            self,
            "lifecycle_descriptor",
            _descriptor(self.lifecycle_descriptor),
        )
        for field_name in (
            "actor_id", "retrieval_identity_ref", "cancellation_descriptor_ref",
            "expiry_descriptor_ref", "resume_descriptor_ref",
        ):
            _strict_id(getattr(self, field_name), field_name)
        if self.independent_reviewer_ref:
            _strict_id(self.independent_reviewer_ref, "independent_reviewer_ref")
        for field_name in (
            "graph_ref_ids", "acceptance_check_refs", "tool_capability_refs",
            "budget_policy_refs", "stop_rule_refs", "exact_read_refs",
        ):
            _canonical_refs(getattr(self, field_name), field_name)
        if not self.graph_ref_ids:
            raise CodingSubagentContextBrokerError(
                "role policy requires graph_ref_ids"
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
        if self.role is CodingSubagentRole.IMPLEMENTER:
            if self.repair_budget < 1 or not self.exact_read_refs:
                raise CodingSubagentContextBrokerError(
                    "implementer policy requires repair budget and exact reads"
                )
            if self.independent_reviewer_ref:
                raise CodingSubagentContextBrokerError(
                    "implementer cannot carry reviewer identity"
                )
        elif self.repair_budget != 0:
            raise CodingSubagentContextBrokerError(
                "tester and reviewer policies cannot carry repair budget"
            )
        if self.role is CodingSubagentRole.TESTER and not self.acceptance_check_refs:
            raise CodingSubagentContextBrokerError(
                "tester policy requires acceptance checks"
            )
        if self.role is CodingSubagentRole.REVIEWER:
            if (
                not self.independent_reviewer_ref
                or not self.acceptance_check_refs
                or not self.exact_read_refs
            ):
                raise CodingSubagentContextBrokerError(
                    "reviewer policy requires independent review, acceptance, and exact reads"
                )
        elif self.independent_reviewer_ref:
            raise CodingSubagentContextBrokerError(
                "independent_reviewer_ref belongs only to reviewer policy"
            )


def build_role_scoped_subagent_capsules(
    *,
    parent_envelope: CodingContextEnvelope,
    implementer_actor_id: str,
    role_policies: tuple[RolePolicy, ...],
) -> tuple[CodingSubagentCapsule, ...]:
    """Derive three immutable capsules without I/O, dispatch, or mutation."""

    if not isinstance(parent_envelope, CodingContextEnvelope):
        raise CodingSubagentContextBrokerError("parent_envelope must be typed")
    if (
        parent_envelope.checkpoint is not CodingContextCheckpoint.PRE_SLICE
        or parent_envelope.disposition is not CodingContextDisposition.READY
        or parent_envelope.blockers
        or parent_envelope.waiting_reasons
    ):
        raise CodingSubagentContextBrokerError(
            "broker requires a READY PRE_SLICE parent envelope"
        )
    for field_name in (
        "planning_item_id", "planning_revision", "claim_id", "claim_owner",
        "input_revision", "parent_envelope_id",
    ):
        value = (
            parent_envelope.envelope_id
            if field_name == "parent_envelope_id"
            else getattr(parent_envelope, field_name)
        )
        _strict_id(value, field_name)
    canonical_run_id = parent_envelope.claim_id
    canonical_slice_id = parent_envelope.planning_item_id
    safe_implementer = _strict_id(implementer_actor_id, "implementer_actor_id")
    if not isinstance(role_policies, tuple) or not all(
        isinstance(item, RolePolicy) for item in role_policies
    ):
        raise CodingSubagentContextBrokerError(
            "role_policies must be a typed tuple"
        )
    if (
        len(role_policies) != len(_ROLE_ORDER)
        or tuple(item.role for item in role_policies) != _ROLE_ORDER
    ):
        raise CodingSubagentContextBrokerError(
            "role_policies must be ordered implementer, tester, reviewer"
        )
    implementer_policy, _, reviewer_policy = role_policies
    if implementer_policy.actor_id != safe_implementer:
        raise CodingSubagentContextBrokerError(
            "implementer policy actor does not match implementer identity"
        )
    if reviewer_policy.actor_id in {safe_implementer, parent_envelope.claim_owner}:
        raise CodingSubagentContextBrokerError(
            "reviewer actor must differ from implementer and claim owner"
        )
    if reviewer_policy.retrieval_identity_ref == implementer_policy.retrieval_identity_ref:
        raise CodingSubagentContextBrokerError(
            "reviewer retrieval identity must be independent"
        )

    graph_by_id = {item.ref_id: item for item in parent_envelope.graph_refs}
    if len(graph_by_id) != len(parent_envelope.graph_refs):
        raise CodingSubagentContextBrokerError(
            "parent graph refs must have unique identities"
        )
    parent_sets = {
        "acceptance_check_refs": set(parent_envelope.acceptance_check_refs),
        "tool_capability_refs": set(parent_envelope.tool_capability_refs),
        "budget_policy_refs": set(parent_envelope.budget_policy_refs),
        "stop_rule_refs": set(parent_envelope.stop_rule_refs),
        "exact_read_refs": set(parent_envelope.exact_read_required),
    }
    capsules: list[CodingSubagentCapsule] = []
    implementer_snapshots: tuple[str, ...] = ()
    for policy in role_policies:
        selected_ids = set(policy.graph_ref_ids)
        if not selected_ids.issubset(graph_by_id):
            raise CodingSubagentContextBrokerError(
                f"{policy.role.value} graph policy widens parent context"
            )
        for field_name, parent_values in parent_sets.items():
            selected_values = set(getattr(policy, field_name))
            if not selected_values.issubset(parent_values):
                raise CodingSubagentContextBrokerError(
                    f"{policy.role.value} {field_name} widens parent policy"
                )
        if not set(policy.exact_read_refs).issubset(selected_ids):
            raise CodingSubagentContextBrokerError(
                f"{policy.role.value} exact reads must be selected graph refs"
            )
        selected_graph = tuple(graph_by_id[item] for item in policy.graph_ref_ids)
        _validate_selected_graph(selected_graph, policy.role)
        provenance_refs = tuple(
            sorted(
                {
                    provenance
                    for graph_ref in selected_graph
                    for provenance in graph_ref.provenance_refs
                }
            )
        )
        snapshots = tuple(
            sorted({item.retrieval_snapshot_ref for item in selected_graph})
        )
        if policy.role is CodingSubagentRole.IMPLEMENTER:
            implementer_snapshots = snapshots
        elif policy.role is CodingSubagentRole.REVIEWER and (
            set(snapshots) & set(implementer_snapshots)
        ):
            raise CodingSubagentContextBrokerError(
                "reviewer retrieval snapshots cannot copy implementer snapshots"
            )
        graph_digest = stable_payload_hash(
            tuple(item.semantic_dict() for item in selected_graph)
        )
        try:
            capsule = create_coding_subagent_capsule(
                role=policy.role,
                lifecycle_descriptor=policy.lifecycle_descriptor,
                parent_envelope_id=parent_envelope.envelope_id,
                parent_run_id=canonical_run_id,
                parent_slice_id=canonical_slice_id,
                planning_item_id=parent_envelope.planning_item_id,
                planning_revision=parent_envelope.planning_revision,
                claim_id=parent_envelope.claim_id,
                claim_owner=parent_envelope.claim_owner,
                scope_digest=parent_envelope.scope_digest,
                input_revision=parent_envelope.input_revision,
                actor_id=policy.actor_id,
                implementer_actor_id=safe_implementer,
                retrieval_identity_ref=policy.retrieval_identity_ref,
                implementer_retrieval_identity_ref=(
                    implementer_policy.retrieval_identity_ref
                ),
                independent_reviewer_ref=policy.independent_reviewer_ref,
                graph_semantic_digest=graph_digest,
                graph_ref_ids=policy.graph_ref_ids,
                provenance_refs=provenance_refs,
                retrieval_snapshot_refs=snapshots,
                implementer_retrieval_snapshot_refs=implementer_snapshots,
                acceptance_check_refs=policy.acceptance_check_refs,
                tool_capability_refs=policy.tool_capability_refs,
                budget_policy_refs=policy.budget_policy_refs,
                stop_rule_refs=policy.stop_rule_refs,
                exact_read_refs=policy.exact_read_refs,
                cancellation_descriptor_ref=policy.cancellation_descriptor_ref,
                expiry_descriptor_ref=policy.expiry_descriptor_ref,
                resume_descriptor_ref=policy.resume_descriptor_ref,
                token_budget=policy.token_budget,
                context_ref_budget=policy.context_ref_budget,
                time_budget_seconds=policy.time_budget_seconds,
                repair_budget=policy.repair_budget,
            )
        except CodingSubagentCapsuleError as exc:
            raise CodingSubagentContextBrokerError(str(exc)) from exc
        capsules.append(capsule)
    return tuple(capsules)


def _validate_selected_graph(
    graph_refs: tuple[CodingGraphRef, ...], role: CodingSubagentRole
) -> None:
    if not graph_refs:
        raise CodingSubagentContextBrokerError(
            f"{role.value} capsule requires graph context"
        )
    for graph_ref in graph_refs:
        if (
            graph_ref.status is not CodingGraphStatus.AVAILABLE
            or graph_ref.freshness
            not in {CodingGraphFreshness.CURRENT, CodingGraphFreshness.RECENT}
            or graph_ref.conflict is not CodingGraphConflict.NONE
            or graph_ref.authority_effect != "none"
            or graph_ref.raw_content_visible is not False
        ):
            raise CodingSubagentContextBrokerError(
                f"{graph_ref.ref_id}:selected graph context is not usable"
            )


def _strict_id(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not _ID_RE.fullmatch(value)
        or _SECRET_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
    ):
        raise CodingSubagentContextBrokerError(
            f"{field} must be a safe bounded identifier"
        )
    return value


def _canonical_refs(values: Any, field: str) -> tuple[str, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > MAX_CAPSULE_REFS
        or values != tuple(sorted(values))
    ):
        raise CodingSubagentContextBrokerError(
            f"{field} must be a canonical bounded tuple"
        )
    for value in values:
        _strict_id(value, field)
    if len(set(values)) != len(values):
        raise CodingSubagentContextBrokerError(f"{field} contains duplicates")
    return values


def _budget(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CodingSubagentContextBrokerError(
            f"{field} is outside its bounded range"
        )
    return value


def _role(value: Any) -> CodingSubagentRole:
    try:
        return value if isinstance(value, CodingSubagentRole) else CodingSubagentRole(value)
    except (TypeError, ValueError) as exc:
        raise CodingSubagentContextBrokerError("role is invalid") from exc


def _descriptor(value: Any) -> CodingSubagentLifecycleDescriptor:
    try:
        descriptor = (
            value
            if isinstance(value, CodingSubagentLifecycleDescriptor)
            else CodingSubagentLifecycleDescriptor(value)
        )
    except (TypeError, ValueError) as exc:
        raise CodingSubagentContextBrokerError(
            "lifecycle_descriptor is invalid"
        ) from exc
    if descriptor is not CodingSubagentLifecycleDescriptor.CAPSULE_READY:
        raise CodingSubagentContextBrokerError(
            "broker only creates capsule_ready descriptors"
        )
    return descriptor


__all__ = [
    "CodingSubagentContextBrokerError",
    "RolePolicy",
    "build_role_scoped_subagent_capsules",
]

"""Planning-bound, content-free context envelopes for four coding checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import re
from typing import Any, Mapping

from src.coding_graph_boundary import (
    CodingGraphBoundaryError,
    CodingGraphKind,
    CodingGraphRef,
    authority_scope_digest,
    evaluate_coding_graph_boundary,
)
from src.coding_lifecycle_authority import (
    AuthorizedCodingLifecycleState,
    CodingLifecycleAuthorityError,
    CodingLifecycleCompletionProof,
)
from src.runtime_event_envelope import stable_payload_hash


CODING_CONTEXT_ENVELOPE_SCHEMA = "odysseus.coding_context_envelope.v1"
MAX_POST_ACCEPTANCE_INTENTS = 16
MAX_POLICY_REFS = 32

_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+\-=]{1,180}$")
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_OWNER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}:[^\s:*]{1,127}$")
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|bearer|chat[_-]?id|cookie|credential|password|passwd|secret|token|api[_-]?key)\b"
)
_PRIVATE_PATH_RE = re.compile(
    r"(^|[\s'\"=])([A-Za-z]:[\\/]|/(home|Users|var|opt|mnt|srv)/|~[\\/])",
    re.IGNORECASE,
)
_RAW_KEYS = {
    "authorization", "body", "bytes", "content", "credential", "diff", "env",
    "html", "log", "message", "output", "password", "patch", "prompt", "raw",
    "raw_content", "secret", "snippet", "stderr", "stdout", "text", "token",
}


class CodingContextEnvelopeError(ValueError):
    """Raised when a coding context envelope request is unsafe or malformed."""


class CodingContextCheckpoint(StrEnum):
    PRE_PLAN = "pre_plan"
    PRE_SLICE = "pre_slice"
    FAILURE_RETRIEVAL = "failure_retrieval"
    POST_ACCEPTANCE_WRITEBACK = "post_acceptance_writeback"


class CodingContextDisposition(StrEnum):
    READY = "ready"
    WAITING = "waiting"
    BLOCKED = "blocked"


_CHECKPOINT_STATES = {
    CodingContextCheckpoint.PRE_PLAN: frozenset({"clarifying", "planning"}),
    CodingContextCheckpoint.PRE_SLICE: frozenset(
        {"claimed", "context_building", "context_ready", "worktree_ready"}
    ),
    CodingContextCheckpoint.FAILURE_RETRIEVAL: frozenset(
        {"verifying", "repair_planning"}
    ),
    CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK: frozenset(
        {"review_ready", "memory_review", "publish_ready", "done"}
    ),
}
_CHECKPOINT_GRAPH_KINDS = {
    CodingContextCheckpoint.PRE_PLAN: frozenset(
        {CodingGraphKind.PLANNING, CodingGraphKind.CODE, CodingGraphKind.CAUSAL}
    ),
    CodingContextCheckpoint.PRE_SLICE: frozenset(
        {CodingGraphKind.CODE, CodingGraphKind.CAUSAL}
    ),
    CodingContextCheckpoint.FAILURE_RETRIEVAL: frozenset(
        {CodingGraphKind.CODE, CodingGraphKind.CAUSAL}
    ),
    CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK: frozenset(
        {CodingGraphKind.CODE, CodingGraphKind.CAUSAL, CodingGraphKind.MEMORY}
    ),
}


@dataclass(frozen=True, slots=True)
class PostAcceptanceIntentRef:
    intent_ref: str
    target_graph: CodingGraphKind
    planning_revision: str
    input_revision: str
    scope_digest: str
    acceptance_decision_id: str
    evidence_id: str
    reviewer_id: str
    authority_effect: str = "none"
    execution_allowed: bool = False
    write_allowed: bool = False
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "intent_ref", "planning_revision", "input_revision",
            "acceptance_decision_id", "evidence_id", "reviewer_id",
        ):
            _strict_id(getattr(self, field_name), field_name)
        if not isinstance(self.scope_digest, str) or not _SHA256_RE.fullmatch(self.scope_digest):
            raise CodingContextEnvelopeError("scope_digest must be canonical SHA-256")
        object.__setattr__(self, "target_graph", _enum(self.target_graph, CodingGraphKind, "target_graph"))
        if self.target_graph is CodingGraphKind.PLANNING:
            raise CodingContextEnvelopeError("post-acceptance intent cannot target Planning")
        if self.authority_effect != "none":
            raise CodingContextEnvelopeError("post-acceptance intent cannot change authority")
        if self.execution_allowed is not False or self.write_allowed is not False:
            raise CodingContextEnvelopeError("post-acceptance intent cannot execute or write")
        if self.raw_content_visible is not False:
            raise CodingContextEnvelopeError("post-acceptance intent cannot expose raw content")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": "odysseus.coding_post_acceptance_intent_ref.v1",
            "intent_ref": self.intent_ref,
            "target_graph": self.target_graph.value,
            "planning_revision": self.planning_revision,
            "input_revision": self.input_revision,
            "scope_digest": self.scope_digest,
            "acceptance_decision_id": self.acceptance_decision_id,
            "evidence_id": self.evidence_id,
            "reviewer_id": self.reviewer_id,
            "authority_effect": "none",
            "execution_allowed": False,
            "write_allowed": False,
            "side_effects": ("none",),
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(payload)
        return payload

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "intent_ref": self.intent_ref,
            "target_graph": self.target_graph.value,
            "planning_revision": self.planning_revision,
            "input_revision": self.input_revision,
            "scope_digest": self.scope_digest,
            "acceptance_decision_id": self.acceptance_decision_id,
            "evidence_id": self.evidence_id,
            "reviewer_id": self.reviewer_id,
            "authority_effect": "none",
            "execution_allowed": False,
            "write_allowed": False,
        }


@dataclass(frozen=True, slots=True)
class CodingContextEnvelope:
    envelope_id: str
    checkpoint: CodingContextCheckpoint
    disposition: CodingContextDisposition
    lifecycle_state: str
    planning_item_id: str
    planning_revision: str
    authority_digest: str
    claim_id: str
    claim_owner: str
    scope_digest: str
    input_revision: str
    objective_ref: str
    objective_digest: str
    acceptance_criteria_id: str
    owner_scope: str
    graph_input_digest: str
    acceptance_check_refs: tuple[str, ...]
    tool_capability_refs: tuple[str, ...]
    budget_policy_refs: tuple[str, ...]
    stop_rule_refs: tuple[str, ...]
    trigger_evidence_ref: str
    graph_refs: tuple[CodingGraphRef, ...]
    excluded_graph_ref_ids: tuple[str, ...]
    warnings: tuple[str, ...]
    waiting_reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    exact_read_required: tuple[str, ...]
    parent_envelope_id: str = ""
    post_acceptance_intents: tuple[PostAcceptanceIntentRef, ...] = ()
    authority_effect: str = "none"
    side_effects: tuple[str, ...] = ("none",)
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "planning_item_id", "planning_revision", "input_revision", "objective_ref",
            "acceptance_criteria_id", "lifecycle_state",
        ):
            _strict_id(getattr(self, field_name), field_name)
        for field_name in (
            "envelope_id", "authority_digest", "scope_digest", "graph_input_digest",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise CodingContextEnvelopeError(f"{field_name} must be canonical SHA-256")
        if self.claim_id:
            _strict_id(self.claim_id, "claim_id")
        if self.claim_owner:
            _strict_id(self.claim_owner, "claim_owner")
        if self.parent_envelope_id:
            _strict_id(self.parent_envelope_id, "parent_envelope_id")
        if self.trigger_evidence_ref:
            _strict_id(self.trigger_evidence_ref, "trigger_evidence_ref")
        if not _SHA256_RE.fullmatch(self.objective_digest):
            raise CodingContextEnvelopeError("objective_digest must be canonical SHA-256")
        _owner_scope(self.owner_scope)
        object.__setattr__(self, "checkpoint", _enum(self.checkpoint, CodingContextCheckpoint, "checkpoint"))
        object.__setattr__(self, "disposition", _enum(self.disposition, CodingContextDisposition, "disposition"))
        for field_name in (
            "acceptance_check_refs", "tool_capability_refs", "budget_policy_refs",
            "stop_rule_refs",
        ):
            values = getattr(self, field_name)
            _bounded_ids(values, field_name, MAX_POLICY_REFS)
            if values != tuple(sorted(values)):
                raise CodingContextEnvelopeError(f"{field_name} must be canonical")
        if self.checkpoint is CodingContextCheckpoint.FAILURE_RETRIEVAL:
            if not self.trigger_evidence_ref:
                raise CodingContextEnvelopeError(
                    "failure retrieval requires trigger_evidence_ref"
                )
        elif self.trigger_evidence_ref:
            raise CodingContextEnvelopeError(
                "trigger_evidence_ref is only valid for failure retrieval"
            )
        if not isinstance(self.graph_refs, tuple) or not all(
            isinstance(item, CodingGraphRef) for item in self.graph_refs
        ):
            raise CodingContextEnvelopeError("graph_refs must be typed")
        if len(self.graph_refs) > 64 or len({item.ref_id for item in self.graph_refs}) != len(self.graph_refs):
            raise CodingContextEnvelopeError("graph_refs must be unique and bounded")
        if not isinstance(self.post_acceptance_intents, tuple) or not all(
            isinstance(item, PostAcceptanceIntentRef) for item in self.post_acceptance_intents
        ):
            raise CodingContextEnvelopeError("post_acceptance_intents must be typed")
        if (
            len(self.post_acceptance_intents) > MAX_POST_ACCEPTANCE_INTENTS
            or len({item.intent_ref for item in self.post_acceptance_intents})
            != len(self.post_acceptance_intents)
        ):
            raise CodingContextEnvelopeError("post_acceptance_intents must be unique and bounded")
        for field_name in (
            "excluded_graph_ref_ids", "warnings", "waiting_reasons", "blockers",
            "exact_read_required",
        ):
            _bounded_ids(getattr(self, field_name), field_name, 64)
        if self.blockers and self.disposition is not CodingContextDisposition.BLOCKED:
            raise CodingContextEnvelopeError("blockers require blocked disposition")
        if not self.blockers and self.waiting_reasons and self.disposition is not CodingContextDisposition.WAITING:
            raise CodingContextEnvelopeError("waiting reasons require waiting disposition")
        if not self.blockers and not self.waiting_reasons and self.disposition is not CodingContextDisposition.READY:
            raise CodingContextEnvelopeError("empty holds require ready disposition")
        audited_ids = {item.ref_id for item in self.graph_refs} | set(self.excluded_graph_ref_ids)
        if set(self.exact_read_required) - audited_ids:
            raise CodingContextEnvelopeError("exact_read_required must map to audited graph refs")
        if {item.ref_id for item in self.graph_refs} & set(self.excluded_graph_ref_ids):
            raise CodingContextEnvelopeError("included and excluded graph refs overlap")
        for item in self.graph_refs:
            if (
                item.planning_revision != self.planning_revision
                or item.planning_item_id != self.planning_item_id
                or item.claim_id != self.claim_id
                or item.claim_owner != self.claim_owner
                or item.input_revision != self.input_revision
                or item.scope_digest != self.scope_digest
                or item.owner_scope != self.owner_scope
            ):
                raise CodingContextEnvelopeError("included graph ref escapes envelope authority")
        for intent in self.post_acceptance_intents:
            if (
                self.checkpoint is not CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK
                or self.disposition is not CodingContextDisposition.READY
                or intent.planning_revision != self.planning_revision
                or intent.input_revision != self.input_revision
                or intent.scope_digest != self.scope_digest
            ):
                raise CodingContextEnvelopeError("post-acceptance intent escapes envelope authority")
        if self.authority_effect != "none" or self.side_effects != ("none",):
            raise CodingContextEnvelopeError("context envelope cannot change state")
        if self.raw_content_visible is not False:
            raise CodingContextEnvelopeError("context envelope cannot expose raw content")
        if self.envelope_id != stable_payload_hash(_envelope_core(self)):
            raise CodingContextEnvelopeError("envelope_id does not match canonical envelope facts")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": CODING_CONTEXT_ENVELOPE_SCHEMA,
            "envelope_id": self.envelope_id,
            "checkpoint": self.checkpoint.value,
            "disposition": self.disposition.value,
            "lifecycle_state": self.lifecycle_state,
            "planning": {
                "item_id": self.planning_item_id,
                "revision": self.planning_revision,
                "authority_digest": self.authority_digest,
                "acceptance_criteria_id": self.acceptance_criteria_id,
            },
            "claim": {
                "claim_id": self.claim_id,
                "owner": self.claim_owner,
                "scope_digest": self.scope_digest,
            },
            "input_revision": self.input_revision,
            "objective_ref": self.objective_ref,
            "objective_digest": self.objective_digest,
            "owner_scope": self.owner_scope,
            "graph_input_digest": self.graph_input_digest,
            "acceptance_check_refs": self.acceptance_check_refs,
            "tool_capability_refs": self.tool_capability_refs,
            "budget_policy_refs": self.budget_policy_refs,
            "stop_rule_refs": self.stop_rule_refs,
            "trigger_evidence_ref": self.trigger_evidence_ref,
            "graph_refs": tuple(item.to_dict() for item in self.graph_refs),
            "excluded_graph_ref_ids": self.excluded_graph_ref_ids,
            "warnings": self.warnings,
            "waiting_reasons": self.waiting_reasons,
            "blockers": self.blockers,
            "exact_read_required": self.exact_read_required,
            "parent_envelope_id": self.parent_envelope_id,
            "post_acceptance_intents": tuple(
                item.to_dict() for item in self.post_acceptance_intents
            ),
            "authority_effect": "none",
            "execution_allowed": False,
            "write_allowed": False,
            "dispatch_allowed": False,
            "live_effect_allowed": False,
            "side_effects": ("none",),
            "raw_content_visible": False,
        }
        _reject_unsafe_payload(payload)
        return payload


def build_coding_context_envelope(
    *,
    checkpoint: CodingContextCheckpoint | str,
    lifecycle: AuthorizedCodingLifecycleState,
    owner_scope: str,
    input_revision: str,
    objective_ref: str,
    objective_digest: str,
    graph_refs: tuple[CodingGraphRef, ...] = (),
    acceptance_check_refs: tuple[str, ...] = (),
    tool_capability_refs: tuple[str, ...] = (),
    budget_policy_refs: tuple[str, ...] = (),
    stop_rule_refs: tuple[str, ...] = (),
    trigger_evidence_ref: str = "",
    parent_envelope: CodingContextEnvelope | None = None,
    completion_proof: CodingLifecycleCompletionProof | Mapping[str, Any] | None = None,
    post_acceptance_intents: tuple[PostAcceptanceIntentRef, ...] = (),
) -> CodingContextEnvelope:
    """Build one deterministic checkpoint envelope without performing I/O."""

    selected_checkpoint = _enum(checkpoint, CodingContextCheckpoint, "checkpoint")
    if not isinstance(lifecycle, AuthorizedCodingLifecycleState):
        raise CodingContextEnvelopeError("lifecycle must be typed")
    if not isinstance(graph_refs, tuple):
        raise CodingContextEnvelopeError("graph_refs must be a tuple")
    if not isinstance(post_acceptance_intents, tuple) or len(post_acceptance_intents) > MAX_POST_ACCEPTANCE_INTENTS:
        raise CodingContextEnvelopeError("post_acceptance_intents must be bounded")
    if not all(isinstance(item, PostAcceptanceIntentRef) for item in post_acceptance_intents):
        raise CodingContextEnvelopeError("post_acceptance_intents must be typed")
    supplied_policy_refs: dict[str, tuple[str, ...]] = {}
    for field_name, values in (
        ("acceptance_check_refs", acceptance_check_refs),
        ("tool_capability_refs", tool_capability_refs),
        ("budget_policy_refs", budget_policy_refs),
        ("stop_rule_refs", stop_rule_refs),
    ):
        _bounded_ids(values, field_name, MAX_POLICY_REFS)
        supplied_policy_refs[field_name] = tuple(sorted(values))
    if selected_checkpoint is CodingContextCheckpoint.FAILURE_RETRIEVAL:
        safe_trigger_evidence_ref = _strict_id(
            trigger_evidence_ref, "trigger_evidence_ref"
        )
    elif trigger_evidence_ref:
        raise CodingContextEnvelopeError(
            "trigger_evidence_ref is only valid for failure retrieval"
        )
    else:
        safe_trigger_evidence_ref = ""
    expected_input = _strict_id(input_revision, "input_revision")
    safe_objective_ref = _strict_id(objective_ref, "objective_ref")
    if not isinstance(objective_digest, str) or not _SHA256_RE.fullmatch(objective_digest):
        raise CodingContextEnvelopeError("objective_digest must be canonical SHA-256")
    _owner_scope(owner_scope)

    authority = lifecycle.authority
    if not authority.planning_item_id or not authority.planning_revision:
        raise CodingContextEnvelopeError(
            "Planning authority requires planning_item_id and planning_revision"
        )
    scope_digest = authority_scope_digest(authority)
    warnings: list[str] = []
    waiting: list[str] = []
    blockers: list[str] = []

    accepted_state = lifecycle.last_accepted_state or lifecycle.state
    if lifecycle.state == "waiting":
        waiting.append("lifecycle_waiting")
    elif lifecycle.state == "blocked":
        blockers.append("lifecycle_blocked")
    if owner_scope != f"repo:{lifecycle.repo_id}":
        blockers.append("lifecycle_owner_scope_mismatch")
    if accepted_state not in _CHECKPOINT_STATES[selected_checkpoint]:
        blockers.append("checkpoint_state_mismatch")
    if not authority.input_revision:
        waiting.append("authority_input_revision_missing")
    elif authority.input_revision != expected_input:
        blockers.append("authority_input_revision_mismatch")
    if selected_checkpoint is not CodingContextCheckpoint.PRE_PLAN:
        for field_name in ("claim_id", "claim_owner", "claim_scope_digest"):
            if not getattr(authority, field_name):
                waiting.append(f"authority_{field_name}_missing")

    invalid_kinds = tuple(
        item.ref_id
        for item in graph_refs
        if item.graph_kind not in _CHECKPOINT_GRAPH_KINDS[selected_checkpoint]
    )
    blockers.extend(f"{ref_id}:graph_kind_not_allowed" for ref_id in invalid_kinds)
    eligible_refs = tuple(item for item in graph_refs if item.ref_id not in invalid_kinds)
    try:
        boundary = evaluate_coding_graph_boundary(
            eligible_refs,
            authority=authority,
            owner_scope=owner_scope,
            input_revision=expected_input,
        )
    except CodingGraphBoundaryError as exc:
        raise CodingContextEnvelopeError(str(exc)) from exc
    warnings.extend(boundary.warnings)
    waiting.extend(boundary.waiting_reasons)
    blockers.extend(boundary.blockers)
    if selected_checkpoint in {
        CodingContextCheckpoint.PRE_PLAN,
        CodingContextCheckpoint.PRE_SLICE,
        CodingContextCheckpoint.FAILURE_RETRIEVAL,
    } and not graph_refs:
        waiting.append("checkpoint_context_missing")

    parent_id = ""
    if selected_checkpoint in {
        CodingContextCheckpoint.FAILURE_RETRIEVAL,
        CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK,
    }:
        if parent_envelope is None:
            waiting.append("parent_envelope_missing")
        elif not isinstance(parent_envelope, CodingContextEnvelope):
            raise CodingContextEnvelopeError("parent_envelope must be typed")
        else:
            parent_id = parent_envelope.envelope_id
            if parent_envelope.disposition is CodingContextDisposition.BLOCKED:
                blockers.append("parent_envelope_blocked")
            elif parent_envelope.disposition is CodingContextDisposition.WAITING:
                waiting.append("parent_envelope_waiting")
            if parent_envelope.planning_item_id != authority.planning_item_id:
                blockers.append("parent_planning_item_mismatch")
            if parent_envelope.planning_revision != authority.planning_revision:
                blockers.append("parent_planning_revision_mismatch")
            if (
                parent_envelope.claim_id != authority.claim_id
                or parent_envelope.claim_owner != authority.claim_owner
            ):
                blockers.append("parent_claim_identity_mismatch")
            if parent_envelope.scope_digest != scope_digest:
                blockers.append("parent_scope_digest_mismatch")
            if parent_envelope.input_revision != expected_input:
                blockers.append("parent_input_revision_mismatch")

    effective_policy_refs = dict(supplied_policy_refs)
    if (
        selected_checkpoint is CodingContextCheckpoint.FAILURE_RETRIEVAL
        and isinstance(parent_envelope, CodingContextEnvelope)
    ):
        if parent_envelope.checkpoint is not CodingContextCheckpoint.PRE_SLICE:
            blockers.append("parent_checkpoint_mismatch")
        for field_name in effective_policy_refs:
            parent_values = getattr(parent_envelope, field_name)
            supplied_values = effective_policy_refs[field_name]
            if supplied_values and supplied_values != parent_values:
                blockers.append(f"parent_{field_name}_mismatch")
            effective_policy_refs[field_name] = parent_values
    if selected_checkpoint in {
        CodingContextCheckpoint.PRE_SLICE,
        CodingContextCheckpoint.FAILURE_RETRIEVAL,
    }:
        for field_name, values in effective_policy_refs.items():
            if not values:
                waiting.append(f"{field_name}_missing")

    accepted_intents: tuple[PostAcceptanceIntentRef, ...] = ()
    if selected_checkpoint is not CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK:
        if post_acceptance_intents or completion_proof is not None:
            blockers.append("post_acceptance_material_outside_checkpoint")
    else:
        if not post_acceptance_intents:
            waiting.append("post_acceptance_intent_missing")
        proof = _completion_proof(completion_proof)
        if proof is None:
            waiting.append("independent_completion_proof_missing")
        else:
            proof_errors = _proof_errors(lifecycle, proof)
            blockers.extend(proof_errors)
            for intent in post_acceptance_intents:
                if intent.planning_revision != authority.planning_revision:
                    blockers.append(f"{intent.intent_ref}:planning_revision_mismatch")
                if intent.input_revision != expected_input:
                    blockers.append(f"{intent.intent_ref}:input_revision_mismatch")
                if intent.scope_digest != scope_digest:
                    blockers.append(f"{intent.intent_ref}:scope_digest_mismatch")
                if (
                    intent.acceptance_decision_id != proof.acceptance_decision_id
                    or intent.evidence_id != proof.evidence_id
                    or intent.reviewer_id != proof.reviewer_id
                ):
                    blockers.append(f"{intent.intent_ref}:completion_proof_mismatch")
            intent_prefixes = tuple(f"{item.intent_ref}:" for item in post_acceptance_intents)
            if not proof_errors and not waiting and not any(
                blocker.startswith(intent_prefixes) for blocker in blockers
            ) and not blockers:
                accepted_intents = tuple(sorted(post_acceptance_intents, key=lambda item: item.intent_ref))

    disposition = (
        CodingContextDisposition.BLOCKED
        if blockers
        else CodingContextDisposition.WAITING
        if waiting
        else CodingContextDisposition.READY
    )
    graph_input_digest = stable_payload_hash(
        tuple(item.semantic_dict() for item in sorted(graph_refs, key=lambda value: value.ref_id))
    )
    core = {
        "checkpoint": selected_checkpoint.value,
        "disposition": disposition.value,
        "lifecycle_state": lifecycle.state,
        "planning_item_id": authority.planning_item_id,
        "planning_revision": authority.planning_revision,
        "authority_digest": stable_payload_hash(authority.to_dict()),
        "claim_id": authority.claim_id,
        "claim_owner": authority.claim_owner,
        "scope_digest": scope_digest,
        "input_revision": expected_input,
        "objective_ref": safe_objective_ref,
        "objective_digest": objective_digest,
        "acceptance_criteria_id": authority.acceptance_criteria_id,
        "owner_scope": owner_scope,
        "graph_input_digest": graph_input_digest,
        "acceptance_check_refs": effective_policy_refs["acceptance_check_refs"],
        "tool_capability_refs": effective_policy_refs["tool_capability_refs"],
        "budget_policy_refs": effective_policy_refs["budget_policy_refs"],
        "stop_rule_refs": effective_policy_refs["stop_rule_refs"],
        "trigger_evidence_ref": safe_trigger_evidence_ref,
        "graph_refs": tuple(item.semantic_dict() for item in boundary.included_refs),
        "excluded_graph_ref_ids": tuple(
            dict.fromkeys((*boundary.excluded_ref_ids, *invalid_kinds))
        ),
        "warnings": tuple(dict.fromkeys(warnings)),
        "waiting_reasons": tuple(dict.fromkeys(waiting)),
        "blockers": tuple(dict.fromkeys(blockers)),
        "exact_read_required": boundary.exact_read_required,
        "parent_envelope_id": parent_id,
        "post_acceptance_intents": tuple(item.semantic_dict() for item in accepted_intents),
    }
    return CodingContextEnvelope(
        envelope_id=stable_payload_hash(core),
        checkpoint=selected_checkpoint,
        disposition=disposition,
        lifecycle_state=lifecycle.state,
        planning_item_id=authority.planning_item_id,
        planning_revision=authority.planning_revision,
        authority_digest=core["authority_digest"],
        claim_id=authority.claim_id,
        claim_owner=authority.claim_owner,
        scope_digest=scope_digest,
        input_revision=expected_input,
        objective_ref=safe_objective_ref,
        objective_digest=objective_digest,
        acceptance_criteria_id=authority.acceptance_criteria_id,
        owner_scope=owner_scope,
        graph_input_digest=graph_input_digest,
        acceptance_check_refs=core["acceptance_check_refs"],
        tool_capability_refs=core["tool_capability_refs"],
        budget_policy_refs=core["budget_policy_refs"],
        stop_rule_refs=core["stop_rule_refs"],
        trigger_evidence_ref=safe_trigger_evidence_ref,
        graph_refs=boundary.included_refs,
        excluded_graph_ref_ids=core["excluded_graph_ref_ids"],
        warnings=core["warnings"],
        waiting_reasons=core["waiting_reasons"],
        blockers=core["blockers"],
        exact_read_required=boundary.exact_read_required,
        parent_envelope_id=parent_id,
        post_acceptance_intents=accepted_intents,
    )


def _completion_proof(
    value: CodingLifecycleCompletionProof | Mapping[str, Any] | None,
) -> CodingLifecycleCompletionProof | None:
    try:
        return CodingLifecycleCompletionProof.from_value(value)
    except CodingLifecycleAuthorityError as exc:
        raise CodingContextEnvelopeError(str(exc)) from exc


def _proof_errors(
    lifecycle: AuthorizedCodingLifecycleState,
    proof: CodingLifecycleCompletionProof,
) -> tuple[str, ...]:
    authority = lifecycle.authority
    errors: list[str] = []
    if proof.acceptance_decision_id != authority.acceptance_decision_id:
        errors.append("completion_acceptance_mismatch")
    if proof.evidence_id != authority.evidence_id:
        errors.append("completion_evidence_mismatch")
    if not proof.all_required_gates_closed:
        errors.append("completion_gates_open")
    if not proof.independent_review:
        errors.append("completion_independent_review_missing")
    if proof.reviewer_id == authority.claim_owner:
        errors.append("completion_reviewer_not_independent")
    return tuple(errors)


def _strict_id(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not _ID_RE.fullmatch(value)
        or _SECRET_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
    ):
        raise CodingContextEnvelopeError(f"{field} must be a safe bounded identifier")
    return value


def _bounded_ids(values: tuple[str, ...], field: str, maximum: int) -> None:
    if not isinstance(values, tuple) or len(values) > maximum:
        raise CodingContextEnvelopeError(f"{field} must be a bounded tuple")
    for value in values:
        _strict_id(value, field)
    if len(set(values)) != len(values):
        raise CodingContextEnvelopeError(f"{field} contains duplicates")


def _owner_scope(value: Any) -> str:
    if not isinstance(value, str) or not _OWNER_RE.fullmatch(value):
        raise CodingContextEnvelopeError("owner_scope must be explicit and bounded")
    identifier = value.split(":", 1)[1]
    if (
        _SECRET_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
        or any(marker in identifier for marker in ("/", "\\", "~", "%", "?", "#"))
        or identifier in {".", ".."}
        or re.search(r"[A-Za-z]:", identifier)
    ):
        raise CodingContextEnvelopeError("owner_scope must not contain private material")
    return value


def _envelope_core(envelope: CodingContextEnvelope) -> dict[str, Any]:
    return {
        "checkpoint": envelope.checkpoint.value,
        "disposition": envelope.disposition.value,
        "lifecycle_state": envelope.lifecycle_state,
        "planning_item_id": envelope.planning_item_id,
        "planning_revision": envelope.planning_revision,
        "authority_digest": envelope.authority_digest,
        "claim_id": envelope.claim_id,
        "claim_owner": envelope.claim_owner,
        "scope_digest": envelope.scope_digest,
        "input_revision": envelope.input_revision,
        "objective_ref": envelope.objective_ref,
        "objective_digest": envelope.objective_digest,
        "acceptance_criteria_id": envelope.acceptance_criteria_id,
        "owner_scope": envelope.owner_scope,
        "graph_input_digest": envelope.graph_input_digest,
        "acceptance_check_refs": envelope.acceptance_check_refs,
        "tool_capability_refs": envelope.tool_capability_refs,
        "budget_policy_refs": envelope.budget_policy_refs,
        "stop_rule_refs": envelope.stop_rule_refs,
        "trigger_evidence_ref": envelope.trigger_evidence_ref,
        "graph_refs": tuple(item.semantic_dict() for item in envelope.graph_refs),
        "excluded_graph_ref_ids": envelope.excluded_graph_ref_ids,
        "warnings": envelope.warnings,
        "waiting_reasons": envelope.waiting_reasons,
        "blockers": envelope.blockers,
        "exact_read_required": envelope.exact_read_required,
        "parent_envelope_id": envelope.parent_envelope_id,
        "post_acceptance_intents": tuple(
            item.semantic_dict() for item in envelope.post_acceptance_intents
        ),
    }


def _enum(value: Any, enum_type: type[StrEnum], field: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise CodingContextEnvelopeError(f"{field} is invalid") from exc


def _reject_unsafe_payload(value: Any, *, key: str = "") -> None:
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in _RAW_KEYS:
        raise CodingContextEnvelopeError("context envelope contains a raw field")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _reject_unsafe_payload(child, key=str(child_key))
    elif isinstance(value, (tuple, list)):
        for child in value:
            _reject_unsafe_payload(child, key=key)
    elif isinstance(value, str) and (_SECRET_RE.search(value) or _PRIVATE_PATH_RE.search(value)):
        raise CodingContextEnvelopeError("context envelope contains private material")
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise CodingContextEnvelopeError("context envelope must be JSON serializable") from exc


__all__ = [
    "CODING_CONTEXT_ENVELOPE_SCHEMA",
    "CodingContextCheckpoint",
    "CodingContextDisposition",
    "CodingContextEnvelope",
    "CodingContextEnvelopeError",
    "PostAcceptanceIntentRef",
    "build_coding_context_envelope",
]

"""Pure Planning-bound production coding-loop controller.

The controller validates commands and emits content-free intents.  It never
executes a tool, dispatches work, mutates a repository, or closes a gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any

from src.coding_context_envelope import (
    CodingContextCheckpoint,
    CodingContextDisposition,
    CodingContextEnvelope,
)
from src.coding_graph_boundary import CodingRetrievalKind, authority_scope_digest
from src.coding_lifecycle_authority import (
    AuthorizedCodingLifecycleState,
    CodingLifecycleAuthorityError,
    transition_authorized_coding_lifecycle,
)
from src.coding_loop_contracts import (
    MAX_LOOP_INTENTS,
    MAX_LOOP_TURNS,
    MAX_REPAIR_ATTEMPTS,
    CodingGateDecision,
    CodingGateOwner,
    CodingGateSubject,
    CodingLoopCommandKind,
    CodingLoopContractError,
    CodingLoopIntent,
    CodingLoopIntentKind,
    CodingLoopModelCommand,
    create_coding_loop_intent,
    strict_id,
    validate_budget,
)
from src.coding_subagent_capsule import (
    CodingSubagentCapsule,
    CodingSubagentLifecycleDescriptor,
    CodingSubagentRole,
)
from src.runtime_event_envelope import stable_payload_hash


CODING_LOOP_CONTROLLER_SCHEMA = "odysseus.coding_loop_controller.v1"


class CodingLoopControllerError(CodingLoopContractError):
    """Raised when a controller input is unsafe or inconsistent."""


class CodingLoopDisposition(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    REVIEW_READY = "review_ready"


@dataclass(frozen=True, slots=True)
class CodingLoopControllerState:
    state_id: str
    lifecycle: AuthorizedCodingLifecycleState
    parent_envelope: CodingContextEnvelope | None
    capsules: tuple[CodingSubagentCapsule, ...]
    intents: tuple[CodingLoopIntent, ...]
    processed_command_digests: tuple[str, ...]
    processed_command_refs: tuple[str, ...]
    repair_plan_refs: tuple[str, ...]
    user_gate_queue: tuple[CodingGateDecision, ...]
    turn_count: int
    max_turns: int
    max_intents: int
    disposition: CodingLoopDisposition
    waiting_reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    execution_allowed: bool = False
    edit_allowed: bool = False
    write_allowed: bool = False
    dispatch_allowed: bool = False
    gate_close_allowed: bool = False
    live_effect_allowed: bool = False
    side_effects: tuple[str, ...] = ("none",)
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.lifecycle, AuthorizedCodingLifecycleState):
            raise CodingLoopControllerError("lifecycle must be typed")
        if self.parent_envelope is not None and not isinstance(
            self.parent_envelope, CodingContextEnvelope
        ):
            raise CodingLoopControllerError("parent_envelope must be typed")
        if not isinstance(self.capsules, tuple) or not all(
            isinstance(item, CodingSubagentCapsule) for item in self.capsules
        ):
            raise CodingLoopControllerError("capsules must be a typed tuple")
        if not isinstance(self.intents, tuple) or not all(
            isinstance(item, CodingLoopIntent) for item in self.intents
        ):
            raise CodingLoopControllerError("intents must be a typed tuple")
        if len(self.intents) > self.max_intents:
            raise CodingLoopControllerError("intent budget exceeded")
        if len({item.intent_id for item in self.intents}) != len(self.intents):
            raise CodingLoopControllerError("stored intent ids must be unique")
        validate_budget(self.max_turns, "max_turns", MAX_LOOP_TURNS)
        validate_budget(self.max_intents, "max_intents", MAX_LOOP_INTENTS)
        if type(self.turn_count) is not int or not 0 <= self.turn_count <= self.max_turns:
            raise CodingLoopControllerError("turn_count is outside its bounded range")
        if len(self.repair_plan_refs) > MAX_REPAIR_ATTEMPTS:
            raise CodingLoopControllerError("repair budget exceeded")
        if len(set(self.repair_plan_refs)) != len(self.repair_plan_refs):
            raise CodingLoopControllerError("repair plan refs must be unique")
        object.__setattr__(self, "disposition", _disposition(self.disposition))
        if len(set(self.processed_command_digests)) != len(self.processed_command_digests):
            raise CodingLoopControllerError("processed command digests must be unique")
        if len(self.processed_command_refs) != len(self.processed_command_digests):
            raise CodingLoopControllerError("processed command refs and digests must align")
        if len(set(self.processed_command_refs)) != len(self.processed_command_refs):
            raise CodingLoopControllerError("processed command refs must be unique")
        for value in self.processed_command_refs:
            _safe_ref(value, "processed_command_ref")
        for value in self.processed_command_digests:
            _sha256(value, "processed_command_digest")
        for value in self.repair_plan_refs:
            _safe_ref(value, "repair_plan_ref")
        for field_name in ("waiting_reasons", "blockers"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or len(values) > 64:
                raise CodingLoopControllerError(f"{field_name} must be a bounded tuple")
            for value in values:
                _safe_ref(value, field_name)
            if len(set(values)) != len(values):
                raise CodingLoopControllerError(f"{field_name} must be unique")
        _validate_user_gate_queue(self.lifecycle, self.user_gate_queue)
        _validate_capsules(self.parent_envelope, self.capsules)
        _validate_state_intents(self)
        if self.lifecycle.state == "waiting" and self.disposition is not CodingLoopDisposition.WAITING:
            raise CodingLoopControllerError("waiting lifecycle requires waiting disposition")
        if self.lifecycle.state == "blocked" and self.disposition is not CodingLoopDisposition.BLOCKED:
            raise CodingLoopControllerError("blocked lifecycle requires blocked disposition")
        if self.lifecycle.state == "review_ready" and self.disposition is not CodingLoopDisposition.REVIEW_READY:
            raise CodingLoopControllerError("review_ready lifecycle requires review_ready disposition")
        if self.disposition is CodingLoopDisposition.REVIEW_READY and self.lifecycle.state != "review_ready":
            raise CodingLoopControllerError("review_ready disposition requires review_ready lifecycle")
        if self.disposition is CodingLoopDisposition.WAITING and not self.waiting_reasons:
            raise CodingLoopControllerError("waiting disposition requires waiting reasons")
        if self.disposition is CodingLoopDisposition.WAITING:
            user_waiting = tuple(
                reason for reason in self.waiting_reasons
                if reason.startswith("waiting_on_user:")
            )
            if user_waiting and self.waiting_reasons != _pending_user_wait_reasons(
                self.user_gate_queue
            ):
                raise CodingLoopControllerError(
                    "waiting_on_user reasons must exactly match pending declared user gates"
                )
        if self.disposition is CodingLoopDisposition.BLOCKED and not self.blockers:
            raise CodingLoopControllerError("blocked disposition requires blockers")
        if self.disposition in {CodingLoopDisposition.RUNNING, CodingLoopDisposition.REVIEW_READY} and (
            self.waiting_reasons or self.blockers
        ):
            raise CodingLoopControllerError("active disposition cannot retain holds")
        for flag in (
            "execution_allowed", "edit_allowed", "write_allowed", "dispatch_allowed",
            "gate_close_allowed", "live_effect_allowed",
        ):
            if getattr(self, flag) is not False:
                raise CodingLoopControllerError(f"{flag} must remain false")
        if self.side_effects != ("none",) or self.raw_content_visible is not False:
            raise CodingLoopControllerError("controller must remain content-free and pure")
        if self.state_id != stable_payload_hash(self.semantic_dict()):
            raise CodingLoopControllerError("state_id does not match canonical controller facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "lifecycle": _lifecycle_semantic(self.lifecycle),
            "parent_envelope_id": self.parent_envelope.envelope_id if self.parent_envelope else "",
            "capsule_ids": tuple(item.capsule_id for item in self.capsules),
            "intent_ids": tuple(item.intent_id for item in self.intents),
            "processed_command_digests": self.processed_command_digests,
            "processed_command_refs": self.processed_command_refs,
            "repair_plan_refs": self.repair_plan_refs,
            "user_gate_queue": tuple(item.to_dict() for item in self.user_gate_queue),
            "turn_count": self.turn_count,
            "max_turns": self.max_turns,
            "max_intents": self.max_intents,
            "disposition": self.disposition.value,
            "waiting_reasons": self.waiting_reasons,
            "blockers": self.blockers,
            "execution_allowed": False,
            "edit_allowed": False,
            "write_allowed": False,
            "dispatch_allowed": False,
            "gate_close_allowed": False,
            "live_effect_allowed": False,
            "side_effects": ("none",),
            "raw_content_visible": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CODING_LOOP_CONTROLLER_SCHEMA,
            "state_id": self.state_id,
            **self.semantic_dict(),
            "intents": tuple(item.to_dict() for item in self.intents),
        }


def start_coding_loop_controller(
    *,
    lifecycle: AuthorizedCodingLifecycleState,
    parent_envelope: CodingContextEnvelope | None = None,
    capsules: tuple[CodingSubagentCapsule, ...] = (),
    user_gate_queue: tuple[CodingGateDecision, ...] = (),
    max_turns: int = MAX_LOOP_TURNS,
    max_intents: int = MAX_LOOP_INTENTS,
) -> CodingLoopControllerState:
    if not isinstance(lifecycle, AuthorizedCodingLifecycleState):
        raise CodingLoopControllerError("lifecycle must be typed")
    validate_budget(max_turns, "max_turns", MAX_LOOP_TURNS)
    validate_budget(max_intents, "max_intents", MAX_LOOP_INTENTS)
    if parent_envelope is not None:
        _validate_parent_binding(lifecycle, parent_envelope)
    _validate_capsules(parent_envelope, capsules)
    _validate_user_gate_queue(lifecycle, user_gate_queue)
    disposition = _lifecycle_disposition(lifecycle)
    return _state(
        lifecycle=lifecycle,
        parent_envelope=parent_envelope,
        capsules=capsules,
        intents=(),
        processed_command_digests=(),
        processed_command_refs=(),
        repair_plan_refs=(),
        user_gate_queue=user_gate_queue,
        turn_count=0,
        max_turns=max_turns,
        max_intents=max_intents,
        disposition=disposition,
        waiting_reasons=lifecycle.waiting_reasons,
        blockers=lifecycle.blockers,
    )


def apply_coding_loop_command(
    current: CodingLoopControllerState,
    *,
    command: CodingLoopModelCommand,
    gate: CodingGateDecision | None = None,
) -> CodingLoopControllerState:
    if not isinstance(current, CodingLoopControllerState):
        raise CodingLoopControllerError("current controller state must be typed")
    if not isinstance(command, CodingLoopModelCommand):
        raise CodingLoopControllerError("command must be typed")
    if gate is not None and not isinstance(gate, CodingGateDecision):
        raise CodingLoopControllerError("gate must be typed")
    if gate is not None:
        _validate_gate_binding(current.lifecycle, gate)
    if current.disposition in {CodingLoopDisposition.WAITING, CodingLoopDisposition.BLOCKED}:
        raise CodingLoopControllerError("held controller must be resumed by Planning authority")
    if current.disposition is CodingLoopDisposition.REVIEW_READY:
        raise CodingLoopControllerError("review_ready is terminal for CAO-08C")
    if command.command_ref in current.processed_command_refs:
        index = current.processed_command_refs.index(command.command_ref)
        if current.processed_command_digests[index] == command.digest:
            return current
        raise CodingLoopControllerError("command_ref idempotency conflict")
    if current.turn_count >= current.max_turns:
        return _hold(current, waiting=("turn_budget_exhausted",))
    if gate is not None and not gate.accepted:
        reason = f"gate_waiting:{gate.owner.value}:{gate.subject.value}"
        if gate.owner is CodingGateOwner.USER_ACCEPTANCE:
            return _hold(current, waiting=(reason,))
        return _hold(current, blockers=(reason,))

    if command.command_kind is CodingLoopCommandKind.ADVANCE:
        if command.target_state in {"review_ready", "memory_review", "publish_ready", "done"}:
            raise CodingLoopControllerError("advance command cannot bypass independent review")
        if command.target_state in {
            "context_building", "context_ready", "worktree_ready", "acting", "verifying"
        }:
            _require_ready_context(current)
        if command.target_state == "acting":
            _capsule_for_role(current, CodingSubagentRole.IMPLEMENTER)
        lifecycle = _transition(current.lifecycle, command.target_state)
        return _advance_state(current, command, lifecycle=lifecycle)

    if command.command_kind is CodingLoopCommandKind.MUTATION_INTENT:
        if command.intent_kind is not CodingLoopIntentKind.PROPOSE_SCOPED_PATCH:
            raise CodingLoopControllerError("mutation intent kind is not allowed")
        _require_gate(gate, CodingGateOwner.AGENT_AUTO, CodingGateSubject.ROUTINE_IMPLEMENTATION)
        _require_state(current, "acting")
        capsule = _capsule_for_role(current, CodingSubagentRole.IMPLEMENTER)
        intent = _intent_from_command(current, command, capsule)
        return _advance_state(current, command, append_intent=intent)

    if command.command_kind is CodingLoopCommandKind.CHECK_INTENT:
        if command.intent_kind is not CodingLoopIntentKind.REQUEST_BOUNDED_CHECK:
            raise CodingLoopControllerError("check intent kind is not allowed")
        _require_gate(gate, CodingGateOwner.AGENT_AUTO, CodingGateSubject.BOUNDED_VERIFICATION)
        _require_state(current, "acting")
        capsule = _capsule_for_role(current, CodingSubagentRole.TESTER)
        intent = _intent_from_command(current, command, capsule)
        lifecycle = _transition(current.lifecycle, "verifying")
        return _advance_state(current, command, lifecycle=lifecycle, append_intent=intent)

    if command.command_kind is CodingLoopCommandKind.REVIEW:
        unresolved = tuple(item for item in current.user_gate_queue if not item.accepted)
        if unresolved:
            return _hold(
                current,
                waiting=tuple(f"waiting_on_user:{item.subject.value}" for item in unresolved),
            )
        _require_gate(gate, CodingGateOwner.AGENT_AUTO, CodingGateSubject.INDEPENDENT_REVIEW)
        _require_state(current, "verifying")
        reviewer = _capsule_for_role(current, CodingSubagentRole.REVIEWER)
        if command.role != reviewer.role.value:
            raise CodingLoopControllerError("model command role does not match reviewer capsule")
        if reviewer.actor_id in {reviewer.implementer_actor_id, reviewer.claim_owner}:
            raise CodingLoopControllerError("reviewer is not independent")
        if command.evidence_ref != current.lifecycle.authority.evidence_id:
            raise CodingLoopControllerError("review evidence does not match Planning authority")
        if gate.decision_ref != current.lifecycle.authority.acceptance_decision_id:
            raise CodingLoopControllerError("review decision does not match Planning acceptance")
        lifecycle = _transition(current.lifecycle, "review_ready")
        return _advance_state(
            current, command, lifecycle=lifecycle, disposition=CodingLoopDisposition.REVIEW_READY
        )

    if command.command_kind is CodingLoopCommandKind.REPAIR:
        _require_gate(gate, CodingGateOwner.AGENT_AUTO, CodingGateSubject.ROUTINE_IMPLEMENTATION)
        _require_state(current, "verifying")
        if command.repair_plan_ref in current.repair_plan_refs:
            return current
        if len(current.repair_plan_refs) >= MAX_REPAIR_ATTEMPTS:
            return _hold(current, waiting=("repair_escalation_required",))
        lifecycle = _transition(current.lifecycle, "repair_planning")
        return _advance_state(
            current,
            command,
            lifecycle=lifecycle,
            repair_plan_refs=(*current.repair_plan_refs, command.repair_plan_ref),
        )
    raise CodingLoopControllerError("unsupported command kind")


def accept_coding_loop_user_gate(
    current: CodingLoopControllerState,
    *,
    accepted_decision: CodingGateDecision,
) -> CodingLoopControllerState:
    """Resume a Planning-declared user gate without losing controller evidence."""

    if not isinstance(current, CodingLoopControllerState):
        raise CodingLoopControllerError("current controller state must be typed")
    if not isinstance(accepted_decision, CodingGateDecision):
        raise CodingLoopControllerError("accepted_decision must be typed")
    if current.disposition is not CodingLoopDisposition.WAITING or current.blockers:
        raise CodingLoopControllerError("only a waiting user gate can resume")
    expected_waiting = _pending_user_wait_reasons(current.user_gate_queue)
    if not expected_waiting or current.waiting_reasons != expected_waiting:
        raise CodingLoopControllerError(
            "resume requires exactly the unresolved declared user gate waits"
        )
    _validate_gate_binding(current.lifecycle, accepted_decision)
    if (
        accepted_decision.owner is not CodingGateOwner.USER_ACCEPTANCE
        or not accepted_decision.accepted
    ):
        raise CodingLoopControllerError("resume requires an accepted user Planning decision")
    matching = tuple(
        item
        for item in current.user_gate_queue
        if item.subject is accepted_decision.subject
    )
    if (
        len(matching) != 1
        or matching[0].accepted
        or matching[0].decision_ref != accepted_decision.decision_ref
    ):
        raise CodingLoopControllerError("accepted user decision does not match declared gate")
    queue = tuple(
        accepted_decision if item.subject is accepted_decision.subject else item
        for item in current.user_gate_queue
    )
    unresolved = tuple(item for item in queue if not item.accepted)
    return _state(
        lifecycle=current.lifecycle,
        parent_envelope=current.parent_envelope,
        capsules=current.capsules,
        intents=current.intents,
        processed_command_digests=current.processed_command_digests,
        processed_command_refs=current.processed_command_refs,
        repair_plan_refs=current.repair_plan_refs,
        user_gate_queue=queue,
        turn_count=current.turn_count,
        max_turns=current.max_turns,
        max_intents=current.max_intents,
        disposition=(
            CodingLoopDisposition.WAITING if unresolved else _lifecycle_disposition(current.lifecycle)
        ),
        waiting_reasons=tuple(
            f"waiting_on_user:{item.subject.value}" for item in unresolved
        ),
        blockers=current.blockers,
    )


def _intent_from_command(
    current: CodingLoopControllerState,
    command: CodingLoopModelCommand,
    capsule: CodingSubagentCapsule,
) -> CodingLoopIntent:
    if command.role != capsule.role.value:
        raise CodingLoopControllerError("model command role does not match role capsule")
    if command.target_graph_ref not in capsule.graph_ref_ids:
        raise CodingLoopControllerError("unscoped graph ref is denied")
    if not command.exact_read_required_ref:
        raise CodingLoopControllerError("mutation/check intent requires exact-read binding")
    if command.exact_read_required_ref not in capsule.exact_read_refs:
        raise CodingLoopControllerError("exact-read requirement is outside role capsule")
    if command.exact_read_required_ref != command.target_graph_ref:
        raise CodingLoopControllerError("semantic graph ref does not prove a different exact read")
    if len(current.intents) >= current.max_intents:
        raise CodingLoopControllerError("intent budget exhausted")
    envelope = _require_ready_context(current)
    graph_ref = next(
        (item for item in envelope.graph_refs if item.ref_id == command.target_graph_ref),
        None,
    )
    if graph_ref is None or graph_ref.retrieval_kind is not CodingRetrievalKind.EXACT_CODE:
        raise CodingLoopControllerError("exact-read binding requires exact_code retrieval")
    return create_coding_loop_intent(
        intent_kind=command.intent_kind,
        command_ref=command.command_ref,
        planning_item_id=envelope.planning_item_id,
        planning_revision=envelope.planning_revision,
        claim_id=envelope.claim_id,
        claim_owner=envelope.claim_owner,
        scope_digest=envelope.scope_digest,
        input_revision=envelope.input_revision,
        parent_envelope_id=envelope.envelope_id,
        capsule_id=capsule.capsule_id,
        role=capsule.role.value,
        target_graph_ref=command.target_graph_ref,
        exact_read_required_ref=command.exact_read_required_ref,
        payload_digest=command.payload_digest,
    )


def _validate_parent_binding(
    lifecycle: AuthorizedCodingLifecycleState, envelope: CodingContextEnvelope
) -> None:
    if not isinstance(envelope, CodingContextEnvelope):
        raise CodingLoopControllerError("parent_envelope must be typed")
    if (
        envelope.checkpoint is not CodingContextCheckpoint.PRE_SLICE
        or envelope.disposition is not CodingContextDisposition.READY
        or envelope.waiting_reasons
        or envelope.blockers
    ):
        raise CodingLoopControllerError("controller requires a READY PRE_SLICE envelope")
    authority = lifecycle.authority
    mismatches = (
        envelope.planning_item_id != authority.planning_item_id,
        envelope.planning_revision != authority.planning_revision,
        envelope.claim_id != authority.claim_id,
        envelope.claim_owner != authority.claim_owner,
        envelope.input_revision != authority.input_revision,
        envelope.scope_digest != authority_scope_digest(authority),
        envelope.authority_digest != stable_payload_hash(authority.to_dict()),
    )
    if any(mismatches):
        raise CodingLoopControllerError("parent envelope authority mismatch")


def _validate_capsules(
    envelope: CodingContextEnvelope | None, capsules: tuple[CodingSubagentCapsule, ...]
) -> None:
    if not isinstance(capsules, tuple) or not all(
        isinstance(item, CodingSubagentCapsule) for item in capsules
    ):
        raise CodingLoopControllerError("capsules must be a typed tuple")
    if len({item.role for item in capsules}) != len(capsules):
        raise CodingLoopControllerError("capsule roles must be unique")
    role_order = {
        CodingSubagentRole.IMPLEMENTER: 0,
        CodingSubagentRole.TESTER: 1,
        CodingSubagentRole.REVIEWER: 2,
    }
    if capsules != tuple(sorted(capsules, key=lambda item: role_order[item.role])):
        raise CodingLoopControllerError("capsule roles must be canonical")
    if capsules and envelope is None:
        raise CodingLoopControllerError("capsules require a parent envelope")
    for capsule in capsules:
        if capsule.lifecycle_descriptor is not CodingSubagentLifecycleDescriptor.CAPSULE_READY:
            raise CodingLoopControllerError("cancelled or expired capsule is rejected")
        if (
            capsule.parent_envelope_id != envelope.envelope_id
            or capsule.planning_item_id != envelope.planning_item_id
            or capsule.planning_revision != envelope.planning_revision
            or capsule.claim_id != envelope.claim_id
            or capsule.claim_owner != envelope.claim_owner
            or capsule.scope_digest != envelope.scope_digest
            or capsule.input_revision != envelope.input_revision
        ):
            raise CodingLoopControllerError("capsule parent binding mismatch")


def _validate_gate_binding(
    lifecycle: AuthorizedCodingLifecycleState, gate: CodingGateDecision
) -> None:
    authority = lifecycle.authority
    if (
        gate.source != "planning"
        or gate.planning_item_id != authority.planning_item_id
        or gate.planning_revision != authority.planning_revision
        or gate.claim_id != authority.claim_id
        or gate.input_revision != authority.input_revision
    ):
        raise CodingLoopControllerError("gate decision Planning binding mismatch")


def _validate_user_gate_queue(
    lifecycle: AuthorizedCodingLifecycleState,
    queue: tuple[CodingGateDecision, ...],
) -> None:
    if not isinstance(queue, tuple) or len(queue) > 16 or not all(
        isinstance(item, CodingGateDecision) for item in queue
    ):
        raise CodingLoopControllerError("user gate queue must be a bounded typed tuple")
    if len({item.subject for item in queue}) != len(queue):
        raise CodingLoopControllerError("user gate subjects must be unique")
    if queue != tuple(sorted(queue, key=lambda item: item.subject.value)):
        raise CodingLoopControllerError("user gate queue must be canonical")
    for item in queue:
        if item.owner is not CodingGateOwner.USER_ACCEPTANCE:
            raise CodingLoopControllerError("user gate queue accepts only user_acceptance subjects")
        _validate_gate_binding(lifecycle, item)


def _validate_state_intents(state: CodingLoopControllerState) -> None:
    if state.intents and state.parent_envelope is None:
        raise CodingLoopControllerError("stored intents require a parent envelope")
    if state.parent_envelope is None:
        return
    envelope = state.parent_envelope
    authority = state.lifecycle.authority
    capsules = {item.capsule_id: item for item in state.capsules}
    command_refs = tuple(intent.command_ref for intent in state.intents)
    if len(set(command_refs)) != len(command_refs):
        raise CodingLoopControllerError("stored intent command refs must be unique")
    for intent in state.intents:
        capsule = capsules.get(intent.capsule_id)
        if capsule is None or intent.role != capsule.role.value:
            raise CodingLoopControllerError("stored intent has a foreign capsule binding")
        if (
            intent.parent_envelope_id != envelope.envelope_id
            or intent.planning_item_id != authority.planning_item_id
            or intent.planning_revision != authority.planning_revision
            or intent.claim_id != authority.claim_id
            or intent.claim_owner != authority.claim_owner
            or intent.scope_digest != authority_scope_digest(authority)
            or intent.input_revision != authority.input_revision
            or intent.target_graph_ref not in capsule.graph_ref_ids
            or intent.exact_read_required_ref not in capsule.exact_read_refs
            or intent.exact_read_required_ref != intent.target_graph_ref
        ):
            raise CodingLoopControllerError("stored intent authority binding mismatch")
        if state.processed_command_refs.count(intent.command_ref) != 1:
            raise CodingLoopControllerError(
                "stored intent command ref must occur exactly once in processed commands"
            )
        graph_ref = next(
            (item for item in envelope.graph_refs if item.ref_id == intent.target_graph_ref),
            None,
        )
        if graph_ref is None or graph_ref.retrieval_kind is not CodingRetrievalKind.EXACT_CODE:
            raise CodingLoopControllerError("stored intent requires exact_code retrieval")
        expected_kind = {
            CodingSubagentRole.IMPLEMENTER: CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
            CodingSubagentRole.TESTER: CodingLoopIntentKind.REQUEST_BOUNDED_CHECK,
        }.get(capsule.role)
        if expected_kind is None or intent.intent_kind is not expected_kind:
            raise CodingLoopControllerError("stored intent kind does not match role")


def _require_ready_context(current: CodingLoopControllerState) -> CodingContextEnvelope:
    if current.parent_envelope is None:
        raise CodingLoopControllerError("pre-slice envelope is required")
    _validate_parent_binding(current.lifecycle, current.parent_envelope)
    return current.parent_envelope


def _pending_user_wait_reasons(
    queue: tuple[CodingGateDecision, ...],
) -> tuple[str, ...]:
    return tuple(
        f"waiting_on_user:{item.subject.value}" for item in queue if not item.accepted
    )


def _capsule_for_role(
    current: CodingLoopControllerState, role: CodingSubagentRole
) -> CodingSubagentCapsule:
    for capsule in current.capsules:
        if capsule.role is role:
            return capsule
    raise CodingLoopControllerError(f"{role.value} capsule is required")


def _require_gate(
    gate: CodingGateDecision | None,
    owner: CodingGateOwner,
    subject: CodingGateSubject,
) -> None:
    if gate is None or gate.owner is not owner or gate.subject is not subject or not gate.accepted:
        raise CodingLoopControllerError("required gate decision is missing or mismatched")


def _require_state(current: CodingLoopControllerState, state: str) -> None:
    if current.lifecycle.state != state:
        raise CodingLoopControllerError(f"command requires lifecycle state {state}")


def _transition(
    lifecycle: AuthorizedCodingLifecycleState, target: str
) -> AuthorizedCodingLifecycleState:
    try:
        return transition_authorized_coding_lifecycle(lifecycle, target_state=target)
    except CodingLifecycleAuthorityError as exc:
        raise CodingLoopControllerError(str(exc)) from exc


def _advance_state(
    current: CodingLoopControllerState,
    command: CodingLoopModelCommand,
    *,
    lifecycle: AuthorizedCodingLifecycleState | None = None,
    append_intent: CodingLoopIntent | None = None,
    repair_plan_refs: tuple[str, ...] | None = None,
    disposition: CodingLoopDisposition | None = None,
) -> CodingLoopControllerState:
    intents = current.intents + ((append_intent,) if append_intent is not None else ())
    new_lifecycle = lifecycle or current.lifecycle
    return _state(
        lifecycle=new_lifecycle,
        parent_envelope=current.parent_envelope,
        capsules=current.capsules,
        intents=intents,
        processed_command_digests=(*current.processed_command_digests, command.digest),
        processed_command_refs=(*current.processed_command_refs, command.command_ref),
        repair_plan_refs=repair_plan_refs if repair_plan_refs is not None else current.repair_plan_refs,
        user_gate_queue=current.user_gate_queue,
        turn_count=current.turn_count + 1,
        max_turns=current.max_turns,
        max_intents=current.max_intents,
        disposition=disposition or _lifecycle_disposition(new_lifecycle),
        waiting_reasons=new_lifecycle.waiting_reasons,
        blockers=new_lifecycle.blockers,
    )


def _hold(
    current: CodingLoopControllerState,
    *,
    waiting: tuple[str, ...] = (),
    blockers: tuple[str, ...] = (),
) -> CodingLoopControllerState:
    return _state(
        lifecycle=current.lifecycle,
        parent_envelope=current.parent_envelope,
        capsules=current.capsules,
        intents=current.intents,
        processed_command_digests=current.processed_command_digests,
        processed_command_refs=current.processed_command_refs,
        repair_plan_refs=current.repair_plan_refs,
        user_gate_queue=current.user_gate_queue,
        turn_count=current.turn_count,
        max_turns=current.max_turns,
        max_intents=current.max_intents,
        disposition=CodingLoopDisposition.BLOCKED if blockers else CodingLoopDisposition.WAITING,
        waiting_reasons=waiting,
        blockers=blockers,
    )


def _state(**facts: Any) -> CodingLoopControllerState:
    disposition = _disposition(facts["disposition"])
    semantic = {
        "lifecycle": _lifecycle_semantic(facts["lifecycle"]),
        "parent_envelope_id": facts["parent_envelope"].envelope_id if facts["parent_envelope"] else "",
        "capsule_ids": tuple(item.capsule_id for item in facts["capsules"]),
        "intent_ids": tuple(item.intent_id for item in facts["intents"]),
        "processed_command_digests": facts["processed_command_digests"],
        "processed_command_refs": facts["processed_command_refs"],
        "repair_plan_refs": facts["repair_plan_refs"],
        "user_gate_queue": tuple(item.to_dict() for item in facts["user_gate_queue"]),
        "turn_count": facts["turn_count"],
        "max_turns": facts["max_turns"],
        "max_intents": facts["max_intents"],
        "disposition": disposition.value,
        "waiting_reasons": facts.get("waiting_reasons", ()),
        "blockers": facts.get("blockers", ()),
        "execution_allowed": False,
        "edit_allowed": False,
        "write_allowed": False,
        "dispatch_allowed": False,
        "gate_close_allowed": False,
        "live_effect_allowed": False,
        "side_effects": ("none",),
        "raw_content_visible": False,
    }
    return CodingLoopControllerState(state_id=stable_payload_hash(semantic), **facts)


def _lifecycle_semantic(lifecycle: AuthorizedCodingLifecycleState) -> dict[str, Any]:
    """Return stable lifecycle facts without the timestamped runtime event."""

    return {
        "coding_task_id": lifecycle.coding_task_id,
        "repo_id": lifecycle.repo_id,
        "state": lifecycle.state,
        "last_accepted_state": lifecycle.last_accepted_state,
        "authority": lifecycle.authority.to_dict(),
        "transition_ordinal": lifecycle.transition_ordinal,
        "resume_condition": lifecycle.resume_condition,
        "resume_checkpoint": lifecycle.resume_checkpoint,
        "waiting_reasons": lifecycle.waiting_reasons,
        "blockers": lifecycle.blockers,
        "legacy_schema": lifecycle.legacy_schema,
        "legacy_payload_digest": lifecycle.legacy_payload_digest,
    }


def _lifecycle_disposition(lifecycle: AuthorizedCodingLifecycleState) -> CodingLoopDisposition:
    if lifecycle.state == "waiting":
        return CodingLoopDisposition.WAITING
    if lifecycle.state == "blocked":
        return CodingLoopDisposition.BLOCKED
    if lifecycle.state == "review_ready":
        return CodingLoopDisposition.REVIEW_READY
    return CodingLoopDisposition.RUNNING


def _disposition(value: Any) -> CodingLoopDisposition:
    if isinstance(value, CodingLoopDisposition):
        return value
    try:
        return CodingLoopDisposition(value)
    except (TypeError, ValueError) as exc:
        raise CodingLoopControllerError("disposition is invalid") from exc


def _safe_ref(value: Any, field: str) -> str:
    try:
        return strict_id(value, field)
    except CodingLoopContractError as exc:
        raise CodingLoopControllerError(str(exc)) from exc


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise CodingLoopControllerError(f"{field} must be canonical SHA-256")
    return value


__all__ = [
    "CODING_LOOP_CONTROLLER_SCHEMA", "CodingLoopControllerError",
    "CodingLoopControllerState", "CodingLoopDisposition",
    "accept_coding_loop_user_gate", "apply_coding_loop_command",
    "start_coding_loop_controller",
]

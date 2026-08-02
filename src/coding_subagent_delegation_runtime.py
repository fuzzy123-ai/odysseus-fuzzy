"""Deterministic in-memory reducer for fake-only bounded delegation."""

from __future__ import annotations

from dataclasses import dataclass, replace

from src.coding_subagent_delegation_contracts import (
    MAX_DELEGATION_HISTORY,
    MAX_DEPTH,
    MAX_FANOUT,
    CancellationOrigin,
    DelegationAttempt,
    DelegationBinding,
    DelegationError,
    DelegationEvent,
    DelegationRole,
    DelegationStatus,
    ParentDecision,
    attempt_id,
    child_id,
    delegation_event_id,
    validate_identifier,
    validate_sha256,
)


_ACTIVE_STATUSES = {DelegationStatus.PLANNED, DelegationStatus.COMPLETED}
_RESUMABLE_STATUSES = {
    DelegationStatus.REJECTED,
    DelegationStatus.CANCELLED,
    DelegationStatus.EXPIRED,
}


@dataclass(frozen=True, slots=True)
class DelegationSession:
    parent_controller_state_id: str
    parent_actor_id: str
    attempts: tuple[DelegationAttempt, ...] = ()
    events: tuple[DelegationEvent, ...] = ()
    parent_decisions: tuple[ParentDecision, ...] = ()

    def __post_init__(self) -> None:
        validate_sha256(
            self.parent_controller_state_id,
            "parent_controller_state_id",
        )
        validate_identifier(self.parent_actor_id, "parent_actor_id")
        if not isinstance(self.attempts, tuple) or not all(
            isinstance(item, DelegationAttempt) for item in self.attempts
        ):
            raise DelegationError("attempts must be a typed tuple")
        if not isinstance(self.events, tuple) or not all(
            isinstance(item, DelegationEvent) for item in self.events
        ):
            raise DelegationError("events must be a typed tuple")
        if not isinstance(self.parent_decisions, tuple) or not all(
            isinstance(item, ParentDecision) for item in self.parent_decisions
        ):
            raise DelegationError("parent_decisions must be a typed tuple")
        if (
            len(self.attempts) > MAX_DELEGATION_HISTORY
            or len(self.events) > MAX_DELEGATION_HISTORY
            or len(self.parent_decisions) > MAX_DELEGATION_HISTORY
        ):
            raise DelegationError("delegation history is outside its bound")
        if len({item.child_id for item in self.attempts}) != len(self.attempts):
            raise DelegationError("current child identities must be unique")
        if len({item.binding.role for item in self.attempts}) != len(self.attempts):
            raise DelegationError("each declared child role may occur only once")
        if len({item.event_id for item in self.events}) != len(self.events):
            raise DelegationError("event identities must be unique")
        if len({item.decision_id for item in self.parent_decisions}) != len(
            self.parent_decisions
        ):
            raise DelegationError("parent decision identities must be unique")
        if sum(item.status in _ACTIVE_STATUSES for item in self.attempts) > MAX_FANOUT:
            raise DelegationError("concurrent child fanout exceeds two")
        for item in self.attempts:
            if item.binding.controller_state_id != self.parent_controller_state_id:
                raise DelegationError("attempt is bound to a foreign controller")
            if item.binding.actor_id == self.parent_actor_id:
                raise DelegationError("parent and child actors must be distinct")
        child_ids = {item.child_id for item in self.attempts}
        if any(item.child_id not in child_ids for item in self.events):
            raise DelegationError("event is bound to an unknown child")
        for item in self.parent_decisions:
            if (
                item.controller_state_id != self.parent_controller_state_id
                or item.parent_actor_id != self.parent_actor_id
                or item.child_id not in child_ids
            ):
                raise DelegationError("parent decision is bound to foreign authority")


def _state_with_attempt(
    session: DelegationSession,
    updated: DelegationAttempt,
) -> tuple[DelegationAttempt, ...]:
    return tuple(
        updated if item.child_id == updated.child_id else item
        for item in session.attempts
    )


def start_fake_attempt(
    session: DelegationSession,
    *,
    binding: DelegationBinding,
    child_slot: int,
    lease_until: int,
    idempotency_key: str,
) -> DelegationSession:
    if not isinstance(session, DelegationSession):
        raise DelegationError("session must be typed")
    if not isinstance(binding, DelegationBinding):
        raise DelegationError("binding must be typed")
    if binding.controller_state_id != session.parent_controller_state_id:
        raise DelegationError("binding is owned by a foreign parent controller")
    if binding.actor_id == session.parent_actor_id:
        raise DelegationError("child cannot also be the accepting parent")
    child = child_id(binding, child_slot)
    existing = next(
        (item for item in session.attempts if item.child_id == child),
        None,
    )
    if existing is not None:
        if (
            existing.idempotency_key == idempotency_key
            and existing.binding.digest == binding.digest
            and existing.lease_until == lease_until
        ):
            return session
        raise DelegationError("child idempotency collision")
    if any(item.binding.role is binding.role for item in session.attempts):
        raise DelegationError("declared child role already exists")
    if sum(item.status in _ACTIVE_STATUSES for item in session.attempts) >= MAX_FANOUT:
        raise DelegationError("concurrent child fanout exceeds two")
    if binding.role is DelegationRole.REVIEWER and not any(
        item.status
        in {
            DelegationStatus.ACCEPTED,
            DelegationStatus.REJECTED,
            DelegationStatus.CANCELLED,
            DelegationStatus.EXPIRED,
        }
        for item in session.attempts
    ):
        raise DelegationError("reviewer requires a terminal predecessor")
    attempt = DelegationAttempt(
        child_id=child,
        attempt_id=attempt_id(child, 1, binding.digest),
        binding=binding,
        child_slot=child_slot,
        depth=MAX_DEPTH,
        fence=1,
        lease_until=lease_until,
        idempotency_key=idempotency_key,
    )
    return DelegationSession(
        parent_controller_state_id=session.parent_controller_state_id,
        parent_actor_id=session.parent_actor_id,
        attempts=(*session.attempts, attempt),
        events=session.events,
        parent_decisions=session.parent_decisions,
    )


def record_child_event(
    session: DelegationSession,
    *,
    child: str,
    fence: int,
    actor_id: str,
    status: DelegationStatus,
    event_ref: str,
    now: int,
    handoff_refs: tuple[str, ...] = (),
    cancellation_origin: CancellationOrigin | None = None,
    cancellation_ref: str = "",
) -> DelegationSession:
    if not isinstance(session, DelegationSession):
        raise DelegationError("session must be typed")
    if type(now) is not int or now < 0:
        raise DelegationError("now must be a non-negative integer")
    attempt = next(
        (item for item in session.attempts if item.child_id == child),
        None,
    )
    if attempt is None or fence != attempt.fence:
        raise DelegationError("child fence is unknown, stale, or future")

    requested = DelegationStatus(status)
    if requested not in {
        DelegationStatus.COMPLETED,
        DelegationStatus.CANCELLED,
        DelegationStatus.EXPIRED,
    }:
        raise DelegationError("child cannot record a parent decision status")
    if requested is DelegationStatus.EXPIRED and now < attempt.lease_until:
        raise DelegationError("lease has not expired")
    effective = (
        DelegationStatus.EXPIRED
        if now >= attempt.lease_until
        else requested
    )
    effective_handoffs = handoff_refs if effective is DelegationStatus.COMPLETED else ()
    effective_origin = (
        cancellation_origin if effective is DelegationStatus.CANCELLED else None
    )
    effective_cancel_ref = (
        cancellation_ref if effective is DelegationStatus.CANCELLED else ""
    )

    if effective is DelegationStatus.COMPLETED:
        if actor_id != attempt.binding.actor_id:
            raise DelegationError("only the bound child may complete its attempt")
    elif effective is DelegationStatus.CANCELLED:
        origin = CancellationOrigin(cancellation_origin)
        expected_actor = (
            session.parent_actor_id
            if origin is CancellationOrigin.PARENT
            else attempt.binding.actor_id
        )
        if actor_id != expected_actor:
            raise DelegationError("cancellation actor does not match its origin")
    elif actor_id not in {session.parent_actor_id, attempt.binding.actor_id}:
        raise DelegationError("expiry actor is outside the parent-child boundary")

    event = DelegationEvent(
        event_id=delegation_event_id(
            child=attempt.child_id,
            attempt=attempt.attempt_id,
            fence=attempt.fence,
            actor_id=actor_id,
            status=effective,
            event_ref=event_ref,
            handoff_refs=effective_handoffs,
            cancellation_origin=effective_origin,
            cancellation_ref=effective_cancel_ref,
        ),
        child_id=attempt.child_id,
        attempt_id=attempt.attempt_id,
        fence=attempt.fence,
        actor_id=actor_id,
        status=effective,
        event_ref=event_ref,
        handoff_refs=effective_handoffs,
        cancellation_origin=effective_origin,
        cancellation_ref=effective_cancel_ref,
    )
    replay = next(
        (
            item
            for item in session.events
            if item.attempt_id == attempt.attempt_id
            and item.event_ref == event_ref
        ),
        None,
    )
    if replay is not None:
        if replay == event:
            return session
        raise DelegationError("event idempotency collision")
    if attempt.status is not DelegationStatus.PLANNED:
        raise DelegationError("attempt already has a terminal child receipt")

    updated = replace(attempt, status=effective)
    return DelegationSession(
        parent_controller_state_id=session.parent_controller_state_id,
        parent_actor_id=session.parent_actor_id,
        attempts=_state_with_attempt(session, updated),
        events=(*session.events, event),
        parent_decisions=session.parent_decisions,
    )


def record_parent_decision(
    session: DelegationSession,
    decision: ParentDecision,
) -> DelegationSession:
    if not isinstance(session, DelegationSession):
        raise DelegationError("session must be typed")
    if not isinstance(decision, ParentDecision):
        raise DelegationError("parent decision must be typed")
    if (
        decision.controller_state_id != session.parent_controller_state_id
        or decision.parent_actor_id != session.parent_actor_id
    ):
        raise DelegationError("only the bound parent may decide")
    replay = next(
        (
            item
            for item in session.parent_decisions
            if item.decision_ref == decision.decision_ref
        ),
        None,
    )
    if replay is not None:
        if replay == decision:
            return session
        raise DelegationError("parent decision idempotency collision")
    attempt = next(
        (item for item in session.attempts if item.child_id == decision.child_id),
        None,
    )
    if (
        attempt is None
        or attempt.attempt_id != decision.attempt_id
        or attempt.fence != decision.fence
        or attempt.status is not DelegationStatus.COMPLETED
    ):
        raise DelegationError("parent decision requires current child completion")
    completion = next(
        (
            item
            for item in session.events
            if item.event_id == decision.completion_event_id
        ),
        None,
    )
    if (
        completion is None
        or completion.child_id != attempt.child_id
        or completion.attempt_id != attempt.attempt_id
        or completion.fence != attempt.fence
        or completion.actor_id != attempt.binding.actor_id
        or completion.status is not DelegationStatus.COMPLETED
    ):
        raise DelegationError("parent decision lacks bound completion evidence")
    updated = replace(attempt, status=decision.outcome)
    return DelegationSession(
        parent_controller_state_id=session.parent_controller_state_id,
        parent_actor_id=session.parent_actor_id,
        attempts=_state_with_attempt(session, updated),
        events=session.events,
        parent_decisions=(*session.parent_decisions, decision),
    )


def resume_fresh_attempt(
    session: DelegationSession,
    *,
    child: str,
    binding: DelegationBinding,
    lease_until: int,
    idempotency_key: str,
    current_exact_read_refs: tuple[str, ...],
    current_tool_capability_refs: tuple[str, ...],
    current_budget_policy_refs: tuple[str, ...],
    current_stop_rule_refs: tuple[str, ...],
    current_budget_facts: tuple[int, int, int, int],
) -> DelegationSession:
    if not isinstance(session, DelegationSession):
        raise DelegationError("session must be typed")
    if not isinstance(binding, DelegationBinding):
        raise DelegationError("resume binding must be typed")
    prior = next(
        (item for item in session.attempts if item.child_id == child),
        None,
    )
    if prior is None or prior.status not in _RESUMABLE_STATUSES:
        raise DelegationError("only rejected, cancelled, or expired child resumes")
    if (
        prior.idempotency_key == idempotency_key
        or binding.capsule_id == prior.binding.capsule_id
        or binding.digest == prior.binding.digest
    ):
        raise DelegationError("resume requires fresh capsule, binding, and key")
    if child_id(binding, prior.child_slot) != prior.child_id:
        raise DelegationError("resume changed parent, authority, input, role, or slot")
    if binding.controller_state_id != session.parent_controller_state_id:
        raise DelegationError("resume binding is owned by a foreign controller")
    if binding.actor_id == session.parent_actor_id:
        raise DelegationError("resumed child cannot be the accepting parent")
    if binding.exact_read_refs != current_exact_read_refs:
        raise DelegationError("resume exact-read refs are not current")
    if binding.tool_capability_refs != current_tool_capability_refs:
        raise DelegationError("resume tool capability refs are not current")
    if binding.budget_policy_refs != current_budget_policy_refs:
        raise DelegationError("resume budget policy refs are not current")
    if binding.stop_rule_refs != current_stop_rule_refs:
        raise DelegationError("resume stop-rule refs are not current")
    if (
        binding.token_budget,
        binding.context_ref_budget,
        binding.time_budget_seconds,
        binding.repair_budget,
    ) != current_budget_facts:
        raise DelegationError("resume budget facts are not current")
    fresh = DelegationAttempt(
        child_id=prior.child_id,
        attempt_id=attempt_id(
            prior.child_id,
            prior.fence + 1,
            binding.digest,
        ),
        binding=binding,
        child_slot=prior.child_slot,
        depth=MAX_DEPTH,
        fence=prior.fence + 1,
        lease_until=lease_until,
        idempotency_key=idempotency_key,
    )
    return DelegationSession(
        parent_controller_state_id=session.parent_controller_state_id,
        parent_actor_id=session.parent_actor_id,
        attempts=_state_with_attempt(session, fresh),
        events=session.events,
        parent_decisions=session.parent_decisions,
    )


__all__ = [
    "DelegationSession",
    "record_child_event",
    "record_parent_decision",
    "resume_fresh_attempt",
    "start_fake_attempt",
]

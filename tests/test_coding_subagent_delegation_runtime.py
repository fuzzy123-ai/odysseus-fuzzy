from dataclasses import replace

import pytest

from src.coding_subagent_delegation_contracts import (
    CancellationOrigin,
    DelegationError,
    DelegationRole,
    DelegationStatus,
    ParentDecision,
    parent_decision_id,
)
from src.coding_subagent_delegation_runtime import (
    DelegationSession,
    record_child_event,
    record_parent_decision,
    resume_fresh_attempt,
    start_fake_attempt,
)
from tests.test_coding_subagent_delegation_contracts import SHA_A, bind


def session():
    return DelegationSession(SHA_A, "parent-1")


def start(current=None, *, binding=None, slot=1, key="key-1", lease=10):
    return start_fake_attempt(
        current or session(),
        binding=binding or bind(),
        child_slot=slot,
        lease_until=lease,
        idempotency_key=key,
    )


def complete(current, *, index=0, event_ref="complete-1", handoff="handoff-1"):
    item = current.attempts[index]
    return record_child_event(
        current,
        child=item.child_id,
        fence=item.fence,
        actor_id=item.binding.actor_id,
        status=DelegationStatus.COMPLETED,
        event_ref=event_ref,
        now=1,
        handoff_refs=(handoff,),
    )


def decide(current, *, index=0, outcome=DelegationStatus.ACCEPTED, ref="decision-1"):
    item = current.attempts[index]
    completion = next(
        event
        for event in current.events
        if event.attempt_id == item.attempt_id
        and event.status is DelegationStatus.COMPLETED
    )
    decision = ParentDecision(
        decision_id=parent_decision_id(
            controller_state_id=current.parent_controller_state_id,
            parent_actor_id=current.parent_actor_id,
            child=item.child_id,
            attempt=item.attempt_id,
            fence=item.fence,
            completion_event_id=completion.event_id,
            decision_ref=ref,
            outcome=outcome,
        ),
        controller_state_id=current.parent_controller_state_id,
        parent_actor_id=current.parent_actor_id,
        child_id=item.child_id,
        attempt_id=item.attempt_id,
        fence=item.fence,
        completion_event_id=completion.event_id,
        decision_ref=ref,
        outcome=outcome,
    )
    return record_parent_decision(current, decision), decision


def current_resume_facts(binding):
    return {
        "current_exact_read_refs": binding.exact_read_refs,
        "current_tool_capability_refs": binding.tool_capability_refs,
        "current_budget_policy_refs": binding.budget_policy_refs,
        "current_stop_rule_refs": binding.stop_rule_refs,
        "current_budget_facts": (
            binding.token_budget,
            binding.context_ref_budget,
            binding.time_budget_seconds,
            binding.repair_budget,
        ),
    }


def test_two_active_children_block_third_then_allow_sequential_reviewer():
    current = start()
    current = start(
        current,
        binding=bind(DelegationRole.TESTER),
        slot=2,
        key="tester-key",
    )
    with pytest.raises(DelegationError, match="fanout"):
        start(
            current,
            binding=bind(DelegationRole.REVIEWER),
            slot=1,
            key="review-key",
        )
    current = complete(current)
    current, _ = decide(current)
    current = start(
        current,
        binding=bind(DelegationRole.REVIEWER),
        slot=1,
        key="review-key",
    )
    assert len(current.attempts) == 3
    assert sum(
        item.status in {DelegationStatus.PLANNED, DelegationStatus.COMPLETED}
        for item in current.attempts
    ) == 2
    assert current.attempts[2].binding.role is DelegationRole.REVIEWER


def test_event_replay_is_idempotent_and_collision_fences_fail_closed():
    current = start()
    item = current.attempts[0]
    completed = complete(current)
    replay = record_child_event(
        completed,
        child=item.child_id,
        fence=1,
        actor_id=item.binding.actor_id,
        status=DelegationStatus.COMPLETED,
        event_ref="complete-1",
        now=2,
        handoff_refs=("handoff-1",),
    )
    assert replay == completed
    with pytest.raises(DelegationError, match="collision"):
        record_child_event(
            completed,
            child=item.child_id,
            fence=1,
            actor_id=item.binding.actor_id,
            status=DelegationStatus.COMPLETED,
            event_ref="complete-1",
            now=2,
            handoff_refs=("handoff-other",),
        )
    for fence in (0, 2):
        with pytest.raises(DelegationError, match="fence"):
            record_child_event(
                current,
                child=item.child_id,
                fence=fence,
                actor_id=item.binding.actor_id,
                status=DelegationStatus.COMPLETED,
                event_ref=f"bad-fence-{fence}",
                now=1,
                handoff_refs=("handoff-1",),
            )


def test_lease_boundary_expires_fail_closed_and_cannot_be_decided():
    current = start(lease=5)
    item = current.attempts[0]
    expired = record_child_event(
        current,
        child=item.child_id,
        fence=1,
        actor_id=item.binding.actor_id,
        status=DelegationStatus.COMPLETED,
        event_ref="lease-boundary",
        now=5,
        handoff_refs=("ignored-after-expiry",),
    )
    assert expired.attempts[0].status is DelegationStatus.EXPIRED
    assert expired.events[0].handoff_refs == ()
    event = expired.events[0]
    decision = ParentDecision(
        decision_id=parent_decision_id(
            controller_state_id=expired.parent_controller_state_id,
            parent_actor_id=expired.parent_actor_id,
            child=item.child_id,
            attempt=item.attempt_id,
            fence=item.fence,
            completion_event_id=event.event_id,
            decision_ref="invalid-expired-decision",
            outcome=DelegationStatus.ACCEPTED,
        ),
        controller_state_id=expired.parent_controller_state_id,
        parent_actor_id=expired.parent_actor_id,
        child_id=item.child_id,
        attempt_id=item.attempt_id,
        fence=item.fence,
        completion_event_id=event.event_id,
        decision_ref="invalid-expired-decision",
        outcome=DelegationStatus.ACCEPTED,
    )
    with pytest.raises(DelegationError, match="completion"):
        record_parent_decision(expired, decision)


@pytest.mark.parametrize(
    ("origin", "actor_id"),
    [
        (CancellationOrigin.CHILD, "implementer-1"),
        (CancellationOrigin.PARENT, "parent-1"),
    ],
)
def test_parent_and_child_cancellation_receipts_are_actor_bound(origin, actor_id):
    current = start()
    item = current.attempts[0]
    cancelled = record_child_event(
        current,
        child=item.child_id,
        fence=1,
        actor_id=actor_id,
        status=DelegationStatus.CANCELLED,
        event_ref=f"cancel-{origin.value}",
        now=1,
        cancellation_origin=origin,
        cancellation_ref=f"cancel-receipt-{origin.value}",
    )
    assert cancelled.events[0].cancellation_origin is origin
    with pytest.raises(DelegationError, match="origin"):
        record_child_event(
            current,
            child=item.child_id,
            fence=1,
            actor_id="parent-1" if origin is CancellationOrigin.CHILD else "implementer-1",
            status=DelegationStatus.CANCELLED,
            event_ref="cancel-wrong-actor",
            now=1,
            cancellation_origin=origin,
            cancellation_ref="cancel-receipt-wrong",
        )


def test_only_bound_parent_can_accept_or_reject_completed_child():
    current = complete(start())
    accepted, decision = decide(current)
    assert accepted.attempts[0].status is DelegationStatus.ACCEPTED
    assert record_parent_decision(accepted, decision) == accepted
    foreign = replace(
        decision,
        decision_id=parent_decision_id(
            controller_state_id=decision.controller_state_id,
            parent_actor_id="foreign-parent",
            child=decision.child_id,
            attempt=decision.attempt_id,
            fence=decision.fence,
            completion_event_id=decision.completion_event_id,
            decision_ref="foreign-decision",
            outcome=DelegationStatus.REJECTED,
        ),
        parent_actor_id="foreign-parent",
        decision_ref="foreign-decision",
        outcome=DelegationStatus.REJECTED,
    )
    with pytest.raises(DelegationError, match="bound parent"):
        record_parent_decision(current, foreign)
    item = start().attempts[0]
    with pytest.raises(DelegationError, match="parent decision status"):
        record_child_event(
            start(),
            child=item.child_id,
            fence=1,
            actor_id=item.binding.actor_id,
            status=DelegationStatus.ACCEPTED,
            event_ref="self-accept",
            now=1,
        )


def test_reviewer_can_complete_but_only_parent_can_accept_review():
    current = complete(start())
    current, _ = decide(current)
    current = start(
        current,
        binding=bind(DelegationRole.REVIEWER),
        slot=2,
        key="review-key",
    )
    current = complete(
        current,
        index=1,
        event_ref="review-complete",
        handoff="review-evidence",
    )
    assert current.attempts[1].status is DelegationStatus.COMPLETED
    current, _ = decide(current, index=1, ref="parent-review-decision")
    assert current.attempts[1].status is DelegationStatus.ACCEPTED


def test_resume_requires_fresh_binding_and_current_policy_budget_and_reads():
    current = start()
    item = current.attempts[0]
    rejected = complete(current)
    rejected, _ = decide(rejected, outcome=DelegationStatus.REJECTED)
    fresh = replace(item.binding, capsule_id="sha256:" + "9" * 64)
    resumed = resume_fresh_attempt(
        rejected,
        child=item.child_id,
        binding=fresh,
        lease_until=30,
        idempotency_key="resume-key-2",
        **current_resume_facts(fresh),
    )
    assert resumed.attempts[0].fence == 2
    assert resumed.attempts[0].binding.capsule_id == fresh.capsule_id
    with pytest.raises(DelegationError, match="fresh"):
        resume_fresh_attempt(
            rejected,
            child=item.child_id,
            binding=item.binding,
            lease_until=30,
            idempotency_key="key-1",
            **current_resume_facts(item.binding),
        )


@pytest.mark.parametrize(
    "binding",
    [
        bind(parent_envelope_id="sha256:" + "1" * 64, capsule_id="sha256:" + "2" * 64),
        bind(input_revision="input-rev-2", capsule_id="sha256:" + "3" * 64),
        bind(DelegationRole.TESTER, capsule_id="sha256:" + "4" * 64),
    ],
)
def test_resume_rejects_parent_input_and_role_changes(binding):
    current = start()
    item = current.attempts[0]
    rejected = complete(current)
    rejected, _ = decide(rejected, outcome=DelegationStatus.REJECTED)
    with pytest.raises(DelegationError, match="parent, authority, input, role, or slot"):
        resume_fresh_attempt(
            rejected,
            child=item.child_id,
            binding=binding,
            lease_until=30,
            idempotency_key="resume-key",
            **current_resume_facts(binding),
        )


@pytest.mark.parametrize(
    "override",
    [
        {"current_exact_read_refs": ("read-stale",)},
        {"current_tool_capability_refs": ("tool-stale",)},
        {"current_budget_policy_refs": ("budget-stale",)},
        {"current_stop_rule_refs": ("stop-stale",)},
        {"current_budget_facts": (1, 1, 1, 1)},
    ],
)
def test_resume_rejects_noncurrent_policy_budget_tool_and_reads(override):
    current = start()
    item = current.attempts[0]
    rejected = complete(current)
    rejected, _ = decide(rejected, outcome=DelegationStatus.REJECTED)
    fresh = replace(item.binding, capsule_id="sha256:" + "8" * 64)
    facts = current_resume_facts(fresh)
    facts.update(override)
    with pytest.raises(DelegationError, match="not current"):
        resume_fresh_attempt(
            rejected,
            child=item.child_id,
            binding=fresh,
            lease_until=30,
            idempotency_key="resume-key",
            **facts,
        )

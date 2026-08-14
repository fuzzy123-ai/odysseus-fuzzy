from src.coding_loop_controller import start_coding_loop_controller
from src.coding_subagent_capsule import CodingSubagentRole
from src.coding_subagent_delegation_contracts import (
    DelegationRole,
    DelegationStatus,
    ParentDecision,
    binding_from_controller_capsule,
    parent_decision_id,
)
from src.coding_subagent_delegation_runtime import (
    DelegationSession,
    record_child_event,
    record_parent_decision,
    start_fake_attempt,
)
from tests.test_coding_loop_controller import _context, _lifecycle


def test_ready_b1_capsules_drive_bounded_c_controller_delegation_receipts():
    authority, envelope, capsules = _context()
    controller = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority),
        parent_envelope=envelope,
        capsules=capsules,
    )
    by_role = {capsule.role: capsule for capsule in capsules}
    implementer = binding_from_controller_capsule(
        controller,
        by_role[CodingSubagentRole.IMPLEMENTER],
    )
    tester = binding_from_controller_capsule(
        controller,
        by_role[CodingSubagentRole.TESTER],
    )
    reviewer = binding_from_controller_capsule(
        controller,
        by_role[CodingSubagentRole.REVIEWER],
    )

    assert implementer.role is DelegationRole.IMPLEMENTER
    assert reviewer.role is DelegationRole.REVIEWER
    assert set(reviewer.retrieval_snapshot_refs).isdisjoint(
        reviewer.implementer_snapshot_refs
    )
    assert implementer.exact_read_refs
    assert implementer.provenance_refs
    assert implementer.budget_policy_refs

    current = DelegationSession(controller.state_id, "parent-controller-actor")
    current = start_fake_attempt(
        current,
        binding=implementer,
        child_slot=1,
        lease_until=20,
        idempotency_key="implementer-attempt-1",
    )
    current = start_fake_attempt(
        current,
        binding=tester,
        child_slot=2,
        lease_until=20,
        idempotency_key="tester-attempt-1",
    )
    implementation = current.attempts[0]
    current = record_child_event(
        current,
        child=implementation.child_id,
        fence=implementation.fence,
        actor_id=implementer.actor_id,
        status=DelegationStatus.COMPLETED,
        event_ref="implementation-complete-1",
        now=1,
        handoff_refs=(
            "edit-receipt-1",
            "execution-receipt-1",
            "failure-envelope-or-none-1",
            "intelligence-receipt-1",
        ),
    )
    completion = current.events[0]
    decision = ParentDecision(
        decision_id=parent_decision_id(
            controller_state_id=controller.state_id,
            parent_actor_id=current.parent_actor_id,
            child=implementation.child_id,
            attempt=implementation.attempt_id,
            fence=implementation.fence,
            completion_event_id=completion.event_id,
            decision_ref="parent-accept-implementation-1",
            outcome=DelegationStatus.ACCEPTED,
        ),
        controller_state_id=controller.state_id,
        parent_actor_id=current.parent_actor_id,
        child_id=implementation.child_id,
        attempt_id=implementation.attempt_id,
        fence=implementation.fence,
        completion_event_id=completion.event_id,
        decision_ref="parent-accept-implementation-1",
        outcome=DelegationStatus.ACCEPTED,
    )
    current = record_parent_decision(current, decision)
    current = start_fake_attempt(
        current,
        binding=reviewer,
        child_slot=1,
        lease_until=20,
        idempotency_key="reviewer-attempt-1",
    )

    assert len(current.attempts) == 3
    assert current.attempts[0].status is DelegationStatus.ACCEPTED
    assert current.attempts[2].binding.role is DelegationRole.REVIEWER
    for item in current.attempts:
        assert item.dispatch_performed is False
        assert item.execution_allowed is False
        assert item.edit_allowed is False
        assert item.write_allowed is False
        assert item.dispatch_allowed is False
        assert item.gate_close_allowed is False
        assert item.network_allowed is False
        assert item.live_effect_allowed is False
        assert item.raw_content_visible is False

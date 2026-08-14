from dataclasses import replace

import pytest

from src.coding_subagent_delegation_contracts import (
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
    parent_decision_id,
)


SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64


def bind(role=DelegationRole.IMPLEMENTER, **overrides):
    values = {
        "controller_state_id": SHA_A,
        "parent_envelope_id": SHA_B,
        "capsule_id": SHA_C,
        "actor_id": "implementer-1",
        "role": role,
        "planning_item_id": "CAO-08C1",
        "planning_revision": "planning-rev-1",
        "claim_id": "claim-1",
        "claim_owner": "claim-owner-1",
        "scope_digest": SHA_D,
        "input_revision": "input-rev-1",
        "exact_read_refs": ("read-1",),
        "provenance_refs": ("provenance-1",),
        "retrieval_snapshot_refs": ("snapshot-implementer",),
        "implementer_actor_id": "implementer-1",
        "retrieval_identity_ref": "retrieval-implementer",
        "implementer_retrieval_identity_ref": "retrieval-implementer",
        "implementer_snapshot_refs": ("snapshot-implementer",),
        "acceptance_check_refs": (),
        "tool_capability_refs": ("tool-local",),
        "budget_policy_refs": ("budget-policy-1",),
        "stop_rule_refs": ("stop-rule-1",),
        "cancellation_descriptor_ref": "cancel-policy-1",
        "expiry_descriptor_ref": "expiry-policy-1",
        "resume_descriptor_ref": "resume-policy-1",
        "token_budget": 2_000,
        "context_ref_budget": 8,
        "time_budget_seconds": 300,
        "repair_budget": 1,
        "reviewer_identity_ref": "",
    }
    if role is DelegationRole.TESTER:
        values.update(
            capsule_id="sha256:" + "e" * 64,
            actor_id="tester-1",
            retrieval_identity_ref="retrieval-tester",
            retrieval_snapshot_refs=("snapshot-tester",),
            acceptance_check_refs=("acceptance-tests",),
            repair_budget=0,
        )
    elif role is DelegationRole.REVIEWER:
        values.update(
            capsule_id="sha256:" + "f" * 64,
            actor_id="reviewer-1",
            retrieval_identity_ref="retrieval-reviewer",
            retrieval_snapshot_refs=("snapshot-reviewer",),
            acceptance_check_refs=("acceptance-review",),
            repair_budget=0,
            reviewer_identity_ref="independent-reviewer-1",
        )
    values.update(overrides)
    return DelegationBinding(**values)


def attempt(binding=None, *, slot=1, fence=1, **overrides):
    binding = binding or bind()
    child = child_id(binding, slot)
    values = {
        "child_id": child,
        "attempt_id": attempt_id(child, fence, binding.digest),
        "binding": binding,
        "child_slot": slot,
        "depth": 1,
        "fence": fence,
        "lease_until": 20,
        "idempotency_key": f"attempt-key-{fence}",
    }
    values.update(overrides)
    return DelegationAttempt(**values)


def test_binding_and_attempt_are_deterministic_and_direct_forgery_fails():
    first = bind()
    assert first.digest == bind().digest
    current = attempt(first)
    assert current.dispatch_performed is False
    assert current.live_effect_allowed is False
    with pytest.raises(DelegationError, match="child identity"):
        replace(current, child_id=SHA_A)
    with pytest.raises(DelegationError, match="attempt identity"):
        replace(current, attempt_id=SHA_B)
    with pytest.raises(DelegationError, match="exactly one"):
        replace(current, depth=2)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("controller_state_id", "sha256:" + "1" * 64),
        ("parent_envelope_id", "sha256:" + "2" * 64),
        ("planning_item_id", "CAO-foreign"),
        ("planning_revision", "planning-rev-2"),
        ("claim_id", "claim-2"),
        ("claim_owner", "claim-owner-2"),
        ("scope_digest", "sha256:" + "3" * 64),
        ("input_revision", "input-rev-2"),
    ],
)
def test_child_identity_changes_with_every_authoritative_fact(field_name, value):
    baseline = bind()
    changed = replace(baseline, **{field_name: value})
    assert child_id(changed, 1) != child_id(baseline, 1)


def test_child_identity_changes_with_role_and_slot():
    assert child_id(bind(), 1) != child_id(bind(), 2)
    assert child_id(bind(), 1) != child_id(bind(DelegationRole.TESTER), 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"actor_id": "implementer-1"},
        {"actor_id": "claim-owner-1"},
        {"retrieval_identity_ref": "retrieval-implementer"},
        {"retrieval_snapshot_refs": ("snapshot-implementer",)},
        {"acceptance_check_refs": ()},
        {"reviewer_identity_ref": ""},
        {"repair_budget": 1},
    ],
)
def test_reviewer_isolation_is_enforced_by_public_binding(overrides):
    with pytest.raises(DelegationError):
        bind(DelegationRole.REVIEWER, **overrides)


@pytest.mark.parametrize(
    "field_name",
    [
        "dispatch_performed",
        "execution_allowed",
        "edit_allowed",
        "write_allowed",
        "dispatch_allowed",
        "gate_close_allowed",
        "network_allowed",
        "live_effect_allowed",
        "raw_content_visible",
    ],
)
def test_every_attempt_effect_flag_fails_closed(field_name):
    with pytest.raises(DelegationError, match="remain false"):
        attempt(**{field_name: True})


def test_event_and_parent_decision_identities_reject_direct_forgery():
    current = attempt()
    event = DelegationEvent(
        event_id=delegation_event_id(
            child=current.child_id,
            attempt=current.attempt_id,
            fence=current.fence,
            actor_id=current.binding.actor_id,
            status=DelegationStatus.COMPLETED,
            event_ref="event-1",
            handoff_refs=("handoff-1",),
        ),
        child_id=current.child_id,
        attempt_id=current.attempt_id,
        fence=current.fence,
        actor_id=current.binding.actor_id,
        status=DelegationStatus.COMPLETED,
        event_ref="event-1",
        handoff_refs=("handoff-1",),
    )
    with pytest.raises(DelegationError, match="event identity"):
        replace(event, event_id=SHA_A)

    decision_id = parent_decision_id(
        controller_state_id=current.binding.controller_state_id,
        parent_actor_id="parent-1",
        child=current.child_id,
        attempt=current.attempt_id,
        fence=current.fence,
        completion_event_id=event.event_id,
        decision_ref="decision-1",
        outcome=DelegationStatus.ACCEPTED,
    )
    decision = ParentDecision(
        decision_id=decision_id,
        controller_state_id=current.binding.controller_state_id,
        parent_actor_id="parent-1",
        child_id=current.child_id,
        attempt_id=current.attempt_id,
        fence=current.fence,
        completion_event_id=event.event_id,
        decision_ref="decision-1",
        outcome=DelegationStatus.ACCEPTED,
    )
    with pytest.raises(DelegationError, match="identity"):
        replace(decision, decision_id=SHA_B)
    with pytest.raises(DelegationError, match="remain false"):
        replace(decision, gate_close_allowed=True)


def test_cancellation_event_requires_typed_origin_and_receipt():
    current = attempt()
    with pytest.raises(DelegationError, match="origin"):
        DelegationEvent(
            event_id=delegation_event_id(
                child=current.child_id,
                attempt=current.attempt_id,
                fence=1,
                actor_id=current.binding.actor_id,
                status=DelegationStatus.CANCELLED,
                event_ref="cancel-event",
            ),
            child_id=current.child_id,
            attempt_id=current.attempt_id,
            fence=1,
            actor_id=current.binding.actor_id,
            status=DelegationStatus.CANCELLED,
            event_ref="cancel-event",
        )
    event_id = delegation_event_id(
        child=current.child_id,
        attempt=current.attempt_id,
        fence=1,
        actor_id=current.binding.actor_id,
        status=DelegationStatus.CANCELLED,
        event_ref="cancel-event",
        cancellation_origin=CancellationOrigin.CHILD,
        cancellation_ref="cancel-receipt-1",
    )
    assert event_id.startswith("sha256:")

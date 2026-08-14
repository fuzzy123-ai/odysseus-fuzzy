from dataclasses import replace

import pytest

from src.coding_context_envelope import (
    CodingContextCheckpoint,
    _envelope_core,
    build_coding_context_envelope,
)
from src.coding_graph_boundary import (
    CodingGraphFreshness,
    CodingGraphKind,
    CodingGraphRef,
    CodingGraphStatus,
    CodingRetrievalKind,
    authority_scope_digest,
)
from src.coding_lifecycle_authority import (
    CodingLifecycleAuthority,
    start_authorized_coding_lifecycle,
    transition_authorized_coding_lifecycle,
)
from src.coding_loop_contracts import (
    CodingGateDecision,
    CodingGateOwner,
    CodingGateSubject,
    CodingLoopCommandKind,
    CodingLoopIntentKind,
    CodingLoopModelCommand,
    create_coding_gate_decision,
    create_coding_loop_intent,
)
from src.coding_loop_controller import (
    CodingLoopControllerError,
    CodingLoopDisposition,
    accept_coding_loop_user_gate,
    apply_coding_loop_command,
    start_coding_loop_controller,
)
from src.coding_subagent_capsule import CodingSubagentRole
from src.coding_subagent_context_broker import RolePolicy, build_role_scoped_subagent_capsules
from src.runtime_event_envelope import stable_payload_hash


INPUT_REVISION = "worktree-rev-12"
OWNER = "repo:odysseus"


def _authority(**overrides):
    values = {
        "planning_item_id": "CAO-08C",
        "planning_revision": "planning-rev-20",
        "acceptance_criteria_id": "acceptance-cao08c",
        "allowed_scope": ("src", "tests"),
        "blocked_scope": ("ops", ".git"),
        "claim_id": "claim-cao08c",
        "claim_owner": "alice",
        "claim_scope": ("src", "tests"),
        "input_revision": INPUT_REVISION,
        "input_diff_digest": "sha256:diff12",
        "acceptance_decision_id": "acceptance-decision-12",
        "evidence_id": "evidence-cao08c-12",
    }
    values.update(overrides)
    return CodingLifecycleAuthority.create(**values)


def _lifecycle(target="clarifying", authority=None):
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08c", repo_id="odysseus", authority=authority or _authority()
    )
    for item in (
        "planning", "ready_for_claim", "claimed", "context_building", "context_ready",
        "worktree_ready", "acting", "verifying", "review_ready",
    ):
        if target == "clarifying":
            return state
        state = transition_authorized_coding_lifecycle(state, target_state=item)
        if item == target:
            return state
    raise AssertionError(target)


def _ref(
    authority, ref_id, path, snapshot, provenance,
    retrieval_kind=CodingRetrievalKind.EXACT_CODE,
):
    return CodingGraphRef(
        ref_id=ref_id,
        graph_kind=CodingGraphKind.CODE,
        retrieval_kind=retrieval_kind,
        mandatory=True,
        owner_scope=OWNER,
        planning_item_id=authority.planning_item_id,
        planning_revision=authority.planning_revision,
        claim_id=authority.claim_id,
        claim_owner=authority.claim_owner,
        input_revision=INPUT_REVISION,
        scope_digest=authority_scope_digest(authority),
        source_revision_ref="source-version-12",
        content_hash="sha256:" + ref_id[-1] * 64,
        provenance_refs=(provenance,),
        retrieval_snapshot_ref=snapshot,
        freshness=CodingGraphFreshness.CURRENT,
        status=CodingGraphStatus.AVAILABLE,
        repo_path=path,
    )


def _context(
    implementer_retrieval=CodingRetrievalKind.EXACT_CODE,
    *,
    multi_ref_implementer=False,
):
    authority = _authority()
    refs = (
        _ref(
            authority, "code-ref-implementer-1", "src/coding_loop_controller.py",
            "snapshot-impl", "prov-impl", implementer_retrieval,
        ),
        _ref(authority, "code-ref-tester-2", "tests/test_coding_loop_controller.py", "snapshot-test", "prov-test"),
        _ref(authority, "code-ref-reviewer-3", "src/coding_loop_contracts.py", "snapshot-review", "prov-review"),
    )
    envelope = build_coding_context_envelope(
        checkpoint=CodingContextCheckpoint.PRE_SLICE,
        lifecycle=_lifecycle("claimed", authority),
        owner_scope=OWNER,
        input_revision=INPUT_REVISION,
        objective_ref="objective-cao08c",
        objective_digest="sha256:" + "d" * 64,
        graph_refs=refs,
        acceptance_check_refs=("acceptance-check-pytest",),
        tool_capability_refs=("tool-capability-controller-intent",),
        budget_policy_refs=("budget-policy-controller",),
        stop_rule_refs=("stop-rule-scope-escape",),
    )
    implementer_refs = (
        (refs[0].ref_id, refs[1].ref_id)
        if multi_ref_implementer
        else (refs[0].ref_id,)
    )
    policies = (
        _policy(CodingSubagentRole.IMPLEMENTER, "alice-implementer", implementer_refs, "retrieval-impl", repair=2),
        _policy(CodingSubagentRole.TESTER, "bob-tester", refs[1].ref_id, "retrieval-test"),
        _policy(CodingSubagentRole.REVIEWER, "charlie-reviewer", refs[2].ref_id, "retrieval-review", reviewer=True),
    )
    capsules = build_role_scoped_subagent_capsules(
        parent_envelope=envelope,
        implementer_actor_id="alice-implementer",
        role_policies=policies,
    )
    return authority, envelope, capsules


def _policy(role, actor, ref_id, retrieval, *, repair=0, reviewer=False):
    graph_ref_ids = (ref_id,) if isinstance(ref_id, str) else ref_id
    return RolePolicy(
        role=role,
        actor_id=actor,
        graph_ref_ids=graph_ref_ids,
        acceptance_check_refs=("acceptance-check-pytest",) if role is not CodingSubagentRole.IMPLEMENTER else (),
        tool_capability_refs=("tool-capability-controller-intent",),
        budget_policy_refs=("budget-policy-controller",),
        stop_rule_refs=("stop-rule-scope-escape",),
        exact_read_refs=graph_ref_ids,
        retrieval_identity_ref=retrieval,
        cancellation_descriptor_ref=f"cancel-{role.value}",
        expiry_descriptor_ref=f"expiry-{role.value}",
        resume_descriptor_ref=f"resume-{role.value}",
        token_budget=2048,
        context_ref_budget=16,
        time_budget_seconds=600,
        repair_budget=repair,
        independent_reviewer_ref="independent-review-1" if reviewer else "",
    )


def _gate(subject, *, owner=CodingGateOwner.AGENT_AUTO, accepted=True, decision_ref=None, authority=None):
    authority = authority or _authority()
    return create_coding_gate_decision(
        owner=owner,
        subject=subject,
        decision_ref=decision_ref or (
            authority.acceptance_decision_id
            if subject is CodingGateSubject.INDEPENDENT_REVIEW
            else f"gate-{subject.value}"
        ),
        planning_item_id=authority.planning_item_id,
        planning_revision=authority.planning_revision,
        claim_id=authority.claim_id,
        input_revision=authority.input_revision,
        accepted=accepted,
    )


def _advance(ref, target):
    return CodingLoopModelCommand(
        command_kind=CodingLoopCommandKind.ADVANCE,
        command_ref=ref,
        target_state=target,
    )


def test_controller_requires_planning_envelope_and_role_capsule_bindings():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("clarifying", authority), parent_envelope=envelope, capsules=capsules
    )
    assert state.disposition is CodingLoopDisposition.RUNNING

    foreign = _authority(planning_revision="planning-rev-foreign")
    with pytest.raises(CodingLoopControllerError, match="authority mismatch"):
        start_coding_loop_controller(
            lifecycle=_lifecycle("clarifying", foreign), parent_envelope=envelope, capsules=capsules
        )
    with pytest.raises(CodingLoopControllerError, match="parent envelope"):
        start_coding_loop_controller(lifecycle=_lifecycle("clarifying", authority), capsules=capsules)

    forged_authority_digest = stable_payload_hash(
        _authority(evidence_id="foreign-evidence-id").to_dict()
    )
    forged_core = _envelope_core(envelope)
    forged_core["authority_digest"] = forged_authority_digest
    forged_envelope = replace(
        envelope,
        authority_digest=forged_authority_digest,
        envelope_id=stable_payload_hash(forged_core),
    )
    with pytest.raises(CodingLoopControllerError, match="authority mismatch"):
        start_coding_loop_controller(
            lifecycle=_lifecycle("clarifying", authority),
            parent_envelope=forged_envelope,
        )


def test_default_denies_unscoped_mutation_and_semantic_ref_as_other_receipt():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority), parent_envelope=envelope, capsules=capsules
    )
    gate = _gate(CodingGateSubject.ROUTINE_IMPLEMENTATION)
    base = {
        "command_kind": CodingLoopCommandKind.MUTATION_INTENT,
        "command_ref": "patch-command-1",
        "intent_kind": CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
        "role": "implementer",
        "payload_digest": "sha256:" + "e" * 64,
    }
    with pytest.raises(CodingLoopControllerError, match="unscoped"):
        apply_coding_loop_command(
            state,
            command=CodingLoopModelCommand(
                **base,
                target_graph_ref="foreign-ref",
                exact_read_required_ref="foreign-ref",
            ),
            gate=gate,
        )
    with pytest.raises(CodingLoopControllerError, match="outside role capsule"):
        apply_coding_loop_command(
            state,
            command=CodingLoopModelCommand(
                **base,
                target_graph_ref="code-ref-implementer-1",
                exact_read_required_ref="code-ref-reviewer-3",
            ),
            gate=gate,
        )
    with pytest.raises(CodingLoopControllerError, match="role does not match"):
        apply_coding_loop_command(
            state,
            command=CodingLoopModelCommand(
                **{**base, "role": "reviewer"},
                target_graph_ref="code-ref-implementer-1",
                exact_read_required_ref="code-ref-implementer-1",
            ),
            gate=gate,
        )


def test_gate_owner_stops_and_deterministic_command_dedupe():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority), parent_envelope=envelope, capsules=capsules
    )
    command = CodingLoopModelCommand(
        command_kind=CodingLoopCommandKind.MUTATION_INTENT,
        command_ref="patch-command-2",
        intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
        role="implementer",
        target_graph_ref="code-ref-implementer-1",
        exact_read_required_ref="code-ref-implementer-1",
        payload_digest="sha256:" + "f" * 64,
    )
    accepted = apply_coding_loop_command(
        state, command=command, gate=_gate(CodingGateSubject.ROUTINE_IMPLEMENTATION)
    )
    assert apply_coding_loop_command(
        accepted, command=command, gate=_gate(CodingGateSubject.ROUTINE_IMPLEMENTATION)
    ) is accepted
    assert len(accepted.intents) == 1

    waiting = apply_coding_loop_command(
        state,
        command=command,
        gate=_gate(
            CodingGateSubject.ARCHITECTURE,
            owner=CodingGateOwner.USER_ACCEPTANCE,
            accepted=False,
            decision_ref="architecture-user-gate",
        ),
    )
    assert waiting.disposition is CodingLoopDisposition.WAITING
    assert waiting.intents == ()


def test_gate_binding_and_command_ref_idempotency_conflicts_block():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority), parent_envelope=envelope, capsules=capsules
    )
    command = CodingLoopModelCommand(
        command_kind=CodingLoopCommandKind.MUTATION_INTENT,
        command_ref="stable-command-ref",
        intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
        role="implementer",
        target_graph_ref="code-ref-implementer-1",
        exact_read_required_ref="code-ref-implementer-1",
        payload_digest="sha256:" + "a" * 64,
    )
    accepted = apply_coding_loop_command(
        state, command=command, gate=_gate(CodingGateSubject.ROUTINE_IMPLEMENTATION)
    )
    changed = replace(command, payload_digest="sha256:" + "b" * 64)
    with pytest.raises(CodingLoopControllerError, match="idempotency conflict"):
        apply_coding_loop_command(
            accepted, command=changed, gate=_gate(CodingGateSubject.ROUTINE_IMPLEMENTATION)
        )
    foreign_gate = _gate(
        CodingGateSubject.ROUTINE_IMPLEMENTATION,
        authority=_authority(planning_revision="planning-rev-foreign"),
    )
    with pytest.raises(CodingLoopControllerError, match="Planning binding"):
        apply_coding_loop_command(state, command=command, gate=foreign_gate)


@pytest.mark.parametrize(
    "retrieval_kind",
    [CodingRetrievalKind.RAPTOR, CodingRetrievalKind.GRAPHRAG],
)
def test_semantic_retrieval_cannot_satisfy_exact_read(retrieval_kind):
    authority, envelope, capsules = _context(retrieval_kind)
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority), parent_envelope=envelope, capsules=capsules
    )
    with pytest.raises(CodingLoopControllerError, match="exact_code"):
        apply_coding_loop_command(
            state,
            command=CodingLoopModelCommand(
                command_kind=CodingLoopCommandKind.MUTATION_INTENT,
                command_ref=f"semantic-{retrieval_kind.value}",
                intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
                role="implementer",
                target_graph_ref="code-ref-implementer-1",
                exact_read_required_ref="code-ref-implementer-1",
                payload_digest="sha256:" + "c" * 64,
            ),
            gate=_gate(CodingGateSubject.ROUTINE_IMPLEMENTATION),
        )


def test_intent_kind_mapping_is_exact():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority), parent_envelope=envelope, capsules=capsules
    )
    with pytest.raises(CodingLoopControllerError, match="mutation intent kind"):
        apply_coding_loop_command(
            state,
            command=CodingLoopModelCommand(
                command_kind=CodingLoopCommandKind.MUTATION_INTENT,
                command_ref="ambiguous-mutation",
                intent_kind=CodingLoopIntentKind.REQUEST_EXACT_READ,
                role="implementer",
                target_graph_ref="code-ref-implementer-1",
                exact_read_required_ref="code-ref-implementer-1",
            ),
            gate=_gate(CodingGateSubject.ROUTINE_IMPLEMENTATION),
        )
    with pytest.raises(CodingLoopControllerError, match="check intent kind"):
        apply_coding_loop_command(
            state,
            command=CodingLoopModelCommand(
                command_kind=CodingLoopCommandKind.CHECK_INTENT,
                command_ref="ambiguous-check",
                intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
                role="tester",
                target_graph_ref="code-ref-tester-2",
                exact_read_required_ref="code-ref-tester-2",
            ),
            gate=_gate(CodingGateSubject.BOUNDED_VERIFICATION),
        )


def test_declared_user_gate_waits_resumes_and_preserves_controller_evidence():
    authority, envelope, capsules = _context()
    pending = _gate(
        CodingGateSubject.ARCHITECTURE,
        owner=CodingGateOwner.USER_ACCEPTANCE,
        accepted=False,
        decision_ref="architecture-decision-1",
        authority=authority,
    )
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("verifying", authority),
        parent_envelope=envelope,
        capsules=capsules,
        user_gate_queue=(pending,),
    )
    review = CodingLoopModelCommand(
        command_kind=CodingLoopCommandKind.REVIEW,
        command_ref="review-after-user-gate",
        target_state="review_ready",
        role="reviewer",
        evidence_ref=authority.evidence_id,
    )
    held = apply_coding_loop_command(
        state, command=review, gate=_gate(CodingGateSubject.INDEPENDENT_REVIEW)
    )
    assert held.disposition is CodingLoopDisposition.WAITING
    assert held.waiting_reasons == ("waiting_on_user:architecture",)
    assert held.intents == state.intents and held.turn_count == state.turn_count

    accepted = _gate(
        CodingGateSubject.ARCHITECTURE,
        owner=CodingGateOwner.USER_ACCEPTANCE,
        accepted=True,
        decision_ref=pending.decision_ref,
        authority=authority,
    )
    resumed = accept_coding_loop_user_gate(held, accepted_decision=accepted)
    assert resumed.disposition is CodingLoopDisposition.RUNNING
    assert resumed.intents == held.intents and resumed.turn_count == held.turn_count
    terminal = apply_coding_loop_command(
        resumed, command=review, gate=_gate(CodingGateSubject.INDEPENDENT_REVIEW)
    )
    assert terminal.disposition is CodingLoopDisposition.REVIEW_READY


def test_user_gate_resume_rejects_non_user_mixed_and_already_accepted_waits():
    authority, envelope, capsules = _context()
    pending = _gate(
        CodingGateSubject.ARCHITECTURE,
        owner=CodingGateOwner.USER_ACCEPTANCE,
        accepted=False,
        decision_ref="architecture-resume-only",
        authority=authority,
    )
    accepted = _gate(
        CodingGateSubject.ARCHITECTURE,
        owner=CodingGateOwner.USER_ACCEPTANCE,
        accepted=True,
        decision_ref=pending.decision_ref,
        authority=authority,
    )
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority),
        parent_envelope=envelope,
        capsules=capsules,
        user_gate_queue=(pending,),
        max_turns=1,
    )

    consumed = apply_coding_loop_command(
        state, command=_advance("consume-only-turn", "verifying")
    )
    budget_wait = apply_coding_loop_command(
        consumed, command=_advance("turn-budget-wait", "acting")
    )
    assert budget_wait.waiting_reasons == ("turn_budget_exhausted",)
    with pytest.raises(CodingLoopControllerError, match="exactly the unresolved"):
        accept_coding_loop_user_gate(budget_wait, accepted_decision=accepted)

    gate_wait = apply_coding_loop_command(
        state,
        command=CodingLoopModelCommand(
            command_kind=CodingLoopCommandKind.MUTATION_INTENT,
            command_ref="arbitrary-gate-wait",
            intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
            role="implementer",
            target_graph_ref="code-ref-implementer-1",
            exact_read_required_ref="code-ref-implementer-1",
        ),
        gate=pending,
    )
    assert gate_wait.waiting_reasons == (
        "gate_waiting:user_acceptance:architecture",
    )
    with pytest.raises(CodingLoopControllerError, match="exactly the unresolved"):
        accept_coding_loop_user_gate(gate_wait, accepted_decision=accepted)

    with pytest.raises(CodingLoopControllerError, match="exactly match pending"):
        replace(
            state,
            disposition=CodingLoopDisposition.WAITING,
            waiting_reasons=("waiting_on_user:architecture", "turn_budget_exhausted"),
        )
    accepted_queue = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority),
        parent_envelope=envelope,
        capsules=capsules,
        user_gate_queue=(accepted,),
    )
    with pytest.raises(CodingLoopControllerError, match="exactly match pending"):
        replace(
            accepted_queue,
            disposition=CodingLoopDisposition.WAITING,
            waiting_reasons=("waiting_on_user:architecture",),
        )


def test_user_gate_resume_requires_the_subject_to_still_be_unresolved():
    authority, envelope, capsules = _context()
    accepted_architecture = _gate(
        CodingGateSubject.ARCHITECTURE,
        owner=CodingGateOwner.USER_ACCEPTANCE,
        accepted=True,
        decision_ref="architecture-already-accepted",
        authority=authority,
    )
    pending_product = _gate(
        CodingGateSubject.PRODUCT,
        owner=CodingGateOwner.USER_ACCEPTANCE,
        accepted=False,
        decision_ref="product-still-pending",
        authority=authority,
    )
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("verifying", authority),
        parent_envelope=envelope,
        capsules=capsules,
        user_gate_queue=(accepted_architecture, pending_product),
    )
    held = apply_coding_loop_command(
        state,
        command=CodingLoopModelCommand(
            command_kind=CodingLoopCommandKind.REVIEW,
            command_ref="review-with-one-pending-gate",
            target_state="review_ready",
            role="reviewer",
            evidence_ref=authority.evidence_id,
        ),
        gate=_gate(CodingGateSubject.INDEPENDENT_REVIEW),
    )
    assert held.waiting_reasons == ("waiting_on_user:product",)
    with pytest.raises(CodingLoopControllerError, match="does not match declared gate"):
        accept_coding_loop_user_gate(
            held, accepted_decision=accepted_architecture
        )


def test_review_evidence_and_acceptance_decision_must_match_authority():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("verifying", authority), parent_envelope=envelope, capsules=capsules
    )
    wrong_evidence = CodingLoopModelCommand(
        command_kind=CodingLoopCommandKind.REVIEW,
        command_ref="wrong-evidence-review",
        target_state="review_ready",
        role="reviewer",
        evidence_ref="foreign-evidence",
    )
    with pytest.raises(CodingLoopControllerError, match="review evidence"):
        apply_coding_loop_command(
            state, command=wrong_evidence, gate=_gate(CodingGateSubject.INDEPENDENT_REVIEW)
        )
    correct = replace(
        wrong_evidence, command_ref="correct-evidence-review", evidence_ref=authority.evidence_id
    )
    with pytest.raises(CodingLoopControllerError, match="review decision"):
        apply_coding_loop_command(
            state,
            command=correct,
            gate=_gate(
                CodingGateSubject.INDEPENDENT_REVIEW,
                decision_ref="foreign-acceptance-decision",
            ),
        )


def test_repair_budget_escalates_after_two_distinct_plans():
    authority, envelope, capsules = _context()
    pending = _gate(
        CodingGateSubject.ARCHITECTURE,
        owner=CodingGateOwner.USER_ACCEPTANCE,
        accepted=False,
        decision_ref="repair-escalation-user-gate",
        authority=authority,
    )
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("verifying", authority),
        parent_envelope=envelope,
        capsules=capsules,
        user_gate_queue=(pending,),
    )
    gate = _gate(CodingGateSubject.ROUTINE_IMPLEMENTATION)
    first = apply_coding_loop_command(
        state,
        command=CodingLoopModelCommand(
            command_kind=CodingLoopCommandKind.REPAIR,
            command_ref="repair-command-1",
            repair_plan_ref="repair-plan-1",
        ),
        gate=gate,
    )
    first = apply_coding_loop_command(first, command=_advance("return-acting-1", "acting"))
    first = apply_coding_loop_command(
        first,
        command=CodingLoopModelCommand(
            command_kind=CodingLoopCommandKind.CHECK_INTENT,
            command_ref="check-after-repair-1",
            intent_kind=CodingLoopIntentKind.REQUEST_BOUNDED_CHECK,
            role="tester",
            target_graph_ref="code-ref-tester-2",
            exact_read_required_ref="code-ref-tester-2",
        ),
        gate=_gate(CodingGateSubject.BOUNDED_VERIFICATION),
    )
    second = apply_coding_loop_command(
        first,
        command=CodingLoopModelCommand(
            command_kind=CodingLoopCommandKind.REPAIR,
            command_ref="repair-command-2",
            repair_plan_ref="repair-plan-2",
        ),
        gate=gate,
    )
    second = apply_coding_loop_command(second, command=_advance("return-acting-2", "acting"))
    second = apply_coding_loop_command(
        second,
        command=CodingLoopModelCommand(
            command_kind=CodingLoopCommandKind.CHECK_INTENT,
            command_ref="check-after-repair-2",
            intent_kind=CodingLoopIntentKind.REQUEST_BOUNDED_CHECK,
            role="tester",
            target_graph_ref="code-ref-tester-2",
            exact_read_required_ref="code-ref-tester-2",
        ),
        gate=_gate(CodingGateSubject.BOUNDED_VERIFICATION),
    )
    escalated = apply_coding_loop_command(
        second,
        command=CodingLoopModelCommand(
            command_kind=CodingLoopCommandKind.REPAIR,
            command_ref="repair-command-3",
            repair_plan_ref="repair-plan-3",
        ),
        gate=gate,
    )
    assert escalated.disposition is CodingLoopDisposition.WAITING
    assert escalated.waiting_reasons == ("repair_escalation_required",)
    with pytest.raises(CodingLoopControllerError, match="exactly the unresolved"):
        accept_coding_loop_user_gate(
            escalated,
            accepted_decision=_gate(
                CodingGateSubject.ARCHITECTURE,
                owner=CodingGateOwner.USER_ACCEPTANCE,
                accepted=True,
                decision_ref=pending.decision_ref,
                authority=authority,
            ),
        )


def test_direct_state_intents_require_processed_unique_command_refs():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority),
        parent_envelope=envelope,
        capsules=capsules,
    )
    unprocessed = create_coding_loop_intent(
        intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
        command_ref="unprocessed-intent-command",
        planning_item_id=authority.planning_item_id,
        planning_revision=authority.planning_revision,
        claim_id=authority.claim_id,
        claim_owner=authority.claim_owner,
        scope_digest=envelope.scope_digest,
        input_revision=authority.input_revision,
        parent_envelope_id=envelope.envelope_id,
        capsule_id=capsules[0].capsule_id,
        role="implementer",
        target_graph_ref="code-ref-implementer-1",
        exact_read_required_ref="code-ref-implementer-1",
    )
    with pytest.raises(CodingLoopControllerError, match="processed commands"):
        replace(state, intents=(unprocessed,))

    command = CodingLoopModelCommand(
        command_kind=CodingLoopCommandKind.MUTATION_INTENT,
        command_ref="processed-intent-command",
        intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
        role="implementer",
        target_graph_ref="code-ref-implementer-1",
        exact_read_required_ref="code-ref-implementer-1",
        payload_digest="sha256:" + "2" * 64,
    )
    processed = apply_coding_loop_command(
        state, command=command, gate=_gate(CodingGateSubject.ROUTINE_IMPLEMENTATION)
    )
    duplicate = create_coding_loop_intent(
        **{
            **processed.intents[0].semantic_dict(),
            "payload_digest": "sha256:" + "3" * 64,
        }
    )
    with pytest.raises(CodingLoopControllerError, match="command refs must be unique"):
        replace(processed, intents=(*processed.intents, duplicate))


def test_direct_state_intent_cannot_split_target_and_exact_ref_in_one_capsule():
    authority, envelope, capsules = _context(multi_ref_implementer=True)
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("acting", authority),
        parent_envelope=envelope,
        capsules=capsules,
    )
    split_ref = create_coding_loop_intent(
        intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
        command_ref="split-exact-ref-command",
        planning_item_id=authority.planning_item_id,
        planning_revision=authority.planning_revision,
        claim_id=authority.claim_id,
        claim_owner=authority.claim_owner,
        scope_digest=envelope.scope_digest,
        input_revision=authority.input_revision,
        parent_envelope_id=envelope.envelope_id,
        capsule_id=capsules[0].capsule_id,
        role="implementer",
        target_graph_ref="code-ref-implementer-1",
        exact_read_required_ref="code-ref-tester-2",
    )
    with pytest.raises(CodingLoopControllerError, match="authority binding"):
        replace(state, intents=(split_ref,))


def test_controller_state_direct_replace_and_review_ready_terminal_fail_closed():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("clarifying", authority), parent_envelope=envelope, capsules=capsules
    )
    with pytest.raises(CodingLoopControllerError, match="state_id"):
        replace(state, turn_count=1)
    with pytest.raises(CodingLoopControllerError, match="safe bounded identifier"):
        replace(
            state,
            disposition=CodingLoopDisposition.WAITING,
            waiting_reasons=("token=private",),
        )
    with pytest.raises(CodingLoopControllerError, match="review_ready disposition"):
        replace(state, disposition=CodingLoopDisposition.REVIEW_READY)

    foreign_intent = create_coding_loop_intent(
        intent_kind=CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
        command_ref="foreign-intent-command",
        planning_item_id="foreign-planning-item",
        planning_revision=authority.planning_revision,
        claim_id=authority.claim_id,
        claim_owner=authority.claim_owner,
        scope_digest=envelope.scope_digest,
        input_revision=authority.input_revision,
        parent_envelope_id=envelope.envelope_id,
        capsule_id=capsules[0].capsule_id,
        role="implementer",
        target_graph_ref="code-ref-implementer-1",
        exact_read_required_ref="code-ref-implementer-1",
        payload_digest="sha256:" + "1" * 64,
    )
    with pytest.raises(CodingLoopControllerError, match="authority binding"):
        replace(state, intents=(foreign_intent,))

    narrower = _authority(claim_scope=("src/coding_loop_controller.py",))
    with pytest.raises(CodingLoopControllerError, match="authority mismatch"):
        start_coding_loop_controller(
            lifecycle=_lifecycle("clarifying", narrower),
            parent_envelope=envelope,
            capsules=(),
        )
    terminal = start_coding_loop_controller(
        lifecycle=_lifecycle("review_ready", authority), parent_envelope=envelope, capsules=capsules
    )
    with pytest.raises(CodingLoopControllerError, match="terminal"):
        apply_coding_loop_command(terminal, command=_advance("too-far", "memory_review"))

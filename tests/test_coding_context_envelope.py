from dataclasses import replace
import json

import pytest

from src.coding_context_envelope import (
    CODING_CONTEXT_ENVELOPE_SCHEMA,
    CodingContextCheckpoint,
    CodingContextDisposition,
    CodingContextEnvelopeError,
    PostAcceptanceIntentRef,
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
    CodingLifecycleCompletionProof,
    start_authorized_coding_lifecycle,
    transition_authorized_coding_lifecycle,
)


OWNER = "repo:odysseus"
INPUT_REVISION = "worktree-rev-9"
OBJECTIVE_DIGEST = "sha256:" + "d" * 64
ACCEPTANCE_REFS = ("acceptance-check-pytest",)
TOOL_REFS = ("tool-capability-local-python",)
BUDGET_REFS = ("budget-policy-bounded-repair",)
STOP_REFS = ("stop-rule-scope-escape",)


def _authority(**overrides):
    values = {
        "planning_item_id": "CAO-08B",
        "planning_revision": "planning-rev-18",
        "acceptance_criteria_id": "acceptance-contract-cao08b",
        "allowed_scope": ("src", "tests"),
        "blocked_scope": ("ops", ".git"),
        "claim_id": "claim-cao08b-bob",
        "claim_owner": "bob",
        "claim_scope": ("src", "tests"),
        "input_revision": INPUT_REVISION,
        "input_diff_digest": "sha256:diff9",
        "acceptance_decision_id": "acceptance-decision-10",
        "evidence_id": "evidence-cao08b-10",
    }
    values.update(overrides)
    return CodingLifecycleAuthority.create(**values)


def _state(target="clarifying", authority=None):
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08b", repo_id="odysseus", authority=authority or _authority()
    )
    sequence = (
        "planning", "ready_for_claim", "claimed", "context_building", "context_ready",
        "worktree_ready", "acting", "verifying", "review_ready", "memory_review",
        "publish_ready",
    )
    if target == "clarifying":
        return state
    for item in sequence:
        state = transition_authorized_coding_lifecycle(state, target_state=item)
        if item == target:
            return state
    raise AssertionError(f"unsupported test target {target}")


def _ref(authority=None, **overrides):
    authority = authority or _authority()
    values = {
        "ref_id": "code-ref-1",
        "graph_kind": CodingGraphKind.CODE,
        "retrieval_kind": CodingRetrievalKind.EXACT_CODE,
        "mandatory": True,
        "owner_scope": OWNER,
        "planning_item_id": authority.planning_item_id,
        "planning_revision": authority.planning_revision,
        "claim_id": authority.claim_id,
        "claim_owner": authority.claim_owner,
        "input_revision": INPUT_REVISION,
        "scope_digest": authority_scope_digest(authority),
        "source_revision_ref": "source-version-1",
        "content_hash": "sha256:" + "a" * 64,
        "provenance_refs": ("provenance-1",),
        "retrieval_snapshot_ref": "snapshot-1",
        "freshness": CodingGraphFreshness.CURRENT,
        "status": CodingGraphStatus.AVAILABLE,
        "repo_path": "src/coding_context_envelope.py",
    }
    values.update(overrides)
    return CodingGraphRef(**values)


def _build(checkpoint, state, refs, **overrides):
    values = {
        "checkpoint": checkpoint,
        "lifecycle": state,
        "owner_scope": OWNER,
        "input_revision": INPUT_REVISION,
        "objective_ref": "planning-objective-cao08b",
        "objective_digest": OBJECTIVE_DIGEST,
        "graph_refs": refs,
    }
    if checkpoint is CodingContextCheckpoint.PRE_SLICE:
        values.update(
            acceptance_check_refs=ACCEPTANCE_REFS,
            tool_capability_refs=TOOL_REFS,
            budget_policy_refs=BUDGET_REFS,
            stop_rule_refs=STOP_REFS,
        )
    elif checkpoint is CodingContextCheckpoint.FAILURE_RETRIEVAL:
        values["trigger_evidence_ref"] = "verification-failure-1"
    values.update(overrides)
    return build_coding_context_envelope(**values)


def test_pre_plan_and_pre_slice_envelopes_are_deterministic_content_free_and_pure():
    authority = _authority()
    planning_ref = _ref(
        authority,
        ref_id="planning-ref-1",
        graph_kind=CodingGraphKind.PLANNING,
        retrieval_kind=CodingRetrievalKind.PLANNING_EXACT,
        repo_path="",
    )
    pre_plan = _build(
        CodingContextCheckpoint.PRE_PLAN,
        _state("clarifying", authority),
        (planning_ref,),
    )
    pre_slice_state = _state("claimed", authority)
    pre_slice = _build(
        CodingContextCheckpoint.PRE_SLICE,
        pre_slice_state,
        (_ref(authority),),
    )
    repeated = _build(
        CodingContextCheckpoint.PRE_SLICE,
        pre_slice_state,
        (_ref(authority),),
    )

    assert pre_plan.disposition is CodingContextDisposition.READY
    assert pre_slice.disposition is CodingContextDisposition.READY
    assert pre_slice.envelope_id == repeated.envelope_id
    assert pre_slice.to_dict() == repeated.to_dict()
    payload = pre_slice.to_dict()
    assert payload["schema"] == CODING_CONTEXT_ENVELOPE_SCHEMA
    assert payload["authority_effect"] == "none"
    assert payload["side_effects"] == ("none",)
    assert payload["execution_allowed"] is False
    assert payload["write_allowed"] is False
    assert payload["dispatch_allowed"] is False
    assert payload["live_effect_allowed"] is False
    assert payload["acceptance_check_refs"] == ACCEPTANCE_REFS
    assert payload["tool_capability_refs"] == TOOL_REFS
    assert payload["budget_policy_refs"] == BUDGET_REFS
    assert payload["stop_rule_refs"] == STOP_REFS
    dumped = json.dumps(payload, default=str)
    assert "provider private snippet" not in dumped
    assert "raw source" not in dumped


def test_envelope_digest_binds_full_graph_reference_semantics_not_only_ref_id():
    authority = _authority()
    state = _state("claimed", authority)
    variants = (
        _ref(authority),
        _ref(authority, planning_item_id="CAO-08A"),
        _ref(authority, retrieval_kind=CodingRetrievalKind.RAPTOR),
        _ref(
            authority,
            graph_kind=CodingGraphKind.CAUSAL,
            retrieval_kind=CodingRetrievalKind.CAUSAL_EXACT,
            repo_path="",
        ),
        _ref(authority, mandatory=False),
        _ref(authority, freshness=CodingGraphFreshness.RECENT),
        _ref(authority, mandatory=False, status=CodingGraphStatus.INPUTS_CHANGED),
        _ref(authority, provenance_refs=("provenance-2",)),
        _ref(authority, source_revision_ref="source-version-2"),
        _ref(authority, claim_id="claim-other"),
        _ref(authority, claim_owner="alice"),
        _ref(authority, input_revision="foreign-revision"),
        _ref(authority, scope_digest="sha256:" + "f" * 64),
        _ref(authority, content_hash="sha256:" + "b" * 64),
        _ref(authority, repo_path="src/coding_graph_boundary.py"),
    )
    envelopes = tuple(
        _build(CodingContextCheckpoint.PRE_SLICE, state, (item,)) for item in variants
    )

    assert len({item.envelope_id for item in envelopes}) == len(envelopes)
    assert len({item.graph_input_digest for item in envelopes}) == len(envelopes)


def test_direct_envelope_constructor_rejects_forged_id():
    authority = _authority()
    envelope = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", authority),
        (_ref(authority),),
    )
    with pytest.raises(CodingContextEnvelopeError, match="canonical envelope facts"):
        replace(envelope, envelope_id="sha256:" + "0" * 64)
    with pytest.raises(CodingContextEnvelopeError, match="canonical envelope facts"):
        replace(envelope, tool_capability_refs=("tool-capability-other",))


def test_policy_refs_are_bounded_content_free_and_digest_bound():
    authority = _authority()
    state = _state("claimed", authority)
    baseline = _build(
        CodingContextCheckpoint.PRE_SLICE, state, (_ref(authority),)
    )
    changed = _build(
        CodingContextCheckpoint.PRE_SLICE,
        state,
        (_ref(authority),),
        tool_capability_refs=("tool-capability-read-only",),
    )

    assert baseline.envelope_id != changed.envelope_id
    with pytest.raises(CodingContextEnvelopeError, match="tool_capability_refs"):
        _build(
            CodingContextCheckpoint.PRE_SLICE,
            state,
            (_ref(authority),),
            tool_capability_refs=("raw tool prose with spaces",),
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "acceptance_check_refs",
        "tool_capability_refs",
        "budget_policy_refs",
        "stop_rule_refs",
    ),
)
def test_pre_slice_requires_each_policy_reference_class(field_name):
    authority = _authority()
    envelope = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", authority),
        (_ref(authority),),
        **{field_name: ()},
    )

    assert envelope.disposition is CodingContextDisposition.WAITING
    assert f"{field_name}_missing" in envelope.waiting_reasons


def test_failure_retrieval_preserves_parent_authority_and_requires_exact_reads():
    authority = _authority()
    parent = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", authority),
        (_ref(authority),),
    )
    failure = _build(
        CodingContextCheckpoint.FAILURE_RETRIEVAL,
        _state("verifying", authority),
        (_ref(authority, ref_id="failure-code-ref"),),
        parent_envelope=parent,
    )

    assert failure.disposition is CodingContextDisposition.READY
    assert failure.parent_envelope_id == parent.envelope_id
    assert failure.planning_revision == parent.planning_revision
    assert failure.scope_digest == parent.scope_digest
    assert failure.input_revision == parent.input_revision
    assert failure.exact_read_required == ("failure-code-ref",)
    assert failure.acceptance_check_refs == parent.acceptance_check_refs
    assert failure.tool_capability_refs == parent.tool_capability_refs
    assert failure.budget_policy_refs == parent.budget_policy_refs
    assert failure.stop_rule_refs == parent.stop_rule_refs
    assert failure.trigger_evidence_ref == "verification-failure-1"
    assert failure.to_dict()["trigger_evidence_ref"] == "verification-failure-1"


def test_failure_retrieval_rejects_policy_widening_and_retains_parent_refs():
    authority = _authority()
    parent = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", authority),
        (_ref(authority),),
    )
    failure = _build(
        CodingContextCheckpoint.FAILURE_RETRIEVAL,
        _state("verifying", authority),
        (_ref(authority, ref_id="failure-code-ref"),),
        parent_envelope=parent,
        tool_capability_refs=("tool-capability-network",),
    )

    assert failure.disposition is CodingContextDisposition.BLOCKED
    assert "parent_tool_capability_refs_mismatch" in failure.blockers
    assert failure.tool_capability_refs == parent.tool_capability_refs


def test_failure_trigger_is_explicit_safe_and_checkpoint_specific():
    authority = _authority()
    parent = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", authority),
        (_ref(authority),),
    )
    with pytest.raises(CodingContextEnvelopeError, match="trigger_evidence_ref"):
        _build(
            CodingContextCheckpoint.FAILURE_RETRIEVAL,
            _state("verifying", authority),
            (_ref(authority, ref_id="failure-code-ref"),),
            parent_envelope=parent,
            trigger_evidence_ref="",
        )
    with pytest.raises(CodingContextEnvelopeError, match="only valid"):
        _build(
            CodingContextCheckpoint.PRE_SLICE,
            _state("claimed", authority),
            (_ref(authority),),
            trigger_evidence_ref="verification-failure-1",
        )
    with pytest.raises(CodingContextEnvelopeError, match="trigger_evidence_ref"):
        _build(
            CodingContextCheckpoint.FAILURE_RETRIEVAL,
            _state("verifying", authority),
            (_ref(authority, ref_id="failure-code-ref"),),
            parent_envelope=parent,
            trigger_evidence_ref="raw failure prose",
        )


def test_post_acceptance_creates_only_matching_independent_intent_refs():
    authority = _authority()
    parent = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", authority),
        (_ref(authority),),
    )
    proof = CodingLifecycleCompletionProof.create(
        acceptance_decision_id=authority.acceptance_decision_id,
        evidence_id=authority.evidence_id,
        reviewer_id="alice-reviewer",
        all_required_gates_closed=True,
        independent_review=True,
    )
    intent = PostAcceptanceIntentRef(
        intent_ref="memory-intent-1",
        target_graph=CodingGraphKind.MEMORY,
        planning_revision=authority.planning_revision,
        input_revision=INPUT_REVISION,
        scope_digest=authority_scope_digest(authority),
        acceptance_decision_id=proof.acceptance_decision_id,
        evidence_id=proof.evidence_id,
        reviewer_id=proof.reviewer_id,
    )
    envelope = _build(
        CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK,
        _state("review_ready", authority),
        (),
        parent_envelope=parent,
        completion_proof=proof,
        post_acceptance_intents=(intent,),
    )

    assert envelope.disposition is CodingContextDisposition.READY
    assert envelope.post_acceptance_intents == (intent,)
    assert envelope.checkpoint.value == "post_acceptance_writeback"
    intent_payload = envelope.to_dict()["post_acceptance_intents"][0]
    assert intent_payload["write_allowed"] is False
    assert intent_payload["execution_allowed"] is False
    assert intent_payload["authority_effect"] == "none"


def test_post_acceptance_without_proof_waits_and_self_or_mismatched_proof_blocks():
    authority = _authority()
    parent = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", authority),
        (_ref(authority),),
    )
    intent = PostAcceptanceIntentRef(
        intent_ref="memory-intent-1",
        target_graph=CodingGraphKind.MEMORY,
        planning_revision=authority.planning_revision,
        input_revision=INPUT_REVISION,
        scope_digest=authority_scope_digest(authority),
        acceptance_decision_id=authority.acceptance_decision_id,
        evidence_id=authority.evidence_id,
        reviewer_id="alice-reviewer",
    )
    missing = _build(
        CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK,
        _state("review_ready", authority),
        (),
        parent_envelope=parent,
        post_acceptance_intents=(intent,),
    )
    self_proof = CodingLifecycleCompletionProof.create(
        acceptance_decision_id=authority.acceptance_decision_id,
        evidence_id=authority.evidence_id,
        reviewer_id=authority.claim_owner,
        all_required_gates_closed=True,
        independent_review=True,
    )
    blocked = _build(
        CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK,
        _state("review_ready", authority),
        (),
        parent_envelope=parent,
        completion_proof=self_proof,
        post_acceptance_intents=(intent,),
    )

    assert missing.disposition is CodingContextDisposition.WAITING
    assert "independent_completion_proof_missing" in missing.waiting_reasons
    assert blocked.disposition is CodingContextDisposition.BLOCKED
    assert "completion_reviewer_not_independent" in blocked.blockers
    assert blocked.post_acceptance_intents == ()


@pytest.mark.parametrize(
    "authority_change,expected_blocker",
    (
        ({"planning_revision": "planning-rev-19"}, "planning_revision_mismatch"),
        ({"claim_scope": ("src/coding_context_envelope.py",)}, "scope_digest_mismatch"),
    ),
)
def test_post_acceptance_intent_replay_under_other_revision_or_scope_blocks(
    authority_change, expected_blocker
):
    original = _authority()
    proof = CodingLifecycleCompletionProof.create(
        acceptance_decision_id=original.acceptance_decision_id,
        evidence_id=original.evidence_id,
        reviewer_id="alice-reviewer",
        all_required_gates_closed=True,
        independent_review=True,
    )
    intent = PostAcceptanceIntentRef(
        intent_ref="memory-intent-replay",
        target_graph=CodingGraphKind.MEMORY,
        planning_revision=original.planning_revision,
        input_revision=INPUT_REVISION,
        scope_digest=authority_scope_digest(original),
        acceptance_decision_id=proof.acceptance_decision_id,
        evidence_id=proof.evidence_id,
        reviewer_id=proof.reviewer_id,
    )
    changed = _authority(**authority_change)
    parent = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", changed),
        (_ref(changed),),
    )
    envelope = _build(
        CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK,
        _state("review_ready", changed),
        (),
        parent_envelope=parent,
        completion_proof=proof,
        post_acceptance_intents=(intent,),
    )

    assert envelope.disposition is CodingContextDisposition.BLOCKED
    assert any(value.endswith(expected_blocker) for value in envelope.blockers)
    assert envelope.post_acceptance_intents == ()


def test_mandatory_bad_context_waits_while_optional_bad_context_warns_and_excludes():
    authority = _authority()
    state = _state("claimed", authority)
    mandatory = _build(
        CodingContextCheckpoint.PRE_SLICE,
        state,
        (_ref(authority, freshness=CodingGraphFreshness.UNKNOWN),),
    )
    optional = _build(
        CodingContextCheckpoint.PRE_SLICE,
        state,
        (
            _ref(
                authority,
                ref_id="optional-ref",
                mandatory=False,
                status=CodingGraphStatus.INPUTS_CHANGED,
            ),
        ),
    )

    assert mandatory.disposition is CodingContextDisposition.WAITING
    assert mandatory.waiting_reasons
    assert optional.disposition is CodingContextDisposition.READY
    assert optional.warnings
    assert optional.excluded_graph_ref_ids == ("optional-ref",)


def test_revision_scope_and_graph_kind_escalation_block_without_mutating_authority():
    authority = _authority()
    state = _state("claimed", authority)
    envelope = _build(
        CodingContextCheckpoint.PRE_SLICE,
        state,
        (
            _ref(authority, ref_id="stale", planning_revision="stale-revision"),
            _ref(authority, ref_id="scope", scope_digest="sha256:" + "f" * 64),
            _ref(
                authority,
                ref_id="planning-escalation",
                graph_kind=CodingGraphKind.PLANNING,
                retrieval_kind=CodingRetrievalKind.PLANNING_EXACT,
                repo_path="",
            ),
        ),
    )

    assert envelope.disposition is CodingContextDisposition.BLOCKED
    assert envelope.planning_revision == authority.planning_revision
    assert envelope.claim_id == authority.claim_id
    assert len(envelope.blockers) >= 3
    assert "planning-escalation" in envelope.excluded_graph_ref_ids
    assert all(item.ref_id != "planning-escalation" for item in envelope.graph_refs)


def test_parent_revision_change_and_input_revision_mismatch_block():
    first_authority = _authority()
    parent = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", first_authority),
        (_ref(first_authority),),
    )
    changed_authority = _authority(planning_revision="planning-rev-19")
    changed = _build(
        CodingContextCheckpoint.FAILURE_RETRIEVAL,
        _state("verifying", changed_authority),
        (_ref(changed_authority, ref_id="changed-ref"),),
        parent_envelope=parent,
    )
    wrong_input = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", first_authority),
        (_ref(first_authority),),
        input_revision="foreign-revision",
    )

    assert changed.disposition is CodingContextDisposition.BLOCKED
    assert "parent_planning_revision_mismatch" in changed.blockers
    assert wrong_input.disposition is CodingContextDisposition.BLOCKED
    assert "authority_input_revision_mismatch" in wrong_input.blockers


def test_missing_context_or_wrong_lifecycle_checkpoint_fails_safe():
    authority = _authority()
    missing = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", authority),
        (),
    )
    wrong_state = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("clarifying", authority),
        (_ref(authority),),
    )

    assert missing.disposition is CodingContextDisposition.WAITING
    assert "checkpoint_context_missing" in missing.waiting_reasons
    assert wrong_state.disposition is CodingContextDisposition.BLOCKED
    assert "checkpoint_state_mismatch" in wrong_state.blockers


def test_missing_planning_authority_rejects_before_retrieval_can_supply_identity():
    missing_authority = _authority(planning_item_id="", planning_revision="")
    retrieval_ref = _ref(
        planning_item_id="CAO-08B", planning_revision="planning-rev-18"
    )

    with pytest.raises(CodingContextEnvelopeError, match="Planning authority"):
        _build(
            CodingContextCheckpoint.PRE_PLAN,
            _state("clarifying", missing_authority),
            (retrieval_ref,),
        )


def test_old_post_acceptance_checkpoint_token_is_not_authoritative():
    authority = _authority()
    with pytest.raises(CodingContextEnvelopeError, match="checkpoint is invalid"):
        _build("post_acceptance", _state("review_ready", authority), ())


def test_owner_scope_is_bound_to_lifecycle_repository_not_caller_consistency():
    authority = _authority()
    foreign_ref = _ref(authority, owner_scope="repo:foreign")
    envelope = _build(
        CodingContextCheckpoint.PRE_SLICE,
        _state("claimed", authority),
        (foreign_ref,),
        owner_scope="repo:foreign",
    )

    assert envelope.disposition is CodingContextDisposition.BLOCKED
    assert "lifecycle_owner_scope_mismatch" in envelope.blockers

    with pytest.raises(CodingContextEnvelopeError, match="owner_scope"):
        _build(
            CodingContextCheckpoint.PRE_SLICE,
            _state("claimed", authority),
            (_ref(authority),),
            owner_scope="repo:token=abc123",
        )
    with pytest.raises(CodingContextEnvelopeError, match="owner_scope"):
        _build(
            CodingContextCheckpoint.PRE_SLICE,
            _state("claimed", authority),
            (_ref(authority),),
            owner_scope=r"repo:C:\private",
        )


def test_raw_private_malformed_and_non_json_inputs_are_strictly_rejected():
    authority = _authority()
    state = _state("claimed", authority)
    with pytest.raises(CodingContextEnvelopeError, match="objective_ref"):
        _build(
            CodingContextCheckpoint.PRE_SLICE,
            state,
            (_ref(authority),),
            objective_ref=r"C:\Users\private\objective",
        )
    with pytest.raises(CodingContextEnvelopeError, match="objective_digest"):
        _build(
            CodingContextCheckpoint.PRE_SLICE,
            state,
            (_ref(authority),),
            objective_digest="raw objective text",
        )
    with pytest.raises(CodingContextEnvelopeError, match="graph_refs"):
        _build(
            CodingContextCheckpoint.PRE_SLICE,
            state,
            [{"content": "raw source"}],
        )


def test_envelope_is_immutable_and_caller_ref_order_is_canonical():
    authority = _authority()
    state = _state("claimed", authority)
    first = _ref(authority, ref_id="ref-b")
    second = _ref(authority, ref_id="ref-a", repo_path="src/coding_graph_boundary.py")
    envelope = _build(
        CodingContextCheckpoint.PRE_SLICE,
        state,
        (first, second),
    )

    assert tuple(item.ref_id for item in envelope.graph_refs) == ("ref-a", "ref-b")
    with pytest.raises((AttributeError, TypeError)):
        envelope.graph_refs += (_ref(authority, ref_id="ref-c"),)

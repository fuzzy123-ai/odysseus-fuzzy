import pytest

from src.coding_agent_memory_bridge import (
    CodingAgentMemoryBridgeError,
    build_coding_agent_capability_memory_write_intent,
    build_coding_agent_memory_checkpoint_receipt,
    build_coding_agent_memory_write_intent,
)
from src.coding_context_envelope import (
    CodingContextCheckpoint,
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
from src.runtime_event_envelope import stable_payload_hash
from src.tool_capability_knowledge import build_coding_agent_capability_knowledge


_INPUT_REVISION = "worktree-rev-11"
_DIGEST = "sha256:" + "d" * 64


def _planning_binding() -> dict[str, object]:
    return {
        "status": "validated",
        "planning_item_id": "ACPR-11",
        "canonical_plan_revision": "plan-rev-11",
        "acceptance_contract": "acceptance-contract-11",
        "allowed_paths": ["src", "tests"],
        "gate_requirements": ["machine_auto", "agent_auto"],
    }


def _authority() -> CodingLifecycleAuthority:
    return CodingLifecycleAuthority.create(
        planning_item_id="ACPR-11",
        planning_revision="plan-rev-11",
        acceptance_criteria_id="acceptance-contract-11",
        allowed_scope=("src", "tests"),
        blocked_scope=(".git",),
        claim_id="claim-acpr11",
        claim_owner="bob",
        claim_scope=("src", "tests"),
        input_revision=_INPUT_REVISION,
        input_diff_digest=_DIGEST,
        acceptance_decision_id="acceptance-decision-11",
        evidence_id="evidence-11",
    )


def _state(target: str):
    state = start_authorized_coding_lifecycle(task_id="task-acpr11", repo_id="odysseus", authority=_authority())
    for item in (
        "planning", "ready_for_claim", "claimed", "context_building", "context_ready",
        "worktree_ready", "acting", "verifying", "review_ready",
    ):
        if target == "clarifying":
            break
        state = transition_authorized_coding_lifecycle(state, target_state=item)
        if item == target:
            break
    return state


def _graph_ref(*, kind: CodingGraphKind = CodingGraphKind.CODE, ref_id: str = "graph-ref-11") -> CodingGraphRef:
    authority = _authority()
    return CodingGraphRef(
        ref_id=ref_id,
        graph_kind=kind,
        retrieval_kind=(CodingRetrievalKind.PLANNING_EXACT if kind is CodingGraphKind.PLANNING else CodingRetrievalKind.EXACT_CODE),
        mandatory=True,
        owner_scope="repo:odysseus",
        planning_item_id=authority.planning_item_id,
        planning_revision=authority.planning_revision,
        claim_id=authority.claim_id,
        claim_owner=authority.claim_owner,
        input_revision=_INPUT_REVISION,
        scope_digest=authority_scope_digest(authority),
        source_revision_ref="source-rev-11",
        content_hash="sha256:" + "a" * 64,
        provenance_refs=("provenance-11",),
        retrieval_snapshot_ref="snapshot-11",
        freshness=CodingGraphFreshness.CURRENT,
        status=CodingGraphStatus.AVAILABLE,
        repo_path="" if kind is CodingGraphKind.PLANNING else "src/coding_agent_memory_bridge.py",
    )


def _envelope(checkpoint: CodingContextCheckpoint, *, parent=None):
    authority = _authority()
    values = {
        "checkpoint": checkpoint,
        "lifecycle": _state(
            "clarifying" if checkpoint is CodingContextCheckpoint.PRE_PLAN
            else "claimed" if checkpoint is CodingContextCheckpoint.PRE_SLICE
            else "verifying" if checkpoint is CodingContextCheckpoint.FAILURE_RETRIEVAL
            else "review_ready"
        ),
        "owner_scope": "repo:odysseus",
        "input_revision": _INPUT_REVISION,
        "objective_ref": "objective-ref-11",
        "objective_digest": _DIGEST,
        "graph_refs": (_graph_ref(kind=CodingGraphKind.PLANNING) if checkpoint is CodingContextCheckpoint.PRE_PLAN else _graph_ref(),),
    }
    if checkpoint is CodingContextCheckpoint.PRE_SLICE:
        values.update(
            acceptance_check_refs=("acceptance-check-11",),
            tool_capability_refs=("tool-capability-11",),
            budget_policy_refs=("budget-policy-11",),
            stop_rule_refs=("stop-rule-11",),
        )
    if checkpoint is CodingContextCheckpoint.FAILURE_RETRIEVAL:
        values.update(
            parent_envelope=parent,
            trigger_evidence_ref="failed-lane-11",
            acceptance_check_refs=("acceptance-check-11",),
            tool_capability_refs=("tool-capability-11",),
            budget_policy_refs=("budget-policy-11",),
            stop_rule_refs=("stop-rule-11",),
        )
    if checkpoint is CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK:
        proof = CodingLifecycleCompletionProof.create(
            acceptance_decision_id=authority.acceptance_decision_id,
            evidence_id=authority.evidence_id,
            reviewer_id="alice-reviewer",
            all_required_gates_closed=True,
            independent_review=True,
        )
        values.update(
            graph_refs=(),
            parent_envelope=parent,
            completion_proof=proof,
            post_acceptance_intents=(
                PostAcceptanceIntentRef(
                    intent_ref="memory-write-intent-11",
                    target_graph=CodingGraphKind.MEMORY,
                    planning_revision=authority.planning_revision,
                    input_revision=_INPUT_REVISION,
                    scope_digest=authority_scope_digest(authority),
                    acceptance_decision_id=proof.acceptance_decision_id,
                    evidence_id=proof.evidence_id,
                    reviewer_id=proof.reviewer_id,
                ),
            ),
        )
    return build_coding_context_envelope(**values)


def test_coding_agent_memory_bridge_builds_candidates_and_raptorgraph_mapping():
    intent = build_coding_agent_memory_write_intent(
        {
            "title": "Sandbox check result",
            "summary": "Focused backend tests passed in a sandbox dry run.",
            "content_hash": "sha256:" + "a" * 64,
            "confidence": 0.9,
            "sensitivity": "project",
            "artifacts": [{"content_hash": "sha256:" + "b" * 64}],
        },
        model="gemma4:e4b",
        operator_auto_write_enabled=False,
    )

    assert intent["policy"]["review_required"] is True
    assert intent["candidates"][0]["author_stamp"]["model"] == "gemma4:e4b"
    assert intent["raptorgraph_mapping"]["nodes"]
    assert intent["raw_content_visible"] is False


def test_coding_agent_memory_bridge_blocks_raw_or_secret_evidence():
    with pytest.raises(CodingAgentMemoryBridgeError):
        build_coding_agent_memory_write_intent(
            {
                "title": "Bad",
                "summary": "Authorization: Bearer abcdefghijk",
                "content_hash": "sha256:" + "a" * 64,
            },
            model="gemma4:e4b",
        )

    with pytest.raises(CodingAgentMemoryBridgeError):
        build_coding_agent_memory_write_intent(
            {
                "title": "Bad",
                "summary": "ok",
                "raw_content_visible": True,
            },
            model="gemma4:e4b",
        )


def test_coding_agent_memory_bridge_accepts_capability_knowledge_packet():
    knowledge = build_coding_agent_capability_knowledge(commit="abc1234")

    intent = build_coding_agent_capability_memory_write_intent(
        knowledge,
        model="gemma4:e4b",
        operator_auto_write_enabled=False,
    )

    assert intent["policy"]["review_required"] is True
    assert intent["candidates"][0]["author_stamp"]["model"] == "gemma4:e4b"
    assert intent["raptorgraph_mapping"]["nodes"]
    assert intent["raw_content_visible"] is False


def test_all_four_checkpoint_receipts_are_revision_bound_and_advisory_only():
    pre_plan = _envelope(CodingContextCheckpoint.PRE_PLAN)
    pre_edit = _envelope(CodingContextCheckpoint.PRE_SLICE)
    failure = _envelope(CodingContextCheckpoint.FAILURE_RETRIEVAL, parent=pre_edit)
    writeback = _envelope(CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK, parent=pre_edit)

    receipts = (
        build_coding_agent_memory_checkpoint_receipt(
            envelope=pre_plan,
            planning_binding=_planning_binding(),
            retrieval_status="unavailable",
            candidate_reference_ids=(),
        ),
        build_coding_agent_memory_checkpoint_receipt(
            envelope=pre_edit,
            planning_binding=_planning_binding(),
            retrieval_status="available",
            candidate_reference_ids=("code-ref-11",),
            unresolved_reference_count=0,
        ),
        build_coding_agent_memory_checkpoint_receipt(
            envelope=failure,
            planning_binding=_planning_binding(),
            retrieval_status="low_confidence",
            candidate_reference_ids=("failure-ref-11",),
            dirty_diff_digest="sha256:" + "b" * 64,
            failed_lane_receipt_ids=("lane-receipt-11",),
        ),
        build_coding_agent_memory_checkpoint_receipt(
            envelope=writeback,
            planning_binding=_planning_binding(),
            retrieval_status="available",
            candidate_reference_ids=("provenance-11",),
            required_machine_auto_receipt_ids=("machine-receipt-11",),
            independent_agent_auto_receipt_id="review-receipt-11",
            applicable_user_acceptance_state="not_applicable",
        ),
    )

    assert [item["checkpoint"] for item in receipts] == [
        "planning_intake", "pre_edit", "failure_retrieval", "post_acceptance_writeback"
    ]
    for receipt in receipts:
        assert receipt["planning"]["planning_item_id"] == "ACPR-11"
        assert receipt["planning"]["canonical_plan_revision"] == "plan-rev-11"
        assert receipt["receipt_id"].startswith("sha256:")
        assert receipt["authority_effect"] == "none"
        assert receipt["gate_effect"] == "none"
        assert receipt["raw_content_visible"] is False
    assert receipts[1]["exact_read_required"] is True
    assert receipts[0]["scope_digest"] == stable_payload_hash(
        {"normalized_allowed_scope": ("src", "tests")}
    )
    assert receipts[1]["scope_digest"] == stable_payload_hash(
        {"normalized_claim_scope": ("src", "tests")}
    )
    assert receipts[2]["active_planning_envelope_digest"] == pre_edit.envelope_id
    assert receipts[3]["memory_write_intent_id"] == "memory-write-intent-11"
    assert receipts[3]["write_intent_ready"] is True


def test_checkpoint_receipt_rejects_stale_or_conflicting_planning_and_cannot_infer_acceptance():
    envelope = _envelope(CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK, parent=_envelope(CodingContextCheckpoint.PRE_SLICE))

    with pytest.raises(CodingAgentMemoryBridgeError, match="does not match validated Planning revision"):
        build_coding_agent_memory_checkpoint_receipt(
            envelope=envelope,
            planning_binding={**_planning_binding(), "canonical_plan_revision": "plan-rev-stale"},
            retrieval_status="available",
        )

    receipt = build_coding_agent_memory_checkpoint_receipt(
        envelope=envelope,
        planning_binding=_planning_binding(),
        retrieval_status="available",
        required_machine_auto_receipt_ids=(),
        independent_agent_auto_receipt_id="",
        applicable_user_acceptance_state="waiting",
    )

    assert receipt["write_intent_ready"] is False
    assert receipt["authority_effect"] == "none"
    assert receipt["gate_effect"] == "none"


def test_checkpoint_receipt_rejects_acceptance_or_scope_mismatch_with_planning():
    envelope = _envelope(CodingContextCheckpoint.PRE_SLICE)

    with pytest.raises(CodingAgentMemoryBridgeError, match="acceptance contract"):
        build_coding_agent_memory_checkpoint_receipt(
            envelope=envelope,
            planning_binding={
                **_planning_binding(),
                "acceptance_contract": "acceptance-contract-foreign",
            },
            retrieval_status="available",
        )

    with pytest.raises(CodingAgentMemoryBridgeError, match="allowed scope"):
        build_coding_agent_memory_checkpoint_receipt(
            envelope=envelope,
            planning_binding={**_planning_binding(), "allowed_paths": ["src"]},
            retrieval_status="available",
        )

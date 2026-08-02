from dataclasses import replace

from src.coding_code_intelligence_contracts import CodeIntelligenceKind, CodeIntelligenceRequest, CodeIntelligenceStatus
from src.coding_code_intelligence_plane import reduce_code_intelligence
from src.coding_context_envelope import CodingContextCheckpoint, build_coding_context_envelope
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


OWNER = "repo:odysseus"
INPUT = "input-rev-1"
OBJECTIVE = "sha256:" + "d" * 64


def _authority():
    return CodingLifecycleAuthority.create(
        planning_item_id="CAO-08F", planning_revision="planning-rev-1",
        acceptance_criteria_id="acceptance-cao08f", allowed_scope=("src", "tests"),
        blocked_scope=("ops",), claim_id="claim-cao08f", claim_owner="bob",
        claim_scope=("src", "tests"), input_revision=INPUT,
        input_diff_digest="sha256:diff-1", acceptance_decision_id="decision-1",
        evidence_id="evidence-1",
    )


def _state(target):
    state = start_authorized_coding_lifecycle(task_id="task-cao08f", repo_id="odysseus", authority=_authority())
    for item in ("planning", "ready_for_claim", "claimed", "context_building", "context_ready", "worktree_ready", "acting", "verifying", "repair_planning"):
        state = transition_authorized_coding_lifecycle(state, target_state=item)
        if item == target:
            return state
    raise AssertionError(target)


def _ref(*, ref_id="code-ref-1", kind=CodingGraphKind.CODE, **overrides):
    authority = _authority()
    values = {
        "ref_id": ref_id,
        "graph_kind": kind,
        "retrieval_kind": CodingRetrievalKind.EXACT_CODE if kind is CodingGraphKind.CODE else CodingRetrievalKind.CAUSAL_EXACT,
        "mandatory": True,
        "owner_scope": OWNER,
        "planning_item_id": authority.planning_item_id,
        "planning_revision": authority.planning_revision,
        "claim_id": authority.claim_id,
        "claim_owner": authority.claim_owner,
        "input_revision": INPUT,
        "scope_digest": authority_scope_digest(authority),
        "source_revision_ref": "source-rev-1",
        "content_hash": "sha256:" + "a" * 64,
        "provenance_refs": ("provenance-1",),
        "retrieval_snapshot_ref": "snapshot-1",
        "freshness": CodingGraphFreshness.CURRENT,
        "status": CodingGraphStatus.AVAILABLE,
        "repo_path": "src/example.py" if kind is CodingGraphKind.CODE else "",
    }
    values.update(overrides)
    return CodingGraphRef(**values)


def _envelope(*, failure=False, refs=None):
    state = _state("verifying" if failure else "claimed")
    refs = refs or (_ref(),)
    values = {
        "checkpoint": CodingContextCheckpoint.FAILURE_RETRIEVAL if failure else CodingContextCheckpoint.PRE_SLICE,
        "lifecycle": state,
        "owner_scope": OWNER,
        "input_revision": INPUT,
        "objective_ref": "objective-cao08f",
        "objective_digest": OBJECTIVE,
        "graph_refs": refs,
        "acceptance_check_refs": ("acceptance-1",),
        "tool_capability_refs": ("tool-1",),
        "budget_policy_refs": ("budget-1",),
        "stop_rule_refs": ("stop-1",),
    }
    if failure:
        parent = build_coding_context_envelope(
            checkpoint=CodingContextCheckpoint.PRE_SLICE,
            lifecycle=_state("claimed"),
            owner_scope=OWNER,
            input_revision=INPUT,
            objective_ref="objective-cao08f",
            objective_digest=OBJECTIVE,
            graph_refs=refs,
            acceptance_check_refs=("acceptance-1",),
            tool_capability_refs=("tool-1",),
            budget_policy_refs=("budget-1",),
            stop_rule_refs=("stop-1",),
        )
        values.update(parent_envelope=parent, trigger_evidence_ref="verification-failure-1")
    return build_coding_context_envelope(**values)


def _request(envelope, *, kind=CodeIntelligenceKind.SYMBOL, ids=("code-ref-1",), **overrides):
    values = {
        "request_ref": "request-cao08f-1", "envelope_id": envelope.envelope_id,
        "planning_item_id": envelope.planning_item_id, "planning_revision": envelope.planning_revision,
        "claim_id": envelope.claim_id, "claim_owner": envelope.claim_owner,
        "scope_digest": envelope.scope_digest, "input_revision": envelope.input_revision,
        "owner_scope": envelope.owner_scope, "lifecycle_state": envelope.lifecycle_state,
        "checkpoint": envelope.checkpoint, "kind": kind, "graph_ref_ids": ids,
        "max_results": len(ids), "trigger_evidence_ref": envelope.trigger_evidence_ref,
    }
    values.update(overrides)
    return CodeIntelligenceRequest(**values)


def test_plane_returns_deterministic_advisory_evidence_and_exact_reads():
    envelope = _envelope()
    request = _request(envelope)

    first = reduce_code_intelligence(envelope, request=request)
    second = reduce_code_intelligence(envelope, request=request)

    assert first == second
    assert first.status is CodeIntelligenceStatus.ACCEPTED
    assert first.exact_read_required == ("code-ref-1",)
    assert first.evidence[0].repo_path == "src/example.py"
    assert first.to_dict()["edit_allowed"] is False


def test_plane_fails_closed_for_authority_stale_foreign_and_scope_escape_refs():
    envelope = _envelope()
    request = _request(envelope)

    assert reduce_code_intelligence(envelope, request=replace(request, input_revision="other-input")).rejection_code == "envelope_authority_mismatch"
    stale = _envelope(refs=(_ref(freshness=CodingGraphFreshness.STALE),))
    assert reduce_code_intelligence(stale, request=_request(stale)).status is CodeIntelligenceStatus.REJECTED
    foreign = _envelope(refs=(_ref(planning_revision="foreign-rev"),))
    assert reduce_code_intelligence(foreign, request=_request(foreign)).status is CodeIntelligenceStatus.REJECTED
    escaped = _envelope(refs=(_ref(repo_path="ops/escaped.py"),))
    assert reduce_code_intelligence(escaped, request=_request(escaped)).status is CodeIntelligenceStatus.REJECTED


def test_failure_retrieval_is_checkpoint_state_and_trigger_bound():
    envelope = _envelope(failure=True, refs=(_ref(), _ref(ref_id="causal-ref-1", kind=CodingGraphKind.CAUSAL)))
    request = _request(
        envelope,
        kind=CodeIntelligenceKind.FAILURE_RETRIEVAL,
        ids=("causal-ref-1", "code-ref-1"),
    )

    result = reduce_code_intelligence(envelope, request=request)

    assert result.status is CodeIntelligenceStatus.ACCEPTED
    assert tuple(item.graph_ref_id for item in result.evidence) == ("causal-ref-1", "code-ref-1")
    assert reduce_code_intelligence(envelope, request=replace(request, trigger_evidence_ref="other-failure")).rejection_code == "failure_trigger_mismatch"

from dataclasses import replace
import inspect

import pytest

import src.coding_subagent_context_broker as broker_module
from src.coding_context_envelope import (
    CodingContextCheckpoint,
    CodingContextDisposition,
    build_coding_context_envelope,
)
from src.coding_graph_boundary import (
    CodingGraphConflict,
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
from src.coding_subagent_capsule import (
    CodingSubagentLifecycleDescriptor,
    CodingSubagentRole,
)
from src.coding_subagent_context_broker import (
    CodingSubagentContextBrokerError,
    RolePolicy,
    build_role_scoped_subagent_capsules,
)


OWNER = "repo:odysseus"
INPUT_REVISION = "worktree-rev-11"
OBJECTIVE_DIGEST = "sha256:" + "d" * 64
ACCEPTANCE_REFS = ("acceptance-check-review", "acceptance-check-tests")
TOOL_REFS = ("tool-local-edit", "tool-local-test")
BUDGET_REFS = ("budget-bounded-repair",)
STOP_REFS = ("stop-scope-escape",)


def _authority(**overrides):
    values = {
        "planning_item_id": "CAO-08B1",
        "planning_revision": "planning-rev-20",
        "acceptance_criteria_id": "acceptance-contract-cao08b1",
        "allowed_scope": ("src", "tests"),
        "blocked_scope": ("ops", ".git"),
        "claim_id": "claim-cao08b1-bob",
        "claim_owner": "bob",
        "claim_scope": ("src", "tests"),
        "input_revision": INPUT_REVISION,
        "input_diff_digest": "sha256:diff11",
        "acceptance_decision_id": "acceptance-decision-11",
        "evidence_id": "evidence-cao08b1-11",
    }
    values.update(overrides)
    return CodingLifecycleAuthority.create(**values)


def _state(target, authority):
    state = start_authorized_coding_lifecycle(
        task_id="task-cao08b1", repo_id="odysseus", authority=authority
    )
    if target == "clarifying":
        return state
    for item in ("planning", "ready_for_claim", "claimed"):
        state = transition_authorized_coding_lifecycle(state, target_state=item)
        if item == target:
            return state
    raise AssertionError(f"unsupported state {target}")


def _ref(authority, ref_id, graph_kind, snapshot, provenance, **overrides):
    values = {
        "ref_id": ref_id,
        "graph_kind": graph_kind,
        "retrieval_kind": (
            CodingRetrievalKind.EXACT_CODE
            if graph_kind is CodingGraphKind.CODE
            else CodingRetrievalKind.CAUSAL_EXACT
        ),
        "mandatory": True,
        "owner_scope": OWNER,
        "planning_item_id": authority.planning_item_id,
        "planning_revision": authority.planning_revision,
        "claim_id": authority.claim_id,
        "claim_owner": authority.claim_owner,
        "input_revision": INPUT_REVISION,
        "scope_digest": authority_scope_digest(authority),
        "source_revision_ref": "source-version-11",
        "content_hash": "sha256:" + "a" * 64,
        "provenance_refs": (provenance,),
        "retrieval_snapshot_ref": snapshot,
        "freshness": CodingGraphFreshness.CURRENT,
        "status": CodingGraphStatus.AVAILABLE,
        "repo_path": (
            f"src/{ref_id.replace('-', '_')}.py"
            if graph_kind is CodingGraphKind.CODE
            else ""
        ),
    }
    values.update(overrides)
    return CodingGraphRef(**values)


def _graph_refs(authority):
    return (
        _ref(
            authority,
            "implementer-code-ref",
            CodingGraphKind.CODE,
            "snapshot-implementer-11",
            "provenance-implementer-11",
        ),
        _ref(
            authority,
            "reviewer-code-ref",
            CodingGraphKind.CODE,
            "snapshot-reviewer-11",
            "provenance-reviewer-11",
        ),
        _ref(
            authority,
            "tester-causal-ref",
            CodingGraphKind.CAUSAL,
            "snapshot-tester-11",
            "provenance-tester-11",
        ),
    )


def _parent(*, refs=None, checkpoint=CodingContextCheckpoint.PRE_SLICE):
    authority = _authority()
    selected_refs = _graph_refs(authority) if refs is None else refs
    state = _state(
        "claimed" if checkpoint is CodingContextCheckpoint.PRE_SLICE else "clarifying",
        authority,
    )
    return build_coding_context_envelope(
        checkpoint=checkpoint,
        lifecycle=state,
        owner_scope=OWNER,
        input_revision=INPUT_REVISION,
        objective_ref="objective-cao08b1",
        objective_digest=OBJECTIVE_DIGEST,
        graph_refs=selected_refs,
        acceptance_check_refs=ACCEPTANCE_REFS,
        tool_capability_refs=TOOL_REFS,
        budget_policy_refs=BUDGET_REFS,
        stop_rule_refs=STOP_REFS,
    )


def _policy(role, **overrides):
    values = {
        "role": role,
        "actor_id": {
            CodingSubagentRole.IMPLEMENTER: "bob",
            CodingSubagentRole.TESTER: "tester-actor",
            CodingSubagentRole.REVIEWER: "reviewer-actor",
        }[role],
        "graph_ref_ids": {
            CodingSubagentRole.IMPLEMENTER: ("implementer-code-ref",),
            CodingSubagentRole.TESTER: ("tester-causal-ref",),
            CodingSubagentRole.REVIEWER: ("reviewer-code-ref",),
        }[role],
        "acceptance_check_refs": (
            ()
            if role is CodingSubagentRole.IMPLEMENTER
            else (
                "acceptance-check-tests",
            )
            if role is CodingSubagentRole.TESTER
            else ("acceptance-check-review",)
        ),
        "tool_capability_refs": {
            CodingSubagentRole.IMPLEMENTER: ("tool-local-edit",),
            CodingSubagentRole.TESTER: ("tool-local-test",),
            CodingSubagentRole.REVIEWER: (),
        }[role],
        "budget_policy_refs": BUDGET_REFS,
        "stop_rule_refs": STOP_REFS,
        "exact_read_refs": (
            ()
            if role is CodingSubagentRole.TESTER
            else (f"{role.value}-code-ref",)
        ),
        "retrieval_identity_ref": f"retrieval-{role.value}-11",
        "cancellation_descriptor_ref": "cancel-policy-11",
        "expiry_descriptor_ref": "expiry-policy-11",
        "resume_descriptor_ref": "resume-policy-11",
        "token_budget": 4_000,
        "context_ref_budget": 8,
        "time_budget_seconds": 600,
        "repair_budget": 1 if role is CodingSubagentRole.IMPLEMENTER else 0,
        "independent_reviewer_ref": (
            "independent-review-11"
            if role is CodingSubagentRole.REVIEWER
            else ""
        ),
    }
    values.update(overrides)
    return RolePolicy(**values)


def _policies():
    return tuple(_policy(role) for role in (
        CodingSubagentRole.IMPLEMENTER,
        CodingSubagentRole.TESTER,
        CodingSubagentRole.REVIEWER,
    ))


def _build(parent=None, policies=None, **overrides):
    values = {
        "parent_envelope": parent or _parent(),
        "implementer_actor_id": "bob",
        "role_policies": policies or _policies(),
    }
    values.update(overrides)
    return build_role_scoped_subagent_capsules(**values)


def test_broker_builds_deterministic_parent_bound_role_isolated_capsules():
    parent = _parent()
    capsules = _build(parent)
    repeated = _build(parent)

    assert capsules == repeated
    assert tuple(item.role for item in capsules) == (
        CodingSubagentRole.IMPLEMENTER,
        CodingSubagentRole.TESTER,
        CodingSubagentRole.REVIEWER,
    )
    implementer, tester, reviewer = capsules
    assert implementer.graph_ref_ids == ("implementer-code-ref",)
    assert implementer.tool_capability_refs == ("tool-local-edit",)
    assert tester.graph_ref_ids == ("tester-causal-ref",)
    assert tester.acceptance_check_refs == ("acceptance-check-tests",)
    assert reviewer.graph_ref_ids == ("reviewer-code-ref",)
    assert reviewer.acceptance_check_refs == ("acceptance-check-review",)
    assert reviewer.retrieval_identity_ref != implementer.retrieval_identity_ref
    assert not set(reviewer.retrieval_snapshot_refs) & set(
        implementer.retrieval_snapshot_refs
    )
    for capsule in capsules:
        assert capsule.parent_envelope_id == parent.envelope_id
        assert capsule.parent_run_id == parent.claim_id
        assert capsule.parent_slice_id == parent.planning_item_id
        assert capsule.planning_item_id == parent.planning_item_id
        assert capsule.planning_revision == parent.planning_revision
        assert capsule.claim_id == parent.claim_id
        assert capsule.claim_owner == parent.claim_owner
        assert capsule.scope_digest == parent.scope_digest
        assert capsule.input_revision == parent.input_revision
        assert capsule.authority_effect == "none"
        assert capsule.side_effects == ("none",)


@pytest.mark.parametrize(
    "field_name,foreign_value",
    (
        ("graph_ref_ids", ("foreign-graph-ref",)),
        ("acceptance_check_refs", ("acceptance-foreign",)),
        ("tool_capability_refs", ("tool-foreign",)),
        ("budget_policy_refs", ("budget-foreign",)),
        ("stop_rule_refs", ("stop-foreign",)),
        ("exact_read_refs", ("foreign-exact-ref",)),
    ),
)
def test_role_policy_cannot_widen_parent_context(field_name, foreign_value):
    policies = list(_policies())
    policies[0] = replace(policies[0], **{field_name: foreign_value})
    with pytest.raises(CodingSubagentContextBrokerError, match="widens parent"):
        _build(policies=tuple(policies))


def test_broker_requires_ready_pre_slice_parent_and_rejects_degraded_context():
    pre_plan = _parent(checkpoint=CodingContextCheckpoint.PRE_PLAN)
    assert pre_plan.disposition is CodingContextDisposition.READY
    with pytest.raises(CodingSubagentContextBrokerError, match="READY PRE_SLICE"):
        _build(pre_plan)

    authority = _authority()
    stale = _ref(
        authority,
        "stale-code-ref",
        CodingGraphKind.CODE,
        "snapshot-stale-11",
        "provenance-stale-11",
        freshness=CodingGraphFreshness.STALE,
    )
    waiting = _parent(refs=(stale,))
    assert waiting.disposition is CodingContextDisposition.WAITING
    with pytest.raises(CodingSubagentContextBrokerError, match="READY PRE_SLICE"):
        _build(waiting)

    conflicted = _ref(
        authority,
        "conflicted-code-ref",
        CodingGraphKind.CODE,
        "snapshot-conflicted-11",
        "provenance-conflicted-11",
        conflict=CodingGraphConflict.CONFLICTED,
        conflict_refs=("conflict-source-1",),
    )
    blocked = _parent(refs=(conflicted,))
    assert blocked.disposition is CodingContextDisposition.BLOCKED
    with pytest.raises(CodingSubagentContextBrokerError, match="READY PRE_SLICE"):
        _build(blocked)


def test_reviewer_actor_retrieval_identity_and_snapshot_copy_are_rejected():
    policies = list(_policies())
    policies[2] = replace(policies[2], actor_id="bob")
    with pytest.raises(CodingSubagentContextBrokerError, match="reviewer actor"):
        _build(policies=tuple(policies))

    policies = list(_policies())
    policies[2] = replace(
        policies[2], retrieval_identity_ref="retrieval-implementer-11"
    )
    with pytest.raises(CodingSubagentContextBrokerError, match="retrieval identity"):
        _build(policies=tuple(policies))

    policies = list(_policies())
    policies[2] = replace(
        policies[2],
        graph_ref_ids=("implementer-code-ref",),
        exact_read_refs=("implementer-code-ref",),
    )
    with pytest.raises(CodingSubagentContextBrokerError, match="snapshots"):
        _build(policies=tuple(policies))


def test_policy_budget_role_order_lifecycle_and_raw_identifiers_fail_closed():
    with pytest.raises(CodingSubagentContextBrokerError, match="bounded range"):
        _policy(CodingSubagentRole.IMPLEMENTER, token_budget=True)
    with pytest.raises(CodingSubagentContextBrokerError, match="capsule_ready"):
        _policy(
            CodingSubagentRole.IMPLEMENTER,
            lifecycle_descriptor=CodingSubagentLifecycleDescriptor.CANCELLED,
        )
    with pytest.raises(CodingSubagentContextBrokerError, match="ordered"):
        _build(policies=tuple(reversed(_policies())))
    with pytest.raises(CodingSubagentContextBrokerError, match="actor_id"):
        _policy(CodingSubagentRole.TESTER, actor_id=r"C:\Users\private\tester")


def test_broker_module_has_no_runtime_or_effectful_dependency():
    source = inspect.getsource(broker_module)
    assert "subagent_runtime" not in source
    assert "dispatch(" not in source
    assert "graph_write_allowed=True" not in source

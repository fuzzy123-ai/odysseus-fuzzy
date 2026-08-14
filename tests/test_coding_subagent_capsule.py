from dataclasses import replace
import json

import pytest

from src.coding_subagent_capsule import (
    MAX_CAPSULE_REFS,
    MAX_REPAIR_BUDGET,
    MAX_TOKEN_BUDGET,
    CodingSubagentCapsuleError,
    CodingSubagentLifecycleDescriptor,
    CodingSubagentRole,
    create_coding_subagent_capsule,
)


SHA_SCOPE = "sha256:" + "a" * 64
SHA_GRAPH = "sha256:" + "b" * 64


def _facts(role=CodingSubagentRole.IMPLEMENTER, **overrides):
    values = {
        "role": role,
        "lifecycle_descriptor": CodingSubagentLifecycleDescriptor.CAPSULE_READY,
        "parent_envelope_id": "sha256:" + "c" * 64,
        "parent_run_id": "claim-cao08b1-bob",
        "parent_slice_id": "CAO-08B1",
        "planning_item_id": "CAO-08B1",
        "planning_revision": "planning-rev-20",
        "claim_id": "claim-cao08b1-bob",
        "claim_owner": "bob",
        "scope_digest": SHA_SCOPE,
        "input_revision": "worktree-rev-11",
        "actor_id": "bob",
        "implementer_actor_id": "bob",
        "retrieval_identity_ref": "retrieval-implementer-1",
        "implementer_retrieval_identity_ref": "retrieval-implementer-1",
        "independent_reviewer_ref": "",
        "graph_semantic_digest": SHA_GRAPH,
        "graph_ref_ids": ("code-ref-implementer",),
        "provenance_refs": ("provenance-code-1",),
        "retrieval_snapshot_refs": ("snapshot-implementer-1",),
        "implementer_retrieval_snapshot_refs": ("snapshot-implementer-1",),
        "acceptance_check_refs": (),
        "tool_capability_refs": ("tool-local-edit",),
        "budget_policy_refs": ("budget-bounded-repair",),
        "stop_rule_refs": ("stop-scope-escape",),
        "exact_read_refs": ("code-ref-implementer",),
        "cancellation_descriptor_ref": "cancel-policy-1",
        "expiry_descriptor_ref": "expiry-policy-1",
        "resume_descriptor_ref": "resume-policy-1",
        "token_budget": 4_000,
        "context_ref_budget": 8,
        "time_budget_seconds": 600,
        "repair_budget": 1,
    }
    if role is CodingSubagentRole.TESTER:
        values.update(
            actor_id="tester-actor",
            retrieval_identity_ref="retrieval-tester-1",
            graph_ref_ids=("causal-ref-tester",),
            provenance_refs=("provenance-causal-1",),
            retrieval_snapshot_refs=("snapshot-tester-1",),
            acceptance_check_refs=("acceptance-check-tests",),
            tool_capability_refs=("tool-local-test",),
            exact_read_refs=(),
            repair_budget=0,
        )
    elif role is CodingSubagentRole.REVIEWER:
        values.update(
            actor_id="reviewer-actor",
            retrieval_identity_ref="retrieval-reviewer-1",
            independent_reviewer_ref="independent-review-1",
            graph_ref_ids=("code-ref-reviewer",),
            provenance_refs=("provenance-review-1",),
            retrieval_snapshot_refs=("snapshot-reviewer-1",),
            acceptance_check_refs=("acceptance-check-review",),
            tool_capability_refs=(),
            exact_read_refs=("code-ref-reviewer",),
            repair_budget=0,
        )
    values.update(overrides)
    return values


def test_capsule_is_deterministic_content_free_immutable_and_zero_authority():
    first = create_coding_subagent_capsule(**_facts())
    second = create_coding_subagent_capsule(**_facts())
    payload = first.to_dict()

    assert first == second
    assert first.capsule_id == second.capsule_id
    for field_name in (
        "execution_allowed", "edit_allowed", "write_allowed", "dispatch_allowed",
        "recursive_delegation_allowed", "gate_close_allowed",
        "graph_write_allowed", "memory_write_allowed", "network_allowed",
        "secrets_allowed", "live_effect_allowed",
    ):
        assert payload[field_name] is False
    assert payload["authority_effect"] == "none"
    assert payload["side_effects"] == ("none",)
    dumped = json.dumps(payload, default=str)
    assert "raw source" not in dumped
    with pytest.raises((AttributeError, TypeError)):
        first.actor_id = "other"


def test_direct_constructor_rejects_forged_id_and_semantic_replacement():
    capsule = create_coding_subagent_capsule(**_facts())
    with pytest.raises(CodingSubagentCapsuleError, match="canonical capsule facts"):
        replace(capsule, capsule_id="sha256:" + "0" * 64)
    with pytest.raises(CodingSubagentCapsuleError, match="canonical capsule facts"):
        replace(capsule, planning_revision="planning-rev-21")
    with pytest.raises(CodingSubagentCapsuleError, match="canonical capsule facts"):
        replace(capsule, cancellation_descriptor_ref="cancel-policy-2")


def test_foreign_safe_parent_run_and_slice_aliases_are_rejected():
    with pytest.raises(CodingSubagentCapsuleError, match="authoritative claim_id"):
        create_coding_subagent_capsule(
            **_facts(parent_run_id="foreign-safe-run")
        )
    with pytest.raises(
        CodingSubagentCapsuleError, match="authoritative planning_item_id"
    ):
        create_coding_subagent_capsule(
            **_facts(parent_slice_id="foreign-safe-slice")
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "execution_allowed", "edit_allowed", "write_allowed", "dispatch_allowed",
        "recursive_delegation_allowed", "gate_close_allowed",
        "graph_write_allowed", "memory_write_allowed", "network_allowed",
        "secrets_allowed", "live_effect_allowed",
    ),
)
def test_every_authority_flag_is_fail_closed(field_name):
    values = _facts()
    values[field_name] = True
    with pytest.raises(CodingSubagentCapsuleError, match="must remain false"):
        create_coding_subagent_capsule(**values)


@pytest.mark.parametrize(
    "field_name,value",
    (
        ("token_budget", True),
        ("token_budget", 0),
        ("token_budget", MAX_TOKEN_BUDGET + 1),
        ("context_ref_budget", 0),
        ("time_budget_seconds", -1),
        ("repair_budget", MAX_REPAIR_BUDGET + 1),
    ),
)
def test_invalid_budgets_including_bool_are_rejected(field_name, value):
    with pytest.raises(CodingSubagentCapsuleError, match="bounded range"):
        create_coding_subagent_capsule(**_facts(**{field_name: value}))


def test_context_budget_and_reference_collections_are_fail_closed():
    with pytest.raises(CodingSubagentCapsuleError, match="exceeds"):
        create_coding_subagent_capsule(**_facts(context_ref_budget=1))
    with pytest.raises(CodingSubagentCapsuleError, match="canonical bounded"):
        create_coding_subagent_capsule(
            **_facts(graph_ref_ids=("ref-b", "ref-a"), exact_read_refs=("ref-a",))
        )
    too_many = tuple(f"ref-{index:03d}" for index in range(MAX_CAPSULE_REFS + 1))
    with pytest.raises(CodingSubagentCapsuleError, match="canonical bounded"):
        create_coding_subagent_capsule(**_facts(provenance_refs=too_many))


@pytest.mark.parametrize(
    "overrides",
    (
        {"actor_id": "bob"},
        {"actor_id": "reviewer-actor", "claim_owner": "reviewer-actor"},
        {"retrieval_identity_ref": "retrieval-implementer-1"},
        {"retrieval_snapshot_refs": ("snapshot-implementer-1",)},
        {"independent_reviewer_ref": ""},
    ),
)
def test_reviewer_identity_and_retrieval_are_independent(overrides):
    with pytest.raises(CodingSubagentCapsuleError, match="independent"):
        create_coding_subagent_capsule(
            **_facts(CodingSubagentRole.REVIEWER, **overrides)
        )


def test_role_specific_requirements_reject_self_approval_and_repair_widening():
    with pytest.raises(CodingSubagentCapsuleError, match="repair budget"):
        create_coding_subagent_capsule(**_facts(repair_budget=0))
    with pytest.raises(CodingSubagentCapsuleError, match="cannot carry repair"):
        create_coding_subagent_capsule(
            **_facts(CodingSubagentRole.TESTER, repair_budget=1)
        )
    with pytest.raises(CodingSubagentCapsuleError, match="acceptance checks"):
        create_coding_subagent_capsule(
            **_facts(CodingSubagentRole.TESTER, acceptance_check_refs=())
        )
    with pytest.raises(CodingSubagentCapsuleError, match="identity and retrieval"):
        create_coding_subagent_capsule(
            **_facts(implementer_retrieval_identity_ref="retrieval-other")
        )
    with pytest.raises(CodingSubagentCapsuleError, match="graph provenance"):
        create_coding_subagent_capsule(
            **_facts(
                CodingSubagentRole.REVIEWER,
                implementer_retrieval_snapshot_refs=(),
            )
        )


def test_raw_private_and_secret_like_identifiers_are_rejected():
    for actor_id in (
        r"C:\Users\private\actor",
        "raw actor prose",
        "token=abc123",
    ):
        with pytest.raises(CodingSubagentCapsuleError, match="actor_id"):
            create_coding_subagent_capsule(**_facts(actor_id=actor_id))

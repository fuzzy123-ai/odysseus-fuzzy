from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
import hashlib
import importlib
import json
import struct

import pytest

from src.planning_definition_contract import compute_roadmap_content_hash


POLICY_DIGEST = "sha256:33966f569d12022127d066bda993322a5bc880230a88b3a4825bbf55ff05cc07"
_ZERO_DIGEST = "sha256:" + ("0" * 64)
_FACT_PRIMARY = {
    "completions": "slice_id",
    "active_claims": "claim_id",
    "gate_states": "gate_id",
    "clarifications": "clarification_id",
    "verification_receipts": "receipt_id",
    "owner_decisions": "decision_id",
}


def _api():
    return importlib.import_module("src.harbor_planning_projection")


def _canonical_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(domain: str, value) -> str:
    payload = _canonical_bytes(value)
    framed = domain.encode("utf-8") + b"\0" + struct.pack(">Q", len(payload)) + payload
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _node(node_id: str, *, depends_on: tuple[str, ...] = (), gate_ids: tuple[str, ...] = ()) -> dict:
    return {
        "node_id": node_id,
        "kind": "work",
        "title": f"Node {node_id}",
        "objective": f"Complete node {node_id} deterministically.",
        "depends_on": depends_on,
        "gate_ids": gate_ids,
        "deliverables": (f"Deliverable {node_id}",),
        "allowed_paths": (f"src/{node_id}.py",),
        "blocked_paths": (),
        "capability_requirements": ("Repository read",),
        "verification_rule_ids": ("rule-static",),
    }


def _gate(gate_id: str, *, kind: str, blocks: tuple[str, ...]) -> dict:
    return {
        "gate_id": gate_id,
        "kind": kind,
        "title": f"Gate {gate_id}",
        "blocks": blocks,
        "decision_needed": "Resolve the bounded gate.",
        "safe_default": "Keep the gate closed.",
        "approval_scope_schema": {"type": "object", "additionalProperties": False},
        "required_verification_rule_ids": ("rule-static",),
    }


def _planning(
    *,
    nodes: tuple[dict, ...] | None = None,
    gates: tuple[dict, ...] = (),
    edges: tuple[dict, ...] | None = None,
) -> dict:
    if nodes is None:
        nodes = (_node("a"), _node("b", depends_on=("a",)), _node("c", depends_on=("b",)))
    if edges is None:
        edges = tuple(
            {"from": node["node_id"], "to": dependency, "kind": "depends_on"}
            for node in nodes
            for dependency in node["depends_on"]
        )
    roadmap = _planning_lists({
        "roadmap_id": "roadmap-a",
        "project_id": "project-a",
        "revision": 4,
        "content_hash": _ZERO_DIGEST,
        "revision_state": "approved",
        "title": "Canonical Harbor roadmap",
        "objective": "Project one closed execution authority snapshot.",
        "assumptions": ("The Planning definition is validated.",),
        "constraints": ("Projection has no authority effect.",),
        "nodes": nodes,
        "edges": edges,
        "gates": gates,
        "done_contract": {
            "required_node_ids": tuple(node["node_id"] for node in nodes),
            "required_gate_ids": tuple(gate["gate_id"] for gate in gates),
            "verification_rules": (
                {
                    "rule_id": "rule-static",
                    "kind": "static",
                    "description": "The canonical definition validates.",
                },
            ),
            "completion_rule": "all_required_nodes_and_gates",
        },
        "source_refs": ("docs/plans/harbor-roadmap.json",),
        "created_at": "2026-08-15T00:00:00+02:00",
        "updated_at": "2026-08-15T00:00:00+02:00",
    })
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    return {
        "schema_id": "odysseus.planning.definition.v2",
        "project": {
            "project_id": "project-a",
            "title": "Harbor project",
            "objective": "PRIVATE OBJECTIVE THAT MUST NEVER ENTER THE PROJECTION",
            "scope": {"in": ["Planning"], "out": ["Execution mutation"]},
            "constraints": ["Read only"],
            "roadmap_refs": ["roadmap-a"],
            "latest_approved_revision": {
                "roadmap-a": {"revision": 4, "content_hash": roadmap["content_hash"]}
            },
            "draft_refs": [],
        },
        "roadmaps": [roadmap],
    }


def _planning_lists(value):
    if type(value) is tuple:
        return [_planning_lists(item) for item in value]
    if type(value) is dict:
        return {key: _planning_lists(item) for key, item in value.items()}
    return value


def _rehash(planning: dict) -> None:
    roadmap = planning["roadmaps"][0]
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    planning["project"]["latest_approved_revision"]["roadmap-a"] = {
        "revision": roadmap["revision"],
        "content_hash": roadmap["content_hash"],
    }


def _definition_payload(planning: dict) -> dict:
    roadmap = planning["roadmaps"][0]
    nodes = roadmap["nodes"]
    gates = roadmap["gates"]
    dependencies = {
        (node["node_id"], dependency)
        for node in nodes
        for dependency in node["depends_on"]
    }
    dependencies.update(
        (edge["from"], edge["to"])
        for edge in roadmap["edges"]
        if edge["kind"] == "depends_on"
    )
    node_gate_pairs = tuple(
        sorted(
            (node["node_id"], gate_id)
            for node in nodes
            for gate_id in node["gate_ids"]
        )
    )
    gate_block_pairs = tuple(
        sorted(
            (node_id, gate["gate_id"])
            for gate in gates
            for node_id in gate["blocks"]
        )
    )
    return {
        "schema": "odysseus.harbor.definition_authority_snapshot.v1",
        "project_id": planning["project"]["project_id"],
        "roadmap_id": roadmap["roadmap_id"],
        "roadmap_revision": roadmap["revision"],
        "roadmap_content_hash": roadmap["content_hash"],
        "node_ids": tuple(sorted(node["node_id"] for node in nodes)),
        "dependency_pairs": tuple(sorted(dependencies)),
        "node_gate_pairs": node_gate_pairs,
        "gate_ids": tuple(sorted(gate["gate_id"] for gate in gates)),
        "gate_block_pairs": gate_block_pairs,
    }


def _record(kind: str, **values) -> tuple[str, dict]:
    return kind, values


def _snapshot(
    planning: dict,
    *records: tuple[str, dict],
    execution_revision: int = 7,
) -> tuple[dict, str, str]:
    grouped = {kind: [] for kind in _FACT_PRIMARY}
    for index, (kind, raw) in enumerate(records):
        item = deepcopy(raw)
        item.setdefault("execution_revision", execution_revision)
        item.setdefault(
            "evidence_digest",
            _digest(
                "odysseus.test.harbor.evidence.v1",
                {"kind": kind, "index": index, "identity": item.get(_FACT_PRIMARY[kind])},
            ),
        )
        item.setdefault("policy_digest", POLICY_DIGEST)
        grouped[kind].append(item)

    evidence_digests = tuple(
        sorted({item["evidence_digest"] for values in grouped.values() for item in values})
    )
    evidence_set_digest = _digest(
        "odysseus.harbor.execution_evidence_set.v1",
        {
            "schema": "odysseus.harbor.execution_evidence_set.v1",
            "evidence_digests": evidence_digests,
        },
    )
    for values in grouped.values():
        for item in values:
            item.setdefault("evidence_set_digest", evidence_set_digest)
        values.sort(key=lambda item, key=_FACT_PRIMARY[next(k for k, v in grouped.items() if v is values)]: item[key])

    facts = {kind: tuple(values) for kind, values in grouped.items()}
    definition_digest = _digest(
        "odysseus.harbor.definition_authority_snapshot.v1",
        _definition_payload(planning),
    )
    overlay_digest = _overlay_digest(execution_revision, facts)
    roadmap = planning["roadmaps"][0]
    composite_payload = {
        "schema": "odysseus.harbor.execution_authority_snapshot.v1",
        "project_id": planning["project"]["project_id"],
        "roadmap_id": roadmap["roadmap_id"],
        "roadmap_revision": roadmap["revision"],
        "roadmap_content_hash": roadmap["content_hash"],
        "definition_snapshot_digest": definition_digest,
        "execution_revision": execution_revision,
        "overlay_digest": overlay_digest,
        "evidence_set_digest": evidence_set_digest,
        "policy_digest": POLICY_DIGEST,
    }
    composite_digest = _digest(
        "odysseus.harbor.execution_authority_snapshot.v1", composite_payload
    )
    snapshot = {
        **composite_payload,
        "facts": facts,
        "composite_snapshot_digest": composite_digest,
    }
    return snapshot, roadmap["content_hash"], composite_digest


def _overlay_digest(execution_revision: int, facts: dict) -> str:
    return _digest(
        "odysseus.harbor.execution_overlay.v1",
        {
            "schema": "odysseus.harbor.execution_overlay.v1",
            "execution_revision": execution_revision,
            **facts,
        },
    )


def _reseal_overlay(snapshot: dict) -> str:
    snapshot["overlay_digest"] = _overlay_digest(
        snapshot["execution_revision"], snapshot["facts"]
    )
    composite_payload = {
        key: snapshot[key]
        for key in (
            "schema",
            "project_id",
            "roadmap_id",
            "roadmap_revision",
            "roadmap_content_hash",
            "definition_snapshot_digest",
            "execution_revision",
            "overlay_digest",
            "evidence_set_digest",
            "policy_digest",
        )
    }
    snapshot["composite_snapshot_digest"] = _digest(
        "odysseus.harbor.execution_authority_snapshot.v1", composite_payload
    )
    return snapshot["composite_snapshot_digest"]


def _build(module, planning: dict, snapshot: dict, *, advisory_refs: tuple = ()):
    return module.build_harbor_planning_projection(
        planning_document=planning,
        execution_snapshot=snapshot,
        expected_roadmap_content_hash=planning["roadmaps"][0]["content_hash"],
        expected_composite_snapshot_digest=snapshot["composite_snapshot_digest"],
        advisory_refs=advisory_refs,
    )


def _assert_error(module, code: str, planning: dict, snapshot: dict, **overrides):
    call = {
        "planning_document": planning,
        "execution_snapshot": snapshot,
        "expected_roadmap_content_hash": planning["roadmaps"][0]["content_hash"],
        "expected_composite_snapshot_digest": snapshot["composite_snapshot_digest"],
        "advisory_refs": (),
    }
    call.update(overrides)
    with pytest.raises(module.HarborPlanningProjectionError) as raised:
        module.build_harbor_planning_projection(**call)
    error = raised.value
    assert error.code == code
    assert error.args == (code,)
    assert str(error) == code
    assert error.__cause__ is None
    assert error.__context__ is None
    return error


def _pairs(values) -> tuple[tuple[str, str], ...]:
    return tuple((value.kind, value.ref_id) for value in values)


def test_h1_29_public_api_and_exact_immutable_shapes_are_closed() -> None:
    module = _api()
    planning = _planning()
    snapshot, _, _ = _snapshot(planning)

    result = _build(module, planning, snapshot)

    assert module.__all__ == (
        "HarborPlanningProjectionError",
        "HarborPlanningProjection",
        "build_harbor_planning_projection",
    )
    assert tuple(field.name for field in fields(result)) == (
        "schema",
        "composite_snapshot_digest",
        "execution_revision",
        "goal_ref",
        "frontier",
        "additional_ready_count",
        "owner_question",
        "claim_refs",
        "gate_refs",
        "clarification_refs",
        "receipt_refs",
        "decision_refs",
        "not_verified_refs",
        "authority",
    )
    assert result.schema == "odysseus.harbor.canonical_planning_projection.v2"
    assert (result.goal_ref.kind, result.goal_ref.ref_id) == ("roadmap", "roadmap-a")
    assert (result.frontier.kind, result.frontier.ref_id) == ("slice", "a")
    assert result.additional_ready_count == 0
    assert result.owner_question is None
    assert all(type(value) is tuple for value in (
        result.claim_refs,
        result.gate_refs,
        result.clarification_refs,
        result.receipt_refs,
        result.decision_refs,
        result.not_verified_refs,
    ))
    assert tuple(field.name for field in fields(result.authority)) == (
        "canonical_truth_effect",
        "claim_effect",
        "decision_effect",
        "dispatch_allowed",
        "gate_effect",
        "live_effect_allowed",
        "planning_effect",
        "write_allowed",
    )
    assert result.authority.canonical_truth_effect == "none"
    assert not result.authority.dispatch_allowed
    assert not result.authority.live_effect_allowed
    assert not result.authority.write_allowed
    assert type(result).__repr__ is object.__repr__
    assert type(result.goal_ref).__repr__ is object.__repr__
    with pytest.raises(FrozenInstanceError):
        result.schema = "changed"
    with pytest.raises(FrozenInstanceError):
        result.goal_ref.ref_id = "changed"


def test_h1_29_derives_one_dependency_proven_frontier_from_canonical_planning() -> None:
    module = _api()
    planning = _planning(
        nodes=(_node("a"), _node("b", depends_on=("a",)), _node("c")),
    )

    empty, _, _ = _snapshot(planning)
    first = _build(module, planning, empty)
    assert (first.frontier.kind, first.frontier.ref_id) == ("slice", "a")
    assert first.additional_ready_count == 1

    after_a, _, _ = _snapshot(
        planning,
        _record("completions", slice_id="a", state="accepted"),
    )
    second = _build(module, planning, after_a)
    assert (second.frontier.kind, second.frontier.ref_id) == ("slice", "b")
    assert second.additional_ready_count == 1

    after_a_b, _, _ = _snapshot(
        planning,
        _record("completions", slice_id="a", state="accepted"),
        _record("completions", slice_id="b", state="accepted"),
    )
    third = _build(module, planning, after_a_b)
    assert (third.frontier.kind, third.frontier.ref_id) == ("slice", "c")
    assert third.additional_ready_count == 0

    complete, _, _ = _snapshot(
        planning,
        _record("completions", slice_id="a", state="accepted"),
        _record("completions", slice_id="b", state="accepted"),
        _record("completions", slice_id="c", state="accepted"),
    )
    final = _build(module, planning, complete)
    assert final.frontier is None
    assert final.additional_ready_count == 0


def test_h1_29_rejects_snapshot_splicing_stale_duplicate_and_cyclic_authority() -> None:
    module = _api()
    planning = _planning()
    snapshot, content_hash, composite_digest = _snapshot(planning)

    _assert_error(
        module,
        "stale_authority",
        planning,
        snapshot,
        expected_roadmap_content_hash=_ZERO_DIGEST,
    )
    _assert_error(
        module,
        "stale_authority",
        planning,
        snapshot,
        expected_composite_snapshot_digest=_ZERO_DIGEST,
    )
    for field in (
        "roadmap_content_hash",
        "definition_snapshot_digest",
        "overlay_digest",
        "evidence_set_digest",
        "policy_digest",
        "composite_snapshot_digest",
    ):
        stale = deepcopy(snapshot)
        stale[field] = _ZERO_DIGEST
        _assert_error(
            module,
            "stale_authority",
            planning,
            stale,
            expected_roadmap_content_hash=content_hash,
            expected_composite_snapshot_digest=(
                _ZERO_DIGEST if field == "composite_snapshot_digest" else composite_digest
            ),
        )

    spliced, _, _ = _snapshot(
        planning,
        _record("completions", slice_id="a", state="accepted"),
    )
    spliced["facts"]["completions"][0]["execution_revision"] = 8
    spliced_expected = _reseal_overlay(spliced)
    _assert_error(
        module,
        "stale_authority",
        planning,
        spliced,
        expected_composite_snapshot_digest=spliced_expected,
    )

    duplicate = _planning()
    duplicate["roadmaps"][0]["nodes"] += [deepcopy(duplicate["roadmaps"][0]["nodes"][0])]
    _rehash(duplicate)
    duplicate_snapshot, _, _ = _snapshot(duplicate)
    _assert_error(module, "contradictory_authority", duplicate, duplicate_snapshot)

    cyclic = _planning(nodes=(_node("a", depends_on=("b",)), _node("b", depends_on=("a",))))
    cyclic_snapshot, _, _ = _snapshot(cyclic)
    _assert_error(module, "contradictory_authority", cyclic, cyclic_snapshot)

    asymmetric = _planning(
        nodes=(_node("a"),),
        gates=(_gate("gate-a", kind="dependency", blocks=("a",)),),
    )
    asymmetric_snapshot, _, _ = _snapshot(
        asymmetric,
        _record(
            "gate_states",
            gate_id="gate-a",
            state="open",
            owner_class="machine",
            decision_ref=None,
        ),
    )
    _assert_error(module, "contradictory_authority", asymmetric, asymmetric_snapshot)


def test_h1_29_gates_claims_completions_and_owner_question_precedence_is_exact() -> None:
    module = _api()
    planning = _planning(
        nodes=(_node("a"), _node("b", depends_on=("a",), gate_ids=("gate-live",))),
        gates=(_gate("gate-live", kind="live", blocks=("b",)),),
    )
    open_gate = _record(
        "gate_states",
        gate_id="gate-live",
        state="open",
        owner_class="user",
        decision_ref=None,
    )

    open_snapshot, _, _ = _snapshot(planning, open_gate)
    opened = _build(module, planning, open_snapshot)
    assert (opened.frontier.kind, opened.frontier.ref_id) == ("slice", "a")
    assert (opened.owner_question.kind, opened.owner_question.ref_id) == ("gate", "gate-live")

    claimed_snapshot, _, _ = _snapshot(
        planning,
        open_gate,
        _record("active_claims", claim_id="claim-a", slice_id="a", state="running"),
    )
    claimed = _build(module, planning, claimed_snapshot)
    assert claimed.frontier is None
    assert _pairs(claimed.claim_refs) == (("claim", "claim-a"),)

    satisfied_snapshot, _, _ = _snapshot(
        planning,
        _record(
            "gate_states",
            gate_id="gate-live",
            state="satisfied",
            owner_class="user",
            decision_ref="decision-live",
        ),
        _record("completions", slice_id="a", state="accepted"),
        _record(
            "owner_decisions",
            decision_id="decision-live",
            gate_id="gate-live",
            state="accepted",
            owner_class="user",
        ),
    )
    satisfied = _build(module, planning, satisfied_snapshot)
    assert (satisfied.frontier.kind, satisfied.frontier.ref_id) == ("slice", "b")
    assert satisfied.owner_question is None

    changes_requested_snapshot, _, _ = _snapshot(
        planning,
        _record(
            "gate_states",
            gate_id="gate-live",
            state="open",
            owner_class="user",
            decision_ref="decision-live",
        ),
        _record(
            "owner_decisions",
            decision_id="decision-live",
            gate_id="gate-live",
            state="changes_requested",
            owner_class="user",
        ),
    )
    assert _build(module, planning, changes_requested_snapshot).owner_question.ref_id == "gate-live"

    deferred_snapshot, _, _ = _snapshot(
        planning,
        _record(
            "gate_states",
            gate_id="gate-live",
            state="deferred",
            owner_class="user",
            decision_ref="decision-live",
        ),
        _record("completions", slice_id="a", state="accepted"),
        _record(
            "owner_decisions",
            decision_id="decision-live",
            gate_id="gate-live",
            state="deferred",
            owner_class="user",
        ),
    )
    deferred = _build(module, planning, deferred_snapshot)
    assert deferred.frontier is None
    assert deferred.owner_question is None
    assert _pairs(deferred.not_verified_refs) == (("gate", "gate-live"),)

    invalid_completion, _, _ = _snapshot(
        planning,
        open_gate,
        _record("completions", slice_id="a", state="accepted"),
        _record("completions", slice_id="b", state="accepted"),
    )
    _assert_error(module, "contradictory_authority", planning, invalid_completion)

    ambiguous, _, _ = _snapshot(
        planning,
        open_gate,
        _record(
            "clarifications",
            clarification_id="clarify-a",
            slice_id="a",
            required=True,
            state="open",
        ),
    )
    _assert_error(module, "ambiguous_owner_question", planning, ambiguous)

    reviewer_planning = _planning(
        nodes=(_node("a", gate_ids=("gate-repo",)),),
        gates=(_gate("gate-repo", kind="repo", blocks=("a",)),),
    )
    reviewer_decision, _, _ = _snapshot(
        reviewer_planning,
        _record(
            "gate_states",
            gate_id="gate-repo",
            state="open",
            owner_class="reviewer",
            decision_ref="decision-repo",
        ),
        _record(
            "owner_decisions",
            decision_id="decision-repo",
            gate_id="gate-repo",
            state="changes_requested",
            owner_class="user",
        ),
    )
    _assert_error(module, "contradictory_authority", reviewer_planning, reviewer_decision)


def test_h1_29_binds_claim_gate_clarification_receipt_and_decision_refs_to_one_revision() -> None:
    module = _api()
    planning = _planning(
        nodes=(
            _node("a"),
            _node("b", depends_on=("a",), gate_ids=("gate-live",)),
            _node("c"),
        ),
        gates=(_gate("gate-live", kind="live", blocks=("b",)),),
    )
    records = (
        _record("completions", slice_id="a", state="accepted"),
        _record("active_claims", claim_id="claim-c", slice_id="c", state="paused"),
        _record(
            "gate_states",
            gate_id="gate-live",
            state="satisfied",
            owner_class="user",
            decision_ref="decision-live",
        ),
        _record(
            "clarifications",
            clarification_id="clarification-b",
            slice_id="b",
            required=True,
            state="resolved",
        ),
        _record(
            "verification_receipts",
            receipt_id="receipt-b",
            slice_id="b",
            state="failed",
        ),
        _record(
            "owner_decisions",
            decision_id="decision-live",
            gate_id="gate-live",
            state="accepted",
            owner_class="user",
        ),
    )
    snapshot, _, _ = _snapshot(planning, *records)

    result = _build(module, planning, snapshot)

    assert (result.frontier.kind, result.frontier.ref_id) == ("slice", "b")
    assert _pairs(result.claim_refs) == (("claim", "claim-c"),)
    assert _pairs(result.gate_refs) == (("gate", "gate-live"),)
    assert _pairs(result.clarification_refs) == (("clarification", "clarification-b"),)
    assert _pairs(result.receipt_refs) == (("receipt", "receipt-b"),)
    assert _pairs(result.decision_refs) == (("decision", "decision-live"),)
    assert _pairs(result.not_verified_refs) == (("receipt", "receipt-b"),)

    wrong_policy = deepcopy(snapshot)
    wrong_policy["facts"]["completions"][0]["policy_digest"] = _ZERO_DIGEST
    wrong_policy_expected = _reseal_overlay(wrong_policy)
    _assert_error(
        module,
        "stale_authority",
        planning,
        wrong_policy,
        expected_composite_snapshot_digest=wrong_policy_expected,
    )

    unknown_claim, _, _ = _snapshot(
        planning,
        _record("active_claims", claim_id="claim-x", slice_id="missing", state="running"),
        records[2],
        records[5],
    )
    _assert_error(module, "contradictory_authority", planning, unknown_claim)

    duplicate_claims, _, _ = _snapshot(
        planning,
        _record("active_claims", claim_id="claim-1", slice_id="c", state="running"),
        _record("active_claims", claim_id="claim-2", slice_id="c", state="paused"),
        records[2],
        records[5],
    )
    _assert_error(module, "contradictory_authority", planning, duplicate_claims)

    duplicate_receipts, _, _ = _snapshot(
        planning,
        records[2],
        records[5],
        _record(
            "verification_receipts", receipt_id="receipt-b", slice_id="b", state="verified"
        ),
        _record(
            "verification_receipts", receipt_id="receipt-b", slice_id="c", state="failed"
        ),
    )
    _assert_error(module, "contradictory_authority", planning, duplicate_receipts)


def test_h1_29_advisory_graph_and_memory_context_cannot_change_planning_truth() -> None:
    module = _api()
    planning = _planning()
    snapshot, _, _ = _snapshot(planning)

    baseline = _build(module, planning, snapshot)
    advised = _build(
        module,
        planning,
        snapshot,
        advisory_refs=(
            {"kind": "advisory", "ref_id": "raptor-z"},
            {"kind": "advisory", "ref_id": "graph-a"},
            {"kind": "advisory", "ref_id": "graph-a"},
        ),
    )

    assert (advised.frontier.kind, advised.frontier.ref_id) == (
        baseline.frontier.kind,
        baseline.frontier.ref_id,
    )
    assert advised.additional_ready_count == baseline.additional_ready_count
    assert advised.owner_question == baseline.owner_question
    assert _pairs(advised.not_verified_refs) == (
        ("advisory", "graph-a"),
        ("advisory", "raptor-z"),
    )

    class HostileReference:
        touched = False

        def __iter__(self):
            self.touched = True
            raise AssertionError("hostile iterator was evaluated")

        def __repr__(self):
            self.touched = True
            raise AssertionError("hostile repr was evaluated")

        def __bool__(self):
            self.touched = True
            raise AssertionError("hostile truth was evaluated")

    hostile = HostileReference()
    _assert_error(module, "invalid_input", planning, snapshot, advisory_refs=(hostile,))
    assert hostile.touched is False

    _assert_error(
        module,
        "invalid_input",
        planning,
        snapshot,
        advisory_refs=({"kind": "slice", "ref_id": "b"},),
    )


def test_h1_29_inputs_outputs_errors_and_budgets_fail_closed_and_detached() -> None:
    module = _api()
    planning = _planning()
    snapshot, _, _ = _snapshot(planning)

    for advisory in ([], {}, "graph", tuple({"kind": "advisory", "ref_id": f"a-{i}"} for i in range(65))):
        _assert_error(module, "invalid_input", planning, snapshot, advisory_refs=advisory)

    unknown_key = deepcopy(snapshot)
    unknown_key["raw"] = "PRIVATE"
    _assert_error(module, "invalid_input", planning, unknown_key)

    bool_revision = deepcopy(snapshot)
    bool_revision["execution_revision"] = True
    _assert_error(module, "invalid_input", planning, bool_revision)

    too_many_nodes = _planning(nodes=tuple(_node(f"n-{index}") for index in range(257)))
    too_many_snapshot, _, _ = _snapshot(too_many_nodes)
    _assert_error(module, "invalid_input", too_many_nodes, too_many_snapshot)

    receipt_records = tuple(
        _record(
            "verification_receipts",
            receipt_id=f"receipt-{index}",
            slice_id="a",
            state="verified",
        )
        for index in range(1025)
    )
    too_many_facts, _, _ = _snapshot(planning, *receipt_records)
    _assert_error(module, "invalid_input", planning, too_many_facts)

    planning_input = deepcopy(planning)
    snapshot_input = deepcopy(snapshot)
    result = _build(module, planning_input, snapshot_input)
    planning_input["project"]["project_id"] = "mutated"
    snapshot_input["project_id"] = "mutated"
    assert result.goal_ref.ref_id == "roadmap-a"
    assert result.composite_snapshot_digest == snapshot["composite_snapshot_digest"]

    first = _assert_error(module, "invalid_input", planning, snapshot, advisory_refs=[])
    second = _assert_error(module, "invalid_input", planning, snapshot, advisory_refs=[])
    assert first is not second


def test_h1_29_projection_has_zero_external_or_mutating_effects(monkeypatch) -> None:
    module = _api()
    planning = _planning()
    snapshot, _, _ = _snapshot(planning)
    planning_before = deepcopy(planning)
    snapshot_before = deepcopy(snapshot)
    calls: list[str] = []

    def forbidden(*_args, **_kwargs):
        calls.append("external")
        raise AssertionError("external effect attempted")

    import builtins
    import os
    import socket
    import subprocess
    import threading

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(threading.Thread, "start", forbidden)

    first = _build(module, planning, snapshot)
    second = _build(module, planning, snapshot)

    assert first == second
    assert planning == planning_before
    assert snapshot == snapshot_before
    assert calls == []

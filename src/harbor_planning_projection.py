"""Content-free projection of one closed Harbor planning authority snapshot."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import struct
from typing import Any

from src.planning_definition_contract import (
    PlanningDefinitionContractError,
    compute_roadmap_content_hash,
    validate_planning_definition,
)


__all__ = (
    "HarborPlanningProjectionError",
    "HarborPlanningProjection",
    "build_harbor_planning_projection",
)


_OUTPUT_SCHEMA = "odysseus.harbor.canonical_planning_projection.v2"
_SNAPSHOT_SCHEMA = "odysseus.harbor.execution_authority_snapshot.v1"
_DEFINITION_SCHEMA = "odysseus.harbor.definition_authority_snapshot.v1"
_OVERLAY_SCHEMA = "odysseus.harbor.execution_overlay.v1"
_EVIDENCE_SET_SCHEMA = "odysseus.harbor.execution_evidence_set.v1"
_POLICY_SCHEMA = "odysseus.harbor.planning_projection_policy.v1"
_POLICY_DIGEST = "sha256:33966f569d12022127d066bda993322a5bc880230a88b3a4825bbf55ff05cc07"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ERROR_CODES = frozenset(
    {"ambiguous_owner_question", "contradictory_authority", "invalid_input", "stale_authority"}
)

_MAX_DEFINITION_BYTES = 1_048_576
_MAX_NODES = 256
_MAX_GATES = 256
_MAX_EDGES = 1_024
_MAX_FACTS = 1_024
_MAX_ADVISORY_REFS = 64
_MAX_OUTPUT_REFS_PER_KIND = 256
_MAX_EXECUTION_REVISION = 9_223_372_036_854_775_807

_GATE_OWNER = {
    "dependency": "machine",
    "design": "user",
    "live": "user",
    "operator": "user",
    "repo": "reviewer",
    "security": "user",
}

_SNAPSHOT_FIELDS = frozenset(
    {
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
        "facts",
        "composite_snapshot_digest",
    }
)
_FACT_GROUPS = (
    "completions",
    "active_claims",
    "gate_states",
    "clarifications",
    "verification_receipts",
    "owner_decisions",
)
_FACT_PRIMARY = {
    "completions": "slice_id",
    "active_claims": "claim_id",
    "gate_states": "gate_id",
    "clarifications": "clarification_id",
    "verification_receipts": "receipt_id",
    "owner_decisions": "decision_id",
}
_FACT_FIELDS = {
    "completions": frozenset(
        {
            "slice_id",
            "state",
            "execution_revision",
            "evidence_digest",
            "evidence_set_digest",
            "policy_digest",
        }
    ),
    "active_claims": frozenset(
        {
            "claim_id",
            "slice_id",
            "state",
            "execution_revision",
            "evidence_digest",
            "evidence_set_digest",
            "policy_digest",
        }
    ),
    "gate_states": frozenset(
        {
            "gate_id",
            "state",
            "owner_class",
            "decision_ref",
            "execution_revision",
            "evidence_digest",
            "evidence_set_digest",
            "policy_digest",
        }
    ),
    "clarifications": frozenset(
        {
            "clarification_id",
            "slice_id",
            "required",
            "state",
            "execution_revision",
            "evidence_digest",
            "evidence_set_digest",
            "policy_digest",
        }
    ),
    "verification_receipts": frozenset(
        {
            "receipt_id",
            "slice_id",
            "state",
            "execution_revision",
            "evidence_digest",
            "evidence_set_digest",
            "policy_digest",
        }
    ),
    "owner_decisions": frozenset(
        {
            "decision_id",
            "gate_id",
            "state",
            "owner_class",
            "execution_revision",
            "evidence_digest",
            "evidence_set_digest",
            "policy_digest",
        }
    ),
}
_FACT_STATES = {
    "completions": frozenset({"accepted"}),
    "active_claims": frozenset({"paused", "running"}),
    "gate_states": frozenset({"deferred", "expired", "open", "satisfied"}),
    "clarifications": frozenset({"deferred", "open", "resolved"}),
    "verification_receipts": frozenset({"failed", "not_verified", "verified"}),
    "owner_decisions": frozenset({"accepted", "changes_requested", "deferred"}),
}


class HarborPlanningProjectionError(ValueError):
    """Stable content-free projection failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Reject(Exception):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class _HarborReference:
    kind: str
    ref_id: str


@dataclass(frozen=True, slots=True, repr=False)
class _HarborProjectionAuthority:
    canonical_truth_effect: str = "none"
    claim_effect: str = "none"
    decision_effect: str = "none"
    dispatch_allowed: bool = False
    gate_effect: str = "none"
    live_effect_allowed: bool = False
    planning_effect: str = "none"
    write_allowed: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class HarborPlanningProjection:
    schema: str
    composite_snapshot_digest: str
    execution_revision: int
    goal_ref: _HarborReference
    frontier: _HarborReference | None
    additional_ready_count: int
    owner_question: _HarborReference | None
    claim_refs: tuple[_HarborReference, ...]
    gate_refs: tuple[_HarborReference, ...]
    clarification_refs: tuple[_HarborReference, ...]
    receipt_refs: tuple[_HarborReference, ...]
    decision_refs: tuple[_HarborReference, ...]
    not_verified_refs: tuple[_HarborReference, ...]
    authority: _HarborProjectionAuthority


def build_harbor_planning_projection(
    *,
    planning_document: dict,
    execution_snapshot: dict,
    expected_roadmap_content_hash: str,
    expected_composite_snapshot_digest: str,
    advisory_refs: tuple = (),
) -> HarborPlanningProjection:
    """Return one detached advisory projection or a fresh allowlisted error."""

    result, error_code = _capture_projection(
        planning_document=planning_document,
        execution_snapshot=execution_snapshot,
        expected_roadmap_content_hash=expected_roadmap_content_hash,
        expected_composite_snapshot_digest=expected_composite_snapshot_digest,
        advisory_refs=advisory_refs,
    )
    if error_code is not None:
        raise HarborPlanningProjectionError(error_code) from None
    assert result is not None
    return result


def _capture_projection(**values) -> tuple[HarborPlanningProjection | None, str | None]:
    try:
        return _build_projection(**values), None
    except (KeyboardInterrupt, SystemExit):
        raise
    except _Reject as error:
        code = error.code if error.code in _ERROR_CODES else "invalid_input"
        return None, code
    except Exception:
        return None, "invalid_input"


def _build_projection(
    *,
    planning_document: dict,
    execution_snapshot: dict,
    expected_roadmap_content_hash: str,
    expected_composite_snapshot_digest: str,
    advisory_refs: tuple,
) -> HarborPlanningProjection:
    expected_roadmap_content_hash = _digest_value(expected_roadmap_content_hash)
    expected_composite_snapshot_digest = _digest_value(expected_composite_snapshot_digest)
    advisory = _advisory_references(advisory_refs)

    _exact_dict(execution_snapshot, _SNAPSHOT_FIELDS)
    _canonical_value(execution_snapshot)
    if execution_snapshot["schema"] != _SNAPSHOT_SCHEMA:
        _reject("invalid_input")
    snapshot_project_id = _identifier(execution_snapshot["project_id"])
    snapshot_roadmap_id = _identifier(execution_snapshot["roadmap_id"])
    snapshot_roadmap_revision = _positive_revision(execution_snapshot["roadmap_revision"])
    execution_revision = _positive_revision(execution_snapshot["execution_revision"])
    snapshot_content_hash = _digest_value(execution_snapshot["roadmap_content_hash"])
    snapshot_definition_digest = _digest_value(
        execution_snapshot["definition_snapshot_digest"]
    )
    snapshot_overlay_digest = _digest_value(execution_snapshot["overlay_digest"])
    snapshot_evidence_set_digest = _digest_value(execution_snapshot["evidence_set_digest"])
    snapshot_policy_digest = _digest_value(execution_snapshot["policy_digest"])
    snapshot_composite_digest = _digest_value(
        execution_snapshot["composite_snapshot_digest"]
    )

    planning, project, roadmap = _validated_selected_planning(
        planning_document,
        project_id=snapshot_project_id,
        roadmap_id=snapshot_roadmap_id,
        roadmap_revision=snapshot_roadmap_revision,
        expected_content_hash=expected_roadmap_content_hash,
        snapshot_content_hash=snapshot_content_hash,
    )
    graph = _closed_graph(planning, project, roadmap)

    definition_payload = {
        "schema": _DEFINITION_SCHEMA,
        "project_id": snapshot_project_id,
        "roadmap_id": snapshot_roadmap_id,
        "roadmap_revision": snapshot_roadmap_revision,
        "roadmap_content_hash": expected_roadmap_content_hash,
        "node_ids": graph["node_ids"],
        "dependency_pairs": graph["dependency_pairs"],
        "node_gate_pairs": graph["node_gate_pairs"],
        "gate_ids": graph["gate_ids"],
        "gate_block_pairs": graph["gate_block_pairs"],
    }
    definition_digest = _digest(_DEFINITION_SCHEMA, definition_payload)
    if definition_digest != snapshot_definition_digest:
        _reject("stale_authority")

    computed_policy_digest = _digest(_POLICY_SCHEMA, _policy_payload())
    if computed_policy_digest != _POLICY_DIGEST or snapshot_policy_digest != _POLICY_DIGEST:
        _reject("stale_authority")

    facts = _closed_facts(
        execution_snapshot["facts"],
        execution_revision=execution_revision,
        evidence_set_digest=snapshot_evidence_set_digest,
        policy_digest=snapshot_policy_digest,
    )
    evidence_set_digest = _digest(
        _EVIDENCE_SET_SCHEMA,
        {
            "schema": _EVIDENCE_SET_SCHEMA,
            "evidence_digests": tuple(
                sorted(
                    {
                        item["evidence_digest"]
                        for group in _FACT_GROUPS
                        for item in facts[group]
                    }
                )
            ),
        },
    )
    if evidence_set_digest != snapshot_evidence_set_digest:
        _reject("stale_authority")

    overlay_payload = {
        "schema": _OVERLAY_SCHEMA,
        "execution_revision": execution_revision,
        **facts,
    }
    overlay_digest = _digest(_OVERLAY_SCHEMA, overlay_payload)
    if overlay_digest != snapshot_overlay_digest:
        _reject("stale_authority")

    composite_payload = {
        "schema": _SNAPSHOT_SCHEMA,
        "project_id": snapshot_project_id,
        "roadmap_id": snapshot_roadmap_id,
        "roadmap_revision": snapshot_roadmap_revision,
        "roadmap_content_hash": expected_roadmap_content_hash,
        "definition_snapshot_digest": definition_digest,
        "execution_revision": execution_revision,
        "overlay_digest": overlay_digest,
        "evidence_set_digest": evidence_set_digest,
        "policy_digest": computed_policy_digest,
    }
    composite_digest = _digest(_SNAPSHOT_SCHEMA, composite_payload)
    if (
        composite_digest != snapshot_composite_digest
        or composite_digest != expected_composite_snapshot_digest
    ):
        _reject("stale_authority")

    semantic = _semantic_authority(graph, facts)
    ready = semantic["ready"]
    frontier = _reference("slice", ready[0]) if ready else None
    owner_questions = semantic["owner_questions"]
    if len(owner_questions) > 1:
        _reject("ambiguous_owner_question")
    owner_question = owner_questions[0] if owner_questions else None

    claim_refs = _references("claim", (item["claim_id"] for item in facts["active_claims"]))
    gate_refs = _references("gate", graph["gate_ids"])
    clarification_refs = _references(
        "clarification", (item["clarification_id"] for item in facts["clarifications"])
    )
    receipt_refs = _references(
        "receipt", (item["receipt_id"] for item in facts["verification_receipts"])
    )
    decision_refs = _references(
        "decision", (item["decision_id"] for item in facts["owner_decisions"])
    )
    not_verified = list(advisory)
    not_verified.extend(
        _reference("receipt", item["receipt_id"])
        for item in facts["verification_receipts"]
        if item["state"] in {"failed", "not_verified"}
    )
    not_verified.extend(
        _reference("gate", item["gate_id"])
        for item in facts["gate_states"]
        if item["state"] in {"deferred", "expired"}
    )
    not_verified.extend(
        _reference("clarification", item["clarification_id"])
        for item in facts["clarifications"]
        if item["state"] == "deferred"
    )
    not_verified_refs = _deduplicated_references(not_verified)

    return HarborPlanningProjection(
        schema=_OUTPUT_SCHEMA,
        composite_snapshot_digest=composite_digest,
        execution_revision=execution_revision,
        goal_ref=_reference("roadmap", snapshot_roadmap_id),
        frontier=frontier,
        additional_ready_count=max(0, len(ready) - 1),
        owner_question=owner_question,
        claim_refs=claim_refs,
        gate_refs=gate_refs,
        clarification_refs=clarification_refs,
        receipt_refs=receipt_refs,
        decision_refs=decision_refs,
        not_verified_refs=not_verified_refs,
        authority=_HarborProjectionAuthority(),
    )


def _validated_selected_planning(
    value: Any,
    *,
    project_id: str,
    roadmap_id: str,
    roadmap_revision: int,
    expected_content_hash: str,
    snapshot_content_hash: str,
) -> tuple[dict, dict, dict]:
    if type(value) is not dict:
        _reject("invalid_input")
    _planning_canonical_value(value)
    if len(_planning_canonical_bytes(value)) > _MAX_DEFINITION_BYTES:
        _reject("invalid_input")
    try:
        validate_planning_definition(value)
    except PlanningDefinitionContractError as error:
        if error.reason_code in {
            "dependency_cycle",
            "duplicate_id",
            "invalid_gate_target",
            "missing_reference",
            "unreferenced_roadmap",
        }:
            _reject("contradictory_authority")
        if error.reason_code in {"content_hash_mismatch", "invalid_approved_reference"}:
            _reject("stale_authority")
        _reject("invalid_input")

    _exact_dict_keys_are_strings(value)
    project = value.get("project")
    roadmaps = value.get("roadmaps")
    if type(project) is not dict or type(roadmaps) is not list:
        _reject("invalid_input")
    if project.get("project_id") != project_id:
        _reject("stale_authority")
    selected = tuple(
        roadmap
        for roadmap in roadmaps
        if type(roadmap) is dict
        and roadmap.get("roadmap_id") == roadmap_id
        and roadmap.get("revision") == roadmap_revision
    )
    if len(selected) != 1:
        _reject("stale_authority")
    roadmap = selected[0]
    if roadmap.get("project_id") != project_id or roadmap.get("revision_state") != "approved":
        _reject("stale_authority")
    computed_content_hash = compute_roadmap_content_hash(roadmap)
    latest = project.get("latest_approved_revision")
    if type(latest) is not dict or type(latest.get(roadmap_id)) is not dict:
        _reject("stale_authority")
    latest_reference = latest[roadmap_id]
    if (
        roadmap.get("content_hash") != computed_content_hash
        or latest_reference.get("revision") != roadmap_revision
        or latest_reference.get("content_hash") != computed_content_hash
        or expected_content_hash != computed_content_hash
        or snapshot_content_hash != computed_content_hash
    ):
        _reject("stale_authority")
    return value, project, roadmap


def _closed_graph(planning: dict, project: dict, roadmap: dict) -> dict[str, Any]:
    del planning, project
    nodes = roadmap.get("nodes")
    gates = roadmap.get("gates")
    edges = roadmap.get("edges")
    if type(nodes) is not list or type(gates) is not list or type(edges) is not list:
        _reject("invalid_input")
    if len(nodes) > _MAX_NODES or len(gates) > _MAX_GATES or len(edges) > _MAX_EDGES:
        _reject("invalid_input")

    node_by_id: dict[str, dict] = {}
    dependencies: dict[str, set[str]] = {}
    node_gate_pairs: set[tuple[str, str]] = set()
    for node in nodes:
        if type(node) is not dict:
            _reject("invalid_input")
        node_id = _identifier(node.get("node_id"))
        if node_id in node_by_id:
            _reject("contradictory_authority")
        node_by_id[node_id] = node
        raw_dependencies = node.get("depends_on")
        raw_gates = node.get("gate_ids")
        if type(raw_dependencies) is not list or type(raw_gates) is not list:
            _reject("invalid_input")
        dependency_values = tuple(_identifier(item) for item in raw_dependencies)
        gate_values = tuple(_identifier(item) for item in raw_gates)
        if len(set(dependency_values)) != len(dependency_values):
            _reject("contradictory_authority")
        if len(set(gate_values)) != len(gate_values):
            _reject("contradictory_authority")
        dependencies[node_id] = set(dependency_values)
        node_gate_pairs.update((node_id, gate_id) for gate_id in gate_values)

    node_ids = frozenset(node_by_id)
    for node_id, values in dependencies.items():
        if node_id in values or not values.issubset(node_ids):
            _reject("contradictory_authority")

    seen_dependency_edges: set[tuple[str, str]] = set()
    for edge in edges:
        if type(edge) is not dict:
            _reject("invalid_input")
        if edge.get("kind") != "depends_on":
            continue
        pair = (_identifier(edge.get("from")), _identifier(edge.get("to")))
        if pair in seen_dependency_edges:
            _reject("contradictory_authority")
        seen_dependency_edges.add(pair)
        if pair[0] not in node_ids or pair[1] not in node_ids or pair[0] == pair[1]:
            _reject("contradictory_authority")
        dependencies[pair[0]].add(pair[1])
    if sum(len(values) for values in dependencies.values()) > _MAX_EDGES:
        _reject("invalid_input")

    gate_by_id: dict[str, dict] = {}
    gate_block_pairs: set[tuple[str, str]] = set()
    gate_nodes: dict[str, set[str]] = {}
    for gate in gates:
        if type(gate) is not dict:
            _reject("invalid_input")
        gate_id = _identifier(gate.get("gate_id"))
        if gate_id in gate_by_id:
            _reject("contradictory_authority")
        kind = gate.get("kind")
        if type(kind) is not str or kind not in _GATE_OWNER:
            _reject("invalid_input")
        blocks = gate.get("blocks")
        if type(blocks) is not list:
            _reject("invalid_input")
        targets = tuple(_identifier(item) for item in blocks)
        if len(set(targets)) != len(targets) or not set(targets).issubset(node_ids):
            _reject("contradictory_authority")
        gate_by_id[gate_id] = gate
        gate_nodes[gate_id] = set(targets)
        gate_block_pairs.update((node_id, gate_id) for node_id in targets)

    if node_gate_pairs != gate_block_pairs:
        _reject("contradictory_authority")
    if any(gate_id not in gate_by_id for _, gate_id in node_gate_pairs):
        _reject("contradictory_authority")
    _reject_cycles(dependencies)

    node_gates = {node_id: set() for node_id in node_ids}
    for node_id, gate_id in gate_block_pairs:
        node_gates[node_id].add(gate_id)
    dependency_pairs = tuple(
        sorted((node_id, dependency) for node_id, values in dependencies.items() for dependency in values)
    )
    return {
        "node_ids": tuple(sorted(node_ids)),
        "dependency_pairs": dependency_pairs,
        "node_gate_pairs": tuple(sorted(node_gate_pairs)),
        "gate_ids": tuple(sorted(gate_by_id)),
        "gate_block_pairs": tuple(sorted(gate_block_pairs)),
        "dependencies": {key: frozenset(value) for key, value in dependencies.items()},
        "gate_by_id": gate_by_id,
        "node_gates": {key: frozenset(value) for key, value in node_gates.items()},
    }


def _reject_cycles(dependencies: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            _reject("contradictory_authority")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in sorted(dependencies[node_id]):
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(dependencies):
        visit(node_id)


def _closed_facts(
    value: Any,
    *,
    execution_revision: int,
    evidence_set_digest: str,
    policy_digest: str,
) -> dict[str, tuple[dict, ...]]:
    _exact_dict(value, frozenset(_FACT_GROUPS))
    total = 0
    parsed: dict[str, tuple[dict, ...]] = {}
    for group in _FACT_GROUPS:
        raw_records = value[group]
        if type(raw_records) is not tuple:
            _reject("invalid_input")
        total += len(raw_records)
        records: list[dict] = []
        for raw in raw_records:
            _exact_dict(raw, _FACT_FIELDS[group])
            _canonical_value(raw)
            record = _closed_fact_record(
                group,
                raw,
                execution_revision=execution_revision,
                evidence_set_digest=evidence_set_digest,
                policy_digest=policy_digest,
            )
            records.append(record)
        records.sort(key=lambda item, key=_FACT_PRIMARY[group]: item[key])
        parsed[group] = tuple(records)
    if total > _MAX_FACTS or len(parsed["gate_states"]) > _MAX_GATES:
        _reject("invalid_input")
    return parsed


def _closed_fact_record(
    group: str,
    value: dict,
    *,
    execution_revision: int,
    evidence_set_digest: str,
    policy_digest: str,
) -> dict:
    if value["state"] not in _FACT_STATES[group] or type(value["state"]) is not str:
        _reject("invalid_input")
    if _positive_revision(value["execution_revision"]) != execution_revision:
        _reject("stale_authority")
    _digest_value(value["evidence_digest"])
    if _digest_value(value["evidence_set_digest"]) != evidence_set_digest:
        _reject("stale_authority")
    if _digest_value(value["policy_digest"]) != policy_digest:
        _reject("stale_authority")

    for key in ("slice_id", "claim_id", "gate_id", "clarification_id", "receipt_id", "decision_id"):
        if key in value:
            _identifier(value[key])
    if group == "clarifications" and type(value["required"]) is not bool:
        _reject("invalid_input")
    if group == "gate_states":
        if type(value["owner_class"]) is not str or value["owner_class"] not in {
            "machine",
            "reviewer",
            "user",
        }:
            _reject("invalid_input")
        decision_ref = value["decision_ref"]
        if decision_ref is not None:
            _identifier(decision_ref)
    if group == "owner_decisions" and value["owner_class"] != "user":
        _reject("invalid_input")
    return value


def _semantic_authority(graph: dict[str, Any], facts: dict[str, tuple[dict, ...]]) -> dict:
    node_ids = set(graph["node_ids"])
    gate_ids = set(graph["gate_ids"])
    for group in _FACT_GROUPS:
        primary = _FACT_PRIMARY[group]
        values = [item[primary] for item in facts[group]]
        if len(values) != len(set(values)):
            _reject("contradictory_authority")

    completions = {item["slice_id"]: item for item in facts["completions"]}
    claims = {item["claim_id"]: item for item in facts["active_claims"]}
    gate_states = {item["gate_id"]: item for item in facts["gate_states"]}
    clarifications = {
        item["clarification_id"]: item for item in facts["clarifications"]
    }
    receipts = {item["receipt_id"]: item for item in facts["verification_receipts"]}
    decisions = {item["decision_id"]: item for item in facts["owner_decisions"]}

    if not set(completions).issubset(node_ids):
        _reject("contradictory_authority")
    if any(item["slice_id"] not in node_ids for item in claims.values()):
        _reject("contradictory_authority")
    if any(item["slice_id"] not in node_ids for item in clarifications.values()):
        _reject("contradictory_authority")
    if any(item["slice_id"] not in node_ids for item in receipts.values()):
        _reject("contradictory_authority")
    if set(gate_states) != gate_ids:
        _reject("contradictory_authority")
    if any(item["gate_id"] not in gate_ids for item in decisions.values()):
        _reject("contradictory_authority")

    claimed_slices = [item["slice_id"] for item in claims.values()]
    if len(claimed_slices) != len(set(claimed_slices)):
        _reject("contradictory_authority")
    completed = set(completions)
    if completed.intersection(claimed_slices):
        _reject("contradictory_authority")

    decisions_by_gate: dict[str, list[dict]] = {gate_id: [] for gate_id in gate_ids}
    for decision in decisions.values():
        decisions_by_gate[decision["gate_id"]].append(decision)
    consumed_decisions: set[str] = set()
    for gate_id in sorted(gate_ids):
        gate = graph["gate_by_id"][gate_id]
        gate_state = gate_states[gate_id]
        expected_owner = _GATE_OWNER[gate["kind"]]
        if gate_state["owner_class"] != expected_owner:
            _reject("contradictory_authority")
        attached = decisions_by_gate[gate_id]
        reference = gate_state["decision_ref"]
        state = gate_state["state"]
        if expected_owner != "user":
            if reference is not None or attached:
                _reject("contradictory_authority")
            continue
        if state == "open" and reference is None and not attached:
            continue
        expected_decision_state = {
            "open": "changes_requested",
            "satisfied": "accepted",
            "deferred": "deferred",
        }.get(state)
        if state == "expired":
            if reference is not None or attached:
                _reject("contradictory_authority")
            continue
        if (
            expected_decision_state is None
            or reference is None
            or len(attached) != 1
            or attached[0]["decision_id"] != reference
            or attached[0]["state"] != expected_decision_state
        ):
            _reject("contradictory_authority")
        consumed_decisions.add(attached[0]["decision_id"])
    if consumed_decisions != set(decisions):
        _reject("contradictory_authority")

    blocking_clarifications: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for clarification in clarifications.values():
        if clarification["required"] and clarification["state"] in {"open", "deferred"}:
            blocking_clarifications[clarification["slice_id"]].add(
                clarification["clarification_id"]
            )

    for node_id in sorted(completed):
        if not graph["dependencies"][node_id].issubset(completed):
            _reject("contradictory_authority")
        if any(gate_states[gate_id]["state"] != "satisfied" for gate_id in graph["node_gates"][node_id]):
            _reject("contradictory_authority")
        if blocking_clarifications[node_id] or node_id in claimed_slices:
            _reject("contradictory_authority")

    claimed_set = set(claimed_slices)
    ready = tuple(
        node_id
        for node_id in sorted(node_ids)
        if node_id not in completed
        and graph["dependencies"][node_id].issubset(completed)
        and all(
            gate_states[gate_id]["state"] == "satisfied"
            for gate_id in graph["node_gates"][node_id]
        )
        and not blocking_clarifications[node_id]
        and node_id not in claimed_set
    )

    owner_questions: list[_HarborReference] = []
    owner_questions.extend(
        _reference("gate", gate_id)
        for gate_id in sorted(gate_ids)
        if gate_states[gate_id]["owner_class"] == "user"
        and gate_states[gate_id]["state"] == "open"
    )
    owner_questions.extend(
        _reference("clarification", item["clarification_id"])
        for item in sorted(
            clarifications.values(), key=lambda clarification: clarification["clarification_id"]
        )
        if item["required"] and item["state"] == "open"
    )
    owner_questions.sort(key=lambda item: (item.kind, item.ref_id))
    return {"ready": ready, "owner_questions": tuple(owner_questions)}


def _advisory_references(value: Any) -> tuple[_HarborReference, ...]:
    if type(value) is not tuple or len(value) > _MAX_ADVISORY_REFS:
        _reject("invalid_input")
    references: list[_HarborReference] = []
    for item in value:
        _exact_dict(item, frozenset({"kind", "ref_id"}))
        if item["kind"] != "advisory":
            _reject("invalid_input")
        references.append(_reference("advisory", item["ref_id"]))
    return _deduplicated_references(references)


def _references(kind: str, identifiers) -> tuple[_HarborReference, ...]:
    references = [_reference(kind, identifier) for identifier in identifiers]
    if len(references) > _MAX_OUTPUT_REFS_PER_KIND:
        _reject("invalid_input")
    return _deduplicated_references(references)


def _deduplicated_references(values) -> tuple[_HarborReference, ...]:
    pairs = sorted({(item.kind, item.ref_id) for item in values})
    counts: dict[str, int] = {}
    for kind, _ in pairs:
        counts[kind] = counts.get(kind, 0) + 1
        if counts[kind] > _MAX_OUTPUT_REFS_PER_KIND:
            _reject("invalid_input")
    return tuple(_HarborReference(kind=kind, ref_id=ref_id) for kind, ref_id in pairs)


def _reference(kind: str, ref_id: Any) -> _HarborReference:
    if kind not in {
        "project",
        "roadmap",
        "slice",
        "claim",
        "gate",
        "clarification",
        "receipt",
        "decision",
        "advisory",
    }:
        _reject("invalid_input")
    return _HarborReference(kind=kind, ref_id=_identifier(ref_id))


def _policy_payload() -> dict:
    return {
        "claim_states": ("paused", "running"),
        "clarification_states": ("deferred", "open", "resolved"),
        "completion_states": ("accepted",),
        "decision_states": ("accepted", "changes_requested", "deferred"),
        "gate_owner_classes": dict(_GATE_OWNER),
        "gate_states": ("deferred", "expired", "open", "satisfied"),
        "policy_id": "h1-29",
        "policy_revision": 2,
        "receipt_states": ("failed", "not_verified", "verified"),
        "schema": _POLICY_SCHEMA,
    }


def _digest(domain: str, value: Any) -> str:
    payload = _canonical_bytes(value)
    framed = domain.encode("utf-8") + b"\0" + struct.pack(">Q", len(payload)) + payload
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    _canonical_value(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        _reject("invalid_input")


def _planning_canonical_bytes(value: Any) -> bytes:
    _planning_canonical_value(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        _reject("invalid_input")


def _planning_canonical_value(value: Any, *, depth: int = 0) -> None:
    if depth > 128:
        _reject("invalid_input")
    value_type = type(value)
    if value is None or value_type in {bool, str, int}:
        return
    if value_type is float:
        if not math.isfinite(value):
            _reject("invalid_input")
        return
    if value_type is list:
        for item in value:
            _planning_canonical_value(item, depth=depth + 1)
        return
    if value_type is dict:
        _exact_dict_keys_are_strings(value)
        for item in value.values():
            _planning_canonical_value(item, depth=depth + 1)
        return
    _reject("invalid_input")


def _canonical_value(value: Any, *, depth: int = 0) -> None:
    if depth > 128:
        _reject("invalid_input")
    value_type = type(value)
    if value is None or value_type in {bool, str}:
        return
    if value_type is int:
        return
    if value_type is float:
        if not math.isfinite(value):
            _reject("invalid_input")
        return
    if value_type is tuple:
        for item in value:
            _canonical_value(item, depth=depth + 1)
        return
    if value_type is dict:
        _exact_dict_keys_are_strings(value)
        for item in value.values():
            _canonical_value(item, depth=depth + 1)
        return
    _reject("invalid_input")


def _exact_dict(value: Any, keys: frozenset[str]) -> dict:
    if type(value) is not dict:
        _reject("invalid_input")
    _exact_dict_keys_are_strings(value)
    if frozenset(value) != keys:
        _reject("invalid_input")
    return value


def _exact_dict_keys_are_strings(value: dict) -> None:
    if any(type(key) is not str for key in value):
        _reject("invalid_input")


def _identifier(value: Any) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        _reject("invalid_input")
    return value


def _digest_value(value: Any) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        _reject("invalid_input")
    return value


def _positive_revision(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_EXECUTION_REVISION:
        _reject("invalid_input")
    return value


def _reject(code: str) -> None:
    raise _Reject(code)

"""Pure immutable authority snapshots for approved Planning Definition v2 revisions.

This module is deliberately below the Agent and Temporal execution boundaries.
It accepts a read-only Planning Definition v2 read model, validates its exact
approved head, and produces a canonical, content-addressed authority record.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from src.planning_definition_contract import (
    EDGE_KINDS,
    GATE_KINDS,
    NODE_KINDS,
    PLANNING_DEFINITION_SCHEMA_ID,
    PlanningDefinitionContractError,
    VERIFICATION_KINDS,
    collect_repository_paths,
    compute_roadmap_content_hash,
    validate_approval_scope_schema,
    validate_planning_definition,
    validate_planning_text,
)


DEFINITION_AUTHORITY_SNAPSHOT_SCHEMA_ID = "odysseus.definition_authority_snapshot.v1"
DEFINITION_AUTHORITY_SNAPSHOT_REF_SCHEMA_ID = "odysseus.definition_authority_snapshot_ref.v1"
_HASH_PREFIX = "sha256:"
_HASH_LENGTH = len(_HASH_PREFIX) + 64
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PAYLOAD_FIELDS = frozenset(
    {
        "schema_id",
        "project_id",
        "roadmap_id",
        "planning_revision",
        "planning_content_hash",
        "normalized_dag",
        "allowed_paths",
        "blocked_paths",
        "done_contract",
        "snapshot_hash",
    }
)
_REFERENCE_FIELDS = frozenset(
    {
        "schema_id",
        "project_id",
        "roadmap_id",
        "planning_revision",
        "planning_content_hash",
        "snapshot_hash",
    }
)


class DefinitionAuthoritySnapshotError(ValueError):
    """Fail-closed snapshot construction or validation error."""

    def __init__(self, code: str, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code} at {path}: {detail}")


@dataclass(frozen=True)
class DefinitionAuthoritySnapshot:
    """Canonical authority projection of exactly one approved roadmap revision."""

    project_id: str
    roadmap_id: str
    planning_revision: int
    planning_content_hash: str
    snapshot_hash: str
    _canonical_bytes: bytes
    _payload_json: str

    @property
    def canonical_bytes(self) -> bytes:
        """Canonical unsigned authority bytes whose digest is ``snapshot_hash``."""

        return bytes(self._canonical_bytes)

    def to_payload(self) -> dict[str, Any]:
        """Return a defensive copy of the complete, externally checkable record."""

        return json.loads(self._payload_json)

    def reference_payload(self) -> dict[str, Any]:
        """Return the compact reference intended for execution manifests."""

        return {
            "schema_id": DEFINITION_AUTHORITY_SNAPSHOT_REF_SCHEMA_ID,
            "project_id": self.project_id,
            "roadmap_id": self.roadmap_id,
            "planning_revision": self.planning_revision,
            "planning_content_hash": self.planning_content_hash,
            "snapshot_hash": self.snapshot_hash,
        }


def build_definition_authority_snapshot(
    read_model: Mapping[str, Any],
) -> DefinitionAuthoritySnapshot:
    """Build one snapshot from a current, approved Definition v2 read model.

    The input is not retained.  A caller cannot select an unapproved, stale, or
    cross-project revision: all such states fail before canonical bytes exist.
    """

    _project, roadmap = _validated_current_approved_read_model(read_model)
    normalized_dag = _normalized_dag(roadmap)
    try:
        _validate_snapshot_dag(normalized_dag)
        allowed_paths = _paths_from_nodes(roadmap["nodes"], "allowed_paths")
        blocked_paths = _paths_from_nodes(roadmap["nodes"], "blocked_paths")
    except PlanningDefinitionContractError as exc:
        _fail(exc.reason_code, exc.path, exc.detail)
    if set(allowed_paths) & set(blocked_paths):
        _fail("path_scope_conflict", "$.roadmap.nodes", "one path is both allowed and blocked")

    unsigned = {
        "schema_id": DEFINITION_AUTHORITY_SNAPSHOT_SCHEMA_ID,
        "project_id": roadmap["project_id"],
        "roadmap_id": roadmap["roadmap_id"],
        "planning_revision": roadmap["revision"],
        "planning_content_hash": roadmap["content_hash"],
        "normalized_dag": normalized_dag,
        "allowed_paths": list(allowed_paths),
        "blocked_paths": list(blocked_paths),
        # ``roadmap`` is already an owned, isolated and second-validated
        # capture.  Re-capturing authority fields here would create a new
        # mutation/failure boundary after final Planning validation.
        "done_contract": roadmap["done_contract"],
    }
    canonical = _canonical_bytes(unsigned, "$")
    snapshot_hash = _hash_bytes(canonical)
    payload = {**unsigned, "snapshot_hash": snapshot_hash}
    return DefinitionAuthoritySnapshot(
        project_id=str(unsigned["project_id"]),
        roadmap_id=str(unsigned["roadmap_id"]),
        planning_revision=int(unsigned["planning_revision"]),
        planning_content_hash=str(unsigned["planning_content_hash"]),
        snapshot_hash=snapshot_hash,
        _canonical_bytes=canonical,
        _payload_json=_canonical_bytes(payload, "$").decode("utf-8"),
    )


def validate_definition_authority_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a persisted snapshot payload and return a defensive copy."""

    payload = _mapping(value, "$")
    _validate_definition_authority_snapshot_payload(payload)
    try:
        captured_payload = deepcopy(dict(payload))
    except RecursionError:
        _fail(
            "read_model_capture_failed",
            "$",
            "validated definition authority snapshot could not be isolated",
        )
    captured_payload = _with_checked_approval_schema_copies(captured_payload)
    _validate_definition_authority_snapshot_payload(captured_payload)
    return captured_payload


def _with_checked_approval_schema_copies(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace every persisted gate schema with its checked isolated copy."""

    dag = dict(_mapping(payload.get("normalized_dag"), "$.normalized_dag"))
    gates = _list(dag.get("gates"), "$.normalized_dag.gates")
    checked_gates: list[dict[str, Any]] = []
    for index, raw in enumerate(gates):
        path = f"$.normalized_dag.gates[{index}]"
        gate = dict(_mapping(raw, path))
        try:
            gate["approval_scope_schema"] = validate_approval_scope_schema(
                gate.get("approval_scope_schema"),
                path=f"{path}.approval_scope_schema",
            )
        except PlanningDefinitionContractError as exc:
            _fail(exc.reason_code, exc.path, exc.detail)
        checked_gates.append(gate)
    dag["gates"] = checked_gates
    payload["normalized_dag"] = dag
    return payload


def _validate_definition_authority_snapshot_payload(payload: Mapping[str, Any]) -> None:
    """Validate and hash the exact snapshot mapping supplied by the caller."""

    _exact_fields(payload, _PAYLOAD_FIELDS, "$")
    if payload["schema_id"] != DEFINITION_AUTHORITY_SNAPSHOT_SCHEMA_ID:
        _fail("invalid_literal", "$.schema_id", "snapshot schema is invalid")
    _identifier(payload["project_id"], "$.project_id")
    _identifier(payload["roadmap_id"], "$.roadmap_id")
    _revision(payload["planning_revision"], "$.planning_revision")
    _hash(payload["planning_content_hash"], "$.planning_content_hash")
    try:
        node_ids, gate_ids, rule_ids = _validate_snapshot_dag(payload["normalized_dag"])
        allowed_paths = collect_repository_paths(payload["allowed_paths"], path="$.allowed_paths", unique=True)
        blocked_paths = collect_repository_paths(payload["blocked_paths"], path="$.blocked_paths", unique=True)
    except PlanningDefinitionContractError as exc:
        _fail(exc.reason_code, exc.path, exc.detail)
    if payload["allowed_paths"] != list(allowed_paths) or payload["blocked_paths"] != list(blocked_paths):
        _fail("non_canonical_value", "$.allowed_paths", "snapshot paths must be unique and sorted")
    if set(allowed_paths) & set(blocked_paths):
        _fail("path_scope_conflict", "$.allowed_paths", "one path is both allowed and blocked")
    try:
        _validate_snapshot_done_contract(payload["done_contract"], node_ids, gate_ids, rule_ids)
    except PlanningDefinitionContractError as exc:
        _fail(exc.reason_code, exc.path, exc.detail)
    snapshot_hash = _hash(payload["snapshot_hash"], "$.snapshot_hash")
    unsigned = {key: item for key, item in payload.items() if key != "snapshot_hash"}
    if _hash_bytes(_canonical_bytes(unsigned, "$")) != snapshot_hash:
        _fail("snapshot_hash_mismatch", "$.snapshot_hash", "snapshot bytes do not match hash")


def validate_definition_authority_snapshot_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact compact reference which may cross into execution."""

    reference = _mapping(value, "$.definition_snapshot_ref")
    _exact_fields(reference, _REFERENCE_FIELDS, "$.definition_snapshot_ref")
    if reference["schema_id"] != DEFINITION_AUTHORITY_SNAPSHOT_REF_SCHEMA_ID:
        _fail("invalid_literal", "$.definition_snapshot_ref.schema_id", "snapshot reference schema is invalid")
    _identifier(reference["project_id"], "$.definition_snapshot_ref.project_id")
    _identifier(reference["roadmap_id"], "$.definition_snapshot_ref.roadmap_id")
    _revision(reference["planning_revision"], "$.definition_snapshot_ref.planning_revision")
    _hash(reference["planning_content_hash"], "$.definition_snapshot_ref.planning_content_hash")
    _hash(reference["snapshot_hash"], "$.definition_snapshot_ref.snapshot_hash")
    return deepcopy(dict(reference))


def _validated_current_approved_read_model(
    read_model: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(read_model, Mapping):
        _fail("invalid_read_model", "$", "Planning read model must be an object")
    project = _mapping(read_model.get("project"), "$.project")
    roadmap = _mapping(read_model.get("roadmap"), "$.roadmap")
    try:
        validate_planning_definition(
            {
                "schema_id": PLANNING_DEFINITION_SCHEMA_ID,
                "project": project,
                "roadmaps": [roadmap],
            }
        )
    except PlanningDefinitionContractError as exc:
        _fail(exc.reason_code, exc.path, exc.detail)

    try:
        project_copy = deepcopy(dict(project))
        roadmap_copy = deepcopy(dict(roadmap))
    except RecursionError:
        _fail(
            "read_model_capture_failed",
            "$",
            "validated Planning read model could not be isolated",
        )
    try:
        validate_planning_definition(
            {
                "schema_id": PLANNING_DEFINITION_SCHEMA_ID,
                "project": project_copy,
                "roadmaps": [roadmap_copy],
            }
        )
    except PlanningDefinitionContractError as exc:
        _fail(exc.reason_code, exc.path, exc.detail)

    project_id = _identifier(project_copy.get("project_id"), "$.project.project_id")
    roadmap_id = _identifier(roadmap_copy.get("roadmap_id"), "$.roadmap.roadmap_id")
    if roadmap_copy.get("project_id") != project_id:
        _fail("planning_reference_mismatch", "$.roadmap.project_id", "roadmap project does not match")
    revision = _revision(roadmap_copy.get("revision"), "$.roadmap.revision")
    content_hash = _hash(roadmap_copy.get("content_hash"), "$.roadmap.content_hash")
    if roadmap_copy.get("revision_state") != "approved":
        _fail("planning_revision_not_approved", "$.roadmap.revision_state", "revision is not approved")
    latest = project_copy.get("latest_approved_revision")
    if not isinstance(latest, Mapping):
        _fail("plan_revision_conflict", "$.project.latest_approved_revision", "approved references are missing")
    approved = latest.get(roadmap_id)
    if not isinstance(approved, Mapping) or approved.get("revision") != revision or approved.get("content_hash") != content_hash:
        _fail("plan_revision_conflict", "$.project.latest_approved_revision", "approved head changed")
    if compute_roadmap_content_hash(roadmap_copy) != content_hash:
        _fail("plan_revision_conflict", "$.roadmap.content_hash", "roadmap content changed after hashing")
    return project_copy, roadmap_copy


def _normalized_dag(roadmap: Mapping[str, Any]) -> dict[str, Any]:
    raw_nodes: dict[str, Mapping[str, Any]] = {}
    dependencies: dict[str, set[str]] = {}
    for index, raw in enumerate(roadmap["nodes"]):
        node = _mapping(raw, f"$.roadmap.nodes[{index}]")
        node_id = str(node["node_id"])
        raw_nodes[node_id] = node
        dependencies[node_id] = {str(item) for item in node["depends_on"]}

    edges: list[dict[str, str]] = []
    for index, raw in enumerate(roadmap["edges"]):
        edge = _mapping(raw, f"$.roadmap.edges[{index}]")
        source = str(edge["from"])
        target = str(edge["to"])
        kind = str(edge["kind"])
        if kind == "depends_on":
            dependencies[source].add(target)
        edges.append({"from": source, "to": target, "kind": kind})

    nodes: list[dict[str, Any]] = []
    for node_id, node in raw_nodes.items():
        nodes.append(
            {
                "node_id": node_id,
                "kind": str(node["kind"]),
                "depends_on": sorted(dependencies[node_id]),
                "gate_ids": sorted(str(item) for item in node["gate_ids"]),
                "verification_rule_ids": sorted(str(item) for item in node["verification_rule_ids"]),
            }
        )
    gates: list[dict[str, Any]] = []
    for index, raw in enumerate(roadmap["gates"]):
        gate = _mapping(raw, f"$.roadmap.gates[{index}]")
        approval_scope = validate_approval_scope_schema(
            gate["approval_scope_schema"],
            path=f"$.roadmap.gates[{index}].approval_scope_schema",
        )
        gates.append(
            {
                "gate_id": str(gate["gate_id"]),
                "kind": str(gate["kind"]),
                "blocks": sorted(str(item) for item in gate["blocks"]),
                "decision_needed": str(gate["decision_needed"]),
                "safe_default": str(gate["safe_default"]),
                "approval_scope_schema": approval_scope,
                "required_verification_rule_ids": sorted(
                    str(item) for item in gate["required_verification_rule_ids"]
                ),
            }
        )
    return {
        "nodes": sorted(nodes, key=lambda item: item["node_id"]),
        "edges": sorted(edges, key=lambda item: (item["from"], item["to"], item["kind"])),
        "gates": sorted(gates, key=lambda item: item["gate_id"]),
    }


def _paths_from_nodes(nodes: Sequence[Mapping[str, Any]], field: str) -> tuple[str, ...]:
    values: list[Any] = []
    for node in nodes:
        values.extend(node.get(field, ()))
    return collect_repository_paths(values, path=f"$.roadmap.nodes.{field}", unique=False)


def _validate_snapshot_dag(value: Any) -> tuple[set[str], set[str], set[str]]:
    dag = _mapping(value, "$.normalized_dag")
    _exact_fields(dag, frozenset({"nodes", "edges", "gates"}), "$.normalized_dag")
    nodes = _list(dag["nodes"], "$.normalized_dag.nodes", minimum=1)
    edges = _list(dag["edges"], "$.normalized_dag.edges")
    gates = _list(dag["gates"], "$.normalized_dag.gates")

    normalized_nodes: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(nodes):
        path = f"$.normalized_dag.nodes[{index}]"
        node = _mapping(raw, path)
        _exact_fields(node, frozenset({"node_id", "kind", "depends_on", "gate_ids", "verification_rule_ids"}), path)
        node_id = _identifier(node["node_id"], f"{path}.node_id")
        if node_id in normalized_nodes:
            _fail("duplicate_id", f"{path}.node_id", "node id is duplicated")
        _literal(node["kind"], f"{path}.kind", NODE_KINDS)
        _identifier_list(node["depends_on"], f"{path}.depends_on")
        _identifier_list(node["gate_ids"], f"{path}.gate_ids")
        _identifier_list(node["verification_rule_ids"], f"{path}.verification_rule_ids")
        for field in ("depends_on", "gate_ids", "verification_rule_ids"):
            if node[field] != sorted(node[field]):
                _fail("non_canonical_value", f"{path}.{field}", "normalized identifiers must be sorted")
        normalized_nodes[node_id] = node
    if [node["node_id"] for node in nodes] != sorted(normalized_nodes):
        _fail("non_canonical_value", "$.normalized_dag.nodes", "normalized nodes must be sorted")

    normalized_gates: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(gates):
        path = f"$.normalized_dag.gates[{index}]"
        gate = _mapping(raw, path)
        _exact_fields(
            gate,
            frozenset(
                {
                    "gate_id", "kind", "blocks", "decision_needed", "safe_default",
                    "approval_scope_schema", "required_verification_rule_ids",
                }
            ),
            path,
        )
        gate_id = _identifier(gate["gate_id"], f"{path}.gate_id")
        if gate_id in normalized_gates:
            _fail("duplicate_id", f"{path}.gate_id", "gate id is duplicated")
        _literal(gate["kind"], f"{path}.kind", GATE_KINDS)
        _identifier_list(gate["blocks"], f"{path}.blocks", minimum=1)
        validate_planning_text(gate["decision_needed"], path=f"{path}.decision_needed", maximum=4_000)
        validate_planning_text(gate["safe_default"], path=f"{path}.safe_default", maximum=4_000)
        validate_approval_scope_schema(gate["approval_scope_schema"], path=f"{path}.approval_scope_schema")
        _identifier_list(gate["required_verification_rule_ids"], f"{path}.required_verification_rule_ids")
        for field in ("blocks", "required_verification_rule_ids"):
            if gate[field] != sorted(gate[field]):
                _fail("non_canonical_value", f"{path}.{field}", "normalized identifiers must be sorted")
        normalized_gates[gate_id] = gate
    if [gate["gate_id"] for gate in gates] != sorted(normalized_gates):
        _fail("non_canonical_value", "$.normalized_dag.gates", "normalized gates must be sorted")

    dependencies: dict[str, set[str]] = {node_id: set() for node_id in normalized_nodes}
    for node_id, node in normalized_nodes.items():
        for dependency in node["depends_on"]:
            if dependency not in normalized_nodes:
                _fail("missing_reference", "$.normalized_dag.nodes", f"unknown node_id {dependency}")
            dependencies[node_id].add(dependency)
        for gate_id in node["gate_ids"]:
            if gate_id not in normalized_gates:
                _fail("missing_reference", "$.normalized_dag.nodes", f"unknown gate_id {gate_id}")

    edge_keys: set[tuple[str, str, str]] = set()
    ordered_edge_keys: list[tuple[str, str, str]] = []
    for index, raw in enumerate(edges):
        path = f"$.normalized_dag.edges[{index}]"
        edge = _mapping(raw, path)
        _exact_fields(edge, frozenset({"from", "to", "kind"}), path)
        source = _identifier(edge["from"], f"{path}.from")
        target = _identifier(edge["to"], f"{path}.to")
        kind = _literal(edge["kind"], f"{path}.kind", EDGE_KINDS)
        key = (source, target, kind)
        if key in edge_keys:
            _fail("duplicate_id", path, "edge is duplicated")
        edge_keys.add(key)
        ordered_edge_keys.append(key)
        if source not in normalized_nodes or target not in normalized_nodes:
            _fail("missing_reference", path, "edge endpoint is unknown")
        if kind == "depends_on":
            if target not in dependencies[source]:
                _fail("missing_reference", path, "depends_on edge is absent from normalized node dependencies")
            dependencies[source].add(target)
    if ordered_edge_keys != sorted(ordered_edge_keys):
        _fail("non_canonical_value", "$.normalized_dag.edges", "normalized edges must be sorted")

    _reject_cycles(dependencies, "$.normalized_dag.nodes")
    return set(normalized_nodes), set(normalized_gates), _validate_snapshot_rule_references(
        normalized_nodes, normalized_gates
    )


def _validate_snapshot_rule_references(
    nodes: Mapping[str, Mapping[str, Any]],
    gates: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    # The rule ids are validated against done_contract immediately afterwards;
    # returning the declared ids first keeps the DAG validator self-contained.
    declared: set[str] = set()
    for node in nodes.values():
        declared.update(str(item) for item in node["verification_rule_ids"])
    for gate in gates.values():
        for node_id in gate["blocks"]:
            if node_id not in nodes:
                _fail("missing_reference", "$.normalized_dag.gates", f"unknown node_id {node_id}")
        declared.update(str(item) for item in gate["required_verification_rule_ids"])
    return declared


def _validate_snapshot_done_contract(
    value: Any,
    node_ids: set[str],
    gate_ids: set[str],
    referenced_rule_ids: set[str],
) -> None:
    path = "$.done_contract"
    contract = _mapping(value, path)
    _exact_fields(contract, frozenset({"required_node_ids", "required_gate_ids", "verification_rules", "completion_rule"}), path)
    required_nodes = _identifier_list(contract["required_node_ids"], f"{path}.required_node_ids", minimum=1)
    required_gates = _identifier_list(contract["required_gate_ids"], f"{path}.required_gate_ids")
    for node_id in required_nodes:
        if node_id not in node_ids:
            _fail("missing_reference", f"{path}.required_node_ids", f"unknown node_id {node_id}")
    for gate_id in required_gates:
        if gate_id not in gate_ids:
            _fail("missing_reference", f"{path}.required_gate_ids", f"unknown gate_id {gate_id}")
    rules = _list(contract["verification_rules"], f"{path}.verification_rules", minimum=1)
    rule_ids: set[str] = set()
    for index, raw in enumerate(rules):
        rule_path = f"{path}.verification_rules[{index}]"
        rule = _mapping(raw, rule_path)
        _exact_fields(rule, frozenset({"rule_id", "kind", "description"}), rule_path)
        rule_id = _identifier(rule["rule_id"], f"{rule_path}.rule_id")
        if rule_id in rule_ids:
            _fail("duplicate_id", f"{rule_path}.rule_id", "verification rule id is duplicated")
        rule_ids.add(rule_id)
        _literal(rule["kind"], f"{rule_path}.kind", VERIFICATION_KINDS)
        validate_planning_text(rule["description"], path=f"{rule_path}.description", maximum=2_000)
    missing_rules = sorted(referenced_rule_ids - rule_ids)
    if missing_rules:
        _fail("missing_reference", path, f"unknown verification rule {missing_rules[0]}")
    _literal(contract["completion_rule"], f"{path}.completion_rule", frozenset({"all_required_nodes_and_gates"}))


def _reject_cycles(graph: Mapping[str, set[str]], path: str) -> None:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in active:
            _fail("dependency_cycle", path, f"cycle contains {node_id}")
        if node_id in visited:
            return
        active.add(node_id)
        for dependency in sorted(graph[node_id]):
            visit(dependency)
        active.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(graph):
        visit(node_id)


def _list(value: Any, path: str, *, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list) or len(value) < minimum:
        _fail("invalid_type", path, "expected a list with the required entries")
    return value


def _identifier_list(value: Any, path: str, *, minimum: int = 0) -> list[str]:
    values = _list(value, path, minimum=minimum)
    resolved: list[str] = []
    for index, item in enumerate(values):
        identifier = _identifier(item, f"{path}[{index}]")
        if identifier in resolved:
            _fail("duplicate_id", f"{path}[{index}]", "identifier is duplicated")
        resolved.append(identifier)
    return resolved


def _literal(value: Any, path: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail("invalid_literal", path, "value is not allowed")
    return value


def _canonical_bytes(value: Mapping[str, Any], path: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("non_canonical_value", path, "snapshot contains a non-canonical JSON value")
        raise AssertionError("unreachable") from exc


def _hash_bytes(value: bytes) -> str:
    return _HASH_PREFIX + hashlib.sha256(value).hexdigest()


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid_type", path, "expected object")
    return value


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], path: str) -> None:
    missing = sorted(expected - set(value))
    if missing:
        _fail("missing_field", path, f"missing required field {missing[0]}")
    unknown = sorted(set(value) - expected)
    if unknown:
        _fail("unknown_field", path, f"unknown field {unknown[0]}")


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _fail("invalid_identifier", path, "expected a stable identifier")
    return value


def _revision(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail("invalid_revision", path, "revision must be positive")
    return value


def _hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) != _HASH_LENGTH:
        _fail("invalid_content_hash", path, "expected sha256 lowercase hexadecimal")
    if not value.startswith(_HASH_PREFIX) or any(character not in "0123456789abcdef" for character in value[len(_HASH_PREFIX) :]):
        _fail("invalid_content_hash", path, "expected sha256 lowercase hexadecimal")
    return value


def _fail(code: str, path: str, detail: str) -> None:
    raise DefinitionAuthoritySnapshotError(code, path, detail)


__all__ = [
    "DEFINITION_AUTHORITY_SNAPSHOT_REF_SCHEMA_ID",
    "DEFINITION_AUTHORITY_SNAPSHOT_SCHEMA_ID",
    "DefinitionAuthoritySnapshot",
    "DefinitionAuthoritySnapshotError",
    "build_definition_authority_snapshot",
    "validate_definition_authority_snapshot",
    "validate_definition_authority_snapshot_reference",
]

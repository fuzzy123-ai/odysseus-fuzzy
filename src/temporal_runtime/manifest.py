"""Deterministic construction of the immutable ABC execution manifest."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping, Sequence

from src.planning_definition_contract import (
    collect_repository_paths,
    PlanningDefinitionContractError,
)
from src.definition_authority_snapshot import (
    DefinitionAuthoritySnapshotError,
    build_definition_authority_snapshot,
)
from src.temporal_runtime.contracts import (
    MAX_MANIFEST_BYTES,
    DefinitionSnapshotReference,
    ExecutionContractError,
    ExecutionManifest,
    ExecutionPolicy,
    FrozenObject,
    NormalizedDag,
    NormalizedEdge,
    NormalizedGate,
    NormalizedNode,
    canonical_json,
    freeze_json,
    validate_identifier,
)


_START_REQUEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_OWNER_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,127}$")


class ManifestBuildError(ValueError):
    def __init__(self, code: str, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code} at {path}: {detail}")


def build_execution_manifest(
    read_model: Mapping[str, Any],
    *,
    owner_scope_ref: str,
    start_request_id: str,
    policy: ExecutionPolicy,
) -> ExecutionManifest:
    owner = _owner_scope(owner_scope_ref)
    request_id = _start_request_id(start_request_id)
    try:
        definition_snapshot = build_definition_authority_snapshot(read_model)
    except DefinitionAuthoritySnapshotError as exc:
        _fail(exc.code, exc.path, exc.detail)
    snapshot_payload = definition_snapshot.to_payload()
    definition_snapshot_ref = DefinitionSnapshotReference.create(definition_snapshot.reference_payload())
    project_id = definition_snapshot_ref.project_id
    roadmap_id = definition_snapshot_ref.roadmap_id
    revision = definition_snapshot_ref.planning_revision
    content_hash = definition_snapshot_ref.planning_content_hash

    # Execution keeps legacy-compatible projections, but they are all derived
    # from the one validated authority snapshot rather than re-normalizing the
    # mutable read model a second time.
    normalized_dag = normalize_execution_dag(snapshot_payload["normalized_dag"])
    done_contract = freeze_json(snapshot_payload["done_contract"], path="$.definition_snapshot.done_contract")
    if not isinstance(done_contract, FrozenObject):
        _fail("invalid_done_contract", "$.definition_snapshot.done_contract", "done contract must be an object")
    allowed_paths = tuple(snapshot_payload["allowed_paths"])
    blocked_paths = tuple(snapshot_payload["blocked_paths"])
    if set(allowed_paths) & set(blocked_paths):
        _fail("path_scope_conflict", "$.definition_snapshot", "one path is both allowed and blocked")
    try:
        hotfiles = collect_repository_paths(policy.hotfiles, path="$.policy.hotfiles", unique=True)
    except PlanningDefinitionContractError as exc:
        _fail(exc.reason_code, exc.path, exc.detail)
    if not set(hotfiles) <= set(allowed_paths):
        _fail("hotfile_outside_scope", "$.policy.hotfiles", "hotfiles must be allowed Planning paths")

    agent_run_id = _agent_run_id(owner, request_id)
    manifest = ExecutionManifest(
        agent_run_id=agent_run_id,
        owner_scope_ref=owner,
        project_id=project_id,
        roadmap_id=roadmap_id,
        planning_revision=revision,
        planning_content_hash=content_hash,
        definition_snapshot_ref=definition_snapshot_ref,
        normalized_dag=normalized_dag,
        done_contract=done_contract,
        queue_scope=policy.queue_scope,
        supervision_mode=policy.supervision_mode,
        mutation_authority=policy.mutation_authority,
        selected_route=policy.selected_route,
        allowed_paths=allowed_paths,
        blocked_paths=blocked_paths,
        hotfiles=hotfiles,
        max_parallel_activities=policy.max_parallel_activities,
        retry_budget=policy.retry_budget,
        deadline_at=policy.deadline_at,
        start_request_id=request_id,
        manifest_hash="",
    )
    unsigned = manifest.to_payload()
    unsigned.pop("manifest_hash")
    manifest_hash = "sha256:" + hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()
    result = ExecutionManifest(**{**manifest.__dict__, "manifest_hash": manifest_hash})
    encoded = canonical_json(result.to_payload()).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        _fail("manifest_too_large", "$", f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    return result


def normalize_execution_dag(snapshot_dag: Mapping[str, Any]) -> NormalizedDag:
    """Adapt the validated snapshot DAG to the legacy manifest value objects."""

    node_records = snapshot_dag.get("nodes")
    edge_records = snapshot_dag.get("edges")
    gate_records = snapshot_dag.get("gates")
    if not isinstance(node_records, Sequence) or isinstance(node_records, (str, bytes)):
        _fail("invalid_dag", "$.definition_snapshot.normalized_dag.nodes", "nodes must be an array")
    if not isinstance(edge_records, Sequence) or isinstance(edge_records, (str, bytes)):
        _fail("invalid_dag", "$.definition_snapshot.normalized_dag.edges", "edges must be an array")
    if not isinstance(gate_records, Sequence) or isinstance(gate_records, (str, bytes)):
        _fail("invalid_dag", "$.definition_snapshot.normalized_dag.gates", "gates must be an array")

    raw_nodes: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(node_records):
        if not isinstance(item, Mapping):
            _fail("invalid_dag", f"$.definition_snapshot.normalized_dag.nodes[{index}]", "node must be an object")
        node_id = validate_identifier(item.get("node_id"), f"$.definition_snapshot.normalized_dag.nodes[{index}].node_id")
        if node_id in raw_nodes:
            _fail("duplicate_node", f"$.definition_snapshot.normalized_dag.nodes[{index}]", "node id is duplicated")
        raw_nodes[node_id] = item

    edges: list[NormalizedEdge] = []
    for index, item in enumerate(edge_records):
        if not isinstance(item, Mapping):
            _fail("invalid_dag", f"$.definition_snapshot.normalized_dag.edges[{index}]", "edge must be an object")
        source = validate_identifier(item.get("from"), f"$.definition_snapshot.normalized_dag.edges[{index}].from")
        target = validate_identifier(item.get("to"), f"$.definition_snapshot.normalized_dag.edges[{index}].to")
        kind = str(item.get("kind") or "")
        if source not in raw_nodes or target not in raw_nodes:
            _fail("missing_dependency", f"$.definition_snapshot.normalized_dag.edges[{index}]", "edge endpoint is unknown")
        edges.append(NormalizedEdge(source=source, target=target, kind=kind))

    nodes = tuple(
        NormalizedNode(
            node_id=node_id,
            kind=str(raw_nodes[node_id]["kind"]),
            depends_on=tuple(raw_nodes[node_id]["depends_on"]),
            gate_ids=tuple(sorted(raw_nodes[node_id].get("gate_ids", ()))),
            verification_rule_ids=tuple(sorted(raw_nodes[node_id].get("verification_rule_ids", ()))),
        )
        for node_id in sorted(raw_nodes)
    )
    normalized_gates: list[NormalizedGate] = []
    for index, item in enumerate(gate_records):
        if not isinstance(item, Mapping):
            _fail("invalid_dag", f"$.definition_snapshot.normalized_dag.gates[{index}]", "gate must be an object")
        scope = freeze_json(item.get("approval_scope_schema", {}), path=f"$.definition_snapshot.normalized_dag.gates[{index}].approval_scope_schema")
        if not isinstance(scope, FrozenObject):
            _fail("invalid_dag", f"$.definition_snapshot.normalized_dag.gates[{index}]", "approval scope must be an object")
        normalized_gates.append(
            NormalizedGate(
                gate_id=validate_identifier(item.get("gate_id"), f"$.definition_snapshot.normalized_dag.gates[{index}].gate_id"),
                kind=str(item.get("kind") or ""),
                blocks=tuple(sorted(item.get("blocks", ()))),
                decision_needed=str(item.get("decision_needed") or ""),
                safe_default=str(item.get("safe_default") or ""),
                approval_scope_schema=scope,
                required_verification_rule_ids=tuple(sorted(item.get("required_verification_rule_ids", ()))),
            )
        )
    return NormalizedDag(
        nodes=nodes,
        edges=tuple(sorted(edges, key=lambda edge: (edge.source, edge.target, edge.kind))),
        gates=tuple(sorted(normalized_gates, key=lambda gate: gate.gate_id)),
    )


def workflow_id_for(manifest: ExecutionManifest) -> str:
    owner_hash = hashlib.sha256(manifest.owner_scope_ref.encode("utf-8")).hexdigest()[:16]
    return f"odysseus-abc/{owner_hash}/{manifest.agent_run_id}"


def _agent_run_id(owner_scope_ref: str, start_request_id: str) -> str:
    digest = hashlib.sha256(f"{owner_scope_ref}\0{start_request_id}".encode("utf-8")).hexdigest()
    return "arun-" + digest[:32]


def _owner_scope(value: Any) -> str:
    if not isinstance(value, str) or not _OWNER_SCOPE_RE.fullmatch(value) or ".." in value:
        _fail("invalid_owner_scope", "$.owner_scope_ref", "owner scope must be a stable non-path reference")
    return value


def _start_request_id(value: Any) -> str:
    if not isinstance(value, str) or not _START_REQUEST_RE.fullmatch(value):
        _fail("invalid_start_request_id", "$.start_request_id", "start request id is invalid")
    return value


def _fail(code: str, path: str, detail: str) -> None:
    raise ManifestBuildError(code, path, detail)

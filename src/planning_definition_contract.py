"""Fail-closed contract for ``odysseus.planning.definition.v2``.

Planning definitions describe immutable intent. Mutable Agent or Temporal
runtime state is rejected recursively before structural validation begins.
The validator is deliberately stdlib-only so definition validation never
depends on a running service or an optional SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any


PLANNING_DEFINITION_SCHEMA_ID = "odysseus.planning.definition.v2"
CONTENT_HASH_PREFIX = "sha256:"

REVISION_STATES = frozenset(
    {"draft", "in_review", "approved", "superseded", "archived", "tombstoned"}
)
NODE_KINDS = frozenset({"work", "gate", "milestone", "group"})
EDGE_KINDS = frozenset({"depends_on", "blocks", "unlocks"})
GATE_KINDS = frozenset({"design", "operator", "repo", "live", "security", "dependency"})
VERIFICATION_KINDS = frozenset({"static", "test", "integration", "visual", "temporal", "manual"})
FORBIDDEN_EXECUTION_STATES = frozenset(
    {
        "queued",
        "starting",
        "running",
        "waiting",
        "waiting_gate",
        "waiting_signal",
        "paused",
        "blocked",
        "retrying",
        "failed",
        "done",
        "completed",
        "cancelled",
        "timed_out",
        "terminated",
    }
)
RUNTIME_FIELD_DENYLIST = frozenset(
    {
        "agent_run_id",
        "run_id",
        "workflow_id",
        "workflow_run_id",
        "temporal_run_id",
        "history_segment",
        "history_event_id",
        "activity_id",
        "activity_attempt",
        "attempt",
        "max_attempts",
        "retry_count",
        "next_retry_at",
        "heartbeat",
        "heartbeat_at",
        "last_heartbeat_at",
        "heartbeat_age_seconds",
        "heartbeat_timeout_seconds",
        "heartbeat_health",
        "signal",
        "signal_id",
        "update_id",
        "command",
        "command_id",
        "allowed_commands",
        "claim",
        "claim_id",
        "lease_id",
        "lease_revision",
        "lease_expires_at",
        "fencing_token",
        "worker_id",
        "runtime_status",
        "run_progress",
        "waiting_reason",
        "evidence",
        "evidence_receipt",
        "changed_files",
        "commit",
        "commit_id",
        "started_at",
        "completed_at",
    }
)
GATE_RUNTIME_FIELD_DENYLIST = frozenset(
    {"state", "decision", "actor", "decided_at", "expires_at", "evidence_receipt"}
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_STATEISH_KEYS = frozenset({"state", "status", "execution_state", "run_state", "runtime_state"})
_BLOCKED_PATH_PARTS = frozenset({".git", ".env", ".ssh", "id_rsa", "id_dsa", "id_ed25519"})


class PlanningDefinitionContractError(ValueError):
    """A stable, path-addressed Planning definition validation failure."""

    def __init__(self, reason_code: str, path: str, detail: str) -> None:
        self.reason_code = reason_code
        self.path = path
        self.detail = detail
        super().__init__(f"{reason_code} at {path}: {detail}")


@dataclass(frozen=True, slots=True)
class PlanningDefinitionValidationResult:
    """Content-free receipt returned after successful validation."""

    project_id: str
    roadmap_hashes: tuple[tuple[str, int, str], ...]
    schema_id: str = PLANNING_DEFINITION_SCHEMA_ID

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "project_id": self.project_id,
            "roadmap_hashes": [
                {"roadmap_id": roadmap_id, "revision": revision, "content_hash": content_hash}
                for roadmap_id, revision, content_hash in self.roadmap_hashes
            ],
        }


def compute_roadmap_content_hash(roadmap: Mapping[str, Any]) -> str:
    """Return the deterministic SHA-256 hash of a roadmap revision.

    ``content_hash`` is excluded because it stores this digest. Every other
    field, including revision metadata and timestamps, is covered.
    """

    if not isinstance(roadmap, Mapping):
        raise PlanningDefinitionContractError("invalid_type", "$", "roadmap must be an object")
    canonical = deepcopy(dict(roadmap))
    canonical.pop("content_hash", None)
    try:
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlanningDefinitionContractError(
            "non_canonical_value", "$", "roadmap contains a non-JSON value"
        ) from exc
    return CONTENT_HASH_PREFIX + hashlib.sha256(encoded).hexdigest()


def validate_planning_definition(
    payload: Mapping[str, Any],
    *,
    approved_hashes: Mapping[tuple[str, int], str] | None = None,
) -> PlanningDefinitionValidationResult:
    """Validate one canonical project definition and return a bounded receipt.

    ``approved_hashes`` is the immutable store boundary. When supplied, an
    approved ``(roadmap_id, revision)`` must retain the already stored hash.
    """

    document = _mapping(payload, "$")
    _reject_runtime_state(document, "$")
    _strict_fields(document, "$", {"schema_id", "project", "roadmaps"})
    _literal(document["schema_id"], "$.schema_id", {PLANNING_DEFINITION_SCHEMA_ID})

    project = _validate_project(document["project"], "$.project")
    roadmaps = _list(document["roadmaps"], "$.roadmaps", minimum=1)
    validated_roadmaps = [
        _validate_roadmap(roadmap, f"$.roadmaps[{index}]")
        for index, roadmap in enumerate(roadmaps)
    ]
    _validate_project_references(project, validated_roadmaps)

    hashes: list[tuple[str, int, str]] = []
    seen_revisions: set[tuple[str, int]] = set()
    for index, roadmap in enumerate(validated_roadmaps):
        path = f"$.roadmaps[{index}]"
        key = (roadmap["roadmap_id"], roadmap["revision"])
        if key in seen_revisions:
            _fail("duplicate_id", f"{path}.revision", "roadmap revision must be unique")
        seen_revisions.add(key)
        _validate_roadmap_references(roadmap, path)

        calculated_hash = compute_roadmap_content_hash(roadmap)
        if roadmap["content_hash"] != calculated_hash:
            _fail("content_hash_mismatch", f"{path}.content_hash", "hash does not match canonical content")
        if roadmap["revision_state"] == "approved" and approved_hashes is not None:
            approved_hash = approved_hashes.get(key)
            if approved_hash is not None and approved_hash != roadmap["content_hash"]:
                _fail(
                    "approved_revision_immutable",
                    f"{path}.content_hash",
                    "approved revision differs from the stored immutable hash",
                )
        hashes.append((roadmap["roadmap_id"], roadmap["revision"], roadmap["content_hash"]))

    return PlanningDefinitionValidationResult(
        project_id=project["project_id"],
        roadmap_hashes=tuple(sorted(hashes)),
    )


def _validate_project(value: Any, path: str) -> Mapping[str, Any]:
    project = _mapping(value, path)
    _strict_fields(
        project,
        path,
        {
            "project_id",
            "title",
            "objective",
            "scope",
            "constraints",
            "roadmap_refs",
            "latest_approved_revision",
            "draft_refs",
        },
    )
    _identifier(project["project_id"], f"{path}.project_id")
    _text(project["title"], f"{path}.title", maximum=200)
    _text(project["objective"], f"{path}.objective", maximum=4_000)

    scope = _mapping(project["scope"], f"{path}.scope")
    _strict_fields(scope, f"{path}.scope", {"in", "out"})
    _text_list(scope["in"], f"{path}.scope.in")
    _text_list(scope["out"], f"{path}.scope.out")
    _text_list(project["constraints"], f"{path}.constraints")

    roadmap_refs = _list(project["roadmap_refs"], f"{path}.roadmap_refs", minimum=1)
    _unique_identifiers(roadmap_refs, f"{path}.roadmap_refs")

    latest = _mapping(project["latest_approved_revision"], f"{path}.latest_approved_revision")
    for roadmap_id, ref in latest.items():
        _identifier(roadmap_id, f"{path}.latest_approved_revision")
        ref_path = f"{path}.latest_approved_revision.{roadmap_id}"
        record = _mapping(ref, ref_path)
        _strict_fields(record, ref_path, {"revision", "content_hash"})
        _positive_int(record["revision"], f"{ref_path}.revision")
        _content_hash(record["content_hash"], f"{ref_path}.content_hash")

    drafts = _list(project["draft_refs"], f"{path}.draft_refs")
    draft_ids: list[str] = []
    for index, draft in enumerate(drafts):
        draft_path = f"{path}.draft_refs[{index}]"
        record = _mapping(draft, draft_path)
        _strict_fields(record, draft_path, {"draft_id", "roadmap_id", "base_revision", "base_hash"})
        draft_ids.append(_identifier(record["draft_id"], f"{draft_path}.draft_id"))
        _identifier(record["roadmap_id"], f"{draft_path}.roadmap_id")
        _positive_int(record["base_revision"], f"{draft_path}.base_revision")
        _content_hash(record["base_hash"], f"{draft_path}.base_hash")
    _ensure_unique(draft_ids, f"{path}.draft_refs", "draft_id")
    return project


def _validate_roadmap(value: Any, path: str) -> Mapping[str, Any]:
    roadmap = _mapping(value, path)
    _strict_fields(
        roadmap,
        path,
        {
            "roadmap_id",
            "project_id",
            "revision",
            "content_hash",
            "revision_state",
            "title",
            "objective",
            "assumptions",
            "constraints",
            "nodes",
            "edges",
            "gates",
            "done_contract",
            "source_refs",
            "created_at",
            "updated_at",
        },
    )
    _identifier(roadmap["roadmap_id"], f"{path}.roadmap_id")
    _identifier(roadmap["project_id"], f"{path}.project_id")
    _positive_int(roadmap["revision"], f"{path}.revision")
    _content_hash(roadmap["content_hash"], f"{path}.content_hash")
    _literal(roadmap["revision_state"], f"{path}.revision_state", REVISION_STATES)
    _text(roadmap["title"], f"{path}.title", maximum=200)
    _text(roadmap["objective"], f"{path}.objective", maximum=4_000)
    _text_list(roadmap["assumptions"], f"{path}.assumptions")
    _text_list(roadmap["constraints"], f"{path}.constraints")
    _text_list(roadmap["source_refs"], f"{path}.source_refs")
    _timestamp(roadmap["created_at"], f"{path}.created_at")
    _timestamp(roadmap["updated_at"], f"{path}.updated_at")

    nodes = _list(roadmap["nodes"], f"{path}.nodes", minimum=1)
    for index, node in enumerate(nodes):
        _validate_node(node, f"{path}.nodes[{index}]")
    edges = _list(roadmap["edges"], f"{path}.edges")
    for index, edge in enumerate(edges):
        _validate_edge(edge, f"{path}.edges[{index}]")
    gates = _list(roadmap["gates"], f"{path}.gates")
    for index, gate in enumerate(gates):
        _validate_gate(gate, f"{path}.gates[{index}]")
    _validate_done_contract(roadmap["done_contract"], f"{path}.done_contract")
    return roadmap


def _validate_node(value: Any, path: str) -> None:
    node = _mapping(value, path)
    _strict_fields(
        node,
        path,
        {
            "node_id",
            "kind",
            "title",
            "objective",
            "depends_on",
            "gate_ids",
            "deliverables",
            "allowed_paths",
            "blocked_paths",
            "capability_requirements",
            "verification_rule_ids",
        },
    )
    _identifier(node["node_id"], f"{path}.node_id")
    _literal(node["kind"], f"{path}.kind", NODE_KINDS)
    _text(node["title"], f"{path}.title", maximum=200)
    _text(node["objective"], f"{path}.objective", maximum=4_000)
    _unique_identifiers(node["depends_on"], f"{path}.depends_on")
    _unique_identifiers(node["gate_ids"], f"{path}.gate_ids")
    _text_list(node["deliverables"], f"{path}.deliverables")
    _repo_path_list(node["allowed_paths"], f"{path}.allowed_paths")
    _repo_path_list(node["blocked_paths"], f"{path}.blocked_paths")
    _text_list(node["capability_requirements"], f"{path}.capability_requirements")
    _unique_identifiers(node["verification_rule_ids"], f"{path}.verification_rule_ids")


def _validate_edge(value: Any, path: str) -> None:
    edge = _mapping(value, path)
    _strict_fields(edge, path, {"from", "to", "kind"})
    _identifier(edge["from"], f"{path}.from")
    _identifier(edge["to"], f"{path}.to")
    _literal(edge["kind"], f"{path}.kind", EDGE_KINDS)


def _validate_gate(value: Any, path: str) -> None:
    gate = _mapping(value, path)
    _reject_gate_runtime_fields(gate, path)
    _strict_fields(
        gate,
        path,
        {
            "gate_id",
            "kind",
            "title",
            "blocks",
            "decision_needed",
            "safe_default",
            "approval_scope_schema",
            "required_verification_rule_ids",
        },
    )
    _identifier(gate["gate_id"], f"{path}.gate_id")
    _literal(gate["kind"], f"{path}.kind", GATE_KINDS)
    _text(gate["title"], f"{path}.title", maximum=200)
    _unique_identifiers(gate["blocks"], f"{path}.blocks", minimum=1)
    _text(gate["decision_needed"], f"{path}.decision_needed", maximum=4_000)
    _text(gate["safe_default"], f"{path}.safe_default", maximum=4_000)
    _mapping(gate["approval_scope_schema"], f"{path}.approval_scope_schema")
    _unique_identifiers(
        gate["required_verification_rule_ids"],
        f"{path}.required_verification_rule_ids",
    )


def _validate_done_contract(value: Any, path: str) -> None:
    contract = _mapping(value, path)
    _strict_fields(
        contract,
        path,
        {"required_node_ids", "required_gate_ids", "verification_rules", "completion_rule"},
    )
    _unique_identifiers(contract["required_node_ids"], f"{path}.required_node_ids", minimum=1)
    _unique_identifiers(contract["required_gate_ids"], f"{path}.required_gate_ids")
    rules = _list(contract["verification_rules"], f"{path}.verification_rules", minimum=1)
    for index, rule in enumerate(rules):
        rule_path = f"{path}.verification_rules[{index}]"
        record = _mapping(rule, rule_path)
        _strict_fields(record, rule_path, {"rule_id", "kind", "description"})
        _identifier(record["rule_id"], f"{rule_path}.rule_id")
        _literal(record["kind"], f"{rule_path}.kind", VERIFICATION_KINDS)
        _text(record["description"], f"{rule_path}.description", maximum=2_000)
    _literal(contract["completion_rule"], f"{path}.completion_rule", {"all_required_nodes_and_gates"})


def _validate_project_references(
    project: Mapping[str, Any], roadmaps: list[Mapping[str, Any]]
) -> None:
    project_id = project["project_id"]
    roadmap_ids = {roadmap["roadmap_id"] for roadmap in roadmaps}
    refs = set(project["roadmap_refs"])
    missing = sorted(refs - roadmap_ids)
    if missing:
        _fail("missing_reference", "$.project.roadmap_refs", f"unknown roadmap_id {missing[0]}")
    unreferenced = sorted(roadmap_ids - refs)
    if unreferenced:
        _fail("unreferenced_roadmap", "$.roadmaps", f"roadmap_id {unreferenced[0]} is not referenced")

    by_revision = {
        (roadmap["roadmap_id"], roadmap["revision"]): roadmap
        for roadmap in roadmaps
    }
    for index, roadmap in enumerate(roadmaps):
        if roadmap["project_id"] != project_id:
            _fail(
                "missing_reference",
                f"$.roadmaps[{index}].project_id",
                "roadmap project_id does not match the enclosing project",
            )

    for roadmap_id, ref in project["latest_approved_revision"].items():
        key = (roadmap_id, ref["revision"])
        target = by_revision.get(key)
        ref_path = f"$.project.latest_approved_revision.{roadmap_id}"
        if target is None or roadmap_id not in refs:
            _fail("missing_reference", ref_path, "approved roadmap revision does not exist")
        if target["revision_state"] != "approved" or target["content_hash"] != ref["content_hash"]:
            _fail("invalid_approved_reference", ref_path, "reference must match an approved revision and hash")

    for index, draft in enumerate(project["draft_refs"]):
        key = (draft["roadmap_id"], draft["base_revision"])
        target = by_revision.get(key)
        path = f"$.project.draft_refs[{index}]"
        if target is None or target["content_hash"] != draft["base_hash"]:
            _fail("missing_reference", path, "draft base revision and hash must exist")


def _validate_roadmap_references(roadmap: Mapping[str, Any], path: str) -> None:
    nodes = roadmap["nodes"]
    gates = roadmap["gates"]
    rules = roadmap["done_contract"]["verification_rules"]

    node_ids = [node["node_id"] for node in nodes]
    gate_ids = [gate["gate_id"] for gate in gates]
    rule_ids = [rule["rule_id"] for rule in rules]
    _ensure_unique(node_ids, f"{path}.nodes", "node_id")
    _ensure_unique(gate_ids, f"{path}.gates", "gate_id")
    _ensure_unique(rule_ids, f"{path}.done_contract.verification_rules", "rule_id")
    node_set = set(node_ids)
    gate_set = set(gate_ids)
    rule_set = set(rule_ids)

    dependencies: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for index, node in enumerate(nodes):
        node_path = f"{path}.nodes[{index}]"
        for dependency in node["depends_on"]:
            if dependency not in node_set:
                _fail("missing_reference", f"{node_path}.depends_on", f"unknown node_id {dependency}")
            dependencies[node["node_id"]].add(dependency)
        for gate_id in node["gate_ids"]:
            if gate_id not in gate_set:
                _fail("missing_reference", f"{node_path}.gate_ids", f"unknown gate_id {gate_id}")
        for rule_id in node["verification_rule_ids"]:
            if rule_id not in rule_set:
                _fail(
                    "missing_reference",
                    f"{node_path}.verification_rule_ids",
                    f"unknown verification rule {rule_id}",
                )

    edge_keys: list[tuple[str, str, str]] = []
    for index, edge in enumerate(roadmap["edges"]):
        edge_path = f"{path}.edges[{index}]"
        if edge["from"] not in node_set or edge["to"] not in node_set:
            _fail("missing_reference", edge_path, "edge endpoints must reference existing nodes")
        edge_keys.append((edge["from"], edge["to"], edge["kind"]))
        if edge["kind"] == "depends_on":
            dependencies[edge["from"]].add(edge["to"])
    _ensure_unique(edge_keys, f"{path}.edges", "edge")
    _reject_dependency_cycles(dependencies, f"{path}.nodes")

    for index, gate in enumerate(gates):
        gate_path = f"{path}.gates[{index}]"
        for target in gate["blocks"]:
            if target not in node_set:
                _fail("invalid_gate_target", f"{gate_path}.blocks", f"unknown node_id {target}")
        for rule_id in gate["required_verification_rule_ids"]:
            if rule_id not in rule_set:
                _fail(
                    "missing_reference",
                    f"{gate_path}.required_verification_rule_ids",
                    f"unknown verification rule {rule_id}",
                )

    done = roadmap["done_contract"]
    for node_id in done["required_node_ids"]:
        if node_id not in node_set:
            _fail(
                "invalid_completion_reference",
                f"{path}.done_contract.required_node_ids",
                f"unknown node_id {node_id}",
            )
    for gate_id in done["required_gate_ids"]:
        if gate_id not in gate_set:
            _fail(
                "invalid_completion_reference",
                f"{path}.done_contract.required_gate_ids",
                f"unknown gate_id {gate_id}",
            )


def _reject_dependency_cycles(graph: Mapping[str, set[str]], path: str) -> None:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in active:
            _fail("dependency_cycle", path, f"cycle contains node_id {node_id}")
        if node_id in visited:
            return
        active.add(node_id)
        for dependency in sorted(graph[node_id]):
            visit(dependency)
        active.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(graph):
        visit(node_id)


def _reject_runtime_state(value: Any, path: str, *, state_context: bool = False) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower()
            child_path = f"{path}.{key}"
            if normalized in RUNTIME_FIELD_DENYLIST:
                _fail("runtime_field_forbidden", child_path, f"field {key} belongs to Agent runtime")
            _reject_runtime_state(
                nested,
                child_path,
                state_context=state_context or normalized in _STATEISH_KEYS,
            )
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_runtime_state(nested, f"{path}[{index}]", state_context=state_context)
        return
    if state_context and isinstance(value, str) and value.lower() in FORBIDDEN_EXECUTION_STATES:
        _fail("execution_state_forbidden", path, f"execution state {value} belongs to Agent runtime")


def _reject_gate_runtime_fields(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in GATE_RUNTIME_FIELD_DENYLIST:
                _fail("runtime_field_forbidden", child_path, "gate runtime decision fields are forbidden")
            _reject_gate_runtime_fields(nested, child_path)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_gate_runtime_fields(nested, f"{path}[{index}]")


def _strict_fields(
    value: Mapping[str, Any],
    path: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = sorted(required - set(value))
    if missing:
        _fail("missing_field", path, f"missing required field {missing[0]}")
    unknown = sorted(set(value) - required - optional)
    if unknown:
        _fail("unknown_field", f"{path}.{unknown[0]}", "field is not part of definition v2")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid_type", path, "expected object")
    return value


def _list(value: Any, path: str, *, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list):
        _fail("invalid_type", path, "expected array")
    if len(value) < minimum:
        _fail("invalid_value", path, f"expected at least {minimum} item(s)")
    return value


def _text(value: Any, path: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("invalid_type", path, "expected non-empty string")
    if len(value) > maximum or any(ord(character) < 32 and character not in "\n\t" for character in value):
        _fail("invalid_value", path, f"string must be at most {maximum} safe characters")
    return value


def _text_list(value: Any, path: str) -> list[str]:
    items = _list(value, path)
    normalized = [_text(item, f"{path}[{index}]", maximum=4_000) for index, item in enumerate(items)]
    _ensure_unique(normalized, path, "value")
    return normalized


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _fail("invalid_value", path, "expected a stable identifier")
    return value


def _unique_identifiers(value: Any, path: str, *, minimum: int = 0) -> list[str]:
    items = _list(value, path, minimum=minimum)
    identifiers = [_identifier(item, f"{path}[{index}]") for index, item in enumerate(items)]
    _ensure_unique(identifiers, path, "identifier")
    return identifiers


def _repo_path_list(value: Any, path: str) -> list[str]:
    items = _list(value, path)
    paths = [_repo_path(item, f"{path}[{index}]") for index, item in enumerate(items)]
    _ensure_unique(paths, path, "path")
    return paths


def _repo_path(value: Any, path: str) -> str:
    text = _text(value, path, maximum=240)
    if "\\" in text or text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text):
        _fail("invalid_repo_path", path, "path must be repository-relative with forward slashes")
    parts = text.split("/")
    if any(part in {"", ".", ".."} or part.lower() in _BLOCKED_PATH_PARTS for part in parts):
        _fail("invalid_repo_path", path, "path contains an empty, traversal or private segment")
    return text


def _positive_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail("invalid_type", path, "expected positive integer")
    return value


def _content_hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        _fail("invalid_value", path, "expected sha256:<64 lowercase hex characters>")
    return value


def _timestamp(value: Any, path: str) -> str:
    text = _text(value, path, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanningDefinitionContractError("invalid_value", path, "expected ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        _fail("invalid_value", path, "timestamp must include a timezone")
    return text


def _literal(value: Any, path: str, choices: set[str] | frozenset[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        _fail("invalid_literal", path, f"expected one of {sorted(choices)}")
    return value


def _ensure_unique(values: list[Any], path: str, label: str) -> None:
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            _fail("duplicate_id", path, f"duplicate {label} {value}")
        seen.add(value)


def _fail(reason_code: str, path: str, detail: str) -> None:
    raise PlanningDefinitionContractError(reason_code, path, detail)


__all__ = [
    "CONTENT_HASH_PREFIX",
    "FORBIDDEN_EXECUTION_STATES",
    "GATE_RUNTIME_FIELD_DENYLIST",
    "PLANNING_DEFINITION_SCHEMA_ID",
    "REVISION_STATES",
    "RUNTIME_FIELD_DENYLIST",
    "PlanningDefinitionContractError",
    "PlanningDefinitionValidationResult",
    "compute_roadmap_content_hash",
    "validate_planning_definition",
]

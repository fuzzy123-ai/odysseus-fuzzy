"""Immutable product contracts for Temporal Light execution starts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping, Sequence, TypeAlias


EXECUTION_MANIFEST_SCHEMA_ID = "odysseus.abc.execution_manifest.v1"
RUN_START_RECEIPT_SCHEMA_ID = "odysseus.abc.run_start_receipt.v1"
MAX_MANIFEST_BYTES = 262_144
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_WORKFLOW_ID_RE = re.compile(r"^odysseus-abc/[0-9a-f]{16}/arun-[0-9a-f]{32}$")
_QUEUE_SCOPES = frozenset({"mvp_10", "open_work", "post_mvp", "named_roadmap"})
_SUPERVISION_MODES = frozenset({"interactive", "unattended_long_run"})
_MUTATION_AUTHORITIES = frozenset(
    {"safe_offline", "repo_only", "needs_live_go", "needs_design", "blocked"}
)
_SENSITIVE_KEY_TOKENS = frozenset(
    {"secret", "password", "credential", "api_key", "access_token", "private_key", "raw_output"}
)


class ExecutionContractError(ValueError):
    def __init__(self, code: str, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code} at {path}: {detail}")


@dataclass(frozen=True)
class FrozenArray:
    items: tuple["FrozenValue", ...]

    def to_value(self) -> list[Any]:
        return [thaw_json(item) for item in self.items]


@dataclass(frozen=True)
class FrozenObject:
    items: tuple[tuple[str, "FrozenValue"], ...]

    def to_value(self) -> dict[str, Any]:
        return {key: thaw_json(value) for key, value in self.items}


FrozenScalar: TypeAlias = None | bool | int | str
FrozenValue: TypeAlias = FrozenScalar | FrozenArray | FrozenObject


def freeze_json(value: Any, *, path: str = "$") -> FrozenValue:
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str) and len(value) > 16_384:
            _fail("value_too_large", path, "string exceeds 16384 characters")
        return value
    if isinstance(value, Mapping):
        items: list[tuple[str, FrozenValue]] = []
        keys = list(value)
        if any(not isinstance(key, str) for key in keys):
            _fail("invalid_json_key", path, "object keys must be bounded strings")
        for key in sorted(keys):
            if not isinstance(key, str) or not key or len(key) > 128:
                _fail("invalid_json_key", path, "object keys must be bounded strings")
            lowered = key.lower()
            if any(token in lowered for token in _SENSITIVE_KEY_TOKENS):
                _fail("sensitive_field_forbidden", f"{path}.{key}", "sensitive field cannot enter history")
            items.append((key, freeze_json(value[key], path=f"{path}.{key}")))
        return FrozenObject(tuple(items))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 2_000:
            _fail("value_too_large", path, "array exceeds 2000 items")
        return FrozenArray(
            tuple(freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value))
        )
    _fail("invalid_json_value", path, "value must be deterministic JSON without floats or bytes")


def thaw_json(value: FrozenValue) -> Any:
    if isinstance(value, FrozenObject):
        return value.to_value()
    if isinstance(value, FrozenArray):
        return value.to_value()
    return value


@dataclass(frozen=True)
class ExecutionPolicy:
    queue_scope: str
    supervision_mode: str
    mutation_authority: str
    selected_route: FrozenObject
    hotfiles: tuple[str, ...]
    max_parallel_activities: int
    retry_budget: int
    deadline_at: str

    @classmethod
    def create(
        cls,
        *,
        queue_scope: str,
        supervision_mode: str,
        mutation_authority: str,
        selected_route: Mapping[str, Any],
        hotfiles: Sequence[str] = (),
        max_parallel_activities: int = 1,
        retry_budget: int = 2,
        deadline_at: str,
    ) -> "ExecutionPolicy":
        queue = _literal(queue_scope, _QUEUE_SCOPES, "$.queue_scope")
        supervision = _literal(supervision_mode, _SUPERVISION_MODES, "$.supervision_mode")
        authority = _literal(mutation_authority, _MUTATION_AUTHORITIES, "$.mutation_authority")
        frozen_route = freeze_json(selected_route, path="$.selected_route")
        if not isinstance(frozen_route, FrozenObject):
            _fail("invalid_route", "$.selected_route", "selected route must be an object")
        route = frozen_route.to_value()
        if route.get("entrypoint") != "/abc":
            _fail("invalid_route", "$.selected_route.entrypoint", "route must originate at /abc")
        if not isinstance(max_parallel_activities, int) or isinstance(max_parallel_activities, bool) or not 1 <= max_parallel_activities <= 3:
            _fail("invalid_parallelism", "$.max_parallel_activities", "parallelism must be 1 through 3")
        if not isinstance(retry_budget, int) or isinstance(retry_budget, bool) or not 0 <= retry_budget <= 2:
            _fail("invalid_retry_budget", "$.retry_budget", "retry budget must be 0 through 2")
        deadline = _timestamp(deadline_at, "$.deadline_at")
        return cls(
            queue_scope=queue,
            supervision_mode=supervision,
            mutation_authority=authority,
            selected_route=frozen_route,
            hotfiles=tuple(hotfiles),
            max_parallel_activities=max_parallel_activities,
            retry_budget=retry_budget,
            deadline_at=deadline,
        )


@dataclass(frozen=True)
class NormalizedNode:
    node_id: str
    kind: str
    depends_on: tuple[str, ...]
    gate_ids: tuple[str, ...]
    verification_rule_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "depends_on": list(self.depends_on),
            "gate_ids": list(self.gate_ids),
            "verification_rule_ids": list(self.verification_rule_ids),
        }


@dataclass(frozen=True)
class NormalizedEdge:
    source: str
    target: str
    kind: str

    def to_payload(self) -> dict[str, str]:
        return {"from": self.source, "to": self.target, "kind": self.kind}


@dataclass(frozen=True)
class NormalizedGate:
    gate_id: str
    kind: str
    blocks: tuple[str, ...]
    decision_needed: str
    safe_default: str
    approval_scope_schema: FrozenObject
    required_verification_rule_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "kind": self.kind,
            "blocks": list(self.blocks),
            "decision_needed": self.decision_needed,
            "safe_default": self.safe_default,
            "approval_scope_schema": self.approval_scope_schema.to_value(),
            "required_verification_rule_ids": list(self.required_verification_rule_ids),
        }


@dataclass(frozen=True)
class NormalizedDag:
    nodes: tuple[NormalizedNode, ...]
    edges: tuple[NormalizedEdge, ...]
    gates: tuple[NormalizedGate, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_payload() for node in self.nodes],
            "edges": [edge.to_payload() for edge in self.edges],
            "gates": [gate.to_payload() for gate in self.gates],
        }


@dataclass(frozen=True)
class DefinitionSnapshotReference:
    schema_id: str
    project_id: str
    roadmap_id: str
    planning_revision: int
    planning_content_hash: str
    snapshot_hash: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "project_id": self.project_id,
            "roadmap_id": self.roadmap_id,
            "planning_revision": self.planning_revision,
            "planning_content_hash": self.planning_content_hash,
            "snapshot_hash": self.snapshot_hash,
        }


@dataclass(frozen=True)
class ExecutionManifest:
    agent_run_id: str
    owner_scope_ref: str
    project_id: str
    roadmap_id: str
    planning_revision: int
    planning_content_hash: str
    definition_snapshot_ref: DefinitionSnapshotReference
    normalized_dag: NormalizedDag
    done_contract: FrozenObject
    queue_scope: str
    supervision_mode: str
    mutation_authority: str
    selected_route: FrozenObject
    allowed_paths: tuple[str, ...]
    blocked_paths: tuple[str, ...]
    hotfiles: tuple[str, ...]
    max_parallel_activities: int
    retry_budget: int
    deadline_at: str
    start_request_id: str
    manifest_hash: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_id": EXECUTION_MANIFEST_SCHEMA_ID,
            "agent_run_id": self.agent_run_id,
            "owner_scope_ref": self.owner_scope_ref,
            "project_id": self.project_id,
            "roadmap_id": self.roadmap_id,
            "planning_revision": self.planning_revision,
            "planning_content_hash": self.planning_content_hash,
            "definition_snapshot_ref": self.definition_snapshot_ref.to_payload(),
            "normalized_dag": self.normalized_dag.to_payload(),
            "done_contract": self.done_contract.to_value(),
            "queue_scope": self.queue_scope,
            "supervision_mode": self.supervision_mode,
            "mutation_authority": self.mutation_authority,
            "selected_route": self.selected_route.to_value(),
            "allowed_paths": list(self.allowed_paths),
            "blocked_paths": list(self.blocked_paths),
            "hotfiles": list(self.hotfiles),
            "max_parallel_activities": self.max_parallel_activities,
            "retry_budget": self.retry_budget,
            "deadline_at": self.deadline_at,
            "start_request_id": self.start_request_id,
            "manifest_hash": self.manifest_hash,
        }


@dataclass(frozen=True)
class RunStartReceipt:
    start_request_id: str
    agent_run_id: str
    workflow_id: str
    workflow_run_id: str
    manifest_hash: str
    planning_ref: FrozenObject
    state: str
    task_queue: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_id": RUN_START_RECEIPT_SCHEMA_ID,
            "start_request_id": self.start_request_id,
            "agent_run_id": self.agent_run_id,
            "workflow_id": self.workflow_id,
            "workflow_run_id": self.workflow_run_id,
            "manifest_hash": self.manifest_hash,
            "planning_ref": self.planning_ref.to_value(),
            "state": self.state,
            "task_queue": self.task_queue,
        }

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "RunStartReceipt":
        required = {
            "schema_id", "start_request_id", "agent_run_id", "workflow_id",
            "workflow_run_id", "manifest_hash", "planning_ref", "state", "task_queue",
        }
        if set(value) != required or value.get("schema_id") != RUN_START_RECEIPT_SCHEMA_ID:
            _fail("invalid_receipt", "$", "run-start receipt fields are invalid")
        planning_ref = freeze_json(value["planning_ref"], path="$.planning_ref")
        if not isinstance(planning_ref, FrozenObject):
            _fail("invalid_receipt", "$.planning_ref", "planning reference must be an object")
        planning_value = planning_ref.to_value()
        if set(planning_value) != {"project_id", "roadmap_id", "revision", "content_hash"}:
            _fail("invalid_receipt", "$.planning_ref", "planning reference fields are invalid")
        validate_identifier(planning_value["project_id"], "$.planning_ref.project_id")
        validate_identifier(planning_value["roadmap_id"], "$.planning_ref.roadmap_id")
        if (
            isinstance(planning_value["revision"], bool)
            or not isinstance(planning_value["revision"], int)
            or planning_value["revision"] < 1
        ):
            _fail("invalid_receipt", "$.planning_ref.revision", "revision must be positive")
        _hash(planning_value["content_hash"], "$.planning_ref.content_hash")
        if value["state"] != "started":
            _fail("invalid_receipt", "$.state", "receipt state must be started")
        _hash(value["manifest_hash"], "$.manifest_hash")
        if not isinstance(value["workflow_id"], str) or not _WORKFLOW_ID_RE.fullmatch(value["workflow_id"]):
            _fail("invalid_receipt", "$.workflow_id", "workflow id is invalid")
        for field in ("start_request_id", "agent_run_id", "workflow_run_id", "task_queue"):
            if not isinstance(value[field], str) or not value[field]:
                _fail("invalid_receipt", f"$.{field}", "field must be a non-empty string")
        if not re.fullmatch(r"arun-[0-9a-f]{32}", value["agent_run_id"]):
            _fail("invalid_receipt", "$.agent_run_id", "agent run id is invalid")
        return cls(
            start_request_id=value["start_request_id"],
            agent_run_id=value["agent_run_id"],
            workflow_id=value["workflow_id"],
            workflow_run_id=value["workflow_run_id"],
            manifest_hash=value["manifest_hash"],
            planning_ref=planning_ref,
            state=value["state"],
            task_queue=value["task_queue"],
        )


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def validate_identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _fail("invalid_identifier", path, "expected stable identifier")
    return value


def validate_hash(value: Any, path: str) -> str:
    return _hash(value, path)


def _hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        _fail("invalid_hash", path, "expected sha256 lowercase hexadecimal")
    return value


def _literal(value: Any, allowed: frozenset[str], path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail("invalid_literal", path, f"expected one of {sorted(allowed)}")
    return value


def _timestamp(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        _fail("invalid_timestamp", path, "expected bounded ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionContractError("invalid_timestamp", path, "timestamp is invalid") from exc
    if parsed.tzinfo is None:
        _fail("invalid_timestamp", path, "timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _fail(code: str, path: str, detail: str) -> None:
    raise ExecutionContractError(code, path, detail)

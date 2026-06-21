"""PlanRuntime loader for the structured Odysseus roadmap.

This is the first runtime-facing wrapper around the canonical roadmap JSON.
It keeps the roadmap richer than PlanGraph while still projecting a safe
PlanGraph-compatible view for existing orchestration components.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable

from src.plan_graph_store import AgentPath, PlanEdge, PlanGraph, PlanNode


_MAX_ID = 96
_MAX_TEXT = 240
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_DEFAULT_ROADMAP_PATH = "specs/roadmaps/odysseus-multiagent-roadmap.v1.json"
_PLAN_STATUS_DEFAULTS = {
    "active_candidate": "running",
    "planned": "pending",
    "active": "running",
    "done": "done",
    "blocked": "blocked",
    "deferred": "skipped",
    "partial": "handoff",
    "research": "pending",
}
_NON_CLAIMABLE_STATUSES = {"done", "blocked", "deferred", "research"}


class PlanRuntimeError(ValueError):
    """Raised when structured roadmap runtime state is invalid."""


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise PlanRuntimeError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise PlanRuntimeError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise PlanRuntimeError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise PlanRuntimeError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_token(value: Any, *, field_name: str) -> str:
    return _normalize_slug(value, field_name=field_name).replace("-", "_")


def _normalize_repo_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise PlanRuntimeError(f"{field_name} must not be empty")
    if "\\" in raw:
        raise PlanRuntimeError(f"{field_name} must use forward slashes only")
    lowered = raw.lower()
    if lowered.startswith("/") or lowered.startswith("./") or re.match(r"^[a-z]:", lowered):
        raise PlanRuntimeError(f"{field_name} must be repo-relative")
    path_part = raw.split(":", 1)[0]
    parts = PurePosixPath(path_part).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise PlanRuntimeError(f"{field_name} must not contain traversal segments")
    return raw


def _normalize_path_list(values: Iterable[Any], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _normalize_repo_path(value, field_name=field_name)
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    if not normalized and not allow_empty:
        raise PlanRuntimeError(f"{field_name} must not be empty")
    return tuple(sorted(normalized))


def _normalize_text_list(values: Iterable[Any], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value, field_name=field_name, allow_empty=True)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    if not normalized and not allow_empty:
        raise PlanRuntimeError(f"{field_name} must not be empty")
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class PlanRuntimeNode:
    node_id: str
    title: str
    kind: str
    priority_rank: int
    horizon: str
    target_version: str
    status: str
    depends_on: tuple[str, ...]
    unlocks: tuple[str, ...]
    gates: tuple[str, ...]
    source_refs: tuple[str, ...]
    deliverables: tuple[str, ...]
    completion_status: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PlanRuntimeNode":
        if not isinstance(payload, dict):
            raise PlanRuntimeError("graph_nodes items must be objects")
        priority = payload.get("priority_rank")
        if not isinstance(priority, int) or priority < 1:
            raise PlanRuntimeError("priority_rank must be a positive integer")
        completion = payload.get("completion_state", {})
        if completion and not isinstance(completion, dict):
            raise PlanRuntimeError("completion_state must be an object")
        return cls(
            node_id=_normalize_slug(_required(payload, "id"), field_name="node_id"),
            title=_normalize_text(_required(payload, "title"), field_name="title", allow_empty=False),
            kind=_normalize_slug(_required(payload, "kind"), field_name="kind"),
            priority_rank=priority,
            horizon=_normalize_slug(_required(payload, "horizon"), field_name="horizon"),
            target_version=_normalize_text(_required(payload, "target_version"), field_name="target_version", allow_empty=False),
            status=_normalize_token(_required(payload, "status"), field_name="status"),
            depends_on=tuple(_normalize_slug(item, field_name="depends_on") for item in _list(payload.get("depends_on", []), field_name="depends_on")),
            unlocks=tuple(_normalize_slug(item, field_name="unlocks") for item in _list(payload.get("unlocks", []), field_name="unlocks")),
            gates=_normalize_text_list(_list(payload.get("gates", []), field_name="gates"), field_name="gates", allow_empty=True),
            source_refs=_normalize_path_list(_list(payload.get("source_refs", []), field_name="source_refs"), field_name="source_ref", allow_empty=True),
            deliverables=_normalize_text_list(
                _list(payload.get("deliverables", []), field_name="deliverables"),
                field_name="deliverables",
                allow_empty=True,
            ),
            completion_status=_normalize_token(completion.get("status", ""), field_name="completion_status") if completion else "",
        )

    @property
    def is_live_done(self) -> bool:
        return self.status == "done" and self.completion_status == "live_installed"


@dataclass(frozen=True, slots=True)
class PlanRuntimeState:
    plan_id: str
    title: str
    source_of_truth: str
    recommended_active_node: str
    version_horizons: tuple[str, ...]
    nodes: tuple[PlanRuntimeNode, ...]
    status_mapping: dict[str, str]
    roadmap_path: str = _DEFAULT_ROADMAP_PATH

    @classmethod
    def load_json(cls, path: str | Path) -> "PlanRuntimeState":
        target = Path(path)
        return cls.from_dict(json.loads(target.read_text(encoding="utf-8")), roadmap_path=str(target).replace("\\", "/"))

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, roadmap_path: str = _DEFAULT_ROADMAP_PATH) -> "PlanRuntimeState":
        if not isinstance(payload, dict):
            raise PlanRuntimeError("payload must be a dict")
        format_decision = _dict(payload.get("format_decision"), field_name="format_decision")
        source_of_truth = _normalize_slug(format_decision.get("source_of_truth", ""), field_name="source_of_truth")
        if source_of_truth != "json":
            raise PlanRuntimeError("format_decision.source_of_truth must be json")
        horizons = tuple(
            _normalize_slug(_required(horizon, "id"), field_name="horizon_id")
            for horizon in _list(payload.get("version_horizons"), field_name="version_horizons")
        )
        if not horizons:
            raise PlanRuntimeError("version_horizons must not be empty")
        nodes = tuple(
            sorted(
                (PlanRuntimeNode.from_dict(node) for node in _list(payload.get("graph_nodes"), field_name="graph_nodes")),
                key=lambda node: (node.priority_rank, node.node_id),
            )
        )
        if not nodes:
            raise PlanRuntimeError("graph_nodes must not be empty")
        node_ids = {node.node_id for node in nodes}
        if len(node_ids) != len(nodes):
            raise PlanRuntimeError("graph_nodes ids must be unique")
        missing_horizons = sorted({node.horizon for node in nodes} - set(horizons))
        if missing_horizons:
            raise PlanRuntimeError(f"nodes reference unknown horizons: {', '.join(missing_horizons)}")
        missing_deps = sorted({dep for node in nodes for dep in node.depends_on} - node_ids)
        if missing_deps:
            raise PlanRuntimeError(f"nodes depend on unknown nodes: {', '.join(missing_deps)}")
        recommended = _normalize_slug(_required(payload, "recommended_active_node"), field_name="recommended_active_node")
        if recommended not in node_ids:
            raise PlanRuntimeError("recommended_active_node must reference a graph node")
        projection = _dict(payload.get("plan_graph_projection", {}), field_name="plan_graph_projection")
        mapping = dict(_PLAN_STATUS_DEFAULTS)
        mapping.update(
            {
                _normalize_token(key, field_name="status_mapping_key"): _normalize_token(value, field_name="status_mapping_value")
                for key, value in _dict(projection.get("status_mapping", {}), field_name="status_mapping").items()
            }
        )
        return cls(
            plan_id=_normalize_slug(_required(payload, "plan_id"), field_name="plan_id"),
            title=_normalize_text(_required(payload, "title"), field_name="title", allow_empty=False),
            source_of_truth=source_of_truth,
            recommended_active_node=recommended,
            version_horizons=horizons,
            nodes=nodes,
            status_mapping=mapping,
            roadmap_path=_normalize_repo_path(roadmap_path, field_name="roadmap_path"),
        )

    def node_map(self) -> dict[str, PlanRuntimeNode]:
        return {node.node_id: node for node in self.nodes}

    def claimable_nodes(self) -> tuple[PlanRuntimeNode, ...]:
        node_map = self.node_map()
        claimable = [
            node
            for node in self.nodes
            if node.status not in _NON_CLAIMABLE_STATUSES
            and all(node_map[dep].is_live_done for dep in node.depends_on)
        ]
        return tuple(sorted(claimable, key=lambda node: (node.priority_rank, node.node_id)))

    def next_claimable_node_id(self) -> str:
        claimable = self.claimable_nodes()
        return claimable[0].node_id if claimable else ""

    def to_plan_graph(self) -> PlanGraph:
        nodes = [
            PlanNode.create(
                node_id=node.node_id,
                slice_id=node.node_id,
                title=node.title,
                owner=node.kind,
                status=self.status_mapping.get(node.status, "pending"),
                allowed_files=node.source_refs or (self.roadmap_path,),
                blocked_files=[],
                evidence_required=node.gates,
            )
            for node in self.nodes
        ]
        edges = [
            PlanEdge.create(from_node=dep, to_node=node.node_id, kind="depends_on")
            for node in self.nodes
            for dep in node.depends_on
        ]
        active_node = self.recommended_active_node if self.recommended_active_node in self.node_map() else self.nodes[0].node_id
        return PlanGraph.create(
            plan_id=self.plan_id,
            title=self.title,
            nodes=nodes,
            edges=edges,
            agent_paths=[AgentPath.create(agent_id="charlie", node_ids=[active_node], status="running")],
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "source_of_truth": self.source_of_truth,
            "recommended_active_node": self.recommended_active_node,
            "node_count": len(self.nodes),
            "horizon_count": len(self.version_horizons),
            "claimable_node_ids": tuple(node.node_id for node in self.claimable_nodes()),
            "live_done_node_count": sum(1 for node in self.nodes if node.is_live_done),
        }


def _required(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise PlanRuntimeError(f"missing required field: {key}")
    return payload[key]


def _list(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        raise PlanRuntimeError(f"{field_name} must be a list")
    if not isinstance(value, list):
        raise PlanRuntimeError(f"{field_name} must be a list")
    return value


def _dict(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        raise PlanRuntimeError(f"{field_name} must be an object")
    if not isinstance(value, dict):
        raise PlanRuntimeError(f"{field_name} must be an object")
    return value

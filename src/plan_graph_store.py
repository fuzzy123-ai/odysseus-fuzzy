"""Small backend contract for an in-memory plan graph store model."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 140
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_STATUS_COMPATIBLE = {"pending", "running", "done", "blocked", "handoff", "failed", "skipped", "partial", "unknown"}


class PlanGraphStoreError(ValueError):
    """Raised when a plan graph payload is invalid or unsafe."""


class PlanNodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    HANDOFF = "handoff"
    SKIPPED = "skipped"


class AgentPathStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    HANDOFF = "handoff"
    SKIPPED = "skipped"


class PlanEdgeKind(StrEnum):
    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"
    HANDOFF_TO = "handoff_to"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise PlanGraphStoreError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise PlanGraphStoreError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise PlanGraphStoreError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise PlanGraphStoreError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_repo_path(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise PlanGraphStoreError(f"{field_name} must not be empty")
    if "\\" in raw:
        raise PlanGraphStoreError(f"{field_name} must use forward slashes only")
    lowered = raw.lower()
    if lowered.startswith("/") or lowered.startswith("./") or re.match(r"^[a-z]:", lowered):
        raise PlanGraphStoreError(f"{field_name} must be repo-relative")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise PlanGraphStoreError(f"{field_name} must not contain traversal segments")
    return "/".join(parts)


def _normalize_path_list(values: Iterable[Any], *, field_name: str, allow_empty: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _normalize_repo_path(value, field_name=field_name)
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    if not allow_empty and not normalized:
        raise PlanGraphStoreError(f"{field_name} must not be empty")
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
    if not allow_empty and not normalized:
        raise PlanGraphStoreError(f"{field_name} must not be empty")
    return tuple(normalized)


def _ensure_status_compatible(status: str, *, field_name: str) -> str:
    normalized = _normalize_slug(status, field_name=field_name)
    if normalized not in _STATUS_COMPATIBLE:
        raise PlanGraphStoreError(f"{field_name} is not compatible with tool-truth status vocabulary")
    return normalized


@dataclass(frozen=True, slots=True)
class PlanNode:
    node_id: str
    slice_id: str
    title: str
    owner: str
    status: PlanNodeStatus
    allowed_files: tuple[str, ...]
    blocked_files: tuple[str, ...]
    evidence_required: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        node_id: str,
        slice_id: str,
        title: str,
        owner: str,
        status: PlanNodeStatus | str,
        allowed_files: Iterable[Any],
        blocked_files: Iterable[Any],
        evidence_required: Iterable[Any],
    ) -> "PlanNode":
        allowed = _normalize_path_list(allowed_files, field_name="allowed_file", allow_empty=False)
        blocked = _normalize_path_list(blocked_files, field_name="blocked_file", allow_empty=True)
        overlap = sorted(set(allowed) & set(blocked))
        if overlap:
            raise PlanGraphStoreError(f"allowed_files and blocked_files overlap: {', '.join(overlap)}")
        normalized_status = status if isinstance(status, PlanNodeStatus) else PlanNodeStatus(_ensure_status_compatible(status, field_name="node_status"))
        return cls(
            node_id=_normalize_slug(node_id, field_name="node_id"),
            slice_id=_normalize_slug(slice_id, field_name="slice_id"),
            title=_normalize_text(title, field_name="title", allow_empty=False),
            owner=_normalize_slug(owner, field_name="owner"),
            status=normalized_status,
            allowed_files=allowed,
            blocked_files=blocked,
            evidence_required=_normalize_text_list(evidence_required, field_name="evidence_required", allow_empty=True),
        )


@dataclass(frozen=True, slots=True)
class PlanEdge:
    from_node: str
    to_node: str
    kind: PlanEdgeKind

    @classmethod
    def create(cls, *, from_node: str, to_node: str, kind: PlanEdgeKind | str) -> "PlanEdge":
        return cls(
            from_node=_normalize_slug(from_node, field_name="from_node"),
            to_node=_normalize_slug(to_node, field_name="to_node"),
            kind=kind if isinstance(kind, PlanEdgeKind) else PlanEdgeKind(str(kind)),
        )


@dataclass(frozen=True, slots=True)
class AgentPath:
    agent_id: str
    node_ids: tuple[str, ...]
    status: AgentPathStatus

    @classmethod
    def create(
        cls,
        *,
        agent_id: str,
        node_ids: Iterable[Any],
        status: AgentPathStatus | str,
    ) -> "AgentPath":
        normalized_nodes = tuple(_normalize_slug(node_id, field_name="path_node_id") for node_id in node_ids)
        if not normalized_nodes:
            raise PlanGraphStoreError("node_ids must not be empty")
        normalized_status = status if isinstance(status, AgentPathStatus) else AgentPathStatus(_ensure_status_compatible(status, field_name="agent_path_status"))
        return cls(
            agent_id=_normalize_slug(agent_id, field_name="agent_id"),
            node_ids=normalized_nodes,
            status=normalized_status,
        )


@dataclass(frozen=True, slots=True)
class PlanGraph:
    plan_id: str
    title: str
    nodes: tuple[PlanNode, ...]
    edges: tuple[PlanEdge, ...]
    agent_paths: tuple[AgentPath, ...]

    @classmethod
    def create(
        cls,
        *,
        plan_id: str,
        title: str,
        nodes: Iterable[PlanNode],
        edges: Iterable[PlanEdge],
        agent_paths: Iterable[AgentPath],
    ) -> "PlanGraph":
        normalized_nodes = tuple(sorted(nodes, key=lambda node: node.node_id))
        normalized_edges = tuple(sorted(edges, key=lambda edge: (edge.from_node, edge.to_node, edge.kind.value)))
        normalized_paths = tuple(sorted(agent_paths, key=lambda path: path.agent_id))
        if not normalized_nodes:
            raise PlanGraphStoreError("nodes must not be empty")

        node_map = {node.node_id: node for node in normalized_nodes}
        if len(node_map) != len(normalized_nodes):
            raise PlanGraphStoreError("node_ids must be unique")

        for edge in normalized_edges:
            if edge.from_node not in node_map or edge.to_node not in node_map:
                raise PlanGraphStoreError("edges must only reference known nodes")

        for path in normalized_paths:
            unknown = [node_id for node_id in path.node_ids if node_id not in node_map]
            if unknown:
                raise PlanGraphStoreError(f"agent path references unknown nodes: {', '.join(unknown)}")

        _validate_depends_on_acyclic(normalized_nodes, normalized_edges)
        _validate_parallel_file_collisions(normalized_nodes, normalized_edges, normalized_paths)

        return cls(
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            title=_normalize_text(title, field_name="title", allow_empty=False),
            nodes=normalized_nodes,
            edges=normalized_edges,
            agent_paths=normalized_paths,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "agent_path_count": len(self.agent_paths),
            "node_ids": tuple(node.node_id for node in self.nodes),
            "statuses": {node.node_id: node.status.value for node in self.nodes},
            "path_statuses": {path.agent_id: path.status.value for path in self.agent_paths},
        }


def validate_status_transition(current: PlanNodeStatus | str, target: PlanNodeStatus | str) -> bool:
    normalized_current = current if isinstance(current, PlanNodeStatus) else PlanNodeStatus(_ensure_status_compatible(current, field_name="current_status"))
    normalized_target = target if isinstance(target, PlanNodeStatus) else PlanNodeStatus(_ensure_status_compatible(target, field_name="target_status"))
    allowed = {
        PlanNodeStatus.PENDING: {
            PlanNodeStatus.RUNNING,
            PlanNodeStatus.BLOCKED,
            PlanNodeStatus.FAILED,
            PlanNodeStatus.HANDOFF,
            PlanNodeStatus.SKIPPED,
        },
        PlanNodeStatus.RUNNING: {
            PlanNodeStatus.DONE,
            PlanNodeStatus.BLOCKED,
            PlanNodeStatus.FAILED,
            PlanNodeStatus.HANDOFF,
        },
        PlanNodeStatus.BLOCKED: {PlanNodeStatus.PENDING, PlanNodeStatus.HANDOFF},
        PlanNodeStatus.FAILED: {PlanNodeStatus.PENDING, PlanNodeStatus.HANDOFF},
        PlanNodeStatus.HANDOFF: {PlanNodeStatus.PENDING, PlanNodeStatus.RUNNING, PlanNodeStatus.DONE},
        PlanNodeStatus.DONE: set(),
        PlanNodeStatus.SKIPPED: set(),
    }
    return normalized_target in allowed[normalized_current]


def _validate_depends_on_acyclic(nodes: tuple[PlanNode, ...], edges: tuple[PlanEdge, ...]) -> None:
    adjacency: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = {node.node_id: 0 for node in nodes}
    for edge in edges:
        if edge.kind != PlanEdgeKind.DEPENDS_ON:
            continue
        if edge.to_node not in adjacency[edge.from_node]:
            adjacency[edge.from_node].add(edge.to_node)
            indegree[edge.to_node] += 1

    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for neighbor in sorted(adjacency.get(node_id, ())):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)
    if visited != len(nodes):
        raise PlanGraphStoreError("depends_on edges must be acyclic")


def _validate_parallel_file_collisions(
    nodes: tuple[PlanNode, ...],
    edges: tuple[PlanEdge, ...],
    agent_paths: tuple[AgentPath, ...],
) -> None:
    node_map = {node.node_id: node for node in nodes}
    barrier_pairs = {
        (edge.from_node, edge.to_node)
        for edge in edges
        if edge.kind in {PlanEdgeKind.DEPENDS_ON, PlanEdgeKind.BLOCKS, PlanEdgeKind.HANDOFF_TO}
    }
    barrier_pairs |= {(to_node, from_node) for from_node, to_node in barrier_pairs}

    for idx, left in enumerate(agent_paths):
        for right in agent_paths[idx + 1 :]:
            for left_node_id in left.node_ids:
                for right_node_id in right.node_ids:
                    if left_node_id == right_node_id:
                        continue
                    if (left_node_id, right_node_id) in barrier_pairs:
                        continue
                    shared = sorted(
                        set(node_map[left_node_id].allowed_files) & set(node_map[right_node_id].allowed_files)
                    )
                    if shared:
                        raise PlanGraphStoreError(
                            "parallel agent paths share allowed files without barrier: "
                            f"{left_node_id} vs {right_node_id} on {', '.join(shared)}"
                        )

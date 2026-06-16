"""Small backend contract for progressive graph API payloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class ProgressiveGraphApiError(ValueError):
    """Raised when a progressive graph API payload is invalid or unsafe."""


class GraphQueryKind(StrEnum):
    OVERVIEW = "overview"
    NEIGHBORHOOD = "neighborhood"
    PATH = "path"
    COMMUNITY = "community"
    QUERY_SUBGRAPH = "query_subgraph"


class ProgressiveGraphStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    CLIPPED = "clipped"
    EMPTY = "empty"
    BLOCKED = "blocked"
    FAILED = "failed"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise ProgressiveGraphApiError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise ProgressiveGraphApiError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise ProgressiveGraphApiError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ProgressiveGraphApiError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_int(value: Any, *, field_name: str, allow_empty: bool = True) -> int:
    if value in (None, ""):
        if allow_empty:
            return 0
        raise ProgressiveGraphApiError(f"{field_name} must not be empty")
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise ProgressiveGraphApiError(f"{field_name} must be an int") from None
    if normalized < 0:
        raise ProgressiveGraphApiError(f"{field_name} must be >= 0")
    return normalized


def _normalize_kind(value: Any) -> GraphQueryKind:
    if isinstance(value, GraphQueryKind):
        return value
    try:
        return GraphQueryKind(_normalize_slug(value, field_name="query_kind").replace("-", "_"))
    except ValueError as exc:
        raise ProgressiveGraphApiError("query_kind is not supported") from exc


def _normalize_status(value: Any) -> ProgressiveGraphStatus:
    if isinstance(value, ProgressiveGraphStatus):
        return value
    try:
        return ProgressiveGraphStatus(_normalize_slug(value, field_name="status"))
    except ValueError as exc:
        raise ProgressiveGraphApiError("status is not supported") from exc


@dataclass(frozen=True, slots=True)
class GraphQueryBudget:
    limit: int
    max_nodes: int
    max_edges: int
    depth: int
    max_hops: int
    time_budget_ms: int
    payload_budget_bytes: int

    @classmethod
    def create(
        cls,
        *,
        limit: Any = 0,
        max_nodes: Any = 0,
        max_edges: Any = 0,
        depth: Any = 0,
        max_hops: Any = 0,
        time_budget_ms: Any = 0,
        payload_budget_bytes: Any = 0,
    ) -> "GraphQueryBudget":
        normalized = cls(
            limit=_normalize_int(limit, field_name="limit"),
            max_nodes=_normalize_int(max_nodes, field_name="max_nodes"),
            max_edges=_normalize_int(max_edges, field_name="max_edges"),
            depth=_normalize_int(depth, field_name="depth"),
            max_hops=_normalize_int(max_hops, field_name="max_hops"),
            time_budget_ms=_normalize_int(time_budget_ms, field_name="time_budget_ms"),
            payload_budget_bytes=_normalize_int(payload_budget_bytes, field_name="payload_budget_bytes"),
        )
        if normalized.limit <= 0 or normalized.max_nodes <= 0 or normalized.max_edges <= 0:
            raise ProgressiveGraphApiError("limit, max_nodes, and max_edges must be bounded and > 0")
        return normalized


@dataclass(frozen=True, slots=True)
class GraphViewport:
    viewport_ref: str
    node_ref: str

    @classmethod
    def create(
        cls,
        *,
        viewport_ref: Any,
        node_ref: Any = "",
    ) -> "GraphViewport":
        return cls(
            viewport_ref=_normalize_slug(viewport_ref, field_name="viewport_ref"),
            node_ref=_normalize_slug(node_ref, field_name="node_ref") if str(node_ref or "").strip() else "",
        )


@dataclass(frozen=True, slots=True)
class GraphNodeSummary:
    node_id: str
    label: str
    node_type: str
    score: float

    @classmethod
    def create(
        cls,
        *,
        node_id: Any,
        label: Any,
        node_type: Any,
        score: Any = 0,
    ) -> "GraphNodeSummary":
        try:
            normalized_score = float(score)
        except (TypeError, ValueError):
            raise ProgressiveGraphApiError("score must be numeric") from None
        return cls(
            node_id=_normalize_slug(node_id, field_name="node_id"),
            label=_normalize_text(label, field_name="label", allow_empty=False, limit=80),
            node_type=_normalize_slug(node_type, field_name="node_type"),
            score=normalized_score,
        )


@dataclass(frozen=True, slots=True)
class GraphEdgeSummary:
    edge_id: str
    source_id: str
    target_id: str
    edge_type: str
    score: float

    @classmethod
    def create(
        cls,
        *,
        edge_id: Any,
        source_id: Any,
        target_id: Any,
        edge_type: Any,
        score: Any = 0,
    ) -> "GraphEdgeSummary":
        try:
            normalized_score = float(score)
        except (TypeError, ValueError):
            raise ProgressiveGraphApiError("edge score must be numeric") from None
        return cls(
            edge_id=_normalize_slug(edge_id, field_name="edge_id"),
            source_id=_normalize_slug(source_id, field_name="source_id"),
            target_id=_normalize_slug(target_id, field_name="target_id"),
            edge_type=_normalize_slug(edge_type, field_name="edge_type"),
            score=normalized_score,
        )


@dataclass(frozen=True, slots=True)
class GraphAggregate:
    aggregate_id: str
    label: str
    count: int

    @classmethod
    def create(
        cls,
        *,
        aggregate_id: Any,
        label: Any,
        count: Any,
    ) -> "GraphAggregate":
        return cls(
            aggregate_id=_normalize_slug(aggregate_id, field_name="aggregate_id"),
            label=_normalize_text(label, field_name="label", allow_empty=False, limit=80),
            count=_normalize_int(count, field_name="count", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class ProgressiveGraphPage:
    graph_query_id: str
    graph_ref: str
    viewport: GraphViewport
    query_kind: GraphQueryKind
    budget: GraphQueryBudget
    nodes: tuple[GraphNodeSummary, ...]
    edges: tuple[GraphEdgeSummary, ...]
    aggregates: tuple[GraphAggregate, ...]
    node_count: int
    edge_count: int
    status: ProgressiveGraphStatus
    partial: bool
    clipped: bool
    next_cursor: str
    reason: str
    next_action: str
    evidence_ref: str

    @classmethod
    def create(
        cls,
        *,
        graph_query_id: Any,
        graph_ref: Any,
        viewport: GraphViewport,
        query_kind: GraphQueryKind | str,
        budget: GraphQueryBudget,
        nodes: Iterable[GraphNodeSummary],
        edges: Iterable[GraphEdgeSummary],
        aggregates: Iterable[GraphAggregate],
        node_count: Any,
        edge_count: Any,
        status: ProgressiveGraphStatus | str,
        partial: bool,
        clipped: bool,
        next_cursor: Any = "",
        reason: Any = "",
        next_action: Any = "",
        evidence_ref: Any = "",
    ) -> "ProgressiveGraphPage":
        if not isinstance(viewport, GraphViewport):
            raise ProgressiveGraphApiError("viewport must be a GraphViewport")
        if not isinstance(budget, GraphQueryBudget):
            raise ProgressiveGraphApiError("budget must be a GraphQueryBudget")
        normalized_nodes = tuple(nodes)
        normalized_edges = tuple(edges)
        normalized_aggregates = tuple(aggregates)
        if any(not isinstance(node, GraphNodeSummary) for node in normalized_nodes):
            raise ProgressiveGraphApiError("nodes must contain GraphNodeSummary items")
        if any(not isinstance(edge, GraphEdgeSummary) for edge in normalized_edges):
            raise ProgressiveGraphApiError("edges must contain GraphEdgeSummary items")
        if any(not isinstance(aggregate, GraphAggregate) for aggregate in normalized_aggregates):
            raise ProgressiveGraphApiError("aggregates must contain GraphAggregate items")

        normalized_kind = _normalize_kind(query_kind)
        normalized_status = _normalize_status(status)
        normalized_node_count = _normalize_int(node_count, field_name="node_count", allow_empty=False)
        normalized_edge_count = _normalize_int(edge_count, field_name="edge_count", allow_empty=False)
        normalized_partial = bool(partial)
        normalized_clipped = bool(clipped)
        normalized_next_cursor = _normalize_text(next_cursor, field_name="next_cursor", allow_empty=True)
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_evidence = _normalize_text(evidence_ref, field_name="evidence_ref", allow_empty=True, limit=_MAX_LONG_TEXT)

        if normalized_node_count != len(normalized_nodes):
            raise ProgressiveGraphApiError("node_count must match nodes length")
        if normalized_edge_count != len(normalized_edges):
            raise ProgressiveGraphApiError("edge_count must match edges length")
        if normalized_node_count > budget.max_nodes or normalized_node_count > budget.limit:
            raise ProgressiveGraphApiError("node_count must not exceed graph budgets")
        if normalized_edge_count > budget.max_edges:
            raise ProgressiveGraphApiError("edge_count must not exceed graph budgets")
        if normalized_partial or normalized_clipped:
            if not (normalized_reason or normalized_next_action):
                raise ProgressiveGraphApiError("partial and clipped pages require reason or next_action")
        if normalized_status in {ProgressiveGraphStatus.BLOCKED, ProgressiveGraphStatus.FAILED} and not (
            normalized_reason and normalized_next_action
        ):
            raise ProgressiveGraphApiError("blocked and failed pages require reason and next_action")
        if normalized_next_cursor and normalized_status == ProgressiveGraphStatus.FAILED and not normalized_next_action:
            raise ProgressiveGraphApiError("failed pages with next_cursor require next_action")
        if normalized_next_cursor and not (
            normalized_partial or normalized_clipped or normalized_status == ProgressiveGraphStatus.COMPLETE
        ):
            raise ProgressiveGraphApiError("next_cursor is only allowed for partial, clipped, or continued complete pages")
        if normalized_kind == GraphQueryKind.PATH and budget.max_hops <= 0:
            raise ProgressiveGraphApiError("path queries require max_hops")
        if normalized_kind == GraphQueryKind.NEIGHBORHOOD and (budget.depth <= 0 or not viewport.node_ref):
            raise ProgressiveGraphApiError("neighborhood queries require depth and node_ref")

        return cls(
            graph_query_id=_normalize_slug(graph_query_id, field_name="graph_query_id"),
            graph_ref=_normalize_slug(graph_ref, field_name="graph_ref"),
            viewport=viewport,
            query_kind=normalized_kind,
            budget=budget,
            nodes=normalized_nodes,
            edges=normalized_edges,
            aggregates=normalized_aggregates,
            node_count=normalized_node_count,
            edge_count=normalized_edge_count,
            status=normalized_status,
            partial=normalized_partial,
            clipped=normalized_clipped,
            next_cursor=normalized_next_cursor,
            reason=normalized_reason,
            next_action=normalized_next_action,
            evidence_ref=normalized_evidence,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "graph_query_id": self.graph_query_id,
            "graph_ref": self.graph_ref,
            "viewport_ref": self.viewport.viewport_ref,
            "query_kind": self.query_kind.value,
            "status": self.status.value,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "aggregate_count": len(self.aggregates),
            "partial": self.partial,
            "clipped": self.clipped,
            "has_next_cursor": bool(self.next_cursor),
            "budget_flags": {
                "limit": self.budget.limit,
                "max_nodes": self.budget.max_nodes,
                "max_edges": self.budget.max_edges,
                "depth": self.budget.depth,
                "max_hops": self.budget.max_hops,
            },
        }

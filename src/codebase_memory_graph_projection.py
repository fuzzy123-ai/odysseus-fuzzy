"""Bounded Progressive Graph API projection for CBM code mappings.

The API consumes typed derivative metadata or an injected bounded store.  It
never reads a productive graph/source directly and has no filesystem, process,
network, configuration, hook, UI, or live-system path.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from src.code_intelligence_contract import CodeEdgeMapping, CodeLocation, CodeSymbolMapping
from src.codebase_memory_query import StructuralMappingCatalog
from src.progressive_graph_api import (
    GraphAggregate,
    GraphEdgeSummary,
    GraphNodeSummary,
    GraphQueryBudget,
    GraphQueryKind,
    GraphViewport,
    ProgressiveGraphPage,
    ProgressiveGraphStatus,
)
from src.unified_source_index_contract import RecordKind, RecordRef


CBM_GRAPH_SCHEMA = "odysseus.codebase_memory.graph_projection.v1"
HARD_MAX_NODES = 500
HARD_MAX_EDGES = 2_000
HARD_MAX_DEPTH = 8
HARD_MAX_HOPS = 8
HARD_MAX_TIME_MS = 10_000
HARD_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_QUERY_ENTITIES = 1_000

_GENERATION_RE = re.compile(r"^cbm_generation_[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,159}$")
_CURSOR_RE = re.compile(r"^c-([0-9a-f]{32})-([0-9]{1,9})$")


class CodebaseMemoryGraphError(ValueError):
    """Raised when graph input/output escapes identity, evidence, or budgets."""


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CodebaseMemoryGraphError(f"{label} is outside its bound")
    return value


def _score(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CodebaseMemoryGraphError(f"{label} must be numeric")
    normalized = round(float(value), 12)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise CodebaseMemoryGraphError(f"{label} is outside its bound")
    return normalized


def _token(value: str, label: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise CodebaseMemoryGraphError(f"{label} must be a bounded token")
    return value


def _generation(value: str) -> str:
    if not isinstance(value, str) or not _GENERATION_RE.fullmatch(value):
        raise CodebaseMemoryGraphError("graph_ref must be a CBM generation")
    return value


def _kind(value: GraphQueryKind | str) -> GraphQueryKind:
    if isinstance(value, GraphQueryKind):
        return value
    try:
        return GraphQueryKind(value)
    except (TypeError, ValueError) as exc:
        raise CodebaseMemoryGraphError("query_kind is invalid") from exc


def _hash(prefix: str, value: str, length: int = 32) -> str:
    return f"code-{prefix}-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _entity(value: str, label: str) -> str:
    if not value:
        return ""
    try:
        return RecordRef(RecordKind.ENTITY, value).record_id
    except Exception as exc:
        raise CodebaseMemoryGraphError(f"{label} is not a USI entity") from exc


@dataclass(frozen=True, slots=True)
class CodeGraphBudget:
    limit: int = 64
    max_nodes: int = 64
    max_edges: int = 256
    depth: int = 2
    max_hops: int = 4
    time_budget_ms: int = 1_000
    payload_budget_bytes: int = 512 * 1024

    def __post_init__(self) -> None:
        limit = _integer(self.limit, "limit", 1, HARD_MAX_NODES)
        max_nodes = _integer(self.max_nodes, "max_nodes", 1, HARD_MAX_NODES)
        if limit > max_nodes:
            raise CodebaseMemoryGraphError("limit cannot exceed max_nodes")
        object.__setattr__(self, "limit", limit)
        object.__setattr__(self, "max_nodes", max_nodes)
        object.__setattr__(self, "max_edges", _integer(self.max_edges, "max_edges", 1, HARD_MAX_EDGES))
        object.__setattr__(self, "depth", _integer(self.depth, "depth", 0, HARD_MAX_DEPTH))
        object.__setattr__(self, "max_hops", _integer(self.max_hops, "max_hops", 0, HARD_MAX_HOPS))
        object.__setattr__(self, "time_budget_ms", _integer(self.time_budget_ms, "time_budget_ms", 10, HARD_MAX_TIME_MS))
        object.__setattr__(
            self,
            "payload_budget_bytes",
            _integer(self.payload_budget_bytes, "payload_budget_bytes", 1024, HARD_MAX_PAYLOAD_BYTES),
        )

    def progressive(self) -> GraphQueryBudget:
        return GraphQueryBudget.create(
            limit=self.limit,
            max_nodes=self.max_nodes,
            max_edges=self.max_edges,
            depth=self.depth,
            max_hops=self.max_hops,
            time_budget_ms=self.time_budget_ms,
            payload_budget_bytes=self.payload_budget_bytes,
        )


@dataclass(frozen=True, slots=True)
class CodeGraphQuery:
    graph_ref: str
    query_kind: GraphQueryKind
    viewport_ref: str
    budget: CodeGraphBudget
    node_entity_id: str = ""
    target_entity_id: str = ""
    community_ref: str = ""
    query_entity_ids: tuple[str, ...] = ()
    cursor: str = ""

    def __post_init__(self) -> None:
        graph_ref = _generation(self.graph_ref)
        kind = _kind(self.query_kind)
        viewport = _token(self.viewport_ref, "viewport_ref")
        if not isinstance(self.budget, CodeGraphBudget):
            raise CodebaseMemoryGraphError("budget must be CodeGraphBudget")
        node = _entity(self.node_entity_id, "node_entity_id")
        target = _entity(self.target_entity_id, "target_entity_id")
        community = _token(self.community_ref, "community_ref") if self.community_ref else ""
        if not isinstance(self.query_entity_ids, tuple) or len(self.query_entity_ids) > MAX_QUERY_ENTITIES:
            raise CodebaseMemoryGraphError("query_entity_ids must be a bounded tuple")
        query_entities = tuple(sorted({_entity(item, "query_entity_ids") for item in self.query_entity_ids}))
        if kind is GraphQueryKind.NEIGHBORHOOD and (not node or self.budget.depth < 1):
            raise CodebaseMemoryGraphError("neighborhood requires node_entity_id and positive depth")
        if kind is GraphQueryKind.PATH and (
            not node or not target or node == target or self.budget.max_hops < 1
        ):
            raise CodebaseMemoryGraphError("path requires distinct endpoints and positive max_hops")
        if kind is GraphQueryKind.COMMUNITY and not community:
            raise CodebaseMemoryGraphError("community query requires community_ref")
        if kind is GraphQueryKind.QUERY_SUBGRAPH and not query_entities:
            raise CodebaseMemoryGraphError("query_subgraph requires query_entity_ids")
        allowed_cursor = kind in {GraphQueryKind.COMMUNITY, GraphQueryKind.QUERY_SUBGRAPH}
        if self.cursor and (not allowed_cursor or not _CURSOR_RE.fullmatch(self.cursor)):
            raise CodebaseMemoryGraphError("cursor is invalid for this graph query")
        object.__setattr__(self, "graph_ref", graph_ref)
        object.__setattr__(self, "query_kind", kind)
        object.__setattr__(self, "viewport_ref", viewport)
        object.__setattr__(self, "node_entity_id", node)
        object.__setattr__(self, "target_entity_id", target)
        object.__setattr__(self, "community_ref", community)
        object.__setattr__(self, "query_entity_ids", query_entities)

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "graph_ref": self.graph_ref,
            "query_kind": self.query_kind.value,
            "viewport_ref": self.viewport_ref,
            "node_entity_id": self.node_entity_id,
            "target_entity_id": self.target_entity_id,
            "community_ref": self.community_ref,
            "query_entity_ids": list(self.query_entity_ids),
            "budget": {
                "limit": self.budget.limit,
                "max_nodes": self.budget.max_nodes,
                "max_edges": self.budget.max_edges,
                "depth": self.budget.depth,
                "max_hops": self.budget.max_hops,
                "time_budget_ms": self.budget.time_budget_ms,
                "payload_budget_bytes": self.budget.payload_budget_bytes,
            },
        }

    @property
    def query_id(self) -> str:
        encoded = json.dumps(self.identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return _hash("graph-query", hashlib.sha256(encoded).hexdigest())

    @property
    def cursor_offset(self) -> int:
        if not self.cursor:
            return 0
        match = _CURSOR_RE.fullmatch(self.cursor)
        assert match is not None
        fingerprint = hashlib.sha256(
            json.dumps(self.identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32]
        if match.group(1) != fingerprint:
            raise CodebaseMemoryGraphError("cursor does not belong to this graph query")
        return int(match.group(2))

    def cursor_for(self, offset: int) -> str:
        offset = _integer(offset, "cursor offset", 1, 999_999_999)
        fingerprint = hashlib.sha256(
            json.dumps(self.identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32]
        return f"c-{fingerprint}-{offset}"


@dataclass(frozen=True, slots=True)
class CodeGraphNodeRecord:
    mapping: CodeSymbolMapping
    community_ref: str
    relevance: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, CodeSymbolMapping):
            raise CodebaseMemoryGraphError("graph node mapping must be typed")
        object.__setattr__(self, "community_ref", _token(self.community_ref, "community_ref"))
        object.__setattr__(self, "relevance", _score(self.relevance, "node relevance"))

    @property
    def visual_id(self) -> str:
        return _hash("node", self.mapping.entity_id)


@dataclass(frozen=True, slots=True)
class CodeGraphEdgeRecord:
    mapping: CodeEdgeMapping
    relevance: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, CodeEdgeMapping):
            raise CodebaseMemoryGraphError("graph edge mapping must be typed")
        object.__setattr__(self, "relevance", _score(self.relevance, "edge relevance"))

    @property
    def visual_id(self) -> str:
        return _hash("edge", self.mapping.relation_id)


@dataclass(frozen=True, slots=True)
class CodeGraphAggregateRecord:
    aggregate_ref: str
    label: str
    count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "aggregate_ref", _token(self.aggregate_ref, "aggregate_ref"))
        if not isinstance(self.label, str) or not self.label.strip() or len(self.label) > 80:
            raise CodebaseMemoryGraphError("aggregate label is invalid")
        object.__setattr__(self, "label", " ".join(self.label.split()))
        object.__setattr__(self, "count", _integer(self.count, "aggregate count", 0, 1_000_000_000))


@dataclass(frozen=True, slots=True)
class CodeGraphSlice:
    nodes: tuple[CodeGraphNodeRecord, ...]
    edges: tuple[CodeGraphEdgeRecord, ...]
    aggregates: tuple[CodeGraphAggregateRecord, ...]
    total_node_count: int
    total_edge_count: int
    has_more: bool
    examined_node_count: int
    examined_edge_count: int
    duration_ms: int
    timed_out: bool = False
    aggregate_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.nodes, tuple) or not all(isinstance(item, CodeGraphNodeRecord) for item in self.nodes):
            raise CodebaseMemoryGraphError("graph slice nodes must be typed")
        if not isinstance(self.edges, tuple) or not all(isinstance(item, CodeGraphEdgeRecord) for item in self.edges):
            raise CodebaseMemoryGraphError("graph slice edges must be typed")
        if not isinstance(self.aggregates, tuple) or not all(isinstance(item, CodeGraphAggregateRecord) for item in self.aggregates):
            raise CodebaseMemoryGraphError("graph slice aggregates must be typed")
        for label, value in (
            ("total_node_count", self.total_node_count),
            ("total_edge_count", self.total_edge_count),
            ("examined_node_count", self.examined_node_count),
            ("examined_edge_count", self.examined_edge_count),
            ("duration_ms", self.duration_ms),
        ):
            _integer(value, label, 0, 1_000_000_000)
        if self.total_node_count < len(self.nodes) or self.total_edge_count < len(self.edges):
            raise CodebaseMemoryGraphError("graph slice totals under-report visible records")
        if not all(isinstance(item, bool) for item in (self.has_more, self.timed_out, self.aggregate_only)):
            raise CodebaseMemoryGraphError("graph slice flags must be boolean")
        if self.aggregate_only and (self.nodes or self.edges or not self.aggregates):
            raise CodebaseMemoryGraphError("aggregate-only slices require aggregates and no details")


@runtime_checkable
class CodeGraphStore(Protocol):
    @property
    def graph_ref(self) -> str: ...

    def query(self, request: CodeGraphQuery, offset: int) -> CodeGraphSlice: ...


class CatalogCodeGraphStore:
    """Small-graph deterministic store built from exact CBM-02 mappings."""

    def __init__(
        self,
        graph_ref: str,
        *,
        symbols: Iterable[CodeSymbolMapping],
        edges: Iterable[CodeEdgeMapping],
        community_by_entity: Mapping[str, str] | None = None,
    ) -> None:
        self._graph_ref = _generation(graph_ref)
        symbol_items = tuple(symbols)
        edge_items = tuple(edges)
        if len(symbol_items) > 100_000 or len(edge_items) > 500_000:
            raise CodebaseMemoryGraphError("catalog graph input is unbounded")
        if not all(isinstance(item, CodeSymbolMapping) for item in symbol_items) or not all(
            isinstance(item, CodeEdgeMapping) for item in edge_items
        ):
            raise CodebaseMemoryGraphError("catalog graph mappings must be typed")
        self._nodes: dict[str, CodeGraphNodeRecord] = {}
        supplied_communities = dict(community_by_entity or {})
        for mapping in symbol_items:
            if mapping.entity_id in self._nodes:
                raise CodebaseMemoryGraphError("catalog graph entity is duplicated")
            community = supplied_communities.get(
                mapping.entity_id,
                _hash("community", mapping.file_fallback_key, 24),
            )
            self._nodes[mapping.entity_id] = CodeGraphNodeRecord(mapping, community)
        if set(supplied_communities) - set(self._nodes):
            raise CodebaseMemoryGraphError("community map references an unknown entity")
        self._edges: dict[str, CodeGraphEdgeRecord] = {}
        self._adjacent: dict[str, list[CodeGraphEdgeRecord]] = {key: [] for key in self._nodes}
        for mapping in edge_items:
            if mapping.relation_id in self._edges:
                raise CodebaseMemoryGraphError("catalog graph relation is duplicated")
            if mapping.source_entity_id not in self._nodes or mapping.target_entity_id not in self._nodes:
                raise CodebaseMemoryGraphError("catalog graph edge endpoint is unmapped")
            edge = CodeGraphEdgeRecord(mapping)
            self._edges[mapping.relation_id] = edge
            self._adjacent[mapping.source_entity_id].append(edge)
            self._adjacent[mapping.target_entity_id].append(edge)
        for values in self._adjacent.values():
            values.sort(key=lambda item: item.mapping.relation_id)
        self._ordered_nodes = tuple(self._nodes[key] for key in sorted(self._nodes))
        self._ordered_edges = tuple(self._edges[key] for key in sorted(self._edges))
        communities: dict[str, list[CodeGraphNodeRecord]] = {}
        counts: dict[str, int] = {}
        for node in self._ordered_nodes:
            communities.setdefault(node.community_ref, []).append(node)
            key = f"symbol-{node.mapping.symbol_kind.value}"
            counts[key] = counts.get(key, 0) + 1
            counts[node.community_ref] = counts.get(node.community_ref, 0) + 1
        for edge in self._ordered_edges:
            key = f"relation-{edge.mapping.relation_kind.value}"
            counts[key] = counts.get(key, 0) + 1
        self._communities = {
            key: tuple(values) for key, values in communities.items()
        }
        self._overview_aggregates = tuple(
            CodeGraphAggregateRecord(key, key.replace("-", " "), count)
            for key, count in sorted(counts.items())
        )

    @classmethod
    def from_catalog(cls, catalog: StructuralMappingCatalog) -> "CatalogCodeGraphStore":
        if not isinstance(catalog, StructuralMappingCatalog):
            raise CodebaseMemoryGraphError("catalog must be StructuralMappingCatalog")
        return cls(
            catalog.generation.generation_ref,
            symbols=catalog.symbols.values(),
            edges=catalog.edges.values(),
        )

    @property
    def graph_ref(self) -> str:
        return self._graph_ref

    def query(self, request: CodeGraphQuery, offset: int) -> CodeGraphSlice:
        if request.query_kind is GraphQueryKind.OVERVIEW:
            return self._overview()
        if request.query_kind is GraphQueryKind.NEIGHBORHOOD:
            return self._neighborhood(request)
        if request.query_kind is GraphQueryKind.PATH:
            return self._path(request)
        if request.query_kind is GraphQueryKind.COMMUNITY:
            selected = self._communities.get(request.community_ref, ())
            return self._paged_induced(request, selected, offset)
        selected = tuple(
            self._nodes[item] for item in request.query_entity_ids if item in self._nodes
        )
        return self._paged_induced(request, selected, offset)

    def _overview(self) -> CodeGraphSlice:
        return CodeGraphSlice(
            (),
            (),
            self._overview_aggregates,
            len(self._ordered_nodes),
            len(self._ordered_edges),
            False,
            0,
            0,
            0,
            aggregate_only=True,
        )

    def _neighborhood(self, request: CodeGraphQuery) -> CodeGraphSlice:
        if request.node_entity_id not in self._nodes:
            return CodeGraphSlice((), (), (), 0, 0, False, 1, 0, 0)
        selected = {request.node_entity_id}
        queue = deque([(request.node_entity_id, 0)])
        examined_edges = 0
        clipped = False
        while queue:
            entity_id, depth = queue.popleft()
            if depth >= request.budget.depth:
                continue
            for edge in self._adjacent[entity_id]:
                if examined_edges >= request.budget.max_edges:
                    clipped = True
                    queue.clear()
                    break
                examined_edges += 1
                other = (
                    edge.mapping.target_entity_id
                    if edge.mapping.source_entity_id == entity_id
                    else edge.mapping.source_entity_id
                )
                if other not in selected:
                    if len(selected) >= request.budget.max_nodes:
                        clipped = True
                        continue
                    selected.add(other)
                    queue.append((other, depth + 1))
        nodes = tuple(self._nodes[item] for item in sorted(selected))
        edges, edge_clipped, induced_examined = self._bounded_induced_edges(
            set(selected), request.budget.max_edges
        )
        clipped = clipped or edge_clipped
        return CodeGraphSlice(
            nodes,
            edges,
            (),
            len(nodes) + (1 if clipped else 0),
            len(edges) + (1 if clipped else 0),
            clipped,
            len(selected),
            examined_edges + induced_examined,
            0,
        )

    def _path(self, request: CodeGraphQuery) -> CodeGraphSlice:
        if request.node_entity_id not in self._nodes or request.target_entity_id not in self._nodes:
            return CodeGraphSlice((), (), (), 0, 0, False, 2, 0, 0)
        queue = deque([request.node_entity_id])
        previous: dict[str, tuple[str, CodeGraphEdgeRecord] | None] = {
            request.node_entity_id: None
        }
        depth = {request.node_entity_id: 0}
        examined_edges = 0
        clipped = False
        while queue and request.target_entity_id not in previous:
            current = queue.popleft()
            if depth[current] >= request.budget.max_hops:
                continue
            for edge in self._adjacent[current]:
                if examined_edges >= request.budget.max_edges:
                    clipped = True
                    queue.clear()
                    break
                examined_edges += 1
                other = (
                    edge.mapping.target_entity_id
                    if edge.mapping.source_entity_id == current
                    else edge.mapping.source_entity_id
                )
                if other not in previous:
                    previous[other] = (current, edge)
                    depth[other] = depth[current] + 1
                    queue.append(other)
                    if len(previous) >= request.budget.max_nodes:
                        clipped = True
                        queue.clear()
                        break
        if request.target_entity_id not in previous:
            return CodeGraphSlice(
                (), (), (), 0, 0, clipped, len(previous), examined_edges, 0
            )
        node_ids = [request.target_entity_id]
        path_edges: list[CodeGraphEdgeRecord] = []
        current = request.target_entity_id
        while previous[current] is not None:
            parent, edge = previous[current]  # type: ignore[misc]
            path_edges.append(edge)
            node_ids.append(parent)
            current = parent
        node_ids.reverse()
        path_edges.reverse()
        return CodeGraphSlice(
            tuple(self._nodes[item] for item in node_ids),
            tuple(path_edges[: request.budget.max_edges]),
            (),
            len(node_ids),
            len(path_edges),
            clipped or len(path_edges) > request.budget.max_edges,
            len(previous),
            examined_edges,
            0,
        )

    def _paged_induced(
        self,
        request: CodeGraphQuery,
        selected: tuple[CodeGraphNodeRecord, ...],
        offset: int,
    ) -> CodeGraphSlice:
        page = selected[offset : offset + request.budget.max_nodes]
        ids = {item.mapping.entity_id for item in page}
        edges, edge_clipped, examined_edges = self._bounded_induced_edges(
            ids, request.budget.max_edges
        )
        has_more = offset + len(page) < len(selected) or edge_clipped
        aggregates = ()
        if request.query_kind is GraphQueryKind.COMMUNITY:
            aggregates = (
                CodeGraphAggregateRecord(
                    request.community_ref,
                    request.community_ref.replace("-", " "),
                    len(selected),
                ),
            )
        return CodeGraphSlice(
            page,
            edges,
            aggregates,
            len(selected),
            len(edges) + (1 if edge_clipped else 0),
            has_more,
            len(page),
            examined_edges,
            0,
        )

    def _bounded_induced_edges(
        self,
        entity_ids: set[str],
        limit: int,
    ) -> tuple[tuple[CodeGraphEdgeRecord, ...], bool, int]:
        selected: dict[str, CodeGraphEdgeRecord] = {}
        examined = 0
        examination_limit = max(limit, 1) * 2
        for entity_id in sorted(entity_ids):
            for edge in self._adjacent.get(entity_id, ()):
                if examined >= examination_limit:
                    return (
                        tuple(selected[key] for key in sorted(selected)[:limit]),
                        True,
                        examined,
                    )
                examined += 1
                if (
                    edge.mapping.source_entity_id in entity_ids
                    and edge.mapping.target_entity_id in entity_ids
                ):
                    selected[edge.mapping.relation_id] = edge
                    if len(selected) > limit:
                        return (
                            tuple(selected[key] for key in sorted(selected)[:limit]),
                            True,
                            examined,
                        )
        return tuple(selected[key] for key in sorted(selected)), False, examined


@dataclass(frozen=True, slots=True)
class CodeGraphNodeEvidence:
    visual_node_id: str
    entity_id: str
    source_id: str
    source_version_id: str
    symbol_mapping_ref: str
    locator: CodeLocation
    method: str
    confidence: float


@dataclass(frozen=True, slots=True)
class CodeGraphEdgeEvidence:
    visual_edge_id: str
    relation_id: str
    source_visual_node_id: str
    target_visual_node_id: str
    relation_kind: str
    edge_mapping_ref: str
    method: str
    confidence: float


@dataclass(frozen=True, slots=True)
class CodeGraphPage:
    page: ProgressiveGraphPage
    node_evidence: tuple[CodeGraphNodeEvidence, ...]
    edge_evidence: tuple[CodeGraphEdgeEvidence, ...]
    total_node_count: int
    total_edge_count: int
    examined_node_count: int
    examined_edge_count: int
    duration_ms: int
    payload_bytes: int
    level_of_detail: str

    def audit_summary(self) -> dict[str, Any]:
        return {
            **self.page.audit_summary(),
            "total_node_count": self.total_node_count,
            "total_edge_count": self.total_edge_count,
            "examined_node_count": self.examined_node_count,
            "examined_edge_count": self.examined_edge_count,
            "duration_ms": self.duration_ms,
            "payload_bytes": self.payload_bytes,
            "level_of_detail": self.level_of_detail,
            "node_evidence_count": len(self.node_evidence),
            "edge_evidence_count": len(self.edge_evidence),
        }


class CodebaseMemoryGraphProjection:
    def __init__(self, store: CodeGraphStore) -> None:
        if not isinstance(store, CodeGraphStore):
            raise CodebaseMemoryGraphError("store must implement CodeGraphStore")
        self._store = store

    def query(self, request: CodeGraphQuery) -> CodeGraphPage:
        if not isinstance(request, CodeGraphQuery):
            raise CodebaseMemoryGraphError("request must be CodeGraphQuery")
        if request.graph_ref != _generation(self._store.graph_ref):
            raise CodebaseMemoryGraphError("request graph_ref does not match selected store")
        offset = request.cursor_offset
        result = self._store.query(request, offset)
        if not isinstance(result, CodeGraphSlice):
            raise CodebaseMemoryGraphError("graph store returned an untyped slice")
        nodes = result.nodes
        edges = result.edges
        aggregates = result.aggregates
        if len(nodes) > request.budget.max_nodes or len(nodes) > request.budget.limit:
            raise CodebaseMemoryGraphError("graph store exceeded node budget")
        if len(edges) > request.budget.max_edges:
            raise CodebaseMemoryGraphError("graph store exceeded edge budget")
        if result.duration_ms > request.budget.time_budget_ms:
            raise CodebaseMemoryGraphError("graph store exceeded time budget")
        if not result.aggregate_only and (
            result.examined_node_count
            > request.budget.max_nodes * (max(request.budget.depth, request.budget.max_hops, 1) + 1)
            or result.examined_edge_count > request.budget.max_edges * 4
        ):
            raise CodebaseMemoryGraphError("graph store exceeded bounded examination work")
        node_ids = {item.mapping.entity_id for item in nodes}
        if len(node_ids) != len(nodes):
            raise CodebaseMemoryGraphError("graph store duplicated a node")
        if len({item.mapping.relation_id for item in edges}) != len(edges):
            raise CodebaseMemoryGraphError("graph store duplicated an edge")
        if any(
            item.mapping.source_entity_id not in node_ids
            or item.mapping.target_entity_id not in node_ids
            for item in edges
        ):
            raise CodebaseMemoryGraphError("visible graph edge has a missing endpoint")
        next_cursor = ""
        if result.has_more and request.query_kind in {
            GraphQueryKind.COMMUNITY,
            GraphQueryKind.QUERY_SUBGRAPH,
        } and nodes:
            next_cursor = request.cursor_for(offset + len(nodes))
        clipped = result.has_more or result.timed_out or len(aggregates) > 128
        partial = clipped
        status = (
            ProgressiveGraphStatus.EMPTY
            if not nodes and not aggregates
            else ProgressiveGraphStatus.CLIPPED
            if clipped
            else ProgressiveGraphStatus.COMPLETE
        )
        reason = "budget_limited" if clipped else ""
        next_action = (
            "continue_with_cursor" if next_cursor else "narrow_scope_or_budget"
        ) if clipped else ""
        while True:
            page, node_evidence, edge_evidence, payload_bytes = self._render(
                request,
                nodes,
                edges,
                aggregates,
                status,
                partial,
                clipped,
                next_cursor,
                reason,
                next_action,
            )
            if payload_bytes <= request.budget.payload_budget_bytes:
                break
            clipped = partial = True
            status = ProgressiveGraphStatus.CLIPPED
            reason = "payload_budget_limited"
            next_action = "continue_with_cursor" if next_cursor else "narrow_scope_or_budget"
            if edges:
                edges = edges[: len(edges) // 2]
            elif nodes:
                nodes = nodes[: len(nodes) // 2]
                kept = {item.mapping.entity_id for item in nodes}
                edges = tuple(
                    item
                    for item in edges
                    if item.mapping.source_entity_id in kept
                    and item.mapping.target_entity_id in kept
                )
                if next_cursor and nodes:
                    next_cursor = request.cursor_for(offset + len(nodes))
            elif aggregates:
                aggregates = aggregates[: len(aggregates) // 2]
            else:
                raise CodebaseMemoryGraphError("minimum graph receipt exceeds payload budget")
        return CodeGraphPage(
            page,
            node_evidence,
            edge_evidence,
            result.total_node_count,
            result.total_edge_count,
            result.examined_node_count,
            result.examined_edge_count,
            result.duration_ms,
            payload_bytes,
            "aggregate" if result.aggregate_only else "detail",
        )

    def _render(
        self,
        request: CodeGraphQuery,
        nodes: tuple[CodeGraphNodeRecord, ...],
        edges: tuple[CodeGraphEdgeRecord, ...],
        aggregates: tuple[CodeGraphAggregateRecord, ...],
        status: ProgressiveGraphStatus,
        partial: bool,
        clipped: bool,
        next_cursor: str,
        reason: str,
        next_action: str,
    ) -> tuple[
        ProgressiveGraphPage,
        tuple[CodeGraphNodeEvidence, ...],
        tuple[CodeGraphEdgeEvidence, ...],
        int,
    ]:
        node_by_entity = {item.mapping.entity_id: item for item in nodes}
        summaries = tuple(
            GraphNodeSummary.create(
                node_id=item.visual_id,
                label=item.mapping.qualified_name,
                node_type=item.mapping.symbol_kind.value,
                score=item.relevance,
            )
            for item in nodes
        )
        edge_summaries = tuple(
            GraphEdgeSummary.create(
                edge_id=item.visual_id,
                source_id=node_by_entity[item.mapping.source_entity_id].visual_id,
                target_id=node_by_entity[item.mapping.target_entity_id].visual_id,
                edge_type=item.mapping.relation_kind.value,
                score=item.relevance,
            )
            for item in edges
        )
        aggregate_summaries = tuple(
            GraphAggregate.create(
                aggregate_id=_hash("aggregate", item.aggregate_ref),
                label=item.label,
                count=item.count,
            )
            for item in aggregates[:128]
        )
        focus_visual = (
            _hash("node", request.node_entity_id) if request.node_entity_id else ""
        )
        page = ProgressiveGraphPage.create(
            graph_query_id=request.query_id,
            graph_ref=request.graph_ref,
            viewport=GraphViewport.create(
                viewport_ref=request.viewport_ref,
                node_ref=focus_visual,
            ),
            query_kind=request.query_kind,
            budget=request.budget.progressive(),
            nodes=summaries,
            edges=edge_summaries,
            aggregates=aggregate_summaries,
            node_count=len(summaries),
            edge_count=len(edge_summaries),
            status=status,
            partial=partial,
            clipped=clipped,
            next_cursor=next_cursor,
            reason=reason,
            next_action=next_action,
            evidence_ref=request.graph_ref,
        )
        node_evidence = tuple(
            CodeGraphNodeEvidence(
                item.visual_id,
                item.mapping.entity_id,
                item.mapping.source_id,
                item.mapping.source_version_id,
                item.mapping.fallback_key,
                item.mapping.location,
                item.mapping.evidence.method.value,
                item.mapping.evidence.confidence,
            )
            for item in nodes
        )
        edge_evidence = tuple(
            CodeGraphEdgeEvidence(
                item.visual_id,
                item.mapping.relation_id,
                node_by_entity[item.mapping.source_entity_id].visual_id,
                node_by_entity[item.mapping.target_entity_id].visual_id,
                item.mapping.relation_kind.value,
                item.mapping.fallback_key,
                item.mapping.evidence.method.value,
                item.mapping.evidence.confidence,
            )
            for item in edges
        )
        payload = {
            "page": page.audit_summary(),
            "nodes": [item.__dict__ if hasattr(item, "__dict__") else {
                "visual_node_id": item.visual_node_id,
                "entity_id": item.entity_id,
                "source_id": item.source_id,
                "source_version_id": item.source_version_id,
                "symbol_mapping_ref": item.symbol_mapping_ref,
                "locator": item.locator.to_dict(),
                "method": item.method,
                "confidence": item.confidence,
            } for item in node_evidence],
            "edges": [{
                "visual_edge_id": item.visual_edge_id,
                "relation_id": item.relation_id,
                "source_visual_node_id": item.source_visual_node_id,
                "target_visual_node_id": item.target_visual_node_id,
                "relation_kind": item.relation_kind,
                "edge_mapping_ref": item.edge_mapping_ref,
                "method": item.method,
                "confidence": item.confidence,
            } for item in edge_evidence],
        }
        payload_bytes = len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        return page, node_evidence, edge_evidence, payload_bytes


__all__ = [
    "CBM_GRAPH_SCHEMA",
    "CatalogCodeGraphStore",
    "CodeGraphAggregateRecord",
    "CodeGraphBudget",
    "CodeGraphEdgeEvidence",
    "CodeGraphEdgeRecord",
    "CodeGraphNodeEvidence",
    "CodeGraphNodeRecord",
    "CodeGraphPage",
    "CodeGraphQuery",
    "CodeGraphSlice",
    "CodeGraphStore",
    "CodebaseMemoryGraphError",
    "CodebaseMemoryGraphProjection",
]

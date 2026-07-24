"""Bounded structural queries over an injected Codebase Memory transport.

The provider exposes nine typed operations only.  Arbitrary engine query
languages, direct upstream tools, source reads, and process access are absent.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol

from src.code_intelligence_contract import (
    CodeEdgeMapping,
    CodeFileMapping,
    CodeSymbolMapping,
)
from src.codebase_memory_projection import (
    GenerationState,
    ProjectionGeneration,
)
from src.project_version_store import validate_repo_id
from src.unified_source_index_contract import RecordKind, RecordRef, RelationKind


CBM_QUERY_SCHEMA = "odysseus.codebase_memory.structural_query.v1"
MAX_LIMIT = 100
MAX_SCOPE_ENTITIES = 256
MAX_NODES_EXAMINED = 10_000
MAX_EDGES_EXAMINED = 20_000
MAX_DEPTH = 16
MAX_TIMEOUT_MS = 30_000

_GENERATION_RE = re.compile(r"^cbm_generation_[0-9a-f]{64}$")
_FALLBACK_RE = re.compile(r"^cbm_(file|symbol|edge)_[0-9a-f]{64}$")
_CURSOR_RE = re.compile(r"^cbmq_([0-9]{1,10})_([0-9a-f]{64})$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_ERROR_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class CodebaseMemoryQueryError(ValueError):
    """Raised when a structural query or engine response fails closed."""


class StructuralOperation(StrEnum):
    SYMBOL = "symbol"
    CALLERS = "callers"
    CALLEES = "callees"
    IMPORTS = "imports"
    INHERITANCE = "inheritance"
    ROUTES = "routes"
    DATAFLOW = "dataflow"
    COMMUNITIES = "communities"
    IMPACT = "impact"


class QueryStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


_SUBJECT_REQUIRED = frozenset(
    {
        StructuralOperation.SYMBOL,
        StructuralOperation.CALLERS,
        StructuralOperation.CALLEES,
        StructuralOperation.IMPORTS,
        StructuralOperation.INHERITANCE,
        StructuralOperation.DATAFLOW,
        StructuralOperation.IMPACT,
    }
)
_ALLOWED_RELATIONS = {
    StructuralOperation.SYMBOL: frozenset(),
    StructuralOperation.CALLERS: frozenset({RelationKind.CALLS}),
    StructuralOperation.CALLEES: frozenset({RelationKind.CALLS}),
    StructuralOperation.IMPORTS: frozenset({RelationKind.IMPORTS}),
    StructuralOperation.INHERITANCE: frozenset({RelationKind.INHERITS}),
    StructuralOperation.ROUTES: frozenset({RelationKind.REFERENCES, RelationKind.DEFINES}),
    StructuralOperation.DATAFLOW: frozenset({RelationKind.REFERENCES}),
    StructuralOperation.COMMUNITIES: frozenset({RelationKind.RELATED_TO, RelationKind.BELONGS_TO}),
    StructuralOperation.IMPACT: frozenset(
        {
            RelationKind.CALLS,
            RelationKind.IMPORTS,
            RelationKind.INHERITS,
            RelationKind.REFERENCES,
            RelationKind.TESTS,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class QueryBudget:
    limit: int = 25
    max_nodes_examined: int = 500
    max_edges_examined: int = 1000
    max_depth: int = 4
    timeout_ms: int = 3000

    def __post_init__(self) -> None:
        object.__setattr__(self, "limit", _integer(self.limit, "limit", 1, MAX_LIMIT))
        object.__setattr__(
            self,
            "max_nodes_examined",
            _integer(self.max_nodes_examined, "max_nodes_examined", 1, MAX_NODES_EXAMINED),
        )
        object.__setattr__(
            self,
            "max_edges_examined",
            _integer(self.max_edges_examined, "max_edges_examined", 0, MAX_EDGES_EXAMINED),
        )
        object.__setattr__(self, "max_depth", _integer(self.max_depth, "max_depth", 0, MAX_DEPTH))
        object.__setattr__(self, "timeout_ms", _integer(self.timeout_ms, "timeout_ms", 10, MAX_TIMEOUT_MS))

    def to_dict(self) -> dict[str, int]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QueryBudget":
        required = {item.name for item in fields(cls)}
        if not isinstance(value, Mapping) or set(value) != required:
            raise CodebaseMemoryQueryError("query budget fields are incomplete")
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class StructuralQueryRequest:
    repo_id: str
    generation_ref: str
    operation: StructuralOperation
    subject_entity_id: str
    scope_entity_ids: tuple[str, ...]
    budget: QueryBudget
    cursor: str = ""

    def __post_init__(self) -> None:
        repo_id = validate_repo_id(self.repo_id)
        generation_ref = _generation_ref(self.generation_ref)
        operation = _operation(self.operation)
        subject = "" if self.subject_entity_id == "" else _entity_id(
            self.subject_entity_id, "subject_entity_id"
        )
        if operation in _SUBJECT_REQUIRED and not subject:
            raise CodebaseMemoryQueryError("operation requires subject_entity_id")
        scope = _entity_ids(self.scope_entity_ids, "scope_entity_ids")
        if subject and scope and subject not in scope:
            raise CodebaseMemoryQueryError("subject_entity_id must stay inside explicit scope")
        if not isinstance(self.budget, QueryBudget):
            raise CodebaseMemoryQueryError("budget must be QueryBudget")
        if self.cursor and not _CURSOR_RE.fullmatch(self.cursor):
            raise CodebaseMemoryQueryError("cursor is invalid")
        object.__setattr__(self, "repo_id", repo_id)
        object.__setattr__(self, "generation_ref", generation_ref)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "subject_entity_id", subject)
        object.__setattr__(self, "scope_entity_ids", scope)

    def identity_payload(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "generation_ref": self.generation_ref,
            "operation": self.operation.value,
            "subject_entity_id": self.subject_entity_id,
            "scope_entity_ids": list(self.scope_entity_ids),
            "budget": self.budget.to_dict(),
        }

    @property
    def query_id(self) -> str:
        return "cbm_query_" + _hash(self.identity_payload())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CBM_QUERY_SCHEMA}.request",
            **self.identity_payload(),
            "cursor": self.cursor,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StructuralQueryRequest":
        required = {
            "schema",
            "repo_id",
            "generation_ref",
            "operation",
            "subject_entity_id",
            "scope_entity_ids",
            "budget",
            "cursor",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise CodebaseMemoryQueryError("query request fields are incomplete or unknown")
        if value.get("schema") != f"{CBM_QUERY_SCHEMA}.request":
            raise CodebaseMemoryQueryError("query request schema is invalid")
        if not isinstance(value["scope_entity_ids"], list):
            raise CodebaseMemoryQueryError("scope_entity_ids must be a list")
        return cls(
            repo_id=value["repo_id"],
            generation_ref=value["generation_ref"],
            operation=value["operation"],
            subject_entity_id=value["subject_entity_id"],
            scope_entity_ids=tuple(value["scope_entity_ids"]),
            budget=QueryBudget.from_dict(value["budget"]),
            cursor=value["cursor"],
        )


@dataclass(frozen=True, slots=True)
class EngineStructuralRequest:
    query_id: str
    repo_id: str
    generation_ref: str
    operation: StructuralOperation
    subject_entity_id: str
    scope_entity_ids: tuple[str, ...]
    offset: int
    budget: QueryBudget


class StructuralQueryTransport(Protocol):
    def query_structural(self, request: EngineStructuralRequest) -> Mapping[str, Any]: ...


class ProviderUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StructuralNode:
    mapping: CodeSymbolMapping

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, CodeSymbolMapping):
            raise CodebaseMemoryQueryError("node mapping must be CodeSymbolMapping")

    @property
    def entity_id(self) -> str:
        return self.mapping.entity_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.mapping.entity_id,
            "source_id": self.mapping.source_id,
            "source_version_id": self.mapping.source_version_id,
            "symbol_mapping_ref": self.mapping.fallback_key,
            "file_mapping_ref": self.mapping.file_fallback_key,
            "symbol_kind": self.mapping.symbol_kind.value,
            "qualified_name": self.mapping.qualified_name,
            "signature_fingerprint": self.mapping.signature_fingerprint,
            "location": self.mapping.location.to_dict(),
            "method": self.mapping.evidence.method.value,
            "confidence": self.mapping.evidence.confidence,
            "extractor_version": self.mapping.evidence.extractor_version,
            "incomplete_parse": self.mapping.evidence.incomplete_parse,
        }


@dataclass(frozen=True, slots=True)
class StructuralEdge:
    mapping: CodeEdgeMapping

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, CodeEdgeMapping):
            raise CodebaseMemoryQueryError("edge mapping must be CodeEdgeMapping")

    @property
    def stable_key(self) -> str:
        return self.mapping.fallback_key

    @property
    def relation_kind(self) -> RelationKind:
        return self.mapping.relation_kind

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": True,
            "relation_id": self.mapping.relation_id,
            "source_entity_id": self.mapping.source_entity_id,
            "target_entity_id": self.mapping.target_entity_id,
            "relation_kind": self.mapping.relation_kind.value,
            "edge_mapping_ref": self.mapping.fallback_key,
            "method": self.mapping.evidence.method.value,
            "confidence": self.mapping.evidence.confidence,
            "extractor_version": self.mapping.evidence.extractor_version,
            "incomplete_parse": self.mapping.evidence.incomplete_parse,
            "unresolved_reason": "",
        }


@dataclass(frozen=True, slots=True)
class UnresolvedStructuralEdge:
    unresolved_ref: str
    source_entity_id: str
    target_engine_ref: str
    relation_kind: RelationKind
    method: str
    confidence: float
    extractor_version: str
    incomplete_parse: bool
    unresolved_reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "unresolved_ref", _token(self.unresolved_ref, "unresolved_ref"))
        object.__setattr__(self, "source_entity_id", _entity_id(self.source_entity_id, "source_entity_id"))
        object.__setattr__(self, "target_engine_ref", _token(self.target_engine_ref, "target_engine_ref"))
        try:
            relation = self.relation_kind if isinstance(self.relation_kind, RelationKind) else RelationKind(self.relation_kind)
        except (TypeError, ValueError) as exc:
            raise CodebaseMemoryQueryError("unresolved relation_kind is invalid") from exc
        object.__setattr__(self, "relation_kind", relation)
        object.__setattr__(self, "method", _token(self.method, "method"))
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise CodebaseMemoryQueryError("unresolved confidence must be numeric")
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise CodebaseMemoryQueryError("unresolved confidence is outside range")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "extractor_version", _token(self.extractor_version, "extractor_version"))
        if not isinstance(self.incomplete_parse, bool):
            raise CodebaseMemoryQueryError("incomplete_parse must be boolean")
        object.__setattr__(self, "unresolved_reason", _error_code(self.unresolved_reason))

    @property
    def stable_key(self) -> str:
        return "unresolved:" + self.unresolved_ref

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": False,
            "relation_id": "",
            "source_entity_id": self.source_entity_id,
            "target_entity_id": "",
            "target_engine_ref": self.target_engine_ref,
            "relation_kind": self.relation_kind.value,
            "edge_mapping_ref": "",
            "unresolved_ref": self.unresolved_ref,
            "method": self.method,
            "confidence": self.confidence,
            "extractor_version": self.extractor_version,
            "incomplete_parse": self.incomplete_parse,
            "unresolved_reason": self.unresolved_reason,
        }


QueryEdge = StructuralEdge | UnresolvedStructuralEdge


@dataclass(frozen=True, slots=True)
class QueryStats:
    nodes_examined: int
    edges_examined: int
    duration_ms: int
    timed_out: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes_examined": self.nodes_examined,
            "edges_examined": self.edges_examined,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
        }


@dataclass(frozen=True, slots=True)
class StructuralQueryResult:
    query_id: str
    operation: StructuralOperation
    generation_ref: str
    status: QueryStatus
    nodes: tuple[StructuralNode, ...]
    edges: tuple[QueryEdge, ...]
    stats: QueryStats
    next_cursor: str
    clipped: bool
    stale: bool
    unresolved_edge_count: int
    failure_code: str
    fallback_required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": f"{CBM_QUERY_SCHEMA}.result",
            "query_id": self.query_id,
            "operation": self.operation.value,
            "generation_ref": self.generation_ref,
            "status": self.status.value,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
            "stats": self.stats.to_dict(),
            "next_cursor": self.next_cursor,
            "clipped": self.clipped,
            "stale": self.stale,
            "unresolved_edge_count": self.unresolved_edge_count,
            "failure_code": self.failure_code,
            "fallback_required": self.fallback_required,
        }


class StructuralMappingCatalog:
    def __init__(
        self,
        generation: ProjectionGeneration,
        *,
        files: Iterable[CodeFileMapping],
        symbols: Iterable[CodeSymbolMapping],
        edges: Iterable[CodeEdgeMapping],
    ) -> None:
        if not isinstance(generation, ProjectionGeneration):
            raise CodebaseMemoryQueryError("generation must be ProjectionGeneration")
        file_items = _typed_items(files, CodeFileMapping, "files", 100_000)
        symbol_items = _typed_items(symbols, CodeSymbolMapping, "symbols", 100_000)
        edge_items = _typed_items(edges, CodeEdgeMapping, "edges", 200_000)
        self.generation = generation
        files_by_key = _unique_by(file_items, lambda item: item.fallback_key, "file mapping")
        symbols_by_key = _unique_by(symbol_items, lambda item: item.fallback_key, "symbol mapping")
        edges_by_key = _unique_by(edge_items, lambda item: item.fallback_key, "edge mapping")
        self.files = MappingProxyType(files_by_key)
        self.symbols = MappingProxyType(symbols_by_key)
        self.edges = MappingProxyType(edges_by_key)
        if set(self.files) != set(generation.plan.engine_config.file_mapping_keys):
            raise CodebaseMemoryQueryError("catalog files do not match projection generation")
        entity_ids: set[str] = set()
        for symbol in self.symbols.values():
            file_mapping = self.files.get(symbol.file_fallback_key)
            if file_mapping is None or (
                symbol.source_id != file_mapping.source_id
                or symbol.source_version_id != file_mapping.source_version_id
            ):
                raise CodebaseMemoryQueryError("catalog symbol ancestry is invalid")
            if symbol.entity_id in entity_ids:
                raise CodebaseMemoryQueryError("catalog entity identity is duplicated")
            entity_ids.add(symbol.entity_id)
        for edge in self.edges.values():
            if edge.source_entity_id not in entity_ids or edge.target_entity_id not in entity_ids:
                raise CodebaseMemoryQueryError("catalog edge endpoint is unmapped")
        self.symbols_by_entity = MappingProxyType(
            {item.entity_id: item for item in self.symbols.values()}
        )


class StructuralQueryProvider:
    def __init__(self, transport: StructuralQueryTransport, catalog: StructuralMappingCatalog) -> None:
        if not hasattr(transport, "query_structural"):
            raise CodebaseMemoryQueryError("transport must implement query_structural")
        if not isinstance(catalog, StructuralMappingCatalog):
            raise CodebaseMemoryQueryError("catalog must be StructuralMappingCatalog")
        self._transport = transport
        self._catalog = catalog

    def query(self, request: StructuralQueryRequest) -> StructuralQueryResult:
        if not isinstance(request, StructuralQueryRequest):
            raise CodebaseMemoryQueryError("request must be StructuralQueryRequest")
        generation = self._catalog.generation
        if request.repo_id != generation.plan.repo_id or request.generation_ref != generation.generation_ref:
            raise CodebaseMemoryQueryError("request escapes the selected projection generation")
        if request.subject_entity_id and request.subject_entity_id not in self._catalog.symbols_by_entity:
            raise CodebaseMemoryQueryError("subject_entity_id is absent from projection catalog")
        if any(item not in self._catalog.symbols_by_entity for item in request.scope_entity_ids):
            raise CodebaseMemoryQueryError("scope_entity_ids escape projection catalog")
        offset = _cursor_offset(request)
        if generation.state not in {GenerationState.ACTIVE, GenerationState.STALE}:
            return _unavailable(request, "projection_unavailable")
        try:
            raw = self._transport.query_structural(
                EngineStructuralRequest(
                    request.query_id,
                    request.repo_id,
                    request.generation_ref,
                    request.operation,
                    request.subject_entity_id,
                    request.scope_entity_ids,
                    offset,
                    request.budget,
                )
            )
        except ProviderUnavailableError:
            return _unavailable(
                request,
                "provider_unavailable",
                stale=generation.state is GenerationState.STALE,
            )
        except Exception as exc:
            raise CodebaseMemoryQueryError("structural transport failed") from exc
        return self._result(request, raw, offset, generation.state is GenerationState.STALE)

    def _result(
        self,
        request: StructuralQueryRequest,
        raw: Mapping[str, Any],
        offset: int,
        stale: bool,
    ) -> StructuralQueryResult:
        required = {"node_keys", "edges", "has_more", "stats"}
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise CodebaseMemoryQueryError("structural response fields are invalid")
        if not isinstance(raw["node_keys"], list) or not isinstance(raw["edges"], list):
            raise CodebaseMemoryQueryError("structural response collections are invalid")
        if not isinstance(raw["has_more"], bool):
            raise CodebaseMemoryQueryError("has_more must be boolean")
        nodes = self._nodes(raw["node_keys"], request)
        edges = self._edges(raw["edges"], request)
        if len(nodes) + len(edges) > request.budget.limit:
            raise CodebaseMemoryQueryError("structural response exceeds result limit")
        stats = _stats(raw["stats"], request.budget)
        if stats.nodes_examined < len(nodes) or stats.edges_examined < len(edges):
            raise CodebaseMemoryQueryError("query stats under-report returned work")
        returned = len(nodes) + len(edges)
        if raw["has_more"] and returned == 0:
            raise CodebaseMemoryQueryError("empty structural page cannot claim more results")
        next_cursor = _cursor(request, offset + returned) if raw["has_more"] else ""
        unresolved = sum(isinstance(item, UnresolvedStructuralEdge) for item in edges)
        clipped = raw["has_more"] or stats.timed_out
        status = (
            QueryStatus.PARTIAL
            if stale or clipped or unresolved
            else QueryStatus.COMPLETE
        )
        failure_code = "query_timeout" if stats.timed_out else ""
        return StructuralQueryResult(
            request.query_id,
            request.operation,
            request.generation_ref,
            status,
            nodes,
            edges,
            stats,
            next_cursor,
            clipped,
            stale,
            unresolved,
            failure_code,
            False,
        )

    def _nodes(
        self, values: list[Any], request: StructuralQueryRequest
    ) -> tuple[StructuralNode, ...]:
        if len(values) > request.budget.limit:
            raise CodebaseMemoryQueryError("node result is unbounded")
        keys = tuple(_fallback_key(item, "symbol") for item in values)
        if len(set(keys)) != len(keys):
            raise CodebaseMemoryQueryError("node result contains duplicates")
        nodes: list[StructuralNode] = []
        for key in keys:
            try:
                mapping = self._catalog.symbols[key]
            except KeyError as exc:
                raise CodebaseMemoryQueryError("node result references an unknown mapping") from exc
            if request.scope_entity_ids and mapping.entity_id not in request.scope_entity_ids:
                raise CodebaseMemoryQueryError("node result escapes explicit entity scope")
            nodes.append(StructuralNode(mapping))
        return tuple(sorted(nodes, key=lambda item: (item.entity_id, item.mapping.fallback_key)))

    def _edges(self, values: list[Any], request: StructuralQueryRequest) -> tuple[QueryEdge, ...]:
        if len(values) > request.budget.limit:
            raise CodebaseMemoryQueryError("edge result is unbounded")
        results: list[QueryEdge] = []
        for value in values:
            if not isinstance(value, Mapping):
                raise CodebaseMemoryQueryError("edge result item must be an object")
            if set(value) == {"edge_key"}:
                key = _fallback_key(value["edge_key"], "edge")
                try:
                    item: QueryEdge = StructuralEdge(self._catalog.edges[key])
                except KeyError as exc:
                    raise CodebaseMemoryQueryError("edge result references an unknown mapping") from exc
            else:
                required = {
                    "unresolved_ref",
                    "source_symbol_key",
                    "target_engine_ref",
                    "relation_kind",
                    "method",
                    "confidence",
                    "extractor_version",
                    "incomplete_parse",
                    "unresolved_reason",
                }
                if set(value) != required:
                    raise CodebaseMemoryQueryError("unresolved edge fields are invalid")
                source_key = _fallback_key(value["source_symbol_key"], "symbol")
                try:
                    source = self._catalog.symbols[source_key]
                except KeyError as exc:
                    raise CodebaseMemoryQueryError("unresolved edge source is unknown") from exc
                item = UnresolvedStructuralEdge(
                    value["unresolved_ref"],
                    source.entity_id,
                    value["target_engine_ref"],
                    value["relation_kind"],
                    value["method"],
                    value["confidence"],
                    value["extractor_version"],
                    value["incomplete_parse"],
                    value["unresolved_reason"],
                )
            if item.relation_kind not in _ALLOWED_RELATIONS[request.operation]:
                raise CodebaseMemoryQueryError("edge relation is invalid for structural operation")
            endpoints = (
                (item.mapping.source_entity_id, item.mapping.target_entity_id)
                if isinstance(item, StructuralEdge)
                else (item.source_entity_id, "")
            )
            if request.scope_entity_ids and any(
                endpoint and endpoint not in request.scope_entity_ids for endpoint in endpoints
            ):
                raise CodebaseMemoryQueryError("edge result escapes explicit entity scope")
            results.append(item)
        keys = [item.stable_key for item in results]
        if len(set(keys)) != len(keys):
            raise CodebaseMemoryQueryError("edge result contains duplicates")
        return tuple(sorted(results, key=lambda item: item.stable_key))


def _stats(value: Mapping[str, Any], budget: QueryBudget) -> QueryStats:
    required = {"nodes_examined", "edges_examined", "duration_ms", "timed_out"}
    if not isinstance(value, Mapping) or set(value) != required or not isinstance(value["timed_out"], bool):
        raise CodebaseMemoryQueryError("query stats fields are invalid")
    nodes = _integer(value["nodes_examined"], "nodes_examined", 0, budget.max_nodes_examined)
    edges = _integer(value["edges_examined"], "edges_examined", 0, budget.max_edges_examined)
    duration = _integer(value["duration_ms"], "duration_ms", 0, budget.timeout_ms)
    return QueryStats(nodes, edges, duration, value["timed_out"])


def _unavailable(
    request: StructuralQueryRequest, failure_code: str, *, stale: bool = False
) -> StructuralQueryResult:
    return StructuralQueryResult(
        request.query_id,
        request.operation,
        request.generation_ref,
        QueryStatus.UNAVAILABLE,
        (),
        (),
        QueryStats(0, 0, 0, False),
        "",
        False,
        stale,
        0,
        _error_code(failure_code),
        True,
    )


def _cursor_offset(request: StructuralQueryRequest) -> int:
    if not request.cursor:
        return 0
    match = _CURSOR_RE.fullmatch(request.cursor)
    assert match is not None
    offset = int(match.group(1))
    if offset > 1_000_000_000:
        raise CodebaseMemoryQueryError("cursor offset is outside its bounded range")
    expected = _hash({"query_id": request.query_id, "offset": offset})
    if match.group(2) != expected:
        raise CodebaseMemoryQueryError("cursor does not match query identity")
    return offset


def _cursor(request: StructuralQueryRequest, offset: int) -> str:
    if not 1 <= offset <= 1_000_000_000:
        raise CodebaseMemoryQueryError("cursor offset is outside its bounded range")
    return f"cbmq_{offset}_{_hash({'query_id': request.query_id, 'offset': offset})}"


def _operation(value: Any) -> StructuralOperation:
    if isinstance(value, StructuralOperation):
        return value
    try:
        return StructuralOperation(value)
    except (TypeError, ValueError) as exc:
        raise CodebaseMemoryQueryError("unknown structural operation") from exc


def _entity_id(value: Any, field_name: str) -> str:
    try:
        return RecordRef(RecordKind.ENTITY, value).record_id
    except Exception as exc:
        raise CodebaseMemoryQueryError(f"{field_name} is not a USI entity id") from exc


def _entity_ids(values: Iterable[Any], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise CodebaseMemoryQueryError(f"{field_name} must be a sequence")
    try:
        items = tuple(_entity_id(item, field_name) for item in values)
    except TypeError as exc:
        raise CodebaseMemoryQueryError(f"{field_name} must be iterable") from exc
    if len(items) > MAX_SCOPE_ENTITIES or len(set(items)) != len(items):
        raise CodebaseMemoryQueryError(f"{field_name} is unbounded or duplicated")
    return tuple(sorted(items))


def _generation_ref(value: Any) -> str:
    if not isinstance(value, str) or not _GENERATION_RE.fullmatch(value):
        raise CodebaseMemoryQueryError("generation_ref is invalid")
    return value


def _fallback_key(value: Any, kind: str) -> str:
    if (
        not isinstance(value, str)
        or not _FALLBACK_RE.fullmatch(value)
        or not value.startswith(f"cbm_{kind}_")
    ):
        raise CodebaseMemoryQueryError(f"{kind} mapping key is invalid")
    return value


def _token(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise CodebaseMemoryQueryError(f"{field_name} must be an opaque bounded token")
    return value


def _error_code(value: Any) -> str:
    if not isinstance(value, str) or not _ERROR_RE.fullmatch(value):
        raise CodebaseMemoryQueryError("failure code is invalid")
    return value


def _integer(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CodebaseMemoryQueryError(f"{field_name} is outside its bounded range")
    return value


def _typed_items(values: Iterable[Any], expected: type[Any], field_name: str, maximum: int) -> tuple[Any, ...]:
    try:
        items = tuple(values)
    except TypeError as exc:
        raise CodebaseMemoryQueryError(f"{field_name} must be iterable") from exc
    if len(items) > maximum or not all(isinstance(item, expected) for item in items):
        raise CodebaseMemoryQueryError(f"{field_name} must be typed and bounded")
    return items


def _unique_by(items: Iterable[Any], key, label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        item_key = key(item)
        if item_key in result:
            raise CodebaseMemoryQueryError(f"duplicate {label}")
        result[item_key] = item
    return result


def _hash(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(rendered).hexdigest()

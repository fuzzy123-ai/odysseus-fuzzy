"""Bounded hybrid code-intelligence planning on top of USI and CBM.

The module contributes a provider adapter and an orchestration contract only.
It does not register providers globally and performs no filesystem, process,
network, source-content, configuration, hook, or live-system access.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import re
from typing import Protocol, runtime_checkable

from src.codebase_memory_query import (
    CodebaseMemoryQueryError,
    QueryBudget,
    QueryStatus,
    StructuralEdge,
    StructuralOperation,
    StructuralQueryProvider,
    StructuralQueryRequest,
    StructuralQueryResult,
    UnresolvedStructuralEdge,
)
from src.project_version_store import validate_repo_id
from src.unified_source_index_contract import (
    Classification,
    EvidenceRef,
    RecordKind,
    RecordRef,
)
from src.unified_source_index_query import (
    FederatedQuery,
    FederatedQueryBudget,
    FederatedQueryPage,
    ProviderQueryRequest,
    ProviderRegistration,
    ProviderResultItem,
    ProviderResultPage,
    QueryMode,
    UnifiedSourceIndexQueryPlanner,
)


CODE_INTELLIGENCE_RETRIEVAL_SCHEMA = "odysseus.code_intelligence.retrieval.v1"
MAX_QUERY_CHARS = 4096
MAX_SCOPE_ENTITIES = 1000

_GENERATION_RE = re.compile(r"^cbm_generation_[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class CodeIntelligenceRetrievalError(ValueError):
    """Raised when hybrid retrieval would be ambiguous, unsafe, or unbounded."""


class StructuralLaneUnavailable(RuntimeError):
    """Signals a clean structural failure so the USI planner can use fallback."""


class RetrievalIntent(StrEnum):
    SYMBOL = "symbol"
    CALLERS = "callers"
    CALLEES = "callees"
    IMPORTS = "imports"
    INHERITANCE = "inheritance"
    ROUTES = "routes"
    DATAFLOW = "dataflow"
    COMMUNITIES = "communities"
    IMPACT = "impact"
    DYNAMIC_CODE = "dynamic_code"
    EXHAUSTIVE = "exhaustive"
    VOCABULARY_MISMATCH = "vocabulary_mismatch"


_STRUCTURAL_OPERATION = {
    RetrievalIntent.SYMBOL: StructuralOperation.SYMBOL,
    RetrievalIntent.CALLERS: StructuralOperation.CALLERS,
    RetrievalIntent.CALLEES: StructuralOperation.CALLEES,
    RetrievalIntent.IMPORTS: StructuralOperation.IMPORTS,
    RetrievalIntent.INHERITANCE: StructuralOperation.INHERITANCE,
    RetrievalIntent.ROUTES: StructuralOperation.ROUTES,
    RetrievalIntent.DATAFLOW: StructuralOperation.DATAFLOW,
    RetrievalIntent.COMMUNITIES: StructuralOperation.COMMUNITIES,
    RetrievalIntent.IMPACT: StructuralOperation.IMPACT,
}
_SUBJECT_REQUIRED = frozenset(
    {
        RetrievalIntent.SYMBOL,
        RetrievalIntent.CALLERS,
        RetrievalIntent.CALLEES,
        RetrievalIntent.IMPORTS,
        RetrievalIntent.INHERITANCE,
        RetrievalIntent.DATAFLOW,
        RetrievalIntent.IMPACT,
    }
)
_EXACT_LEXICAL = frozenset(
    {RetrievalIntent.SYMBOL, RetrievalIntent.DYNAMIC_CODE, RetrievalIntent.EXHAUSTIVE}
)


def _intent(value: RetrievalIntent | str) -> RetrievalIntent:
    if isinstance(value, RetrievalIntent):
        return value
    try:
        return RetrievalIntent(value)
    except (TypeError, ValueError) as exc:
        raise CodeIntelligenceRetrievalError("intent is invalid") from exc


def _generation_ref(value: str) -> str:
    if not isinstance(value, str) or not _GENERATION_RE.fullmatch(value):
        raise CodeIntelligenceRetrievalError("generation_ref is invalid")
    return value


def _query_text(value: str) -> str:
    if not isinstance(value, str):
        raise CodeIntelligenceRetrievalError("query_text must be text")
    if _CONTROL_RE.search(value):
        raise CodeIntelligenceRetrievalError("query_text is empty, invalid, or unbounded")
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > MAX_QUERY_CHARS:
        raise CodeIntelligenceRetrievalError("query_text is empty, invalid, or unbounded")
    return normalized


def _entity_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > MAX_SCOPE_ENTITIES:
        raise CodeIntelligenceRetrievalError("scope_entity_ids must be a bounded tuple")
    try:
        normalized = tuple(
            sorted({RecordRef(RecordKind.ENTITY, value).record_id for value in values})
        )
    except Exception as exc:
        raise CodeIntelligenceRetrievalError("scope_entity_ids contains an invalid entity") from exc
    return normalized


@dataclass(frozen=True, slots=True)
class CodeIntelligenceRetrievalRequest:
    repo_id: str
    generation_ref: str
    owner_scope: str
    query_text: str
    classification_ceiling: Classification
    intent: RetrievalIntent
    subject_entity_id: str = ""
    scope_entity_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    limit: int = 20
    semantic_enabled: bool = False
    federated_budget: FederatedQueryBudget = FederatedQueryBudget()
    structural_budget: QueryBudget = QueryBudget(timeout_ms=750)

    def __post_init__(self) -> None:
        repo_id = validate_repo_id(self.repo_id)
        generation_ref = _generation_ref(self.generation_ref)
        intent = _intent(self.intent)
        query_text = _query_text(self.query_text)
        if not isinstance(self.semantic_enabled, bool):
            raise CodeIntelligenceRetrievalError("semantic_enabled must be boolean")
        if self.semantic_enabled and intent is not RetrievalIntent.VOCABULARY_MISMATCH:
            raise CodeIntelligenceRetrievalError(
                "semantic_enabled is reserved for vocabulary mismatch intent"
            )
        if not isinstance(self.federated_budget, FederatedQueryBudget):
            raise CodeIntelligenceRetrievalError("federated_budget must be typed")
        if not isinstance(self.structural_budget, QueryBudget):
            raise CodeIntelligenceRetrievalError("structural_budget must be typed")
        if (
            self.structural_budget.limit > self.federated_budget.max_candidates_per_provider
            or self.structural_budget.timeout_ms > self.federated_budget.per_provider_timeout_ms
        ):
            raise CodeIntelligenceRetrievalError("structural budget exceeds its federated lane")
        try:
            # Reuse the USI contract for owner, classification, source, limit,
            # time, and provider-count validation without touching a store.
            usi_boundary = FederatedQuery(
                self.owner_scope,
                query_text,
                self.classification_ceiling,
                modes=(QueryMode.LEXICAL,),
                limit=self.limit,
                source_ids=self.source_ids,
                budget=self.federated_budget,
            )
        except Exception as exc:
            raise CodeIntelligenceRetrievalError("USI query boundary is invalid") from exc
        subject = ""
        if self.subject_entity_id:
            try:
                subject = RecordRef(RecordKind.ENTITY, self.subject_entity_id).record_id
            except Exception as exc:
                raise CodeIntelligenceRetrievalError("subject_entity_id is invalid") from exc
        scope = _entity_ids(self.scope_entity_ids)
        if intent in _SUBJECT_REQUIRED and not subject:
            raise CodeIntelligenceRetrievalError("intent requires subject_entity_id")
        if subject and scope and subject not in scope:
            raise CodeIntelligenceRetrievalError("subject_entity_id must remain in scope")
        if intent not in _STRUCTURAL_OPERATION and (subject or scope):
            raise CodeIntelligenceRetrievalError("non-structural intent cannot carry entity scope")
        required_lanes = 2 if intent in _STRUCTURAL_OPERATION else 1
        if intent is RetrievalIntent.VOCABULARY_MISMATCH and self.semantic_enabled:
            required_lanes = 2
        if self.federated_budget.max_providers < required_lanes:
            raise CodeIntelligenceRetrievalError("provider budget cannot preserve required fallback")
        object.__setattr__(self, "repo_id", repo_id)
        object.__setattr__(self, "generation_ref", generation_ref)
        object.__setattr__(self, "owner_scope", usi_boundary.owner_scope)
        object.__setattr__(self, "query_text", query_text)
        object.__setattr__(self, "classification_ceiling", usi_boundary.classification_ceiling)
        object.__setattr__(self, "intent", intent)
        object.__setattr__(self, "subject_entity_id", subject)
        object.__setattr__(self, "scope_entity_ids", scope)
        object.__setattr__(self, "source_ids", usi_boundary.source_ids)
        object.__setattr__(self, "limit", usi_boundary.limit)

    @property
    def request_id(self) -> str:
        payload = {
            "repo_id": self.repo_id,
            "generation_ref": self.generation_ref,
            "owner_scope": self.owner_scope,
            "query_text": self.query_text,
            "classification_ceiling": self.classification_ceiling.value,
            "intent": self.intent.value,
            "subject_entity_id": self.subject_entity_id,
            "scope_entity_ids": list(self.scope_entity_ids),
            "source_ids": list(self.source_ids),
            "limit": self.limit,
            "semantic_enabled": self.semantic_enabled,
            "federated_budget": {
                "max_providers": self.federated_budget.max_providers,
                "max_candidates_per_provider": self.federated_budget.max_candidates_per_provider,
                "max_total_candidates": self.federated_budget.max_total_candidates,
                "time_budget_ms": self.federated_budget.time_budget_ms,
                "per_provider_timeout_ms": self.federated_budget.per_provider_timeout_ms,
            },
            "structural_budget": self.structural_budget.to_dict(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "code_query_" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class HybridRetrievalPlan:
    request_id: str
    intent: RetrievalIntent
    structural_operation: StructuralOperation | None
    primary_modes: tuple[QueryMode, ...]
    fallback_modes: tuple[QueryMode, ...]
    requested_modes: tuple[QueryMode, ...]
    lexical_exact: bool
    semantic_optional: bool
    exact_read_recommended: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": f"{CODE_INTELLIGENCE_RETRIEVAL_SCHEMA}.plan",
            "request_id": self.request_id,
            "intent": self.intent.value,
            "structural_operation": (
                self.structural_operation.value if self.structural_operation else ""
            ),
            "primary_modes": [item.value for item in self.primary_modes],
            "fallback_modes": [item.value for item in self.fallback_modes],
            "requested_modes": [item.value for item in self.requested_modes],
            "lexical_exact": self.lexical_exact,
            "semantic_optional": self.semantic_optional,
            "exact_read_recommended": self.exact_read_recommended,
            "reasons": list(self.reasons),
        }


class HybridCodeQueryPlanner:
    """Select a deterministic provider mix from explicit code-query intent."""

    def plan(self, request: CodeIntelligenceRetrievalRequest) -> HybridRetrievalPlan:
        if not isinstance(request, CodeIntelligenceRetrievalRequest):
            raise CodeIntelligenceRetrievalError("request must be typed")
        operation = _STRUCTURAL_OPERATION.get(request.intent)
        if operation is not None:
            structural_mode = (
                QueryMode.SYMBOL if operation is StructuralOperation.SYMBOL else QueryMode.GRAPH
            )
            return HybridRetrievalPlan(
                request.request_id,
                request.intent,
                operation,
                (structural_mode,),
                (QueryMode.LEXICAL,),
                (structural_mode, QueryMode.LEXICAL),
                request.intent in _EXACT_LEXICAL,
                False,
                True,
                ("structural_first", "lexical_fallback", "usi_evidence_required"),
            )
        if request.intent is RetrievalIntent.VOCABULARY_MISMATCH:
            primary = (QueryMode.SEMANTIC,) if request.semantic_enabled else (QueryMode.LEXICAL,)
            fallback = (QueryMode.LEXICAL,) if request.semantic_enabled else ()
            requested = primary + tuple(item for item in fallback if item not in primary)
            return HybridRetrievalPlan(
                request.request_id,
                request.intent,
                None,
                primary,
                fallback,
                requested,
                False,
                True,
                True,
                ("semantic_vocabulary_bridge", "lexical_fallback", "usi_evidence_required"),
            )
        return HybridRetrievalPlan(
            request.request_id,
            request.intent,
            None,
            (QueryMode.LEXICAL,),
            (),
            (QueryMode.LEXICAL,),
            True,
            False,
            True,
            ("lexical_exact_first", "dynamic_or_exhaustive", "usi_evidence_required"),
        )


@runtime_checkable
class StructuralEvidenceResolver(Protocol):
    """Resolve a CBM entity ID back to its canonical USI evidence."""

    def resolve_entity(self, entity_id: str) -> EvidenceRef: ...


class _BoundStructuralUSIProvider:
    def __init__(
        self,
        provider: StructuralQueryProvider,
        resolver: StructuralEvidenceResolver,
        request: CodeIntelligenceRetrievalRequest,
        plan: HybridRetrievalPlan,
    ) -> None:
        if plan.structural_operation is None:
            raise CodeIntelligenceRetrievalError("structural adapter requires an operation")
        self._provider = provider
        self._resolver = resolver
        self._request = request
        self._plan = plan
        self._kind = plan.primary_modes[0]

    @property
    def provider_id(self) -> str:
        return f"code_intelligence.cbm.{self._kind.value}"

    @property
    def provider_kind(self) -> QueryMode:
        return self._kind

    def query(self, request: ProviderQueryRequest) -> ProviderResultPage:
        if not isinstance(request, ProviderQueryRequest):
            raise CodeIntelligenceRetrievalError("USI provider request must be typed")
        if (
            request.owner_scope != self._request.owner_scope
            or request.query_text != self._request.query_text
            or request.source_ids != self._request.source_ids
        ):
            raise CodeIntelligenceRetrievalError("USI request does not match bound code query")
        if request.time_budget_ms < 10:
            raise StructuralLaneUnavailable("structural_time_budget_exhausted")
        budget = replace(
            self._request.structural_budget,
            limit=min(self._request.structural_budget.limit, request.limit),
            timeout_ms=min(self._request.structural_budget.timeout_ms, request.time_budget_ms),
        )
        structural_request = StructuralQueryRequest(
            self._request.repo_id,
            self._request.generation_ref,
            self._plan.structural_operation,
            self._request.subject_entity_id,
            self._request.scope_entity_ids,
            budget,
        )
        try:
            result = self._provider.query(structural_request)
        except CodebaseMemoryQueryError:
            raise
        if not isinstance(result, StructuralQueryResult):
            raise CodeIntelligenceRetrievalError("CBM returned an untyped result")
        if result.status is QueryStatus.UNAVAILABLE or result.fallback_required:
            raise StructuralLaneUnavailable(result.failure_code or "structural_unavailable")
        candidates: dict[str, ProviderResultItem] = {}
        for node in result.nodes:
            self._add_candidate(
                candidates,
                node.entity_id,
                node.mapping.evidence.confidence,
                f"cbm_{result.operation.value}_node",
                result.stale,
                expected_source_id=node.mapping.source_id,
                expected_version_id=node.mapping.source_version_id,
                expected_locator=node.mapping.location.to_usi_locator(),
            )
        for edge in result.edges:
            confidence = edge.mapping.evidence.confidence if isinstance(edge, StructuralEdge) else edge.confidence
            self._add_candidate(
                candidates,
                edge.mapping.source_entity_id if isinstance(edge, StructuralEdge) else edge.source_entity_id,
                confidence,
                f"cbm_{result.operation.value}_edge_source",
                result.stale,
            )
            if isinstance(edge, StructuralEdge):
                self._add_candidate(
                    candidates,
                    edge.mapping.target_entity_id,
                    confidence,
                    f"cbm_{result.operation.value}_edge_target",
                    result.stale,
                )
            elif not isinstance(edge, UnresolvedStructuralEdge):
                raise CodeIntelligenceRetrievalError("CBM result contains an unknown edge type")
        ordered = tuple(
            sorted(candidates.values(), key=lambda item: (-item.score, item.evidence.record_id))
        )
        clipped = (
            result.clipped
            or result.status is QueryStatus.PARTIAL
            or len(ordered) > request.limit
        )
        return ProviderResultPage(
            ordered[: request.limit],
            clipped=clipped,
            snapshot_ref=result.generation_ref,
        )

    def _add_candidate(
        self,
        candidates: dict[str, ProviderResultItem],
        entity_id: str,
        score: float,
        reason: str,
        stale: bool,
        *,
        expected_source_id: str = "",
        expected_version_id: str = "",
        expected_locator: object | None = None,
    ) -> None:
        evidence = self._resolver.resolve_entity(entity_id)
        if not isinstance(evidence, EvidenceRef):
            raise CodeIntelligenceRetrievalError("resolver returned untyped evidence")
        if evidence.record_kind is not RecordKind.ENTITY or evidence.record_id != entity_id:
            raise CodeIntelligenceRetrievalError("resolver evidence does not match CBM entity")
        if expected_source_id and evidence.source_id != expected_source_id:
            raise CodeIntelligenceRetrievalError("resolver evidence source ancestry conflicts with CBM")
        if expected_version_id and evidence.source_version_id != expected_version_id:
            raise CodeIntelligenceRetrievalError("resolver evidence version ancestry conflicts with CBM")
        if expected_locator is not None and evidence.locator != expected_locator:
            raise CodeIntelligenceRetrievalError("resolver evidence locator conflicts with CBM")
        candidate = ProviderResultItem(
            evidence,
            round(float(score), 12),
            snippet="",
            snippet_clipped=False,
            stale=stale,
            why_selected=reason,
        )
        current = candidates.get(entity_id)
        if current is None or (-candidate.score, candidate.why_selected) < (
            -current.score,
            current.why_selected,
        ):
            candidates[entity_id] = candidate


@dataclass(frozen=True, slots=True)
class CodeIntelligenceRetrievalResult:
    plan: HybridRetrievalPlan
    page: FederatedQueryPage
    exact_read_recommended: bool
    exact_reader: str

    def __post_init__(self) -> None:
        if not isinstance(self.plan, HybridRetrievalPlan) or not isinstance(
            self.page, FederatedQueryPage
        ):
            raise CodeIntelligenceRetrievalError("retrieval result requires typed plan and page")
        if not isinstance(self.exact_read_recommended, bool):
            raise CodeIntelligenceRetrievalError("exact-read recommendation must be boolean")
        if self.exact_reader not in {"", "read_file"}:
            raise CodeIntelligenceRetrievalError("exact_reader is not canonical")


class CodeIntelligenceRetriever:
    """Compose local USI providers with one query-bound CBM provider."""

    def __init__(
        self,
        registrations: tuple[ProviderRegistration, ...],
        *,
        structural_provider: StructuralQueryProvider | None = None,
        evidence_resolver: StructuralEvidenceResolver | None = None,
        planner: HybridCodeQueryPlanner | None = None,
    ) -> None:
        if not isinstance(registrations, tuple) or not all(
            isinstance(item, ProviderRegistration) for item in registrations
        ):
            raise CodeIntelligenceRetrievalError("registrations must be a typed tuple")
        if any(
            item.provider.provider_kind not in {QueryMode.LEXICAL, QueryMode.SEMANTIC}
            for item in registrations
        ):
            raise CodeIntelligenceRetrievalError("base registrations may only own lexical or semantic lanes")
        try:
            UnifiedSourceIndexQueryPlanner(registrations)
        except Exception as exc:
            raise CodeIntelligenceRetrievalError("base registrations violate the USI planner contract") from exc
        if (structural_provider is None) != (evidence_resolver is None):
            raise CodeIntelligenceRetrievalError("structural provider and resolver must be supplied together")
        if structural_provider is not None and not callable(
            getattr(structural_provider, "query", None)
        ):
            raise CodeIntelligenceRetrievalError("structural_provider must expose a query method")
        if evidence_resolver is not None and not isinstance(evidence_resolver, StructuralEvidenceResolver):
            raise CodeIntelligenceRetrievalError("evidence_resolver does not implement its protocol")
        self._registrations = registrations
        self._structural_provider = structural_provider
        self._evidence_resolver = evidence_resolver
        self._planner = planner or HybridCodeQueryPlanner()

    def plan(self, request: CodeIntelligenceRetrievalRequest) -> HybridRetrievalPlan:
        return self._planner.plan(request)

    def query(self, request: CodeIntelligenceRetrievalRequest) -> CodeIntelligenceRetrievalResult:
        plan = self.plan(request)
        selected_modes = set(plan.requested_modes)
        registrations = tuple(
            item for item in self._registrations if item.provider.provider_kind in selected_modes
        )
        if (
            plan.structural_operation is not None
            and self._structural_provider is not None
            and self._evidence_resolver is not None
        ):
            registrations += (
                ProviderRegistration(
                    _BoundStructuralUSIProvider(
                        self._structural_provider,
                        self._evidence_resolver,
                        request,
                        plan,
                    ),
                    weight=1.25,
                ),
            )
        query = FederatedQuery(
            request.owner_scope,
            request.query_text,
            request.classification_ceiling,
            modes=plan.requested_modes,
            limit=request.limit,
            source_ids=request.source_ids,
            budget=request.federated_budget,
        )
        page = UnifiedSourceIndexQueryPlanner(registrations).execute(query)
        recommend = plan.exact_read_recommended and bool(page.items)
        return CodeIntelligenceRetrievalResult(
            plan,
            page,
            recommend,
            "read_file" if recommend else "",
        )


__all__ = [
    "CODE_INTELLIGENCE_RETRIEVAL_SCHEMA",
    "CodeIntelligenceRetrievalError",
    "CodeIntelligenceRetrievalRequest",
    "CodeIntelligenceRetrievalResult",
    "CodeIntelligenceRetriever",
    "HybridCodeQueryPlanner",
    "HybridRetrievalPlan",
    "RetrievalIntent",
    "StructuralEvidenceResolver",
    "StructuralLaneUnavailable",
]

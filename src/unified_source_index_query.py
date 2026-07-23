"""Deterministic, bounded federated query planner for the Unified Source Index.

The baseline planner uses no LLM.  It selects registered lexical, semantic,
symbol, graph and timeline providers in a fixed order, enforces shared budgets,
and preserves exact EvidenceRef values through fusion and deduplication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import math
import re
import time
from typing import Callable, Protocol, runtime_checkable

from src.unified_source_index_contract import (
    Classification,
    EvidenceRef,
    PolicyEvidence,
    RecordKind,
    RecordRef,
)
from src.unified_source_index_lexical import UnifiedSourceIndexLexicalRetriever
from src.unified_source_index_query_contract import (
    LexicalMatchMode,
    LexicalQuery,
    MAX_RESULT_SNIPPET_CHARS,
)
from src.unified_source_index_stores import _owner_scope


MAX_FEDERATED_PROVIDERS = 5
MAX_PROVIDER_CANDIDATES = 100
MAX_TOTAL_CANDIDATES = 500
MAX_FEDERATED_RESULTS = 100
MAX_QUERY_TEXT_CHARS = 512

_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_MODE_ORDER: tuple["QueryMode", ...]
_CLASSIFICATION_RANK = {
    Classification.PUBLIC: 0,
    Classification.PRIVATE: 1,
    Classification.SENSITIVE: 2,
    Classification.SECRET: 3,
    Classification.UNKNOWN: 4,
}


class FederatedQueryError(ValueError):
    """Raised when a federated query/provider violates the bounded contract."""


class QueryMode(StrEnum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    SYMBOL = "symbol"
    GRAPH = "graph"
    TIMELINE = "timeline"
    HYBRID = "hybrid"


_MODE_ORDER = (
    QueryMode.LEXICAL,
    QueryMode.SEMANTIC,
    QueryMode.SYMBOL,
    QueryMode.GRAPH,
    QueryMode.TIMELINE,
)


class ProviderStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    MISSING = "missing"
    BUDGET_SKIPPED = "budget_skipped"


@dataclass(frozen=True, slots=True)
class FederatedQueryBudget:
    max_providers: int = 5
    max_candidates_per_provider: int = 50
    max_total_candidates: int = 200
    time_budget_ms: int = 2_000
    per_provider_timeout_ms: int = 750

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_providers",
            _integer(self.max_providers, "max_providers", 1, MAX_FEDERATED_PROVIDERS),
        )
        object.__setattr__(
            self,
            "max_candidates_per_provider",
            _integer(
                self.max_candidates_per_provider,
                "max_candidates_per_provider",
                1,
                MAX_PROVIDER_CANDIDATES,
            ),
        )
        object.__setattr__(
            self,
            "max_total_candidates",
            _integer(
                self.max_total_candidates,
                "max_total_candidates",
                1,
                MAX_TOTAL_CANDIDATES,
            ),
        )
        object.__setattr__(
            self,
            "time_budget_ms",
            _integer(self.time_budget_ms, "time_budget_ms", 1, 86_400_000),
        )
        timeout = _integer(
            self.per_provider_timeout_ms,
            "per_provider_timeout_ms",
            1,
            86_400_000,
        )
        if timeout > self.time_budget_ms:
            raise FederatedQueryError("per-provider timeout exceeds total time budget")
        object.__setattr__(self, "per_provider_timeout_ms", timeout)


@dataclass(frozen=True, slots=True)
class FederatedQuery:
    owner_scope: str
    query_text: str
    classification_ceiling: Classification
    modes: tuple[QueryMode, ...] = (QueryMode.HYBRID,)
    limit: int = 20
    source_ids: tuple[str, ...] = ()
    stale_after: str = ""
    budget: FederatedQueryBudget = field(default_factory=FederatedQueryBudget)

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_scope", _owner_scope(self.owner_scope))
        if not isinstance(self.query_text, str):
            raise FederatedQueryError("query_text must be text")
        text = self.query_text.strip()
        if not text or len(text) > MAX_QUERY_TEXT_CHARS or any(ord(char) < 32 for char in text):
            raise FederatedQueryError("query_text is empty, invalid or unbounded")
        object.__setattr__(self, "query_text", text)
        object.__setattr__(
            self,
            "classification_ceiling",
            _enum(self.classification_ceiling, Classification, "classification_ceiling"),
        )
        if not isinstance(self.modes, tuple) or not self.modes:
            raise FederatedQueryError("modes must be a non-empty tuple")
        modes = tuple(_enum(mode, QueryMode, "mode") for mode in self.modes)
        if QueryMode.HYBRID in modes and len(modes) != 1:
            raise FederatedQueryError("hybrid mode cannot be combined with explicit modes")
        if len(set(modes)) != len(modes):
            raise FederatedQueryError("modes must not contain duplicates")
        normalized = modes if modes == (QueryMode.HYBRID,) else tuple(
            mode for mode in _MODE_ORDER if mode in set(modes)
        )
        object.__setattr__(self, "modes", normalized)
        object.__setattr__(self, "limit", _integer(self.limit, "limit", 1, MAX_FEDERATED_RESULTS))
        if not isinstance(self.source_ids, tuple) or len(self.source_ids) > 1_000:
            raise FederatedQueryError("source_ids must be a bounded tuple")
        object.__setattr__(
            self,
            "source_ids",
            tuple(
                sorted(
                    {
                        RecordRef(RecordKind.SOURCE, source_id).record_id
                        for source_id in self.source_ids
                    }
                )
            ),
        )
        if not isinstance(self.stale_after, str) or len(self.stale_after) > 64:
            raise FederatedQueryError("stale_after is invalid or unbounded")
        if self.stale_after:
            try:
                stale_after = datetime.fromisoformat(self.stale_after.replace("Z", "+00:00"))
            except ValueError as exc:
                raise FederatedQueryError("stale_after must be an ISO-8601 timestamp") from exc
            if stale_after.tzinfo is None or stale_after.utcoffset() is None:
                raise FederatedQueryError("stale_after must include a timezone")
        if not isinstance(self.budget, FederatedQueryBudget):
            raise FederatedQueryError("budget must be typed")

    @property
    def requested_provider_kinds(self) -> tuple[QueryMode, ...]:
        return _MODE_ORDER if self.modes == (QueryMode.HYBRID,) else self.modes


@dataclass(frozen=True, slots=True)
class ProviderQueryRequest:
    owner_scope: str
    query_text: str
    classification_ceiling: Classification
    source_ids: tuple[str, ...]
    stale_after: str
    limit: int
    time_budget_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_scope", _owner_scope(self.owner_scope))
        if not isinstance(self.query_text, str) or not self.query_text:
            raise FederatedQueryError("provider query_text must be non-empty")
        object.__setattr__(
            self,
            "classification_ceiling",
            _enum(self.classification_ceiling, Classification, "classification_ceiling"),
        )
        if not isinstance(self.source_ids, tuple):
            raise FederatedQueryError("provider source_ids must be a tuple")
        object.__setattr__(self, "limit", _integer(self.limit, "limit", 1, MAX_PROVIDER_CANDIDATES))
        object.__setattr__(
            self,
            "time_budget_ms",
            _integer(self.time_budget_ms, "time_budget_ms", 1, 86_400_000),
        )


@dataclass(frozen=True, slots=True)
class ProviderResultItem:
    evidence: EvidenceRef
    score: float
    snippet: str = ""
    snippet_clipped: bool = False
    stale: bool = False
    why_selected: str = "provider_match"

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, EvidenceRef):
            raise FederatedQueryError("provider result requires typed evidence")
        object.__setattr__(self, "score", _score(self.score, "provider score"))
        if not isinstance(self.snippet, str) or len(self.snippet) > MAX_RESULT_SNIPPET_CHARS:
            raise FederatedQueryError("provider snippet is invalid or unbounded")
        if not isinstance(self.snippet_clipped, bool) or not isinstance(self.stale, bool):
            raise FederatedQueryError("provider result flags must be boolean")
        object.__setattr__(self, "why_selected", _token(self.why_selected, "why_selected"))

    @property
    def identity(self) -> tuple[RecordKind, str]:
        return self.evidence.record_kind, self.evidence.record_id


@dataclass(frozen=True, slots=True)
class ProviderResultPage:
    items: tuple[ProviderResultItem, ...]
    clipped: bool = False
    snapshot_ref: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or len(self.items) > MAX_PROVIDER_CANDIDATES:
            raise FederatedQueryError("provider items must be a bounded tuple")
        if not all(isinstance(item, ProviderResultItem) for item in self.items):
            raise FederatedQueryError("provider page contains an invalid item")
        if not isinstance(self.clipped, bool):
            raise FederatedQueryError("provider clipped flag must be boolean")
        if not isinstance(self.snapshot_ref, str) or len(self.snapshot_ref) > 128:
            raise FederatedQueryError("provider snapshot_ref is invalid or unbounded")


@runtime_checkable
class FederatedQueryProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def provider_kind(self) -> QueryMode: ...

    def query(self, request: ProviderQueryRequest) -> ProviderResultPage: ...


# Readable interface names for the five provider lanes.  Implementations share
# the same bounded method and declare their lane through provider_kind.
LexicalQueryProvider = FederatedQueryProvider
SemanticQueryProvider = FederatedQueryProvider
SymbolQueryProvider = FederatedQueryProvider
GraphQueryProvider = FederatedQueryProvider
TimelineQueryProvider = FederatedQueryProvider


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    provider: FederatedQueryProvider
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.provider, FederatedQueryProvider):
            raise FederatedQueryError("provider must implement FederatedQueryProvider")
        _token(self.provider.provider_id, "provider_id")
        kind = _enum(self.provider.provider_kind, QueryMode, "provider_kind")
        if kind is QueryMode.HYBRID:
            raise FederatedQueryError("providers must declare one concrete lane")
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)):
            raise FederatedQueryError("provider weight must be numeric")
        weight = round(float(self.weight), 12)
        if not math.isfinite(weight) or not 0.0 < weight <= 10.0:
            raise FederatedQueryError("provider weight is outside its bound")
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True, slots=True)
class QueryPlan:
    requested_kinds: tuple[QueryMode, ...]
    selected_provider_ids: tuple[str, ...]
    selected_kinds: tuple[QueryMode, ...]
    omitted_kinds: tuple[QueryMode, ...]
    budget: FederatedQueryBudget


@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    provider_id: str
    provider_kind: QueryMode
    status: ProviderStatus
    supplied_count: int
    accepted_count: int
    policy_filtered_count: int
    elapsed_ms: int
    clipped: bool = False
    snapshot_ref: str = ""
    error_code: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _token(self.provider_id, "provider_id"))
        object.__setattr__(self, "provider_kind", _enum(self.provider_kind, QueryMode, "provider_kind"))
        object.__setattr__(self, "status", _enum(self.status, ProviderStatus, "status"))
        for field_name in ("supplied_count", "accepted_count", "policy_filtered_count"):
            object.__setattr__(
                self,
                field_name,
                _integer(getattr(self, field_name), field_name, 0, MAX_TOTAL_CANDIDATES),
            )
        object.__setattr__(self, "elapsed_ms", _integer(self.elapsed_ms, "elapsed_ms", 0, 86_400_000))
        if not isinstance(self.clipped, bool):
            raise FederatedQueryError("outcome clipped flag must be boolean")
        if not isinstance(self.snapshot_ref, str) or len(self.snapshot_ref) > 128:
            raise FederatedQueryError("outcome snapshot_ref is invalid or unbounded")
        if self.error_code:
            object.__setattr__(self, "error_code", _token(self.error_code, "error_code"))


@dataclass(frozen=True, slots=True)
class ProviderScore:
    provider_id: str
    provider_kind: QueryMode
    raw_score: float
    rank: int
    contribution: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _token(self.provider_id, "provider_id"))
        object.__setattr__(self, "provider_kind", _enum(self.provider_kind, QueryMode, "provider_kind"))
        object.__setattr__(self, "raw_score", _score(self.raw_score, "raw_score"))
        object.__setattr__(self, "rank", _integer(self.rank, "rank", 1, MAX_PROVIDER_CANDIDATES))
        object.__setattr__(self, "contribution", _score(self.contribution, "contribution"))


@dataclass(frozen=True, slots=True)
class FederatedResultItem:
    evidence: EvidenceRef
    fused_score: float
    provider_scores: tuple[ProviderScore, ...]
    snippet: str
    snippet_clipped: bool
    stale: bool
    why_selected: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, EvidenceRef):
            raise FederatedQueryError("federated result requires exact evidence")
        object.__setattr__(self, "fused_score", _score(self.fused_score, "fused_score"))
        if not isinstance(self.provider_scores, tuple) or not self.provider_scores:
            raise FederatedQueryError("provider_scores must be a non-empty tuple")
        if not all(isinstance(item, ProviderScore) for item in self.provider_scores):
            raise FederatedQueryError("provider_scores contains an invalid item")
        if not isinstance(self.snippet, str) or len(self.snippet) > MAX_RESULT_SNIPPET_CHARS:
            raise FederatedQueryError("federated snippet is invalid or unbounded")
        if not isinstance(self.snippet_clipped, bool) or not isinstance(self.stale, bool):
            raise FederatedQueryError("federated result flags must be boolean")
        if not isinstance(self.why_selected, tuple) or not self.why_selected:
            raise FederatedQueryError("why_selected must be a non-empty tuple")
        object.__setattr__(
            self,
            "why_selected",
            tuple(sorted({_token(value, "why_selected") for value in self.why_selected})),
        )

    @property
    def source_ref(self) -> RecordRef:
        return RecordRef(RecordKind.SOURCE, self.evidence.source_id)

    @property
    def source_version_ref(self) -> RecordRef:
        return RecordRef(RecordKind.SOURCE_VERSION, self.evidence.source_version_id)

    @property
    def record_ref(self) -> RecordRef:
        return RecordRef(self.evidence.record_kind, self.evidence.record_id)


@dataclass(frozen=True, slots=True)
class FederatedQueryPage:
    items: tuple[FederatedResultItem, ...]
    plan: QueryPlan
    outcomes: tuple[ProviderOutcome, ...]
    total_supplied_candidates: int
    policy_filtered_count: int
    partial: bool
    clipped: bool
    fallback_used: bool

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or len(self.items) > MAX_FEDERATED_RESULTS:
            raise FederatedQueryError("federated items must be a bounded tuple")
        if not all(isinstance(item, FederatedResultItem) for item in self.items):
            raise FederatedQueryError("federated page contains an invalid item")
        if not isinstance(self.plan, QueryPlan) or not isinstance(self.outcomes, tuple):
            raise FederatedQueryError("federated plan/outcomes must be typed")
        if not all(isinstance(item, ProviderOutcome) for item in self.outcomes):
            raise FederatedQueryError("federated outcome is invalid")
        object.__setattr__(
            self,
            "total_supplied_candidates",
            _integer(
                self.total_supplied_candidates,
                "total_supplied_candidates",
                0,
                MAX_TOTAL_CANDIDATES,
            ),
        )
        object.__setattr__(
            self,
            "policy_filtered_count",
            _integer(self.policy_filtered_count, "policy_filtered_count", 0, MAX_TOTAL_CANDIDATES),
        )
        if not all(isinstance(value, bool) for value in (self.partial, self.clipped, self.fallback_used)):
            raise FederatedQueryError("federated page state flags must be boolean")


class LexicalProviderAdapter:
    provider_id = "usi.lexical"
    provider_kind = QueryMode.LEXICAL

    def __init__(self, retriever: UnifiedSourceIndexLexicalRetriever) -> None:
        if not isinstance(retriever, UnifiedSourceIndexLexicalRetriever):
            raise FederatedQueryError("retriever must be a USI lexical retriever")
        self._retriever = retriever

    def query(self, request: ProviderQueryRequest) -> ProviderResultPage:
        lexical = LexicalQuery(
            owner_scope=request.owner_scope,
            query_text=request.query_text,
            match_mode=LexicalMatchMode.TOKEN,
            classification_ceiling=request.classification_ceiling,
            limit=request.limit,
            max_candidates=request.limit,
            source_ids=request.source_ids,
            stale_after=request.stale_after,
        )
        page = self._retriever.search(lexical)
        items = []
        for item in page.items:
            policy = PolicyEvidence(
                RecordKind.CHUNK,
                item.chunk_ref.record_id,
                item.source_ref.record_id,
                item.source_version_ref.record_id,
                item.owner_scope,
                item.classification,
                item.content_policy,
            )
            evidence = EvidenceRef(
                RecordKind.CHUNK,
                item.chunk_ref.record_id,
                item.source_ref.record_id,
                item.source_version_ref.record_id,
                item.locator,
                item.content_hash,
                policy,
            )
            items.append(
                ProviderResultItem(
                    evidence,
                    min(1.0, item.scores.total),
                    item.snippet,
                    item.snippet_clipped,
                    item.stale,
                    item.why_selected,
                )
            )
        return ProviderResultPage(
            tuple(items),
            clipped=page.clipped or page.partial,
            snapshot_ref=page.snapshot.snapshot_ref,
        )


class UnifiedSourceIndexQueryPlanner:
    def __init__(
        self,
        registrations: tuple[ProviderRegistration, ...],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(registrations, tuple) or len(registrations) > MAX_FEDERATED_PROVIDERS:
            raise FederatedQueryError("provider registrations must be a bounded tuple")
        if not callable(clock):
            raise FederatedQueryError("clock must be callable")
        by_kind: dict[QueryMode, ProviderRegistration] = {}
        ids: set[str] = set()
        for registration in registrations:
            if not isinstance(registration, ProviderRegistration):
                raise FederatedQueryError("provider registration must be typed")
            kind = _enum(registration.provider.provider_kind, QueryMode, "provider_kind")
            provider_id = _token(registration.provider.provider_id, "provider_id")
            if kind in by_kind or provider_id in ids:
                raise FederatedQueryError("provider kind/id is registered more than once")
            by_kind[kind] = registration
            ids.add(provider_id)
        self._by_kind = by_kind
        self._clock = clock

    def plan(self, query: FederatedQuery) -> QueryPlan:
        if not isinstance(query, FederatedQuery):
            raise FederatedQueryError("query must be a FederatedQuery")
        requested = query.requested_provider_kinds
        available = [kind for kind in requested if kind in self._by_kind]
        selected_kinds = tuple(available[: query.budget.max_providers])
        omitted = tuple(kind for kind in requested if kind not in selected_kinds)
        return QueryPlan(
            requested,
            tuple(self._by_kind[kind].provider.provider_id for kind in selected_kinds),
            selected_kinds,
            omitted,
            query.budget,
        )

    def execute(self, query: FederatedQuery) -> FederatedQueryPage:
        plan = self.plan(query)
        outcomes: list[ProviderOutcome] = []
        accepted: list[tuple[ProviderRegistration, ProviderResultItem, int]] = []
        accepted_evidence: dict[tuple[RecordKind, str], EvidenceRef] = {}
        total_supplied = 0
        policy_filtered = 0
        start = self._clock()

        for kind in plan.requested_kinds:
            if kind not in plan.selected_kinds:
                status = ProviderStatus.MISSING if kind not in self._by_kind else ProviderStatus.BUDGET_SKIPPED
                provider_id = self._by_kind[kind].provider.provider_id if kind in self._by_kind else f"missing.{kind.value}"
                outcomes.append(_outcome(provider_id, kind, status))
                continue
            registration = self._by_kind[kind]
            elapsed_total_ms = max(0, round((self._clock() - start) * 1_000))
            remaining_time = query.budget.time_budget_ms - elapsed_total_ms
            remaining_candidates = query.budget.max_total_candidates - total_supplied
            if remaining_time <= 0 or remaining_candidates <= 0:
                outcomes.append(
                    _outcome(registration.provider.provider_id, kind, ProviderStatus.BUDGET_SKIPPED)
                )
                continue
            limit = min(query.budget.max_candidates_per_provider, remaining_candidates)
            timeout = min(query.budget.per_provider_timeout_ms, remaining_time)
            request = ProviderQueryRequest(
                query.owner_scope,
                query.query_text,
                query.classification_ceiling,
                query.source_ids,
                query.stale_after,
                limit,
                timeout,
            )
            provider_start = self._clock()
            try:
                page = registration.provider.query(request)
                provider_elapsed = max(0, round((self._clock() - provider_start) * 1_000))
                if provider_elapsed > timeout:
                    outcomes.append(
                        _outcome(
                            registration.provider.provider_id,
                            kind,
                            ProviderStatus.TIMED_OUT,
                            elapsed_ms=provider_elapsed,
                            error_code="provider_timeout",
                        )
                    )
                    continue
                if not isinstance(page, ProviderResultPage) or len(page.items) > limit:
                    raise FederatedQueryError("provider exceeded its result limit")
                supplied = len(page.items)
                total_supplied += supplied
                provider_items = _dedupe_provider_items(page.items)
                filtered = 0
                provider_accepted: list[tuple[ProviderRegistration, ProviderResultItem, int]] = []
                for rank, item in enumerate(provider_items, start=1):
                    _require_owner(item, query.owner_scope)
                    if query.source_ids and item.evidence.source_id not in query.source_ids:
                        filtered += 1
                        continue
                    if _CLASSIFICATION_RANK[item.evidence.policy_evidence.classification] > _CLASSIFICATION_RANK[
                        query.classification_ceiling
                    ]:
                        filtered += 1
                        continue
                    known_evidence = accepted_evidence.get(item.identity)
                    if known_evidence is not None and known_evidence != item.evidence:
                        raise FederatedQueryError(
                            "providers returned conflicting evidence for one identity"
                        )
                    provider_accepted.append((registration, item, rank))
                accepted.extend(provider_accepted)
                for _, item, _ in provider_accepted:
                    accepted_evidence.setdefault(item.identity, item.evidence)
                policy_filtered += filtered
                outcomes.append(
                    ProviderOutcome(
                        registration.provider.provider_id,
                        kind,
                        ProviderStatus.PARTIAL if page.clipped or filtered else ProviderStatus.COMPLETED,
                        supplied,
                        len(provider_accepted),
                        filtered,
                        provider_elapsed,
                        page.clipped,
                        page.snapshot_ref,
                    )
                )
            except Exception as exc:
                provider_elapsed = max(0, round((self._clock() - provider_start) * 1_000))
                timed_out = provider_elapsed > timeout
                outcomes.append(
                    _outcome(
                        registration.provider.provider_id,
                        kind,
                        ProviderStatus.TIMED_OUT if timed_out else ProviderStatus.FAILED,
                        elapsed_ms=provider_elapsed,
                        error_code="provider_timeout" if timed_out else _exception_code(exc),
                    )
                )

        items = _fuse(accepted, plan, query.limit)
        degraded = any(outcome.status is not ProviderStatus.COMPLETED for outcome in outcomes)
        succeeded = any(
            outcome.status in {ProviderStatus.COMPLETED, ProviderStatus.PARTIAL}
            for outcome in outcomes
        )
        fallback = succeeded and any(
            outcome.status in {
                ProviderStatus.FAILED,
                ProviderStatus.TIMED_OUT,
                ProviderStatus.MISSING,
                ProviderStatus.BUDGET_SKIPPED,
            }
            for outcome in outcomes
        )
        clipped = len({item.identity for _, item, _ in accepted}) > query.limit or any(
            outcome.clipped or outcome.status is ProviderStatus.BUDGET_SKIPPED
            for outcome in outcomes
        )
        return FederatedQueryPage(
            items,
            plan,
            tuple(outcomes),
            total_supplied,
            policy_filtered,
            degraded,
            clipped,
            fallback,
        )


def _dedupe_provider_items(items: tuple[ProviderResultItem, ...]) -> tuple[ProviderResultItem, ...]:
    selected: dict[tuple[RecordKind, str], ProviderResultItem] = {}
    for item in items:
        current = selected.get(item.identity)
        if current is not None and current.evidence != item.evidence:
            raise FederatedQueryError("provider returned conflicting evidence for one identity")
        if current is None or (-item.score, item.evidence.record_id) < (
            -current.score,
            current.evidence.record_id,
        ):
            selected[item.identity] = item
    return tuple(sorted(selected.values(), key=lambda item: (-item.score, item.evidence.record_id)))


def _fuse(
    accepted: list[tuple[ProviderRegistration, ProviderResultItem, int]],
    _plan: QueryPlan,
    limit: int,
) -> tuple[FederatedResultItem, ...]:
    # The shared denominator preserves comparable contributions across all
    # successful lanes while rewarding corroboration by independent providers.
    registrations = {registration.provider.provider_id: registration for registration, _, _ in accepted}
    total_weight = sum(registration.weight for registration in registrations.values()) or 1.0
    groups: dict[tuple[RecordKind, str], list[tuple[ProviderRegistration, ProviderResultItem, int]]] = {}
    for registration, item, rank in accepted:
        groups.setdefault(item.identity, []).append((registration, item, rank))
    fused: list[FederatedResultItem] = []
    for group in groups.values():
        evidence = group[0][1].evidence
        if any(item.evidence != evidence for _, item, _ in group[1:]):
            raise FederatedQueryError("providers returned conflicting evidence for one identity")
        scores: list[ProviderScore] = []
        best = sorted(
            group,
            key=lambda value: (-value[1].score, _MODE_ORDER.index(value[0].provider.provider_kind)),
        )[0][1]
        for registration, item, rank in group:
            contribution = round(
                registration.weight * (0.7 * item.score + 0.3 / (rank + 1)) / total_weight,
                12,
            )
            scores.append(
                ProviderScore(
                    registration.provider.provider_id,
                    registration.provider.provider_kind,
                    item.score,
                    rank,
                    contribution,
                )
            )
        scores.sort(key=lambda item: _MODE_ORDER.index(item.provider_kind))
        fused.append(
            FederatedResultItem(
                evidence,
                round(sum(item.contribution for item in scores), 12),
                tuple(scores),
                best.snippet,
                any(item.snippet_clipped for _, item, _ in group),
                any(item.stale for _, item, _ in group),
                tuple(item.why_selected for _, item, _ in group),
            )
        )
    fused.sort(key=lambda item: (-item.fused_score, item.evidence.record_kind.value, item.evidence.record_id))
    return tuple(fused[:limit])


def _require_owner(item: ProviderResultItem, owner_scope: str) -> None:
    if item.evidence.policy_evidence.owner_scope != owner_scope:
        raise FederatedQueryError("provider result crosses owner scope")


def _outcome(
    provider_id: str,
    kind: QueryMode,
    status: ProviderStatus,
    *,
    elapsed_ms: int = 0,
    error_code: str = "",
) -> ProviderOutcome:
    return ProviderOutcome(provider_id, kind, status, 0, 0, 0, elapsed_ms, False, "", error_code)


def _exception_code(exc: Exception) -> str:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).lower()
    return normalized if _TOKEN_RE.fullmatch(normalized) else "provider_error"


def _score(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FederatedQueryError(f"{field_name} must be numeric")
    normalized = round(float(value), 12)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise FederatedQueryError(f"{field_name} is outside its bound")
    return normalized


def _token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise FederatedQueryError(f"{field_name} must be a bounded token")
    return value


def _integer(value: int, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise FederatedQueryError(f"{field_name} must be between {minimum} and {maximum}")
    return value


def _enum(value, enum_type, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise FederatedQueryError(f"{field_name} is invalid") from exc


__all__ = [
    "FederatedQuery",
    "FederatedQueryBudget",
    "FederatedQueryError",
    "FederatedQueryPage",
    "FederatedQueryProvider",
    "FederatedResultItem",
    "GraphQueryProvider",
    "LexicalProviderAdapter",
    "LexicalQueryProvider",
    "ProviderOutcome",
    "ProviderQueryRequest",
    "ProviderRegistration",
    "ProviderResultItem",
    "ProviderResultPage",
    "ProviderScore",
    "ProviderStatus",
    "QueryMode",
    "QueryPlan",
    "SemanticQueryProvider",
    "SymbolQueryProvider",
    "TimelineQueryProvider",
    "UnifiedSourceIndexQueryPlanner",
]

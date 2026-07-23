import pytest

from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_lexical import UnifiedSourceIndexLexicalRetriever
from src.unified_source_index_query import (
    FederatedQuery,
    FederatedQueryBudget,
    FederatedQueryError,
    LexicalProviderAdapter,
    ProviderRegistration,
    ProviderResultItem,
    ProviderResultPage,
    ProviderStatus,
    QueryMode,
    UnifiedSourceIndexQueryPlanner,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore


NOW = "2026-07-17T08:00:00Z"


def _chunk(
    canonical_ref,
    text,
    *,
    owner="user:alice",
    classification=Classification.PRIVATE,
    start=0,
):
    source = SourceRecord(
        owner_scope=owner,
        source_kind=SourceKind.DOCUMENT,
        canonical_ref=canonical_ref,
        classification=classification,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="fake.docs",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref="rev:1",
        content_hash=content_hash(text),
        version_observed_at=NOW,
        indexed_at=NOW,
    )
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(start, start + len(text)),
        extractor_profile_ref="text-v1",
        content_hash=content_hash(text),
        content=text,
        indexed_at=NOW,
    )
    return source, version, chunk


def _item(chunk, score, *, why="provider_match", snippet="match"):
    return ProviderResultItem(
        evidence=chunk.evidence_ref(),
        score=score,
        snippet=snippet,
        why_selected=why,
    )


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance_ms(self, milliseconds):
        self.now += milliseconds / 1_000


class _Provider:
    def __init__(
        self,
        provider_id,
        provider_kind,
        items=(),
        *,
        clock=None,
        advance_ms=0,
        error=None,
        clipped=False,
    ):
        self.provider_id = provider_id
        self.provider_kind = provider_kind
        self.items = tuple(items)
        self.clock = clock
        self.advance = advance_ms
        self.error = error
        self.clipped = clipped
        self.requests = []

    def query(self, request):
        self.requests.append(request)
        if self.clock is not None:
            self.clock.advance_ms(self.advance)
        if self.error is not None:
            raise self.error
        return ProviderResultPage(
            self.items,
            clipped=self.clipped,
            snapshot_ref=f"snapshot.{self.provider_kind.value}",
        )


def _query(**changes):
    values = dict(
        owner_scope="user:alice",
        query_text="odyssey memory",
        classification_ceiling=Classification.SENSITIVE,
    )
    values.update(changes)
    return FederatedQuery(**values)


def _planner(*providers, clock=None):
    return UnifiedSourceIndexQueryPlanner(
        tuple(ProviderRegistration(provider) for provider in providers),
        clock=clock or __import__("time").monotonic,
    )


def _insert(store, *records):
    write = store.begin_write(store.current_snapshot())
    for record in records:
        write.put(record)
    return write.commit()


def test_hybrid_plan_is_fixed_order_and_exposes_missing_lanes():
    _, _, chunk = _chunk("doc:plan", "odyssey memory")
    lexical = _Provider("fake.lexical", QueryMode.LEXICAL, (_item(chunk, 0.8),))
    semantic = _Provider("fake.semantic", QueryMode.SEMANTIC, (_item(chunk, 0.7),))
    symbol = _Provider("fake.symbol", QueryMode.SYMBOL)
    planner = _planner(symbol, semantic, lexical)

    page = planner.execute(_query())

    assert page.plan.requested_kinds == (
        QueryMode.LEXICAL,
        QueryMode.SEMANTIC,
        QueryMode.SYMBOL,
        QueryMode.GRAPH,
        QueryMode.TIMELINE,
    )
    assert page.plan.selected_provider_ids == (
        "fake.lexical",
        "fake.semantic",
        "fake.symbol",
    )
    assert [outcome.status for outcome in page.outcomes] == [
        ProviderStatus.COMPLETED,
        ProviderStatus.COMPLETED,
        ProviderStatus.COMPLETED,
        ProviderStatus.MISSING,
        ProviderStatus.MISSING,
    ]
    assert page.partial is True
    assert page.fallback_used is True


def test_fusion_deduplicates_by_occurrence_and_preserves_exact_evidence():
    _, _, shared = _chunk("doc:shared", "odyssey shared")
    _, _, lexical_only = _chunk("doc:lexical", "odyssey lexical")
    lexical = _Provider(
        "fake.lexical",
        QueryMode.LEXICAL,
        (
            _item(lexical_only, 1.0, why="lexical_match"),
            _item(shared, 0.8, why="lexical_match"),
        ),
    )
    semantic = _Provider(
        "fake.semantic",
        QueryMode.SEMANTIC,
        (_item(shared, 0.7, why="semantic_match"),),
    )

    page = _planner(semantic, lexical).execute(
        _query(modes=(QueryMode.LEXICAL, QueryMode.SEMANTIC))
    )

    assert [item.record_ref.record_id for item in page.items] == [
        shared.chunk_id,
        lexical_only.chunk_id,
    ]
    fused = page.items[0]
    assert fused.evidence == shared.evidence_ref()
    assert [score.provider_kind for score in fused.provider_scores] == [
        QueryMode.LEXICAL,
        QueryMode.SEMANTIC,
    ]
    assert fused.why_selected == ("lexical_match", "semantic_match")


def test_provider_failure_returns_lexical_fallback_without_error_message_leakage():
    _, _, chunk = _chunk("doc:fallback", "odyssey fallback")
    lexical = _Provider("fake.lexical", QueryMode.LEXICAL, (_item(chunk, 0.9),))
    semantic = _Provider(
        "fake.semantic",
        QueryMode.SEMANTIC,
        error=RuntimeError("secret provider connection string"),
    )

    page = _planner(lexical, semantic).execute(
        _query(modes=(QueryMode.LEXICAL, QueryMode.SEMANTIC))
    )

    assert [item.record_ref.record_id for item in page.items] == [chunk.chunk_id]
    assert [outcome.status for outcome in page.outcomes] == [
        ProviderStatus.COMPLETED,
        ProviderStatus.FAILED,
    ]
    assert page.outcomes[1].error_code == "runtime_error"
    assert "secret" not in page.outcomes[1].error_code
    assert page.partial is True
    assert page.fallback_used is True


def test_late_provider_result_is_discarded_and_reported_as_timeout():
    clock = _Clock()
    _, _, lexical_chunk = _chunk("doc:fast", "odyssey fast")
    _, _, semantic_chunk = _chunk("doc:late", "odyssey late")
    lexical = _Provider("fake.lexical", QueryMode.LEXICAL, (_item(lexical_chunk, 0.8),))
    semantic = _Provider(
        "fake.semantic",
        QueryMode.SEMANTIC,
        (_item(semantic_chunk, 1.0),),
        clock=clock,
        advance_ms=51,
    )
    budget = FederatedQueryBudget(
        max_providers=2,
        max_candidates_per_provider=10,
        max_total_candidates=20,
        time_budget_ms=100,
        per_provider_timeout_ms=50,
    )

    page = _planner(lexical, semantic, clock=clock).execute(
        _query(modes=(QueryMode.LEXICAL, QueryMode.SEMANTIC), budget=budget)
    )

    assert [item.record_ref.record_id for item in page.items] == [lexical_chunk.chunk_id]
    assert page.outcomes[1].status is ProviderStatus.TIMED_OUT
    assert page.outcomes[1].error_code == "provider_timeout"
    assert page.outcomes[1].accepted_count == 0
    assert page.fallback_used is True


def test_provider_and_candidate_budgets_bound_requests_and_skip_extra_lane():
    _, _, first = _chunk("doc:first", "odyssey first")
    lexical = _Provider("fake.lexical", QueryMode.LEXICAL, (_item(first, 0.8),))
    semantic = _Provider("fake.semantic", QueryMode.SEMANTIC)
    budget = FederatedQueryBudget(
        max_providers=1,
        max_candidates_per_provider=3,
        max_total_candidates=2,
        time_budget_ms=100,
        per_provider_timeout_ms=50,
    )

    page = _planner(lexical, semantic).execute(
        _query(modes=(QueryMode.LEXICAL, QueryMode.SEMANTIC), budget=budget)
    )

    assert lexical.requests[0].limit == 2
    assert semantic.requests == []
    assert page.outcomes[1].status is ProviderStatus.BUDGET_SKIPPED
    assert page.total_supplied_candidates == 1
    assert page.clipped is True


def test_owner_violation_fails_whole_provider_without_leaking_earlier_items():
    _, _, allowed = _chunk("doc:allowed", "odyssey allowed")
    _, _, forbidden = _chunk(
        "doc:forbidden",
        "odyssey forbidden",
        owner="user:bob",
    )
    semantic = _Provider(
        "fake.semantic",
        QueryMode.SEMANTIC,
        (_item(allowed, 1.0), _item(forbidden, 0.9)),
    )

    page = _planner(semantic).execute(_query(modes=(QueryMode.SEMANTIC,)))

    assert page.items == ()
    assert page.outcomes[0].status is ProviderStatus.FAILED
    assert page.outcomes[0].accepted_count == 0
    assert page.outcomes[0].error_code == "federated_query_error"


def test_policy_filters_are_explicit_and_do_not_cross_source_or_classification():
    allowed_source, _, allowed = _chunk("doc:allowed", "odyssey allowed")
    _, _, sensitive = _chunk(
        "doc:sensitive",
        "odyssey sensitive",
        classification=Classification.SENSITIVE,
    )
    _, _, another_source = _chunk("doc:another", "odyssey another")
    lexical = _Provider(
        "fake.lexical",
        QueryMode.LEXICAL,
        (_item(allowed, 0.9), _item(sensitive, 0.8), _item(another_source, 0.7)),
    )

    page = _planner(lexical).execute(
        _query(
            modes=(QueryMode.LEXICAL,),
            classification_ceiling=Classification.PRIVATE,
            source_ids=(allowed_source.source_id,),
        )
    )

    assert [item.record_ref.record_id for item in page.items] == [allowed.chunk_id]
    assert page.policy_filtered_count == 2
    assert page.outcomes[0].policy_filtered_count == 2
    assert page.outcomes[0].status is ProviderStatus.PARTIAL
    assert page.partial is True


def test_lexical_adapter_runs_against_real_store_and_keeps_chunk_evidence(tmp_path):
    source, version, chunk = _chunk("doc:real", "odyssey real lexical")
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, chunk, version, source)
    adapter = LexicalProviderAdapter(UnifiedSourceIndexLexicalRetriever(store))

    page = _planner(adapter).execute(
        _query(query_text="odyssey", modes=(QueryMode.LEXICAL,))
    )

    assert len(page.items) == 1
    assert page.items[0].evidence == chunk.evidence_ref()
    assert page.outcomes[0].status is ProviderStatus.COMPLETED
    assert page.partial is False
    assert page.fallback_used is False


def test_duplicate_lanes_and_invalid_mode_combinations_are_rejected():
    first = _Provider("fake.lexical.one", QueryMode.LEXICAL)
    second = _Provider("fake.lexical.two", QueryMode.LEXICAL)

    with pytest.raises(FederatedQueryError, match="registered more than once"):
        _planner(first, second)
    with pytest.raises(FederatedQueryError, match="hybrid mode"):
        _query(modes=(QueryMode.HYBRID, QueryMode.LEXICAL))
    with pytest.raises(FederatedQueryError, match="duplicates"):
        _query(modes=(QueryMode.LEXICAL, QueryMode.LEXICAL))

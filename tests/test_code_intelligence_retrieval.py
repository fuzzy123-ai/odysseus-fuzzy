from __future__ import annotations

from dataclasses import replace

import pytest

from src.code_intelligence_contract import (
    CodeEdgeMapping,
    CodeFileMapping,
    CodeLocation,
    CodeSymbolKind,
    CodeSymbolMapping,
    ExtractionEvidence,
    ExtractionMethod,
    symbol_natural_key,
)
from src.code_intelligence_retrieval import (
    CodeIntelligenceRetrievalError,
    CodeIntelligenceRetrievalRequest,
    CodeIntelligenceRetriever,
    HybridCodeQueryPlanner,
    RetrievalIntent,
)
from src.codebase_memory_query import (
    QueryBudget,
    QueryStats,
    QueryStatus,
    StructuralEdge,
    StructuralNode,
    StructuralOperation,
    StructuralQueryResult,
    UnresolvedStructuralEdge,
)
from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    EntityKind,
    EntityRecord,
    RelationKind,
    RelationRecord,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    content_hash,
)
from src.unified_source_index_query import (
    FederatedQueryBudget,
    ProviderRegistration,
    ProviderResultItem,
    ProviderResultPage,
    ProviderStatus,
    QueryMode,
)


NOW = "2026-07-18T10:00:00Z"
GENERATION = "cbm_generation_" + "a" * 64


class FakeUSIProvider:
    def __init__(self, provider_id, kind, items=(), *, error=None):
        self._provider_id = provider_id
        self._kind = kind
        self.items = tuple(items)
        self.error = error
        self.requests = []

    @property
    def provider_id(self):
        return self._provider_id

    @property
    def provider_kind(self):
        return self._kind

    def query(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return ProviderResultPage(self.items[: request.limit], len(self.items) > request.limit, "usi-fixture")


class FakeStructuralProvider:
    def __init__(self, factory):
        self.factory = factory
        self.requests = []

    def query(self, request):
        self.requests.append(request)
        if isinstance(self.factory, Exception):
            raise self.factory
        return self.factory(request)


class FakeEvidenceResolver:
    def __init__(self, evidence):
        self.evidence = dict(evidence)
        self.requests = []

    def resolve_entity(self, entity_id):
        self.requests.append(entity_id)
        return self.evidence[entity_id]


def _fixtures():
    extraction = ExtractionEvidence(
        ExtractionMethod.CBM_PARSER,
        0.92,
        "cbm",
        "0.9.0",
        False,
    )
    source = SourceRecord(
        owner_scope="user:alice",
        source_kind=SourceKind.CODE,
        canonical_ref="repo:demo/src/main.py",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        provider_ref="local-git",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref="git:" + "b" * 40,
        content_hash=content_hash("fixture code"),
        version_observed_at=NOW,
    )
    file_mapping = CodeFileMapping.create(
        source,
        version,
        repo_id="demo",
        relative_path="src/main.py",
        byte_length=80,
        engine_project_ref="project-fixture",
        engine_file_ref="file-fixture",
        evidence=extraction,
    )
    entities = []
    mappings = []
    for index, name in enumerate(("Service.run", "Store.save"), start=1):
        location = CodeLocation(
            "src/main.py",
            index * 20,
            index * 20 + 10,
            index * 5,
            0,
            index * 5 + 2,
            0,
        )
        signature = "sha256:" + str(index) * 64
        entity = EntityRecord.create(
            version,
            entity_kind=EntityKind.SYMBOL,
            natural_key=symbol_natural_key(CodeSymbolKind.METHOD, name, signature),
            locator=location.to_usi_locator(),
            extractor_profile_ref=extraction.extractor_profile_ref,
            content_hash=content_hash(f"symbol-{index}"),
            label=name,
        )
        mapping = CodeSymbolMapping.create(
            file_mapping,
            entity,
            symbol_kind=CodeSymbolKind.METHOD,
            qualified_name=name,
            signature_fingerprint=signature,
            location=location,
            engine_symbol_ref=f"symbol-{index}",
            evidence=extraction,
        )
        entities.append(entity)
        mappings.append(mapping)
    relation = RelationRecord.create(
        entities[0].ref(),
        entities[1].ref(),
        relation_kind=RelationKind.CALLS,
        method_ref=extraction.extractor_profile_ref,
        confidence=0.92,
        evidence_refs=(entities[0].evidence_ref(), entities[1].evidence_ref()),
    )
    edge = CodeEdgeMapping.create(
        relation,
        engine_project_ref="project-fixture",
        engine_edge_ref="edge-calls",
        evidence=extraction,
    )
    return {
        "source": source,
        "version": version,
        "entities": tuple(entities),
        "mappings": tuple(mappings),
        "edge": edge,
        "resolver": FakeEvidenceResolver(
            {item.entity_id: item.evidence_ref() for item in entities}
        ),
    }


def _request(fixtures, intent=RetrievalIntent.CALLERS, **changes):
    structural = intent in {
        RetrievalIntent.SYMBOL,
        RetrievalIntent.CALLERS,
        RetrievalIntent.CALLEES,
        RetrievalIntent.IMPORTS,
        RetrievalIntent.INHERITANCE,
        RetrievalIntent.ROUTES,
        RetrievalIntent.DATAFLOW,
        RetrievalIntent.COMMUNITIES,
        RetrievalIntent.IMPACT,
    }
    subject = ""
    if intent not in {RetrievalIntent.ROUTES, RetrievalIntent.COMMUNITIES} and structural:
        subject = fixtures["entities"][0].entity_id
    values = {
        "repo_id": "demo",
        "generation_ref": GENERATION,
        "owner_scope": "user:alice",
        "query_text": "Who calls Service.run?",
        "classification_ceiling": Classification.PRIVATE,
        "intent": intent,
        "subject_entity_id": subject,
        "scope_entity_ids": tuple(item.entity_id for item in fixtures["entities"]) if structural else (),
        "source_ids": (fixtures["source"].source_id,),
        "limit": 10,
        "semantic_enabled": False,
        "federated_budget": FederatedQueryBudget(
            max_providers=3,
            max_candidates_per_provider=20,
            max_total_candidates=40,
            time_budget_ms=1000,
            per_provider_timeout_ms=500,
        ),
        "structural_budget": QueryBudget(
            limit=10,
            max_nodes_examined=50,
            max_edges_examined=50,
            max_depth=4,
            timeout_ms=500,
        ),
    }
    values.update(changes)
    return CodeIntelligenceRetrievalRequest(**values)


def _success(fixtures, *, edges=False, unresolved=False, stale=False):
    def factory(request):
        result_edges = ()
        nodes = (StructuralNode(fixtures["mappings"][0]),)
        unresolved_count = 0
        status = QueryStatus.PARTIAL if unresolved else QueryStatus.COMPLETE
        if edges:
            nodes = ()
            result_edges = (StructuralEdge(fixtures["edge"]),)
        if unresolved:
            nodes = ()
            result_edges = (
                UnresolvedStructuralEdge(
                    "missing_target",
                    fixtures["entities"][0].entity_id,
                    "engine_missing_target",
                    RelationKind.CALLS,
                    "cbm_parser",
                    0.61,
                    "0.9.0",
                    True,
                    "unresolved_target",
                ),
            )
            unresolved_count = 1
        return StructuralQueryResult(
            request.query_id,
            request.operation,
            request.generation_ref,
            status,
            nodes,
            result_edges,
            QueryStats(len(nodes), len(result_edges), 5, False),
            "",
            False,
            stale,
            unresolved_count,
            "",
            False,
        )

    return factory


def _unavailable(request):
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
        False,
        0,
        "provider_unavailable",
        True,
    )


_STRUCTURAL_CASES = {
    RetrievalIntent.SYMBOL: (StructuralOperation.SYMBOL, QueryMode.SYMBOL),
    RetrievalIntent.CALLERS: (StructuralOperation.CALLERS, QueryMode.GRAPH),
    RetrievalIntent.CALLEES: (StructuralOperation.CALLEES, QueryMode.GRAPH),
    RetrievalIntent.IMPORTS: (StructuralOperation.IMPORTS, QueryMode.GRAPH),
    RetrievalIntent.INHERITANCE: (StructuralOperation.INHERITANCE, QueryMode.GRAPH),
    RetrievalIntent.ROUTES: (StructuralOperation.ROUTES, QueryMode.GRAPH),
    RetrievalIntent.DATAFLOW: (StructuralOperation.DATAFLOW, QueryMode.GRAPH),
    RetrievalIntent.COMMUNITIES: (StructuralOperation.COMMUNITIES, QueryMode.GRAPH),
    RetrievalIntent.IMPACT: (StructuralOperation.IMPACT, QueryMode.GRAPH),
}


@pytest.mark.parametrize("intent,expected", _STRUCTURAL_CASES.items())
def test_nine_structural_intents_plan_structural_first_with_lexical_fallback(intent, expected):
    fixtures = _fixtures()
    plan = HybridCodeQueryPlanner().plan(_request(fixtures, intent))

    assert plan.structural_operation is expected[0]
    assert plan.primary_modes == (expected[1],)
    assert plan.fallback_modes == (QueryMode.LEXICAL,)
    assert plan.requested_modes == (expected[1], QueryMode.LEXICAL)
    assert plan.reasons[0] == "structural_first"
    assert "query_text" not in plan.to_dict()


@pytest.mark.parametrize("intent", [RetrievalIntent.DYNAMIC_CODE, RetrievalIntent.EXHAUSTIVE])
def test_dynamic_and_exhaustive_queries_are_lexical_exact_first(intent):
    plan = HybridCodeQueryPlanner().plan(_request(_fixtures(), intent))

    assert plan.structural_operation is None
    assert plan.primary_modes == (QueryMode.LEXICAL,)
    assert plan.lexical_exact is True
    assert plan.semantic_optional is False


def test_vocabulary_mismatch_uses_optional_semantic_then_lexical():
    fixtures = _fixtures()
    plan = HybridCodeQueryPlanner().plan(
        _request(fixtures, RetrievalIntent.VOCABULARY_MISMATCH, semantic_enabled=True)
    )

    assert plan.primary_modes == (QueryMode.SEMANTIC,)
    assert plan.fallback_modes == (QueryMode.LEXICAL,)
    assert plan.requested_modes == (QueryMode.SEMANTIC, QueryMode.LEXICAL)
    assert plan.semantic_optional is True

    lexical_only = HybridCodeQueryPlanner().plan(
        _request(fixtures, RetrievalIntent.VOCABULARY_MISMATCH)
    )
    assert lexical_only.requested_modes == (QueryMode.LEXICAL,)


def test_request_identity_is_canonical_and_deterministic():
    fixtures = _fixtures()
    first = _request(fixtures, query_text="  Who   calls Service.run?  ")
    second = _request(
        fixtures,
        query_text="Who calls Service.run?",
        scope_entity_ids=tuple(reversed(first.scope_entity_ids)),
    )

    assert first.query_text == "Who calls Service.run?"
    assert first.scope_entity_ids == tuple(sorted(first.scope_entity_ids))
    assert first.request_id == second.request_id


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"generation_ref": "latest"}, "generation_ref"),
        ({"query_text": "bad\nquery"}, "query_text"),
        ({"intent": "raw_cypher"}, "intent"),
        ({"subject_entity_id": ""}, "requires subject"),
        ({"scope_entity_ids": ("usi_entity_" + "f" * 64,)}, "remain in scope"),
        ({"federated_budget": FederatedQueryBudget(max_providers=1)}, "fallback"),
        ({"structural_budget": QueryBudget(limit=51, timeout_ms=750)}, "exceeds"),
    ],
)
def test_structural_request_rejects_invalid_or_unbounded_inputs(changes, match):
    with pytest.raises(CodeIntelligenceRetrievalError, match=match):
        _request(_fixtures(), **changes)


def test_non_structural_request_rejects_hidden_entity_scope():
    fixtures = _fixtures()
    with pytest.raises(CodeIntelligenceRetrievalError, match="non-structural"):
        _request(
            fixtures,
            RetrievalIntent.EXHAUSTIVE,
            subject_entity_id=fixtures["entities"][0].entity_id,
        )


def test_semantic_lane_is_only_available_for_explicit_vocabulary_mismatch():
    with pytest.raises(CodeIntelligenceRetrievalError, match="vocabulary mismatch"):
        _request(_fixtures(), RetrievalIntent.EXHAUSTIVE, semantic_enabled=True)


def test_hybrid_query_fuses_structural_and_lexical_exact_evidence():
    fixtures = _fixtures()
    evidence = fixtures["entities"][0].evidence_ref()
    lexical = FakeUSIProvider(
        "usi.lexical.fixture",
        QueryMode.LEXICAL,
        (ProviderResultItem(evidence, 0.75, why_selected="lexical_exact"),),
    )
    structural = FakeStructuralProvider(_success(fixtures))
    result = CodeIntelligenceRetriever(
        (ProviderRegistration(lexical),),
        structural_provider=structural,
        evidence_resolver=fixtures["resolver"],
    ).query(_request(fixtures))

    assert len(result.page.items) == 1
    assert result.page.items[0].evidence == evidence
    assert {item.provider_kind for item in result.page.items[0].provider_scores} == {
        QueryMode.LEXICAL,
        QueryMode.GRAPH,
    }
    assert result.page.items[0].snippet == ""
    assert result.exact_read_recommended is True
    assert result.exact_reader == "read_file"
    assert result.page.fallback_used is False
    assert len(structural.requests) == 1


def test_engine_unavailable_returns_usable_lexical_fallback():
    fixtures = _fixtures()
    evidence = fixtures["entities"][0].evidence_ref()
    lexical = FakeUSIProvider(
        "usi.lexical.fixture",
        QueryMode.LEXICAL,
        (ProviderResultItem(evidence, 0.7, why_selected="lexical_fallback"),),
    )
    result = CodeIntelligenceRetriever(
        (ProviderRegistration(lexical),),
        structural_provider=FakeStructuralProvider(_unavailable),
        evidence_resolver=fixtures["resolver"],
    ).query(_request(fixtures))

    assert result.page.items[0].evidence == evidence
    assert result.page.fallback_used is True
    graph = next(item for item in result.page.outcomes if item.provider_kind is QueryMode.GRAPH)
    assert graph.status is ProviderStatus.FAILED
    assert graph.error_code == "structural_lane_unavailable"


def test_missing_engine_registration_is_explicit_and_lexical_still_works():
    fixtures = _fixtures()
    lexical = FakeUSIProvider(
        "usi.lexical.fixture",
        QueryMode.LEXICAL,
        (ProviderResultItem(fixtures["entities"][0].evidence_ref(), 0.7),),
    )
    result = CodeIntelligenceRetriever((ProviderRegistration(lexical),)).query(_request(fixtures))

    graph = next(item for item in result.page.outcomes if item.provider_kind is QueryMode.GRAPH)
    assert graph.status is ProviderStatus.MISSING
    assert result.page.fallback_used is True
    assert result.page.items


def test_structural_edges_return_both_exact_usi_endpoint_evidences():
    fixtures = _fixtures()
    lexical = FakeUSIProvider("usi.lexical.fixture", QueryMode.LEXICAL)
    result = CodeIntelligenceRetriever(
        (ProviderRegistration(lexical),),
        structural_provider=FakeStructuralProvider(_success(fixtures, edges=True)),
        evidence_resolver=fixtures["resolver"],
    ).query(_request(fixtures))

    assert {item.evidence.record_id for item in result.page.items} == {
        item.entity_id for item in fixtures["entities"]
    }
    assert all(item.evidence.record_kind.value == "entity" for item in result.page.items)
    assert result.page.total_supplied_candidates == 2


def test_unresolved_edge_keeps_partial_state_visible():
    fixtures = _fixtures()
    result = CodeIntelligenceRetriever(
        (ProviderRegistration(FakeUSIProvider("usi.lexical.fixture", QueryMode.LEXICAL)),),
        structural_provider=FakeStructuralProvider(_success(fixtures, unresolved=True)),
        evidence_resolver=fixtures["resolver"],
    ).query(_request(fixtures))

    graph = next(item for item in result.page.outcomes if item.provider_kind is QueryMode.GRAPH)
    assert graph.status is ProviderStatus.PARTIAL
    assert graph.clipped is True
    assert result.page.partial is True


def test_stale_structural_evidence_remains_stale_after_fusion():
    fixtures = _fixtures()
    result = CodeIntelligenceRetriever(
        (ProviderRegistration(FakeUSIProvider("usi.lexical.fixture", QueryMode.LEXICAL)),),
        structural_provider=FakeStructuralProvider(_success(fixtures, stale=True)),
        evidence_resolver=fixtures["resolver"],
    ).query(_request(fixtures))

    assert result.page.items[0].stale is True


def test_conflicting_resolver_evidence_fails_structural_lane_and_falls_back():
    fixtures = _fixtures()
    first = fixtures["entities"][0]
    wrong_resolver = FakeEvidenceResolver(
        {first.entity_id: fixtures["entities"][1].evidence_ref()}
    )
    lexical = FakeUSIProvider(
        "usi.lexical.fixture",
        QueryMode.LEXICAL,
        (ProviderResultItem(first.evidence_ref(), 0.7),),
    )
    result = CodeIntelligenceRetriever(
        (ProviderRegistration(lexical),),
        structural_provider=FakeStructuralProvider(_success(fixtures)),
        evidence_resolver=wrong_resolver,
    ).query(_request(fixtures))

    graph = next(item for item in result.page.outcomes if item.provider_kind is QueryMode.GRAPH)
    assert graph.status is ProviderStatus.FAILED
    assert graph.error_code == "code_intelligence_retrieval_error"
    assert result.page.fallback_used is True


def test_structural_budget_is_narrowed_to_usi_provider_budget():
    fixtures = _fixtures()
    structural = FakeStructuralProvider(_success(fixtures))
    request = _request(
        fixtures,
        federated_budget=FederatedQueryBudget(
            max_providers=2,
            max_candidates_per_provider=4,
            max_total_candidates=8,
            time_budget_ms=300,
            per_provider_timeout_ms=200,
        ),
        structural_budget=QueryBudget(
            limit=4,
            max_nodes_examined=20,
            max_edges_examined=20,
            max_depth=3,
            timeout_ms=200,
        ),
    )
    CodeIntelligenceRetriever(
        (ProviderRegistration(FakeUSIProvider("usi.lexical.fixture", QueryMode.LEXICAL)),),
        structural_provider=structural,
        evidence_resolver=fixtures["resolver"],
    ).query(request)

    assert structural.requests[0].budget.limit == 4
    assert structural.requests[0].budget.timeout_ms <= 200
    assert structural.requests[0].operation is StructuralOperation.CALLERS


def test_semantic_failure_for_vocabulary_mismatch_uses_lexical_fallback():
    fixtures = _fixtures()
    evidence = fixtures["entities"][0].evidence_ref()
    semantic = FakeUSIProvider(
        "usi.semantic.fixture",
        QueryMode.SEMANTIC,
        error=RuntimeError("fixture unavailable"),
    )
    lexical = FakeUSIProvider(
        "usi.lexical.fixture",
        QueryMode.LEXICAL,
        (ProviderResultItem(evidence, 0.6, why_selected="lexical_fallback"),),
    )
    result = CodeIntelligenceRetriever(
        (ProviderRegistration(lexical), ProviderRegistration(semantic))
    ).query(
        _request(fixtures, RetrievalIntent.VOCABULARY_MISMATCH, semantic_enabled=True)
    )

    assert result.page.items[0].evidence == evidence
    assert result.page.fallback_used is True
    assert next(
        item for item in result.page.outcomes if item.provider_kind is QueryMode.SEMANTIC
    ).status is ProviderStatus.FAILED


@pytest.mark.parametrize("intent", [RetrievalIntent.DYNAMIC_CODE, RetrievalIntent.EXHAUSTIVE])
def test_non_structural_intents_never_call_cbm(intent):
    fixtures = _fixtures()
    structural = FakeStructuralProvider(AssertionError("CBM must not be called"))
    lexical = FakeUSIProvider(
        "usi.lexical.fixture",
        QueryMode.LEXICAL,
        (ProviderResultItem(fixtures["entities"][0].evidence_ref(), 0.8),),
    )
    result = CodeIntelligenceRetriever(
        (ProviderRegistration(lexical),),
        structural_provider=structural,
        evidence_resolver=fixtures["resolver"],
    ).query(_request(fixtures, intent))

    assert result.page.items
    assert structural.requests == []


def test_retriever_rejects_foreign_structural_registration_and_unpaired_adapter():
    graph = FakeUSIProvider("foreign.graph", QueryMode.GRAPH)
    with pytest.raises(CodeIntelligenceRetrievalError, match="lexical or semantic"):
        CodeIntelligenceRetriever((ProviderRegistration(graph),))

    with pytest.raises(CodeIntelligenceRetrievalError, match="supplied together"):
        CodeIntelligenceRetriever((), structural_provider=FakeStructuralProvider(_unavailable))

    lexical = ProviderRegistration(FakeUSIProvider("duplicate.lexical.one", QueryMode.LEXICAL))
    duplicate = ProviderRegistration(FakeUSIProvider("duplicate.lexical.two", QueryMode.LEXICAL))
    with pytest.raises(CodeIntelligenceRetrievalError, match="USI planner contract"):
        CodeIntelligenceRetriever((lexical, duplicate))


def test_empty_result_does_not_recommend_an_exact_read():
    result = CodeIntelligenceRetriever(
        (ProviderRegistration(FakeUSIProvider("usi.lexical.fixture", QueryMode.LEXICAL)),)
    ).query(_request(_fixtures(), RetrievalIntent.EXHAUSTIVE))

    assert result.page.items == ()
    assert result.exact_read_recommended is False
    assert result.exact_reader == ""


def test_module_has_no_runtime_or_raw_query_escape_hatch():
    source = __import__("pathlib").Path("src/code_intelligence_retrieval.py").read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "socket" not in source
    assert "requests." not in source
    assert "cypher" not in source.lower()
    assert "query_structural" not in source

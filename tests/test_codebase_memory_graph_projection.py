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
from src.codebase_memory_graph_projection import (
    CatalogCodeGraphStore,
    CodeGraphAggregateRecord,
    CodeGraphBudget,
    CodeGraphEdgeRecord,
    CodeGraphNodeRecord,
    CodeGraphQuery,
    CodeGraphSlice,
    CodebaseMemoryGraphError,
    CodebaseMemoryGraphProjection,
)
from src.progressive_graph_api import GraphQueryKind, ProgressiveGraphStatus
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


NOW = "2026-07-18T10:00:00Z"
GRAPH = "cbm_generation_" + "a" * 64


class FakeStore:
    def __init__(self, result, graph_ref=GRAPH):
        self._result = result
        self._graph_ref = graph_ref
        self.requests = []

    @property
    def graph_ref(self):
        return self._graph_ref

    def query(self, request, offset):
        self.requests.append((request, offset))
        return self._result(request, offset) if callable(self._result) else self._result


def _fixtures(*, engine_suffix="one"):
    extraction = ExtractionEvidence(
        ExtractionMethod.CBM_PARSER,
        0.94,
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
        content_hash=content_hash("fixture code graph"),
        version_observed_at=NOW,
    )
    file_mapping = CodeFileMapping.create(
        source,
        version,
        repo_id="demo",
        relative_path="src/main.py",
        byte_length=200,
        engine_project_ref=f"project-{engine_suffix}",
        engine_file_ref=f"file-{engine_suffix}",
        evidence=extraction,
    )
    entities = []
    symbols = []
    for index, name in enumerate(
        ("Service.run", "Store.save", "Router.route", "Worker.tick", "Audit.write"),
        start=1,
    ):
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
        symbol = CodeSymbolMapping.create(
            file_mapping,
            entity,
            symbol_kind=CodeSymbolKind.METHOD,
            qualified_name=name,
            signature_fingerprint=signature,
            location=location,
            engine_symbol_ref=f"engine-symbol-{engine_suffix}-{index}",
            evidence=extraction,
        )
        entities.append(entity)
        symbols.append(symbol)
    edges = []
    for index in range(4):
        relation_kind = RelationKind.CALLS if index < 3 else RelationKind.IMPORTS
        relation = RelationRecord.create(
            entities[index].ref(),
            entities[index + 1].ref(),
            relation_kind=relation_kind,
            method_ref=extraction.extractor_profile_ref,
            confidence=0.94,
            evidence_refs=(entities[index].evidence_ref(), entities[index + 1].evidence_ref()),
        )
        edges.append(
            CodeEdgeMapping.create(
                relation,
                engine_project_ref=f"project-{engine_suffix}",
                engine_edge_ref=f"engine-edge-{engine_suffix}-{index}",
                evidence=extraction,
            )
        )
    communities = {
        item.entity_id: "community-alpha" if index < 3 else "community-beta"
        for index, item in enumerate(symbols)
    }
    store = CatalogCodeGraphStore(
        GRAPH,
        symbols=symbols,
        edges=edges,
        community_by_entity=communities,
    )
    return {
        "source": source,
        "version": version,
        "file": file_mapping,
        "entities": tuple(entities),
        "symbols": tuple(symbols),
        "edges": tuple(edges),
        "communities": communities,
        "store": store,
        "projection": CodebaseMemoryGraphProjection(store),
    }


def _query(fixtures, kind, **changes):
    values = {
        "graph_ref": GRAPH,
        "query_kind": kind,
        "viewport_ref": "code-fixture",
        "budget": CodeGraphBudget(
            limit=10,
            max_nodes=10,
            max_edges=20,
            depth=2,
            max_hops=4,
            time_budget_ms=1000,
            payload_budget_bytes=128 * 1024,
        ),
    }
    if kind is GraphQueryKind.NEIGHBORHOOD:
        values["node_entity_id"] = fixtures["entities"][1].entity_id
    elif kind is GraphQueryKind.PATH:
        values["node_entity_id"] = fixtures["entities"][0].entity_id
        values["target_entity_id"] = fixtures["entities"][4].entity_id
    elif kind is GraphQueryKind.COMMUNITY:
        values["community_ref"] = "community-alpha"
    elif kind is GraphQueryKind.QUERY_SUBGRAPH:
        values["query_entity_ids"] = tuple(item.entity_id for item in fixtures["entities"][:3])
    values.update(changes)
    return CodeGraphQuery(**values)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"limit": 0}, "limit"),
        ({"max_nodes": 501}, "max_nodes"),
        ({"max_edges": 2001}, "max_edges"),
        ({"depth": 9}, "depth"),
        ({"max_hops": 9}, "max_hops"),
        ({"time_budget_ms": 10001}, "time_budget_ms"),
        ({"payload_budget_bytes": 2 * 1024 * 1024 + 1}, "payload_budget_bytes"),
        ({"limit": 20, "max_nodes": 10}, "limit cannot"),
    ],
)
def test_code_graph_budget_has_hard_caps(changes, match):
    values = {
        "limit": 10,
        "max_nodes": 10,
        "max_edges": 20,
        "depth": 2,
        "max_hops": 4,
        "time_budget_ms": 1000,
        "payload_budget_bytes": 4096,
    }
    values.update(changes)
    with pytest.raises(CodebaseMemoryGraphError, match=match):
        CodeGraphBudget(**values)


@pytest.mark.parametrize(
    "kind,changes,match",
    [
        (GraphQueryKind.NEIGHBORHOOD, {}, "neighborhood"),
        (GraphQueryKind.PATH, {}, "path"),
        (GraphQueryKind.COMMUNITY, {}, "community"),
        (GraphQueryKind.QUERY_SUBGRAPH, {}, "query_subgraph"),
        (GraphQueryKind.OVERVIEW, {"cursor": "c-" + "a" * 32 + "-1"}, "cursor"),
    ],
)
def test_query_kind_requires_exact_scope(kind, changes, match):
    with pytest.raises(CodebaseMemoryGraphError, match=match):
        CodeGraphQuery(
            GRAPH,
            kind,
            "fixture",
            CodeGraphBudget(),
            **changes,
        )


def test_overview_is_aggregate_first_and_never_dumps_nodes():
    fixtures = _fixtures()
    result = fixtures["projection"].query(_query(fixtures, GraphQueryKind.OVERVIEW))

    assert result.page.status is ProgressiveGraphStatus.COMPLETE
    assert result.page.nodes == ()
    assert result.page.edges == ()
    assert result.page.aggregates
    assert result.total_node_count == 5
    assert result.total_edge_count == 4
    assert result.level_of_detail == "aggregate"


def test_complete_small_neighborhood_preserves_all_local_nodes_and_edges():
    fixtures = _fixtures()
    result = fixtures["projection"].query(
        _query(fixtures, GraphQueryKind.NEIGHBORHOOD)
    )

    assert result.page.status is ProgressiveGraphStatus.COMPLETE
    assert result.page.partial is False
    assert result.page.clipped is False
    assert result.page.node_count == 4
    assert result.page.edge_count == 3
    assert len(result.node_evidence) == result.page.node_count
    assert len(result.edge_evidence) == result.page.edge_count


def test_path_respects_max_hops_and_returns_one_explainable_path():
    fixtures = _fixtures()
    complete = fixtures["projection"].query(_query(fixtures, GraphQueryKind.PATH))

    assert complete.page.node_count == 5
    assert complete.page.edge_count == 4
    assert complete.page.status is ProgressiveGraphStatus.COMPLETE

    too_shallow = fixtures["projection"].query(
        _query(
            fixtures,
            GraphQueryKind.PATH,
            budget=replace(_query(fixtures, GraphQueryKind.PATH).budget, max_hops=2),
        )
    )
    assert too_shallow.page.status is ProgressiveGraphStatus.EMPTY
    assert too_shallow.page.nodes == ()


def test_community_pages_with_cursor_and_cursor_is_query_bound():
    fixtures = _fixtures()
    budget = CodeGraphBudget(
        limit=2,
        max_nodes=2,
        max_edges=4,
        depth=1,
        max_hops=2,
        time_budget_ms=1000,
        payload_budget_bytes=64 * 1024,
    )
    request = _query(fixtures, GraphQueryKind.COMMUNITY, budget=budget)
    first = fixtures["projection"].query(request)

    assert first.page.status is ProgressiveGraphStatus.CLIPPED
    assert first.page.node_count == 2
    assert first.page.next_cursor

    second = fixtures["projection"].query(replace(request, cursor=first.page.next_cursor))
    assert second.page.node_count == 1
    assert second.page.next_cursor == ""
    assert {item.entity_id for item in first.node_evidence}.isdisjoint(
        {item.entity_id for item in second.node_evidence}
    )

    other = _query(
        fixtures,
        GraphQueryKind.COMMUNITY,
        budget=budget,
        community_ref="community-beta",
        cursor=first.page.next_cursor,
    )
    with pytest.raises(CodebaseMemoryGraphError, match="cursor does not belong"):
        fixtures["projection"].query(other)


def test_query_subgraph_is_induced_and_bounded_to_selected_entities():
    fixtures = _fixtures()
    request = _query(fixtures, GraphQueryKind.QUERY_SUBGRAPH)
    result = fixtures["projection"].query(request)

    assert {item.entity_id for item in result.node_evidence} == set(request.query_entity_ids)
    assert result.page.edge_count == 2
    assert all(
        edge.source_visual_node_id in {node.visual_node_id for node in result.node_evidence}
        and edge.target_visual_node_id in {node.visual_node_id for node in result.node_evidence}
        for edge in result.edge_evidence
    )


def test_visual_ids_are_stable_and_independent_of_raw_engine_ids():
    first = _fixtures(engine_suffix="one")
    second = _fixtures(engine_suffix="two")
    first_page = first["projection"].query(_query(first, GraphQueryKind.NEIGHBORHOOD))
    second_page = second["projection"].query(_query(second, GraphQueryKind.NEIGHBORHOOD))

    assert {item.visual_node_id for item in first_page.node_evidence} == {
        item.visual_node_id for item in second_page.node_evidence
    }
    assert {item.visual_edge_id for item in first_page.edge_evidence} == {
        item.visual_edge_id for item in second_page.edge_evidence
    }
    rendered = str(first_page.audit_summary()) + str(first_page.page.nodes) + str(first_page.page.edges)
    assert "engine-symbol" not in rendered
    assert "engine-edge" not in rendered


def test_node_and_edge_receipts_preserve_exact_usi_mapping_evidence():
    fixtures = _fixtures()
    result = fixtures["projection"].query(_query(fixtures, GraphQueryKind.NEIGHBORHOOD))
    symbols = {item.entity_id: item for item in fixtures["symbols"]}
    edges = {item.relation_id: item for item in fixtures["edges"]}

    for item in result.node_evidence:
        mapping = symbols[item.entity_id]
        assert item.source_id == mapping.source_id
        assert item.source_version_id == mapping.source_version_id
        assert item.symbol_mapping_ref == mapping.fallback_key
        assert item.locator == mapping.location
    for item in result.edge_evidence:
        mapping = edges[item.relation_id]
        assert item.edge_mapping_ref == mapping.fallback_key
        assert item.relation_kind == mapping.relation_kind.value


def test_missing_focus_returns_honest_empty_page():
    fixtures = _fixtures()
    request = _query(
        fixtures,
        GraphQueryKind.NEIGHBORHOOD,
        node_entity_id="usi_entity_" + "f" * 64,
    )
    result = fixtures["projection"].query(request)

    assert result.page.status is ProgressiveGraphStatus.EMPTY
    assert result.page.node_count == 0
    assert result.page.clipped is False


def test_large_graph_metadata_stays_aggregate_only_and_bounded():
    def million(_request, _offset):
        return CodeGraphSlice(
            (),
            (),
            (
                CodeGraphAggregateRecord("community-services", "Services", 600_000),
                CodeGraphAggregateRecord("community-data", "Data", 400_000),
            ),
            1_000_000,
            8_000_000,
            False,
            0,
            0,
            1,
            aggregate_only=True,
        )

    store = FakeStore(million)
    result = CodebaseMemoryGraphProjection(store).query(
        CodeGraphQuery(GRAPH, GraphQueryKind.OVERVIEW, "million", CodeGraphBudget())
    )

    assert result.total_node_count == 1_000_000
    assert result.total_edge_count == 8_000_000
    assert result.page.node_count == 0
    assert len(result.page.aggregates) == 2
    assert result.payload_bytes <= CodeGraphBudget().payload_budget_bytes
    assert store.requests[0][1] == 0


def test_payload_budget_clips_instead_of_silently_overflowing():
    fixtures = _fixtures()
    budget = CodeGraphBudget(
        limit=10,
        max_nodes=10,
        max_edges=20,
        depth=4,
        max_hops=4,
        time_budget_ms=1000,
        payload_budget_bytes=2048,
    )
    result = fixtures["projection"].query(
        _query(fixtures, GraphQueryKind.NEIGHBORHOOD, budget=budget)
    )

    assert result.payload_bytes <= 2048
    assert result.page.status is ProgressiveGraphStatus.CLIPPED
    assert result.page.clipped is True
    assert result.page.reason == "payload_budget_limited"


@pytest.mark.parametrize(
    "result,match",
    [
        (
            lambda f, q: CodeGraphSlice(
                tuple(CodeGraphNodeRecord(item, "community-x") for item in f["symbols"]),
                (),
                (),
                5,
                0,
                False,
                5,
                0,
                0,
            ),
            "node budget",
        ),
        (
            lambda f, q: CodeGraphSlice(
                (CodeGraphNodeRecord(f["symbols"][0], "community-x"),),
                (CodeGraphEdgeRecord(f["edges"][0]),),
                (),
                1,
                1,
                False,
                1,
                1,
                0,
            ),
            "missing endpoint",
        ),
        (
            lambda f, q: CodeGraphSlice((), (), (), 0, 0, False, 0, 0, q.budget.time_budget_ms + 1),
            "time budget",
        ),
        (
            lambda f, q: CodeGraphSlice(
                (), (), (), 0, 0, False, q.budget.max_nodes * 10, 0, 0
            ),
            "examination work",
        ),
    ],
)
def test_untrusted_store_output_fails_closed(result, match):
    fixtures = _fixtures()
    budget = CodeGraphBudget(
        limit=2,
        max_nodes=2,
        max_edges=2,
        depth=1,
        max_hops=1,
        time_budget_ms=100,
        payload_budget_bytes=4096,
    )
    request = _query(fixtures, GraphQueryKind.QUERY_SUBGRAPH, budget=budget)
    store = FakeStore(lambda q, _offset: result(fixtures, q))
    with pytest.raises(CodebaseMemoryGraphError, match=match):
        CodebaseMemoryGraphProjection(store).query(request)


def test_catalog_rejects_unmapped_edge_endpoint():
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemoryGraphError, match="endpoint"):
        CatalogCodeGraphStore(
            GRAPH,
            symbols=fixtures["symbols"][:1],
            edges=fixtures["edges"][:1],
        )


def test_aggregate_only_slice_cannot_hide_detail_records():
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemoryGraphError, match="aggregate-only"):
        CodeGraphSlice(
            (CodeGraphNodeRecord(fixtures["symbols"][0], "community-x"),),
            (),
            (CodeGraphAggregateRecord("bucket-x", "Bucket", 1),),
            1,
            0,
            False,
            0,
            0,
            0,
            aggregate_only=True,
        )


def test_aggregate_overflow_is_explicitly_clipped():
    aggregates = tuple(
        CodeGraphAggregateRecord(f"bucket-{index}", f"Bucket {index}", 1)
        for index in range(129)
    )
    store = FakeStore(CodeGraphSlice((), (), aggregates, 129, 0, False, 0, 0, 0, aggregate_only=True))
    result = CodebaseMemoryGraphProjection(store).query(
        CodeGraphQuery(
            GRAPH,
            GraphQueryKind.OVERVIEW,
            "aggregate-overflow",
            CodeGraphBudget(payload_budget_bytes=256 * 1024),
        )
    )

    assert len(result.page.aggregates) == 128
    assert result.page.status is ProgressiveGraphStatus.CLIPPED
    assert result.page.clipped is True


def test_module_has_no_ui_runtime_filesystem_process_network_or_live_path():
    source = __import__("pathlib").Path(
        "src/codebase_memory_graph_projection.py"
    ).read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "socket" not in source
    assert "query_structural" not in source
    assert "open(" not in source
    assert "static/" not in source

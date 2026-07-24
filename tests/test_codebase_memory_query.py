from __future__ import annotations

from dataclasses import replace
import hashlib
import json

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
from src.codebase_memory_projection import (
    GenerationState,
    OneWayRepositoryBridge,
    ProjectionGeneration,
)
from src.codebase_memory_query import (
    CodebaseMemoryQueryError,
    ProviderUnavailableError,
    QueryBudget,
    QueryStatus,
    StructuralMappingCatalog,
    StructuralOperation,
    StructuralQueryProvider,
    StructuralQueryRequest,
)
from src.project_version_store import (
    StoredProjectVersion,
    VERSION_MANIFEST_SCHEMA,
    canonical_json_bytes,
    owner_key_for,
)
from src.repo_registry import RepoRecord, RepoRegistry
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


COMMIT = "a" * 40
NOW = "2026-07-18T10:00:00Z"
SNAPSHOT = "usi_snapshot_" + "1" * 64


class FakeTransport:
    def __init__(self, responses=None, *, unavailable: bool = False, error: Exception | None = None):
        self.responses = dict(responses or {})
        self.unavailable = unavailable
        self.error = error
        self.requests = []

    def query_structural(self, request):
        self.requests.append(request)
        if self.unavailable:
            raise ProviderUnavailableError("fixture unavailable")
        if self.error is not None:
            raise self.error
        response = self.responses.get(request.operation)
        if callable(response):
            return response(request)
        return response if response is not None else _raw()


def _raw(*, nodes=(), edges=(), has_more=False, stats=None):
    return {
        "node_keys": list(nodes),
        "edges": list(edges),
        "has_more": has_more,
        "stats": stats
        or {
            "nodes_examined": len(nodes),
            "edges_examined": len(edges),
            "duration_ms": 5,
            "timed_out": False,
        },
    }


def _fixtures(state: GenerationState = GenerationState.ACTIVE):
    record = RepoRecord.create(
        repo_id="demo",
        title="Demo",
        owner="alice",
        workspace_root="repos",
        project_root="repos/demo",
        path_ref="repos/demo",
        created_at=NOW,
        current_branch="main",
        allowed_actions=("status",),
    )
    registry = RepoRegistry()
    registry.add(record)
    manifest = {
        "schema": VERSION_MANIFEST_SCHEMA,
        "owner_key": owner_key_for("alice"),
        "repo_id": "demo",
        "transaction_id": "pct_" + "b" * 32,
        "version_id": "pv_" + "c" * 32,
        "commit_sha": COMMIT,
        "created_at": NOW,
        "policy_snapshot": {"schema": "policy.v1", "mode": "local"},
        "artifacts": [],
    }
    project_version = StoredProjectVersion(
        manifest,
        "sha256:" + hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
    )
    extraction = ExtractionEvidence(
        ExtractionMethod.CBM_PARSER, 0.92, "cbm", "0.9.0", False
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
        revision_ref=f"git:{COMMIT}",
        content_hash=content_hash("fixture code"),
        version_observed_at=NOW,
    )
    file_mapping = CodeFileMapping.create(
        source,
        version,
        repo_id="demo",
        relative_path="src/main.py",
        byte_length=12,
        engine_project_ref="project-fixture",
        engine_file_ref="file-fixture",
        evidence=extraction,
    )

    symbols = []
    for index, name in enumerate(("Service.run", "Store.save", "Router.route"), start=1):
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
            natural_key=symbol_natural_key(
                CodeSymbolKind.METHOD, name, signature
            ),
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
        symbols.append((entity, mapping))

    edge_mappings = {}
    for relation_kind in (
        RelationKind.CALLS,
        RelationKind.IMPORTS,
        RelationKind.INHERITS,
        RelationKind.REFERENCES,
        RelationKind.RELATED_TO,
        RelationKind.BELONGS_TO,
        RelationKind.TESTS,
    ):
        relation = RelationRecord.create(
            symbols[0][0].ref(),
            symbols[1][0].ref(),
            relation_kind=relation_kind,
            method_ref=extraction.extractor_profile_ref,
            confidence=0.92,
            evidence_refs=(
                symbols[0][0].evidence_ref(),
                symbols[1][0].evidence_ref(),
            ),
        )
        edge_mappings[relation_kind] = CodeEdgeMapping.create(
            relation,
            engine_project_ref="project-fixture",
            engine_edge_ref=f"edge-{relation_kind.value}",
            evidence=extraction,
        )

    plan = OneWayRepositoryBridge().create_plan(
        registry,
        repo_id="demo",
        project_version=project_version,
        input_snapshot_ref=SNAPSHOT,
        file_mappings=(file_mapping,),
        input_evidence=(version.evidence_ref(),),
    )
    generation = ProjectionGeneration(
        plan,
        state,
        error_code="fixture_failed" if state is GenerationState.FAILED else "",
    )
    catalog = StructuralMappingCatalog(
        generation,
        files=(file_mapping,),
        symbols=tuple(item[1] for item in symbols),
        edges=tuple(edge_mappings.values()),
    )
    return {
        "generation": generation,
        "catalog": catalog,
        "file": file_mapping,
        "symbols": tuple(item[1] for item in symbols),
        "edges": edge_mappings,
    }


def _request(fixtures, operation: StructuralOperation, *, cursor: str = "", scope=True):
    subject = (
        ""
        if operation in {StructuralOperation.ROUTES, StructuralOperation.COMMUNITIES}
        else fixtures["symbols"][0].entity_id
    )
    scope_ids = tuple(item.entity_id for item in fixtures["symbols"]) if scope else ()
    return StructuralQueryRequest(
        "demo",
        fixtures["generation"].generation_ref,
        operation,
        subject,
        scope_ids,
        QueryBudget(limit=10, max_nodes_examined=50, max_edges_examined=50, max_depth=4, timeout_ms=1000),
        cursor,
    )


_RELATION_BY_OPERATION = {
    StructuralOperation.SYMBOL: None,
    StructuralOperation.CALLERS: RelationKind.CALLS,
    StructuralOperation.CALLEES: RelationKind.CALLS,
    StructuralOperation.IMPORTS: RelationKind.IMPORTS,
    StructuralOperation.INHERITANCE: RelationKind.INHERITS,
    StructuralOperation.ROUTES: RelationKind.REFERENCES,
    StructuralOperation.DATAFLOW: RelationKind.REFERENCES,
    StructuralOperation.COMMUNITIES: RelationKind.RELATED_TO,
    StructuralOperation.IMPACT: RelationKind.TESTS,
}


@pytest.mark.parametrize("operation", list(StructuralOperation))
def test_all_nine_operations_return_bounded_exact_deterministic_usi_mappings(operation):
    fixtures = _fixtures()
    relation_kind = _RELATION_BY_OPERATION[operation]
    edges = () if relation_kind is None else ({"edge_key": fixtures["edges"][relation_kind].fallback_key},)
    response = _raw(nodes=(fixtures["symbols"][0].fallback_key,), edges=edges)
    transport = FakeTransport({operation: response})
    provider = StructuralQueryProvider(transport, fixtures["catalog"])
    request = _request(fixtures, operation)

    first = provider.query(request)
    second = provider.query(request)

    assert first == second
    assert first.status is QueryStatus.COMPLETE
    assert first.operation is operation
    assert first.generation_ref == fixtures["generation"].generation_ref
    assert first.nodes[0].mapping.entity_id == fixtures["symbols"][0].entity_id
    assert first.nodes[0].mapping.source_version_id == fixtures["file"].source_version_id
    assert len(first.nodes) + len(first.edges) <= request.budget.limit
    assert first.next_cursor == ""
    assert first.fallback_required is False
    assert transport.requests[-1].operation is operation
    if relation_kind is not None:
        assert first.edges[0].mapping.relation_kind is relation_kind
        assert first.edges[0].to_dict()["confidence"] == 0.92


def test_request_round_trip_rejects_unknown_operation_and_arbitrary_query_language():
    fixtures = _fixtures()
    request = _request(fixtures, StructuralOperation.CALLERS)
    restored = StructuralQueryRequest.from_dict(request.to_dict())
    assert restored == request
    assert restored.query_id == request.query_id

    unknown = request.to_dict()
    unknown["operation"] = "cypher"
    with pytest.raises(CodebaseMemoryQueryError, match="unknown"):
        StructuralQueryRequest.from_dict(unknown)

    arbitrary = request.to_dict()
    arbitrary["cypher"] = "MATCH (n) RETURN n"
    with pytest.raises(CodebaseMemoryQueryError, match="unknown"):
        StructuralQueryRequest.from_dict(arbitrary)

    malformed_scope = request.to_dict()
    malformed_scope["scope_entity_ids"] = 7
    with pytest.raises(CodebaseMemoryQueryError, match="scope_entity_ids"):
        StructuralQueryRequest.from_dict(malformed_scope)


@pytest.mark.parametrize(
    "changes",
    [
        {"limit": 0},
        {"limit": 101},
        {"max_nodes_examined": 10001},
        {"max_edges_examined": 20001},
        {"max_depth": 17},
        {"timeout_ms": 30001},
    ],
)
def test_unbounded_query_budgets_fail_closed(changes):
    values = QueryBudget().to_dict()
    values.update(changes)
    with pytest.raises(CodebaseMemoryQueryError):
        QueryBudget.from_dict(values)


@pytest.mark.parametrize("operation", list(_SUBJECT_REQUIRED := {
    StructuralOperation.SYMBOL,
    StructuralOperation.CALLERS,
    StructuralOperation.CALLEES,
    StructuralOperation.IMPORTS,
    StructuralOperation.INHERITANCE,
    StructuralOperation.DATAFLOW,
    StructuralOperation.IMPACT,
}))
def test_subject_bound_operations_require_exact_usi_entity(operation):
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemoryQueryError, match="requires"):
        StructuralQueryRequest(
            "demo",
            fixtures["generation"].generation_ref,
            operation,
            "",
            (),
            QueryBudget(),
        )


def test_pagination_cursor_is_deterministic_tamper_evident_and_query_bound():
    fixtures = _fixtures()

    def response(request):
        if request.offset == 0:
            return _raw(nodes=(fixtures["symbols"][0].fallback_key,), has_more=True)
        assert request.offset == 1
        return _raw(nodes=(fixtures["symbols"][1].fallback_key,), has_more=False)

    provider = StructuralQueryProvider(
        FakeTransport({StructuralOperation.CALLERS: response}), fixtures["catalog"]
    )
    first_request = _request(fixtures, StructuralOperation.CALLERS)
    first = provider.query(first_request)
    assert first.status is QueryStatus.PARTIAL
    assert first.clipped is True
    assert first.next_cursor.startswith("cbmq_1_")

    second = provider.query(
        _request(fixtures, StructuralOperation.CALLERS, cursor=first.next_cursor)
    )
    assert second.status is QueryStatus.COMPLETE
    assert second.nodes[0].entity_id == fixtures["symbols"][1].entity_id
    assert second.next_cursor == ""

    tampered = first.next_cursor[:-1] + ("0" if first.next_cursor[-1] != "0" else "1")
    with pytest.raises(CodebaseMemoryQueryError, match="cursor"):
        provider.query(_request(fixtures, StructuralOperation.CALLERS, cursor=tampered))
    with pytest.raises(CodebaseMemoryQueryError, match="cursor"):
        provider.query(_request(fixtures, StructuralOperation.CALLEES, cursor=first.next_cursor))


def test_unresolved_edges_are_visible_with_confidence_method_and_partial_status():
    fixtures = _fixtures()
    unresolved = {
        "unresolved_ref": "edge-unresolved-1",
        "source_symbol_key": fixtures["symbols"][0].fallback_key,
        "target_engine_ref": "engine-target-9",
        "relation_kind": "calls",
        "method": "cbm-parser",
        "confidence": 0.41,
        "extractor_version": "0.9.0",
        "incomplete_parse": True,
        "unresolved_reason": "target_not_mapped",
    }
    provider = StructuralQueryProvider(
        FakeTransport({StructuralOperation.CALLERS: _raw(edges=(unresolved,))}),
        fixtures["catalog"],
    )
    result = provider.query(_request(fixtures, StructuralOperation.CALLERS))
    edge = result.edges[0].to_dict()

    assert result.status is QueryStatus.PARTIAL
    assert result.unresolved_edge_count == 1
    assert edge["resolved"] is False
    assert edge["source_entity_id"] == fixtures["symbols"][0].entity_id
    assert edge["target_entity_id"] == ""
    assert edge["confidence"] == 0.41
    assert edge["incomplete_parse"] is True
    assert edge["unresolved_reason"] == "target_not_mapped"


def test_stale_generation_is_explicitly_partial_but_still_queryable():
    fixtures = _fixtures(GenerationState.STALE)
    transport = FakeTransport(
        {StructuralOperation.CALLERS: _raw(nodes=(fixtures["symbols"][0].fallback_key,))}
    )
    result = StructuralQueryProvider(transport, fixtures["catalog"]).query(
        _request(fixtures, StructuralOperation.CALLERS)
    )
    assert result.status is QueryStatus.PARTIAL
    assert result.stale is True
    assert result.fallback_required is False


@pytest.mark.parametrize("state", [GenerationState.PREPARED, GenerationState.FAILED])
def test_nonqueryable_generation_returns_unavailable_without_transport(state):
    fixtures = _fixtures(state)
    transport = FakeTransport(error=AssertionError("transport must not run"))
    result = StructuralQueryProvider(transport, fixtures["catalog"]).query(
        _request(fixtures, StructuralOperation.CALLERS)
    )
    assert result.status is QueryStatus.UNAVAILABLE
    assert result.failure_code == "projection_unavailable"
    assert result.fallback_required is True
    assert transport.requests == []


def test_provider_unavailable_returns_typed_fallback_signal():
    fixtures = _fixtures()
    result = StructuralQueryProvider(
        FakeTransport(unavailable=True), fixtures["catalog"]
    ).query(_request(fixtures, StructuralOperation.CALLERS))
    assert result.status is QueryStatus.UNAVAILABLE
    assert result.nodes == () and result.edges == ()
    assert result.failure_code == "provider_unavailable"
    assert result.fallback_required is True


def test_provider_unavailable_preserves_stale_projection_signal():
    fixtures = _fixtures(GenerationState.STALE)
    result = StructuralQueryProvider(
        FakeTransport(unavailable=True), fixtures["catalog"]
    ).query(_request(fixtures, StructuralOperation.CALLERS))
    assert result.status is QueryStatus.UNAVAILABLE
    assert result.stale is True
    assert result.clipped is False


def test_unexpected_transport_failure_is_content_free_and_fails_closed():
    fixtures = _fixtures()
    provider = StructuralQueryProvider(
        FakeTransport(error=RuntimeError("C:\\private\\source.py")),
        fixtures["catalog"],
    )
    with pytest.raises(CodebaseMemoryQueryError) as error:
        provider.query(_request(fixtures, StructuralOperation.CALLERS))
    assert "private" not in str(error.value)
    assert "source.py" not in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        {"node_keys": [], "edges": [], "has_more": False},
        _raw(nodes=("cbm_symbol_" + "f" * 64,)),
        _raw(edges=({"edge_key": "cbm_edge_" + "f" * 64},)),
        _raw(nodes=(), has_more=True),
        _raw(stats={"nodes_examined": 51, "edges_examined": 0, "duration_ms": 1, "timed_out": False}),
        _raw(stats={"nodes_examined": 0, "edges_examined": 0, "duration_ms": 1001, "timed_out": True}),
    ],
)
def test_malformed_unknown_empty_more_or_overbudget_responses_fail_closed(response):
    fixtures = _fixtures()
    provider = StructuralQueryProvider(
        FakeTransport({StructuralOperation.CALLERS: response}), fixtures["catalog"]
    )
    with pytest.raises(CodebaseMemoryQueryError):
        provider.query(_request(fixtures, StructuralOperation.CALLERS))


def test_duplicate_and_out_of_scope_results_fail_closed():
    fixtures = _fixtures()
    duplicate = _raw(
        nodes=(
            fixtures["symbols"][0].fallback_key,
            fixtures["symbols"][0].fallback_key,
        )
    )
    provider = StructuralQueryProvider(
        FakeTransport({StructuralOperation.CALLERS: duplicate}), fixtures["catalog"]
    )
    with pytest.raises(CodebaseMemoryQueryError, match="duplicates"):
        provider.query(_request(fixtures, StructuralOperation.CALLERS))

    out_of_scope = _raw(nodes=(fixtures["symbols"][2].fallback_key,))
    provider = StructuralQueryProvider(
        FakeTransport({StructuralOperation.CALLERS: out_of_scope}), fixtures["catalog"]
    )
    request = _request(fixtures, StructuralOperation.CALLERS, scope=False)
    request = replace(
        request,
        scope_entity_ids=(fixtures["symbols"][0].entity_id, fixtures["symbols"][1].entity_id),
    )
    with pytest.raises(CodebaseMemoryQueryError, match="scope"):
        provider.query(request)


def test_query_stats_cannot_under_report_returned_work():
    fixtures = _fixtures()
    response = _raw(
        nodes=(fixtures["symbols"][0].fallback_key,),
        stats={"nodes_examined": 0, "edges_examined": 0, "duration_ms": 1, "timed_out": False},
    )
    provider = StructuralQueryProvider(
        FakeTransport({StructuralOperation.CALLERS: response}), fixtures["catalog"]
    )
    with pytest.raises(CodebaseMemoryQueryError, match="under-report"):
        provider.query(_request(fixtures, StructuralOperation.CALLERS))


def test_edge_relation_must_match_operation():
    fixtures = _fixtures()
    response = _raw(
        edges=({"edge_key": fixtures["edges"][RelationKind.IMPORTS].fallback_key},)
    )
    provider = StructuralQueryProvider(
        FakeTransport({StructuralOperation.CALLERS: response}), fixtures["catalog"]
    )
    with pytest.raises(CodebaseMemoryQueryError, match="relation"):
        provider.query(_request(fixtures, StructuralOperation.CALLERS))


def test_result_limit_counts_nodes_and_edges_together():
    fixtures = _fixtures()
    response = _raw(
        nodes=(fixtures["symbols"][0].fallback_key,),
        edges=({"edge_key": fixtures["edges"][RelationKind.CALLS].fallback_key},),
    )
    request = replace(
        _request(fixtures, StructuralOperation.CALLERS),
        budget=QueryBudget(limit=1, max_nodes_examined=50, max_edges_examined=50, max_depth=4, timeout_ms=1000),
    )
    with pytest.raises(CodebaseMemoryQueryError, match="limit"):
        StructuralQueryProvider(
            FakeTransport({StructuralOperation.CALLERS: response}), fixtures["catalog"]
        ).query(request)


def test_timeout_receipt_is_partial_clipped_and_never_silent():
    fixtures = _fixtures()
    response = _raw(
        nodes=(fixtures["symbols"][0].fallback_key,),
        stats={"nodes_examined": 50, "edges_examined": 0, "duration_ms": 1000, "timed_out": True},
    )
    result = StructuralQueryProvider(
        FakeTransport({StructuralOperation.CALLERS: response}), fixtures["catalog"]
    ).query(_request(fixtures, StructuralOperation.CALLERS))
    assert result.status is QueryStatus.PARTIAL
    assert result.clipped is True
    assert result.failure_code == "query_timeout"


def test_catalog_must_match_projection_files_and_symbol_edge_ancestry():
    fixtures = _fixtures()
    with pytest.raises(CodebaseMemoryQueryError, match="files"):
        StructuralMappingCatalog(
            fixtures["generation"],
            files=(),
            symbols=fixtures["symbols"],
            edges=fixtures["edges"].values(),
        )

    duplicate_symbol = fixtures["symbols"][0]
    with pytest.raises(CodebaseMemoryQueryError, match="duplicate"):
        StructuralMappingCatalog(
            fixtures["generation"],
            files=(fixtures["file"],),
            symbols=(duplicate_symbol, duplicate_symbol),
            edges=(),
        )

    with pytest.raises(TypeError):
        fixtures["catalog"].files[fixtures["file"].fallback_key] = fixtures["file"]


def test_subject_and_scope_must_exist_in_selected_projection_catalog():
    fixtures = _fixtures()
    provider = StructuralQueryProvider(FakeTransport(), fixtures["catalog"])
    unknown = "usi_entity_" + "f" * 64

    request = _request(fixtures, StructuralOperation.CALLERS, scope=False)
    with pytest.raises(CodebaseMemoryQueryError, match="subject_entity_id"):
        provider.query(replace(request, subject_entity_id=unknown))

    with pytest.raises(CodebaseMemoryQueryError, match="scope_entity_ids"):
        provider.query(
            replace(
                request,
                scope_entity_ids=(request.subject_entity_id, unknown),
            )
        )


def test_serialized_results_contain_no_raw_content_or_absolute_host_paths():
    fixtures = _fixtures()
    result = StructuralQueryProvider(
        FakeTransport(
            {
                StructuralOperation.CALLERS: _raw(
                    nodes=(fixtures["symbols"][0].fallback_key,),
                    edges=({"edge_key": fixtures["edges"][RelationKind.CALLS].fallback_key},),
                )
            }
        ),
        fixtures["catalog"],
    ).query(_request(fixtures, StructuralOperation.CALLERS))
    rendered = json.dumps(result.to_dict(), sort_keys=True).lower()
    for forbidden in (
        "source_text",
        "snippet",
        "prompt",
        "model_output",
        "absolute_path",
        "host_path",
        "c:\\\\",
        "/home/",
    ):
        assert forbidden not in rendered

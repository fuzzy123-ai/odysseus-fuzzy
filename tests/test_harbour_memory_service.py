"""Comprehensive tests for HarbourMemoryService and USI adapter registry (Aspekt 4)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from src.harbour.memory import (
    HarbourMemoryService,
    HarbourMemoryServiceError,
    default_source_adapter_registry,
)
from src.harbour.mcp.dispatcher import HarbourMcpDispatcher, HarbourMcpDispatchError
from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    ChunkRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore


REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-07-17T06:00:00Z"


def _create_source(
    canonical_ref: str = "repo:alpha/src/service.py",
    *,
    owner_scope: str = "owner:admin",
) -> SourceRecord:
    return SourceRecord(
        owner_scope=owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref=canonical_ref,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="local-git",
        first_seen_at=NOW,
        source_modified_at=NOW,
        valid_from=NOW,
    )


def _create_version(source: SourceRecord, revision: str = "git:rev123") -> SourceVersionRecord:
    return SourceVersionRecord.create(
        source,
        revision_ref=revision,
        content_hash=content_hash(revision),
        version_observed_at=NOW,
        indexed_at=NOW,
    )


def _create_chunk(version: SourceVersionRecord, text: str, *, start: int = 0) -> ChunkRecord:
    return ChunkRecord.create(
        version,
        locator=TextRangeLocator(start, start + len(text)),
        extractor_profile_ref="text-v1",
        content_hash=content_hash(text),
        content=text,
        indexed_at=NOW,
    )


# ---------------------------------------------------------------------------
# 1. Registry tests
# ---------------------------------------------------------------------------

def test_default_source_adapter_registry_contains_approved_sources():
    """default_source_adapter_registry must deterministically contain exactly the 3 approved USI adapters."""
    registry = default_source_adapter_registry()
    manifests = registry.manifests()

    assert len(manifests) == 3

    adapter_ids = {m.adapter_id for m in manifests}
    expected_adapters = {
        "planning.approved_sources",
        "memory.accepted",
        "knowledge.native",
    }
    assert adapter_ids == expected_adapters

    # Check registration retrieval
    for adapter_id in expected_adapters:
        registration = registry.registration(adapter_id)
        assert registration.manifest.adapter_id == adapter_id

    # Check domain resolution
    assert registry.for_domain("planning").adapter_id == "planning.approved_sources"
    assert registry.for_domain("personal_memory").adapter_id == "memory.accepted"
    assert registry.for_domain("native_knowledge").adapter_id == "knowledge.native"

    # Check select method
    selection = registry.select("planning.approved_sources")
    assert selection.adapter_id == "planning.approved_sources"
    assert "implementation_ref" in selection.projection_evidence()


# ---------------------------------------------------------------------------
# 2. Parameter validation tests
# ---------------------------------------------------------------------------

def test_query_usi_parameter_validation():
    """query_usi must strictly validate query, limit, and owner_scope parameters."""
    service = HarbourMemoryService(repo_root=REPO_ROOT)

    # Empty or whitespace query
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi("")
    assert exc.value.code == "invalid_params"

    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi("   \t\n  ")
    assert exc.value.code == "invalid_params"

    # Non-string query
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi(None)  # type: ignore[arg-type]
    assert exc.value.code == "invalid_params"

    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi(12345)  # type: ignore[arg-type]
    assert exc.value.code == "invalid_params"

    # Query exceeding 1000 characters
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi("x" * 1001)
    assert exc.value.code == "invalid_params"

    # Limit validation: out of bounds or wrong type
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi("test", limit=0)
    assert exc.value.code == "invalid_params"

    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi("test", limit=-10)
    assert exc.value.code == "invalid_params"

    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi("test", limit=101)
    assert exc.value.code == "invalid_params"

    # Bool limit (bool is subclass of int)
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi("test", limit=True)  # type: ignore[arg-type]
    assert exc.value.code == "invalid_params"

    # Non-int limit
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi("test", limit="10")  # type: ignore[arg-type]
    assert exc.value.code == "invalid_params"

    # owner_scope validation
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.query_usi("test", owner_scope="")
    assert exc.value.code == "invalid_params"


# ---------------------------------------------------------------------------
# 3. Fallback path (census) tests
# ---------------------------------------------------------------------------

def test_query_usi_census_fallback():
    """When no SQLite store with chunks is active, query_usi uses the deterministic structural census."""
    service = HarbourMemoryService(repo_root=REPO_ROOT)
    assert service.store is None

    result = service.query_usi("roadmap", limit=5)
    assert result["schema"] == "odysseus.harbour.memory_query_result.v1"
    assert result["query"] == "roadmap"
    assert result["limit"] == 5
    assert result["total_matches"] > 0
    assert len(result["matches"]) <= 5

    for m in result["matches"]:
        assert "source_id" in m
        assert "path" in m
        assert "kind" in m
        assert "structural_role" in m

    # Query with no match returns empty matches list
    no_match = service.query_usi("nonexistent_unique_token_xyz_9999", limit=5)
    assert no_match["schema"] == "odysseus.harbour.memory_query_result.v1"
    assert no_match["total_matches"] == 0
    assert no_match["matches"] == []


# ---------------------------------------------------------------------------
# 4. Primary path (SQLite FTS5 chunk search) tests
# ---------------------------------------------------------------------------

def test_query_usi_sqlite_fts5_search(tmp_path):
    """When a SQLiteUnifiedSourceIndexStore with chunks is active, query_usi searches FTS5 BM25 chunks."""
    db_file = tmp_path / "test_usi.sqlite3"
    store = SQLiteUnifiedSourceIndexStore(db_file)

    # Populate store with source, version, and chunks
    src = _create_source("repo:alpha/src/harbour.py", owner_scope="owner:admin")
    ver = _create_version(src, "git:rev001")
    chunk_1 = _create_chunk(ver, "Harbour memory service provides unified indexing and retrieval.")
    chunk_2 = _create_chunk(ver, "Planning context reader handles roadmap and milestone projections.")
    chunk_3 = _create_chunk(ver, "Gateway coordinator manages isolated worker pods.")

    bob_src = _create_source("repo:beta/private.py", owner_scope="user:bob")
    bob_ver = _create_version(bob_src, "git:bob001")
    bob_chunk = _create_chunk(bob_ver, "Harbour memory secret notes for Bob only.")

    write = store.begin_write(store.current_snapshot())
    for r in (src, ver, chunk_1, chunk_2, chunk_3, bob_src, bob_ver, bob_chunk):
        write.put(r)
    write.commit()

    service = HarbourMemoryService(repo_root=tmp_path, store=store)
    assert service.store is store

    # Search matching chunk 1
    res = service.query_usi("retrieval", limit=10, owner_scope="owner:admin")
    assert res["schema"] == "odysseus.harbour.memory_query_result.v1"
    assert res["query"] == "retrieval"
    assert res["limit"] == 10
    assert res["total_matches"] == 1

    match = res["matches"][0]
    assert match["source_id"] == src.source_id
    assert match["chunk_id"] == chunk_1.chunk_id
    assert "retrieval" in match["snippet"]
    assert isinstance(match["rank"], float)

    # Search matching "Harbour" with owner:admin scope (should find chunk 1, but NOT bob_chunk)
    harbour_res = service.query_usi("Harbour", limit=10, owner_scope="owner:admin")
    assert harbour_res["total_matches"] == 1
    assert harbour_res["matches"][0]["chunk_id"] == chunk_1.chunk_id

    # Search with user:bob scope
    bob_res = service.query_usi("Harbour", limit=10, owner_scope="user:bob")
    assert bob_res["total_matches"] == 1
    assert bob_res["matches"][0]["chunk_id"] == bob_chunk.chunk_id

    # Search with no match
    empty_res = service.query_usi("astronomy", limit=5, owner_scope="owner:admin")
    assert empty_res["total_matches"] == 0
    assert empty_res["matches"] == []


# ---------------------------------------------------------------------------
# 5. Store auto-discovery tests
# ---------------------------------------------------------------------------

def test_store_auto_discovery(tmp_path):
    """HarbourMemoryService must auto-discover data/usi.sqlite3 when store is not provided."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "usi.sqlite3"

    store = SQLiteUnifiedSourceIndexStore(db_file)
    src = _create_source("repo:autodisc/main.py", owner_scope="owner:admin")
    ver = _create_version(src, "git:disc1")
    chunk = _create_chunk(ver, "Auto-discovered SQLite index content for verification.")

    write = store.begin_write(store.current_snapshot())
    for r in (src, ver, chunk):
        write.put(r)
    write.commit()

    # Construct service without explicit store parameter
    service = HarbourMemoryService(repo_root=tmp_path)
    assert service.store is not None

    res = service.query_usi("Auto-discovered", limit=5)
    assert res["total_matches"] == 1
    assert res["matches"][0]["chunk_id"] == chunk.chunk_id


# ---------------------------------------------------------------------------
# 6. Context manifest tests
# ---------------------------------------------------------------------------

def test_get_context_manifest():
    """get_context_manifest must produce standardized manifest with registered adapters."""
    service = HarbourMemoryService(repo_root=REPO_ROOT)

    # Default session
    manifest = service.get_context_manifest()
    assert manifest["schema"] == "odysseus.harbour.context_manifest.v1"
    assert manifest["manifest_version"] == "1.0.0"
    assert manifest["session_id"] == "default-harbour-session"
    assert manifest["bounded"] is True
    assert manifest["memory_domain"]["usi_status"] == "ready"
    assert manifest["planning_domain"]["status"] == "synchronized"
    assert manifest["planning_domain"]["scope"] == "mcp:harbour:context:read"

    # Registered adapters in manifest
    assert "registered_adapters" in manifest
    assert len(manifest["registered_adapters"]) == 3
    registered_ids = {a["adapter_id"] for a in manifest["registered_adapters"]}
    assert registered_ids == {
        "planning.approved_sources",
        "memory.accepted",
        "knowledge.native",
    }

    # Custom session_id
    custom = service.get_context_manifest(session_id="custom-session-99")
    assert custom["session_id"] == "custom-session-99"

    # Invalid session_id
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.get_context_manifest(session_id=123)  # type: ignore[arg-type]
    assert exc.value.code == "invalid_params"

    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.get_context_manifest(session_id="x" * 300)
    assert exc.value.code == "invalid_params"


# ---------------------------------------------------------------------------
# 7. Runtime status tests
# ---------------------------------------------------------------------------

def test_get_runtime_status():
    """get_runtime_status must project USI status accurately."""
    service = HarbourMemoryService(repo_root=REPO_ROOT)
    status = service.get_runtime_status()

    assert status["schema"] == "odysseus.unified_source_index.runtime_status.v1"
    assert "knowledge" in status
    assert "index_job_pump" in status
    assert "snapshot_atomic" in status
    assert "live_activation_authorized" in status


# ---------------------------------------------------------------------------
# 8. MCP Dispatcher integration tests
# ---------------------------------------------------------------------------

def test_dispatcher_memory_integration():
    """Dispatcher must delegate memory calls to HarbourMemoryService cleanly."""
    dispatcher = HarbourMcpDispatcher(repo_root=REPO_ROOT)

    class DummyIdentity:
        scope = "mcp:harbour:context:read"

    identity = DummyIdentity()

    # 1. query_usi
    res = dispatcher.dispatch(
        "harbour_memory_query_usi",
        {"query": "roadmap", "limit": 5},
        identity=identity,
    )
    assert res["schema"] == "odysseus.harbour.memory_query_result.v1"
    assert res["query"] == "roadmap"
    assert res["limit"] == 5
    assert len(res["matches"]) <= 5

    # 2. query_usi with invalid params
    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch("harbour_memory_query_usi", {"query": ""}, identity=identity)
    assert exc.value.code == "invalid_params"

    with pytest.raises(HarbourMcpDispatchError) as exc:
        dispatcher.dispatch("harbour_memory_query_usi", {"query": "test", "limit": 999}, identity=identity)
    assert exc.value.code == "invalid_params"

    # 3. get_context_manifest
    manifest = dispatcher.dispatch(
        "harbour_memory_get_context_manifest",
        {"session_id": "integration-sess-1"},
        identity=identity,
    )
    assert manifest["schema"] == "odysseus.harbour.context_manifest.v1"
    assert manifest["session_id"] == "integration-sess-1"
    assert manifest["bounded"] is True
    assert "registered_adapters" in manifest

    # 4. runtime_status
    status = dispatcher.dispatch(
        "harbour_memory_runtime_status",
        {},
        identity=identity,
    )
    assert status["schema"] == "odysseus.unified_source_index.runtime_status.v1"
    assert "knowledge" in status
    assert "index_job_pump" in status


def test_dispatcher_custom_memory_service_injection():
    """HarbourMcpDispatcher must accept an injected HarbourMemoryService."""
    class CustomMemoryService(HarbourMemoryService):
        def query_usi(self, query: str, limit: int = 10, *, owner_scope: str = "owner:admin"):
            return {
                "schema": "odysseus.harbour.memory_query_result.v1",
                "query": query,
                "limit": limit,
                "total_matches": 1,
                "matches": [{"source_id": "custom:source", "path": "custom/path", "kind": "test"}],
            }

    custom_service = CustomMemoryService(repo_root=REPO_ROOT)
    dispatcher = HarbourMcpDispatcher(repo_root=REPO_ROOT, memory_service=custom_service)

    class DummyIdentity:
        scope = "mcp:harbour:context:read"

    res = dispatcher.dispatch(
        "harbour_memory_query_usi",
        {"query": "custom_query", "limit": 3},
        identity=DummyIdentity(),
    )
    assert res["total_matches"] == 1
    assert res["matches"][0]["source_id"] == "custom:source"


# ---------------------------------------------------------------------------
# 8. Index sources tests (Etappe 3)
# ---------------------------------------------------------------------------

def test_index_sources_parameter_validation(tmp_path):
    """index_sources must strictly validate sources, owner_scope, and batch_size."""
    service = HarbourMemoryService(repo_root=tmp_path)

    # Invalid sources type
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.index_sources(sources="not_a_list")  # type: ignore[arg-type]
    assert exc.value.code == "invalid_params"

    # Invalid sources item
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.index_sources(sources=[""])
    assert exc.value.code == "invalid_params"

    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.index_sources(sources=[123])  # type: ignore[list-item]
    assert exc.value.code == "invalid_params"

    # Invalid owner_scope
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.index_sources(owner_scope="")
    assert exc.value.code == "invalid_params"

    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.index_sources(owner_scope=None)  # type: ignore[arg-type]
    assert exc.value.code == "invalid_params"

    # Invalid batch_size
    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.index_sources(batch_size=0)
    assert exc.value.code == "invalid_params"

    with pytest.raises(HarbourMemoryServiceError) as exc:
        service.index_sources(batch_size=-5)
    assert exc.value.code == "invalid_params"


def test_index_sources_creates_sqlite_and_enables_fts5_search(tmp_path):
    """index_sources creates data/usi.sqlite3 and indexes documents for FTS5 BM25 search."""
    docs_dir = tmp_path / "docs" / "plans"
    docs_dir.mkdir(parents=True)
    doc_file = docs_dir / "harbour-memory-spec.md"
    doc_file.write_text(
        "# Harbour Memory Specification\n\n"
        "Unified Source Index (USI) consolidates planning, memory, and code occurrences.\n\n"
        "## BM25 Full Text Search\n"
        "SQLite FTS5 BM25 ranking guarantees fast lexical search with deterministic scoring.\n\n"
        "## Cooperative Indexer\n"
        "Cooperative background pump processes change feeds without lock contention.",
        encoding="utf-8",
    )

    db_path = tmp_path / "data" / "usi.sqlite3"
    service = HarbourMemoryService(repo_root=tmp_path)

    # 1. Before indexing: no chunks exist
    assert not service._has_chunks()

    # 2. Run index_sources on the document
    report = service.index_sources(sources=["docs/plans/harbour-memory-spec.md"])
    assert report["schema"] == "odysseus.harbour.memory_index_report.v1"
    assert report["status"] == "ok"
    assert report["indexed_sources"] == 1
    assert report["indexed_chunks"] == 3
    assert db_path.is_file()

    # 3. After indexing: store has chunks!
    assert service._has_chunks()

    # 4. query_usi must now return FTS5 BM25 matches with snippets and ranks
    res = service.query_usi("BM25", limit=5)
    assert res["schema"] == "odysseus.harbour.memory_query_result.v1"
    assert res["total_matches"] >= 1

    top_hit = res["matches"][0]
    assert "source_id" in top_hit
    assert "chunk_id" in top_hit
    assert "snippet" in top_hit
    assert "rank" in top_hit
    assert "BM25 ranking guarantees" in top_hit["snippet"]
    assert isinstance(top_hit["rank"], float)


def test_index_sources_roadmap_json_semantic_chunking(tmp_path):
    """index_sources must decompose roadmap JSON into overview and feature chunks."""
    roadmaps_dir = tmp_path / "specs" / "roadmaps"
    roadmaps_dir.mkdir(parents=True)
    roadmap_file = roadmaps_dir / "harbour-test-roadmap.v1.json"

    roadmap_data = {
        "schema_version": 1,
        "plan_id": "harbour-test-roadmap",
        "title": "Harbour Integration Roadmap",
        "goal": "Unify all memory, planning, and gateway execution protocols.",
        "status": "active",
        "completed_features": [
            {
                "node_id": "usi-sqlite-fts5-engine",
                "status": "live_installed",
                "evidence": "FTS5 BM25 ranking active on database.",
            },
            {
                "node_id": "mcp-dispatcher-routing",
                "status": "live_installed",
                "evidence": "Dispatcher delegates to memory service.",
            },
        ],
    }
    roadmap_file.write_text(json.dumps(roadmap_data), encoding="utf-8")

    service = HarbourMemoryService(repo_root=tmp_path)
    report = service.index_sources(sources=["specs/roadmaps/harbour-test-roadmap.v1.json"])

    assert report["indexed_sources"] == 1
    assert report["indexed_chunks"] == 3  # 1 Overview + 2 Features

    # Search for a specific feature node_id
    search_feat = service.query_usi("usi-sqlite-fts5-engine", limit=5)
    assert search_feat["total_matches"] == 1
    assert "usi-sqlite-fts5-engine" in search_feat["matches"][0]["snippet"]

    # Search for roadmap goal keyword
    search_goal = service.query_usi("protocols", limit=5)
    assert search_goal["total_matches"] == 1
    assert "Unify all memory" in search_goal["matches"][0]["snippet"]


def test_query_usi_transition_from_fallback_to_fts5(tmp_path):
    """query_usi smoothly transitions from census fallback to FTS5 search once indexed."""
    docs_dir = tmp_path / "docs" / "plans"
    docs_dir.mkdir(parents=True)
    f = docs_dir / "sample-plan.md"
    f.write_text("# Sample Plan\nContent about autonomous orchestration.", encoding="utf-8")

    service = HarbourMemoryService(repo_root=tmp_path)
    assert not service._has_chunks()

    # Query before indexing: Census fallback
    pre_result = service.query_usi("orchestration", limit=5)
    assert pre_result["total_matches"] == 0  # Census fallback doesn't read full text

    # Perform indexing
    service.index_sources(sources=["docs/plans/sample-plan.md"])
    assert service._has_chunks()

    # Query after indexing: FTS5 BM25 hit
    post_result = service.query_usi("orchestration", limit=5)
    assert post_result["total_matches"] == 1
    assert "autonomous orchestration" in post_result["matches"][0]["snippet"]

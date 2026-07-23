"""USI-14 synthetic failure matrix; temporary stores and fakes only."""

import json

import pytest

from src.unified_source_index_context import ContextProjectionRequest, UnifiedSourceIndexContextBridge
from src.unified_source_index_contract import (
    ChunkRecord, Classification, ContentPolicy, RecordKind, SourceKind, SourceRecord,
    SourceVersionRecord, TextRangeLocator, content_hash,
)
from src.unified_source_index_embeddings import (
    DeterministicFakeEmbeddingEncoder, EmbeddingConfig, EmbeddingProjectionStatus,
    FakeChromaGenerationSink, UnifiedSourceIndexEmbeddingProjector,
)
from src.unified_source_index_query import (
    FederatedQuery, ProviderRegistration, ProviderResultItem, ProviderResultPage,
    ProviderStatus, QueryMode, UnifiedSourceIndexQueryPlanner,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexError, SQLiteUnifiedSourceIndexStore


NOW = "2026-07-23T18:00:00Z"
PRIVATE = "USI14_PRIVATE_FAILURE_SENTINEL"


def _records(text=PRIVATE):
    source = SourceRecord("user:alice", SourceKind.DOCUMENT, "doc:usi14-failure",
                          Classification.PRIVATE, ContentPolicy.INLINE_LOCAL, "fake.usi14")
    version = SourceVersionRecord.create(source, revision_ref="rev:usi14", content_hash=content_hash(text),
                                         version_observed_at=NOW)
    chunk = ChunkRecord.create(version, locator=TextRangeLocator(0, len(text)),
                               extractor_profile_ref="text-v1", content_hash=content_hash(text), content=text)
    return source, version, chunk


def _insert(store, *records):
    write = store.begin_write(store.current_snapshot())
    for record in records:
        write.put(record)
    return write.commit()


class _Provider:
    def __init__(self, provider_id, kind, item=None, *, error=None, stale=False):
        self.provider_id, self.provider_kind, self.item, self.error, self.stale = provider_id, kind, item, error, stale

    def query(self, _request):
        if self.error:
            raise self.error
        items = () if self.item is None else (ProviderResultItem(
            self.item.evidence_ref(), 0.9, snippet=PRIVATE, stale=self.stale, why_selected="synthetic"
        ),)
        return ProviderResultPage(items, snapshot_ref="snapshot.usi14")


def _query(*providers):
    return UnifiedSourceIndexQueryPlanner(tuple(ProviderRegistration(item) for item in providers)).execute(
        FederatedQuery("user:alice", "synthetic", Classification.SENSITIVE,
                       modes=tuple(item.provider_kind for item in providers))
    )


def test_corrupt_sqlite_backing_file_is_fail_closed_sanitized_and_content_free(tmp_path, caplog):
    database_path = tmp_path / "usi14-corrupt.db"
    store = SQLiteUnifiedSourceIndexStore(database_path)
    database_path.write_bytes(PRIVATE.encode("utf-8"))
    with pytest.raises(SQLiteUnifiedSourceIndexError) as unavailable:
        store.current_snapshot()
    assert "could not open the isolated USI SQLite database" in str(unavailable.value)
    assert PRIVATE not in str(unavailable.value)
    assert PRIVATE not in caplog.text

    with pytest.raises(SQLiteUnifiedSourceIndexError) as unavailable:
        SQLiteUnifiedSourceIndexStore(":memory:")
    assert PRIVATE not in str(unavailable.value)
    assert "filesystem-backed" in str(unavailable.value)


@pytest.mark.parametrize(("provider_id", "provider_kind"), (
    ("fake.chroma", QueryMode.SEMANTIC),
    ("fake.cbm", QueryMode.SYMBOL),
    ("fake.raptor", QueryMode.GRAPH),
))
def test_embedding_sink_and_optional_query_lane_fail_soft_without_false_success(
    tmp_path, provider_id, provider_kind
):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi14.db")
    source, version, chunk = _records()
    truth = _insert(store, chunk, version, source)
    sink = FakeChromaGenerationSink()
    sink.available = False
    result = UnifiedSourceIndexEmbeddingProjector(
        store, DeterministicFakeEmbeddingEncoder(), sink
    ).rebuild(EmbeddingConfig("semantic-v1", "fake", "v1", 8), owner_scope="user:alice")
    assert result.status is EmbeddingProjectionStatus.UNAVAILABLE
    assert result.error_code == "semantic_sink_unavailable"
    assert result.fallback_required is True
    assert store.current_snapshot() == truth

    page = _query(
        _Provider("fake.lexical", QueryMode.LEXICAL, chunk),
        _Provider(provider_id, provider_kind, error=RuntimeError(PRIVATE)),
    )
    assert [item.record_ref.record_id for item in page.items] == [chunk.chunk_id]
    assert [item.status for item in page.outcomes] == [ProviderStatus.COMPLETED, ProviderStatus.FAILED]
    assert page.partial is True and page.fallback_used is True
    assert PRIVATE not in json.dumps([{"error_code": item.error_code} for item in page.outcomes])


def test_tombstone_hides_truth_and_stale_results_are_excluded_from_lens(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi14.db")
    source, version, chunk = _records("tombstone token")
    snapshot = _insert(store, chunk, version, source)
    deletion = store.begin_write(snapshot)
    deletion.tombstone(RecordKind.CHUNK, chunk.chunk_id, owner_scope="user:alice",
                       expected_record_revision=1, reason="synthetic_deleted")
    deletion.commit()
    assert store.search_chunks(owner_scope="user:alice", query="tombstone") == ()

    stale_page = _query(_Provider("fake.raptor", QueryMode.GRAPH, chunk, stale=True))
    projected = UnifiedSourceIndexContextBridge().project(stale_page, ContextProjectionRequest(
        owner_scope="user:alice", conversation_ref="conversation-usi14", turn_ref="turn-usi14",
        created_at=NOW, model_ref="model-usi14",
    ))
    assert projected.context_items[0].selection_state == "excluded"
    assert projected.provenance == ()
    payload = json.dumps({
        "lens": [event.to_dict() for event in projected.lens_events],
        "outcomes": [{"status": item.status.value, "error_code": item.error_code} for item in stale_page.outcomes],
    }, sort_keys=True)
    assert PRIVATE not in payload

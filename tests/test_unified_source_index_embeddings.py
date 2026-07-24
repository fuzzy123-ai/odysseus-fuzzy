import math

from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    RecordKind,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_embeddings import (
    DeterministicFakeEmbeddingEncoder,
    EmbeddingConfig,
    EmbeddingGenerationHealth,
    EmbeddingProjectionStatus,
    FakeChromaGenerationSink,
    UnifiedSourceIndexEmbeddingProjector,
    inspect_embedding_generation,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_stores import StoredRecord


NOW = "2026-07-17T08:00:00Z"


def _source(
    canonical_ref="doc:alpha",
    *,
    policy=ContentPolicy.INLINE_LOCAL,
):
    return SourceRecord(
        owner_scope="user:alice",
        source_kind=SourceKind.DOCUMENT,
        canonical_ref=canonical_ref,
        classification=Classification.PRIVATE,
        content_policy=policy,
        provider_ref="fake.docs",
    )


def _version(source, revision="rev:1"):
    return SourceVersionRecord.create(
        source,
        revision_ref=revision,
        content_hash=content_hash(revision),
        version_observed_at=NOW,
        indexed_at=NOW,
    )


def _chunk(version, text, start=0):
    return ChunkRecord.create(
        version,
        locator=TextRangeLocator(start, start + len(text)),
        extractor_profile_ref="text-v1",
        content_hash=content_hash(text),
        content=text if version.content_policy is ContentPolicy.INLINE_LOCAL else None,
        indexed_at=NOW,
    )


def _insert(store, *records):
    write = store.begin_write(store.current_snapshot())
    for record in records:
        write.put(record)
    return write.commit()


def _config(version="v1", dimensions=8):
    return EmbeddingConfig(
        profile_ref="semantic-v1",
        model_ref="fake-gemma",
        model_version=version,
        dimensions=dimensions,
        normalize=True,
    )


def _projector(store, sink=None):
    sink = sink or FakeChromaGenerationSink()
    return (
        UnifiedSourceIndexEmbeddingProjector(
            store,
            DeterministicFakeEmbeddingEncoder(),
            sink,
        ),
        sink,
    )


def _stored(store, kind, record_id):
    with store.begin_read() as read:
        item = read.get(kind, record_id, owner_scope="user:alice")
    assert isinstance(item, StoredRecord)
    return item


def test_rebuild_maps_occurrences_to_distinct_points_and_persists_manifest(tmp_path):
    source = _source()
    version = _version(source)
    first = _chunk(version, "identical content", 0)
    second = _chunk(version, "identical content", 100)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, second, first, version, source)
    projector, sink = _projector(store)

    result = projector.rebuild(
        _config(),
        owner_scope="user:alice",
        batch_size=1,
        indexed_at=NOW,
    )

    assert result.status is EmbeddingProjectionStatus.READY
    assert result.projected_count == 2
    assert result.manifest is not None
    assert result.manifest.input_snapshot_ref == result.input_snapshot.snapshot_ref
    assert result.manifest.output_generation_ref == result.generation_ref
    assert sink.active_generation(
        owner_scope="user:alice",
        profile_ref="semantic-v1",
    ) == result.generation_ref
    points = sink.points(result.generation_ref)
    assert {point.point_id for point in points} == {first.chunk_id, second.chunk_id}
    assert points[0].vector == points[1].vector
    assert points[0].point_id != points[1].point_id
    assert all("content" not in point.metadata for point in points)
    assert all(len(point.vector) == 8 for point in points)
    assert all(math.isclose(sum(v * v for v in point.vector), 1.0) for point in points)
    assert inspect_embedding_generation(
        result.manifest,
        sink,
    ) is EmbeddingGenerationHealth.CURRENT
    assert _stored(
        store,
        RecordKind.PROJECTION,
        result.manifest.projection_id,
    ).record == result.manifest


def test_batch_retry_succeeds_without_changing_chunk_truth(tmp_path):
    source = _source()
    version = _version(source)
    chunk = _chunk(version, "retry content")
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, chunk, version, source)
    original = _stored(store, RecordKind.CHUNK, chunk.chunk_id)
    sink = FakeChromaGenerationSink()
    sink.fail_upserts = 1
    projector, _ = _projector(store, sink)

    result = projector.rebuild(
        _config(),
        owner_scope="user:alice",
        max_retries=1,
    )

    assert result.status is EmbeddingProjectionStatus.READY
    assert sink.upsert_attempts == 2
    assert _stored(store, RecordKind.CHUNK, chunk.chunk_id).revision == original.revision


def test_retry_exhaustion_and_unavailable_sink_fail_open_to_other_retrieval(tmp_path):
    source = _source()
    version = _version(source)
    chunk = _chunk(version, "fallback content")
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    truth_snapshot = _insert(store, chunk, version, source)

    failing = FakeChromaGenerationSink()
    failing.fail_upserts = 5
    failed_projector, _ = _projector(store, failing)
    failed = failed_projector.rebuild(
        _config(),
        owner_scope="user:alice",
        max_retries=1,
    )

    assert failed.status is EmbeddingProjectionStatus.FAILED
    assert failed.fallback_required is True
    assert failed.manifest is None
    assert failing.upsert_attempts == 2
    assert store.current_snapshot() == truth_snapshot

    unavailable_sink = FakeChromaGenerationSink()
    unavailable_sink.available = False
    unavailable_projector, _ = _projector(store, unavailable_sink)
    unavailable = unavailable_projector.rebuild(
        _config(),
        owner_scope="user:alice",
    )
    assert unavailable.status is EmbeddingProjectionStatus.UNAVAILABLE
    assert unavailable.error_code == "semantic_sink_unavailable"
    assert unavailable.fallback_required is True
    assert store.current_snapshot() == truth_snapshot


def test_fake_chroma_delete_and_rebuild_leave_usi_truth_intact(tmp_path):
    source = _source()
    version = _version(source)
    chunk = _chunk(version, "rebuildable content")
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, chunk, version, source)
    original = _stored(store, RecordKind.CHUNK, chunk.chunk_id)
    projector, sink = _projector(store)
    first = projector.rebuild(_config(), owner_scope="user:alice")
    assert first.manifest is not None

    sink.delete_generation(first.generation_ref)

    assert inspect_embedding_generation(
        first.manifest,
        sink,
    ) is EmbeddingGenerationHealth.MISSING
    assert _stored(store, RecordKind.CHUNK, chunk.chunk_id).record == chunk

    rebuilt = projector.rebuild(_config(), owner_scope="user:alice")
    assert rebuilt.status is EmbeddingProjectionStatus.READY
    assert rebuilt.generation_ref != first.generation_ref
    assert sink.generation_count(rebuilt.generation_ref) == 1
    assert _stored(store, RecordKind.CHUNK, chunk.chunk_id).revision == original.revision


def test_generation_cutover_makes_prior_manifest_drift_explicit(tmp_path):
    source = _source()
    version = _version(source)
    chunk = _chunk(version, "generation drift")
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, chunk, version, source)
    projector, sink = _projector(store)

    first = projector.rebuild(_config("v1"), owner_scope="user:alice")
    second = projector.rebuild(_config("v2"), owner_scope="user:alice")

    assert first.manifest is not None
    assert second.manifest is not None
    assert first.generation_ref != second.generation_ref
    assert inspect_embedding_generation(
        first.manifest,
        sink,
    ) is EmbeddingGenerationHealth.STALE_GENERATION
    assert inspect_embedding_generation(
        second.manifest,
        sink,
    ) is EmbeddingGenerationHealth.CURRENT


def test_reference_only_chunks_are_skipped_and_empty_projection_is_explicit(tmp_path):
    source = _source(policy=ContentPolicy.REFERENCE_ONLY)
    version = _version(source)
    chunk = _chunk(version, "not persisted inline")
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    truth = _insert(store, chunk, version, source)
    projector, sink = _projector(store)

    result = projector.rebuild(_config(), owner_scope="user:alice")

    assert result.status is EmbeddingProjectionStatus.EMPTY
    assert result.projected_count == 0
    assert result.skipped_count == 1
    assert result.manifest is None
    assert result.fallback_required is True
    assert sink.generation_count(result.generation_ref) is None
    assert store.current_snapshot() == truth


def test_max_chunk_budget_returns_honest_partial_generation(tmp_path):
    source = _source()
    version = _version(source)
    chunks = tuple(_chunk(version, f"bounded content {index}", index * 100) for index in range(3))
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, *chunks, version, source)
    projector, sink = _projector(store)

    result = projector.rebuild(
        _config(),
        owner_scope="user:alice",
        max_chunks=2,
        batch_size=2,
    )

    assert result.status is EmbeddingProjectionStatus.PARTIAL
    assert result.projected_count == 2
    assert result.manifest is not None
    assert len(result.manifest.input_evidence) == 2
    assert sink.generation_count(result.generation_ref) == 2

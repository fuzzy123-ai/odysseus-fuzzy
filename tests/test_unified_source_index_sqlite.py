from dataclasses import replace
import sqlite3

import pytest

from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    DerivedRunKind,
    DerivedRunRecord,
    EntityKind,
    EntityRecord,
    IndexJobKind,
    IndexJobRecord,
    LineageReason,
    LineageRecord,
    ProjectionKind,
    ProjectionManifest,
    RecordKind,
    RelationKind,
    RelationRecord,
    SourceKind,
    SourceRecord,
    SourceScope,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_sqlite import (
    SQLiteUnifiedSourceIndexError,
    SQLiteUnifiedSourceIndexStore,
)
from src.unified_source_index_stores import (
    ChunkStore,
    EmbeddingStore,
    GraphStore,
    JobStore,
    MemoryStore,
    QueryCacheStore,
    ReviewStore,
    SourceStore,
    StoreConflictError,
    StoreCursorError,
    StoreInterfaceKind,
    StoreTombstoneError,
    StoreTransactionClosedError,
    StoredRecord,
    TombstoneRecord,
    TransactionalStore,
    UnifiedSourceIndexStoreError,
)


NOW = "2026-07-17T06:00:00Z"


def _source(
    canonical_ref: str = "repo:alpha/src/main.py",
    *,
    owner_scope: str = "user:alice",
) -> SourceRecord:
    return SourceRecord(
        owner_scope=owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref=canonical_ref,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="local-git",
        first_seen_at="2026-07-15T09:00:00Z",
        source_modified_at="2026-07-16T10:00:00Z",
        valid_from="2026-07-15T09:00:00Z",
    )


def _version(source: SourceRecord, revision: str = "git:abc123") -> SourceVersionRecord:
    return SourceVersionRecord.create(
        source,
        revision_ref=revision,
        content_hash=content_hash(revision),
        version_observed_at=NOW,
        indexed_at=NOW,
    )


def _chunk(
    version: SourceVersionRecord,
    content: str,
    *,
    start: int = 0,
) -> ChunkRecord:
    return ChunkRecord.create(
        version,
        locator=TextRangeLocator(start, start + len(content)),
        extractor_profile_ref="text-v1",
        content_hash=content_hash(content),
        content=content,
        indexed_at=NOW,
    )


def _insert(store: SQLiteUnifiedSourceIndexStore, *records):
    write = store.begin_write(store.current_snapshot())
    for record in records:
        write.put(record)
    return write.commit()


def _all_record_families():
    source = _source()
    first_version = _version(source, "git:first")
    second_version = _version(source, "git:second")
    first_chunk = _chunk(first_version, "first")
    second_chunk = _chunk(second_version, "second")
    entity = EntityRecord.create(
        second_version,
        entity_kind=EntityKind.SYMBOL,
        natural_key="module:function",
        locator=TextRangeLocator(0, 6),
        extractor_profile_ref="symbols-v1",
        content_hash=content_hash("second"),
        label="function",
    )
    relation = RelationRecord.create(
        entity.ref(),
        second_chunk.ref(),
        relation_kind=RelationKind.DEFINES,
        method_ref="symbols-v1",
        confidence=1.0,
        evidence_refs=(entity.evidence_ref(), second_chunk.evidence_ref()),
    )
    lineage = LineageRecord.create(
        first_chunk.evidence_ref(),
        second_chunk.evidence_ref(),
        reason=LineageReason.EDITED,
        method_ref="git-diff-v1",
        confidence=0.9,
    )
    projection = ProjectionManifest.create(
        projection_kind=ProjectionKind.EMBEDDING,
        projection_profile_ref="semantic-v1",
        input_snapshot_ref="snapshot:one",
        config_hash="sha256:" + "c" * 64,
        input_evidence=(second_chunk.evidence_ref(),),
        implementation_ref="fake-vector",
        implementation_version="1",
        output_generation_ref="generation:one",
        indexed_at=NOW,
    )
    scope = SourceScope.create((second_chunk.policy_evidence_ref(),))
    derived = DerivedRunRecord.create(
        derived_kind=DerivedRunKind.SUMMARY,
        source_scope=scope,
        input_snapshot_ref="snapshot:one",
        algorithm_ref="summary-v1",
        algorithm_version="1",
        config_hash="sha256:" + "d" * 64,
        input_evidence=(second_chunk.evidence_ref(),),
        quality_evidence_refs=("quality:counts",),
        rebuild_evidence_ref="rebuild:summary-v1",
        max_nodes=10,
        max_depth=2,
        started_at=NOW,
        completed_at=NOW,
    )
    job = IndexJobRecord.create(
        job_kind=IndexJobKind.EXTRACTION,
        source_scope=scope,
        request_ref="request:one",
        profile_ref="extractor:text-v1",
        max_items=20,
        time_budget_ms=1_000,
    )
    return (
        source,
        first_version,
        second_version,
        first_chunk,
        second_chunk,
        entity,
        relation,
        lineage,
        projection,
        derived,
        job,
    )


def test_sqlite_backend_satisfies_interfaces_and_round_trips_every_record_family(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    records = _all_record_families()

    # Deliberately reverse dependency order; commit orders parents before children.
    snapshot = _insert(store, *reversed(records))

    assert snapshot.revision == 1
    assert snapshot.record_count == len(records)
    assert isinstance(store, TransactionalStore)
    for protocol in (
        MemoryStore,
        SourceStore,
        ChunkStore,
        EmbeddingStore,
        GraphStore,
        JobStore,
        ReviewStore,
        QueryCacheStore,
    ):
        assert isinstance(store, protocol)
    assert store.store_interfaces == frozenset(StoreInterfaceKind)

    with store.begin_read(snapshot) as read:
        for record in records:
            kind, id_field = {
                SourceRecord: (RecordKind.SOURCE, "source_id"),
                SourceVersionRecord: (
                    RecordKind.SOURCE_VERSION,
                    "source_version_id",
                ),
                ChunkRecord: (RecordKind.CHUNK, "chunk_id"),
                EntityRecord: (RecordKind.ENTITY, "entity_id"),
                RelationRecord: (RecordKind.RELATION, "relation_id"),
                LineageRecord: (RecordKind.LINEAGE, "lineage_id"),
                ProjectionManifest: (RecordKind.PROJECTION, "projection_id"),
                DerivedRunRecord: (RecordKind.DERIVED_RUN, "derived_run_id"),
                IndexJobRecord: (RecordKind.JOB, "job_id"),
            }[type(record)]
            record_id = getattr(record, id_field)
            item = read.require(kind, record_id, owner_scope=record.owner_scope)
            assert isinstance(item, StoredRecord)
            assert item.record == record
            assert item.revision == 1


def test_commit_survives_reopen_and_retains_historical_snapshots(tmp_path):
    path = tmp_path / "usi.db"
    store = SQLiteUnifiedSourceIndexStore(path)
    initial = store.current_snapshot()
    source = _source()
    committed = _insert(store, source)

    reopened = SQLiteUnifiedSourceIndexStore(path)

    assert reopened.current_snapshot() == committed
    with reopened.begin_read(initial) as historical:
        assert historical.get(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        ) is None
    with reopened.begin_read(committed) as current:
        item = current.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
        assert isinstance(item, StoredRecord)
        assert item.record == source


def test_rollback_and_foreign_key_failures_leave_no_partial_commit(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    source = _source()
    version = _version(source)

    with store.begin_write(store.current_snapshot()) as rolled_back:
        rolled_back.put(source)
    assert store.current_snapshot().revision == 0
    with pytest.raises(StoreTransactionClosedError):
        rolled_back.put(source)

    orphan = store.begin_write(store.current_snapshot())
    orphan.put(version)
    with pytest.raises(SQLiteUnifiedSourceIndexError) as failed:
        orphan.commit()
    assert isinstance(failed.value.__cause__, sqlite3.IntegrityError)
    assert orphan.closed is True
    assert store.current_snapshot().revision == 0
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE_VERSION,
            version.source_version_id,
            owner_scope=version.owner_scope,
        ) is None

    _insert(store, version, source)
    parent_delete = store.begin_write(store.current_snapshot())
    parent_delete.tombstone(
        RecordKind.SOURCE,
        source.source_id,
        owner_scope=source.owner_scope,
        expected_record_revision=1,
        reason="source_deleted",
    )
    with pytest.raises(SQLiteUnifiedSourceIndexError) as restricted:
        parent_delete.commit()
    assert isinstance(restricted.value.__cause__, sqlite3.IntegrityError)
    assert store.current_snapshot().record_count == 2
    assert store.current_snapshot().tombstone_count == 0


def test_compare_and_swap_tombstone_restore_and_duplicate_staging(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    source = _source()
    first = _insert(store, source)
    updated = replace(source, source_modified_at="2026-07-17T07:00:00Z")

    wrong = store.begin_write(first)
    with pytest.raises(StoreConflictError) as record_conflict:
        wrong.compare_and_swap(updated, expected_record_revision=0)
    assert record_conflict.value.actual_revision == 1
    wrong.rollback()

    update = store.begin_write(first)
    update.compare_and_swap(updated, expected_record_revision=1)
    second = update.commit()
    with pytest.raises(StoreConflictError, match="stale"):
        store.begin_write(first)

    duplicate = store.begin_write(second)
    duplicate.put(updated)
    with pytest.raises(UnifiedSourceIndexStoreError, match="only once"):
        duplicate.put(updated)
    duplicate.rollback()

    deletion = store.begin_write(second)
    deletion.tombstone(
        RecordKind.SOURCE,
        source.source_id,
        owner_scope=source.owner_scope,
        expected_record_revision=2,
        reason="source_deleted",
    )
    deleted = deletion.commit()
    with store.begin_read(deleted) as read:
        marker = read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
            include_tombstone=True,
        )
        assert isinstance(marker, TombstoneRecord)
        assert marker.previous_revision == 2
        assert marker.revision == 3

    blind = store.begin_write(deleted)
    with pytest.raises(StoreTombstoneError, match="use restore"):
        blind.put(updated)
    blind.rollback()

    restore = store.begin_write(deleted)
    restore.restore(updated, expected_tombstone_revision=3)
    restored = restore.commit()
    with store.begin_read(restored) as read:
        item = read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
        assert isinstance(item, StoredRecord)
        assert item.revision == 4


def test_owner_scoped_cursor_pages_remain_bound_to_their_snapshot(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    alice = [_source(f"repo:alpha/{name}.py") for name in ("a", "b", "c")]
    bob = _source("repo:beta/private.py", owner_scope="user:bob")
    snapshot = _insert(store, *alice, bob)
    read = store.begin_read(snapshot)

    first = read.list_records(
        RecordKind.SOURCE,
        owner_scope="user:alice",
        limit=2,
    )
    second = read.list_records(
        RecordKind.SOURCE,
        owner_scope="user:alice",
        limit=2,
        cursor=first.next_cursor,
    )
    assert len(first.items) == 2
    assert len(second.items) == 1
    assert [item.record_id for item in (*first.items, *second.items)] == sorted(
        source.source_id for source in alice
    )
    assert bob.source_id not in {item.record_id for item in first.items}

    _insert(store, _source("repo:alpha/d.py"))
    assert read.list_records(
        RecordKind.SOURCE,
        owner_scope="user:alice",
        limit=2,
        cursor=first.next_cursor,
    ) == second
    read.close()

    with store.begin_read() as current:
        with pytest.raises(StoreCursorError, match="another snapshot"):
            current.list_records(
                RecordKind.SOURCE,
                owner_scope="user:alice",
                limit=2,
                cursor=first.next_cursor,
            )


def test_fts_triggers_cover_insert_update_delete_owner_scope_and_rebuild(tmp_path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    alice_source = _source()
    alice_version = _version(alice_source)
    alice_chunk = _chunk(alice_version, "Odyssey semantic voyage")
    bob_source = _source("repo:beta/private.py", owner_scope="user:bob")
    bob_version = _version(bob_source)
    bob_chunk = _chunk(bob_version, "Odyssey private voyage")
    _insert(
        store,
        alice_chunk,
        alice_version,
        alice_source,
        bob_chunk,
        bob_version,
        bob_source,
    )

    alice_hits = store.search_chunks(owner_scope="user:alice", query="Odyssey")
    assert [hit.item.record.chunk_id for hit in alice_hits] == [alice_chunk.chunk_id]
    assert all(hit.item.owner_scope == "user:alice" for hit in alice_hits)

    replacement_text = "Ithaca graph destination"
    replacement = replace(
        alice_chunk,
        content=replacement_text,
        content_hash=content_hash(replacement_text),
    )
    write = store.begin_write(store.current_snapshot())
    write.compare_and_swap(replacement, expected_record_revision=1)
    updated = write.commit()
    assert store.search_chunks(owner_scope="user:alice", query="Odyssey") == ()
    assert store.search_chunks(owner_scope="user:alice", query="Ithaca")[0].item.record == replacement

    store.rebuild_chunk_fts()
    assert store.search_chunks(owner_scope="user:alice", query="Ithaca")

    deletion = store.begin_write(updated)
    deletion.tombstone(
        RecordKind.CHUNK,
        replacement.chunk_id,
        owner_scope=replacement.owner_scope,
        expected_record_revision=2,
        reason="chunk_deleted",
    )
    deletion.commit()
    assert store.search_chunks(owner_scope="user:alice", query="Ithaca") == ()
    assert store.search_chunks(owner_scope="user:bob", query="Odyssey")


def test_empty_commit_invalid_fts_and_memory_database_fail_closed(tmp_path):
    with pytest.raises(SQLiteUnifiedSourceIndexError, match="filesystem-backed"):
        SQLiteUnifiedSourceIndexStore(":memory:")

    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    empty = store.begin_write(store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="empty"):
        empty.commit()
    empty.rollback()

    with pytest.raises(SQLiteUnifiedSourceIndexError, match="non-empty"):
        store.search_chunks(owner_scope="user:alice", query="")
    with pytest.raises(SQLiteUnifiedSourceIndexError, match="FTS query failed"):
        store.search_chunks(owner_scope="user:alice", query='"unterminated')

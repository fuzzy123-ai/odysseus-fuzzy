from dataclasses import replace

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
from src.unified_source_index_stores import (
    ChunkStore,
    EmbeddingStore,
    GraphStore,
    InMemoryUnifiedSourceIndexStore,
    JobStore,
    MemoryStore,
    QueryCacheStore,
    ReviewStore,
    SourceStore,
    StoreConflictError,
    StoreCursorError,
    StoreInterfaceKind,
    StoreNotFoundError,
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
    modified_at: str = "2026-07-16T10:00:00Z",
) -> SourceRecord:
    return SourceRecord(
        owner_scope=owner_scope,
        source_kind=SourceKind.CODE,
        canonical_ref=canonical_ref,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="local-git",
        source_modified_at=modified_at,
        first_seen_at="2026-07-15T09:00:00Z",
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


def _insert(store: InMemoryUnifiedSourceIndexStore, *records) -> int:
    write = store.begin_write(store.current_snapshot())
    for record in records:
        write.put(record)
    return write.commit().revision


def _all_record_families():
    source = _source()
    first_version = _version(source, "git:first")
    second_version = _version(source, "git:second")
    first_chunk = ChunkRecord.create(
        first_version,
        locator=TextRangeLocator(0, 5),
        extractor_profile_ref="text-v1",
        content_hash=content_hash("first"),
        content="first",
        indexed_at=NOW,
    )
    second_chunk = ChunkRecord.create(
        second_version,
        locator=TextRangeLocator(0, 6),
        extractor_profile_ref="text-v1",
        content_hash=content_hash("second"),
        content="second",
        indexed_at=NOW,
    )
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


def test_in_memory_fake_satisfies_the_frozen_store_interface_vocabulary():
    store = InMemoryUnifiedSourceIndexStore()

    assert isinstance(store, TransactionalStore)
    assert isinstance(store, MemoryStore)
    assert isinstance(store, SourceStore)
    assert isinstance(store, ChunkStore)
    assert isinstance(store, EmbeddingStore)
    assert isinstance(store, GraphStore)
    assert isinstance(store, JobStore)
    assert isinstance(store, ReviewStore)
    assert isinstance(store, QueryCacheStore)
    assert store.store_interfaces == frozenset(StoreInterfaceKind)


def test_fake_round_trips_every_usi_01_record_family_without_backend_types():
    store = InMemoryUnifiedSourceIndexStore()
    records = _all_record_families()
    _insert(store, *records)

    with store.begin_read() as read:
        identities = {
            SourceRecord: (RecordKind.SOURCE, "source_id"),
            SourceVersionRecord: (RecordKind.SOURCE_VERSION, "source_version_id"),
            ChunkRecord: (RecordKind.CHUNK, "chunk_id"),
            EntityRecord: (RecordKind.ENTITY, "entity_id"),
            RelationRecord: (RecordKind.RELATION, "relation_id"),
            LineageRecord: (RecordKind.LINEAGE, "lineage_id"),
            ProjectionManifest: (RecordKind.PROJECTION, "projection_id"),
            DerivedRunRecord: (RecordKind.DERIVED_RUN, "derived_run_id"),
            IndexJobRecord: (RecordKind.JOB, "job_id"),
        }
        for record in records:
            kind, id_field = identities[type(record)]
            item = read.require(
                kind,
                getattr(record, id_field),
                owner_scope=record.owner_scope,
            )
            assert isinstance(item, StoredRecord)
            assert item.record is record
            assert item.revision == 1


def test_atomic_write_and_snapshot_isolation_cover_multiple_record_families():
    store = InMemoryUnifiedSourceIndexStore()
    initial = store.current_snapshot()
    source = _source()
    version = _version(source)

    write = store.begin_write(initial)
    write.put(source)
    write.put(version)

    with store.begin_read(initial) as before_commit:
        assert before_commit.get(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        ) is None

    committed = write.commit()
    assert committed.revision == 1
    assert committed.record_count == 2
    assert committed.tombstone_count == 0
    assert committed.snapshot_ref.startswith("usi_snapshot_")

    with store.begin_read(committed) as current:
        stored_source = current.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
        stored_version = current.require(
            RecordKind.SOURCE_VERSION,
            version.source_version_id,
            owner_scope=source.owner_scope,
        )
        assert isinstance(stored_source, StoredRecord)
        assert stored_source.record == source
        assert stored_source.revision == 1
        assert isinstance(stored_version, StoredRecord)
        assert stored_version.record == version
        assert stored_version.revision == 1

    with store.begin_read(initial) as historical:
        assert historical.get(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        ) is None


def test_bounded_cursor_pages_are_owner_scoped_and_snapshot_stable():
    store = InMemoryUnifiedSourceIndexStore()
    alice = [_source(f"repo:alpha/src/{name}.py") for name in ("a", "b", "c")]
    bob = _source("repo:beta/src/private.py", owner_scope="user:bob")
    _insert(store, *alice, bob)
    snapshot = store.current_snapshot()
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

    ids = [item.record_id for item in (*first.items, *second.items)]
    assert len(first.items) == 2
    assert first.clipped is True
    assert len(second.items) == 1
    assert second.clipped is False
    assert ids == sorted(source.source_id for source in alice)
    assert bob.source_id not in ids

    _insert(store, _source("repo:alpha/src/d.py"))

    # The open read transaction can finish its immutable page sequence.
    stable_second = read.list_records(
        RecordKind.SOURCE,
        owner_scope="user:alice",
        limit=2,
        cursor=first.next_cursor,
    )
    assert stable_second == second

    # A current-state read must not reuse a cursor from another snapshot.
    with store.begin_read() as current:
        with pytest.raises(StoreCursorError, match="another snapshot"):
            current.list_records(
                RecordKind.SOURCE,
                owner_scope="user:alice",
                limit=2,
                cursor=first.next_cursor,
            )

    with pytest.raises(UnifiedSourceIndexStoreError, match="between 1"):
        read.list_records(
            RecordKind.SOURCE,
            owner_scope="user:alice",
            limit=0,
        )
    with pytest.raises(UnifiedSourceIndexStoreError, match="owner_scope"):
        read.list_records(RecordKind.SOURCE, owner_scope="*", limit=10)
    read.close()


def test_cursor_tampering_and_query_reuse_fail_closed():
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, _source("repo:alpha/a.py"), _source("repo:alpha/b.py"))

    with store.begin_read() as read:
        page = read.list_records(
            RecordKind.SOURCE,
            owner_scope="user:alice",
            limit=1,
        )
        assert page.next_cursor
        replacement = "A" if page.next_cursor[-1] != "A" else "B"
        tampered = page.next_cursor[:-1] + replacement
        with pytest.raises(StoreCursorError):
            read.list_records(
                RecordKind.SOURCE,
                owner_scope="user:alice",
                limit=1,
                cursor=tampered,
            )
        with pytest.raises(StoreCursorError, match="another snapshot or query"):
            read.list_records(
                RecordKind.SOURCE,
                owner_scope="user:bob",
                limit=1,
                cursor=page.next_cursor,
            )


def test_snapshot_and_record_compare_and_swap_conflicts_are_atomic():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    base = store.current_snapshot()
    first = store.begin_write(base)
    second = store.begin_write(base)
    first_update = replace(source, source_modified_at="2026-07-17T07:00:00Z")
    second_update = replace(source, source_modified_at="2026-07-17T08:00:00Z")

    first.compare_and_swap(first_update, expected_record_revision=1)
    second.compare_and_swap(second_update, expected_record_revision=1)
    accepted = first.commit()

    with pytest.raises(StoreConflictError) as stale:
        second.commit()
    assert stale.value.expected_revision == base.revision
    assert stale.value.actual_revision == accepted.revision
    assert second.closed is True

    with store.begin_read() as read:
        stored = read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
        assert isinstance(stored, StoredRecord)
        assert stored.record == first_update
        assert stored.revision == 2

    wrong_record_cas = store.begin_write(store.current_snapshot())
    with pytest.raises(StoreConflictError) as wrong:
        wrong_record_cas.compare_and_swap(
            second_update,
            expected_record_revision=1,
        )
    assert wrong.value.expected_revision == 1
    assert wrong.value.actual_revision == 2
    wrong_record_cas.rollback()


def test_tombstones_hide_records_reserve_identity_and_require_explicit_restore():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)

    wrong_owner = store.begin_write(store.current_snapshot())
    with pytest.raises(StoreNotFoundError):
        wrong_owner.tombstone(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope="user:bob",
            expected_record_revision=1,
            reason="source_deleted",
        )
    wrong_owner.rollback()

    deletion = store.begin_write(store.current_snapshot())
    deletion.tombstone(
        RecordKind.SOURCE,
        source.source_id,
        owner_scope=source.owner_scope,
        expected_record_revision=1,
        reason="source_deleted",
    )
    deleted = deletion.commit()
    assert deleted.record_count == 0
    assert deleted.tombstone_count == 1

    with store.begin_read(deleted) as read:
        assert read.get(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        ) is None
        marker = read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
            include_tombstone=True,
        )
        assert isinstance(marker, TombstoneRecord)
        assert marker.previous_revision == 1
        assert marker.revision == 2
        assert marker.reason == "source_deleted"
        page = read.list_records(
            RecordKind.SOURCE,
            owner_scope=source.owner_scope,
            limit=10,
            include_tombstones=True,
        )
        assert page.items == (marker,)

    blind_recreate = store.begin_write(store.current_snapshot())
    with pytest.raises(StoreTombstoneError, match="use restore"):
        blind_recreate.put(source)
    blind_recreate.rollback()

    stale_restore = store.begin_write(store.current_snapshot())
    with pytest.raises(StoreConflictError):
        stale_restore.restore(source, expected_tombstone_revision=1)
    stale_restore.rollback()

    restore = store.begin_write(store.current_snapshot())
    restore.restore(source, expected_tombstone_revision=2)
    restored = restore.commit()
    assert restored.record_count == 1
    assert restored.tombstone_count == 0

    with store.begin_read(restored) as read:
        item = read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
            include_tombstone=True,
        )
        assert isinstance(item, StoredRecord)
        assert item.revision == 3


def test_transactions_are_explicit_and_closed_instances_cannot_be_reused():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()

    with store.begin_write(store.current_snapshot()) as write:
        write.put(source)
        # Exiting a write context without commit is a rollback, never an
        # implicit mutation.

    assert store.current_snapshot().revision == 0
    with pytest.raises(StoreTransactionClosedError):
        write.put(source)

    committed_write = store.begin_write(store.current_snapshot())
    committed_write.put(source)
    committed_write.commit()
    with pytest.raises(StoreTransactionClosedError):
        committed_write.commit()

    read = store.begin_read()
    read.close()
    with pytest.raises(StoreTransactionClosedError):
        read.get(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )


def test_duplicate_staging_empty_commit_and_contentful_reasons_are_rejected():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    empty = store.begin_write(store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="empty"):
        empty.commit()
    empty.rollback()

    duplicate = store.begin_write(store.current_snapshot())
    duplicate.put(source)
    with pytest.raises(UnifiedSourceIndexStoreError, match="only once"):
        duplicate.put(source)
    duplicate.rollback()

    _insert(store, source)
    deletion = store.begin_write(store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="content-free token"):
        deletion.tombstone(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
            expected_record_revision=1,
            reason="contains private title",
        )
    deletion.rollback()

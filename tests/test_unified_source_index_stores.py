from dataclasses import replace
from threading import Barrier, Event, Thread
from types import MappingProxyType

import pytest

import src.unified_source_index_stores as stores_module
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
            assert item.record == record
            assert item.record is not record
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


def test_paged_bulk_staging_rejects_duplicate_stale_and_hostile_batches_atomically():
    store = InMemoryUnifiedSourceIndexStore()
    first = _source("repo:alpha/src/bulk-first.py")
    second = _source("repo:alpha/src/bulk-second.py")

    duplicate = store.begin_write(store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="only once"):
        duplicate.put_many_paged_truth(((first, 0), (first, 0)))
    assert duplicate._mutations == {}
    duplicate.rollback()

    stale = store.begin_write(store.current_snapshot())
    with pytest.raises(StoreConflictError, match="stale"):
        stale.put_many_paged_truth(((first, 0), (second, 1)))
    assert stale._mutations == {}
    stale.rollback()

    hostile = store.begin_write(store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="exact supported"):
        hostile.put_many_paged_truth(((first, 0), (object(), 0)))
    assert hostile._mutations == {}
    hostile.rollback()
    assert store.current_snapshot().record_count == 0


def test_paged_bulk_staging_fences_reentrancy_and_rolls_back_without_partial_state(
    monkeypatch,
):
    import src.unified_source_index_stores as stores

    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    first = _source("repo:alpha/src/reentrant-first.py")
    nested = _source("repo:alpha/src/reentrant-nested.py")
    original_capture = stores._capture_store_record

    def reenter(record):
        if record is first:
            with pytest.raises(UnifiedSourceIndexStoreError, match="reentrant"):
                write.put(nested)
        return original_capture(record)

    monkeypatch.setattr(stores, "_capture_store_record", reenter)
    with pytest.raises(UnifiedSourceIndexStoreError, match="changed during"):
        write.put_many_paged_truth(((first, 0),))
    assert write._mutations == {}
    write.rollback()
    assert store.current_snapshot().record_count == 0


def test_paged_bulk_staging_keeps_ordinary_put_forge_guard_and_rollback_atomic():
    store = InMemoryUnifiedSourceIndexStore()
    forged_source = replace(
        _source("forge-code-source:sha256:" + "a" * 64),
        provider_ref="forge.code",
    )
    rejected = store.begin_write(store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="put_code_occurrence"):
        rejected.put_many_paged_truth(((forged_source, 0),))
    assert rejected._mutations == {}
    rejected.rollback()

    accepted = store.begin_write(store.current_snapshot())
    accepted.put_many_paged_truth(
        (
            (_source("repo:alpha/src/bulk-rollback-one.py"), 0),
            (_source("repo:alpha/src/bulk-rollback-two.py"), 0),
        )
    )
    assert len(accepted._mutations) == 2
    accepted.rollback()
    assert store.current_snapshot().record_count == 0


def test_paged_bulk_staging_detaches_mutable_records_before_publication():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source("repo:alpha/src/bulk-detached.py")
    original_json = source.to_json()
    write = store.begin_write(store.current_snapshot())
    write.put_many_paged_truth(((source, 0),))
    object.__setattr__(source, "canonical_ref", "repo:alpha/src/changed-after-stage.py")

    committed = write.commit()

    with store.begin_read(committed) as read:
        stored = read.require(
            RecordKind.SOURCE,
            SourceRecord.from_json(original_json).source_id,
            owner_scope=source.owner_scope,
        )
    assert stored.record.to_json() == original_json


def test_write_boundary_detaches_put_cas_restore_and_nested_record_values():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    original_source_json = source.to_json()
    put = store.begin_write(store.current_snapshot())
    put.put(source)
    object.__setattr__(source, "provider_ref", "forge.code")
    object.__setattr__(
        source,
        "canonical_ref",
        "forge-code-source:sha256:" + "a" * 64,
    )
    inserted = put.commit()
    with store.begin_read(inserted) as read:
        stored = read.require(
            RecordKind.SOURCE,
            SourceRecord.from_json(original_source_json).source_id,
            owner_scope="user:alice",
        )
        assert isinstance(stored, StoredRecord)
        assert stored.record.to_json() == original_source_json
        assert stored.record.provider_ref == "local-git"

    captured_source = SourceRecord.from_json(original_source_json)
    update = replace(
        captured_source,
        source_modified_at="2026-07-17T09:00:00Z",
    )
    expected_update_json = update.to_json()
    cas = store.begin_write(inserted)
    cas.compare_and_swap(update, expected_record_revision=1)
    object.__setattr__(update, "provider_ref", "forge.code")
    updated = cas.commit()
    with store.begin_read(updated) as read:
        stored = read.require(
            RecordKind.SOURCE,
            captured_source.source_id,
            owner_scope=captured_source.owner_scope,
        )
        assert isinstance(stored, StoredRecord)
        assert stored.record.to_json() == expected_update_json

    deletion = store.begin_write(updated)
    deletion.tombstone(
        RecordKind.SOURCE,
        captured_source.source_id,
        owner_scope=captured_source.owner_scope,
        expected_record_revision=2,
        reason="source_deleted",
    )
    deleted = deletion.commit()
    restored_record = SourceRecord.from_json(expected_update_json)
    restore = store.begin_write(deleted)
    restore.restore(restored_record, expected_tombstone_revision=3)
    object.__setattr__(restored_record, "canonical_ref", "mutated-after-restore")
    restored = restore.commit()
    with store.begin_read(restored) as read:
        stored = read.require(
            RecordKind.SOURCE,
            captured_source.source_id,
            owner_scope=captured_source.owner_scope,
        )
        assert isinstance(stored, StoredRecord)
        assert stored.record.to_json() == expected_update_json

    version = _version(SourceRecord.from_json(expected_update_json), "git:nested")
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(0, 6),
        extractor_profile_ref="text-v1",
        content_hash=content_hash("nested"),
        content="nested",
    )
    expected_chunk_json = chunk.to_json()
    nested = store.begin_write(restored)
    nested.put(version)
    nested.put(chunk)
    object.__setattr__(chunk.locator, "end_char", 99)
    nested_snapshot = nested.commit()
    with store.begin_read(nested_snapshot) as read:
        stored = read.require(
            RecordKind.CHUNK,
            ChunkRecord.from_json(expected_chunk_json).chunk_id,
            owner_scope=chunk.owner_scope,
        )
        assert isinstance(stored, StoredRecord)
        assert stored.record.to_json() == expected_chunk_json
        assert stored.record.locator is not chunk.locator


def test_hostile_record_subclasses_are_rejected_before_serializer_dispatch():
    class HostileSource(SourceRecord):
        def to_json(self):
            raise AssertionError("hostile serializer must not be invoked")

    source = HostileSource(
        owner_scope="user:alice",
        source_kind=SourceKind.CODE,
        canonical_ref="repo:hostile/source.py",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="local-git",
    )
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="exact supported"):
        write.put(source)
    write.rollback()


def test_scalar_subclasses_are_rejected_before_any_overloaded_operation():
    calls = []

    class EvilStr(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            calls.append("eq")
            raise AssertionError("hostile equality must not be invoked")

        def __len__(self):
            calls.append("len")
            raise AssertionError("hostile length must not be invoked")

        def encode(self, *args, **kwargs):
            calls.append("encode")
            raise AssertionError("hostile encoder must not be invoked")

        def startswith(self, *args, **kwargs):
            calls.append("startswith")
            raise AssertionError("hostile prefix check must not be invoked")

        def to_dict(self):
            calls.append("to_dict")
            raise AssertionError("hostile nested serializer must not be invoked")

    class EvilInt(int):
        def __eq__(self, other):
            calls.append("int-eq")
            raise AssertionError("hostile integer equality must not be invoked")

    source = _source()
    object.__setattr__(source, "provider_ref", EvilStr("local-git"))
    version = _version(_source(), "git:evil-scalar")
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(0, 6),
        extractor_profile_ref="text-v1",
        content_hash=content_hash("scalar"),
    )
    object.__setattr__(chunk.locator, "end_char", EvilInt(6))
    store = InMemoryUnifiedSourceIndexStore()
    write = store.begin_write(store.current_snapshot())
    with pytest.raises(UnifiedSourceIndexStoreError, match="overloaded"):
        write.put(source)
    assert calls == []
    write.rollback()

    write = store.begin_write(store.current_snapshot())
    write.put(version)
    with pytest.raises(UnifiedSourceIndexStoreError, match="overloaded"):
        write.put(chunk)
    assert calls == []
    write.rollback()


def test_private_staged_record_mutation_fails_closed_without_publication():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    key, mutation = next(iter(write._mutations.items()))
    changed = _source("repo:alpha/src/private-tamper.py")
    write._mutations[key] = replace(
        mutation,
        canonical_bytes=changed.to_json().encode("utf-8"),
    )
    with pytest.raises(UnifiedSourceIndexStoreError, match="changed"):
        write.commit()
    assert write.closed is True
    assert store.current_snapshot().revision == 0


def test_current_integrity_decodes_populated_records_at_most_twice(monkeypatch):
    store = InMemoryUnifiedSourceIndexStore()
    sources = tuple(
        _source(f"repo:alpha/src/current-capture-{index}.py")
        for index in range(12)
    )
    write = store.begin_write(store.current_snapshot())
    for source in sources:
        write.put(source)
    write.commit()

    original_decode = stores_module._record_from_canonical_bytes
    decoded = []

    def count_decode(canonical_bytes):
        decoded.append(canonical_bytes)
        return original_decode(canonical_bytes)

    monkeypatch.setattr(stores_module, "_record_from_canonical_bytes", count_decode)
    snapshot = store.current_snapshot()

    assert snapshot.record_count == len(sources)
    assert len(decoded) <= 2 * len(sources)


def test_implicit_begin_read_reuses_current_validation_with_exact_decode_count(
    monkeypatch,
):
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    expected = store.current_snapshot()
    make_state_calls = []
    decoded = []
    original_make_state = InMemoryUnifiedSourceIndexStore._make_state
    original_decode = stores_module._record_from_canonical_bytes

    def count_make_state(*args, **kwargs):
        make_state_calls.append(True)
        return original_make_state(*args, **kwargs)

    def count_decode(canonical_bytes):
        decoded.append(canonical_bytes)
        return original_decode(canonical_bytes)

    monkeypatch.setattr(
        InMemoryUnifiedSourceIndexStore,
        "_make_state",
        staticmethod(count_make_state),
    )
    monkeypatch.setattr(stores_module, "_record_from_canonical_bytes", count_decode)
    read = store.begin_read()
    read.close()
    assert len(make_state_calls) == 2
    assert len(decoded) == 2

    with store.begin_read() as read:
        assert read.snapshot == expected
        assert read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        ).record.to_json() == source.to_json()

    make_state_calls.clear()
    decoded.clear()
    with store.begin_read(expected) as read:
        assert read.snapshot == expected
    assert len(make_state_calls) == 2
    assert len(decoded) == 2


def test_implicit_begin_read_rejects_reentrant_copy_mutation_after_full_proof():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    key = (RecordKind.SOURCE, source.source_id)
    original_assert_current = store._assert_current_state_integrity

    def proof_then_mutate():
        original_assert_current()
        entry = store._records[key]
        forged = replace(source, source_modified_at="2026-07-17T10:00:00Z")
        store._records[key] = replace(
            entry,
            canonical_bytes=forged.to_json().encode("utf-8"),
        )

    store._assert_current_state_integrity = proof_then_mutate
    with pytest.raises(UnifiedSourceIndexStoreError, match="changed during read capture"):
        store.begin_read()


def test_implicit_begin_read_rejects_mutation_during_final_snapshot_comparison(monkeypatch):
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    key = (RecordKind.SOURCE, source.source_id)
    original_live_binding = stores_module._live_store_content_binding
    original_snapshot_values = stores_module._snapshot_values
    live_binding_calls = 0
    armed = False
    mutated = False

    def count_live_binding(*args, **kwargs):
        nonlocal live_binding_calls, armed
        live_binding_calls += 1
        result = original_live_binding(*args, **kwargs)
        if live_binding_calls == 3:
            armed = True
        return result

    def compare_then_mutate(snapshot):
        nonlocal mutated
        result = original_snapshot_values(snapshot)
        if armed and not mutated:
            mutated = True
            entry = store._records[key]
            forged = replace(source, source_modified_at="2026-07-17T10:00:00Z")
            store._records[key] = replace(
                entry,
                canonical_bytes=forged.to_json().encode("utf-8"),
            )
        return result

    monkeypatch.setattr(
        stores_module,
        "_live_store_content_binding",
        count_live_binding,
    )
    monkeypatch.setattr(stores_module, "_snapshot_values", compare_then_mutate)
    with pytest.raises(UnifiedSourceIndexStoreError, match="changed during read capture"):
        store.begin_read()
    assert live_binding_calls == 4
    assert mutated is True


@pytest.mark.parametrize("tamper", ("snapshot", "owner", "kind", "id"))
def test_current_integrity_rejects_make_state_metadata_tampering(tamper, monkeypatch):
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    original_make_state = InMemoryUnifiedSourceIndexStore._make_state

    def tampering_make_state(
        records,
        tombstones,
        forge_occurrences,
        revision,
        *,
        decoded_records=None,
    ):
        state = original_make_state(
            records,
            tombstones,
            forge_occurrences,
            revision,
            decoded_records=decoded_records,
        )
        if records is not store._records:
            return state
        if tamper == "snapshot":
            return replace(
                state,
                snapshot=replace(
                    state.snapshot,
                    record_count=state.snapshot.record_count + 1,
                    snapshot_ref="",
                ),
            )
        key = (RecordKind.SOURCE, source.source_id)
        entry = state.records[key]
        if tamper == "owner":
            changed_entry = replace(entry, owner_scope="user:bob")
        elif tamper == "kind":
            changed_entry = replace(entry, record_kind=RecordKind.CHUNK)
        else:
            changed_entry = replace(entry, record_id="repo:alpha/src/forged.py")
        changed_records = dict(state.records)
        changed_records[key] = changed_entry
        return replace(state, records=MappingProxyType(changed_records))

    monkeypatch.setattr(
        InMemoryUnifiedSourceIndexStore,
        "_make_state",
        staticmethod(tampering_make_state),
    )
    with pytest.raises(UnifiedSourceIndexStoreError, match="integrity"):
        store.current_snapshot()


def test_current_integrity_projects_make_state_errors_to_stable_capture_error(
    monkeypatch,
):
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    original_make_state = InMemoryUnifiedSourceIndexStore._make_state

    def failing_live_make_state(
        records,
        tombstones,
        forge_occurrences,
        revision,
        *,
        decoded_records=None,
    ):
        if records is store._records:
            raise UnifiedSourceIndexStoreError("lower-level make-state detail")
        return original_make_state(
            records,
            tombstones,
            forge_occurrences,
            revision,
            decoded_records=decoded_records,
        )

    monkeypatch.setattr(
        InMemoryUnifiedSourceIndexStore,
        "_make_state",
        staticmethod(failing_live_make_state),
    )
    with pytest.raises(UnifiedSourceIndexStoreError) as exc_info:
        store.current_snapshot()

    assert str(exc_info.value) == "current store state failed integrity validation"


def test_current_integrity_preserves_live_forge_semantic_error(monkeypatch):
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, _source())
    original_validate = stores_module._validate_forge_occurrence_state
    validation_calls = 0
    semantic_error = "fixed Forge semantic diagnostic"

    def fail_current_forge_validation(
        records,
        forge_occurrences,
        *,
        decoded_records=None,
    ):
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            return original_validate(
                records,
                forge_occurrences,
                decoded_records=decoded_records,
            )
        raise UnifiedSourceIndexStoreError(semantic_error)

    monkeypatch.setattr(
        stores_module,
        "_validate_forge_occurrence_state",
        fail_current_forge_validation,
    )
    with pytest.raises(UnifiedSourceIndexStoreError) as exc_info:
        store.current_snapshot()

    assert str(exc_info.value) == semantic_error
    assert validation_calls == 2


@pytest.mark.parametrize(
    "tamper",
    ("live_entry", "authority_snapshot", "authority_entry", "revision"),
)
def test_current_integrity_rejects_authority_or_live_mutation_during_forge_validation(
    tamper, monkeypatch
):
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    key = (RecordKind.SOURCE, source.source_id)
    original_validate = stores_module._validate_forge_occurrence_state
    validation_calls = 0

    def mutate_live_mapping_during_validation(
        records,
        forge_occurrences,
        *,
        decoded_records=None,
    ):
        nonlocal validation_calls
        validation_calls += 1
        original_validate(
            records,
            forge_occurrences,
            decoded_records=decoded_records,
        )
        if validation_calls == 2:
            if tamper == "live_entry":
                entry = store._records[key]
                store._records[key] = replace(entry, owner_scope="user:bob")
            elif tamper == "authority_snapshot":
                authority = store._history[store._revision]
                snapshot = authority.snapshot
                object.__setattr__(
                    authority,
                    "snapshot",
                    stores_module.StoreSnapshot(
                        snapshot.revision,
                        snapshot.state_hash,
                        snapshot.record_count + 1,
                        snapshot.tombstone_count,
                    ),
                )
            elif tamper == "authority_entry":
                authority = store._history[store._revision]
                object.__setattr__(
                    authority.records[key], "owner_scope", "user:bob"
                )
            else:
                store._revision = True

    monkeypatch.setattr(
        stores_module,
        "_validate_forge_occurrence_state",
        mutate_live_mapping_during_validation,
    )
    with pytest.raises(UnifiedSourceIndexStoreError, match="integrity"):
        store.current_snapshot()
    assert validation_calls == 2


def test_retained_authority_and_staged_mutations_hold_bytes_not_domain_records():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    mutation = next(iter(write._mutations.values()))
    assert not hasattr(mutation, "record")
    assert type(mutation.canonical_bytes) is bytes
    accepted = write.commit()
    entry = store._history[accepted.revision].records[
        (RecordKind.SOURCE, source.source_id)
    ]
    assert not hasattr(entry, "record")
    assert type(entry.canonical_bytes) is bytes
    with store.begin_read(accepted) as read:
        returned = read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
        assert isinstance(returned, StoredRecord)
        assert returned.record == source


def test_read_results_and_snapshot_values_are_defensive_copies():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    expected_source_json = source.to_json()
    expected_source_id = source.source_id
    _insert(store, source)
    object.__setattr__(source, "provider_ref", "forge.code")
    accepted = store.current_snapshot()
    retained = store.current_snapshot()
    with store.begin_read(retained) as read:
        first = read.require(
            RecordKind.SOURCE,
            expected_source_id,
            owner_scope="user:alice",
        )
        page = read.list_records(
            RecordKind.SOURCE,
            owner_scope="user:alice",
            limit=10,
        )
        assert isinstance(first, StoredRecord)
        object.__setattr__(first.record, "provider_ref", "forge.code")
        object.__setattr__(page.items[0].record, "canonical_ref", "mutated-page")
        object.__setattr__(read.snapshot, "revision", 99)
    object.__setattr__(accepted, "revision", 99)

    current = store.current_snapshot()
    assert current.revision == 1
    with store.begin_read(retained) as historical:
        stored = historical.require(
            RecordKind.SOURCE,
            expected_source_id,
            owner_scope="user:alice",
        )
        assert isinstance(stored, StoredRecord)
        assert stored.record.to_json() == expected_source_json
        assert stored.record.provider_ref == "local-git"


def test_cursor_and_boolean_subclasses_fail_before_overloaded_operations():
    calls = []

    class EvilCursor(str):
        def __len__(self):
            calls.append("len")
            raise AssertionError("cursor length must not run")

        def __add__(self, other):
            calls.append("add")
            raise AssertionError("cursor addition must not run")

        def encode(self, *args, **kwargs):
            calls.append("encode")
            raise AssertionError("cursor encoding must not run")

    class EvilTruth:
        def __bool__(self):
            calls.append("bool")
            raise AssertionError("truth conversion must not run")

    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, _source())
    with store.begin_read() as read:
        with pytest.raises(StoreCursorError, match="invalid"):
            read.list_records(
                RecordKind.SOURCE,
                owner_scope="user:alice",
                limit=1,
                cursor=EvilCursor("abc"),
            )
        with pytest.raises(UnifiedSourceIndexStoreError, match="exact boolean"):
            read.get(
                RecordKind.SOURCE,
                _source().source_id,
                owner_scope="user:alice",
                include_tombstone=EvilTruth(),
            )
        with pytest.raises(UnifiedSourceIndexStoreError, match="exact boolean"):
            read.list_records(
                RecordKind.SOURCE,
                owner_scope="user:alice",
                limit=1,
                include_tombstones=EvilTruth(),
            )
    assert calls == []


@pytest.mark.parametrize("tamper", ("foreign_identity", "same_identity_content"))
def test_retained_read_revalidates_key_identity_and_snapshot_commitment(tamper):
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    accepted = store.current_snapshot()
    read = store.begin_read(accepted)
    key = (RecordKind.SOURCE, source.source_id)
    entry = read._state.records[key]
    if tamper == "foreign_identity":
        changed = _source("repo:alpha/src/replaced.py")
        replacement = replace(
            entry,
            record_id=changed.source_id,
            canonical_bytes=changed.to_json().encode("utf-8"),
        )
    else:
        changed = replace(source, source_modified_at="2026-07-17T10:00:00Z")
        replacement = replace(
            entry,
            canonical_bytes=changed.to_json().encode("utf-8"),
        )
    forged = dict(read._state.records)
    forged[key] = replacement
    object.__setattr__(read._state, "records", MappingProxyType(forged))
    with pytest.raises(UnifiedSourceIndexStoreError, match="integrity"):
        read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
    read.close()


@pytest.mark.parametrize("operation", ("snapshot", "get", "require", "list"))
def test_each_read_operation_uses_one_detached_state_after_capture_swap(operation):
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    accepted = store.current_snapshot()
    read = store.begin_read(accepted)
    key = (RecordKind.SOURCE, source.source_id)
    entry = read._state.records[key]
    changed = replace(source, source_modified_at="2026-07-17T10:00:00Z")
    forged_records = dict(read._state.records)
    forged_records[key] = replace(
        entry,
        canonical_bytes=changed.to_json().encode("utf-8"),
    )
    forged_state = replace(
        read._state,
        records=MappingProxyType(forged_records),
    )
    original_capture = read._capture_operation_state
    captures = []

    def capture_then_swap():
        local_state = original_capture()
        captures.append(local_state.snapshot.snapshot_ref)
        read._state = forged_state
        return local_state

    read._capture_operation_state = capture_then_swap
    if operation == "snapshot":
        result = read.snapshot
        assert result == accepted
    elif operation == "get":
        result = read.get(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
        assert isinstance(result, StoredRecord)
        assert result.record.to_json() == source.to_json()
    elif operation == "require":
        result = read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
        assert isinstance(result, StoredRecord)
        assert result.record.to_json() == source.to_json()
    else:
        result = read.list_records(
            RecordKind.SOURCE,
            owner_scope=source.owner_scope,
            limit=10,
        )
        assert len(result.items) == 1
        assert result.items[0].record.to_json() == source.to_json()
    assert captures == [accepted.snapshot_ref]
    with pytest.raises(UnifiedSourceIndexStoreError, match="integrity"):
        read.get(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
        )
    read.close()
    read.close()


@pytest.mark.parametrize("tamper", ("cached_state", "cached_index"))
def test_retained_read_cache_alias_tampering_fails_closed(tamper):
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    read = store.begin_read()
    assert read.snapshot.record_count == 1
    cache = stores_module._RETAINED_READ_CACHES[id(read)][1]
    if tamper == "cached_state":
        entry = cache.detached_state.records[(RecordKind.SOURCE, source.source_id)]
        forged_records = dict(cache.detached_state.records)
        forged_records[(RecordKind.SOURCE, source.source_id)] = replace(
            entry,
            canonical_bytes=replace(
                source,
                source_modified_at="2026-07-17T10:00:00Z",
            ).to_json().encode("utf-8"),
        )
        object.__setattr__(
            cache,
            "detached_state",
            replace(cache.detached_state, records=MappingProxyType(forged_records)),
        )
    else:
        object.__setattr__(cache, "index", object())
    with pytest.raises(UnifiedSourceIndexStoreError, match="integrity"):
        read.list_records(
            RecordKind.SOURCE,
            owner_scope=source.owner_scope,
            limit=1,
        )
    read.close()
    read.close()


def test_retained_read_cache_first_capture_is_shared_across_threads(monkeypatch):
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    _insert(store, source)
    read = store.begin_read()
    expected_snapshot = store.current_snapshot()
    barrier = Barrier(2)
    original_make_state = InMemoryUnifiedSourceIndexStore._make_state

    def overlap_first_capture(
        records,
        tombstones,
        forge_occurrences,
        revision,
        *,
        decoded_records=None,
    ):
        if records is read._state.records:
            barrier.wait(timeout=5)
        return original_make_state(
            records,
            tombstones,
            forge_occurrences,
            revision,
            decoded_records=decoded_records,
        )

    monkeypatch.setattr(
        InMemoryUnifiedSourceIndexStore,
        "_make_state",
        staticmethod(overlap_first_capture),
    )
    results = []
    failures = []

    def capture_snapshot():
        try:
            results.append(read.snapshot)
        except BaseException:
            failures.append("failed")

    first = Thread(target=capture_snapshot)
    second = Thread(target=capture_snapshot)
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
    assert results == [expected_snapshot, expected_snapshot]
    read.close()
    read.close()


def test_retained_read_cache_decodes_once_for_bounded_multi_page_read(monkeypatch):
    store = InMemoryUnifiedSourceIndexStore()
    sources = tuple(
        _source(f"repo:alpha/src/retained-page-{index:03d}.py")
        for index in range(128)
    )
    write = store.begin_write(store.current_snapshot())
    for source in sources:
        write.put(source)
    write.commit()

    read = store.begin_read()
    original_decode = stores_module._record_from_canonical_bytes
    decoded = []

    def count_decode(canonical_bytes):
        decoded.append(canonical_bytes)
        return original_decode(canonical_bytes)

    monkeypatch.setattr(stores_module, "_record_from_canonical_bytes", count_decode)
    records = []
    cursor = ""
    while True:
        page = read.list_records(
            RecordKind.SOURCE,
            owner_scope="user:alice",
            limit=17,
            cursor=cursor,
        )
        records.extend(item.record for item in page.items)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    assert tuple(records) == tuple(sorted(sources, key=lambda item: item.source_id))
    assert len(decoded) <= 3 * len(sources)
    read.close()
    read.close()


def test_commit_phase_fences_concurrent_public_mutation():
    store = InMemoryUnifiedSourceIndexStore()
    source = _source()
    other = _source("repo:alpha/src/concurrent.py")
    write = store.begin_write(store.current_snapshot())
    write.put(source)
    entered = Event()
    release = Event()
    original_commit = store._commit

    def delayed_commit(transaction):
        entered.set()
        assert release.wait(timeout=5)
        return original_commit(transaction)

    store._commit = delayed_commit
    commit_results = []
    mutation_results = []

    def do_commit():
        commit_results.append(write.commit())

    def do_mutation():
        try:
            write.put(other)
        except StoreTransactionClosedError:
            mutation_results.append("closed")

    commit_thread = Thread(target=do_commit)
    mutation_thread = Thread(target=do_mutation)
    commit_thread.start()
    assert entered.wait(timeout=5)
    mutation_thread.start()
    assert mutation_results == []
    release.set()
    commit_thread.join(timeout=5)
    mutation_thread.join(timeout=5)
    assert not commit_thread.is_alive()
    assert not mutation_thread.is_alive()
    assert len(commit_results) == 1
    assert mutation_results == ["closed"]
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            other.source_id,
            owner_scope=other.owner_scope,
        ) is None


def _ulo02_commands(records):
    from src.unified_source_index_tombstone_policy import _new_instruction

    return tuple(
        _new_instruction(record.ref(), 1, "source_deleted", record.owner_scope)
        for record in records
    )


def test_ulo02_protocol_fake_subclass_proxy_and_shadow_methods_are_rejected_before_access():
    from src.unified_source_index_stores import StoreSnapshot, commit_tombstone_batch

    class Fake:
        def __getattribute__(self, name):
            raise AssertionError("protocol fake was accessed")

    class Child(InMemoryUnifiedSourceIndexStore):
        def current_snapshot(self):
            raise AssertionError("subclass method was accessed")

    class Proxy:
        def __getattribute__(self, name):
            raise AssertionError("proxy was accessed")

    def reject(candidate):
        with pytest.raises(UnifiedSourceIndexStoreError):
            commit_tombstone_batch(
                candidate,
                authority_ref="usi_authority_" + "a" * 64,
                plan_digest="sha256:" + "b" * 64,
                base_snapshot=StoreSnapshot(0, "sha256:" + "0" * 64, 0, 0),
                commands=(),
            )

    for candidate in (Fake(), Child(), Proxy()):
        reject(candidate)

    shadowed = InMemoryUnifiedSourceIndexStore()
    shadowed.current_snapshot = lambda: (_ for _ in ()).throw(AssertionError("shadow was accessed"))
    reject(shadowed)


def test_ulo02_exact_store_gate_rejects_malformed_and_mutated_instances():
    from src.unified_source_index_stores import StoreSnapshot, commit_tombstone_batch

    malformed = object.__new__(InMemoryUnifiedSourceIndexStore)
    with pytest.raises(UnifiedSourceIndexStoreError):
        commit_tombstone_batch(
            malformed,
            authority_ref="usi_authority_" + "a" * 64,
            plan_digest="sha256:" + "b" * 64,
            base_snapshot=StoreSnapshot(0, "sha256:" + "0" * 64, 0, 0),
            commands=(),
        )
    store = InMemoryUnifiedSourceIndexStore()
    store._revision = True
    with pytest.raises(UnifiedSourceIndexStoreError):
        store.current_snapshot()
    store = InMemoryUnifiedSourceIndexStore()
    store._history = []
    with pytest.raises(UnifiedSourceIndexStoreError):
        commit_tombstone_batch(
            store,
            authority_ref="usi_authority_" + "a" * 64,
            plan_digest="sha256:" + "b" * 64,
            base_snapshot=StoreSnapshot(0, "sha256:" + "0" * 64, 0, 0),
            commands=(),
        )
    store = InMemoryUnifiedSourceIndexStore()

    class LockTrap:
        def __enter__(self):
            raise AssertionError("untrusted lock was entered")

        def __exit__(self, *args):
            raise AssertionError("untrusted lock was exited")

    store._lock = LockTrap()
    with pytest.raises(UnifiedSourceIndexStoreError):
        commit_tombstone_batch(
            store,
            authority_ref="usi_authority_" + "a" * 64,
            plan_digest="sha256:" + "b" * 64,
            base_snapshot=StoreSnapshot(0, "sha256:" + "0" * 64, 0, 0),
            commands=(),
        )


def test_ulo02_store_batch_receipt_is_issued_only_after_exact_historical_readback():
    from src.unified_source_index_stores import (
        TombstoneCommitReceipt,
        VerifiedTombstoneCommit,
        commit_tombstone_batch,
        verify_tombstone_commit,
    )

    source = _source()
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, source)
    base = store.current_snapshot()
    commands = _ulo02_commands((source,))
    receipt = commit_tombstone_batch(
        store,
        authority_ref="usi_authority_" + "a" * 64,
        plan_digest="sha256:" + "b" * 64,
        base_snapshot=base,
        commands=commands,
    )
    assert type(receipt) is TombstoneCommitReceipt
    verified = verify_tombstone_commit(
        store,
        receipt,
        expected_authority_ref=receipt.authority_ref,
        expected_plan_digest=receipt.plan_digest,
        expected_commands=commands,
    )
    assert type(verified) is VerifiedTombstoneCommit
    assert receipt.committed_snapshot.revision == base.revision + 1
    with store.begin_read(receipt.committed_snapshot) as read:
        marker = read.require(
            RecordKind.SOURCE,
            source.source_id,
            owner_scope=source.owner_scope,
            include_tombstone=True,
        )
    assert marker.reason.startswith("ulo2:source_deleted:")


def test_ulo02_store_receipt_rejects_fabricated_spliced_partial_foreign_and_wrong_instance_history():
    from dataclasses import replace
    from src.unified_source_index_stores import commit_tombstone_batch, verify_tombstone_commit

    source = _source()
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, source)
    base = store.current_snapshot()
    commands = _ulo02_commands((source,))
    receipt = commit_tombstone_batch(
        store,
        authority_ref="usi_authority_" + "a" * 64,
        plan_digest="sha256:" + "b" * 64,
        base_snapshot=base,
        commands=commands,
    )
    with pytest.raises(UnifiedSourceIndexStoreError):
        verify_tombstone_commit(
            InMemoryUnifiedSourceIndexStore(),
            receipt,
            expected_authority_ref=receipt.authority_ref,
            expected_plan_digest=receipt.plan_digest,
            expected_commands=commands,
        )
    with pytest.raises(UnifiedSourceIndexStoreError):
        verify_tombstone_commit(
            store,
            replace(receipt, plan_digest="sha256:" + "c" * 64),
            expected_authority_ref=receipt.authority_ref,
            expected_plan_digest=receipt.plan_digest,
            expected_commands=commands,
        )
    object.__setattr__(receipt, "receipt_digest", "sha256:" + "0" * 64)
    with pytest.raises(UnifiedSourceIndexStoreError):
        verify_tombstone_commit(
            store,
            receipt,
            expected_authority_ref=receipt.authority_ref,
            expected_plan_digest=receipt.plan_digest,
            expected_commands=commands,
        )


def test_ulo02_store_batch_is_atomic_child_first_and_request_bound():
    from src.unified_source_index_stores import commit_tombstone_batch

    source, version, _, chunk, *_ = _all_record_families()
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, source, version, chunk)
    base = store.current_snapshot()
    receipt = commit_tombstone_batch(
        store,
        authority_ref="usi_authority_" + "a" * 64,
        plan_digest="sha256:" + "b" * 64,
        base_snapshot=base,
        commands=_ulo02_commands((chunk, version, source)),
    )
    assert receipt.command_count == 3
    assert receipt.committed_snapshot.record_count == 0
    assert receipt.committed_snapshot.tombstone_count == 3


def test_ulo02_captured_write_failure_rolls_back_staged_tombstone(monkeypatch):
    from src.unified_source_index_stores import InMemoryStoreWriteTransaction, commit_tombstone_batch

    source = _source()
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, source)
    base = store.current_snapshot()
    original = InMemoryStoreWriteTransaction.tombstone

    def fail_after_stage(transaction, *args, **kwargs):
        original(transaction, *args, **kwargs)
        raise UnifiedSourceIndexStoreError("fixed failure")

    monkeypatch.setattr(InMemoryStoreWriteTransaction, "tombstone", fail_after_stage)
    with pytest.raises(UnifiedSourceIndexStoreError):
        commit_tombstone_batch(
            store, authority_ref="usi_authority_" + "a" * 64,
            plan_digest="sha256:" + "b" * 64, base_snapshot=base,
            commands=_ulo02_commands((source,)),
        )
    assert store.current_snapshot() == base


def test_ulo02_store_receipt_reopens_on_exact_sqlite_without_schema_migration(tmp_path):
    from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
    from src.unified_source_index_stores import commit_tombstone_batch, verify_tombstone_commit
    from src.unified_source_index_tombstone_policy import _new_instruction

    source = _source()
    path = tmp_path / "ulo02.db"
    store = SQLiteUnifiedSourceIndexStore(path)
    _insert(store, source)
    base = store.current_snapshot()
    commands = (
        _new_instruction(source.ref(), 1, "source_deleted", source.owner_scope),
    )
    receipt = commit_tombstone_batch(
        store,
        authority_ref="usi_authority_" + "a" * 64,
        plan_digest="sha256:" + "b" * 64,
        base_snapshot=base,
        commands=commands,
    )
    reopened = SQLiteUnifiedSourceIndexStore(path)
    verified = verify_tombstone_commit(
        reopened,
        receipt.to_json(),
        expected_authority_ref=receipt.authority_ref,
        expected_plan_digest=receipt.plan_digest,
        expected_commands=commands,
    )
    assert verified.receipt.committed_snapshot == receipt.committed_snapshot


def test_ulo02_receipt_verification_rejects_current_snapshot_drift_before_foreign_call(monkeypatch):
    from src.unified_source_index_stores import InMemoryUnifiedSourceIndexStore, commit_tombstone_batch, verify_tombstone_commit

    source = _source()
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, source)
    base = store.current_snapshot()
    commands = _ulo02_commands((source,))
    receipt = commit_tombstone_batch(store, authority_ref="usi_authority_" + "a" * 64,
                                     plan_digest="sha256:" + "b" * 64,
                                     base_snapshot=base, commands=commands)
    calls = 0

    def trap(self):
        nonlocal calls
        calls += 1
        raise AssertionError("drifted snapshot method called")

    monkeypatch.setattr(InMemoryUnifiedSourceIndexStore, "current_snapshot", trap)
    with pytest.raises(UnifiedSourceIndexStoreError):
        verify_tombstone_commit(store, receipt, expected_authority_ref=receipt.authority_ref,
                                expected_plan_digest=receipt.plan_digest, expected_commands=commands)
    assert calls == 0


def test_ulo02_command_projection_rejects_hostile_ref_before_store_capture():
    from src.unified_source_index_tombstone_policy import _new_instruction
    from src.unified_source_index_stores import StoreSnapshot, commit_tombstone_batch

    source = _source()
    command = _new_instruction(source.ref(), 1, "source_deleted", source.owner_scope)

    class Trap:
        def __hash__(self):
            raise AssertionError("hash invoked")

        def __eq__(self, other):
            raise AssertionError("equality invoked")

        def __getattribute__(self, name):
            if name.startswith("__"):
                return object.__getattribute__(self, name)
            raise AssertionError("getter invoked")

    object.__setattr__(command.record_ref, "record_kind", Trap())

    candidate = InMemoryUnifiedSourceIndexStore()
    calls = 0

    def store_trap():
        nonlocal calls
        calls += 1
        raise AssertionError("store inspected before command projection")

    candidate.current_snapshot = store_trap
    with pytest.raises(UnifiedSourceIndexStoreError):
        commit_tombstone_batch(candidate, authority_ref="usi_authority_" + "a" * 64,
                               plan_digest="sha256:" + "b" * 64,
                               base_snapshot=StoreSnapshot(0, "sha256:" + "0" * 64, 0, 0),
                               commands=(command,))
    assert calls == 0

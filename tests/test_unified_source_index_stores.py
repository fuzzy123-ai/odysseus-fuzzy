from dataclasses import replace
from threading import Event, Thread
from types import MappingProxyType

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

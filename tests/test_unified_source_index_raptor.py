import pytest

from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    DerivedRunKind,
    RecordKind,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_raptor import (
    RaptorArtifactKind,
    RaptorArtifactRef,
    RaptorInvalidationStatus,
    RaptorMaintenanceReceipt,
    RaptorMaintenanceResult,
    RaptorMaintenanceStatus,
    RaptorRunConfig,
    UnifiedSourceIndexRaptorAdapter,
    UnifiedSourceIndexRaptorError,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_stores import (
    InMemoryUnifiedSourceIndexStore,
    StoredRecord,
    TombstoneRecord,
)


STARTED = "2026-07-17T08:00:00Z"
COMPLETED = "2026-07-17T08:01:00Z"


def _records(canonical_ref="doc:alpha", text="odyssey alpha", *, owner="user:alice"):
    source = SourceRecord(
        owner_scope=owner,
        source_kind=SourceKind.DOCUMENT,
        canonical_ref=canonical_ref,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="fake.docs",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref="rev:1",
        content_hash=content_hash(text),
        version_observed_at=STARTED,
        indexed_at=STARTED,
    )
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(0, len(text)),
        extractor_profile_ref="text-v1",
        content_hash=content_hash(text),
        content=text,
        indexed_at=STARTED,
    )
    return source, version, chunk


def _insert(store, *records):
    write = store.begin_write(store.current_snapshot())
    for record in records:
        write.put(record)
    return write.commit()


def _config(**changes):
    values = dict(
        algorithm_ref="raptor.fake",
        algorithm_version="v1",
        embedding_snapshot_ref="embedding:fixture",
        quality_evidence_refs=("quality.input",),
        rebuild_evidence_ref="rebuild.fixture",
        max_inputs=16,
        max_nodes=100,
        max_depth=4,
        task_timeout_ms=5_000,
    )
    values.update(changes)
    return RaptorRunConfig(**values)


def _prepare(store, chunk, *, snapshot=None, config=None):
    snapshot = snapshot or store.current_snapshot()
    return UnifiedSourceIndexRaptorAdapter(store).prepare(
        config or _config(),
        owner_scope=chunk.owner_scope,
        input_snapshot=snapshot,
        input_refs=(chunk.ref(),),
        started_at=STARTED,
    )


def _artifacts(plan, evidence=None):
    evidence = tuple(evidence or plan.run.input_evidence)
    cluster = RaptorArtifactRef.create(
        artifact_kind=RaptorArtifactKind.CLUSTER,
        derived_run_id=plan.run.derived_run_id,
        natural_key="cluster.root",
        input_evidence=evidence,
    )
    node = RaptorArtifactRef.create(
        artifact_kind=RaptorArtifactKind.NODE,
        derived_run_id=plan.run.derived_run_id,
        natural_key="node.leaf",
        input_evidence=evidence,
    )
    membership = RaptorArtifactRef.create(
        artifact_kind=RaptorArtifactKind.MEMBERSHIP,
        derived_run_id=plan.run.derived_run_id,
        natural_key="membership.root",
        input_evidence=evidence,
        parent_artifact_ids=(cluster.artifact_id, node.artifact_id),
    )
    summary = RaptorArtifactRef.create(
        artifact_kind=RaptorArtifactKind.SUMMARY,
        derived_run_id=plan.run.derived_run_id,
        natural_key="summary.root",
        input_evidence=evidence,
        parent_artifact_ids=(cluster.artifact_id,),
    )
    return cluster, node, membership, summary


def _result(plan, artifacts=None):
    return RaptorMaintenanceResult(
        task_id=plan.tasks[0].task_id,
        status=RaptorMaintenanceStatus.COMPLETED,
        artifacts=tuple(artifacts or _artifacts(plan)),
        quality_evidence_refs=("quality.worker",),
    )


def _stored(store, kind, record_id, *, include_tombstone=False):
    read = store.begin_read()
    try:
        return read.get(
            kind,
            record_id,
            owner_scope="user:alice",
            include_tombstone=include_tombstone,
        )
    finally:
        read.close()


def _non_derived_records(store):
    values = {}
    read = store.begin_read()
    try:
        for kind in (RecordKind.SOURCE, RecordKind.SOURCE_VERSION, RecordKind.CHUNK):
            page = read.list_records(kind, owner_scope="user:alice", limit=100)
            values[kind] = tuple(item.record for item in page.items if isinstance(item, StoredRecord))
    finally:
        read.close()
    return values


def test_prepare_uses_exact_input_fingerprint_and_never_places_raw_content_in_task():
    source, version, chunk = _records(text="NEVER_COPY_RAPTOR_SOURCE_TEXT")
    store = InMemoryUnifiedSourceIndexStore()
    snapshot = _insert(store, chunk, version, source)

    plan = _prepare(store, chunk, snapshot=snapshot)

    assert plan.run.derived_kind is DerivedRunKind.RAPTOR
    assert plan.run.input_evidence == (chunk.evidence_ref(),)
    assert plan.run.input_snapshot_ref.startswith("raptor-input:")
    assert plan.run.content_policy is ContentPolicy.METADATA_ONLY
    assert plan.run.completed_at == ""
    assert plan.tasks[0].store_snapshot == snapshot
    assert len(plan.tasks[0].input_evidence) == 1
    assert "NEVER_COPY_RAPTOR_SOURCE_TEXT" not in repr(plan.tasks[0])


def test_unrelated_store_change_does_not_change_input_identity_or_invalidate_run():
    source, version, chunk = _records()
    store = InMemoryUnifiedSourceIndexStore()
    first_snapshot = _insert(store, chunk, version, source)
    adapter = UnifiedSourceIndexRaptorAdapter(store)
    first = _prepare(store, chunk, snapshot=first_snapshot)
    other_source, other_version, other_chunk = _records("doc:other", "unrelated text")
    second_snapshot = _insert(store, other_chunk, other_version, other_source)

    second = _prepare(store, chunk, snapshot=second_snapshot)
    invalidation = adapter.assess_invalidation(
        first.run,
        current_snapshot=second_snapshot,
        current_input_refs=(chunk.ref(),),
    )

    assert first.run.input_snapshot_ref == second.run.input_snapshot_ref
    assert first.run.derived_run_id == second.run.derived_run_id
    assert first.tasks[0].store_snapshot != second.tasks[0].store_snapshot
    assert invalidation.status is RaptorInvalidationStatus.CURRENT
    assert invalidation.affected_input_count == 0
    assert invalidation.global_rebuild_required is False


def test_invalidation_names_only_changed_added_and_removed_inputs():
    source, version, first = _records(text="odyssey alpha")
    second = ChunkRecord.create(
        version,
        locator=TextRangeLocator(100, 114),
        extractor_profile_ref="text-v1",
        content_hash=content_hash("odyssey second"),
        content="odyssey second",
        indexed_at=STARTED,
    )
    store = InMemoryUnifiedSourceIndexStore()
    snapshot = _insert(store, second, first, version, source)
    adapter = UnifiedSourceIndexRaptorAdapter(store)
    plan = adapter.prepare(
        _config(),
        owner_scope="user:alice",
        input_snapshot=snapshot,
        input_refs=(first.ref(),),
        started_at=STARTED,
    )
    changed_first = ChunkRecord.create(
        version,
        locator=first.locator,
        extractor_profile_ref="text-v1",
        content_hash=content_hash("odyssey bravo"),
        content="odyssey bravo",
        indexed_at=STARTED,
    )
    stored_first = _stored(store, RecordKind.CHUNK, first.chunk_id)
    assert isinstance(stored_first, StoredRecord)
    write = store.begin_write(store.current_snapshot())
    write.put(changed_first, expected_record_revision=stored_first.revision)
    current = write.commit()

    changed_and_added = adapter.assess_invalidation(
        plan.run,
        current_snapshot=current,
        current_input_refs=(changed_first.ref(), second.ref()),
    )
    removed = adapter.assess_invalidation(
        plan.run,
        current_snapshot=current,
        current_input_refs=(),
    )

    assert changed_and_added.status is RaptorInvalidationStatus.INPUTS_CHANGED
    assert changed_and_added.changed == (changed_first.evidence_ref(),)
    assert changed_and_added.added == (second.evidence_ref(),)
    assert changed_and_added.removed == ()
    assert changed_and_added.affected_input_count == 2
    assert removed.removed == (first.evidence_ref(),)
    assert removed.global_rebuild_required is False


def test_submit_delegates_one_bounded_task_to_existing_worker_protocol():
    source, version, chunk = _records()
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, chunk, version, source)
    plan = _prepare(store, chunk)

    class ExistingWorker:
        worker_ref = "existing.maintenance.worker"

        def __init__(self):
            self.tasks = []

        def submit(self, task):
            self.tasks.append(task)
            return RaptorMaintenanceReceipt(
                task.task_id,
                "submission.fixture",
                self.worker_ref,
            )

    worker = ExistingWorker()
    receipts = UnifiedSourceIndexRaptorAdapter(store).submit(plan, worker)

    assert worker.tasks == [plan.tasks[0]]
    assert receipts[0].task_id == plan.tasks[0].task_id
    assert plan.tasks[0].timeout_ms == 5_000
    assert plan.tasks[0].max_nodes == 100


def test_completion_requires_all_artifact_kinds_and_persists_canonical_run():
    source, version, chunk = _records()
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, chunk, version, source)
    adapter = UnifiedSourceIndexRaptorAdapter(store)
    plan = _prepare(store, chunk)

    with pytest.raises(UnifiedSourceIndexRaptorError, match="required RAPTOR artifact kinds"):
        adapter.complete(
            plan,
            _result(plan, artifacts=_artifacts(plan)[:1]),
            completed_at=COMPLETED,
        )
    assert _stored(store, RecordKind.DERIVED_RUN, plan.run.derived_run_id) is None

    manifest = adapter.complete(plan, _result(plan), completed_at=COMPLETED)
    stored = _stored(store, RecordKind.DERIVED_RUN, plan.run.derived_run_id)

    assert isinstance(stored, StoredRecord)
    assert stored.record == manifest.run
    assert manifest.run.completed_at == COMPLETED
    assert set(manifest.run.quality_evidence_refs) == {"quality.input", "quality.worker"}
    assert {item.artifact_kind for item in manifest.artifacts} == set(RaptorArtifactKind)
    assert manifest.run.input_evidence == (chunk.evidence_ref(),)


def test_worker_artifact_cannot_escape_exact_run_evidence():
    source, version, chunk = _records()
    other_source, other_version, other_chunk = _records("doc:other", "other evidence")
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, other_chunk, other_version, other_source, chunk, version, source)
    plan = _prepare(store, chunk)
    escaped = _artifacts(plan, evidence=(other_chunk.evidence_ref(),))

    with pytest.raises(UnifiedSourceIndexRaptorError, match="escapes run inputs"):
        UnifiedSourceIndexRaptorAdapter(store).complete(
            plan,
            _result(plan, artifacts=escaped),
            completed_at=COMPLETED,
        )


def test_delete_and_rebuild_restores_same_run_without_changing_non_derived_truth():
    source, version, chunk = _records()
    store = InMemoryUnifiedSourceIndexStore()
    _insert(store, chunk, version, source)
    adapter = UnifiedSourceIndexRaptorAdapter(store)
    first_plan = _prepare(store, chunk)
    first_manifest = adapter.complete(first_plan, _result(first_plan), completed_at=COMPLETED)
    truth_before = _non_derived_records(store)
    stored = _stored(store, RecordKind.DERIVED_RUN, first_manifest.run.derived_run_id)
    assert isinstance(stored, StoredRecord)
    write = store.begin_write(store.current_snapshot())
    write.tombstone(
        RecordKind.DERIVED_RUN,
        first_manifest.run.derived_run_id,
        owner_scope="user:alice",
        expected_record_revision=stored.revision,
        reason="raptor_rebuild",
    )
    deleted_snapshot = write.commit()
    tombstone = _stored(
        store,
        RecordKind.DERIVED_RUN,
        first_manifest.run.derived_run_id,
        include_tombstone=True,
    )
    assert isinstance(tombstone, TombstoneRecord)

    rebuild_plan = _prepare(store, chunk, snapshot=deleted_snapshot)
    rebuilt = adapter.complete(rebuild_plan, _result(rebuild_plan), completed_at=COMPLETED)

    assert rebuild_plan.run.derived_run_id == first_manifest.run.derived_run_id
    assert rebuilt.run.input_evidence == first_manifest.run.input_evidence
    assert isinstance(_stored(store, RecordKind.DERIVED_RUN, rebuilt.run.derived_run_id), StoredRecord)
    assert _non_derived_records(store) == truth_before


def test_missing_cross_owner_and_unbounded_inputs_fail_closed():
    alice_source, alice_version, alice_chunk = _records()
    bob_source, bob_version, bob_chunk = _records("doc:bob", "bob", owner="user:bob")
    store = InMemoryUnifiedSourceIndexStore()
    snapshot = _insert(store, bob_chunk, bob_version, bob_source, alice_chunk, alice_version, alice_source)
    adapter = UnifiedSourceIndexRaptorAdapter(store)

    with pytest.raises(UnifiedSourceIndexRaptorError, match="missing"):
        adapter.prepare(
            _config(),
            owner_scope="user:alice",
            input_snapshot=snapshot,
            input_refs=(bob_chunk.ref(),),
            started_at=STARTED,
        )
    with pytest.raises(UnifiedSourceIndexRaptorError, match="non-empty and bounded"):
        adapter.prepare(
            _config(max_inputs=1),
            owner_scope="user:alice",
            input_snapshot=snapshot,
            input_refs=(alice_chunk.ref(), alice_version.ref()),
            started_at=STARTED,
        )
    with pytest.raises(UnifiedSourceIndexRaptorError, match="max_inputs"):
        _config(max_inputs=257)


def test_sqlite_store_roundtrip_persists_only_derived_run_manifest(tmp_path):
    source, version, chunk = _records()
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    _insert(store, chunk, version, source)
    adapter = UnifiedSourceIndexRaptorAdapter(store)
    plan = _prepare(store, chunk)

    manifest = adapter.complete(plan, _result(plan), completed_at=COMPLETED)
    stored = _stored(store, RecordKind.DERIVED_RUN, manifest.run.derived_run_id)

    assert isinstance(stored, StoredRecord)
    assert stored.record == manifest.run
    assert _non_derived_records(store) == {
        RecordKind.SOURCE: (source,),
        RecordKind.SOURCE_VERSION: (version,),
        RecordKind.CHUNK: (chunk,),
    }

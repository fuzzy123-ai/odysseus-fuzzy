import pytest

from src.unified_source_index_adapters import (
    AdapterCapability,
    AdapterScope,
    DeterministicFakeSourceAdapter,
    ExtractionProfile,
    FakeAdapterDocument,
)
from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    IndexJobKind,
    IndexJobRecord,
    JobStatus,
    ProjectionKind,
    ProjectionManifest,
    RecordKind,
    SourceKind,
    SourceScope,
)
from src.unified_source_index_jobs import (
    IndexJobExecutionError,
    IndexJobLeaseConflict,
    IndexJobTerminalError,
    JobStage,
    ProjectionRequest,
    ProjectionSink,
    UnifiedSourceIndexJobRuntime,
    plan_unavailable_tombstones,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_stores import StoredRecord


T0 = "2026-07-17T10:00:00Z"
T30 = "2026-07-17T10:00:30Z"
T61 = "2026-07-17T10:01:01Z"


def _adapter(*documents):
    capability = AdapterCapability(
        adapter_id="fake.docs",
        adapter_version="v1",
        owner_scope="user:alice",
        domain_kind="personal_docs",
        source_kind=SourceKind.DOCUMENT,
        content_policy=ContentPolicy.INLINE_LOCAL,
        classification_ceiling=Classification.SENSITIVE,
        supports_exact_reads=True,
        max_discovery_page=10,
        max_extract_items=10,
    )
    return DeterministicFakeSourceAdapter(
        capability,
        tuple(documents)
        or (FakeAdapterDocument("doc:alpha", "rev:1", "alpha content"),),
    )


def _sources(adapter):
    page = adapter.discover(
        AdapterScope("user:alice", Classification.SENSITIVE),
        cursor="",
        limit=10,
        time_budget_ms=100,
    )
    return tuple(item.source for item in page.items)


def _job(adapter, *, request_ref="request:one", max_items=10):
    scope = SourceScope.create(source.policy_evidence() for source in _sources(adapter))
    return IndexJobRecord.create(
        job_kind=IndexJobKind.EXTRACTION,
        source_scope=scope,
        request_ref=request_ref,
        profile_ref="text-v1",
        max_items=max_items,
        time_budget_ms=1_000,
    )


def _stored(store, kind, record_id, owner="user:alice"):
    with store.begin_read() as read:
        item = read.get(kind, record_id, owner_scope=owner)
    assert isinstance(item, StoredRecord)
    return item


class _FailOnceProjection:
    def __init__(self):
        self.calls = 0

    def project(self, request: ProjectionRequest) -> ProjectionManifest:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("synthetic provider payload must not enter checkpoint")
        return ProjectionManifest.create(
            projection_kind=ProjectionKind.EMBEDDING,
            projection_profile_ref="semantic-v1",
            input_snapshot_ref=request.truth_snapshot.snapshot_ref,
            config_hash="sha256:" + "c" * 64,
            input_evidence=tuple(
                chunk.evidence_ref() for chunk in request.extraction.chunks
            ),
            implementation_ref="fake-vector",
            implementation_version="1",
            output_generation_ref="generation:one",
        )


def test_runtime_processes_bounded_pages_and_completes_durably(tmp_path):
    adapter = _adapter(
        FakeAdapterDocument("doc:alpha", "rev:1", "alpha content"),
        FakeAdapterDocument("doc:beta", "rev:2", "beta content"),
    )
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter)
    runtime.register(job)

    first_lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.one",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )
    first = runtime.run_next(first_lease, adapter=adapter, now=T30)
    second = runtime.run_next(first.lease, adapter=adapter, now=T30)

    assert first.terminal is False
    assert first.lease.checkpoint.items_completed == 1
    assert first.lease.checkpoint.discovery_cursor
    assert second.terminal is True
    assert second.lease.job.status is JobStatus.COMPLETED
    assert second.lease.checkpoint.stage is JobStage.COMPLETE
    assert second.lease.checkpoint.items_completed == 2
    assert second.lease.checkpoint.lease_owner == ""
    assert second.lease.job.attempt_count == 1
    for source in _sources(adapter):
        assert _stored(store, RecordKind.SOURCE, source.source_id).record == source

    reopened = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    persisted_job = _stored(reopened, RecordKind.JOB, job.job_id)
    assert persisted_job.record.status is JobStatus.COMPLETED
    with pytest.raises(IndexJobTerminalError):
        runtime.acquire(
            job.job_id,
            owner_scope=job.owner_scope,
            worker_id="worker.two",
            adapter=adapter,
            now=T61,
        )


def test_abandoned_lease_blocks_then_expires_for_crash_recovery(tmp_path):
    adapter = _adapter()
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter)
    runtime.register(job)
    abandoned = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.crashed",
        adapter=adapter,
        now=T0,
        lease_seconds=60,
    )

    with pytest.raises(IndexJobLeaseConflict, match="live lease"):
        runtime.acquire(
            job.job_id,
            owner_scope=job.owner_scope,
            worker_id="worker.recovery",
            adapter=adapter,
            now=T30,
            lease_seconds=60,
        )

    recovered = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.recovery",
        adapter=adapter,
        now=T61,
        lease_seconds=60,
    )
    assert abandoned.job.attempt_count == 1
    assert recovered.job.attempt_count == 2
    assert recovered.checkpoint.lease_owner == "worker.recovery"


def test_total_job_item_budget_completes_with_explicit_clipped_checkpoint(tmp_path):
    adapter = _adapter(
        FakeAdapterDocument("doc:alpha", "rev:1", "alpha"),
        FakeAdapterDocument("doc:beta", "rev:2", "beta"),
    )
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:bounded", max_items=1)
    runtime.register(job)

    result = runtime.run_to_completion(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.one",
        adapter=adapter,
        now=T0,
        max_steps=2,
    )

    assert result.lease.job.status is JobStatus.COMPLETED
    assert result.lease.checkpoint.items_completed == 1
    assert result.lease.checkpoint.discovery_cursor
    assert result.lease.checkpoint.error_code == "item_budget_exhausted"
    with store.begin_read() as read:
        page = read.list_records(
            RecordKind.SOURCE,
            owner_scope="user:alice",
            limit=10,
        )
    assert len(page.items) == 1


def test_cancellation_invalidates_an_active_worker_lease(tmp_path):
    adapter = _adapter()
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.one",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    cancelled = runtime.cancel(
        job.job_id,
        owner_scope=job.owner_scope,
        now=T30,
    )

    assert cancelled.job.status is JobStatus.CANCELLED
    assert cancelled.checkpoint.stage is JobStage.CANCELLED
    assert cancelled.checkpoint.error_code == "cancelled_by_operator"
    with pytest.raises(IndexJobLeaseConflict, match="stale, cancelled"):
        runtime.run_next(lease, adapter=adapter, now=T30)


def test_projection_failure_keeps_truth_marks_stale_and_retries_idempotently(tmp_path):
    adapter = _adapter()
    source = _sources(adapter)[0]
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter)
    runtime.register(job)
    projection = _FailOnceProjection()
    assert isinstance(projection, ProjectionSink)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.one",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    with pytest.raises(IndexJobExecutionError, match="after truth commit") as failed:
        runtime.run_next(
            lease,
            adapter=adapter,
            projection=projection,
            now=T30,
        )

    assert failed.value.job.status is JobStatus.FAILED
    stored_source = _stored(store, RecordKind.SOURCE, source.source_id)
    first_truth_revision = stored_source.revision
    assert "synthetic provider payload" not in failed.value.job.cursor

    retry = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.retry",
        adapter=adapter,
        now=T61,
        lease_seconds=120,
    )
    assert retry.checkpoint.projection_stale is True
    assert retry.checkpoint.truth_snapshot_ref
    completed = runtime.run_next(
        retry,
        adapter=adapter,
        projection=projection,
        now=T61,
    )

    assert completed.lease.job.status is JobStatus.COMPLETED
    assert completed.lease.checkpoint.projection_stale is False
    assert completed.projection_manifest is not None
    assert projection.calls == 2
    assert _stored(store, RecordKind.SOURCE, source.source_id).revision == first_truth_revision


def test_unavailable_source_builds_bounded_content_free_dependency_plan(tmp_path):
    available = _adapter(
        FakeAdapterDocument("doc:alpha", "rev:1", "private source body"),
    )
    source = _sources(available)[0]
    version = available.observe_version(source.ref())
    extraction = available.extract(
        version,
        ExtractionProfile("text-v1", 10, 100, 100),
    )
    unavailable = _adapter(
        FakeAdapterDocument(
            "doc:alpha",
            "rev:1",
            "private source body",
            available=False,
        ),
    )
    observation = unavailable.observe_version(source.ref())

    store = SQLiteUnifiedSourceIndexStore(tmp_path / "usi.db")
    write = store.begin_write(store.current_snapshot())
    for record in (source, version, *extraction.records):
        write.put(record)
    write.commit()
    plan = plan_unavailable_tombstones(store, observation, limit=10)

    assert [item.record_ref.record_kind for item in plan.items] == [
        RecordKind.CHUNK,
        RecordKind.SOURCE_VERSION,
        RecordKind.SOURCE,
    ]
    assert plan.clipped is False
    assert all(item.reason == "source_deleted" for item in plan.items)
    assert "private source body" not in repr(plan)

    clipped = plan_unavailable_tombstones(store, observation, limit=2)
    assert len(clipped.items) == 2
    assert clipped.clipped is True

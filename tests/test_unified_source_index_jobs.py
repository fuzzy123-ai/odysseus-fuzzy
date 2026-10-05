from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import time

import pytest

from src.unified_source_index_adapters import (
    AdapterCapability,
    AdapterScope,
    DeterministicFakeSourceAdapter,
    DiscoveryItem,
    DiscoveryPage,
    ExactReadRequest,
    ExtractionProfile,
    ExtractionPageBudget,
    ExtractionResult,
    FakeAdapterDocument,
    PagedExtractionPage,
    PolicyReadContext,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    IndexJobKind,
    IndexJobRecord,
    JobStatus,
    ProjectionKind,
    ProjectionManifest,
    RecordKind,
    RecordRef,
    SourceKind,
    SourceScope,
    SourceVersionRecord,
    TextRangeLocator,
    canonical_json,
    content_hash,
)
from src.unified_source_index_jobs import (
    IndexJobExecutionError,
    IndexJobLeaseConflict,
    IndexJobTerminalError,
    JobCheckpoint,
    JobLease,
    JobStage,
    ProjectionRequest,
    ProjectionSink,
    UnifiedSourceIndexJobRuntime,
    IndexJobRuntimeError,
    plan_unavailable_tombstones,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_stores import (
    InMemoryUnifiedSourceIndexStore,
    StoredRecord,
)
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.harbour_knowledge_store import HarbourKnowledgeStore
from src.unified_source_index_sources.knowledge import (
    KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
    KnowledgeSourceAdapter,
    KnowledgeSourceAdapterError,
    UnavailableReason,
)


T0 = "2026-07-17T10:00:00Z"
T30 = "2026-07-17T10:00:30Z"
T61 = "2026-07-17T10:01:01Z"
HARBOUR_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
HARBOUR_OWNER = "owner_" + "a" * 32
HARBOUR_AUTH = "sha256:" + "a" * 64


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


def _job(
    adapter, *, request_ref="request:one", max_items=10, time_budget_ms=1_000
):
    scope = SourceScope.create(source.policy_evidence() for source in _sources(adapter))
    return IndexJobRecord.create(
        job_kind=IndexJobKind.EXTRACTION,
        source_scope=scope,
        request_ref=request_ref,
        profile_ref="text-v1",
        max_items=max_items,
        time_budget_ms=time_budget_ms,
    )


def _stored(store, kind, record_id, owner="user:alice"):
    with store.begin_read() as read:
        item = read.get(kind, record_id, owner_scope=owner)
    assert isinstance(item, StoredRecord)
    return item


def _records_digest(records):
    payload = b"\n".join(record.to_json().encode("utf-8") for record in records)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _record_key(record):
    if type(record) is ChunkRecord:
        return RecordKind.CHUNK.value, record.chunk_id
    if hasattr(record, "entity_id"):
        return RecordKind.ENTITY.value, record.entity_id
    return RecordKind.RELATION.value, record.relation_id


def _harbour_knowledge_adapter(tmp_path, payload: bytes, *, max_page_items=1):
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="usi-job-test",
        key_id="job-test-key",
        secret=b"k" * 32,
        clock=lambda: HARBOUR_NOW,
    )
    principal = issuer.issue(
        subject_id=HARBOUR_OWNER,
        owner_id=HARBOUR_OWNER,
        auth_context_digest=HARBOUR_AUTH,
        ttl_seconds=3_600,
    )
    harbour = HarbourKnowledgeStore(
        tmp_path / "harbour-knowledge.sqlite3",
        trusted_issuers=(issuer,),
        clock=lambda: HARBOUR_NOW,
    )
    harbour.create_object(
        principal,
        owner_id=HARBOUR_OWNER,
        knowledge_id="integration-secret-id",
        knowledge_kind="note",
        payload_bytes=payload,
        payload_schema_id="harbour.note.v1",
        payload_media_type="text/plain",
        classification="private",
        idempotency_key="create-integration-note",
    )
    adapter = KnowledgeSourceAdapter(
        store=harbour,
        principal=principal,
        owner_id=HARBOUR_OWNER,
        cursor_signing_key=b"k" * 32,
        classification_ceiling=Classification.SENSITIVE,
    )
    original_describe = adapter.describe_capability
    adapter.describe_capability = lambda: replace(
        original_describe(), max_extract_items=max_page_items
    )
    return harbour, principal, adapter


def _run_harbour_job(tmp_path, payload: bytes, *, max_page_items=1):
    harbour, principal, adapter = _harbour_knowledge_adapter(
        tmp_path, payload, max_page_items=max_page_items
    )
    page_profiles = []
    page_budgets = []
    original_budgeted_page = adapter.extract_page_budgeted

    def record_page_request(source_version, profile, budget, *, cursor, sequence_start):
        page_profiles.append(profile)
        page_budgets.append(budget)
        return original_budgeted_page(
            source_version,
            profile,
            budget,
            cursor=cursor,
            sequence_start=sequence_start,
        )

    adapter.extract_page_budgeted = record_page_request
    capability = adapter.describe_capability()
    discovery = adapter.discover(
        AdapterScope(capability.owner_scope, capability.classification_ceiling),
        cursor="",
        limit=1,
        time_budget_ms=5_000,
    )
    scope = SourceScope.create(
        item.source.policy_evidence() for item in discovery.items
    )
    job = IndexJobRecord.create(
        job_kind=IndexJobKind.EXTRACTION,
        source_scope=scope,
        request_ref="request:harbour-budgeted-job",
        profile_ref=KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
        max_items=1,
        time_budget_ms=120_000,
    )
    truth = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(truth)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.harbour-budgeted-job",
        adapter=adapter,
        now=T0,
        lease_seconds=3_600,
    )
    result = runtime.run_next(lease, adapter=adapter, now=T0)
    assert result.terminal
    with truth.begin_read() as read:
        chunks = read.list_records(
            RecordKind.CHUNK,
            owner_scope=capability.owner_scope,
            limit=100,
        ).items
        source = read.get(
            RecordKind.SOURCE,
            discovery.items[0].source.source_id,
            owner_scope=capability.owner_scope,
        )
    return harbour, principal, adapter, truth, result, tuple(
        item.record for item in chunks
    ), source, tuple(page_profiles), tuple(page_budgets)


class _PagedAdapter:
    def __init__(
        self,
        count=3,
        *,
        page_size=2,
        required_profile="text-v1",
        fault="",
        on_page=None,
    ):
        self.capability = AdapterCapability(
            adapter_id="fake.paged",
            adapter_version="v1",
            owner_scope="user:alice",
            domain_kind="personal_docs",
            source_kind=SourceKind.DOCUMENT,
            content_policy=ContentPolicy.INLINE_LOCAL,
            classification_ceiling=Classification.SENSITIVE,
            supports_exact_reads=True,
            max_discovery_page=10,
            max_extract_items=1_000,
            required_job_profile_ref=required_profile,
        )
        self._legacy = DeterministicFakeSourceAdapter(
            self.capability,
            (FakeAdapterDocument("doc:paged", "rev:1", "x"),),
        )
        scope = AdapterScope("user:alice", Classification.SENSITIVE)
        self.discovery = self._legacy.discover(
            scope, cursor="", limit=1, time_budget_ms=100
        ).items[0]
        self.version = self._legacy.observe_version(self.discovery.source.ref())
        assert type(self.version) is SourceVersionRecord
        self.drift_version = replace(
            self.version,
            source_version_id="",
            revision_ref="rev:drift",
        )
        records = tuple(
            ChunkRecord.create(
                self.version,
                locator=TextRangeLocator(index, index + 1),
                extractor_profile_ref="child.exact-v1",
                content_hash=content_hash("x"),
                content="x",
            )
            for index in range(count)
        )
        self.records = tuple(sorted(records, key=_record_key))
        self.page_size = page_size
        self.fault = fault
        self.on_page = on_page
        self.discover_calls = 0
        self.observe_calls = 0
        self.page_calls = 0
        self.legacy_extract_calls = 0
        self.profiles = []
        self.page_budgets = []

    def describe_capability(self):
        return self.capability

    def discover(self, scope, *, cursor, limit, time_budget_ms):
        self.discover_calls += 1
        return DiscoveryPage(()) if cursor else DiscoveryPage((self.discovery,))

    def observe_version(self, source_ref):
        self.observe_calls += 1
        assert source_ref == self.discovery.source.ref()
        return self.version

    def extract(self, source_version, profile):
        self.legacy_extract_calls += 1
        raise AssertionError("paged jobs must not call legacy extract")

    def extract_page(self, source_version, profile, *, cursor, sequence_start):
        self.page_calls += 1
        self.profiles.append(profile)
        if self.on_page is not None:
            self.on_page(self.page_calls)
        start = int(cursor) if cursor else 0
        end = min(start + self.page_size, len(self.records))
        records = self.records[start:end]
        next_cursor = str(end) if end < len(self.records) else ""
        final_count = None if next_cursor else len(self.records)
        final_digest = "" if next_cursor else _records_digest(self.records)
        if self.fault == "order" and len(records) > 1:
            records = tuple(reversed(records))
        elif self.fault == "duplicate" and records:
            records = (records[0], records[0])
        elif self.fault == "empty_advance":
            records, next_cursor, final_count, final_digest = (), "next", None, ""
        elif self.fault == "cursor_loop" and cursor:
            next_cursor = cursor
        elif self.fault == "final_count" and not next_cursor:
            final_count += 1
        elif self.fault == "final_digest" and not next_cursor:
            final_digest = "sha256:" + "f" * 64
        page = PagedExtractionPage(
            self.drift_version if self.fault == "version" else source_version,
            sequence_start + 1 if self.fault == "sequence" else sequence_start,
            records,
            next_cursor,
            final_count,
            final_digest,
        )
        if self.fault == "page_overflow":
            object.__setattr__(page, "records", (self.records[0],) * 101)
        return page

    def extract_page_budgeted(
        self, source_version, profile, budget, *, cursor, sequence_start
    ):
        assert type(budget) is ExtractionPageBudget
        assert budget.max_items <= profile.max_items
        assert budget.max_chars <= profile.max_chars
        assert budget.time_budget_ms <= profile.time_budget_ms
        self.page_budgets.append(budget)
        return self.extract_page(
            source_version,
            profile,
            cursor=cursor,
            sequence_start=sequence_start,
        )

    def read_exact(self, request, policy_context):
        raise AssertionError("outside paged ingestion test")

    def observe_unavailable(self, source_ref, reason):
        raise AssertionError("outside paged ingestion test")


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


@pytest.mark.parametrize(
    "fault",
    (
        "order", "duplicate", "empty_advance", "cursor_loop", "final_count",
        "final_digest", "page_overflow", "version", "sequence",
    ),
)
def test_paged_extraction_rejects_malformed_graph_before_truth_commit(tmp_path, fault):
    adapter = _PagedAdapter(count=3, page_size=2, fault=fault)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / f"{fault}.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref=f"request:{fault}", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.one",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T30)

    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None
    assert adapter.legacy_extract_calls == 0


@pytest.mark.parametrize("store_kind", ("memory", "sqlite"))
def test_paged_extraction_commits_exact_12296_record_graph_atomically(tmp_path, store_kind):
    adapter = _PagedAdapter(count=12_296, page_size=100)
    store = (
        InMemoryUnifiedSourceIndexStore()
        if store_kind == "memory"
        else SQLiteUnifiedSourceIndexStore(tmp_path / "full.db")
    )
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(
        adapter,
        request_ref=f"request:full-{store_kind}",
        max_items=1,
        time_budget_ms=300_000,
    )
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.full",
        adapter=adapter,
        now=T0,
        lease_seconds=600,
    )

    result = runtime.run_next(lease, adapter=adapter, now=T0)

    assert result.terminal is True
    assert result.lease.checkpoint.items_completed == 1
    assert result.lease.checkpoint.truth_snapshot_ref == result.truth_snapshot.snapshot_ref
    assert adapter.page_calls == 123
    assert adapter.legacy_extract_calls == 0
    assert max(profile.max_items for profile in adapter.profiles) == 100
    assert adapter.profiles[-1].max_items == 96
    assert _stored(store, RecordKind.SOURCE, adapter.discovery.source.source_id).record == adapter.discovery.source
    assert _stored(store, RecordKind.SOURCE_VERSION, adapter.version.source_version_id).record == adapter.version
    stored_chunks = []
    cursor = ""
    with store.begin_read() as read:
        while True:
            page = read.list_records(
                RecordKind.CHUNK,
                owner_scope=job.owner_scope,
                limit=1_000,
                cursor=cursor,
            )
            assert all(type(item) is StoredRecord for item in page.items)
            stored_chunks.extend(item.record for item in page.items)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
    assert tuple(stored_chunks) == adapter.records


@pytest.mark.parametrize("count", (0, 3))
def test_paged_extraction_accepts_terminal_empty_and_complete_ordered_graph(tmp_path, count):
    adapter = _PagedAdapter(count=count, page_size=2)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / f"valid-{count}.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref=f"request:valid-{count}", max_items=1)
    runtime.register(job)

    result = runtime.run_to_completion(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.valid",
        adapter=adapter,
        now=T0,
    )

    assert result.lease.job.status is JobStatus.COMPLETED
    assert result.lease.checkpoint.items_completed == 1
    assert adapter.legacy_extract_calls == 0


def test_paged_profile_mismatch_and_projection_deny_before_source_io(tmp_path):
    adapter = _PagedAdapter(required_profile="required-v1")
    mismatch = _job(adapter, request_ref="request:mismatch", max_items=1)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "profile.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    runtime.register(mismatch)
    adapter.discover_calls = adapter.observe_calls = adapter.page_calls = 0

    with pytest.raises(IndexJobRuntimeError, match="profile"):
        runtime.acquire(
            mismatch.job_id,
            owner_scope=mismatch.owner_scope,
            worker_id="worker.profile",
            adapter=adapter,
            now=T0,
        )
    assert (adapter.discover_calls, adapter.observe_calls, adapter.page_calls) == (0, 0, 0)

    matching = replace(mismatch, job_id="", request_ref="request:projection", profile_ref="required-v1")
    runtime.register(matching)
    lease = runtime.acquire(
        matching.job_id,
        owner_scope=matching.owner_scope,
        worker_id="worker.projection",
        adapter=adapter,
        now=T0,
    )
    with pytest.raises(IndexJobExecutionError) as failed:
        runtime.run_next(lease, adapter=adapter, now=T30, projection=_FailOnceProjection())
    assert "paged_projection_unsupported" in failed.value.job.cursor
    assert (adapter.discover_calls, adapter.observe_calls, adapter.page_calls) == (0, 0, 0)


def test_paged_cancellation_between_pages_prevents_truth_commit(tmp_path):
    adapter = _PagedAdapter(count=3, page_size=2)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "cancel.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:cancel-paged", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.cancel",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )
    adapter.on_page = lambda call: runtime.cancel(
        job.job_id, owner_scope=job.owner_scope, now=T30
    ) if call == 1 else None

    with pytest.raises(IndexJobLeaseConflict):
        runtime.run_next(lease, adapter=adapter, now=T30)

    assert _stored(store, RecordKind.JOB, job.job_id).record.status is JobStatus.CANCELLED
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None


def test_paged_atomic_commit_failure_leaves_no_partial_truth(tmp_path, monkeypatch):
    adapter = _PagedAdapter(count=3, page_size=2)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:atomic-fail", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.atomic",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )
    original_begin = store.begin_write
    commits = 0

    def begin_write(expected):
        transaction = original_begin(expected)
        original_commit = transaction.commit

        def commit():
            nonlocal commits
            commits += 1
            if commits == 1:
                transaction.rollback()
                raise RuntimeError("synthetic atomic rejection")
            return original_commit()

        transaction.commit = commit
        return transaction

    monkeypatch.setattr(store, "begin_write", begin_write)
    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T30)

    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None


class _CrashAfterCommit(BaseException):
    pass


def test_paged_crash_after_truth_commit_finalizes_without_source_io_or_rewrite(tmp_path, monkeypatch):
    adapter = _PagedAdapter(count=3, page_size=2)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:crash", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.crash",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )
    original_begin = store.begin_write
    crashed = False

    def begin_write(expected):
        transaction = original_begin(expected)
        original_commit = transaction.commit

        def commit():
            nonlocal crashed
            snapshot = original_commit()
            if not crashed:
                crashed = True
                raise _CrashAfterCommit()
            return snapshot

        transaction.commit = commit
        return transaction

    monkeypatch.setattr(store, "begin_write", begin_write)
    with pytest.raises(_CrashAfterCommit):
        runtime.run_next(lease, adapter=adapter, now=T30)
    calls = (adapter.discover_calls, adapter.observe_calls, adapter.page_calls)
    revisions = {
        record.chunk_id: _stored(store, RecordKind.CHUNK, record.chunk_id).revision
        for record in adapter.records
    }

    recovered = runtime.run_to_completion(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.crash",
        adapter=adapter,
        now=T30,
        lease_seconds=120,
    )

    assert recovered.lease.job.status is JobStatus.COMPLETED
    assert recovered.lease.checkpoint.truth_snapshot_ref
    assert recovered.truth_snapshot.snapshot_ref == recovered.lease.checkpoint.truth_snapshot_ref
    assert (adapter.discover_calls, adapter.observe_calls, adapter.page_calls) == calls
    assert {
        record.chunk_id: _stored(store, RecordKind.CHUNK, record.chunk_id).revision
        for record in adapter.records
    } == revisions


def test_forge_truth_commit_cannot_use_generic_terminal_finalizer():
    adapter = _adapter()
    job = _job(adapter)
    forge_capability = replace(adapter.describe_capability(), adapter_id="forge.code")
    checkpoint = JobCheckpoint(
        job_id=job.job_id,
        adapter_id="forge.code",
        adapter_version=forge_capability.adapter_version,
        stage=JobStage.TRUTH_COMMIT,
    )
    lease = JobLease(job, 1, checkpoint)
    runtime = UnifiedSourceIndexJobRuntime(InMemoryUnifiedSourceIndexStore())

    with pytest.raises(IndexJobExecutionError, match="coverage_publication_required"):
        runtime._finalize_truth_commit(lease, forge_capability, T0)


@pytest.mark.parametrize("expiry", ("deadline", "lease"))
def test_paged_rechecks_deadline_and_lease_after_staging_before_commit(
    tmp_path, monkeypatch, expiry
):
    adapter = _PagedAdapter(count=3, page_size=2)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(
        adapter,
        request_ref=f"request:precommit-{expiry}",
        max_items=1,
        time_budget_ms=1_000 if expiry == "deadline" else 60_000,
    )
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.precommit",
        adapter=adapter,
        now=T0,
        lease_seconds=120 if expiry == "deadline" else 1,
    )
    clock = [0.0]
    monkeypatch.setattr("src.unified_source_index_jobs.time.monotonic", lambda: clock[0])
    original_begin = store.begin_write

    def begin_write(expected):
        transaction = original_begin(expected)
        original_put = transaction.put

        def put(record, *, expected_record_revision=0):
            result = original_put(
                record, expected_record_revision=expected_record_revision
            )
            clock[0] = 2.0
            return result

        transaction.put = put
        return transaction

    monkeypatch.setattr(store, "begin_write", begin_write)
    expected = IndexJobExecutionError if expiry == "deadline" else IndexJobLeaseConflict
    with pytest.raises(expected):
        runtime.run_next(lease, adapter=adapter, now=T0)

    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None


def test_paged_cancel_then_adapter_error_preserves_cancelled_job_and_no_truth(tmp_path):
    adapter = _PagedAdapter(count=3, page_size=2)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:cancel-error", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.cancel-error",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    def cancel_then_raise(call):
        assert call == 1
        runtime.cancel(job.job_id, owner_scope=job.owner_scope, now=T30)
        raise RuntimeError("synthetic adapter failure after cancellation")

    adapter.on_page = cancel_then_raise
    with pytest.raises(IndexJobLeaseConflict):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert _stored(store, RecordKind.JOB, job.job_id).record.status is JobStatus.CANCELLED
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None


def test_paged_ordinary_exception_after_s1_recovers_without_source_io_or_rewrite(
    monkeypatch,
):
    adapter = _PagedAdapter(count=3, page_size=2)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:ordinary-post-s1", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.ordinary-crash",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )
    original_begin = store.begin_write
    raised = False

    def begin_write(expected):
        transaction = original_begin(expected)
        original_commit = transaction.commit

        def commit():
            nonlocal raised
            snapshot = original_commit()
            if not raised:
                raised = True
                raise RuntimeError("synthetic ordinary exception after durable commit")
            return snapshot

        transaction.commit = commit
        return transaction

    monkeypatch.setattr(store, "begin_write", begin_write)
    with pytest.raises(IndexJobExecutionError, match="finalization remains pending"):
        runtime.run_next(lease, adapter=adapter, now=T30)
    calls = (adapter.discover_calls, adapter.observe_calls, adapter.page_calls)
    revisions = {
        record.chunk_id: _stored(store, RecordKind.CHUNK, record.chunk_id).revision
        for record in adapter.records
    }

    recovered = runtime.run_to_completion(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.ordinary-crash",
        adapter=adapter,
        now=T30,
        lease_seconds=120,
    )

    assert recovered.terminal is True
    assert recovered.truth_snapshot.snapshot_ref == recovered.lease.checkpoint.truth_snapshot_ref
    assert (adapter.discover_calls, adapter.observe_calls, adapter.page_calls) == calls
    assert {
        record.chunk_id: _stored(store, RecordKind.CHUNK, record.chunk_id).revision
        for record in adapter.records
    } == revisions


def test_paged_page_budget_rejects_123rd_nonterminal_page_without_truth(tmp_path):
    adapter = _PagedAdapter(count=124, page_size=1)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "page-budget.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:page-budget", max_items=1, time_budget_ms=60_000)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.page-budget",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert adapter.page_calls == 123
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None






def test_paged_required_profile_rejects_adapter_without_page_protocol_before_io(tmp_path):
    paged = _PagedAdapter(count=1)

    class MissingPageAdapter:
        def describe_capability(self):
            return paged.capability

        def discover(self, *args, **kwargs):
            paged.discover_calls += 1
            raise AssertionError("protocol mismatch must deny before discovery")

        def observe_version(self, *args, **kwargs):
            raise AssertionError("protocol mismatch must deny before observation")

        def extract(self, *args, **kwargs):
            raise AssertionError("protocol mismatch must deny before extraction")

        def read_exact(self, *args, **kwargs):
            raise AssertionError("outside protocol mismatch test")

        def observe_unavailable(self, *args, **kwargs):
            raise AssertionError("outside protocol mismatch test")

    adapter = MissingPageAdapter()
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "missing-page.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(paged, request_ref="request:missing-page", max_items=1)
    runtime.register(job)

    with pytest.raises(IndexJobRuntimeError, match="PagedSourceAdapter"):
        runtime.acquire(
            job.job_id,
            owner_scope=job.owner_scope,
            worker_id="worker.missing-page",
            adapter=adapter,
            now=T0,
        )
    assert paged.discover_calls == 1


def test_paged_inline_character_aggregate_is_bounded_before_truth(tmp_path):
    adapter = _PagedAdapter(count=0, page_size=1)
    payload = "x" * 250_001
    adapter.records = tuple(sorted((
        ChunkRecord.create(
            adapter.version,
            locator=TextRangeLocator(index, index + 1),
            extractor_profile_ref="child.large-v1",
            content_hash=content_hash(payload),
            content=payload,
        )
        for index in range(4)
    ), key=_record_key))
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "chars.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:chars", max_items=1, time_budget_ms=60_000)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.chars",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert [profile.max_chars for profile in adapter.profiles] == [
        1_000_000, 749_999, 499_998, 249_997,
    ]
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None


@pytest.mark.slow
def test_paged_aggregate_rejects_12297th_record_without_truth(tmp_path):
    adapter = _PagedAdapter(count=12_297, page_size=100)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(
        adapter,
        request_ref="request:aggregate-overflow",
        max_items=1,
        time_budget_ms=300_000,
    )
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.aggregate-overflow",
        adapter=adapter,
        now=T0,
        lease_seconds=600,
    )

    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert adapter.page_calls == 123
    assert adapter.profiles[-1].max_items == 96
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None



def test_paged_observation_rejects_nonexact_type_without_class_behavior(tmp_path):
    adapter = _PagedAdapter(count=0)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "hostile-observation.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:hostile-observation", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.hostile-observation",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )
    callbacks = []

    class HostileObservation:
        @property
        def __class__(self):
            callbacks.append("class")
            return SourceVersionRecord

    adapter.observe_version = lambda source_ref: HostileObservation()
    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert callbacks == []
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None


def test_paged_rechecks_fresh_time_after_final_lease_reload_before_commit(
    monkeypatch,
):
    adapter = _PagedAdapter(count=1, page_size=1)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(
        adapter,
        request_ref="request:fresh-precommit-time",
        max_items=1,
        time_budget_ms=1_000,
    )
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.fresh-precommit-time",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )
    clock = [0.0]
    monkeypatch.setattr("src.unified_source_index_jobs.time.monotonic", lambda: clock[0])
    original_require = runtime._require_current_lease
    calls = 0

    def require_current(*args, **kwargs):
        nonlocal calls
        result = original_require(*args, **kwargs)
        calls += 1
        if calls == 4:
            clock[0] = 2.0
        return result

    monkeypatch.setattr(runtime, "_require_current_lease", require_current)
    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert calls == 4
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None


@pytest.mark.parametrize(("count", "mutation_call"), ((0, 1), (3, 2)))
def test_paged_source_version_authority_cannot_be_mutated_by_page_adapter(
    tmp_path, count, mutation_call
):
    adapter = _PagedAdapter(count=count, page_size=2)
    original_version = adapter.version
    original_extract_page = adapter.extract_page

    def mutating_extract_page(
        source_version, profile, *, cursor, sequence_start
    ):
        if adapter.page_calls + 1 == mutation_call:
            object.__setattr__(
                source_version,
                "version_observed_at",
                "2026-07-17T10:00:01Z",
            )
        return original_extract_page(
            source_version,
            profile,
            cursor=cursor,
            sequence_start=sequence_start,
        )

    adapter.extract_page = mutating_extract_page
    store = SQLiteUnifiedSourceIndexStore(
        tmp_path / f"mutated-version-{count}.db"
    )
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(
        adapter,
        request_ref=f"request:mutated-version-{count}",
        max_items=1,
        time_budget_ms=60_000,
    )
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id=f"worker.mutated-version-{count}",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert adapter.version == original_version
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE_VERSION,
            original_version.source_version_id,
            owner_scope=job.owner_scope,
        ) is None



def test_paged_empty_discovery_cancel_preserves_cancelled_job(tmp_path):
    adapter = _PagedAdapter(count=0)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "empty-cancel.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:empty-cancel", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.empty-cancel",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    def cancel_then_empty(*args, **kwargs):
        runtime.cancel(job.job_id, owner_scope=job.owner_scope, now=T30)
        return DiscoveryPage(())

    adapter.discover = cancel_then_empty
    with pytest.raises(IndexJobLeaseConflict):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert _stored(store, RecordKind.JOB, job.job_id).record.status is JobStatus.CANCELLED


def test_paged_empty_discovery_rechecks_lease_expiry_after_adapter(
    monkeypatch,
):
    adapter = _PagedAdapter(count=0)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(
        adapter,
        request_ref="request:empty-expired-after-adapter",
        max_items=1,
        time_budget_ms=60_000,
    )
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.empty-expired-after-adapter",
        adapter=adapter,
        now=T0,
        lease_seconds=1,
    )
    clock = [0.0]
    monkeypatch.setattr("src.unified_source_index_jobs.time.monotonic", lambda: clock[0])

    def expire_then_empty(*args, **kwargs):
        clock[0] = 2.0
        return DiscoveryPage(())

    adapter.discover = expire_then_empty
    with pytest.raises(IndexJobLeaseConflict):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert _stored(store, RecordKind.JOB, job.job_id).record.status is JobStatus.RUNNING


@pytest.mark.parametrize("expiry", ("deadline", "lease"))
def test_paged_empty_discovery_rechecks_time_after_completion_staging(
    monkeypatch, expiry
):
    adapter = _PagedAdapter(count=0)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(
        adapter,
        request_ref=f"request:empty-staging-{expiry}",
        max_items=1,
        time_budget_ms=1_000 if expiry == "deadline" else 60_000,
    )
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.empty-staging",
        adapter=adapter,
        now=T0,
        lease_seconds=120 if expiry == "deadline" else 1,
    )
    adapter.discover = lambda *args, **kwargs: DiscoveryPage(())
    clock = [0.0]
    monkeypatch.setattr("src.unified_source_index_jobs.time.monotonic", lambda: clock[0])
    original_begin = store.begin_write
    advanced = False

    def begin_write(expected):
        transaction = original_begin(expected)
        original_compare = transaction.compare_and_swap

        def compare_and_swap(record, *, expected_record_revision):
            nonlocal advanced
            result = original_compare(
                record,
                expected_record_revision=expected_record_revision,
            )
            if not advanced:
                advanced = True
                clock[0] = 2.0
            return result

        transaction.compare_and_swap = compare_and_swap
        return transaction

    monkeypatch.setattr(store, "begin_write", begin_write)
    expected = IndexJobExecutionError if expiry == "deadline" else IndexJobLeaseConflict
    with pytest.raises(expected):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert _stored(store, RecordKind.JOB, job.job_id).record.status is not JobStatus.COMPLETED


def test_paged_empty_discovery_late_cancel_wins_atomic_completion_cas(monkeypatch):
    adapter = _PagedAdapter(count=0)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:empty-late-cancel", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.empty-late-cancel",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )
    adapter.discover = lambda *args, **kwargs: DiscoveryPage(())
    original_begin = store.begin_write
    triggered = False

    def begin_write(expected):
        transaction = original_begin(expected)
        original_compare = transaction.compare_and_swap

        def compare_and_swap(record, *, expected_record_revision):
            nonlocal triggered
            result = original_compare(
                record,
                expected_record_revision=expected_record_revision,
            )
            if not triggered:
                triggered = True
                runtime.cancel(job.job_id, owner_scope=job.owner_scope, now=T30)
            return result

        transaction.compare_and_swap = compare_and_swap
        return transaction

    monkeypatch.setattr(store, "begin_write", begin_write)
    with pytest.raises(IndexJobLeaseConflict):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert _stored(store, RecordKind.JOB, job.job_id).record.status is JobStatus.CANCELLED


def test_paged_empty_discovery_result_matches_checkpoint_truth_snapshot(tmp_path):
    adapter = _PagedAdapter(count=0)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "empty-positive.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:empty-positive", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.empty-positive",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )
    adapter.discover = lambda *args, **kwargs: DiscoveryPage(())

    result = runtime.run_next(lease, adapter=adapter, now=T0)

    assert result.terminal is True
    assert result.lease.job.status is JobStatus.COMPLETED
    assert result.lease.checkpoint.truth_snapshot_ref == result.truth_snapshot.snapshot_ref


def test_paged_page_cannot_exceed_requested_capability_budget_before_truth(tmp_path):
    adapter = _PagedAdapter(count=2, page_size=2)
    adapter.capability = replace(adapter.capability, max_extract_items=1)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "page-budget-overflow.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:page-budget-overflow", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.page-budget-overflow",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert adapter.page_calls == 1
    assert adapter.profiles[0].max_items == 1
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None


def test_paged_page_budget_uses_immutable_requested_value_against_adapter_mutation(
    tmp_path,
):
    adapter = _PagedAdapter(count=2, page_size=2)
    adapter.capability = replace(adapter.capability, max_extract_items=1)
    original_extract_page = adapter.extract_page

    def mutate_profile_then_extract(source_version, profile, *, cursor, sequence_start):
        object.__setattr__(profile, "max_items", 2)
        return original_extract_page(
            source_version,
            profile,
            cursor=cursor,
            sequence_start=sequence_start,
        )

    adapter.extract_page = mutate_profile_then_extract
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "mutated-page-budget.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:mutated-page-budget", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.mutated-page-budget",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert adapter.page_calls == 1
    assert adapter.profiles[0].max_items == 2
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            adapter.discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None


def test_paged_capability_one_accepts_compliant_one_record_pages(tmp_path):
    adapter = _PagedAdapter(count=2, page_size=1)
    adapter.capability = replace(adapter.capability, max_extract_items=1)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "page-budget-compliant.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:page-budget-compliant", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.page-budget-compliant",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    result = runtime.run_next(lease, adapter=adapter, now=T0)

    assert result.terminal is True
    assert adapter.page_calls == 2
    assert [profile.max_items for profile in adapter.profiles] == [1, 1]
    for record in adapter.records:
        assert _stored(store, RecordKind.CHUNK, record.chunk_id).record == record


@pytest.mark.parametrize("budgeted", (False, True))
def test_paged_exact_character_cap_accepts_trailing_noncontent_terminal_record(
    tmp_path, budgeted
):
    adapter = _PagedAdapter(count=1, page_size=1)
    content_chunk = adapter.records[0]
    metadata_chunk = None
    for start in range(1, 1_001):
        candidate = ChunkRecord.create(
            adapter.version,
            locator=TextRangeLocator(start, start + 1),
            extractor_profile_ref="child.exact-v1",
            content_hash=content_hash("metadata-only"),
            content=None,
        )
        if content_chunk.chunk_id < candidate.chunk_id:
            metadata_chunk = candidate
            break
    assert metadata_chunk is not None
    adapter.records = (content_chunk, metadata_chunk)
    adapter.capability = replace(
        adapter.capability,
        max_total_extract_chars=1,
        supports_budgeted_paging=budgeted,
    )
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:exact-character-cap", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.exact-character-cap",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    result = runtime.run_next(lease, adapter=adapter, now=T0)

    assert result.terminal
    assert sum(len(record.content or "") for record in adapter.records) == 1
    assert _stored(store, RecordKind.CHUNK, metadata_chunk.chunk_id).record == metadata_chunk
    if budgeted:
        assert [budget.max_chars for budget in adapter.page_budgets] == [1, 0]


def test_knowledge_direct_paging_rejects_mutated_profile_and_budget_before_store_io(
    tmp_path, monkeypatch
):
    harbour, _, adapter = _harbour_knowledge_adapter(tmp_path, b"content")
    capability = adapter.describe_capability()
    discovery = adapter.discover(
        AdapterScope(capability.owner_scope, capability.classification_ceiling),
        cursor="",
        limit=1,
        time_budget_ms=5_000,
    )
    version = adapter.observe_version(
        RecordRef(RecordKind.SOURCE, discovery.items[0].source.source_id)
    )
    assert type(version) is SourceVersionRecord

    class FalseComparisonText(str):
        def __eq__(self, other):
            return False

        def __ne__(self, other):
            return False

    class IntegerSubclass(int):
        pass

    store_calls = []
    monkeypatch.setattr(
        harbour,
        "capture_index_snapshot",
        lambda *args, **kwargs: store_calls.append("capture"),
    )
    monkeypatch.setattr(
        harbour,
        "read_payload_at_generation",
        lambda *args, **kwargs: store_calls.append("payload"),
    )

    profile_mutations = (
        ("profile_ref", FalseComparisonText(KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE)),
        ("max_items", True),
        ("max_items", -1),
        ("max_items", 1_001),
        ("max_chars", IntegerSubclass(10)),
        ("max_chars", -1),
        ("max_chars", 1_000_001),
        ("time_budget_ms", IntegerSubclass(10)),
        ("time_budget_ms", -1),
        ("time_budget_ms", 86_400_001),
    )
    for field, invalid_value in profile_mutations:
        profile = ExtractionProfile(
            KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
            max_items=100,
            max_chars=100,
            time_budget_ms=1_000,
        )
        object.__setattr__(profile, field, invalid_value)
        with pytest.raises(KnowledgeSourceAdapterError) as raised:
            adapter.extract_page_budgeted(
                version,
                profile,
                ExtractionPageBudget(1, 10, 10),
                cursor="",
                sequence_start=0,
            )
        assert raised.value.code == "invalid_request"
        assert raised.value.__context__ is None
        assert raised.value.__cause__ is None
        assert store_calls == []

    budget_mutations = (
        ("max_items", True),
        ("max_items", -1),
        ("max_items", 101),
        ("max_chars", IntegerSubclass(10)),
        ("max_chars", -1),
        ("max_chars", 1_000_001),
        ("time_budget_ms", IntegerSubclass(10)),
        ("time_budget_ms", -1),
        ("time_budget_ms", 86_400_001),
    )
    for field, invalid_value in budget_mutations:
        budget = ExtractionPageBudget(1, 10, 10)
        object.__setattr__(budget, field, invalid_value)
        with pytest.raises(KnowledgeSourceAdapterError) as raised:
            adapter.extract_page_budgeted(
                version,
                ExtractionProfile(
                    KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
                    max_items=1_000,
                    max_chars=1_000_000,
                    time_budget_ms=86_400_000,
                ),
                budget,
                cursor="",
                sequence_start=0,
            )
        assert raised.value.code == "invalid_request"
        assert raised.value.__context__ is None
        assert raised.value.__cause__ is None
        assert store_calls == []

    harbour.close()


def test_knowledge_public_boundaries_reject_hostile_nested_inputs_before_store_io(
    tmp_path, monkeypatch
):
    harbour, principal, adapter = _harbour_knowledge_adapter(tmp_path, b"content")
    capability = adapter.describe_capability()
    scope = AdapterScope(capability.owner_scope, Classification.SENSITIVE)
    discovery = adapter.discover(
        scope, cursor="", limit=1, time_budget_ms=5_000
    )
    source_ref = RecordRef(RecordKind.SOURCE, discovery.items[0].source.source_id)
    version = adapter.observe_version(source_ref)
    assert type(version) is SourceVersionRecord
    profile = ExtractionProfile(
        KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 10, 1_000_000, 5_000
    )
    page = adapter.extract_page_budgeted(
        version,
        profile,
        ExtractionPageBudget(10, 1_000_000, 5_000),
        cursor="",
        sequence_start=0,
    )
    assert len(page.records) == 1

    class HostileText(str):
        def __eq__(self, other):
            raise RuntimeError("must not compare hostile text")

        def __ne__(self, other):
            raise RuntimeError("must not compare hostile text")

        def __hash__(self):
            raise RuntimeError("must not hash hostile text")

    class HostileInteger(int):
        def __lt__(self, other):
            raise RuntimeError("must not compare hostile integer")

        def __le__(self, other):
            raise RuntimeError("must not compare hostile integer")

    hostile_text = HostileText("not-canonical")
    store_calls = []
    monkeypatch.setattr(
        harbour,
        "capture_index_snapshot",
        lambda *args, **kwargs: store_calls.append("capture"),
    )
    monkeypatch.setattr(
        harbour,
        "read_payload_at_generation",
        lambda *args, **kwargs: store_calls.append("payload"),
    )

    def assert_rejected_without_io(call):
        with pytest.raises(KnowledgeSourceAdapterError) as caught:
            call()
        assert caught.value.__context__ is None
        assert caught.value.__cause__ is None
        assert caught.value.code in {
            "invalid_request",
            "invalid_authority",
            "policy_blocked",
        }
        assert store_calls == []

    # Constructor validates identity and all principal slots before its initial
    # authenticated metadata capture.
    assert_rejected_without_io(
        lambda: KnowledgeSourceAdapter(
            store=harbour,
            principal=principal,
            owner_id=HostileText(HARBOUR_OWNER),
            cursor_signing_key=b"k" * 32,
        )
    )
    original_owner = object.__getattribute__(principal, "_owner_id")
    object.__setattr__(principal, "_owner_id", hostile_text)
    try:
        assert_rejected_without_io(
            lambda: KnowledgeSourceAdapter(
                store=harbour, principal=principal, owner_id=HARBOUR_OWNER,
                cursor_signing_key=b"k" * 32,
            )
        )
    finally:
        object.__setattr__(principal, "_owner_id", original_owner)

    malformed_scope = AdapterScope(capability.owner_scope, Classification.SENSITIVE)
    object.__setattr__(malformed_scope, "owner_scope", hostile_text)
    assert_rejected_without_io(
        lambda: adapter.discover(
            malformed_scope, cursor="", limit=1, time_budget_ms=5_000
        )
    )
    malformed_scope = AdapterScope(capability.owner_scope, Classification.SENSITIVE)
    object.__setattr__(malformed_scope, "source_ids", (hostile_text,))
    assert_rejected_without_io(
        lambda: adapter.discover(
            malformed_scope, cursor="", limit=1, time_budget_ms=5_000
        )
    )
    assert_rejected_without_io(
        lambda: adapter.discover(
            scope,
            cursor=hostile_text,
            limit=1,
            time_budget_ms=5_000,
        )
    )
    assert_rejected_without_io(
        lambda: adapter.discover(
            scope,
            cursor="",
            limit=HostileInteger(1),
            time_budget_ms=5_000,
        )
    )
    assert_rejected_without_io(
        lambda: adapter.discover(
            scope,
            cursor="",
            limit=1,
            time_budget_ms=HostileInteger(5_000),
        )
    )
    malformed_scope = AdapterScope(capability.owner_scope, Classification.SENSITIVE)
    object.__setattr__(malformed_scope, "classification_ceiling", UnavailableReason.DELETED)
    assert_rejected_without_io(
        lambda: adapter.discover(
            malformed_scope, cursor="", limit=1, time_budget_ms=5_000
        )
    )

    malformed_ref = RecordRef(RecordKind.SOURCE, source_ref.record_id)
    object.__setattr__(malformed_ref, "record_id", hostile_text)
    assert_rejected_without_io(lambda: adapter.observe_version(malformed_ref))
    assert_rejected_without_io(
        lambda: adapter.observe_unavailable(malformed_ref, UnavailableReason.DELETED)
    )
    assert_rejected_without_io(
        lambda: adapter.observe_unavailable(source_ref, hostile_text)
    )
    original_reason_value = object.__getattribute__(
        UnavailableReason.DELETED, "_value_"
    )
    object.__setattr__(UnavailableReason.DELETED, "_value_", hostile_text)
    try:
        assert_rejected_without_io(
            lambda: adapter.observe_unavailable(
                source_ref, UnavailableReason.DELETED
            )
        )
    finally:
        object.__setattr__(UnavailableReason.DELETED, "_value_", original_reason_value)

    clean_version = replace(version)
    malformed_version = version
    object.__setattr__(malformed_version, "source_id", hostile_text)
    assert_rejected_without_io(lambda: adapter.extract(malformed_version, profile))
    assert_rejected_without_io(
        lambda: adapter.extract_page(
            malformed_version, profile, cursor="", sequence_start=0
        )
    )
    assert_rejected_without_io(
        lambda: adapter.extract_page_budgeted(
            malformed_version,
            profile,
            ExtractionPageBudget(10, 1_000_000, 5_000),
            cursor="",
            sequence_start=0,
        )
    )

    malformed_policy_version = replace(clean_version)
    object.__setattr__(
        malformed_policy_version,
        "policy_evidence",
        replace(clean_version.policy_evidence),
    )
    object.__setattr__(
        malformed_policy_version.policy_evidence,
        "owner_scope",
        hostile_text,
    )
    assert_rejected_without_io(
        lambda: adapter.extract_page_budgeted(
            malformed_policy_version,
            profile,
            ExtractionPageBudget(10, 1_000_000, 5_000),
            cursor="",
            sequence_start=0,
        )
    )
    assert_rejected_without_io(
        lambda: adapter.extract_page_budgeted(
            version,
            profile,
            ExtractionPageBudget(10, 1_000_000, 5_000),
            cursor=hostile_text,
            sequence_start=0,
        )
    )

    malformed_profile = ExtractionProfile(
        KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE, 10, 1_000_000, 5_000
    )
    object.__setattr__(malformed_profile, "profile_ref", hostile_text)
    assert_rejected_without_io(lambda: adapter.extract(version, malformed_profile))
    malformed_budget = ExtractionPageBudget(10, 1_000_000, 5_000)
    object.__setattr__(malformed_budget, "max_items", HostileInteger(10))
    assert_rejected_without_io(
        lambda: adapter.extract_page_budgeted(
            version, profile, malformed_budget, cursor="", sequence_start=0
        )
    )

    malformed_evidence = page.records[0].evidence_ref()
    object.__setattr__(malformed_evidence, "record_id", hostile_text)
    malformed_request = ExactReadRequest(malformed_evidence, 10)
    policy = PolicyReadContext(capability.owner_scope, Classification.SENSITIVE, True)
    assert_rejected_without_io(lambda: adapter.read_exact(malformed_request, policy))
    malformed_request = ExactReadRequest(page.records[0].evidence_ref(), 10)
    object.__setattr__(malformed_request, "max_chars", HostileInteger(10))
    assert_rejected_without_io(lambda: adapter.read_exact(malformed_request, policy))
    malformed_evidence = page.records[0].evidence_ref()
    malformed_locator = replace(malformed_evidence.locator)
    object.__setattr__(malformed_locator, "start_char", hostile_text)
    object.__setattr__(malformed_evidence, "locator", malformed_locator)
    assert_rejected_without_io(
        lambda: adapter.read_exact(ExactReadRequest(malformed_evidence, 10), policy)
    )
    malformed_evidence = page.records[0].evidence_ref()
    malformed_evidence_policy = replace(malformed_evidence.policy_evidence)
    object.__setattr__(malformed_evidence_policy, "owner_scope", hostile_text)
    object.__setattr__(malformed_evidence, "policy_evidence", malformed_evidence_policy)
    assert_rejected_without_io(
        lambda: adapter.read_exact(ExactReadRequest(malformed_evidence, 10), policy)
    )
    malformed_policy = PolicyReadContext(
        capability.owner_scope, Classification.SENSITIVE, True
    )
    object.__setattr__(malformed_policy, "owner_scope", hostile_text)
    assert_rejected_without_io(
        lambda: adapter.read_exact(
            ExactReadRequest(page.records[0].evidence_ref(), 10), malformed_policy
        )
    )

    harbour.close()


def test_real_harbour_budgeted_job_commits_262145_chars_across_single_record_pages(
    tmp_path,
):
    payload = b"a" * 262_145
    harbour, _, adapter, truth, result, chunks, source, profiles, budgets = _run_harbour_job(
        tmp_path, payload, max_page_items=1
    )

    assert result.lease.job.status is JobStatus.COMPLETED
    assert source is not None
    assert len(chunks) == 2
    assert sum(len(chunk.content or "") for chunk in chunks) == len(payload)
    assert adapter.describe_capability().supports_budgeted_paging is True
    assert adapter.describe_capability().max_total_extract_chars == 8_388_608
    assert all(chunk.extractor_profile_ref == KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE for chunk in chunks)
    assert len(profiles) == 2 and profiles[0] == profiles[1]
    assert all(type(budget) is ExtractionPageBudget for budget in budgets)
    assert all(budget.max_items == 1 for budget in budgets)
    ordered = sorted(chunks, key=lambda chunk: chunk.locator.start_char)
    assert "".join(chunk.content or "" for chunk in ordered) == payload.decode("utf-8")
    assert len(ordered) == 2
    for chunk in chunks:
        assert _stored(
            truth,
            RecordKind.CHUNK,
            chunk.chunk_id,
            owner=adapter.describe_capability().owner_scope,
        ).record == chunk
    harbour.close()


def test_real_harbour_budgeted_job_commits_eight_mib_and_exact_reads_committed_evidence(
    tmp_path,
):
    payload = b"z" * 8_388_608
    harbour, _, adapter, truth, result, chunks, source, profiles, budgets = _run_harbour_job(
        tmp_path, payload, max_page_items=100
    )

    assert result.lease.job.status is JobStatus.COMPLETED
    assert source is not None
    assert len(chunks) == 32
    assert len(profiles) == len(budgets) == 11
    assert all(profile == profiles[0] for profile in profiles)
    assert all(type(budget) is ExtractionPageBudget for budget in budgets)
    assert all(
        budget.max_chars <= profile.max_chars
        for budget, profile in zip(budgets, profiles)
    )
    assert budgets[-1].max_chars < budgets[0].max_chars
    assert all(
        later.time_budget_ms <= earlier.time_budget_ms
        for earlier, later in zip(budgets, budgets[1:])
    )
    assert sum(len(chunk.content or "") for chunk in chunks) == 8_388_608
    assert "".join(
        chunk.content or ""
        for chunk in sorted(chunks, key=lambda chunk: chunk.locator.start_char)
    ) == payload.decode("utf-8")
    exact = adapter.read_exact(
        ExactReadRequest(chunks[0].evidence_ref(), 64),
        PolicyReadContext(
            adapter.describe_capability().owner_scope,
            Classification.SENSITIVE,
            True,
        ),
    )
    assert exact.content == "z" * 64
    assert exact.clipped is True
    assert result.lease.checkpoint.truth_snapshot_ref == result.truth_snapshot.snapshot_ref
    assert all(
        _stored(
            truth,
            RecordKind.CHUNK,
            chunk.chunk_id,
            owner=adapter.describe_capability().owner_scope,
        ).record == chunk
        for chunk in chunks
    )
    harbour.close()


@pytest.mark.parametrize(
    "fault",
    ("page_error", "cursor", "profile", "budget", "expiry", "lease"),
)
def test_real_harbour_budgeted_job_failure_before_terminal_commit_leaves_no_source_truth(
    tmp_path, fault
):
    harbour, _, adapter = _harbour_knowledge_adapter(
        tmp_path, b"q" * 262_145, max_page_items=1
    )
    capability = adapter.describe_capability()
    discovery = adapter.discover(
        AdapterScope(capability.owner_scope, capability.classification_ceiling),
        cursor="",
        limit=1,
        time_budget_ms=5_000,
    )
    source_id = discovery.items[0].source.source_id
    observed_version = adapter.observe_version(
        RecordRef(RecordKind.SOURCE, source_id)
    )
    assert type(observed_version) is SourceVersionRecord
    scope = SourceScope.create(
        item.source.policy_evidence() for item in discovery.items
    )
    duration_ms = 250 if fault == "expiry" else 120_000
    job = IndexJobRecord.create(
        job_kind=IndexJobKind.EXTRACTION,
        source_scope=scope,
        request_ref=f"request:harbour-budgeted-{fault}",
        profile_ref=KNOWLEDGE_SOURCE_EXTRACTOR_PROFILE,
        max_items=1,
        time_budget_ms=duration_ms,
    )
    truth = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(truth)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.harbour-budgeted-failure",
        adapter=adapter,
        now=T0,
        lease_seconds=3_600,
    )
    original_page = adapter.extract_page_budgeted
    calls = 0

    def inject_fault(source_version, profile, budget, *, cursor, sequence_start):
        nonlocal calls
        calls += 1
        if fault == "page_error" and calls == 2:
            raise RuntimeError("bounded adapter failure")
        if fault == "cursor" and calls == 2:
            cursor += "!"
        page = original_page(
            source_version,
            profile,
            budget,
            cursor=cursor,
            sequence_start=sequence_start,
        )
        if fault == "profile" and calls == 1:
            object.__setattr__(profile, "max_chars", profile.max_chars + 1)
        elif fault == "budget" and calls == 1:
            object.__setattr__(budget, "max_items", True)
        elif fault == "expiry" and calls == 1:
            time.sleep(0.3)
        elif fault == "lease" and calls == 1:
            runtime.cancel(job.job_id, owner_scope=job.owner_scope, now=T30)
        return page

    adapter.extract_page_budgeted = inject_fault
    expected_error = IndexJobLeaseConflict if fault == "lease" else IndexJobExecutionError
    with pytest.raises(expected_error):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert calls == (1 if fault in {"profile", "budget", "expiry", "lease"} else 2)
    with truth.begin_read() as read:
        assert read.get(RecordKind.SOURCE, source_id, owner_scope=job.owner_scope) is None
        assert read.get(
            RecordKind.SOURCE_VERSION,
            observed_version.source_version_id,
            owner_scope=job.owner_scope,
        ) is None
        assert read.list_records(
            RecordKind.CHUNK,
            owner_scope=job.owner_scope,
            limit=100,
        ).items == ()
    harbour.close()


def test_paged_projection_denial_preserves_concurrent_cancel_without_adapter_io(
    monkeypatch,
):
    adapter = _PagedAdapter(count=1)
    store = InMemoryUnifiedSourceIndexStore()
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:paged-projection-cancel", max_items=1)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.paged-projection-cancel",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )
    original_require = runtime._require_current_lease
    triggered = False

    def require_current(*args, **kwargs):
        nonlocal triggered
        current = original_require(*args, **kwargs)
        if not triggered:
            triggered = True
            runtime.cancel(job.job_id, owner_scope=job.owner_scope, now=T30)
        return current

    monkeypatch.setattr(runtime, "_require_current_lease", require_current)
    with pytest.raises(IndexJobLeaseConflict):
        runtime.run_next(lease, adapter=adapter, now=T0, projection=_FailOnceProjection())

    assert adapter.discover_calls == 1
    assert adapter.observe_calls == 0
    assert adapter.page_calls == 0
    assert _stored(store, RecordKind.JOB, job.job_id).record.status is JobStatus.CANCELLED


def test_paged_discovery_scope_mutation_cannot_expand_source_authority(tmp_path):
    adapter = _PagedAdapter(count=1, page_size=1)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "mutated-source-scope.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(
        adapter,
        request_ref="request:mutated-source-scope",
        max_items=1,
    )
    expected_source_ids = job.source_scope.source_ids
    foreign = DeterministicFakeSourceAdapter(
        adapter.capability,
        (FakeAdapterDocument("doc:foreign", "rev:1", "x"),),
    )
    foreign_scope = AdapterScope("user:alice", Classification.SENSITIVE)
    foreign_discovery = foreign.discover(
        foreign_scope,
        cursor="",
        limit=1,
        time_budget_ms=100,
    ).items[0]
    foreign_version = foreign.observe_version(foreign_discovery.source.ref())
    assert type(foreign_version) is SourceVersionRecord
    foreign_record = ChunkRecord.create(
        foreign_version,
        locator=TextRangeLocator(0, 1),
        extractor_profile_ref="child.exact-v1",
        content_hash=content_hash("x"),
        content="x",
    )

    def mutate_scope_then_discover(scope, *, cursor, limit, time_budget_ms):
        object.__setattr__(scope, "source_ids", ())
        return DiscoveryPage((foreign_discovery,))

    adapter.discover = mutate_scope_then_discover
    adapter.observe_version = lambda source_ref: foreign_version
    adapter.records = (foreign_record,)
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.mutated-source-scope",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    with pytest.raises(IndexJobExecutionError):
        runtime.run_next(lease, adapter=adapter, now=T0)

    assert job.source_scope.source_ids == expected_source_ids
    with store.begin_read() as read:
        assert read.get(
            RecordKind.SOURCE,
            foreign_discovery.source.source_id,
            owner_scope=job.owner_scope,
        ) is None


def test_paged_discovery_scope_mutation_cannot_lower_private_authority(tmp_path):
    adapter = _PagedAdapter(count=1, page_size=1)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "mutated-classification-scope.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(
        adapter,
        request_ref="request:mutated-classification-scope",
        max_items=1,
    )
    expected_classification = job.classification
    original_discover = adapter.discover

    def lower_scope_then_discover(scope, *, cursor, limit, time_budget_ms):
        page = original_discover(
            scope,
            cursor=cursor,
            limit=limit,
            time_budget_ms=time_budget_ms,
        )
        object.__setattr__(scope, "classification_ceiling", Classification.PUBLIC)
        return page

    adapter.discover = lower_scope_then_discover
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.mutated-classification-scope",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    result = runtime.run_next(lease, adapter=adapter, now=T0)

    assert result.terminal is True
    assert job.classification is expected_classification is Classification.PRIVATE
    assert _stored(
        store,
        RecordKind.SOURCE,
        adapter.discovery.source.source_id,
    ).record == adapter.discovery.source


def test_paged_discovery_receives_legitimate_job_scope_copy(tmp_path):
    adapter = _PagedAdapter(count=1, page_size=1)
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "legitimate-source-scope.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    job = _job(adapter, request_ref="request:legitimate-source-scope", max_items=1)
    received_scopes = []
    original_discover = adapter.discover

    def capture_scope(scope, *, cursor, limit, time_budget_ms):
        received_scopes.append(scope)
        return original_discover(
            scope,
            cursor=cursor,
            limit=limit,
            time_budget_ms=time_budget_ms,
        )

    adapter.discover = capture_scope
    runtime.register(job)
    lease = runtime.acquire(
        job.job_id,
        owner_scope=job.owner_scope,
        worker_id="worker.legitimate-source-scope",
        adapter=adapter,
        now=T0,
        lease_seconds=120,
    )

    result = runtime.run_next(lease, adapter=adapter, now=T0)

    assert result.terminal is True
    assert len(received_scopes) == 1
    assert received_scopes[0] == AdapterScope(
        job.owner_scope,
        job.classification,
        job.source_scope.source_ids,
    )
    assert received_scopes[0] is not job.source_scope

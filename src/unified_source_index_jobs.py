"""Leased, resumable and idempotent Unified Source Index job runtime.

The runtime coordinates one bounded discovery item per step.  Source truth is
committed before rebuildable projections; projection failure is recorded as a
stale checkpoint and never rolls source/version/chunk truth back.  Durable job
state lives in the existing ``IndexJobRecord.cursor`` envelope, so USI-04 does
not introduce a second scheduler store or migration.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
import re
import time
from typing import Protocol, cast, runtime_checkable

from src.unified_source_index_adapters import (
    AdapterCapability,
    AdapterScope,
    BudgetedPagedSourceAdapter,
    DiscoveryItem,
    DiscoveryPage,
    ExtractionProfile,
    ExtractionPageBudget,
    ExtractionResult,
    MAX_ADAPTER_CURSOR_CHARS,
    MAX_ADAPTER_PAGE_SIZE,
    MAX_ADAPTER_WARNINGS,
    MAX_PAGED_EXTRACTION_PAGE_RECORDS,
    MAX_PAGED_EXTRACTION_PAGES,
    MAX_PAGED_EXTRACTION_RECORDS,
    MAX_TOTAL_EXTRACTION_CHARS,
    PagedExtractionPage,
    PagedSourceAdapter,
    SourceAdapter,
    SourceAdapterError,
    UnavailableObservation,
    _capture_exact_contract_value,
    _detach_exact_record,
    validate_adapter_output,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    DerivedRunRecord,
    EntityRecord,
    IndexJobRecord,
    JobStatus,
    LineageRecord,
    ProjectionManifest,
    ContentPolicy,
    RecordKind,
    RecordRef,
    RelationRecord,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    UnifiedSourceIndexContractError,
    canonical_json,
    record_from_json,
)
from src.unified_source_index_code_coverage import CoverageManifest
from src.unified_source_index_stores import (
    InMemoryStoreWriteTransaction,
    MAX_PAGE_SIZE,
    StoreRecord,
    StoreSnapshot,
    StoreTombstoneError,
    StoredRecord,
    TombstoneRecord,
    TransactionalStore,
    _owner_scope,
    _record_descriptor,
)


MAX_JOB_CHECKPOINT_CHARS = 1_024
MAX_JOB_STEPS = 1_000
MAX_LEASE_SECONDS = 3_600
MAX_EXTRACTION_CHARS = 1_000_000

_WORKER_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class IndexJobRuntimeError(RuntimeError):
    """Base error for invalid or unsafe job runtime operations."""


class IndexJobLeaseConflict(IndexJobRuntimeError):
    """Raised when another worker owns a live lease or a lease is stale."""


class IndexJobTerminalError(IndexJobRuntimeError):
    """Raised when a completed or cancelled job cannot be resumed."""


class IndexJobExecutionError(IndexJobRuntimeError):
    """Raised after a bounded job step is durably marked failed."""

    def __init__(self, message: str, *, job: IndexJobRecord) -> None:
        super().__init__(message)
        self.job = job


class JobStage(StrEnum):
    DISCOVERY = "discovery"
    EXTRACTION = "extraction"
    TRUTH_COMMIT = "truth_commit"
    PROJECTION = "projection"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class JobCheckpoint:
    job_id: str
    adapter_id: str
    adapter_version: str
    stage: JobStage
    discovery_cursor: str = ""
    items_completed: int = 0
    lease_owner: str = ""
    lease_expires_at: str = ""
    truth_snapshot_ref: str = ""
    projection_stale: bool = False
    error_code: str = ""

    def __post_init__(self) -> None:
        job_ref = RecordRef(RecordKind.JOB, self.job_id)
        object.__setattr__(self, "job_id", job_ref.record_id)
        object.__setattr__(self, "adapter_id", _token(self.adapter_id, "adapter_id"))
        object.__setattr__(
            self,
            "adapter_version",
            _token(self.adapter_version, "adapter_version"),
        )
        object.__setattr__(self, "stage", _enum(self.stage, JobStage, "stage"))
        if not isinstance(self.discovery_cursor, str) or len(self.discovery_cursor) > 768:
            raise IndexJobRuntimeError("discovery_cursor is invalid or unbounded")
        object.__setattr__(
            self,
            "items_completed",
            _bounded_integer(
                self.items_completed,
                "items_completed",
                minimum=0,
                maximum=1_000_000,
            ),
        )
        if self.lease_owner:
            object.__setattr__(self, "lease_owner", _worker(self.lease_owner))
            _timestamp(self.lease_expires_at, "lease_expires_at")
        elif self.lease_expires_at:
            raise IndexJobRuntimeError("lease expiry requires a lease owner")
        if self.truth_snapshot_ref and not re.fullmatch(
            r"usi_snapshot_[0-9a-f]{64}", self.truth_snapshot_ref
        ):
            raise IndexJobRuntimeError("truth_snapshot_ref is invalid")
        if not isinstance(self.projection_stale, bool):
            raise IndexJobRuntimeError("projection_stale must be boolean")
        if self.error_code:
            object.__setattr__(self, "error_code", _error_code(self.error_code))

    def lease_is_active(self, now: str) -> bool:
        return bool(
            self.lease_owner
            and _parse_timestamp(self.lease_expires_at)
            > _parse_timestamp(_timestamp(now, "now"))
        )


@dataclass(frozen=True, slots=True)
class JobLease:
    job: IndexJobRecord
    record_revision: int
    checkpoint: JobCheckpoint

    def __post_init__(self) -> None:
        if not isinstance(self.job, IndexJobRecord):
            raise IndexJobRuntimeError("lease job must be typed")
        if isinstance(self.record_revision, bool) or self.record_revision < 1:
            raise IndexJobRuntimeError("lease record revision must be positive")
        if not isinstance(self.checkpoint, JobCheckpoint):
            raise IndexJobRuntimeError("lease checkpoint must be typed")
        if self.job.job_id != self.checkpoint.job_id:
            raise IndexJobRuntimeError("lease checkpoint belongs to another job")


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    truth_snapshot: StoreSnapshot
    discovery: DiscoveryItem
    extraction: ExtractionResult

    def __post_init__(self) -> None:
        if not isinstance(self.truth_snapshot, StoreSnapshot):
            raise IndexJobRuntimeError("projection truth snapshot must be typed")
        if not isinstance(self.discovery, DiscoveryItem) or not isinstance(
            self.extraction, ExtractionResult
        ):
            raise IndexJobRuntimeError("projection input must be typed")


@runtime_checkable
class ProjectionSink(Protocol):
    def project(self, request: ProjectionRequest) -> ProjectionManifest: ...


@dataclass(frozen=True, slots=True)
class JobStepResult:
    lease: JobLease
    truth_snapshot: StoreSnapshot
    projection_manifest: ProjectionManifest | None = None

    @property
    def terminal(self) -> bool:
        return self.lease.job.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class PlannedTombstone:
    record_ref: RecordRef
    record_revision: int
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.record_ref, RecordRef):
            raise IndexJobRuntimeError("planned tombstone ref must be typed")
        if isinstance(self.record_revision, bool) or self.record_revision < 1:
            raise IndexJobRuntimeError("planned tombstone revision must be positive")
        object.__setattr__(self, "reason", _error_code(self.reason))


@dataclass(frozen=True, slots=True)
class TombstonePlan:
    source_ref: RecordRef
    items: tuple[PlannedTombstone, ...]
    clipped: bool

    def __post_init__(self) -> None:
        if not isinstance(self.source_ref, RecordRef) or self.source_ref.record_kind is not RecordKind.SOURCE:
            raise IndexJobRuntimeError("tombstone plan requires a source ref")
        if not isinstance(self.items, tuple) or len(self.items) > MAX_PAGE_SIZE:
            raise IndexJobRuntimeError("tombstone plan is unbounded")
        if not all(isinstance(item, PlannedTombstone) for item in self.items):
            raise IndexJobRuntimeError("tombstone plan contains an invalid item")
        if not isinstance(self.clipped, bool):
            raise IndexJobRuntimeError("tombstone plan clipped flag must be boolean")


class UnifiedSourceIndexJobRuntime:
    """One durable, cooperative USI ingestion coordinator."""

    def __init__(self, store: TransactionalStore) -> None:
        if not isinstance(store, TransactionalStore):
            raise IndexJobRuntimeError("store must implement TransactionalStore")
        self._store = store

    def register(self, job: IndexJobRecord) -> JobLease:
        if not isinstance(job, IndexJobRecord):
            raise IndexJobRuntimeError("job must be an IndexJobRecord")
        snapshot, current = self._load_job(job.job_id, job.owner_scope, required=False)
        if current is not None:
            if current.record != job:
                raise IndexJobRuntimeError("job identity is already registered differently")
            checkpoint = _decode_checkpoint(job.cursor, job, None)
            return JobLease(current.record, current.revision, checkpoint)
        write = self._store.begin_write(snapshot)
        write.put(job)
        write.commit()
        _, stored = self._load_job(job.job_id, job.owner_scope)
        assert stored is not None
        checkpoint = _decode_checkpoint(stored.record.cursor, stored.record, None)
        return JobLease(stored.record, stored.revision, checkpoint)

    def acquire(
        self,
        job_id: str,
        *,
        owner_scope: str,
        worker_id: str,
        adapter: SourceAdapter,
        now: str,
        lease_seconds: int = 60,
    ) -> JobLease:
        lease, _ = self._acquire(
            job_id,
            owner_scope=owner_scope,
            worker_id=worker_id,
            adapter=adapter,
            now=now,
            lease_seconds=lease_seconds,
        )
        return lease

    def _acquire(
        self,
        job_id: str,
        *,
        owner_scope: str,
        worker_id: str,
        adapter: SourceAdapter,
        now: str,
        lease_seconds: int = 60,
    ) -> tuple[JobLease, StoreSnapshot | None]:
        owner = _owner_scope(owner_scope)
        worker = _worker(worker_id)
        normalized_now = _timestamp(now, "now")
        duration = _bounded_integer(
            lease_seconds,
            "lease_seconds",
            minimum=1,
            maximum=MAX_LEASE_SECONDS,
        )
        capability = _capability(adapter)
        snapshot, stored = self._load_job(job_id, owner)
        assert stored is not None
        job = stored.record
        _validate_job_capability(job, capability)
        checkpoint = _decode_checkpoint(job.cursor, job, capability)
        if checkpoint.stage is JobStage.TRUTH_COMMIT and job.status is JobStatus.RUNNING:
            if capability.adapter_id == "forge.code":
                raise IndexJobExecutionError(
                    "coverage_publication_required", job=job
                )
            recovered = self._finalize_truth_commit(
                JobLease(job, stored.revision, checkpoint),
                capability,
                normalized_now,
                truth_snapshot=snapshot,
            )
            if recovered.lease.job.status is JobStatus.COMPLETED:
                return recovered.lease, recovered.truth_snapshot
            snapshot, stored = self._load_job(job_id, owner)
            assert stored is not None
            job = stored.record
            checkpoint = _decode_checkpoint(job.cursor, job, capability)
        if job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED}:
            raise IndexJobTerminalError(f"job is already {job.status.value}")
        if (
            job.status is JobStatus.RUNNING
            and checkpoint.lease_is_active(normalized_now)
            and checkpoint.lease_owner != worker
        ):
            raise IndexJobLeaseConflict("job already has a live lease")
        same_lease = (
            job.status is JobStatus.RUNNING
            and checkpoint.lease_owner == worker
            and checkpoint.lease_is_active(normalized_now)
        )
        expiry = _format_timestamp(
            _parse_timestamp(normalized_now) + timedelta(seconds=duration)
        )
        leased_checkpoint = replace(
            checkpoint,
            lease_owner=worker,
            lease_expires_at=expiry,
            error_code="",
        )
        leased_job = replace(
            job,
            status=JobStatus.RUNNING,
            cursor=_encode_checkpoint(leased_checkpoint),
            attempt_count=job.attempt_count if same_lease else job.attempt_count + 1,
            started_at=job.started_at or normalized_now,
            completed_at="",
        )
        return self._replace_job(snapshot, stored, leased_job, capability), None

    def cancel(
        self,
        job_id: str,
        *,
        owner_scope: str,
        now: str,
        reason: str = "cancelled_by_operator",
    ) -> JobLease:
        owner = _owner_scope(owner_scope)
        normalized_now = _timestamp(now, "now")
        snapshot, stored = self._load_job(job_id, owner)
        assert stored is not None
        job = stored.record
        if job.status is JobStatus.COMPLETED:
            raise IndexJobTerminalError("completed job cannot be cancelled")
        checkpoint = _decode_checkpoint(job.cursor, job, None)
        cancelled = replace(
            checkpoint,
            stage=JobStage.CANCELLED,
            truth_snapshot_ref=(
                snapshot.snapshot_ref
                if checkpoint.stage is JobStage.TRUTH_COMMIT
                else checkpoint.truth_snapshot_ref
            ),
            lease_owner="",
            lease_expires_at="",
            error_code=_error_code(reason),
        )
        cancelled_job = replace(
            job,
            status=JobStatus.CANCELLED,
            cursor=_encode_checkpoint(cancelled),
            started_at=job.started_at or normalized_now,
            completed_at=normalized_now,
        )
        return self._replace_job(snapshot, stored, cancelled_job, None)

    def run_next(
        self,
        lease: JobLease,
        *,
        adapter: SourceAdapter,
        now: str,
        projection: ProjectionSink | None = None,
    ) -> JobStepResult:
        if not isinstance(lease, JobLease):
            raise IndexJobRuntimeError("lease must be typed")
        normalized_now = _timestamp(now, "now")
        capability = _capability(adapter)
        _validate_job_capability(lease.job, capability)
        current = self._require_current_lease(lease, capability, normalized_now)
        if current.checkpoint.stage is JobStage.TRUTH_COMMIT:
            if capability.adapter_id == "forge.code":
                raise IndexJobExecutionError(
                    "coverage_publication_required", job=current.job
                )
            return self._finalize_truth_commit(current, capability, normalized_now)

        job = current.job
        checkpoint = current.checkpoint
        paged = bool(capability.required_job_profile_ref)
        if paged and projection is not None:
            failed = self._fail_paged_job(
                current,
                capability,
                normalized_now,
                stage=JobStage.EXTRACTION,
                error_code="paged_projection_unsupported",
                projection_stale=False,
                truth_snapshot_ref=checkpoint.truth_snapshot_ref,
            )
            raise IndexJobExecutionError(
                "paged extraction does not support legacy projection",
                job=failed.job,
            )

        scope = AdapterScope(
            job.owner_scope,
            job.classification,
            job.source_scope.source_ids,
        )
        started = time.monotonic()
        deadline = started + (job.time_budget_ms / 1_000)
        try:
            discovery_budget = (
                _remaining_time_budget_ms(deadline) if paged else job.time_budget_ms
            )
            discovery_scope = (
                AdapterScope(
                    scope.owner_scope,
                    scope.classification_ceiling,
                    scope.source_ids,
                )
                if paged
                else scope
            )
            raw_page = adapter.discover(
                discovery_scope,
                cursor=checkpoint.discovery_cursor,
                limit=1,
                time_budget_ms=discovery_budget,
            )
            page = _detach_paged_discovery_page(raw_page) if paged else raw_page
            if len(page.items) > 1:
                raise SourceAdapterError("job discovery returned more than one item")
            if not page.items:
                if page.next_cursor:
                    raise SourceAdapterError("empty discovery page cannot advance a cursor")
                if paged:
                    return self._finish_paged_empty(
                        current,
                        capability,
                        normalized_now,
                        started=started,
                        deadline=deadline,
                    )
                completed = self._finish_job(
                    current,
                    capability,
                    normalized_now,
                    truth_snapshot=self._store.current_snapshot(),
                )
                return JobStepResult(completed, self._store.current_snapshot())

            discovery = page.items[0]
            raw_observed = adapter.observe_version(discovery.source.ref())
            if paged:
                if type(raw_observed) is UnavailableObservation:
                    raise SourceAdapterError("source_unavailable_requires_tombstone_plan")
                detached_observed = cast(
                    SourceVersionRecord,
                    _detach_exact_record(raw_observed, (SourceVersionRecord,)),
                )
                observed_authority = _source_version_authority_bytes(
                    detached_observed
                )
                records, precommit = self._extract_paged_records(
                    current,
                    adapter=cast(PagedSourceAdapter, adapter),
                    capability=capability,
                    scope=scope,
                    discovery=discovery,
                    observed_authority=observed_authority,
                    normalized_now=normalized_now,
                    started=started,
                    deadline=deadline,
                )
                observed = _source_version_from_authority(observed_authority)
            else:
                if isinstance(raw_observed, UnavailableObservation):
                    raise SourceAdapterError("source_unavailable_requires_tombstone_plan")
                observed = raw_observed
                profile = ExtractionProfile(
                    job.profile_ref,
                    max_items=min(job.max_items, capability.max_extract_items),
                    max_chars=min(MAX_EXTRACTION_CHARS, capability.max_total_extract_chars),
                    time_budget_ms=job.time_budget_ms,
                )
                extraction = adapter.extract(observed, profile)
                validate_adapter_output(
                    capability,
                    scope,
                    discovery,
                    observed,
                    extraction,
                )
        except IndexJobLeaseConflict:
            raise
        except Exception as exc:
            fail = self._fail_paged_job if paged else self._fail_job
            failed = fail(
                current,
                capability,
                normalized_now,
                stage=JobStage.EXTRACTION,
                error_code=_exception_code(exc, "adapter_error"),
                projection_stale=False,
                truth_snapshot_ref=checkpoint.truth_snapshot_ref,
            )
            raise IndexJobExecutionError("adapter job step failed", job=failed.job) from exc

        item_budget_exhausted = (
            bool(page.next_cursor)
            and precommit.checkpoint.items_completed + 1 >= job.max_items
        ) if paged else False
        completed = not page.next_cursor or item_budget_exhausted
        if paged:
            try:
                truth_snapshot, truth_lease = self._commit_paged_truth(
                    precommit,
                    capability=capability,
                    discovery=discovery,
                    observed=observed,
                    records=records,
                    discovery_cursor=page.next_cursor,
                    item_budget_exhausted=item_budget_exhausted,
                    normalized_now=normalized_now,
                    started=started,
                    deadline=deadline,
                )
            except IndexJobLeaseConflict:
                raise
            except Exception as exc:
                _, stored = self._load_job(job.job_id, job.owner_scope)
                assert stored is not None
                stored_checkpoint = _decode_checkpoint(
                    stored.record.cursor, stored.record, capability
                )
                if (
                    stored.record.status is JobStatus.RUNNING
                    and stored.revision != precommit.record_revision
                    and stored_checkpoint.stage is JobStage.TRUTH_COMMIT
                ):
                    raise IndexJobExecutionError(
                        "paged truth committed; finalization remains pending",
                        job=stored.record,
                    ) from exc
                failed = self._fail_paged_job(
                    precommit,
                    capability,
                    normalized_now,
                    stage=JobStage.EXTRACTION,
                    error_code=_exception_code(exc, "truth_commit_error"),
                    projection_stale=False,
                    truth_snapshot_ref=precommit.checkpoint.truth_snapshot_ref,
                )
                raise IndexJobExecutionError("paged truth commit failed", job=failed.job) from exc
            try:
                return self._finalize_truth_commit(
                    truth_lease,
                    capability,
                    normalized_now,
                    truth_snapshot=truth_snapshot,
                )
            except IndexJobLeaseConflict:
                raise
            except Exception as exc:
                _, stored = self._load_job(job.job_id, job.owner_scope)
                assert stored is not None
                raise IndexJobExecutionError(
                    "paged truth committed; finalization remains pending",
                    job=stored.record,
                ) from exc

        truth_snapshot = self._persist_records(
            (discovery.source, observed, *extraction.records)
        )
        manifest: ProjectionManifest | None = None
        if projection is not None:
            if not isinstance(projection, ProjectionSink):
                failed = self._fail_job(
                    self._reload_lease(current, capability),
                    capability,
                    normalized_now,
                    stage=JobStage.PROJECTION,
                    error_code="invalid_projection_sink",
                    projection_stale=True,
                    truth_snapshot_ref=truth_snapshot.snapshot_ref,
                )
                raise IndexJobExecutionError(
                    "projection sink is invalid",
                    job=failed.job,
                )
            try:
                manifest = projection.project(
                    ProjectionRequest(truth_snapshot, discovery, extraction)
                )
                _validate_projection_manifest(manifest, truth_snapshot, extraction)
                truth_snapshot = self._persist_records((manifest,))
            except Exception as exc:
                failed = self._fail_job(
                    self._reload_lease(current, capability),
                    capability,
                    normalized_now,
                    stage=JobStage.PROJECTION,
                    error_code=_exception_code(exc, "projection_error"),
                    projection_stale=True,
                    truth_snapshot_ref=truth_snapshot.snapshot_ref,
                )
                raise IndexJobExecutionError(
                    "projection failed after truth commit",
                    job=failed.job,
                ) from exc

        reloaded = self._reload_lease(current, capability)
        item_budget_exhausted = (
            bool(page.next_cursor)
            and reloaded.checkpoint.items_completed + 1 >= job.max_items
        )
        completed = not page.next_cursor or item_budget_exhausted
        advanced = replace(
            reloaded.checkpoint,
            stage=JobStage.COMPLETE if completed else JobStage.DISCOVERY,
            discovery_cursor=page.next_cursor,
            items_completed=reloaded.checkpoint.items_completed + 1,
            truth_snapshot_ref=truth_snapshot.snapshot_ref,
            projection_stale=False,
            error_code="item_budget_exhausted" if item_budget_exhausted else "",
            lease_owner="" if completed else reloaded.checkpoint.lease_owner,
            lease_expires_at="" if completed else reloaded.checkpoint.lease_expires_at,
        )
        updated_job = replace(
            reloaded.job,
            status=JobStatus.COMPLETED if completed else JobStatus.RUNNING,
            cursor=_encode_checkpoint(advanced),
            completed_at=normalized_now if completed else "",
        )
        snapshot, stored = self._load_job(
            updated_job.job_id,
            updated_job.owner_scope,
        )
        assert stored is not None
        updated = self._replace_job(snapshot, stored, updated_job, capability)
        return JobStepResult(updated, truth_snapshot, manifest)

    def run_to_completion(
        self,
        job_id: str,
        *,
        owner_scope: str,
        worker_id: str,
        adapter: SourceAdapter,
        now: str,
        projection: ProjectionSink | None = None,
        lease_seconds: int = 60,
        max_steps: int = 100,
    ) -> JobStepResult:
        step_limit = _bounded_integer(
            max_steps,
            "max_steps",
            minimum=1,
            maximum=MAX_JOB_STEPS,
        )
        lease, recovered_truth_snapshot = self._acquire(
            job_id,
            owner_scope=owner_scope,
            worker_id=worker_id,
            adapter=adapter,
            now=now,
            lease_seconds=lease_seconds,
        )
        result = JobStepResult(
            lease,
            recovered_truth_snapshot or self._store.current_snapshot(),
        )
        if result.terminal:
            return result
        for _ in range(step_limit):
            result = self.run_next(
                result.lease,
                adapter=adapter,
                now=now,
                projection=projection,
            )
            if result.terminal:
                return result
        raise IndexJobRuntimeError("job did not finish within max_steps")

    def _finish_paged_empty(
        self,
        lease: JobLease,
        capability: AdapterCapability,
        now: str,
        *,
        started: float,
        deadline: float,
    ) -> JobStepResult:
        snapshot, stored = self._load_job(lease.job.job_id, lease.job.owner_scope)
        assert stored is not None
        checkpoint = _decode_checkpoint(stored.record.cursor, stored.record, capability)
        if (
            stored.revision != lease.record_revision
            or stored.record != lease.job
            or stored.record.status is not JobStatus.RUNNING
            or checkpoint.lease_owner != lease.checkpoint.lease_owner
        ):
            raise IndexJobLeaseConflict(
                "job changed before paged empty completion"
            )
        completed_checkpoint = replace(
            checkpoint,
            stage=JobStage.COMPLETE,
            lease_owner="",
            lease_expires_at="",
            truth_snapshot_ref=snapshot.snapshot_ref,
            projection_stale=False,
            error_code="",
        )
        completed_job = replace(
            stored.record,
            status=JobStatus.COMPLETED,
            cursor=_encode_checkpoint(completed_checkpoint),
            completed_at=now,
        )
        write = self._store.begin_write(snapshot)
        try:
            write.compare_and_swap(
                completed_job,
                expected_record_revision=stored.revision,
            )
            _require_fresh_paged_commit_time(
                JobLease(stored.record, stored.revision, checkpoint),
                normalized_now=now,
                started=started,
                deadline=deadline,
            )
        except BaseException:
            write.rollback()
            raise
        write.commit()
        _, updated = self._load_job(completed_job.job_id, completed_job.owner_scope)
        assert updated is not None
        updated_checkpoint = _decode_checkpoint(
            updated.record.cursor, updated.record, capability
        )
        return JobStepResult(
            JobLease(updated.record, updated.revision, updated_checkpoint),
            snapshot,
        )

    def _extract_paged_records(
        self,
        lease: JobLease,
        *,
        adapter: PagedSourceAdapter,
        capability: AdapterCapability,
        scope: AdapterScope,
        discovery: DiscoveryItem,
        observed_authority: bytes,
        normalized_now: str,
        started: float,
        deadline: float,
    ) -> tuple[tuple[ChunkRecord | EntityRecord | RelationRecord, ...], JobLease]:
        records: list[ChunkRecord | EntityRecord | RelationRecord] = []
        seen_keys: set[tuple[str, str]] = set()
        seen_cursors = {""}
        cursor = ""
        last_key: tuple[str, str] | None = None
        inline_chars = 0
        pages = 0
        current = lease
        while True:
            remaining_records = MAX_PAGED_EXTRACTION_RECORDS - len(records)
            if remaining_records < 1:
                raise SourceAdapterError("paged extraction exceeds aggregate record bound")
            total_char_limit = capability.max_total_extract_chars
            remaining_chars = total_char_limit - inline_chars
            requested_page_max_items = min(
                MAX_PAGED_EXTRACTION_PAGE_RECORDS,
                capability.max_extract_items,
                remaining_records,
            )
            budgeted = capability.supports_budgeted_paging
            if budgeted:
                profile = ExtractionProfile(
                    lease.job.profile_ref,
                    max_items=min(
                        MAX_PAGED_EXTRACTION_PAGE_RECORDS,
                        capability.max_extract_items,
                    ),
                    max_chars=min(MAX_EXTRACTION_CHARS, total_char_limit),
                    time_budget_ms=min(lease.job.time_budget_ms, 86_400_000),
                )
                requested_page_max_items = min(
                    profile.max_items,
                    remaining_records,
                )
                page_budget = ExtractionPageBudget(
                    max_items=requested_page_max_items,
                    max_chars=min(
                        profile.max_chars,
                        remaining_chars,
                    ),
                    time_budget_ms=min(
                        profile.time_budget_ms,
                        _remaining_time_budget_ms(deadline),
                    ),
                )
                profile_authority = (
                    profile.profile_ref,
                    profile.max_items,
                    profile.max_chars,
                    profile.time_budget_ms,
                )
                budget_authority = (
                    page_budget.max_items,
                    page_budget.max_chars,
                    page_budget.time_budget_ms,
                )
            else:
                profile = ExtractionProfile(
                    lease.job.profile_ref,
                    max_items=requested_page_max_items,
                    max_chars=max(1, remaining_chars),
                    time_budget_ms=_remaining_time_budget_ms(deadline),
                )
                page_budget = None
                profile_authority = ()
                budget_authority = ()
            page_input_version = _source_version_from_authority(
                observed_authority
            )
            if budgeted:
                if not isinstance(adapter, BudgetedPagedSourceAdapter):
                    raise SourceAdapterError("budgeted paged adapter protocol is missing")
                raw_page = adapter.extract_page_budgeted(
                    page_input_version,
                    profile,
                    page_budget,
                    cursor=cursor,
                    sequence_start=len(records),
                )
            else:
                raw_page = adapter.extract_page(
                    page_input_version,
                    profile,
                    cursor=cursor,
                    sequence_start=len(records),
                )
            if budgeted and (
                type(profile) is not ExtractionProfile
                or (
                    profile.profile_ref,
                    profile.max_items,
                    profile.max_chars,
                    profile.time_budget_ms,
                ) != profile_authority
                or type(profile.profile_ref) is not str
                or type(profile.max_items) is not int
                or type(profile.max_chars) is not int
                or type(profile.time_budget_ms) is not int
                or type(page_budget) is not ExtractionPageBudget
                or (
                    page_budget.max_items,
                    page_budget.max_chars,
                    page_budget.time_budget_ms,
                ) != budget_authority
                or type(page_budget.max_items) is not int
                or type(page_budget.max_chars) is not int
                or type(page_budget.time_budget_ms) is not int
            ):
                raise SourceAdapterError("paged extraction request budget mutated")
            _remaining_time_budget_ms(deadline)
            page = _detach_paged_extraction_page(raw_page)
            if len(page.records) > requested_page_max_items:
                raise SourceAdapterError(
                    "paged extraction returned more records than requested"
                )
            if _source_version_authority_bytes(page.source_version) != observed_authority:
                raise SourceAdapterError("paged extraction source version drifted")
            if page.sequence_start != len(records):
                raise SourceAdapterError("paged extraction sequence_start drifted")
            temporary = ExtractionResult(
                page.source_version,
                chunks=tuple(record for record in page.records if type(record) is ChunkRecord),
                entities=tuple(record for record in page.records if type(record) is EntityRecord),
                relations=tuple(record for record in page.records if type(record) is RelationRecord),
                warnings=page.warnings,
            )
            validation_version = _source_version_from_authority(
                observed_authority
            )
            validate_adapter_output(
                capability, scope, discovery, validation_version, temporary
            )
            page_inline_chars_before = inline_chars
            for record in page.records:
                key = _paged_record_key(record)
                if key in seen_keys:
                    raise SourceAdapterError("paged extraction repeats a record identity")
                if last_key is not None and key <= last_key:
                    raise SourceAdapterError("paged extraction records are not globally ordered")
                seen_keys.add(key)
                last_key = key
                records.append(record)
                if type(record) is ChunkRecord and record.content is not None:
                    inline_chars += len(record.content)
                    if inline_chars > total_char_limit:
                        raise SourceAdapterError("paged extraction exceeds inline character bound")
                    if budgeted and inline_chars - page_inline_chars_before > page_budget.max_chars:
                        raise SourceAdapterError("paged extraction exceeds requested page character budget")
            pages += 1
            if len(records) > MAX_PAGED_EXTRACTION_RECORDS:
                raise SourceAdapterError("paged extraction exceeds aggregate record bound")
            current = self._require_current_lease(
                lease,
                capability,
                _mapped_now(normalized_now, started),
            )
            if page.next_cursor:
                if pages >= MAX_PAGED_EXTRACTION_PAGES:
                    raise SourceAdapterError("paged extraction exceeds page bound")
                if len(records) >= MAX_PAGED_EXTRACTION_RECORDS:
                    raise SourceAdapterError("paged extraction cannot advance at aggregate bound")
                if page.next_cursor == cursor or page.next_cursor in seen_cursors:
                    raise SourceAdapterError("paged extraction cursor repeats")
                seen_cursors.add(page.next_cursor)
                cursor = page.next_cursor
                continue
            if page.final_record_count != len(records):
                raise SourceAdapterError("paged extraction final record count does not match")
            if page.final_records_digest != _paged_records_digest(tuple(records)):
                raise SourceAdapterError("paged extraction final record digest does not match")
            counts = {kind: 0 for kind in (RecordKind.CHUNK, RecordKind.ENTITY, RecordKind.RELATION)}
            for record in records:
                kind, _, _ = _record_descriptor(record)
                counts[kind] += 1
                if counts[kind] > MAX_PAGED_EXTRACTION_RECORDS:
                    raise SourceAdapterError("paged extraction kind count is unbounded")
            if sum(counts.values()) != len(records):
                raise SourceAdapterError("paged extraction kind counts do not sum")
            _remaining_time_budget_ms(deadline)
            current = self._require_current_lease(
                lease,
                capability,
                _mapped_now(normalized_now, started),
            )
            return tuple(records), current

    def _commit_paged_truth(
        self,
        lease: JobLease,
        *,
        capability: AdapterCapability,
        discovery: DiscoveryItem,
        observed: SourceVersionRecord,
        records: tuple[ChunkRecord | EntityRecord | RelationRecord, ...],
        discovery_cursor: str,
        item_budget_exhausted: bool,
        normalized_now: str,
        started: float,
        deadline: float,
    ) -> tuple[StoreSnapshot, JobLease]:
        snapshot, stored = self._load_job(lease.job.job_id, lease.job.owner_scope)
        assert stored is not None
        if stored.revision != lease.record_revision or stored.record != lease.job:
            raise IndexJobLeaseConflict("job changed before paged truth commit")
        truth_checkpoint = replace(
            lease.checkpoint,
            stage=JobStage.TRUTH_COMMIT,
            discovery_cursor=discovery_cursor,
            items_completed=lease.checkpoint.items_completed + 1,
            projection_stale=False,
            error_code="item_budget_exhausted" if item_budget_exhausted else "",
        )
        truth_job = replace(
            lease.job,
            status=JobStatus.RUNNING,
            cursor=_encode_checkpoint(truth_checkpoint),
            completed_at="",
        )
        candidates = (discovery.source, observed, *records)
        target_keys: set[tuple[RecordKind, str, str]] = set()
        groups: set[tuple[RecordKind, str]] = set()
        for record in candidates:
            kind, record_id, owner = _record_descriptor(record)
            target_keys.add((kind, record_id, owner))
            groups.add((kind, owner))
        existing_by_key: dict[
            tuple[RecordKind, str, str], StoredRecord | TombstoneRecord
        ] = {}
        with self._store.begin_read(snapshot) as read:
            for kind, owner in sorted(groups, key=lambda item: (item[0].value, item[1])):
                cursor = ""
                while True:
                    _remaining_time_budget_ms(deadline)
                    page = read.list_records(
                        kind,
                        owner_scope=owner,
                        limit=MAX_PAGE_SIZE,
                        cursor=cursor,
                        include_tombstones=True,
                    )
                    for existing in page.items:
                        key = (
                            existing.record_kind,
                            existing.record_id,
                            existing.owner_scope,
                        )
                        if key in target_keys:
                            existing_by_key[key] = existing
                    if not page.next_cursor:
                        break
                    cursor = page.next_cursor
        pending: list[tuple[StoreRecord, int]] = []
        for record in candidates:
            kind, record_id, owner = _record_descriptor(record)
            existing = existing_by_key.get((kind, record_id, owner))
            if isinstance(existing, TombstoneRecord):
                raise StoreTombstoneError(
                    "job output targets a tombstoned record identity"
                )
            if isinstance(existing, StoredRecord):
                if existing.record == record:
                    continue
                pending.append((cast(StoreRecord, record), existing.revision))
            else:
                pending.append((cast(StoreRecord, record), 0))
        write = self._store.begin_write(snapshot)
        try:
            if type(write) is InMemoryStoreWriteTransaction:
                write.put_many_paged_truth(tuple(pending))
            else:
                for record, expected_revision in pending:
                    write.put(record, expected_record_revision=expected_revision)
            write.compare_and_swap(
                truth_job, expected_record_revision=stored.revision
            )
            checked = self._require_current_lease(
                lease,
                capability,
                _mapped_now(normalized_now, started),
            )
            _require_fresh_paged_commit_time(
                checked,
                normalized_now=normalized_now,
                started=started,
                deadline=deadline,
            )
        except BaseException:
            write.rollback()
            raise
        truth_snapshot = write.commit()
        _, updated = self._load_job(truth_job.job_id, truth_job.owner_scope)
        assert updated is not None
        updated_checkpoint = _decode_checkpoint(
            updated.record.cursor, updated.record, capability
        )
        return truth_snapshot, JobLease(
            updated.record, updated.revision, updated_checkpoint
        )

    def _finalize_truth_commit(
        self,
        lease: JobLease,
        capability: AdapterCapability,
        now: str,
        *,
        truth_snapshot: StoreSnapshot | None = None,
    ) -> JobStepResult:
        if capability.adapter_id == "forge.code":
            raise IndexJobExecutionError(
                "coverage_publication_required", job=lease.job
            )
        snapshot, stored = self._load_job(lease.job.job_id, lease.job.owner_scope)
        assert stored is not None
        checkpoint = _decode_checkpoint(stored.record.cursor, stored.record, capability)
        if (
            stored.revision != lease.record_revision
            or stored.record.status is not JobStatus.RUNNING
            or checkpoint.stage is not JobStage.TRUTH_COMMIT
        ):
            raise IndexJobLeaseConflict("paged truth finalization lost its job lease")
        if truth_snapshot is not None and snapshot != truth_snapshot:
            raise IndexJobLeaseConflict("paged truth snapshot changed before finalization")
        completed = (
            not checkpoint.discovery_cursor
            or checkpoint.error_code == "item_budget_exhausted"
        )
        finalized_checkpoint = replace(
            checkpoint,
            stage=JobStage.COMPLETE if completed else JobStage.DISCOVERY,
            truth_snapshot_ref=snapshot.snapshot_ref,
            lease_owner="" if completed else checkpoint.lease_owner,
            lease_expires_at="" if completed else checkpoint.lease_expires_at,
        )
        finalized_job = replace(
            stored.record,
            status=JobStatus.COMPLETED if completed else JobStatus.RUNNING,
            cursor=_encode_checkpoint(finalized_checkpoint),
            completed_at=now if completed else "",
        )
        updated = self._replace_job(snapshot, stored, finalized_job, capability)
        return JobStepResult(updated, snapshot)

    def finalize_selected_coverage(
        self,
        lease: JobLease,
        *,
        adapter: SourceAdapter,
        manifest: CoverageManifest,
        expected_ledger_head_digest: str,
        expected_selection_event_digest: str,
        now: str,
    ) -> JobStepResult:
        """Publish Forge coverage and terminal job state in one P transaction."""
        from src.unified_source_index_sqlite import (
            SQLiteStoreWriteTransaction,
            SQLiteUnifiedSourceIndexStore,
        )

        normalized_now = _timestamp(now, "now")
        capability = _capability(adapter)
        _validate_job_capability(lease.job, capability)
        if (
            capability.adapter_id != "forge.code"
            or type(manifest) is not CoverageManifest
            or not isinstance(self._store, SQLiteUnifiedSourceIndexStore)
        ):
            raise IndexJobRuntimeError("coverage_publication_required")
        canonical = CoverageManifest.from_canonical_bytes(manifest.canonical_bytes())
        attempt = canonical.attempt
        _latest_snapshot, latest_stored = self._load_job(
            lease.job.job_id, lease.job.owner_scope
        )
        assert latest_stored is not None
        if latest_stored.record.status is JobStatus.COMPLETED:
            try:
                persisted_original_checkpoint = _decode_checkpoint(
                    lease.job.cursor, lease.job, capability
                )
            except Exception:
                raise IndexJobExecutionError(
                    "terminal_job_mismatch", job=lease.job
                ) from None
            if (
                lease.job.status is not JobStatus.RUNNING
                or persisted_original_checkpoint != lease.checkpoint
                or lease.checkpoint.stage is not JobStage.TRUTH_COMMIT
                or lease.record_revision != attempt.store_revision
                or attempt.job_id != lease.job.job_id
                or attempt.adapter_id != capability.adapter_id
                or attempt.adapter_version != capability.adapter_version
                or lease.checkpoint.adapter_id != capability.adapter_id
                or lease.checkpoint.adapter_version != capability.adapter_version
                or canonical.authority_binding[:2]
                != (capability.adapter_id, capability.adapter_version)
            ):
                raise IndexJobExecutionError("terminal_job_mismatch", job=lease.job)
            completed_checkpoint = replace(
                lease.checkpoint,
                stage=JobStage.COMPLETE,
                lease_owner="",
                lease_expires_at="",
                error_code="",
            )
            completed_job = replace(
                lease.job,
                status=JobStatus.COMPLETED,
                cursor=_encode_checkpoint(completed_checkpoint),
                completed_at=latest_stored.record.completed_at,
            )
            try:
                replay_snapshot = self._store.verify_selected_coverage_replay(
                    canonical,
                    original_job=lease.job,
                    terminal_job=completed_job,
                    expected_ledger_head_digest=expected_ledger_head_digest,
                    expected_selection_event_digest=expected_selection_event_digest,
                    adapter_authority_binding=canonical.authority_binding,
                )
            except Exception:
                raise IndexJobExecutionError(
                    "terminal_job_mismatch", job=lease.job
                ) from None
            checkpoint = _decode_checkpoint(
                latest_stored.record.cursor, latest_stored.record, capability
            )
            return JobStepResult(
                JobLease(latest_stored.record, latest_stored.revision, checkpoint),
                replay_snapshot,
            )
        current = self._require_current_lease(lease, capability, normalized_now)
        if current.checkpoint.stage is not JobStage.TRUTH_COMMIT:
            raise IndexJobRuntimeError("coverage_publication_required")
        truth_snapshot = StoreSnapshot(
            attempt.store_revision,
            attempt.store_state_hash,
            attempt.store_record_count,
            attempt.store_tombstone_count,
            attempt.store_snapshot_ref,
        )
        if (
            attempt.job_id != current.job.job_id
            or attempt.adapter_id != capability.adapter_id
            or attempt.adapter_version != capability.adapter_version
            or attempt.adapter_id != current.checkpoint.adapter_id
            or attempt.adapter_version != current.checkpoint.adapter_version
            or attempt.store_revision != current.record_revision
        ):
            raise IndexJobExecutionError("terminal_job_mismatch", job=current.job)
        try:
            with self._store.begin_read(truth_snapshot):
                pass
            write_snapshot = self._store.current_snapshot()
        except Exception:
            raise IndexJobExecutionError("truth_snapshot_mismatch", job=current.job) from None
        completed_checkpoint = replace(
            current.checkpoint,
            stage=JobStage.COMPLETE,
            lease_owner="",
            lease_expires_at="",
            error_code="",
        )
        completed_job = replace(
            current.job,
            status=JobStatus.COMPLETED,
            cursor=_encode_checkpoint(completed_checkpoint),
            completed_at=normalized_now,
        )
        if (
            attempt.job_status is not JobStatus.COMPLETED
            or attempt.job_record_digest
            != "sha256:" + hashlib.sha256(completed_job.to_json().encode("utf-8")).hexdigest()
        ):
            raise IndexJobExecutionError("terminal_job_mismatch", job=current.job)
        write = self._store.begin_write(write_snapshot)
        if not isinstance(write, SQLiteStoreWriteTransaction):
            write.rollback()
            raise IndexJobRuntimeError("coverage_publication_required")
        try:
            write.compare_and_swap(
                completed_job,
                expected_record_revision=current.record_revision,
            )
            write.publish_selected_coverage(
                canonical,
                expected_ledger_head_digest=expected_ledger_head_digest,
                expected_selection_event_digest=expected_selection_event_digest,
            )
            checked = self._require_current_lease(lease, capability, normalized_now)
            if checked.record_revision != current.record_revision:
                raise IndexJobLeaseConflict("job changed before coverage publication")
        except BaseException:
            write.rollback()
            raise
        published_snapshot = write.commit()
        _, stored = self._load_job(completed_job.job_id, completed_job.owner_scope)
        assert stored is not None
        checkpoint = _decode_checkpoint(stored.record.cursor, stored.record, capability)
        return JobStepResult(JobLease(stored.record, stored.revision, checkpoint), published_snapshot)

    def _require_current_lease(
        self,
        lease: JobLease,
        capability: AdapterCapability,
        now: str,
    ) -> JobLease:
        _, stored = self._load_job(lease.job.job_id, lease.job.owner_scope)
        assert stored is not None
        checkpoint = _decode_checkpoint(stored.record.cursor, stored.record, capability)
        if (
            stored.record.status is not JobStatus.RUNNING
            or stored.revision != lease.record_revision
            or checkpoint.lease_owner != lease.checkpoint.lease_owner
            or not checkpoint.lease_is_active(now)
        ):
            raise IndexJobLeaseConflict("job lease is stale, cancelled or expired")
        return JobLease(stored.record, stored.revision, checkpoint)

    def _reload_lease(
        self,
        lease: JobLease,
        capability: AdapterCapability,
    ) -> JobLease:
        _, stored = self._load_job(lease.job.job_id, lease.job.owner_scope)
        assert stored is not None
        checkpoint = _decode_checkpoint(stored.record.cursor, stored.record, capability)
        if (
            stored.record.status is not JobStatus.RUNNING
            or checkpoint.lease_owner != lease.checkpoint.lease_owner
        ):
            raise IndexJobLeaseConflict("job changed while its step was running")
        return JobLease(stored.record, stored.revision, checkpoint)

    def _finish_job(
        self,
        lease: JobLease,
        capability: AdapterCapability,
        now: str,
        *,
        truth_snapshot: StoreSnapshot,
    ) -> JobLease:
        checkpoint = replace(
            lease.checkpoint,
            stage=JobStage.COMPLETE,
            lease_owner="",
            lease_expires_at="",
            truth_snapshot_ref=truth_snapshot.snapshot_ref,
            projection_stale=False,
            error_code="",
        )
        job = replace(
            lease.job,
            status=JobStatus.COMPLETED,
            cursor=_encode_checkpoint(checkpoint),
            completed_at=now,
        )
        snapshot, stored = self._load_job(job.job_id, job.owner_scope)
        assert stored is not None
        return self._replace_job(snapshot, stored, job, capability)

    def _fail_paged_job(
        self,
        lease: JobLease,
        capability: AdapterCapability,
        now: str,
        *,
        stage: JobStage,
        error_code: str,
        projection_stale: bool,
        truth_snapshot_ref: str,
    ) -> JobLease:
        snapshot, stored = self._load_job(lease.job.job_id, lease.job.owner_scope)
        assert stored is not None
        if (
            stored.revision != lease.record_revision
            or stored.record != lease.job
            or stored.record.status is not JobStatus.RUNNING
        ):
            raise IndexJobLeaseConflict("job changed before paged failure commit")
        checkpoint = replace(
            lease.checkpoint,
            stage=stage,
            lease_owner="",
            lease_expires_at="",
            truth_snapshot_ref=truth_snapshot_ref,
            projection_stale=projection_stale,
            error_code=_error_code(error_code),
        )
        failed_job = replace(
            lease.job,
            status=JobStatus.FAILED,
            cursor=_encode_checkpoint(checkpoint),
            completed_at=now,
        )
        return self._replace_job(snapshot, stored, failed_job, capability)

    def _fail_job(
        self,
        lease: JobLease,
        capability: AdapterCapability,
        now: str,
        *,
        stage: JobStage,
        error_code: str,
        projection_stale: bool,
        truth_snapshot_ref: str,
    ) -> JobLease:
        checkpoint = replace(
            lease.checkpoint,
            stage=stage,
            lease_owner="",
            lease_expires_at="",
            truth_snapshot_ref=truth_snapshot_ref,
            projection_stale=projection_stale,
            error_code=_error_code(error_code),
        )
        job = replace(
            lease.job,
            status=JobStatus.FAILED,
            cursor=_encode_checkpoint(checkpoint),
            completed_at=now,
        )
        snapshot, stored = self._load_job(job.job_id, job.owner_scope)
        assert stored is not None
        return self._replace_job(snapshot, stored, job, capability)

    def _persist_records(
        self,
        records: tuple[
            SourceRecord
            | SourceVersionRecord
            | ChunkRecord
            | EntityRecord
            | RelationRecord
            | ProjectionManifest,
            ...,
        ],
    ) -> StoreSnapshot:
        snapshot = self._store.current_snapshot()
        pending: list[tuple[object, int]] = []
        with self._store.begin_read(snapshot) as read:
            for record in records:
                kind, record_id, owner = _record_descriptor(record)
                current = read.get(
                    kind,
                    record_id,
                    owner_scope=owner,
                    include_tombstone=True,
                )
                if isinstance(current, TombstoneRecord):
                    raise StoreTombstoneError(
                        "job output targets a tombstoned record identity"
                    )
                if isinstance(current, StoredRecord):
                    if current.record == record:
                        continue
                    pending.append((record, current.revision))
                else:
                    pending.append((record, 0))
        if not pending:
            return snapshot
        write = self._store.begin_write(snapshot)
        for record, expected_revision in pending:
            write.put(
                cast(StoreRecord, record),
                expected_record_revision=expected_revision,
            )
        return write.commit()

    def _replace_job(
        self,
        snapshot: StoreSnapshot,
        stored: StoredRecord[IndexJobRecord],
        job: IndexJobRecord,
        capability: AdapterCapability | None,
    ) -> JobLease:
        write = self._store.begin_write(snapshot)
        write.compare_and_swap(job, expected_record_revision=stored.revision)
        write.commit()
        _, updated = self._load_job(job.job_id, job.owner_scope)
        assert updated is not None
        checkpoint = _decode_checkpoint(updated.record.cursor, updated.record, capability)
        return JobLease(updated.record, updated.revision, checkpoint)

    def _load_job(
        self,
        job_id: str,
        owner_scope: str,
        *,
        required: bool = True,
    ) -> tuple[StoreSnapshot, StoredRecord[IndexJobRecord] | None]:
        identity = RecordRef(RecordKind.JOB, job_id).record_id
        owner = _owner_scope(owner_scope)
        snapshot = self._store.current_snapshot()
        with self._store.begin_read(snapshot) as read:
            item = read.get(
                RecordKind.JOB,
                identity,
                owner_scope=owner,
                include_tombstone=True,
            )
        if isinstance(item, TombstoneRecord):
            raise IndexJobTerminalError("job identity is tombstoned")
        if item is None:
            if required:
                raise IndexJobRuntimeError("owner-scoped job was not found")
            return snapshot, None
        if not isinstance(item, StoredRecord) or not isinstance(item.record, IndexJobRecord):
            raise IndexJobRuntimeError("stored job record is invalid")
        return snapshot, cast(StoredRecord[IndexJobRecord], item)


def plan_unavailable_tombstones(
    store: TransactionalStore,
    observation: UnavailableObservation,
    *,
    limit: int = 1_000,
) -> TombstonePlan:
    """Build a bounded, content-free deletion plan without applying it."""

    if not isinstance(store, TransactionalStore):
        raise IndexJobRuntimeError("store must implement TransactionalStore")
    if not isinstance(observation, UnavailableObservation):
        raise IndexJobRuntimeError("observation must be typed")
    page_limit = _bounded_integer(
        limit,
        "limit",
        minimum=1,
        maximum=MAX_PAGE_SIZE,
    )
    source_id = observation.source_ref.record_id
    order = (
        RecordKind.LINEAGE,
        RecordKind.RELATION,
        RecordKind.ENTITY,
        RecordKind.CHUNK,
        RecordKind.SOURCE_VERSION,
        RecordKind.PROJECTION,
        RecordKind.DERIVED_RUN,
        RecordKind.SOURCE,
    )
    planned: list[PlannedTombstone] = []
    clipped = False
    with store.begin_read() as read:
        for kind in order:
            if len(planned) >= page_limit:
                clipped = True
                break
            page = read.list_records(
                kind,
                owner_scope=observation.owner_scope,
                limit=min(MAX_PAGE_SIZE, page_limit - len(planned)),
            )
            for item in page.items:
                if not isinstance(item, StoredRecord):
                    continue
                if _record_mentions_source(item.record, source_id):
                    planned.append(
                        PlannedTombstone(
                            RecordRef(item.record_kind, item.record_id),
                            item.revision,
                            f"source_{observation.reason.value}",
                        )
                    )
                    if len(planned) >= page_limit:
                        clipped = True
                        break
            if page.clipped:
                clipped = True
    return TombstonePlan(observation.source_ref, tuple(planned), clipped)


def _record_mentions_source(record: object, source_id: str) -> bool:
    if isinstance(record, SourceRecord):
        return record.source_id == source_id
    if isinstance(record, (SourceVersionRecord, ChunkRecord, EntityRecord)):
        return record.source_id == source_id
    if isinstance(record, RelationRecord):
        return any(item.source_id == source_id for item in record.evidence_refs)
    if isinstance(record, LineageRecord):
        return record.previous.source_id == source_id or record.current.source_id == source_id
    if isinstance(record, ProjectionManifest):
        return any(item.source_id == source_id for item in record.input_evidence)
    if isinstance(record, (DerivedRunRecord, IndexJobRecord)):
        return source_id in record.source_scope.source_ids
    return False


def _source_version_authority_bytes(value: SourceVersionRecord) -> bytes:
    if type(value) is not SourceVersionRecord:
        raise SourceAdapterError("paged source version authority must be exact")
    return canonical_json(_capture_exact_contract_value(value)).encode("utf-8")


def _source_version_from_authority(value: bytes) -> SourceVersionRecord:
    if type(value) is not bytes:
        raise SourceAdapterError("paged source version authority must be exact bytes")
    try:
        record = record_from_json(value.decode("utf-8"))
    except (UnicodeDecodeError, UnifiedSourceIndexContractError) as exc:
        raise SourceAdapterError("paged source version authority is invalid") from exc
    if type(record) is not SourceVersionRecord:
        raise SourceAdapterError("paged source version authority changed type")
    if _source_version_authority_bytes(record) != value:
        raise SourceAdapterError("paged source version authority changed bytes")
    return record


def _detach_paged_discovery_page(value: object) -> DiscoveryPage:
    if type(value) is not DiscoveryPage:
        raise SourceAdapterError("paged discovery requires an exact page")
    items = object.__getattribute__(value, "items")
    cursor = object.__getattribute__(value, "next_cursor")
    warnings = object.__getattribute__(value, "warnings")
    if (
        type(items) is not tuple
        or len(items) > MAX_ADAPTER_PAGE_SIZE
        or any(type(item) is not DiscoveryItem for item in items)
    ):
        raise SourceAdapterError("paged discovery items must use exact bounded types")
    if type(cursor) is not str or len(cursor) > MAX_ADAPTER_CURSOR_CHARS:
        raise SourceAdapterError("paged discovery cursor must use exact bounded text")
    if (
        type(warnings) is not tuple
        or len(warnings) > MAX_ADAPTER_WARNINGS
        or any(type(item) is not str for item in warnings)
    ):
        raise SourceAdapterError("paged discovery warnings must use exact bounded types")
    detached_items = []
    for item in items:
        source = object.__getattribute__(item, "source")
        fingerprint = object.__getattribute__(item, "fingerprint")
        if type(fingerprint) is not str:
            raise SourceAdapterError("paged discovery fingerprint must use exact text")
        detached_items.append(
            DiscoveryItem(
                cast(SourceRecord, _detach_exact_record(source, (SourceRecord,))),
                fingerprint,
            )
        )
    return DiscoveryPage(tuple(detached_items), cursor, warnings)


def _detach_paged_extraction_page(value: object) -> PagedExtractionPage:
    if type(value) is not PagedExtractionPage:
        raise SourceAdapterError("paged extraction requires an exact page")
    return PagedExtractionPage(
        object.__getattribute__(value, "source_version"),
        object.__getattribute__(value, "sequence_start"),
        object.__getattribute__(value, "records"),
        object.__getattribute__(value, "next_cursor"),
        object.__getattribute__(value, "final_record_count"),
        object.__getattribute__(value, "final_records_digest"),
        object.__getattribute__(value, "warnings"),
    )


def _paged_record_key(
    record: ChunkRecord | EntityRecord | RelationRecord,
) -> tuple[str, str]:
    kind, record_id, _ = _record_descriptor(record)
    return kind.value, record_id


def _paged_records_digest(
    records: tuple[ChunkRecord | EntityRecord | RelationRecord, ...],
) -> str:
    payload = b"\n".join(
        canonical_json(_capture_exact_contract_value(record)).encode("utf-8")
        for record in records
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _require_fresh_paged_commit_time(
    lease: JobLease,
    *,
    normalized_now: str,
    started: float,
    deadline: float,
) -> None:
    commit_time = time.monotonic()
    if int((deadline - commit_time) * 1_000) < 1:
        raise SourceAdapterError(
            "paged extraction exceeded its monotonic time budget"
        )
    commit_now = _format_timestamp(
        _parse_timestamp(normalized_now)
        + timedelta(seconds=max(0.0, commit_time - started))
    )
    if not lease.checkpoint.lease_is_active(commit_now):
        raise IndexJobLeaseConflict(
            "job lease expired before paged truth commit"
        )


def _remaining_time_budget_ms(deadline: float) -> int:
    remaining = int((deadline - time.monotonic()) * 1_000)
    if remaining < 1:
        raise SourceAdapterError("paged extraction exceeded its monotonic time budget")
    return min(remaining, 86_400_000)


def _mapped_now(normalized_now: str, started: float) -> str:
    elapsed = max(0.0, time.monotonic() - started)
    return _format_timestamp(
        _parse_timestamp(normalized_now) + timedelta(seconds=elapsed)
    )


def _validate_job_capability(
    job: IndexJobRecord,
    capability: AdapterCapability,
) -> None:
    if job.owner_scope != capability.owner_scope:
        raise IndexJobRuntimeError("job and adapter owner scopes do not match")
    if job.profile_ref == "":
        raise IndexJobRuntimeError("job extraction profile is missing")
    if (
        capability.required_job_profile_ref
        and job.profile_ref != capability.required_job_profile_ref
    ):
        raise IndexJobRuntimeError("job profile does not match paged adapter generation")


def _validate_projection_manifest(
    manifest: ProjectionManifest,
    truth_snapshot: StoreSnapshot,
    extraction: ExtractionResult,
) -> None:
    if not isinstance(manifest, ProjectionManifest):
        raise IndexJobRuntimeError("projection did not return a manifest")
    if manifest.input_snapshot_ref != truth_snapshot.snapshot_ref:
        raise IndexJobRuntimeError("projection manifest references another truth snapshot")
    if manifest.owner_scope != extraction.source_version.owner_scope:
        raise IndexJobRuntimeError("projection manifest crosses owner scope")
    evidence_ids = {item.record_id for item in manifest.input_evidence}
    available_ids = {
        item.evidence_ref().record_id
        for item in (*extraction.chunks, *extraction.entities)
    }
    if not evidence_ids or not evidence_ids <= available_ids:
        raise IndexJobRuntimeError("projection manifest evidence escapes extraction")


def _capability(adapter: SourceAdapter) -> AdapterCapability:
    if not isinstance(adapter, SourceAdapter):
        raise IndexJobRuntimeError("adapter must implement SourceAdapter")
    raw = adapter.describe_capability()
    if type(raw) is not AdapterCapability:
        raise IndexJobRuntimeError("adapter capability is invalid")
    values = tuple(
        object.__getattribute__(raw, name)
        for name in (
            "adapter_id",
            "adapter_version",
            "owner_scope",
            "domain_kind",
            "source_kind",
            "content_policy",
            "classification_ceiling",
            "supports_exact_reads",
            "max_discovery_page",
            "max_extract_items",
            "required_job_profile_ref",
            "max_total_extract_chars",
            "supports_budgeted_paging",
        )
    )
    expected_types = (
        str, str, str, str, SourceKind, ContentPolicy,
        Classification, bool, int, int, str, int, bool,
    )
    if any(type(value) is not expected for value, expected in zip(values, expected_types, strict=True)):
        raise IndexJobRuntimeError("adapter capability contains non-exact values")
    capability = AdapterCapability(*values)
    if capability.required_job_profile_ref and not isinstance(adapter, PagedSourceAdapter):
        raise IndexJobRuntimeError("paged adapter must implement PagedSourceAdapter")
    if capability.max_total_extract_chars > MAX_TOTAL_EXTRACTION_CHARS:
        raise IndexJobRuntimeError("adapter aggregate extraction budget is unbounded")
    if capability.supports_budgeted_paging and not isinstance(
        adapter, BudgetedPagedSourceAdapter
    ):
        raise IndexJobRuntimeError("budgeted paged adapter protocol is missing")
    return capability


def _initial_checkpoint(
    job: IndexJobRecord,
    capability: AdapterCapability | None,
) -> JobCheckpoint:
    adapter_id = capability.adapter_id if capability else "pending.adapter"
    adapter_version = capability.adapter_version if capability else "pending"
    return JobCheckpoint(
        job.job_id,
        adapter_id,
        adapter_version,
        JobStage.DISCOVERY,
    )


def _encode_checkpoint(checkpoint: JobCheckpoint) -> str:
    body = {
        "s": "usi.job.v1",
        "j": checkpoint.job_id,
        "a": checkpoint.adapter_id,
        "v": checkpoint.adapter_version,
        "g": checkpoint.stage.value,
        "c": checkpoint.discovery_cursor,
        "n": checkpoint.items_completed,
        "o": checkpoint.lease_owner,
        "x": checkpoint.lease_expires_at,
        "t": checkpoint.truth_snapshot_ref,
        "p": checkpoint.projection_stale,
        "e": checkpoint.error_code,
    }
    checksum = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    encoded = canonical_json({**body, "h": checksum})
    if len(encoded) > MAX_JOB_CHECKPOINT_CHARS:
        raise IndexJobRuntimeError("job checkpoint exceeds the durable cursor bound")
    return encoded


def _decode_checkpoint(
    value: str,
    job: IndexJobRecord,
    capability: AdapterCapability | None,
) -> JobCheckpoint:
    if not value:
        return _initial_checkpoint(job, capability)
    if not isinstance(value, str) or len(value) > MAX_JOB_CHECKPOINT_CHARS:
        raise IndexJobRuntimeError("job checkpoint is invalid or unbounded")
    try:
        payload = json.loads(value, object_pairs_hook=_unique_json_object)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IndexJobRuntimeError("job checkpoint is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise IndexJobRuntimeError("job checkpoint must be an object")
    expected_fields = {"s", "j", "a", "v", "g", "c", "n", "o", "x", "t", "p", "e", "h"}
    if set(payload) != expected_fields:
        raise IndexJobRuntimeError("job checkpoint fields are incomplete or unknown")
    checksum = payload.pop("h")
    expected_checksum = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    if checksum != expected_checksum:
        raise IndexJobRuntimeError("job checkpoint checksum does not match")
    if payload.pop("s") != "usi.job.v1":
        raise IndexJobRuntimeError("job checkpoint schema is unsupported")
    checkpoint = JobCheckpoint(
        job_id=payload["j"],
        adapter_id=payload["a"],
        adapter_version=payload["v"],
        stage=payload["g"],
        discovery_cursor=payload["c"],
        items_completed=payload["n"],
        lease_owner=payload["o"],
        lease_expires_at=payload["x"],
        truth_snapshot_ref=payload["t"],
        projection_stale=payload["p"],
        error_code=payload["e"],
    )
    if checkpoint.job_id != job.job_id:
        raise IndexJobRuntimeError("job checkpoint belongs to another job")
    if capability is not None and (
        checkpoint.adapter_id not in {capability.adapter_id, "pending.adapter"}
        or checkpoint.adapter_version not in {capability.adapter_version, "pending"}
    ):
        raise IndexJobRuntimeError("job checkpoint belongs to another adapter generation")
    if capability is not None and checkpoint.adapter_id == "pending.adapter":
        checkpoint = replace(
            checkpoint,
            adapter_id=capability.adapter_id,
            adapter_version=capability.adapter_version,
        )
    return checkpoint


def _exception_code(exc: Exception, fallback: str) -> str:
    name = type(exc).__name__
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    try:
        return _error_code(normalized)
    except IndexJobRuntimeError:
        return fallback


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise IndexJobRuntimeError(f"job checkpoint contains duplicate field: {key}")
        result[key] = value
    return result


def _timestamp(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise IndexJobRuntimeError(f"{field_name} must be a timezone-aware timestamp")
    parsed = _parse_timestamp(value)
    return _format_timestamp(parsed)


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise IndexJobRuntimeError("timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise IndexJobRuntimeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _worker(value: str) -> str:
    if not isinstance(value, str) or not _WORKER_RE.fullmatch(value):
        raise IndexJobRuntimeError("worker_id must be a bounded token")
    return value


def _error_code(value: str) -> str:
    if not isinstance(value, str) or not _ERROR_CODE_RE.fullmatch(value):
        raise IndexJobRuntimeError("error code must be a bounded content-free token")
    return value


def _token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _WORKER_RE.fullmatch(value):
        raise IndexJobRuntimeError(f"{field_name} must be a bounded token")
    return value


def _enum(value, enum_type, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise IndexJobRuntimeError(f"{field_name} is invalid") from exc


def _bounded_integer(
    value: int,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise IndexJobRuntimeError(
            f"{field_name} must be between {minimum} and {maximum}"
        )
    return value


__all__ = [
    "IndexJobExecutionError",
    "IndexJobLeaseConflict",
    "IndexJobRuntimeError",
    "IndexJobTerminalError",
    "JobCheckpoint",
    "JobLease",
    "JobStage",
    "JobStepResult",
    "MAX_JOB_CHECKPOINT_CHARS",
    "MAX_JOB_STEPS",
    "PlannedTombstone",
    "ProjectionRequest",
    "ProjectionSink",
    "TombstonePlan",
    "UnifiedSourceIndexJobRuntime",
    "plan_unavailable_tombstones",
]

"""Snapshot-bound RAPTOR derived-run adapter for the Unified Source Index.

This module does not implement clustering, summarization, or a scheduler.  It
builds immutable, bounded maintenance tasks for an injected existing worker,
validates returned artifact lineage, and persists only the canonical
DerivedRunRecord.  RAPTOR artifacts remain replaceable projections.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import hashlib
import re
from typing import Protocol, runtime_checkable

from src.unified_source_index_contract import (
    ContentPolicy,
    DerivedRunKind,
    DerivedRunRecord,
    EvidenceRef,
    RecordKind,
    RecordRef,
    SourceScope,
    canonical_json,
    content_hash,
)
from src.unified_source_index_stores import (
    StoreSnapshot,
    StoredRecord,
    TombstoneRecord,
    TransactionalStore,
)


MAX_RAPTOR_INPUTS = 256
MAX_RAPTOR_ARTIFACTS = 4_096
MAX_QUALITY_REFS = 32
MAX_TASK_TIMEOUT_MS = 86_400_000

_TOKEN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:@/+~-]{0,255}$")
_OWNER_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}:[^\s:*]{1,127}$")
_ARTIFACT_ID_RE = re.compile(r"^raptor_(cluster|node|membership|summary)_[0-9a-f]{40}$")
_INPUT_KINDS = {RecordKind.SOURCE_VERSION, RecordKind.CHUNK, RecordKind.ENTITY}


class UnifiedSourceIndexRaptorError(ValueError):
    """Raised when RAPTOR planning, worker output, or persistence is unsafe."""


class RaptorArtifactKind(StrEnum):
    CLUSTER = "cluster"
    NODE = "node"
    MEMBERSHIP = "membership"
    SUMMARY = "summary"


class RaptorMaintenanceStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class RaptorInvalidationStatus(StrEnum):
    CURRENT = "current"
    INPUTS_CHANGED = "inputs_changed"


@dataclass(frozen=True, slots=True)
class RaptorRunConfig:
    algorithm_ref: str
    algorithm_version: str
    embedding_snapshot_ref: str
    quality_evidence_refs: tuple[str, ...]
    rebuild_evidence_ref: str
    max_inputs: int = 256
    max_nodes: int = 100_000
    max_depth: int = 8
    task_timeout_ms: int = 60_000

    def __post_init__(self) -> None:
        for field_name in (
            "algorithm_ref",
            "algorithm_version",
            "embedding_snapshot_ref",
            "rebuild_evidence_ref",
        ):
            object.__setattr__(self, field_name, _token(getattr(self, field_name), field_name))
        quality = _tokens(
            self.quality_evidence_refs,
            "quality_evidence_refs",
            minimum=1,
            maximum=MAX_QUALITY_REFS,
        )
        object.__setattr__(self, "quality_evidence_refs", quality)
        object.__setattr__(self, "max_inputs", _integer(self.max_inputs, "max_inputs", 1, MAX_RAPTOR_INPUTS))
        object.__setattr__(self, "max_nodes", _integer(self.max_nodes, "max_nodes", 1, 1_000_000))
        object.__setattr__(self, "max_depth", _integer(self.max_depth, "max_depth", 0, 64))
        object.__setattr__(
            self,
            "task_timeout_ms",
            _integer(self.task_timeout_ms, "task_timeout_ms", 1, MAX_TASK_TIMEOUT_MS),
        )

    @property
    def config_hash(self) -> str:
        return content_hash(
            canonical_json(
                {
                    "algorithm_ref": self.algorithm_ref,
                    "algorithm_version": self.algorithm_version,
                    "embedding_snapshot_ref": self.embedding_snapshot_ref,
                    "max_inputs": self.max_inputs,
                    "max_nodes": self.max_nodes,
                    "max_depth": self.max_depth,
                    "task_timeout_ms": self.task_timeout_ms,
                }
            )
        )


@dataclass(frozen=True, slots=True)
class RaptorMaintenanceTask:
    task_id: str
    derived_run_id: str
    input_snapshot_ref: str
    store_snapshot: StoreSnapshot
    input_evidence: tuple[EvidenceRef, ...]
    embedding_snapshot_ref: str
    max_nodes: int
    max_depth: int
    timeout_ms: int
    operation: str = "raptor_build"

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _token(self.task_id, "task_id"))
        RecordRef(RecordKind.DERIVED_RUN, self.derived_run_id)
        object.__setattr__(self, "input_snapshot_ref", _token(self.input_snapshot_ref, "input_snapshot_ref"))
        if not isinstance(self.store_snapshot, StoreSnapshot):
            raise UnifiedSourceIndexRaptorError("store_snapshot must be typed")
        evidence = _evidence(self.input_evidence, maximum=MAX_RAPTOR_INPUTS)
        object.__setattr__(self, "input_evidence", evidence)
        object.__setattr__(
            self,
            "embedding_snapshot_ref",
            _token(self.embedding_snapshot_ref, "embedding_snapshot_ref"),
        )
        object.__setattr__(self, "max_nodes", _integer(self.max_nodes, "max_nodes", 1, 1_000_000))
        object.__setattr__(self, "max_depth", _integer(self.max_depth, "max_depth", 0, 64))
        object.__setattr__(self, "timeout_ms", _integer(self.timeout_ms, "timeout_ms", 1, MAX_TASK_TIMEOUT_MS))
        if self.operation != "raptor_build":
            raise UnifiedSourceIndexRaptorError("maintenance operation is invalid")


@dataclass(frozen=True, slots=True)
class RaptorMaintenanceReceipt:
    task_id: str
    submission_ref: str
    worker_ref: str

    def __post_init__(self) -> None:
        for field_name in ("task_id", "submission_ref", "worker_ref"):
            object.__setattr__(self, field_name, _token(getattr(self, field_name), field_name))


@runtime_checkable
class RaptorMaintenanceSubmitter(Protocol):
    @property
    def worker_ref(self) -> str: ...

    def submit(self, task: RaptorMaintenanceTask) -> RaptorMaintenanceReceipt: ...


@dataclass(frozen=True, slots=True)
class RaptorArtifactRef:
    artifact_kind: RaptorArtifactKind
    artifact_id: str
    derived_run_id: str
    natural_key: str
    input_evidence: tuple[EvidenceRef, ...]
    parent_artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_kind", _enum(self.artifact_kind, RaptorArtifactKind, "artifact_kind"))
        if not isinstance(self.artifact_id, str) or not _ARTIFACT_ID_RE.fullmatch(self.artifact_id):
            raise UnifiedSourceIndexRaptorError("artifact_id is invalid")
        RecordRef(RecordKind.DERIVED_RUN, self.derived_run_id)
        object.__setattr__(self, "natural_key", _token(self.natural_key, "natural_key"))
        object.__setattr__(self, "input_evidence", _evidence(self.input_evidence, maximum=MAX_RAPTOR_INPUTS))
        parents = _tokens(
            self.parent_artifact_ids,
            "parent_artifact_ids",
            minimum=0,
            maximum=64,
        )
        if self.artifact_id in parents:
            raise UnifiedSourceIndexRaptorError("artifact cannot parent itself")
        object.__setattr__(self, "parent_artifact_ids", parents)
        expected = _artifact_id(
            self.artifact_kind,
            self.derived_run_id,
            self.natural_key,
            self.input_evidence,
            self.parent_artifact_ids,
        )
        if self.artifact_id != expected:
            raise UnifiedSourceIndexRaptorError("artifact_id does not match artifact lineage")

    @classmethod
    def create(
        cls,
        *,
        artifact_kind: RaptorArtifactKind | str,
        derived_run_id: str,
        natural_key: str,
        input_evidence: tuple[EvidenceRef, ...],
        parent_artifact_ids: tuple[str, ...] = (),
    ) -> "RaptorArtifactRef":
        kind = _enum(artifact_kind, RaptorArtifactKind, "artifact_kind")
        evidence = _evidence(input_evidence, maximum=MAX_RAPTOR_INPUTS)
        parents = _tokens(parent_artifact_ids, "parent_artifact_ids", minimum=0, maximum=64)
        return cls(
            kind,
            _artifact_id(kind, derived_run_id, natural_key, evidence, parents),
            derived_run_id,
            natural_key,
            evidence,
            parents,
        )


@dataclass(frozen=True, slots=True)
class RaptorMaintenanceResult:
    task_id: str
    status: RaptorMaintenanceStatus
    artifacts: tuple[RaptorArtifactRef, ...]
    quality_evidence_refs: tuple[str, ...]
    error_code: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _token(self.task_id, "task_id"))
        object.__setattr__(self, "status", _enum(self.status, RaptorMaintenanceStatus, "status"))
        if (
            not isinstance(self.artifacts, tuple)
            or len(self.artifacts) > MAX_RAPTOR_ARTIFACTS
            or not all(isinstance(item, RaptorArtifactRef) for item in self.artifacts)
        ):
            raise UnifiedSourceIndexRaptorError("artifacts must be typed and bounded")
        if len({item.artifact_id for item in self.artifacts}) != len(self.artifacts):
            raise UnifiedSourceIndexRaptorError("artifacts contain duplicate ids")
        quality = _tokens(
            self.quality_evidence_refs,
            "quality_evidence_refs",
            minimum=0,
            maximum=MAX_QUALITY_REFS,
        )
        object.__setattr__(self, "quality_evidence_refs", quality)
        if self.status is RaptorMaintenanceStatus.COMPLETED:
            if self.error_code or not self.artifacts or not quality:
                raise UnifiedSourceIndexRaptorError("completed maintenance result is incomplete")
        elif not self.error_code:
            raise UnifiedSourceIndexRaptorError("non-completed maintenance result requires error_code")
        if self.error_code:
            object.__setattr__(self, "error_code", _token(self.error_code, "error_code"))


@dataclass(frozen=True, slots=True)
class RaptorRunPlan:
    run: DerivedRunRecord
    store_snapshot: StoreSnapshot
    input_fingerprint: str
    tasks: tuple[RaptorMaintenanceTask, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.run, DerivedRunRecord) or self.run.derived_kind is not DerivedRunKind.RAPTOR:
            raise UnifiedSourceIndexRaptorError("plan requires a RAPTOR DerivedRunRecord")
        if self.run.completed_at:
            raise UnifiedSourceIndexRaptorError("planned run must not be completed")
        if not isinstance(self.store_snapshot, StoreSnapshot):
            raise UnifiedSourceIndexRaptorError("plan store_snapshot must be typed")
        if self.input_fingerprint != _input_fingerprint(self.run.input_evidence):
            raise UnifiedSourceIndexRaptorError("input_fingerprint does not match exact evidence")
        if not isinstance(self.tasks, tuple) or len(self.tasks) != 1:
            raise UnifiedSourceIndexRaptorError("baseline RAPTOR plan requires one bounded task")
        task = self.tasks[0]
        if (
            task.derived_run_id != self.run.derived_run_id
            or task.input_snapshot_ref != self.run.input_snapshot_ref
            or task.store_snapshot != self.store_snapshot
            or task.input_evidence != self.run.input_evidence
        ):
            raise UnifiedSourceIndexRaptorError("maintenance task escapes its run plan")


@dataclass(frozen=True, slots=True)
class RaptorRunManifest:
    run: DerivedRunRecord
    artifacts: tuple[RaptorArtifactRef, ...]
    input_fingerprint: str
    persisted_snapshot: StoreSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.run, DerivedRunRecord) or not self.run.completed_at:
            raise UnifiedSourceIndexRaptorError("manifest requires a completed derived run")
        if (
            not isinstance(self.artifacts, tuple)
            or not self.artifacts
            or len(self.artifacts) > MAX_RAPTOR_ARTIFACTS
        ):
            raise UnifiedSourceIndexRaptorError("manifest artifacts are invalid or unbounded")
        if any(item.derived_run_id != self.run.derived_run_id for item in self.artifacts):
            raise UnifiedSourceIndexRaptorError("manifest artifact belongs to another run")
        if self.input_fingerprint != _input_fingerprint(self.run.input_evidence):
            raise UnifiedSourceIndexRaptorError("manifest input fingerprint is invalid")
        if not isinstance(self.persisted_snapshot, StoreSnapshot):
            raise UnifiedSourceIndexRaptorError("persisted_snapshot must be typed")


@dataclass(frozen=True, slots=True)
class RaptorInvalidation:
    status: RaptorInvalidationStatus
    previous_run_id: str
    current_store_snapshot: StoreSnapshot
    added: tuple[EvidenceRef, ...]
    changed: tuple[EvidenceRef, ...]
    removed: tuple[EvidenceRef, ...]
    global_rebuild_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _enum(self.status, RaptorInvalidationStatus, "status"))
        RecordRef(RecordKind.DERIVED_RUN, self.previous_run_id)
        if not isinstance(self.current_store_snapshot, StoreSnapshot):
            raise UnifiedSourceIndexRaptorError("current_store_snapshot must be typed")
        for field_name in ("added", "changed", "removed"):
            object.__setattr__(
                self,
                field_name,
                _evidence(getattr(self, field_name), maximum=MAX_RAPTOR_INPUTS, allow_empty=True),
            )
        has_changes = bool(self.added or self.changed or self.removed)
        if (self.status is RaptorInvalidationStatus.INPUTS_CHANGED) != has_changes:
            raise UnifiedSourceIndexRaptorError("invalidation status and changed inputs disagree")
        if self.global_rebuild_required is not False:
            raise UnifiedSourceIndexRaptorError("RAPTOR invalidation must remain input-scoped")

    @property
    def affected_input_count(self) -> int:
        return len(self.added) + len(self.changed) + len(self.removed)


class UnifiedSourceIndexRaptorAdapter:
    def __init__(self, store: TransactionalStore) -> None:
        if not isinstance(store, TransactionalStore):
            raise UnifiedSourceIndexRaptorError("store must implement TransactionalStore")
        self._store = store

    def prepare(
        self,
        config: RaptorRunConfig,
        *,
        owner_scope: str,
        input_snapshot: StoreSnapshot,
        input_refs: tuple[RecordRef, ...],
        started_at: str,
    ) -> RaptorRunPlan:
        if not isinstance(config, RaptorRunConfig):
            raise UnifiedSourceIndexRaptorError("config must be typed")
        owner = _owner_scope(owner_scope)
        if not isinstance(input_snapshot, StoreSnapshot):
            raise UnifiedSourceIndexRaptorError("input_snapshot must be typed")
        evidence = self._resolve(
            owner,
            input_snapshot,
            input_refs,
            maximum=config.max_inputs,
        )
        scope = SourceScope.create(
            tuple(item.policy_evidence for item in evidence),
            content_policy=ContentPolicy.METADATA_ONLY,
        )
        fingerprint = _input_fingerprint(evidence)
        run = DerivedRunRecord.create(
            derived_kind=DerivedRunKind.RAPTOR,
            source_scope=scope,
            input_snapshot_ref=_input_snapshot_ref(fingerprint),
            algorithm_ref=config.algorithm_ref,
            algorithm_version=config.algorithm_version,
            config_hash=config.config_hash,
            input_evidence=evidence,
            embedding_snapshot_ref=config.embedding_snapshot_ref,
            quality_evidence_refs=config.quality_evidence_refs,
            rebuild_evidence_ref=config.rebuild_evidence_ref,
            max_nodes=config.max_nodes,
            max_depth=config.max_depth,
            content_policy=ContentPolicy.METADATA_ONLY,
            started_at=_timestamp(started_at, "started_at"),
        )
        task = RaptorMaintenanceTask(
            task_id=_task_id(run.derived_run_id, input_snapshot.snapshot_ref),
            derived_run_id=run.derived_run_id,
            input_snapshot_ref=run.input_snapshot_ref,
            store_snapshot=input_snapshot,
            input_evidence=run.input_evidence,
            embedding_snapshot_ref=config.embedding_snapshot_ref,
            max_nodes=config.max_nodes,
            max_depth=config.max_depth,
            timeout_ms=config.task_timeout_ms,
        )
        return RaptorRunPlan(run, input_snapshot, fingerprint, (task,))

    def submit(
        self,
        plan: RaptorRunPlan,
        submitter: RaptorMaintenanceSubmitter,
    ) -> tuple[RaptorMaintenanceReceipt, ...]:
        if not isinstance(plan, RaptorRunPlan):
            raise UnifiedSourceIndexRaptorError("plan must be typed")
        if not isinstance(submitter, RaptorMaintenanceSubmitter):
            raise UnifiedSourceIndexRaptorError("submitter must implement the maintenance protocol")
        worker_ref = _token(submitter.worker_ref, "worker_ref")
        receipts = []
        for task in plan.tasks:
            receipt = submitter.submit(task)
            if not isinstance(receipt, RaptorMaintenanceReceipt):
                raise UnifiedSourceIndexRaptorError("worker returned an invalid receipt")
            if receipt.task_id != task.task_id or receipt.worker_ref != worker_ref:
                raise UnifiedSourceIndexRaptorError("worker receipt does not match submitted task")
            receipts.append(receipt)
        return tuple(receipts)

    def complete(
        self,
        plan: RaptorRunPlan,
        result: RaptorMaintenanceResult,
        *,
        completed_at: str,
    ) -> RaptorRunManifest:
        if not isinstance(plan, RaptorRunPlan) or not isinstance(result, RaptorMaintenanceResult):
            raise UnifiedSourceIndexRaptorError("plan and result must be typed")
        task = plan.tasks[0]
        if result.task_id != task.task_id:
            raise UnifiedSourceIndexRaptorError("maintenance result belongs to another task")
        if result.status is not RaptorMaintenanceStatus.COMPLETED:
            raise UnifiedSourceIndexRaptorError("non-completed worker result cannot commit a run")
        expected_evidence = {
            (item.record_kind, item.record_id): item for item in plan.run.input_evidence
        }
        covered: set[tuple[RecordKind, str]] = set()
        kinds = set()
        artifact_ids = {item.artifact_id for item in result.artifacts}
        for artifact in result.artifacts:
            if artifact.derived_run_id != plan.run.derived_run_id:
                raise UnifiedSourceIndexRaptorError("worker artifact belongs to another run")
            for parent_id in artifact.parent_artifact_ids:
                if parent_id not in artifact_ids:
                    raise UnifiedSourceIndexRaptorError("worker artifact names an unknown parent")
            for item in artifact.input_evidence:
                key = (item.record_kind, item.record_id)
                if expected_evidence.get(key) != item:
                    raise UnifiedSourceIndexRaptorError("worker artifact evidence escapes run inputs")
                covered.add(key)
            kinds.add(artifact.artifact_kind)
        if covered != set(expected_evidence):
            raise UnifiedSourceIndexRaptorError("worker artifacts do not cover every run input")
        if kinds != set(RaptorArtifactKind):
            raise UnifiedSourceIndexRaptorError("worker output lacks required RAPTOR artifact kinds")
        quality = tuple(
            sorted(set(plan.run.quality_evidence_refs) | set(result.quality_evidence_refs))
        )
        if len(quality) > MAX_QUALITY_REFS:
            raise UnifiedSourceIndexRaptorError("combined quality evidence exceeds its bound")
        completed = replace(
            plan.run,
            quality_evidence_refs=quality,
            completed_at=_timestamp(completed_at, "completed_at"),
        )
        persisted = self._persist(completed)
        artifacts = tuple(
            sorted(result.artifacts, key=lambda item: (item.artifact_kind.value, item.artifact_id))
        )
        return RaptorRunManifest(completed, artifacts, plan.input_fingerprint, persisted)

    def assess_invalidation(
        self,
        previous: DerivedRunRecord,
        *,
        current_snapshot: StoreSnapshot,
        current_input_refs: tuple[RecordRef, ...],
    ) -> RaptorInvalidation:
        if not isinstance(previous, DerivedRunRecord) or previous.derived_kind is not DerivedRunKind.RAPTOR:
            raise UnifiedSourceIndexRaptorError("previous must be a RAPTOR derived run")
        current = self._resolve(
            previous.owner_scope,
            current_snapshot,
            current_input_refs,
            maximum=MAX_RAPTOR_INPUTS,
            allow_empty=True,
        )
        old_by_key = {(item.record_kind, item.record_id): item for item in previous.input_evidence}
        new_by_key = {(item.record_kind, item.record_id): item for item in current}
        added = tuple(new_by_key[key] for key in sorted(new_by_key.keys() - old_by_key.keys(), key=_key_sort))
        removed = tuple(old_by_key[key] for key in sorted(old_by_key.keys() - new_by_key.keys(), key=_key_sort))
        changed = tuple(
            new_by_key[key]
            for key in sorted(old_by_key.keys() & new_by_key.keys(), key=_key_sort)
            if old_by_key[key] != new_by_key[key]
        )
        status = (
            RaptorInvalidationStatus.INPUTS_CHANGED
            if added or removed or changed
            else RaptorInvalidationStatus.CURRENT
        )
        return RaptorInvalidation(
            status,
            previous.derived_run_id,
            current_snapshot,
            added,
            changed,
            removed,
        )

    def _resolve(
        self,
        owner_scope: str,
        snapshot: StoreSnapshot,
        input_refs: tuple[RecordRef, ...],
        *,
        maximum: int,
        allow_empty: bool = False,
    ) -> tuple[EvidenceRef, ...]:
        refs = _input_refs(input_refs, maximum=maximum, allow_empty=allow_empty)
        read = self._store.begin_read(snapshot)
        try:
            evidence = []
            for ref in refs:
                stored = read.get(ref.record_kind, ref.record_id, owner_scope=owner_scope)
                if not isinstance(stored, StoredRecord):
                    raise UnifiedSourceIndexRaptorError("RAPTOR input record is missing")
                if stored.owner_scope != owner_scope:
                    raise UnifiedSourceIndexRaptorError("RAPTOR input crosses owner scope")
                evidence_ref = stored.record.evidence_ref()
                if not isinstance(evidence_ref, EvidenceRef):
                    raise UnifiedSourceIndexRaptorError("RAPTOR input lacks exact evidence")
                evidence.append(evidence_ref)
            return _evidence(tuple(evidence), maximum=maximum, allow_empty=allow_empty)
        finally:
            read.close()

    def _persist(self, run: DerivedRunRecord) -> StoreSnapshot:
        snapshot = self._store.current_snapshot()
        read = self._store.begin_read(snapshot)
        try:
            current = read.get(
                RecordKind.DERIVED_RUN,
                run.derived_run_id,
                owner_scope=run.owner_scope,
                include_tombstone=True,
            )
        finally:
            read.close()
        write = self._store.begin_write(snapshot)
        try:
            if isinstance(current, StoredRecord):
                write.put(run, expected_record_revision=current.revision)
            elif isinstance(current, TombstoneRecord):
                write.restore(run, expected_tombstone_revision=current.revision)
            else:
                write.put(run)
            return write.commit()
        except Exception:
            write.rollback()
            raise


def _artifact_id(
    kind: RaptorArtifactKind,
    run_id: str,
    natural_key: str,
    evidence: tuple[EvidenceRef, ...],
    parents: tuple[str, ...],
) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "kind": kind.value,
                "run_id": run_id,
                "natural_key": _token(natural_key, "natural_key"),
                "evidence": [item.to_dict() for item in evidence],
                "parents": parents,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"raptor_{kind.value}_{digest[:40]}"


def _input_fingerprint(evidence: tuple[EvidenceRef, ...]) -> str:
    return content_hash(canonical_json([item.to_dict() for item in evidence]))


def _input_snapshot_ref(fingerprint: str) -> str:
    return "raptor-input:" + fingerprint.removeprefix("sha256:")


def _task_id(run_id: str, store_snapshot_ref: str) -> str:
    digest = hashlib.sha256((run_id + "\x00" + store_snapshot_ref).encode("utf-8")).hexdigest()
    return "raptor-task:" + digest


def _input_refs(
    values: tuple[RecordRef, ...],
    *,
    maximum: int,
    allow_empty: bool,
) -> tuple[RecordRef, ...]:
    if not isinstance(values, tuple) or len(values) > maximum or (not values and not allow_empty):
        raise UnifiedSourceIndexRaptorError("input_refs must be non-empty and bounded")
    if not all(isinstance(item, RecordRef) and item.record_kind in _INPUT_KINDS for item in values):
        raise UnifiedSourceIndexRaptorError("input_refs contains an unsupported record kind")
    by_key = {(item.record_kind, item.record_id): item for item in values}
    if len(by_key) != len(values):
        raise UnifiedSourceIndexRaptorError("input_refs contains duplicates")
    return tuple(by_key[key] for key in sorted(by_key, key=_key_sort))


def _evidence(
    values: tuple[EvidenceRef, ...],
    *,
    maximum: int,
    allow_empty: bool = False,
) -> tuple[EvidenceRef, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > maximum
        or (not values and not allow_empty)
        or not all(isinstance(item, EvidenceRef) for item in values)
    ):
        raise UnifiedSourceIndexRaptorError("evidence must be typed and bounded")
    by_key = {(item.record_kind, item.record_id): item for item in values}
    if len(by_key) != len(values):
        raise UnifiedSourceIndexRaptorError("evidence contains duplicate occurrences")
    return tuple(by_key[key] for key in sorted(by_key, key=_key_sort))


def _key_sort(value: tuple[RecordKind, str]) -> tuple[str, str]:
    return value[0].value, value[1]


def _tokens(
    values: tuple[str, ...],
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not minimum <= len(values) <= maximum:
        raise UnifiedSourceIndexRaptorError(f"{field_name} must be a bounded tuple")
    normalized = tuple(sorted({_token(value, field_name) for value in values}))
    if len(normalized) != len(values):
        raise UnifiedSourceIndexRaptorError(f"{field_name} contains duplicates")
    return normalized


def _token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise UnifiedSourceIndexRaptorError(f"{field_name} must be a bounded token")
    return value


def _owner_scope(value: str) -> str:
    if not isinstance(value, str) or not _OWNER_RE.fullmatch(value) or value.lower().endswith(":all"):
        raise UnifiedSourceIndexRaptorError("owner_scope must be explicit and bounded")
    return value


def _timestamp(value: str, field_name: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise UnifiedSourceIndexRaptorError(f"{field_name} must be a bounded timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UnifiedSourceIndexRaptorError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UnifiedSourceIndexRaptorError(f"{field_name} must include a timezone")
    return parsed.isoformat().replace("+00:00", "Z")


def _integer(value: int, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise UnifiedSourceIndexRaptorError(
            f"{field_name} must be between {minimum} and {maximum}"
        )
    return value


def _enum(value, enum_type, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise UnifiedSourceIndexRaptorError(f"{field_name} is invalid") from exc


__all__ = [
    "RaptorArtifactKind",
    "RaptorArtifactRef",
    "RaptorInvalidation",
    "RaptorInvalidationStatus",
    "RaptorMaintenanceReceipt",
    "RaptorMaintenanceResult",
    "RaptorMaintenanceStatus",
    "RaptorMaintenanceSubmitter",
    "RaptorMaintenanceTask",
    "RaptorRunConfig",
    "RaptorRunManifest",
    "RaptorRunPlan",
    "UnifiedSourceIndexRaptorAdapter",
    "UnifiedSourceIndexRaptorError",
]

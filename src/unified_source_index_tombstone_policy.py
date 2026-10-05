"""Pure ULO-02 tombstone planning, evidence, and restore authorization."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable

from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    ChunkRecord,
    DerivedRunRecord,
    EntityRecord,
    EvidenceRef,
    IndexJobRecord,
    LineageRecord,
    PolicyEvidence,
    ProjectionManifest,
    RecordKind,
    RecordRef,
    RelationRecord,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator, PageRangeLocator, RowRangeLocator, MessageRangeLocator, CodeRangeLocator,
)
from src.unified_source_index_lifecycle_contract import (
    LifecycleActionKind,
    LifecycleCheckpoint,
    LifecycleContractError,
    LifecycleEffectKind,
    LifecycleErrorCode,
    LifecycleRequest,
    LifecycleScopeKind,
    _lifecycle_request_projection,
)
from src.unified_source_index_stores import (
    MAX_PAGE_SIZE,
    StoreSnapshot,
    StorePage,
    StoredRecord,
    TombstoneRecord,
)


TOMBSTONE_EVIDENCE_SCHEMA = "odysseus.unified_source_index.tombstone_evidence.v1"
_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class LifecycleTombstonePolicyError(ValueError):
    def __init__(self, code: LifecycleErrorCode = LifecycleErrorCode.TOMBSTONE_MISMATCH):
        self.code = code if type(code) is LifecycleErrorCode else LifecycleErrorCode.TOMBSTONE_MISMATCH
        super().__init__()


def _fail(code: LifecycleErrorCode = LifecycleErrorCode.TOMBSTONE_MISMATCH) -> None:
    raise LifecycleTombstonePolicyError(code) from None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class TombstoneInstruction:
    record_ref: RecordRef
    record_revision: int
    reason: str

    def __post_init__(self) -> None:
        if type(self.record_ref) is not RecordRef or type(self.record_revision) is not int or self.record_revision < 1 or type(self.reason) is not str or _REASON_RE.fullmatch(self.reason) is None:
            _fail()


@dataclass(frozen=True, slots=True)
class TombstonePlan:
    request: LifecycleRequest
    base_snapshot: StoreSnapshot
    instructions: tuple[TombstoneInstruction, ...]
    clipped: bool

    @property
    def items(self) -> tuple[TombstoneInstruction, ...]:
        return self.instructions

    def __post_init__(self) -> None:
        if type(self.request) is not LifecycleRequest or type(self.base_snapshot) is not StoreSnapshot or type(self.instructions) is not tuple or len(self.instructions) > MAX_PAGE_SIZE or any(type(item) is not TombstoneInstruction for item in self.instructions) or type(self.clipped) is not bool:
            _fail()


@dataclass(frozen=True, slots=True)
class TombstoneEvidence:
    record_ref: RecordRef
    owner_scope: str
    tombstone_revision: int
    snapshot_ref: str

    def __post_init__(self) -> None:
        if type(self.record_ref) is not RecordRef or type(self.owner_scope) is not str or type(self.tombstone_revision) is not int or self.tombstone_revision < 1 or type(self.snapshot_ref) is not str or not re.fullmatch(r"usi_snapshot_[0-9a-f]{64}", self.snapshot_ref):
            _fail()

    def to_dict(self) -> dict[str, Any]:
        return {"schema": TOMBSTONE_EVIDENCE_SCHEMA, "record_kind": self.record_ref.record_kind.value, "record_id": self.record_ref.record_id, "owner_scope": self.owner_scope, "tombstone_revision": self.tombstone_revision, "snapshot_ref": self.snapshot_ref}

    def to_json(self) -> str:
        return _canonical(self.to_dict())


@dataclass(frozen=True, slots=True)
class RestorePlan:
    record_ref: RecordRef
    expected_tombstone_revision: int
    canonical_record: StoredRecord[Any]

    def __post_init__(self) -> None:
        if type(self.record_ref) is not RecordRef or type(self.expected_tombstone_revision) is not int or self.expected_tombstone_revision < 1 or type(self.canonical_record) is not StoredRecord or self.canonical_record.record_id != self.record_ref.record_id:
            _fail()


def _source_id(request: LifecycleRequest) -> str | None:
    if request.target.scope_kind is LifecycleScopeKind.OWNER:
        return None
    assert request.target.source_ref is not None
    return request.target.source_ref.record_id


def _belongs(item: StoredRecord[Any], request: LifecycleRequest) -> bool:
    if item.owner_scope != request.target.owner_scope:
        return False
    source_id = _source_id(request)
    if source_id is None:
        return True
    record = item.record
    if item.record_kind is RecordKind.SOURCE:
        return item.record_id == source_id
    if hasattr(record, "source_id") and record.source_id == source_id:
        if request.target.scope_kind is LifecycleScopeKind.SOURCE_VERSION and hasattr(record, "source_version_id"):
            return record.source_version_id == request.target.source_version_ref.record_id
        return True
    if hasattr(record, "source_version_id"):
        version = request.target.source_version_ref.record_id if request.target.source_version_ref else None
        return version is None or record.source_version_id == version
    return False


def _rank(item: StoredRecord[Any]) -> tuple[int, str]:
    return ({RecordKind.CHUNK: 0, RecordKind.ENTITY: 0, RecordKind.RELATION: 0, RecordKind.LINEAGE: 0, RecordKind.PROJECTION: 0, RecordKind.DERIVED_RUN: 0, RecordKind.SOURCE_VERSION: 1, RecordKind.SOURCE: 2, RecordKind.JOB: 3}.get(item.record_kind, 4), item.record_id)


def plan_tombstones(request: LifecycleRequest, *, base_snapshot: StoreSnapshot, candidates: tuple[StoredRecord[Any], ...], limit: int = 1000) -> TombstonePlan:
    if type(request) is not LifecycleRequest or type(base_snapshot) is not StoreSnapshot or type(candidates) is not tuple or type(limit) is not int or limit < 1 or limit > MAX_PAGE_SIZE or any(type(item) is not StoredRecord for item in candidates):
        _fail()
    if request.action_kind is LifecycleActionKind.RESTORE or request.target.scope_kind is LifecycleScopeKind.OWNER:
        _fail(LifecycleErrorCode.OWNER_MISMATCH)
    selected = sorted((item for item in candidates if _belongs(item, request)), key=_rank)
    clipped = len(selected) > limit
    selected = selected[:limit]
    reason = {
        LifecycleActionKind.ARCHIVE: "source_archived", LifecycleActionKind.ACCESS_REVOKE: "access_revoked", LifecycleActionKind.SUPERSEDE: "source_superseded", LifecycleActionKind.DELETE: "source_deleted", LifecycleActionKind.ERASE: "source_erased",
    }.get(request.action_kind)
    if reason is None:
        _fail()
    instructions = tuple(TombstoneInstruction(item.record_ref if hasattr(item, "record_ref") else RecordRef(item.record_kind, item.record_id), item.revision, reason) for item in selected)
    return TombstonePlan(request, base_snapshot, instructions, clipped)


def evidence_from_committed_tombstones(plan: TombstonePlan, *, committed_snapshot: StoreSnapshot, tombstones: tuple[TombstoneRecord, ...]) -> tuple[TombstoneEvidence, ...]:
    if type(plan) is not TombstonePlan or type(committed_snapshot) is not StoreSnapshot or type(tombstones) is not tuple or any(type(item) is not TombstoneRecord for item in tombstones):
        _fail()
    if committed_snapshot.revision <= plan.base_snapshot.revision or committed_snapshot.snapshot_ref == plan.base_snapshot.snapshot_ref:
        _fail(LifecycleErrorCode.REVISION_MISMATCH)
    by_ref = {(item.record_kind, item.record_id): item for item in tombstones}
    result: list[TombstoneEvidence] = []
    for instruction in plan.instructions:
        marker = by_ref.get((instruction.record_ref.record_kind, instruction.record_ref.record_id))
        if marker is None or marker.owner_scope != plan.request.target.owner_scope or marker.previous_revision != instruction.record_revision:
            _fail()
        result.append(TombstoneEvidence(instruction.record_ref, marker.owner_scope, marker.revision, committed_snapshot.snapshot_ref))
    return tuple(result)


def authorize_restore(request: LifecycleRequest, *, previous: LifecycleCheckpoint, base_snapshot: StoreSnapshot, tombstones: tuple[TombstoneRecord, ...], canonical_records: tuple[StoredRecord[Any], ...]) -> RestorePlan:
    if type(request) is not LifecycleRequest or request.action_kind is not LifecycleActionKind.RESTORE or type(previous) is not LifecycleCheckpoint or type(base_snapshot) is not StoreSnapshot or type(tombstones) is not tuple or type(canonical_records) is not tuple:
        _fail(LifecycleErrorCode.RESTORE_FORBIDDEN)
    if not request.restore_authority_ref or request.lifecycle_generation <= previous.lifecycle_generation:
        _fail(LifecycleErrorCode.STALE_GENERATION)
    if request.target.scope_kind is not LifecycleScopeKind.SOURCE or request.target.source_ref is None:
        _fail(LifecycleErrorCode.RESTORE_FORBIDDEN)
    ref = request.target.source_ref
    marker = next((item for item in tombstones if type(item) is TombstoneRecord and item.record_kind is ref.record_kind and item.record_id == ref.record_id and item.owner_scope == request.target.owner_scope), None)
    record = next((item for item in canonical_records if type(item) is StoredRecord and item.record_kind is ref.record_kind and item.record_id == ref.record_id and item.owner_scope == request.target.owner_scope), None)
    if marker is None or record is None:
        _fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
    if marker.reason in {"source_superseded", "source_erased"}:
        _fail(LifecycleErrorCode.RESTORE_FORBIDDEN)
    return RestorePlan(ref, marker.revision, record)


__all__ = ["TOMBSTONE_EVIDENCE_SCHEMA", "LifecycleTombstonePolicyError", "RestorePlan", "TombstoneEvidence", "TombstoneInstruction", "TombstonePlan", "authorize_restore", "evidence_from_committed_tombstones", "plan_tombstones"]


# ULO-02 v6 policy and result boundary.  The legacy value-object surface above
# remains readable for existing USI fixtures; v6 plans are sealed by the
# factory below and never accept caller-supplied candidate collections.
import hashlib as _hashlib


_FACTORY_TOKEN = object()
_FACTORY_OBJECTS: dict[int, object] = {}
_ISSUED_PLANS: dict[int, "_IssuedPlan"] = {}
_ISSUED_RESULTS: dict[int, "_IssuedResult"] = {}
_ISSUED_EVIDENCE: dict[int, tuple[object, ...]] = {}


@dataclass(frozen=True, slots=True)
class _IssuedPlan:
    request_json: str
    snapshot_values: tuple[object, ...]
    instruction_values: tuple[tuple[object, ...], ...]
    candidate_values: tuple[tuple[object, ...], ...]
    plan_digest: str
    capability: object
    limit: int


@dataclass(frozen=True, slots=True)
class _IssuedResult:
    request_json: str
    plan_digest: str
    receipt_digest: str
    evidence_digest: str
    receipt: object
    evidence_values: tuple[tuple[object, ...], ...]
    committed_snapshot_values: tuple[object, ...]
    store_identity: int


def _factory_object(value: object) -> object:
    _FACTORY_OBJECTS[id(value)] = value
    return value


def _is_factory_object(value: object) -> bool:
    return _FACTORY_OBJECTS.get(id(value)) is value


def _instruction_values(item: TombstoneInstruction) -> tuple[object, ...]:
    if type(item) is not TombstoneInstruction or not _is_factory_object(item):
        _v6_policy_fail()
    try:
        ref = object.__getattribute__(item, "record_ref")
        revision = object.__getattribute__(item, "record_revision")
        reason = object.__getattribute__(item, "reason")
        owner_scope = object.__getattribute__(item, "owner_scope")
    except Exception:
        _v6_policy_fail()
    if type(ref) is not RecordRef or type(revision) is not int or revision < 1 or type(reason) is not str or _REASON_RE.fullmatch(reason) is None or type(owner_scope) is not str:
        _v6_policy_fail()
    try:
        kind = object.__getattribute__(ref, "record_kind")
        record_id = object.__getattribute__(ref, "record_id")
    except Exception:
        _v6_policy_fail()
    if type(kind) is not RecordKind or type(record_id) is not str:
        _v6_policy_fail()
    return (
        kind.value, record_id, revision, reason, owner_scope,
    )


def _snapshot_values(snapshot: StoreSnapshot) -> tuple[object, ...]:
    if type(snapshot) is not StoreSnapshot:
        _v6_policy_fail()
    try:
        values = tuple(object.__getattribute__(snapshot, name) for name in (
            "revision", "state_hash", "record_count", "tombstone_count", "snapshot_ref"
        ))
    except Exception:
        _v6_policy_fail()
    revision, state_hash, record_count, tombstone_count, snapshot_ref = values
    if (type(revision) is not int or revision < 0 or type(state_hash) is not str
            or type(record_count) is not int or record_count < 0
            or type(tombstone_count) is not int or tombstone_count < 0
            or type(snapshot_ref) is not str):
        _v6_policy_fail()
    return values


def _candidate_values(item: StoredRecord[Any]) -> tuple[object, ...]:
    if type(item) is not StoredRecord:
        _v6_policy_fail()
    try:
        kind = object.__getattribute__(item, "record_kind")
        record_id = object.__getattribute__(item, "record_id")
        owner = object.__getattribute__(item, "owner_scope")
        revision = object.__getattribute__(item, "revision")
        record = object.__getattribute__(item, "record")
        if type(kind) is not RecordKind or type(record_id) is not str or type(owner) is not str or type(revision) is not int:
            _v6_policy_fail()
        if type(record) not in (SourceRecord, SourceVersionRecord, ChunkRecord, EntityRecord, RelationRecord, LineageRecord, ProjectionManifest, DerivedRunRecord, IndexJobRecord):
            _v6_policy_fail()
        canonical = type(record).to_json(record)
    except Exception:
        _v6_policy_fail()
    if type(canonical) is not str:
        _v6_policy_fail()
    return (kind.value, record_id, owner, revision, canonical)


def _evidence_values(item: TombstoneEvidence) -> tuple[object, ...]:
    if type(item) is not TombstoneEvidence or not _is_factory_object(item):
        _v6_policy_fail()
    try:
        ref = object.__getattribute__(item, "record_ref")
        values = tuple(object.__getattribute__(item, name) for name in (
            "owner_scope", "tombstone_revision", "snapshot_ref", "request_id",
            "disposition", "plan_digest", "base_snapshot_ref", "reason_code",
            "reason_commitment", "evidence_digest",
        ))
    except Exception:
        _v6_policy_fail()
    if type(ref) is not RecordRef:
        _v6_policy_fail()
    try:
        kind = object.__getattribute__(ref, "record_kind")
        record_id = object.__getattribute__(ref, "record_id")
    except Exception:
        _v6_policy_fail()
    if type(kind) is not RecordKind or type(record_id) is not str:
        _v6_policy_fail()
    owner_scope, revision, snapshot_ref, request_id, disposition, plan_digest, base_ref, reason_code, reason_commitment, evidence_digest = values
    if (type(owner_scope) is not str or type(revision) is not int or type(snapshot_ref) is not str
            or any(type(v) is not str for v in (request_id, disposition, plan_digest, base_ref, reason_code, reason_commitment, evidence_digest))):
        _v6_policy_fail()
    return (kind.value, record_id, owner_scope, revision, snapshot_ref, request_id, disposition, plan_digest, base_ref, reason_code, reason_commitment, evidence_digest)


def _issue_plan(plan: TombstonePlan, *, request: LifecycleRequest, snapshot: StoreSnapshot, instructions: tuple[TombstoneInstruction, ...], candidates: tuple[StoredRecord[Any], ...], capability: object, limit: int) -> TombstonePlan:
    _ISSUED_PLANS[id(plan)] = _IssuedPlan(
        _lifecycle_request_projection(request)[1], _snapshot_values(snapshot),
        tuple(_instruction_values(item) for item in instructions),
        tuple(_candidate_values(item) for item in candidates),
        plan.plan_digest, capability, limit,
    )
    return plan


def _issued_plan(plan: object) -> _IssuedPlan | None:
    if type(plan) is not TombstonePlan or not _is_factory_object(plan):
        return None
    issued = _ISSUED_PLANS.get(id(plan))
    return issued if issued is not None else None


def _verified_result_projection(result: object, request: LifecycleRequest, store: object) -> _IssuedResult | None:
    if type(result) is not TombstoneCommitResult or not _is_factory_object(result):
        return None
    try:
        token = object.__getattribute__(result, "_factory_token")
        request_json = _lifecycle_request_projection(request)[1]
    except Exception:
        return None
    if token is not _FACTORY_TOKEN:
        return None
    issued = _ISSUED_RESULTS.get(id(result))
    if issued is None or issued.request_json != request_json or issued.store_identity != id(store):
        return None
    try:
        plan_digest = object.__getattribute__(result, "plan_digest")
        receipt_digest = object.__getattribute__(result, "receipt_digest")
        evidence_digest = object.__getattribute__(result, "evidence_digest")
        receipt = object.__getattribute__(result, "receipt")
        committed = object.__getattribute__(result, "committed_snapshot")
        evidence = object.__getattribute__(result, "evidence")
        if (
            type(plan_digest) is not str or plan_digest != issued.plan_digest
            or type(receipt_digest) is not str or receipt_digest != issued.receipt_digest
            or type(evidence_digest) is not str or evidence_digest != issued.evidence_digest
            or receipt is not issued.receipt
            or _snapshot_values(committed) != issued.committed_snapshot_values
            or type(evidence) is not tuple
            or tuple(_evidence_values(item) for item in evidence) != issued.evidence_values
        ):
            return None
    except Exception:
        return None
    return issued


@dataclass(frozen=True, slots=True, init=False)
class TombstoneInstruction:
    record_ref: RecordRef
    record_revision: int
    reason: str
    owner_scope: str
    _factory_token: object

    def __init__(self, record_ref: RecordRef, record_revision: int, reason: str, owner_scope: str = "", *, _factory_token: object | None = None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            _fail()
        if type(record_ref) is not RecordRef or type(record_revision) is not int or record_revision < 1 or type(reason) is not str or _REASON_RE.fullmatch(reason) is None or type(owner_scope) is not str:
            _fail()
        object.__setattr__(self, "record_ref", record_ref)
        object.__setattr__(self, "record_revision", record_revision)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "owner_scope", owner_scope)
        object.__setattr__(self, "_factory_token", _factory_token)


@dataclass(frozen=True, slots=True, init=False)
class TombstonePlan:
    request: LifecycleRequest
    base_snapshot: StoreSnapshot
    instructions: tuple[TombstoneInstruction, ...]
    plan_digest: str
    _store_binding: object
    _factory_token: object

    def __init__(self, request: LifecycleRequest, base_snapshot: StoreSnapshot, instructions: tuple[TombstoneInstruction, ...], *, _plan_digest: str, _store_binding: object, _factory_token: object | None = None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            _fail()
        if type(request) is not LifecycleRequest or type(base_snapshot) is not StoreSnapshot or type(instructions) is not tuple or any(type(item) is not TombstoneInstruction for item in instructions):
            _fail()
        if len(instructions) > MAX_PAGE_SIZE:
            _fail()
        digest = _plan_digest
        if re.fullmatch(r"usi_plan_[0-9a-f]{64}", digest) is None:
            _fail()
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "base_snapshot", base_snapshot)
        object.__setattr__(self, "instructions", instructions)
        object.__setattr__(self, "plan_digest", digest)
        object.__setattr__(self, "_store_binding", _store_binding)
        object.__setattr__(self, "_factory_token", _factory_token)

    @property
    def items(self) -> tuple[TombstoneInstruction, ...]:
        return self.instructions


@dataclass(frozen=True, slots=True, init=False)
class TombstoneEvidence:
    record_ref: RecordRef
    owner_scope: str
    tombstone_revision: int
    snapshot_ref: str
    request_id: str
    disposition: str
    plan_digest: str
    base_snapshot_ref: str
    reason_code: str
    reason_commitment: str
    evidence_digest: str
    _factory_token: object

    def __init__(self, record_ref: RecordRef, owner_scope: str, tombstone_revision: int, snapshot_ref: str, *, request_id: str, disposition: str, plan_digest: str, base_snapshot_ref: str, reason_code: str, reason_commitment: str, evidence_digest: str, _factory_token: object | None = None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            _fail()
        if type(record_ref) is not RecordRef or type(owner_scope) is not str or type(tombstone_revision) is not int or tombstone_revision < 1 or type(snapshot_ref) is not str or not re.fullmatch(r"usi_snapshot_[0-9a-f]{64}", snapshot_ref):
            _fail()
        for value in (request_id, disposition, plan_digest, base_snapshot_ref, reason_code, reason_commitment, evidence_digest):
            if type(value) is not str:
                _fail()
        for field_name, value in (("request_id", request_id), ("plan_digest", plan_digest), ("base_snapshot_ref", base_snapshot_ref), ("evidence_digest", evidence_digest)):
            if value and field_name == "request_id" and re.fullmatch(r"usi_lifecycle_[0-9a-f]{64}", value) is None:
                _fail()
            if value and field_name == "plan_digest" and re.fullmatch(r"(?:sha256:|usi_plan_)[0-9a-f]{64}", value) is None:
                _fail()
            if value and field_name == "evidence_digest" and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
                _fail()
        for name, value in (("record_ref", record_ref), ("owner_scope", owner_scope), ("tombstone_revision", tombstone_revision), ("snapshot_ref", snapshot_ref), ("request_id", request_id), ("disposition", disposition), ("plan_digest", plan_digest), ("base_snapshot_ref", base_snapshot_ref), ("reason_code", reason_code), ("reason_commitment", reason_commitment), ("evidence_digest", evidence_digest)):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_factory_token", _factory_token)

    def to_dict(self) -> dict[str, Any]:
        if not self.request_id:
            return {"schema": TOMBSTONE_EVIDENCE_SCHEMA, "record_kind": self.record_ref.record_kind.value, "record_id": self.record_ref.record_id, "owner_scope": self.owner_scope, "tombstone_revision": self.tombstone_revision, "snapshot_ref": self.snapshot_ref}
        return {"schema": TOMBSTONE_EVIDENCE_SCHEMA, "request_id": self.request_id, "disposition": self.disposition, "record_kind": self.record_ref.record_kind.value, "record_id": self.record_ref.record_id, "owner_scope": self.owner_scope, "previous_record_revision": self.tombstone_revision - 1, "tombstone_revision": self.tombstone_revision, "plan_digest": self.plan_digest, "base_snapshot_ref": self.base_snapshot_ref, "store_snapshot_ref": self.snapshot_ref, "reason_code": self.reason_code, "reason_commitment": self.reason_commitment, "evidence_digest": self.evidence_digest}

    def to_json(self) -> str:
        return _canonical(self.to_dict())


@dataclass(frozen=True, slots=True, init=False)
class TombstoneCommitResult:
    plan_digest: str
    receipt: object
    evidence: tuple[TombstoneEvidence, ...]
    evidence_digest: str
    receipt_digest: str
    committed_snapshot: StoreSnapshot
    _store_binding: object
    _factory_token: object

    def __init__(self, plan_digest: str, receipt: object, evidence: tuple[TombstoneEvidence, ...], evidence_digest: str, receipt_digest: str, committed_snapshot: StoreSnapshot, _store_binding: object, *, _factory_token: object | None = None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            _fail()
        for name, value in (("plan_digest", plan_digest), ("receipt", receipt), ("evidence", evidence), ("evidence_digest", evidence_digest), ("receipt_digest", receipt_digest), ("committed_snapshot", committed_snapshot), ("_store_binding", _store_binding), ("_factory_token", _factory_token)):
            object.__setattr__(self, name, value)


def _v6_policy_fail(code: LifecycleErrorCode = LifecycleErrorCode.TOMBSTONE_MISMATCH) -> None:
    raise LifecycleTombstonePolicyError(code) from None


def _v6_rank(item: StoredRecord[Any]) -> tuple[int, str, str]:
    return ({RecordKind.LINEAGE: 0, RecordKind.RELATION: 0, RecordKind.ENTITY: 0, RecordKind.CHUNK: 0, RecordKind.PROJECTION: 0, RecordKind.DERIVED_RUN: 0, RecordKind.SOURCE_VERSION: 1, RecordKind.SOURCE: 2, RecordKind.JOB: 3}.get(item.record_kind, 4), item.record_kind.value, item.record_id)


def _v6_project_evidence(item: object) -> tuple[str, str]:
    if type(item) is not EvidenceRef:
        _v6_policy_fail()
    try:
        kind = object.__getattribute__(item, "record_kind")
        record_id = object.__getattribute__(item, "record_id")
        item_source = object.__getattribute__(item, "source_id")
        item_version = object.__getattribute__(item, "source_version_id")
        locator = object.__getattribute__(item, "locator")
        content_hash = object.__getattribute__(item, "content_hash")
        policy = object.__getattribute__(item, "policy_evidence")
    except Exception:
        _v6_policy_fail()
    if (type(kind) is not RecordKind or type(record_id) is not str or not record_id
            or type(item_source) is not str or not item_source
            or type(item_version) is not str or not item_version
            or (locator is not None and type(locator) not in (TextRangeLocator, PageRangeLocator, RowRangeLocator, MessageRangeLocator, CodeRangeLocator))
            or type(content_hash) is not str or type(policy) is not PolicyEvidence):
        _v6_policy_fail()
    if locator is not None:
        locator_fields = {
            TextRangeLocator: ("start_char", "end_char"),
            PageRangeLocator: ("start_page", "end_page"),
            RowRangeLocator: ("table_ref", "start_row", "end_row"),
            MessageRangeLocator: ("thread_ref", "start_message", "end_message"),
            CodeRangeLocator: ("path", "start_line", "start_column", "end_line", "end_column"),
        }.get(type(locator))
        if locator_fields is None:
            _v6_policy_fail()
        try:
            leaf_values = tuple(object.__getattribute__(locator, name) for name in locator_fields)
        except Exception:
            _v6_policy_fail()
        if any(type(value) not in (str, int) for value in leaf_values):
            _v6_policy_fail()
    try:
        policy_values = tuple(object.__getattribute__(policy, field) for field in (
            "parent_kind", "parent_id", "source_id", "source_version_id", "owner_scope", "classification", "content_policy", "evidence_id"
        ))
    except Exception:
        _v6_policy_fail()
    if (type(policy_values[0]) is not RecordKind or any(type(v) is not str for v in policy_values[1:5])
            or type(policy_values[5]) is not Classification or type(policy_values[6]) is not ContentPolicy
            or type(policy_values[7]) is not str):
        _v6_policy_fail()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash) is None:
        _v6_policy_fail()
    if (policy_values[0] is not kind or policy_values[1] != record_id
            or policy_values[2] != item_source or policy_values[3] != item_version):
        _v6_policy_fail()
    return item_source, item_version


def _v6_evidence_matches(evidence: tuple[object, ...], request: LifecycleRequest) -> bool:
    if not evidence or request.target.source_ref is None:
        _v6_policy_fail()
    source_id = request.target.source_ref.record_id
    version_id = request.target.source_version_ref.record_id if request.target.source_version_ref else ""
    matched = False
    for item in evidence:
        item_source, item_version = _v6_project_evidence(item)
        if item_source != source_id or (version_id and item_version != version_id):
            _v6_policy_fail()
        matched = True
    return matched


def _v6_endpoint_matches(ref: object, records: dict[tuple[RecordKind, str], StoredRecord[Any]], request: LifecycleRequest) -> bool:
    if type(ref) is not RecordRef:
        _v6_policy_fail()
    try:
        kind = object.__getattribute__(ref, "record_kind")
        record_id = object.__getattribute__(ref, "record_id")
    except Exception:
        _v6_policy_fail()
    if type(kind) is not RecordKind or type(record_id) is not str or not record_id:
        _v6_policy_fail()
    endpoint = records.get((kind, record_id))
    if endpoint is None or endpoint.owner_scope != request.target.owner_scope or request.target.source_ref is None:
        _v6_policy_fail()
    source_id = request.target.source_ref.record_id
    version_id = request.target.source_version_ref.record_id if request.target.source_version_ref else ""
    record = endpoint.record
    if type(record) is SourceRecord:
        matched = not version_id and record.source_id == source_id
    elif type(record) is SourceVersionRecord:
        matched = record.source_id == source_id and (not version_id or record.source_version_id == version_id)
    elif type(record) in (ChunkRecord, EntityRecord):
        matched = record.source_id == source_id and (not version_id or record.source_version_id == version_id)
    else:
        _v6_policy_fail()
    if not matched:
        _v6_policy_fail()
    return True


def _v6_matches(item: StoredRecord[Any], request: LifecycleRequest, records: dict[tuple[RecordKind, str], StoredRecord[Any]]) -> bool:
    if item.owner_scope != request.target.owner_scope or request.target.source_ref is None:
        return False
    source_ref = request.target.source_ref
    version_ref = request.target.source_version_ref
    record = item.record
    if request.target.scope_kind is LifecycleScopeKind.SOURCE:
        if type(record) is SourceRecord:
            return record.source_id == source_ref.record_id
        if type(record) is SourceVersionRecord:
            return record.source_id == source_ref.record_id
        if type(record) in (ChunkRecord, EntityRecord):
            return record.source_id == source_ref.record_id
        if type(record) is RelationRecord:
            return (
                _v6_evidence_matches(record.evidence_refs, request)
                and _v6_endpoint_matches(record.source_ref, records, request)
                and _v6_endpoint_matches(record.target_ref, records, request)
            )
        if type(record) is LineageRecord:
            return _v6_evidence_matches((record.previous, record.current), request)
        if type(record) is ProjectionManifest:
            return _v6_evidence_matches(record.input_evidence, request)
        if type(record) is DerivedRunRecord:
            if record.source_scope.source_ids != (source_ref.record_id,):
                _v6_policy_fail()
            return _v6_evidence_matches(record.input_evidence, request)
        return False
    if request.target.scope_kind is LifecycleScopeKind.SOURCE_VERSION and version_ref is not None:
        if type(record) is SourceRecord:
            return False
        if type(record) is SourceVersionRecord:
            return record.source_id == source_ref.record_id and record.source_version_id == version_ref.record_id
        if type(record) in (ChunkRecord, EntityRecord):
            return record.source_id == source_ref.record_id and record.source_version_id == version_ref.record_id
        if type(record) is RelationRecord:
            return (
                _v6_evidence_matches(record.evidence_refs, request)
                and _v6_endpoint_matches(record.source_ref, records, request)
                and _v6_endpoint_matches(record.target_ref, records, request)
            )
        if type(record) is LineageRecord:
            return _v6_evidence_matches((record.previous, record.current), request)
        if type(record) is ProjectionManifest:
            return _v6_evidence_matches(record.input_evidence, request)
        if type(record) is DerivedRunRecord:
            if (
                record.source_scope.source_ids != (source_ref.record_id,)
                or record.source_scope.source_version_ids != (version_ref.record_id,)
            ):
                _v6_policy_fail()
            return _v6_evidence_matches(record.input_evidence, request)
    return False


def _v6_selected(records: tuple[StoredRecord[Any], ...], request: LifecycleRequest) -> tuple[StoredRecord[Any], ...]:
    # Validate every relation endpoint/evidence leaf before constructing lookup
    # keys or resolving either endpoint against the record graph.
    if type(records) is not tuple or type(request) is not LifecycleRequest:
        _v6_policy_fail()
    for item in records:
        if type(item) is not StoredRecord:
            _v6_policy_fail()
        try:
            record = object.__getattribute__(item, "record")
            kind = object.__getattribute__(item, "record_kind")
            record_id = object.__getattribute__(item, "record_id")
        except Exception:
            _v6_policy_fail()
        if type(kind) is not RecordKind or type(record_id) is not str or not record_id:
            _v6_policy_fail()
        if type(record) is RelationRecord:
            try:
                source_ref = object.__getattribute__(record, "source_ref")
                target_ref = object.__getattribute__(record, "target_ref")
                evidence = object.__getattribute__(record, "evidence_refs")
            except Exception:
                _v6_policy_fail()
            for ref in (source_ref, target_ref):
                if type(ref) is not RecordRef:
                    _v6_policy_fail()
                try:
                    ref_kind = object.__getattribute__(ref, "record_kind")
                    ref_id = object.__getattribute__(ref, "record_id")
                except Exception:
                    _v6_policy_fail()
                if type(ref_kind) is not RecordKind or type(ref_id) is not str or not ref_id:
                    _v6_policy_fail()
            if type(evidence) is not tuple:
                _v6_policy_fail()
            for evidence_item in evidence:
                _v6_project_evidence(evidence_item)
    by_ref: dict[tuple[RecordKind, str], StoredRecord[Any]] = {}
    for item in records:
        if type(item) is not StoredRecord:
            _v6_policy_fail()
        key = (item.record_kind, item.record_id)
        if key in by_ref:
            _v6_policy_fail()
        by_ref[key] = item
    return tuple(item for item in records if _v6_matches(item, request, by_ref))


def _v6_plan_digest(request: LifecycleRequest, base_snapshot: StoreSnapshot, instructions: tuple[TombstoneInstruction, ...]) -> str:
    request_values = json.loads(_lifecycle_request_projection(request)[1])
    revision, state_hash, record_count, tombstone_count, snapshot_ref = _snapshot_values(base_snapshot)
    instruction_values = tuple(_instruction_values(item) for item in instructions)
    payload = {"request": request_values, "base_snapshot": {"revision": revision, "state_hash": state_hash, "record_count": record_count, "tombstone_count": tombstone_count, "snapshot_ref": snapshot_ref}, "instructions": [{"record_kind": kind, "record_id": record_id, "record_revision": rev, "reason": reason, "owner_scope": owner} for kind, record_id, rev, reason, owner in instruction_values]}
    return "usi_plan_" + _hashlib.sha256(_canonical(payload).encode("ascii")).hexdigest()


def _new_instruction(record_ref: RecordRef, revision: int, reason: str, owner_scope: str) -> TombstoneInstruction:
    return _factory_object(TombstoneInstruction(record_ref, revision, reason, owner_scope, _factory_token=_FACTORY_TOKEN))  # type: ignore[return-value]


def _new_plan(request: LifecycleRequest, snapshot: StoreSnapshot, instructions: tuple[TombstoneInstruction, ...], binding: object) -> TombstonePlan:
    return _factory_object(TombstonePlan(request, snapshot, instructions, _plan_digest=_v6_plan_digest(request, snapshot, instructions), _store_binding=binding, _factory_token=_FACTORY_TOKEN))  # type: ignore[return-value]


def _read_all_exact(store: object, snapshot: StoreSnapshot, capability: object, read_type: type, owner_scope: str) -> tuple[StoredRecord[Any], ...]:
    read = None
    try:
        read = capability.begin_read(capability.store, snapshot)
        from src.unified_source_index_stores import _ulo02_exact_transaction
        _ulo02_exact_transaction(read, read_type, write=False)
        selected: list[StoredRecord[Any]] = []
        for kind in RecordKind:
            page = capability.read_list(read, kind, owner_scope=owner_scope, limit=MAX_PAGE_SIZE)
            if type(page) is not StorePage or page.next_cursor:
                _v6_policy_fail()
            if any(type(item) is not StoredRecord for item in page.items):
                _v6_policy_fail()
            selected.extend(page.items)
        return tuple(selected)
    except LifecycleTombstonePolicyError:
        raise
    except Exception:
        _v6_policy_fail()
    finally:
        if read is not None:
            try:
                capability.read_close(read)
            except Exception:
                _v6_policy_fail()


def plan_tombstones(store: object, request: LifecycleRequest, *, base_snapshot: StoreSnapshot, limit: int = MAX_PAGE_SIZE) -> TombstonePlan:
    if type(request) is not LifecycleRequest or type(limit) is not int or limit < 1 or limit > MAX_PAGE_SIZE:
        _v6_policy_fail()
    request_json = _lifecycle_request_projection(request)[1]
    snapshot_values = _snapshot_values(base_snapshot)
    try:
        request = LifecycleRequest.from_json(request_json)
        base_snapshot = StoreSnapshot(*snapshot_values)
    except Exception:
        _v6_policy_fail()
    if request.action_kind is LifecycleActionKind.RESTORE or request.target.scope_kind is LifecycleScopeKind.OWNER:
        _v6_policy_fail(LifecycleErrorCode.OWNER_MISMATCH)
    from src.unified_source_index_stores import UnifiedSourceIndexStoreError, _ulo02_capability_matches, _ulo02_store_capability
    try:
        capability = _ulo02_store_capability(store)
    except UnifiedSourceIndexStoreError:
        _v6_policy_fail()
    if not _ulo02_capability_matches(capability) or capability.snapshot_values != snapshot_values:
        _v6_policy_fail(LifecycleErrorCode.REVISION_MISMATCH)
    binding = capability.binding
    read_type = capability.read_type
    reason = {LifecycleActionKind.ARCHIVE: "source_archived", LifecycleActionKind.ACCESS_REVOKE: "access_revoked", LifecycleActionKind.SUPERSEDE: "source_superseded", LifecycleActionKind.DELETE: "source_deleted", LifecycleActionKind.ERASE: "source_erased"}.get(request.action_kind)
    if reason is None:
        _v6_policy_fail()
    selected = list(_v6_selected(_read_all_exact(store, base_snapshot, capability, read_type, request.target.owner_scope), request))
    selected.sort(key=_v6_rank)
    if not selected or len(selected) > limit:
        _v6_policy_fail(LifecycleErrorCode.TOMBSTONE_MISMATCH)
    if len({(item.record_kind, item.record_id) for item in selected}) != len(selected):
        _v6_policy_fail()
    candidates = tuple(selected)
    instructions = tuple(_new_instruction(RecordRef(item.record_kind, item.record_id), item.revision, reason, item.owner_scope) for item in candidates)
    plan = _new_plan(request, base_snapshot, instructions, binding)
    return _issue_plan(plan, request=request, snapshot=base_snapshot, instructions=instructions, candidates=candidates, capability=capability, limit=limit)


def commit_tombstone_plan(store: object, plan: TombstonePlan) -> TombstoneCommitResult:
    issued = _issued_plan(plan)
    if issued is None:
        _v6_policy_fail()
    try:
        token = object.__getattribute__(plan, "_factory_token")
        public_request = object.__getattribute__(plan, "request")
        public_snapshot = object.__getattribute__(plan, "base_snapshot")
        public_instructions = object.__getattribute__(plan, "instructions")
        public_digest = object.__getattribute__(plan, "plan_digest")
        public_binding = object.__getattribute__(plan, "_store_binding")
    except Exception:
        _v6_policy_fail()
    if token is not _FACTORY_TOKEN:
        _v6_policy_fail()
    if (
        type(public_request) is not LifecycleRequest
        or type(public_snapshot) is not StoreSnapshot
        or type(public_instructions) is not tuple
        or type(public_digest) is not str
        or type(public_binding) is not int
    ):
        _v6_policy_fail()
    public_request_json = _lifecycle_request_projection(public_request)[1]
    public_snapshot_values = _snapshot_values(public_snapshot)
    public_instruction_values = tuple(_instruction_values(item) for item in public_instructions)
    if (public_request_json != issued.request_json
            or public_snapshot_values != issued.snapshot_values
            or public_digest != issued.plan_digest
            or public_instruction_values != issued.instruction_values):
        _v6_policy_fail()
    from src.unified_source_index_stores import (
        UnifiedSourceIndexStoreError, _ulo02_capability_matches, _ulo02_exact_transaction,
        commit_tombstone_batch, verify_tombstone_commit,
    )
    try:
        request = LifecycleRequest.from_json(issued.request_json)
        base_snapshot = StoreSnapshot(*issued.snapshot_values)
    except Exception:
        _v6_policy_fail()
    capability = issued.capability
    if type(capability) is not type(issued.capability) or capability.store is not store:
        _v6_policy_fail()
    if not _ulo02_capability_matches(capability):
        _v6_policy_fail(LifecycleErrorCode.REVISION_MISMATCH)
    current = capability.current_snapshot(store)
    if _snapshot_values(current) != issued.snapshot_values:
        _v6_policy_fail(LifecycleErrorCode.REVISION_MISMATCH)
    binding = capability.binding
    read_type = capability.read_type
    reason = {LifecycleActionKind.ARCHIVE: "source_archived", LifecycleActionKind.ACCESS_REVOKE: "access_revoked", LifecycleActionKind.SUPERSEDE: "source_superseded", LifecycleActionKind.DELETE: "source_deleted", LifecycleActionKind.ERASE: "source_erased"}.get(request.action_kind)
    if reason is None:
        _v6_policy_fail()
    candidates = _v6_selected(_read_all_exact(store, base_snapshot, capability, read_type, request.target.owner_scope), request)
    candidates = tuple(sorted(candidates, key=_v6_rank))
    if not candidates or len(candidates) > issued.limit or tuple(_candidate_values(item) for item in candidates) != issued.candidate_values:
        _v6_policy_fail()
    commands = tuple(_new_instruction(RecordRef(item.record_kind, item.record_id), item.revision, reason, item.owner_scope) for item in candidates)
    if (
        tuple(_instruction_values(item) for item in commands) != issued.instruction_values
        or _v6_plan_digest(request, base_snapshot, commands) != issued.plan_digest
        or public_binding != capability.binding
    ):
        _v6_policy_fail()
    authority_ref = "usi_authority_" + _hashlib.sha256(request.request_id.encode("ascii")).hexdigest()
    read = None
    try:
        receipt = commit_tombstone_batch(store, authority_ref=authority_ref, plan_digest=issued.plan_digest, base_snapshot=base_snapshot, commands=commands, _trusted_capability=capability)
        verify_tombstone_commit(store, receipt, expected_authority_ref=authority_ref, expected_plan_digest=issued.plan_digest, expected_commands=commands, _trusted_capability=capability, _allow_own_commit_revision=True)
        read = capability.begin_read(capability.store, receipt.committed_snapshot)
        _ulo02_exact_transaction(read, read_type, write=False)
        markers = tuple(capability.read_require(read, item.record_ref.record_kind, item.record_ref.record_id, owner_scope=item.owner_scope, include_tombstone=True) for item in commands)
    except UnifiedSourceIndexStoreError:
        _v6_policy_fail()
    except Exception:
        _v6_policy_fail()
    finally:
        if read is not None:
            try:
                capability.read_close(read)
            except Exception:
                _v6_policy_fail()
    evidence_items: list[TombstoneEvidence] = []
    for item, marker in zip(commands, markers, strict=True):
        if type(marker) is not TombstoneRecord or marker.previous_revision != item.record_revision:
            _v6_policy_fail()
        parts = marker.reason.split(":")
        if len(parts) != 3 or parts[0] != "ulo2" or parts[1] != item.reason or re.fullmatch(r"[0-9a-f]{64}", parts[2]) is None:
            _v6_policy_fail()
        evidence_items.append(_factory_object(TombstoneEvidence(item.record_ref, marker.owner_scope, marker.revision, receipt.committed_snapshot.snapshot_ref, request_id=request.request_id, disposition=request.disposition.value, plan_digest=issued.plan_digest, base_snapshot_ref=receipt.base_snapshot.snapshot_ref, reason_code=item.reason, reason_commitment=parts[-1], evidence_digest="", _factory_token=_FACTORY_TOKEN)))
    evidence_digest = "sha256:" + _hashlib.sha256(_canonical([item.to_dict() | {"evidence_digest": ""} for item in evidence_items]).encode("ascii")).hexdigest()
    evidence = tuple(_factory_object(TombstoneEvidence(item.record_ref, item.owner_scope, item.tombstone_revision, item.snapshot_ref, request_id=item.request_id, disposition=item.disposition, plan_digest=item.plan_digest, base_snapshot_ref=item.base_snapshot_ref, reason_code=item.reason_code, reason_commitment=item.reason_commitment, evidence_digest=evidence_digest, _factory_token=_FACTORY_TOKEN)) for item in evidence_items)
    for item in evidence:
        _ISSUED_EVIDENCE[id(item)] = _evidence_values(item)
    result = _factory_object(TombstoneCommitResult(issued.plan_digest, receipt, evidence, evidence_digest, receipt.receipt_digest, receipt.committed_snapshot, binding, _factory_token=_FACTORY_TOKEN))
    _ISSUED_RESULTS[id(result)] = _IssuedResult(issued.request_json, issued.plan_digest, receipt.receipt_digest, evidence_digest, receipt, tuple(_evidence_values(item) for item in evidence), _snapshot_values(receipt.committed_snapshot), id(store))
    return result  # type: ignore[return-value]


__all__ += ["TombstoneCommitResult", "commit_tombstone_plan"]

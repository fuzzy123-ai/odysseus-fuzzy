"""Backend-neutral transactional store protocols for the Unified Source Index.

USI-02 deliberately contains no SQLite, Chroma, filesystem, or network code.
The in-memory implementation is an executable contract fake: it provides
snapshot-isolated reads, explicit optimistic writes, bounded cursor paging,
and tombstones that reserve deleted identities until an explicit restore.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum, StrEnum
import base64
import hashlib
import json
import re
from threading import RLock
from types import MappingProxyType
from typing import Generic, Mapping, Protocol, TypeAlias, TypeVar, cast, runtime_checkable

import src.unified_source_index_contract as usi_contract

from src.unified_source_index_contract import (
    ChunkRecord,
    CodeOccurrenceRecords,
    DerivedRunRecord,
    EntityRecord,
    ForgeCodeOccurrenceEvidence,
    IndexJobRecord,
    LineageRecord,
    ProjectionManifest,
    RecordKind,
    RecordRef,
    RelationRecord,
    SourceRecord,
    SourceVersionRecord,
    canonical_json,
    record_from_json,
)


USI_STORE_SCHEMA = "odysseus.unified_source_index.store.v1"
MAX_PAGE_SIZE = 1_000
MAX_CURSOR_CHARS = 2_048

_OWNER_SCOPE_RE = re.compile(
    r"^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9][A-Za-z0-9._/@+-]{0,159}$"
)
_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SNAPSHOT_REF_RE = re.compile(r"^usi_snapshot_[0-9a-f]{64}$")


class UnifiedSourceIndexStoreError(ValueError):
    """Base error for invalid or unsafe store operations."""


class StoreConflictError(UnifiedSourceIndexStoreError):
    """Raised when an optimistic snapshot or record revision is stale."""

    def __init__(
        self,
        message: str,
        *,
        expected_revision: int,
        actual_revision: int,
    ) -> None:
        super().__init__(message)
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


class StoreNotFoundError(UnifiedSourceIndexStoreError):
    """Raised when a required owner-scoped record does not exist."""


class StoreCursorError(UnifiedSourceIndexStoreError):
    """Raised when a cursor is invalid or belongs to another snapshot/query."""


class StoreTransactionClosedError(UnifiedSourceIndexStoreError):
    """Raised when a closed transaction is reused."""


class StoreTombstoneError(UnifiedSourceIndexStoreError):
    """Raised when an operation would silently bypass a tombstone."""


class StoreInterfaceKind(StrEnum):
    MEMORY = "memory"
    SOURCE = "source"
    CHUNK = "chunk"
    EMBEDDING = "embedding"
    GRAPH = "graph"
    JOB = "job"
    REVIEW = "review"
    QUERY_CACHE = "query_cache"


StoreRecord: TypeAlias = (
    SourceRecord
    | SourceVersionRecord
    | ChunkRecord
    | EntityRecord
    | RelationRecord
    | LineageRecord
    | ProjectionManifest
    | DerivedRunRecord
    | IndexJobRecord
)
StoreKey: TypeAlias = tuple[RecordKind, str]

TRecord = TypeVar("TRecord", bound=StoreRecord)


@dataclass(frozen=True, slots=True)
class StoreSnapshot:
    """Content-free identity for one immutable committed store state."""

    revision: int
    state_hash: str
    record_count: int
    tombstone_count: int
    snapshot_ref: str = ""

    def __post_init__(self) -> None:
        revision = _revision(self.revision, "revision")
        state_hash = _sha256(self.state_hash, "state_hash")
        record_count = _count(self.record_count, "record_count")
        tombstone_count = _count(self.tombstone_count, "tombstone_count")
        expected = _snapshot_ref(
            revision,
            state_hash,
            record_count,
            tombstone_count,
        )
        if type(self.snapshot_ref) is not str:
            raise UnifiedSourceIndexStoreError(
                "snapshot_ref must use exact text"
            )
        if self.snapshot_ref and self.snapshot_ref != expected:
            raise UnifiedSourceIndexStoreError("snapshot_ref does not match snapshot state")
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "state_hash", state_hash)
        object.__setattr__(self, "record_count", record_count)
        object.__setattr__(self, "tombstone_count", tombstone_count)
        object.__setattr__(self, "snapshot_ref", expected)


@dataclass(frozen=True, slots=True)
class StoredRecord(Generic[TRecord]):
    """A domain record paired with its optimistic record revision."""

    record: TRecord
    revision: int

    def __post_init__(self) -> None:
        _record_descriptor(self.record)
        object.__setattr__(self, "revision", _positive_revision(self.revision))

    @property
    def record_kind(self) -> RecordKind:
        return _record_descriptor(self.record)[0]

    @property
    def record_id(self) -> str:
        return _record_descriptor(self.record)[1]

    @property
    def owner_scope(self) -> str:
        return _record_descriptor(self.record)[2]


@dataclass(frozen=True, slots=True)
class TombstoneRecord:
    """Content-free deletion marker that reserves a USI record identity."""

    record_kind: RecordKind
    record_id: str
    owner_scope: str
    previous_revision: int
    revision: int
    reason: str

    def __post_init__(self) -> None:
        kind = _record_kind(self.record_kind)
        record_id = _record_id(kind, self.record_id)
        owner_scope = _owner_scope(self.owner_scope)
        previous_revision = _positive_revision(self.previous_revision)
        revision = _positive_revision(self.revision)
        if revision <= previous_revision:
            raise UnifiedSourceIndexStoreError(
                "tombstone revision must follow the previous record revision"
            )
        reason = _reason(self.reason)
        object.__setattr__(self, "record_kind", kind)
        object.__setattr__(self, "record_id", record_id)
        object.__setattr__(self, "owner_scope", owner_scope)
        object.__setattr__(self, "previous_revision", previous_revision)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "reason", reason)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": f"{USI_STORE_SCHEMA}.tombstone",
            "record_kind": self.record_kind.value,
            "record_id": self.record_id,
            "owner_scope": self.owner_scope,
            "previous_revision": self.previous_revision,
            "revision": self.revision,
            "reason": self.reason,
        }


StorePageItem: TypeAlias = StoredRecord[StoreRecord] | TombstoneRecord


@dataclass(frozen=True, slots=True)
class StorePage:
    """One bounded, snapshot-stable page of records and optional tombstones."""

    items: tuple[StorePageItem, ...]
    snapshot: StoreSnapshot
    next_cursor: str = ""

    def __post_init__(self) -> None:
        if type(self.snapshot) is not StoreSnapshot:
            raise UnifiedSourceIndexStoreError("page snapshot must be typed")
        if type(self.items) is not tuple or len(self.items) > MAX_PAGE_SIZE:
            raise UnifiedSourceIndexStoreError("page items must be a bounded tuple")
        if not all(type(item) in (StoredRecord, TombstoneRecord) for item in self.items):
            raise UnifiedSourceIndexStoreError("page contains an unsupported item")
        if type(self.next_cursor) is not str or len(self.next_cursor) > MAX_CURSOR_CHARS:
            raise UnifiedSourceIndexStoreError("next_cursor is invalid or unbounded")

    @property
    def clipped(self) -> bool:
        return bool(self.next_cursor)


@runtime_checkable
class StoreReadTransaction(Protocol):
    @property
    def snapshot(self) -> StoreSnapshot: ...

    @property
    def closed(self) -> bool: ...

    def get(
        self,
        record_kind: RecordKind | str,
        record_id: str,
        *,
        owner_scope: str,
        include_tombstone: bool = False,
    ) -> StorePageItem | None: ...

    def list_records(
        self,
        record_kind: RecordKind | str,
        *,
        owner_scope: str,
        limit: int,
        cursor: str = "",
        include_tombstones: bool = False,
    ) -> StorePage: ...

    def close(self) -> None: ...


@runtime_checkable
class StoreWriteTransaction(Protocol):
    @property
    def base_snapshot(self) -> StoreSnapshot: ...

    @property
    def closed(self) -> bool: ...

    def put(
        self,
        record: StoreRecord,
        *,
        expected_record_revision: int | None = None,
    ) -> None: ...

    def compare_and_swap(
        self,
        record: StoreRecord,
        *,
        expected_record_revision: int,
    ) -> None: ...

    def tombstone(
        self,
        record_kind: RecordKind | str,
        record_id: str,
        *,
        owner_scope: str,
        expected_record_revision: int,
        reason: str,
    ) -> None: ...

    def restore(
        self,
        record: StoreRecord,
        *,
        expected_tombstone_revision: int,
    ) -> None: ...

    def commit(self) -> StoreSnapshot: ...

    def rollback(self) -> None: ...


@runtime_checkable
class TransactionalStore(Protocol):
    @property
    def store_interfaces(self) -> frozenset[StoreInterfaceKind]: ...

    def current_snapshot(self) -> StoreSnapshot: ...

    def begin_read(self, snapshot: StoreSnapshot | None = None) -> StoreReadTransaction: ...

    def begin_write(
        self,
        expected: StoreSnapshot | int,
    ) -> StoreWriteTransaction: ...


# These names freeze the MS1A vocabulary while sharing one USI transaction
# boundary. Later backend adapters may expose narrower implementations, but
# they must retain the same explicit snapshot/write contract.
@runtime_checkable
class MemoryStore(TransactionalStore, Protocol):
    pass


@runtime_checkable
class SourceStore(TransactionalStore, Protocol):
    pass


@runtime_checkable
class ChunkStore(TransactionalStore, Protocol):
    pass


@runtime_checkable
class EmbeddingStore(TransactionalStore, Protocol):
    pass


@runtime_checkable
class GraphStore(TransactionalStore, Protocol):
    pass


@runtime_checkable
class JobStore(TransactionalStore, Protocol):
    pass


@runtime_checkable
class ReviewStore(TransactionalStore, Protocol):
    pass


@runtime_checkable
class QueryCacheStore(TransactionalStore, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class _SnapshotState:
    snapshot: StoreSnapshot
    records: Mapping[StoreKey, "_StoredEntry"]
    tombstones: Mapping[StoreKey, "_TombstoneEntry"]
    forge_occurrences: Mapping[str, bytes]


@dataclass(frozen=True, slots=True)
class _StoredEntry:
    record_kind: RecordKind
    record_id: str
    owner_scope: str
    revision: int
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class _TombstoneEntry:
    record_kind: RecordKind
    record_id: str
    owner_scope: str
    previous_revision: int
    revision: int
    reason: str


@dataclass(frozen=True, slots=True)
class _PutMutation:
    record_kind: RecordKind
    record_id: str
    owner_scope: str
    canonical_bytes: bytes
    expected_revision: int
    restore: bool = False


@dataclass(frozen=True, slots=True)
class _TombstoneMutation:
    record_kind: RecordKind
    record_id: str
    owner_scope: str
    expected_revision: int
    reason: str


@dataclass(frozen=True, slots=True)
class _ForgeOccurrenceMutation:
    record_bytes: tuple[bytes, bytes, bytes]
    evidence_bytes: bytes


Mutation: TypeAlias = _PutMutation | _TombstoneMutation


class InMemoryStoreReadTransaction:
    """Immutable read view over exactly one committed snapshot."""

    def __init__(self, state: _SnapshotState) -> None:
        self._state = state
        self._snapshot_binding = _snapshot_values(state.snapshot)
        self._closed = False

    @property
    def snapshot(self) -> StoreSnapshot:
        state = self._capture_operation_state()
        return _copy_snapshot(state.snapshot)

    @property
    def closed(self) -> bool:
        if type(self._closed) is not bool:
            raise UnifiedSourceIndexStoreError(
                "read transaction phase failed integrity validation"
            )
        return self._closed

    def get(
        self,
        record_kind: RecordKind | str,
        record_id: str,
        *,
        owner_scope: str,
        include_tombstone: bool = False,
    ) -> StorePageItem | None:
        kind = _record_kind(record_kind)
        key = (kind, _record_id(kind, record_id))
        owner = _owner_scope(owner_scope)
        include_deleted = _exact_bool(include_tombstone, "include_tombstone")
        state = self._capture_operation_state()
        return self._get_from_state(
            state,
            key,
            owner,
            include_tombstone=include_deleted,
        )

    def require(
        self,
        record_kind: RecordKind | str,
        record_id: str,
        *,
        owner_scope: str,
        include_tombstone: bool = False,
    ) -> StorePageItem:
        kind = _record_kind(record_kind)
        key = (kind, _record_id(kind, record_id))
        owner = _owner_scope(owner_scope)
        include_deleted = _exact_bool(include_tombstone, "include_tombstone")
        state = self._capture_operation_state()
        item = self._get_from_state(
            state,
            key,
            owner,
            include_tombstone=include_deleted,
        )
        if item is None:
            raise StoreNotFoundError("owner-scoped USI record was not found")
        return item

    def require_code_occurrence(
        self,
        *,
        source_id: str,
        source_version_id: str,
        chunk_id: str,
        owner_scope: str,
    ) -> CodeOccurrenceRecords:
        """Resolve one exact parent chain; never substitute an overlapping chunk."""

        source_key = _record_id(RecordKind.SOURCE, source_id)
        version_key = _record_id(RecordKind.SOURCE_VERSION, source_version_id)
        chunk_key = _record_id(RecordKind.CHUNK, chunk_id)
        owner = _owner_scope(owner_scope)
        state = self._capture_operation_state()

        source_item = self._get_from_state(
            state,
            (RecordKind.SOURCE, source_key),
            owner,
            include_tombstone=False,
        )
        version_item = self._get_from_state(
            state,
            (RecordKind.SOURCE_VERSION, version_key),
            owner,
            include_tombstone=False,
        )
        chunk_item = self._get_from_state(
            state,
            (RecordKind.CHUNK, chunk_key),
            owner,
            include_tombstone=False,
        )
        if not all(type(item) is StoredRecord for item in (source_item, version_item, chunk_item)):
            raise StoreNotFoundError("code occurrence parent chain is unavailable")
        try:
            evidence_bytes = state.forge_occurrences.get(chunk_key)
            evidence = (
                None
                if evidence_bytes is None
                else _forge_evidence_from_canonical_bytes(evidence_bytes)
            )
            return CodeOccurrenceRecords(
                cast(SourceRecord, source_item.record),
                cast(SourceVersionRecord, version_item.record),
                cast(ChunkRecord, chunk_item.record),
                evidence,
            )
        except Exception:
            raise StoreNotFoundError("code occurrence parent chain is foreign or inconsistent") from None

    def list_records(
        self,
        record_kind: RecordKind | str,
        *,
        owner_scope: str,
        limit: int,
        cursor: str = "",
        include_tombstones: bool = False,
    ) -> StorePage:
        kind = _record_kind(record_kind)
        owner = _owner_scope(owner_scope)
        page_limit = _page_limit(limit)
        if type(cursor) is not str:
            raise StoreCursorError("cursor is invalid or unbounded")
        include_deleted = _exact_bool(include_tombstones, "include_tombstones")
        state = self._capture_operation_state()
        snapshot = _copy_snapshot(state.snapshot)
        after_id = ""
        if cursor:
            after_id = _decode_cursor(
                cursor,
                snapshot=snapshot,
                record_kind=kind,
                owner_scope=owner,
                include_tombstones=include_deleted,
            )

        items: list[tuple[StoreKey, _StoredEntry | _TombstoneEntry]] = [
            (key, item)
            for key, item in state.records.items()
            if key[0] is kind and item.owner_scope == owner
        ]
        if include_deleted:
            items.extend(
                (key, item)
                for key, item in state.tombstones.items()
                if key[0] is kind and item.owner_scope == owner
            )
        items.sort(key=lambda pair: pair[0][1])
        if after_id:
            items = [pair for pair in items if pair[0][1] > after_id]

        selected = tuple(
            _stored_entry_to_public(key, item)
            if type(item) is _StoredEntry
            else _tombstone_entry_to_public(key, item)
            for key, item in items[:page_limit]
        )
        has_more = len(items) > page_limit
        next_cursor = ""
        if has_more and selected:
            next_cursor = _encode_cursor(
                snapshot=snapshot,
                record_kind=kind,
                owner_scope=owner,
                after_record_id=_page_item_id(selected[-1]),
                include_tombstones=include_deleted,
            )
        return StorePage(selected, snapshot, next_cursor)

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> "InMemoryStoreReadTransaction":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if type(self._closed) is not bool:
            raise UnifiedSourceIndexStoreError(
                "read transaction phase failed integrity validation"
            )
        if self._closed:
            raise StoreTransactionClosedError("read transaction is closed")

    def _capture_operation_state(self) -> _SnapshotState:
        """Return one detached validated state used exclusively by one read call."""

        self._ensure_open()
        state = self._state
        binding = self._snapshot_binding
        if (
            type(binding) is not tuple
            or len(binding) != 5
            or type(binding[0]) is not int
            or type(binding[1]) is not str
            or type(binding[2]) is not int
            or type(binding[3]) is not int
            or type(binding[4]) is not str
        ):
            raise UnifiedSourceIndexStoreError(
                "retained read binding failed integrity validation"
            )
        if type(state) is not _SnapshotState:
            raise UnifiedSourceIndexStoreError(
                "retained read state failed integrity validation"
            )
        snapshot = _copy_snapshot(state.snapshot)
        try:
            detached = InMemoryUnifiedSourceIndexStore._make_state(
                state.records,
                state.tombstones,
                state.forge_occurrences,
                snapshot.revision,
            )
            _validate_forge_occurrence_state(
                detached.records,
                detached.forge_occurrences,
            )
        except Exception:
            raise UnifiedSourceIndexStoreError(
                "retained read state failed integrity validation"
            ) from None
        if (
            _snapshot_values(detached.snapshot) != _snapshot_values(snapshot)
            or _snapshot_values(detached.snapshot) != binding
        ):
            raise UnifiedSourceIndexStoreError(
                "retained read snapshot integrity changed after transaction start"
            )
        return detached

    @staticmethod
    def _get_from_state(
        state: _SnapshotState,
        key: StoreKey,
        owner_scope: str,
        *,
        include_tombstone: bool,
    ) -> StorePageItem | None:
        stored = state.records.get(key)
        if stored is not None:
            return (
                _stored_entry_to_public(key, stored)
                if stored.owner_scope == owner_scope
                else None
            )
        if include_tombstone:
            tombstone = state.tombstones.get(key)
            if tombstone is not None and tombstone.owner_scope == owner_scope:
                return _tombstone_entry_to_public(key, tombstone)
        return None


class InMemoryStoreWriteTransaction:
    """Explicit optimistic transaction; context exit rolls back by default."""

    def __init__(
        self,
        store: "InMemoryUnifiedSourceIndexStore",
        state: _SnapshotState,
    ) -> None:
        self._store = store
        self._state = state
        self._base_snapshot_binding = _snapshot_values(state.snapshot)
        self._mutations: dict[StoreKey, Mutation] = {}
        self._forge_occurrence_mutations: dict[str, _ForgeOccurrenceMutation] = {}
        self._phase = "open"
        self._lock = RLock()

    @property
    def base_snapshot(self) -> StoreSnapshot:
        with self._lock:
            InMemoryUnifiedSourceIndexStore._assert_snapshot_state_integrity(
                self._state
            )
            if _snapshot_values(self._state.snapshot) != self._base_snapshot_binding:
                raise UnifiedSourceIndexStoreError(
                    "write base snapshot changed after transaction start"
                )
            return _copy_snapshot(self._state.snapshot)

    @property
    def closed(self) -> bool:
        with self._lock:
            if type(self._phase) is not str:
                raise UnifiedSourceIndexStoreError(
                    "write transaction phase failed integrity validation"
                )
            return self._phase == "closed"

    def put(
        self,
        record: StoreRecord,
        *,
        expected_record_revision: int | None = None,
    ) -> None:
        with self._lock:
            self._ensure_open()
            captured, captured_bytes = _capture_store_record(record)
            self._stage_captured_record(
                captured,
                captured_bytes,
                expected_record_revision=expected_record_revision,
                allow_forge_occurrence=False,
            )

    def _stage_captured_record(
        self,
        record: StoreRecord,
        canonical_bytes: bytes,
        *,
        expected_record_revision: int | None = None,
        allow_forge_occurrence: bool,
    ) -> None:
        self._ensure_open()
        if not allow_forge_occurrence and _is_forge_bound_record(
            record,
            records=self._state.records,
            staged=self._mutations,
        ):
            raise UnifiedSourceIndexStoreError(
                "Forge-bound records require atomic put_code_occurrence"
            )
        kind, record_id, _ = _record_descriptor(record)
        key = (kind, record_id)
        self._ensure_not_staged(key)
        if key in self._state.tombstones:
            raise StoreTombstoneError(
                "record identity is tombstoned; use restore with its revision"
            )
        actual = _key_revision(self._state.records, self._state.tombstones, key)
        expected = actual if expected_record_revision is None else _revision(
            expected_record_revision,
            "expected_record_revision",
        )
        _require_revision_match(expected, actual, "record revision is stale")
        self._mutations[key] = _PutMutation(
            kind,
            record_id,
            _record_descriptor(record)[2],
            canonical_bytes,
            expected,
        )

    def compare_and_swap(
        self,
        record: StoreRecord,
        *,
        expected_record_revision: int,
    ) -> None:
        self.put(record, expected_record_revision=expected_record_revision)

    def put_code_occurrence(self, occurrence: CodeOccurrenceRecords) -> None:
        """Stage one complete code occurrence atomically in parent-first order."""
        with self._lock:
            self._ensure_open()
            canonical = _capture_forge_occurrence(occurrence)
            records = _records_from_occurrence_mutation(canonical)
            prior_mutations = dict(self._mutations)
            prior_occurrences = dict(self._forge_occurrence_mutations)
            try:
                for record, canonical_bytes in zip(
                    (
                        records.source,
                        records.source_version,
                        records.chunk,
                    ),
                    canonical.record_bytes,
                    strict=True,
                ):
                    self._stage_captured_record(
                        record,
                        canonical_bytes,
                        allow_forge_occurrence=True,
                    )
                chunk_id = records.chunk.chunk_id
                if chunk_id in self._forge_occurrence_mutations:
                    raise UnifiedSourceIndexStoreError(
                        "a Forge occurrence may be staged only once per transaction"
                    )
                self._forge_occurrence_mutations[chunk_id] = canonical
            except UnifiedSourceIndexStoreError:
                self._mutations = prior_mutations
                self._forge_occurrence_mutations = prior_occurrences
                raise

    def tombstone(
        self,
        record_kind: RecordKind | str,
        record_id: str,
        *,
        owner_scope: str,
        expected_record_revision: int,
        reason: str,
    ) -> None:
        with self._lock:
            self._ensure_open()
            kind = _record_kind(record_kind)
            key = (kind, _record_id(kind, record_id))
            self._ensure_not_staged(key)
            owner = _owner_scope(owner_scope)
            stored = self._state.records.get(key)
            if stored is None or stored.owner_scope != owner:
                raise StoreNotFoundError("owner-scoped USI record was not found")
            expected = _revision(expected_record_revision, "expected_record_revision")
            _require_revision_match(expected, stored.revision, "record revision is stale")
            self._reject_forge_parent_tombstone(
                _record_from_canonical_bytes(stored.canonical_bytes)
            )
            self._mutations[key] = _TombstoneMutation(
                kind,
                key[1],
                owner,
                expected,
                _reason(reason),
            )

    def _reject_forge_parent_tombstone(self, record: StoreRecord) -> None:
        """Keep Forge parent cleanup child-first and transactionally explicit."""

        if not _is_forge_bound_record(
            record,
            records=self._state.records,
            staged=self._mutations,
        ):
            return
        if type(record) is SourceRecord and any(
            type(candidate) is SourceVersionRecord
            and candidate.source_id == record.source_id
            for candidate in (
                _record_from_canonical_bytes(item.canonical_bytes)
                for item in self._state.records.values()
            )
        ):
            raise UnifiedSourceIndexStoreError(
                "Forge source cannot be tombstoned while live versions exist"
            )
        if type(record) is SourceVersionRecord and any(
            type(candidate) is ChunkRecord
            and candidate.source_version_id == record.source_version_id
            for candidate in (
                _record_from_canonical_bytes(item.canonical_bytes)
                for item in self._state.records.values()
            )
        ):
            raise UnifiedSourceIndexStoreError(
                "Forge source version cannot be tombstoned while live chunks exist"
            )

    def restore(
        self,
        record: StoreRecord,
        *,
        expected_tombstone_revision: int,
    ) -> None:
        with self._lock:
            self._ensure_open()
            captured, captured_bytes = _capture_store_record(record)
            if _is_forge_bound_record(
                captured,
                records=self._state.records,
                staged=self._mutations,
            ):
                raise UnifiedSourceIndexStoreError(
                    "Forge-bound records require atomic put_code_occurrence"
                )
            kind, record_id, owner = _record_descriptor(captured)
            key = (kind, record_id)
            self._ensure_not_staged(key)
            tombstone = self._state.tombstones.get(key)
            if tombstone is None or tombstone.owner_scope != owner:
                raise StoreNotFoundError("matching owner-scoped tombstone was not found")
            expected = _positive_revision(expected_tombstone_revision)
            _require_revision_match(expected, tombstone.revision, "tombstone revision is stale")
            self._mutations[key] = _PutMutation(
                kind,
                record_id,
                owner,
                captured_bytes,
                expected,
                restore=True,
            )

    def commit(self) -> StoreSnapshot:
        with self._lock:
            self._ensure_open()
            if not self._mutations:
                raise UnifiedSourceIndexStoreError("empty write transaction cannot commit")
            self._phase = "committing"
            try:
                snapshot = self._store._commit(self)
            except UnifiedSourceIndexStoreError:
                self._phase = "closed"
                raise
            except Exception:
                self._phase = "closed"
                raise UnifiedSourceIndexStoreError(
                    "write transaction failed at the store boundary"
                ) from None
            except BaseException:
                self._phase = "closed"
                raise
            self._phase = "closed"
            return _copy_snapshot(snapshot)

    def rollback(self) -> None:
        with self._lock:
            self._mutations = {}
            self._forge_occurrence_mutations = {}
            self._phase = "closed"

    def __enter__(self) -> "InMemoryStoreWriteTransaction":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self.closed:
            self.rollback()

    def _ensure_open(self) -> None:
        if type(self._phase) is not str:
            raise UnifiedSourceIndexStoreError(
                "write transaction phase failed integrity validation"
            )
        if self._phase != "open":
            raise StoreTransactionClosedError("write transaction is closed")
        if (
            type(self._mutations) is not dict
            or type(self._forge_occurrence_mutations) is not dict
        ):
            raise UnifiedSourceIndexStoreError(
                "write transaction mutation state failed integrity validation"
            )
        InMemoryUnifiedSourceIndexStore._assert_snapshot_state_integrity(
            self._state
        )
        if _snapshot_values(self._state.snapshot) != self._base_snapshot_binding:
            raise UnifiedSourceIndexStoreError(
                "write base snapshot changed after transaction start"
            )

    def _ensure_not_staged(self, key: StoreKey) -> None:
        if key in self._mutations:
            raise UnifiedSourceIndexStoreError(
                "a record may be mutated only once per transaction"
            )


class InMemoryUnifiedSourceIndexStore:
    """Thread-safe deterministic USI transaction fake for contracts and tests."""

    _INTERFACES = frozenset(StoreInterfaceKind)

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[StoreKey, _StoredEntry] = {}
        self._tombstones: dict[StoreKey, _TombstoneEntry] = {}
        self._forge_occurrences: dict[str, bytes] = {}
        self._revision = 0
        initial = self._make_state(
            self._records,
            self._tombstones,
            self._forge_occurrences,
            self._revision,
        )
        self._history: dict[int, _SnapshotState] = {0: initial}

    @property
    def store_interfaces(self) -> frozenset[StoreInterfaceKind]:
        return self._INTERFACES

    def current_snapshot(self) -> StoreSnapshot:
        with self._lock:
            self._assert_current_state_integrity()
            return _copy_snapshot(self._history[self._revision].snapshot)

    def begin_read(
        self,
        snapshot: StoreSnapshot | None = None,
    ) -> InMemoryStoreReadTransaction:
        with self._lock:
            if snapshot is None:
                requested = self.current_snapshot()
            else:
                self._assert_authority_envelope()
                requested = _copy_snapshot(snapshot)
            state = self._history.get(requested.revision)
            if state is None or _snapshot_values(state.snapshot) != _snapshot_values(requested):
                raise StoreConflictError(
                    "snapshot is unknown or does not match retained state",
                    expected_revision=requested.revision,
                    actual_revision=self._revision,
                )
            self._assert_snapshot_state_integrity(state)
            return InMemoryStoreReadTransaction(state)

    def begin_write(
        self,
        expected: StoreSnapshot | int,
    ) -> InMemoryStoreWriteTransaction:
        with self._lock:
            self._assert_current_state_integrity()
            if type(expected) is StoreSnapshot:
                expected_snapshot = _copy_snapshot(expected)
                expected_revision = expected_snapshot.revision
            else:
                expected_revision = _revision(expected, "expected")
                expected_snapshot = None
            current = self._history[self._revision]
            if expected_revision != self._revision or (
                expected_snapshot is not None
                and _snapshot_values(expected_snapshot) != _snapshot_values(current.snapshot)
            ):
                raise StoreConflictError(
                    "write snapshot is stale",
                    expected_revision=expected_revision,
                    actual_revision=self._revision,
                )
            return InMemoryStoreWriteTransaction(self, current)

    def _commit(self, transaction: InMemoryStoreWriteTransaction) -> StoreSnapshot:
        with self._lock:
            self._assert_current_state_integrity()
            if type(transaction) is not InMemoryStoreWriteTransaction or transaction._phase != "committing":
                raise UnifiedSourceIndexStoreError("write transaction is not frozen for commit")
            expected_revision = transaction._state.snapshot.revision
            self._assert_snapshot_state_integrity(transaction._state)
            base_authority = self._history.get(expected_revision)
            if (
                _snapshot_values(transaction._state.snapshot)
                != transaction._base_snapshot_binding
                or base_authority is None
                or transaction._base_snapshot_binding
                != _snapshot_values(base_authority.snapshot)
            ):
                raise UnifiedSourceIndexStoreError(
                    "write transaction authority changed before commit"
                )
            if expected_revision != self._revision:
                raise StoreConflictError(
                    "write snapshot became stale before commit",
                    expected_revision=expected_revision,
                    actual_revision=self._revision,
                )

            mutations = {
                _capture_store_key(key): _capture_mutation(key, mutation)
                for key, mutation in dict(transaction._mutations).items()
            }
            occurrence_mutations = {
                _capture_exact_text(chunk_id, "Forge occurrence chunk_id"):
                _recapture_forge_occurrence_mutation(chunk_id, occurrence)
                for chunk_id, occurrence in dict(
                    transaction._forge_occurrence_mutations
                ).items()
            }
            authorized_forge_records: dict[StoreKey, bytes] = {}
            for chunk_id, occurrence in occurrence_mutations.items():
                occurrence_records = _records_from_occurrence_mutation(occurrence)
                if chunk_id != occurrence_records.chunk.chunk_id:
                    raise UnifiedSourceIndexStoreError(
                        "Forge occurrence mutation key is inconsistent"
                    )
                for record, canonical_bytes in zip(
                    (
                        occurrence_records.source,
                        occurrence_records.source_version,
                        occurrence_records.chunk,
                    ),
                    occurrence.record_bytes,
                    strict=True,
                ):
                    kind, record_id, _ = _record_descriptor(record)
                    key = (kind, record_id)
                    staged = mutations.get(key)
                    if (
                        type(staged) is not _PutMutation
                        or staged.restore
                        or staged.canonical_bytes != canonical_bytes
                    ):
                        raise UnifiedSourceIndexStoreError(
                            "Forge occurrence mutation is not atomically staged"
                        )
                    previous = authorized_forge_records.get(key)
                    if previous is not None and previous != canonical_bytes:
                        raise UnifiedSourceIndexStoreError(
                            "Forge occurrence parents conflict within one transaction"
                        )
                    authorized_forge_records[key] = canonical_bytes

            for key, mutation in mutations.items():
                if type(mutation) is not _PutMutation:
                    continue
                record = _record_from_canonical_bytes(mutation.canonical_bytes)
                if _is_forge_bound_record(
                    record,
                    records=self._records,
                    staged=mutations,
                ) and authorized_forge_records.get(key) != mutation.canonical_bytes:
                    raise UnifiedSourceIndexStoreError(
                        "Forge-bound records require atomic put_code_occurrence"
                    )

            records = dict(self._records)
            tombstones = dict(self._tombstones)
            forge_occurrences = dict(self._forge_occurrences)
            next_revision = self._revision + 1

            # Validate every CAS before applying any mutation.
            for key, mutation in mutations.items():
                actual = _key_revision(records, tombstones, key)
                _require_revision_match(
                    mutation.expected_revision,
                    actual,
                    "record changed before transaction commit",
                )
                if type(mutation) is _PutMutation:
                    if mutation.restore and key not in tombstones:
                        raise StoreTombstoneError("restore target is no longer tombstoned")
                    if not mutation.restore and key in tombstones:
                        raise StoreTombstoneError("record identity is tombstoned")
                elif key not in records:
                    raise StoreNotFoundError("tombstone target no longer exists")

            for key, mutation in mutations.items():
                if type(mutation) is _PutMutation:
                    tombstones.pop(key, None)
                    records[key] = _StoredEntry(
                        mutation.record_kind,
                        mutation.record_id,
                        mutation.owner_scope,
                        next_revision,
                        mutation.canonical_bytes,
                    )
                    continue
                previous = records.pop(key)
                if mutation.record_kind is RecordKind.CHUNK:
                    forge_occurrences.pop(mutation.record_id, None)
                tombstones[key] = _TombstoneEntry(
                    mutation.record_kind,
                    mutation.record_id,
                    mutation.owner_scope,
                    previous.revision,
                    next_revision,
                    mutation.reason,
                )

            for chunk_id, mutation in occurrence_mutations.items():
                occurrence = _records_from_occurrence_mutation(mutation)
                stored_source = records.get((RecordKind.SOURCE, occurrence.source.source_id))
                stored_version = records.get(
                    (RecordKind.SOURCE_VERSION, occurrence.source_version.source_version_id)
                )
                stored_chunk = records.get((RecordKind.CHUNK, chunk_id))
                if (
                    stored_source is None
                    or stored_version is None
                    or stored_chunk is None
                    or stored_source.canonical_bytes != mutation.record_bytes[0]
                    or stored_version.canonical_bytes != mutation.record_bytes[1]
                    or stored_chunk.canonical_bytes != mutation.record_bytes[2]
                ):
                    raise UnifiedSourceIndexStoreError(
                        "Forge occurrence commit would create a partial or mismatched parent chain"
                    )
                evidence = _forge_evidence_from_canonical_bytes(
                    mutation.evidence_bytes
                )
                if occurrence.forge_evidence != evidence:
                    raise UnifiedSourceIndexStoreError(
                        "Forge occurrence evidence changed before commit"
                    )
                forge_occurrences[chunk_id] = mutation.evidence_bytes

            proposed = self._make_state(
                records,
                tombstones,
                forge_occurrences,
                next_revision,
            )
            self._assert_snapshot_state_integrity(proposed)

            live = self._make_state(
                proposed.records,
                proposed.tombstones,
                proposed.forge_occurrences,
                next_revision,
            )
            self._assert_snapshot_state_integrity(live)
            if _state_projection(live) != _state_projection(proposed):
                raise UnifiedSourceIndexStoreError(
                    "canonical proposed state changed before publication"
                )

            self._records = dict(live.records)
            self._tombstones = dict(live.tombstones)
            self._forge_occurrences = dict(live.forge_occurrences)
            self._revision = next_revision
            self._history[next_revision] = proposed
            return _copy_snapshot(proposed.snapshot)

    def _assert_current_state_integrity(self) -> None:
        self._assert_authority_envelope()
        authority = self._history.get(self._revision)
        if authority is None:
            raise UnifiedSourceIndexStoreError("current store authority is unavailable")
        self._assert_snapshot_state_integrity(authority)
        try:
            current = self._make_state(
                self._records,
                self._tombstones,
                self._forge_occurrences,
                self._revision,
            )
        except Exception:
            raise UnifiedSourceIndexStoreError("current store state failed integrity validation") from None
        if _state_projection(current) != _state_projection(authority):
            raise UnifiedSourceIndexStoreError("current store state failed integrity validation")
        self._assert_snapshot_state_integrity(current)

    def _assert_authority_envelope(self) -> None:
        if (
            type(self._revision) is not int
            or type(self._history) is not dict
            or type(self._records) is not dict
            or type(self._tombstones) is not dict
            or type(self._forge_occurrences) is not dict
        ):
            raise UnifiedSourceIndexStoreError(
                "current store authority envelope failed integrity validation"
            )

    @classmethod
    def _assert_snapshot_state_integrity(cls, state: _SnapshotState) -> None:
        if type(state) is not _SnapshotState:
            raise UnifiedSourceIndexStoreError("snapshot state failed integrity validation")
        snapshot = _copy_snapshot(state.snapshot)
        try:
            recomputed = cls._make_state(
                state.records,
                state.tombstones,
                state.forge_occurrences,
                snapshot.revision,
            )
        except Exception:
            raise UnifiedSourceIndexStoreError("snapshot state failed integrity validation") from None
        if _snapshot_values(recomputed.snapshot) != _snapshot_values(
            snapshot
        ):
            raise UnifiedSourceIndexStoreError("snapshot state failed integrity validation")
        _validate_forge_occurrence_state(
            recomputed.records,
            recomputed.forge_occurrences,
        )

    @staticmethod
    def _make_state(
        records: Mapping[StoreKey, _StoredEntry],
        tombstones: Mapping[StoreKey, _TombstoneEntry],
        forge_occurrences: Mapping[str, bytes],
        revision: int,
    ) -> _SnapshotState:
        if type(records) not in (dict, _MAPPING_PROXY_TYPE):
            raise UnifiedSourceIndexStoreError("store records must use an exact mapping")
        if type(tombstones) not in (dict, _MAPPING_PROXY_TYPE):
            raise UnifiedSourceIndexStoreError("store tombstones must use an exact mapping")
        if type(forge_occurrences) not in (dict, _MAPPING_PROXY_TYPE):
            raise UnifiedSourceIndexStoreError("Forge evidence must use an exact mapping")
        captured_records: dict[StoreKey, _StoredEntry] = {}
        for raw_key, raw_item in records.items():
            key = _capture_store_key(raw_key)
            item = _capture_stored_entry(key, raw_item)
            if (item.record_kind, item.record_id) != key or key in captured_records:
                raise UnifiedSourceIndexStoreError(
                    "store record key failed canonical integrity validation"
                )
            captured_records[key] = item

        captured_tombstones: dict[StoreKey, _TombstoneEntry] = {}
        for raw_key, raw_item in tombstones.items():
            key = _capture_store_key(raw_key)
            item = _capture_tombstone_entry(key, raw_item)
            if (item.record_kind, item.record_id) != key or key in captured_tombstones:
                raise UnifiedSourceIndexStoreError(
                    "store tombstone key failed canonical integrity validation"
                )
            captured_tombstones[key] = item
        if set(captured_records).intersection(captured_tombstones):
            raise UnifiedSourceIndexStoreError(
                "record and tombstone identities must remain disjoint"
            )

        captured_occurrences: dict[str, bytes] = {}
        for raw_chunk_id, raw_evidence in forge_occurrences.items():
            chunk_id = _capture_exact_text(raw_chunk_id, "Forge occurrence chunk_id")
            evidence_bytes = _capture_exact_bytes(
                raw_evidence,
                "Forge occurrence evidence",
            )
            _forge_evidence_from_canonical_bytes(evidence_bytes)
            if chunk_id in captured_occurrences:
                raise UnifiedSourceIndexStoreError(
                    "Forge occurrence evidence contains a duplicate chunk"
                )
            captured_occurrences[chunk_id] = evidence_bytes

        content_free_state = [
            {
                "record_kind": key[0].value,
                "record_id": key[1],
                "revision": item.revision,
                "record_hash": hashlib.sha256(
                    item.canonical_bytes
                ).hexdigest(),
                "state": "record",
            }
            for key, item in captured_records.items()
        ] + [
            {
                "record_kind": key[0].value,
                "record_id": key[1],
                "revision": item.revision,
                "record_hash": hashlib.sha256(
                    _tombstone_entry_bytes(item)
                ).hexdigest(),
                "state": "tombstone",
            }
            for key, item in captured_tombstones.items()
        ]
        content_free_state.extend(
            {
                "record_kind": "forge_occurrence_evidence",
                "record_id": chunk_id,
                "revision": revision,
                "record_hash": hashlib.sha256(
                    evidence_bytes
                ).hexdigest(),
                "state": "record",
            }
            for chunk_id, evidence_bytes in captured_occurrences.items()
        )
        content_free_state.sort(
            key=lambda item: (cast(str, item["record_kind"]), cast(str, item["record_id"]))
        )
        state_hash = "sha256:" + hashlib.sha256(
            canonical_json(content_free_state).encode("utf-8")
        ).hexdigest()
        snapshot = StoreSnapshot(
            revision,
            state_hash,
            len(captured_records),
            len(captured_tombstones),
        )
        return _SnapshotState(
            snapshot,
            MappingProxyType(captured_records),
            MappingProxyType(captured_tombstones),
            MappingProxyType(captured_occurrences),
        )


_RECORD_TYPES: tuple[type[StoreRecord], ...] = (
    SourceRecord,
    SourceVersionRecord,
    ChunkRecord,
    EntityRecord,
    RelationRecord,
    LineageRecord,
    ProjectionManifest,
    DerivedRunRecord,
    IndexJobRecord,
)
_MAPPING_PROXY_TYPE = type(MappingProxyType({}))
_SAFE_CONTRACT_DATACLASSES = frozenset(
    candidate
    for candidate in vars(usi_contract).values()
    if type(candidate) is type and is_dataclass(candidate)
)


def _capture_exact_text(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise UnifiedSourceIndexStoreError(f"{field_name} must use exact text")
    return value


def _capture_exact_bytes(value: object, field_name: str) -> bytes:
    if type(value) is not bytes:
        raise UnifiedSourceIndexStoreError(f"{field_name} must use exact bytes")
    return value


def _exact_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise UnifiedSourceIndexStoreError(f"{field_name} must use an exact boolean")
    return value


def _assert_safe_contract_graph(value: object, active: set[int] | None = None) -> None:
    """Reject overloaded values before any contract serializer can dispatch."""

    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float:
        if value != value or value in (float("inf"), float("-inf")):
            raise UnifiedSourceIndexStoreError("contract graph contains a non-finite number")
        return
    value_type = type(value)
    if issubclass(value_type, Enum):
        if value_type.__module__ != usi_contract.__name__:
            raise UnifiedSourceIndexStoreError("contract graph contains a foreign enum")
        _assert_safe_contract_graph(value.value, active)
        return
    if value_type in (tuple, list):
        graph = active if active is not None else set()
        marker = id(value)
        if marker in graph:
            raise UnifiedSourceIndexStoreError("contract graph contains a cycle")
        graph.add(marker)
        try:
            for item in value:
                _assert_safe_contract_graph(item, graph)
        finally:
            graph.remove(marker)
        return
    if value_type is dict:
        graph = active if active is not None else set()
        marker = id(value)
        if marker in graph:
            raise UnifiedSourceIndexStoreError("contract graph contains a cycle")
        graph.add(marker)
        try:
            for key, item in value.items():
                if type(key) is not str:
                    raise UnifiedSourceIndexStoreError(
                        "contract graph mappings require exact text keys"
                    )
                _assert_safe_contract_graph(item, graph)
        finally:
            graph.remove(marker)
        return
    if value_type not in _SAFE_CONTRACT_DATACLASSES:
        raise UnifiedSourceIndexStoreError(
            "contract graph contains a foreign or overloaded value"
        )
    graph = active if active is not None else set()
    marker = id(value)
    if marker in graph:
        raise UnifiedSourceIndexStoreError("contract graph contains a cycle")
    graph.add(marker)
    try:
        for field in fields(value_type):
            _assert_safe_contract_graph(
                object.__getattribute__(value, field.name),
                graph,
            )
    finally:
        graph.remove(marker)


def _capture_store_key(value: object) -> StoreKey:
    if type(value) is not tuple or len(value) != 2:
        raise UnifiedSourceIndexStoreError("store key must be an exact pair")
    kind, record_id = value
    if type(kind) is not RecordKind or type(record_id) is not str:
        raise UnifiedSourceIndexStoreError("store key contains hostile scalar types")
    return kind, _record_id(kind, record_id)


def _capture_store_record(record: object) -> tuple[StoreRecord, bytes]:
    """Detach one exact record through the pinned canonical schema codec."""

    if type(record) not in _RECORD_TYPES:
        raise UnifiedSourceIndexStoreError(
            "store record must use one exact supported record type"
        )
    try:
        _assert_safe_contract_graph(record)
        serialized = type(record).to_json(record)
        if type(serialized) is not str:
            raise TypeError("record serializer did not return exact text")
        canonical_bytes = serialized.encode("utf-8")
        captured = _record_from_canonical_bytes(canonical_bytes)
        if type(captured) is not type(record):
            raise TypeError("record schema changed during capture")
        rebuilt = type(captured).to_json(captured)
        if type(rebuilt) is not str or rebuilt.encode("utf-8") != canonical_bytes:
            raise ValueError("record is not byte-stable canonical JSON")
        typed = cast(StoreRecord, captured)
        _record_descriptor(typed)
        return typed, canonical_bytes
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        raise UnifiedSourceIndexStoreError(
            "store record failed canonical capture"
        ) from None


def _record_from_canonical_bytes(value: object) -> StoreRecord:
    canonical_bytes = _capture_exact_bytes(value, "canonical record")
    try:
        captured = record_from_json(canonical_bytes)
        if type(captured) not in _RECORD_TYPES:
            raise TypeError("record decoder returned an unsupported type")
        _assert_safe_contract_graph(captured)
        serialized = type(captured).to_json(captured)
        if type(serialized) is not str or serialized.encode("utf-8") != canonical_bytes:
            raise ValueError("record decoder was not byte-stable")
        typed = cast(StoreRecord, captured)
        _record_descriptor(typed)
        return typed
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        raise UnifiedSourceIndexStoreError(
            "canonical record failed reconstruction"
        ) from None


def _capture_forge_evidence(
    evidence: object,
) -> tuple[ForgeCodeOccurrenceEvidence, bytes]:
    if type(evidence) is not ForgeCodeOccurrenceEvidence:
        raise UnifiedSourceIndexStoreError(
            "Forge occurrence evidence must use the exact typed contract"
        )
    try:
        _assert_safe_contract_graph(evidence)
        serialized = ForgeCodeOccurrenceEvidence.to_json(evidence)
        if type(serialized) is not str:
            raise TypeError("evidence serializer did not return exact text")
        canonical_bytes = serialized.encode("utf-8")
        captured = _forge_evidence_from_canonical_bytes(canonical_bytes)
        rebuilt = ForgeCodeOccurrenceEvidence.to_json(captured)
        if type(rebuilt) is not str or rebuilt.encode("utf-8") != canonical_bytes:
            raise ValueError("evidence is not byte-stable canonical JSON")
        return captured, canonical_bytes
    except Exception:
        raise UnifiedSourceIndexStoreError(
            "Forge occurrence evidence failed canonical capture"
        ) from None


def _forge_evidence_from_canonical_bytes(value: object) -> ForgeCodeOccurrenceEvidence:
    canonical_bytes = _capture_exact_bytes(value, "canonical Forge evidence")
    try:
        captured = ForgeCodeOccurrenceEvidence.from_json(canonical_bytes)
        if type(captured) is not ForgeCodeOccurrenceEvidence:
            raise TypeError("Forge evidence decoder returned a foreign type")
        _assert_safe_contract_graph(captured)
        rebuilt = ForgeCodeOccurrenceEvidence.to_json(captured)
        if type(rebuilt) is not str or rebuilt.encode("utf-8") != canonical_bytes:
            raise ValueError("Forge evidence decoder was not byte-stable")
        return captured
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        raise UnifiedSourceIndexStoreError(
            "canonical Forge evidence failed reconstruction"
        ) from None


def _capture_forge_occurrence(
    occurrence: object,
) -> _ForgeOccurrenceMutation:
    if type(occurrence) is not CodeOccurrenceRecords:
        raise UnifiedSourceIndexStoreError(
            "code occurrence must use the exact CodeOccurrenceRecords type"
        )
    try:
        _assert_safe_contract_graph(occurrence)
        source, source_bytes = _capture_store_record(occurrence.source)
        version, version_bytes = _capture_store_record(occurrence.source_version)
        chunk, chunk_bytes = _capture_store_record(occurrence.chunk)
        evidence, evidence_bytes = _capture_forge_evidence(occurrence.forge_evidence)
        canonical = CodeOccurrenceRecords(
            cast(SourceRecord, source),
            cast(SourceVersionRecord, version),
            cast(ChunkRecord, chunk),
            evidence,
        )
        from src.unified_source_index_sources.forge_code import (
            validate_forge_code_occurrence_records,
        )

        validated = validate_forge_code_occurrence_records(canonical)
        if type(validated) is not CodeOccurrenceRecords:
            raise TypeError("Forge validator returned a foreign aggregate")
        final_source, final_source_bytes = _capture_store_record(validated.source)
        final_version, final_version_bytes = _capture_store_record(
            validated.source_version
        )
        final_chunk, final_chunk_bytes = _capture_store_record(validated.chunk)
        final_evidence, final_evidence_bytes = _capture_forge_evidence(
            validated.forge_evidence
        )
        if (
            source_bytes,
            version_bytes,
            chunk_bytes,
            evidence_bytes,
        ) != (
            final_source_bytes,
            final_version_bytes,
            final_chunk_bytes,
            final_evidence_bytes,
        ):
            raise ValueError("Forge validation changed canonical occurrence bytes")
        records = CodeOccurrenceRecords(
            cast(SourceRecord, final_source),
            cast(SourceVersionRecord, final_version),
            cast(ChunkRecord, final_chunk),
            final_evidence,
        )
        return _ForgeOccurrenceMutation(
            (final_source_bytes, final_version_bytes, final_chunk_bytes),
            final_evidence_bytes,
        )
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        raise UnifiedSourceIndexStoreError(
            "Forge occurrence evidence or parent chain is invalid"
        ) from None


def _records_from_occurrence_mutation(
    mutation: object,
) -> CodeOccurrenceRecords:
    if (
        type(mutation) is not _ForgeOccurrenceMutation
        or type(mutation.record_bytes) is not tuple
        or len(mutation.record_bytes) != 3
        or any(type(value) is not bytes for value in mutation.record_bytes)
        or type(mutation.evidence_bytes) is not bytes
    ):
        raise UnifiedSourceIndexStoreError(
            "Forge occurrence mutation envelope failed integrity validation"
        )
    try:
        source = _record_from_canonical_bytes(mutation.record_bytes[0])
        version = _record_from_canonical_bytes(mutation.record_bytes[1])
        chunk = _record_from_canonical_bytes(mutation.record_bytes[2])
        evidence = _forge_evidence_from_canonical_bytes(mutation.evidence_bytes)
        if (
            type(source) is not SourceRecord
            or type(version) is not SourceVersionRecord
            or type(chunk) is not ChunkRecord
        ):
            raise TypeError("Forge occurrence bytes contain the wrong record kinds")
        return CodeOccurrenceRecords(source, version, chunk, evidence)
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        raise UnifiedSourceIndexStoreError(
            "Forge occurrence mutation failed reconstruction"
        ) from None


def _recapture_forge_occurrence_mutation(
    chunk_id: object,
    mutation: object,
) -> _ForgeOccurrenceMutation:
    expected_chunk_id = _capture_exact_text(chunk_id, "Forge occurrence chunk_id")
    if (
        type(mutation) is not _ForgeOccurrenceMutation
        or type(mutation.record_bytes) is not tuple
        or len(mutation.record_bytes) != 3
        or any(type(value) is not bytes for value in mutation.record_bytes)
        or type(mutation.evidence_bytes) is not bytes
    ):
        raise UnifiedSourceIndexStoreError(
            "Forge occurrence mutation envelope failed integrity validation"
        )
    records = _records_from_occurrence_mutation(mutation)
    captured = _capture_forge_occurrence(records)
    if (
        records.chunk.chunk_id != expected_chunk_id
        or captured.record_bytes != mutation.record_bytes
        or captured.evidence_bytes != mutation.evidence_bytes
    ):
        raise UnifiedSourceIndexStoreError(
            "Forge occurrence mutation changed after staging"
        )
    return captured


def _capture_stored_entry(key: StoreKey, item: object) -> _StoredEntry:
    if type(item) is not _StoredEntry:
        raise UnifiedSourceIndexStoreError(
            "stored entry failed exact-type integrity validation"
        )
    if (
        type(item.record_kind) is not RecordKind
        or type(item.record_id) is not str
        or type(item.owner_scope) is not str
        or type(item.revision) is not int
        or type(item.canonical_bytes) is not bytes
    ):
        raise UnifiedSourceIndexStoreError("stored entry contains hostile scalar types")
    record = _record_from_canonical_bytes(item.canonical_bytes)
    descriptor = _record_descriptor(record)
    captured = _StoredEntry(
        descriptor[0],
        descriptor[1],
        descriptor[2],
        _positive_revision(item.revision),
        item.canonical_bytes,
    )
    if descriptor[:2] != key or (
        captured.record_kind,
        captured.record_id,
        captured.owner_scope,
    ) != (
        item.record_kind,
        item.record_id,
        item.owner_scope,
    ):
        raise UnifiedSourceIndexStoreError(
            "stored entry key or ownership failed canonical integrity validation"
        )
    return captured


def _capture_tombstone_entry(key: StoreKey, item: object) -> _TombstoneEntry:
    if type(item) is not _TombstoneEntry:
        raise UnifiedSourceIndexStoreError(
            "tombstone entry failed exact-type integrity validation"
        )
    if (
        type(item.record_kind) is not RecordKind
        or type(item.record_id) is not str
        or type(item.owner_scope) is not str
        or type(item.previous_revision) is not int
        or type(item.revision) is not int
        or type(item.reason) is not str
    ):
        raise UnifiedSourceIndexStoreError(
            "tombstone entry contains hostile scalar types"
        )
    captured = _TombstoneEntry(
        item.record_kind,
        _record_id(item.record_kind, item.record_id),
        _owner_scope(item.owner_scope),
        _positive_revision(item.previous_revision),
        _positive_revision(item.revision),
        _reason(item.reason),
    )
    if captured.revision <= captured.previous_revision:
        raise UnifiedSourceIndexStoreError(
            "tombstone revision must follow the previous record revision"
        )
    if (captured.record_kind, captured.record_id) != key:
        raise UnifiedSourceIndexStoreError(
            "tombstone entry key failed canonical integrity validation"
        )
    return captured


def _stored_entry_to_public(
    key: StoreKey,
    item: object,
) -> StoredRecord[StoreRecord]:
    captured = _capture_stored_entry(key, item)
    return StoredRecord(
        _record_from_canonical_bytes(captured.canonical_bytes),
        captured.revision,
    )


def _tombstone_entry_to_public(
    key: StoreKey,
    item: object,
) -> TombstoneRecord:
    captured = _capture_tombstone_entry(key, item)
    return TombstoneRecord(
        captured.record_kind,
        captured.record_id,
        captured.owner_scope,
        captured.previous_revision,
        captured.revision,
        captured.reason,
    )


def _tombstone_entry_bytes(item: object) -> bytes:
    if type(item) is not _TombstoneEntry:
        raise UnifiedSourceIndexStoreError("tombstone entry has a foreign type")
    payload = {
        "schema": f"{USI_STORE_SCHEMA}.tombstone",
        "record_kind": item.record_kind.value,
        "record_id": item.record_id,
        "owner_scope": item.owner_scope,
        "previous_revision": item.previous_revision,
        "revision": item.revision,
        "reason": item.reason,
    }
    return canonical_json(payload).encode("utf-8")


def _copy_stored_record(item: object) -> StoredRecord[StoreRecord]:
    if type(item) is not StoredRecord:
        raise UnifiedSourceIndexStoreError(
            "stored record failed exact-type integrity validation"
        )
    record, _ = _capture_store_record(item.record)
    return StoredRecord(record, _positive_revision(item.revision))


def _copy_tombstone(item: object) -> TombstoneRecord:
    if type(item) is not TombstoneRecord:
        raise UnifiedSourceIndexStoreError(
            "tombstone failed exact-type integrity validation"
        )
    if (
        type(item.record_kind) is not RecordKind
        or type(item.record_id) is not str
        or type(item.owner_scope) is not str
        or type(item.previous_revision) is not int
        or type(item.revision) is not int
        or type(item.reason) is not str
    ):
        raise UnifiedSourceIndexStoreError(
            "tombstone contains hostile scalar types"
        )
    return TombstoneRecord(
        item.record_kind,
        item.record_id,
        item.owner_scope,
        item.previous_revision,
        item.revision,
        item.reason,
    )


def _copy_page_item(item: StorePageItem) -> StorePageItem:
    if type(item) is StoredRecord:
        return _copy_stored_record(item)
    if type(item) is TombstoneRecord:
        return _copy_tombstone(item)
    raise UnifiedSourceIndexStoreError("store page item has a foreign type")


def _copy_snapshot(snapshot: object) -> StoreSnapshot:
    if type(snapshot) is not StoreSnapshot:
        raise UnifiedSourceIndexStoreError("snapshot must use the exact typed contract")
    if (
        type(snapshot.revision) is not int
        or type(snapshot.state_hash) is not str
        or type(snapshot.record_count) is not int
        or type(snapshot.tombstone_count) is not int
        or type(snapshot.snapshot_ref) is not str
    ):
        raise UnifiedSourceIndexStoreError("snapshot contains hostile scalar types")
    return StoreSnapshot(
        snapshot.revision,
        snapshot.state_hash,
        snapshot.record_count,
        snapshot.tombstone_count,
        snapshot.snapshot_ref,
    )


def _snapshot_values(snapshot: object) -> tuple[int, str, int, int, str]:
    captured = _copy_snapshot(snapshot)
    return (
        captured.revision,
        captured.state_hash,
        captured.record_count,
        captured.tombstone_count,
        captured.snapshot_ref,
    )


def _capture_mutation(key: object, mutation: object) -> Mutation:
    captured_key = _capture_store_key(key)
    if type(mutation) is _PutMutation:
        if (
            type(mutation.record_kind) is not RecordKind
            or type(mutation.record_id) is not str
            or type(mutation.owner_scope) is not str
            or type(mutation.canonical_bytes) is not bytes
            or type(mutation.expected_revision) is not int
            or type(mutation.restore) is not bool
        ):
            raise UnifiedSourceIndexStoreError(
                "put mutation contains hostile scalar types"
            )
        record = _record_from_canonical_bytes(mutation.canonical_bytes)
        canonical_bytes = mutation.canonical_bytes
        descriptor = _record_descriptor(record)
        if (
            descriptor[:2] != captured_key
            or descriptor
            != (
                mutation.record_kind,
                mutation.record_id,
                mutation.owner_scope,
            )
        ):
            raise UnifiedSourceIndexStoreError(
                "put mutation changed after staging"
            )
        return _PutMutation(
            descriptor[0],
            descriptor[1],
            descriptor[2],
            canonical_bytes,
            _revision(mutation.expected_revision, "expected_revision"),
            mutation.restore,
        )
    if type(mutation) is _TombstoneMutation:
        if (
            type(mutation.record_kind) is not RecordKind
            or type(mutation.record_id) is not str
            or type(mutation.owner_scope) is not str
            or type(mutation.expected_revision) is not int
            or type(mutation.reason) is not str
        ):
            raise UnifiedSourceIndexStoreError(
                "tombstone mutation contains hostile scalar types"
            )
        captured = _TombstoneMutation(
            mutation.record_kind,
            _record_id(mutation.record_kind, mutation.record_id),
            _owner_scope(mutation.owner_scope),
            _revision(mutation.expected_revision, "expected_revision"),
            _reason(mutation.reason),
        )
        if (captured.record_kind, captured.record_id) != captured_key:
            raise UnifiedSourceIndexStoreError(
                "tombstone mutation key changed after staging"
            )
        return captured
    raise UnifiedSourceIndexStoreError(
        "transaction mutation envelope failed exact-type validation"
    )


def _state_projection(state: _SnapshotState) -> tuple[object, ...]:
    if type(state) is not _SnapshotState:
        raise UnifiedSourceIndexStoreError("state projection requires exact state")
    records = tuple(
        sorted(
            (
                key[0].value,
                key[1],
                item.revision,
                item.canonical_bytes,
            )
            for key, item in state.records.items()
        )
    )
    tombstones = tuple(
        sorted(
            (
                key[0].value,
                key[1],
                _tombstone_entry_bytes(item),
            )
            for key, item in state.tombstones.items()
        )
    )
    occurrences = tuple(
        sorted(
            (chunk_id, evidence_bytes)
            for chunk_id, evidence_bytes in state.forge_occurrences.items()
        )
    )
    return _snapshot_values(state.snapshot), records, tombstones, occurrences


def _record_descriptor(record: StoreRecord) -> tuple[RecordKind, str, str]:
    if type(record) not in _RECORD_TYPES:
        raise UnifiedSourceIndexStoreError(
            f"unsupported USI store record: {type(record).__name__}"
        )
    mapping: tuple[tuple[type[StoreRecord], RecordKind, str], ...] = (
        (SourceRecord, RecordKind.SOURCE, "source_id"),
        (SourceVersionRecord, RecordKind.SOURCE_VERSION, "source_version_id"),
        (ChunkRecord, RecordKind.CHUNK, "chunk_id"),
        (EntityRecord, RecordKind.ENTITY, "entity_id"),
        (RelationRecord, RecordKind.RELATION, "relation_id"),
        (LineageRecord, RecordKind.LINEAGE, "lineage_id"),
        (ProjectionManifest, RecordKind.PROJECTION, "projection_id"),
        (DerivedRunRecord, RecordKind.DERIVED_RUN, "derived_run_id"),
        (IndexJobRecord, RecordKind.JOB, "job_id"),
    )
    for record_type, kind, id_field in mapping:
        if type(record) is record_type:
            record_id = _record_id(kind, cast(str, getattr(record, id_field)))
            owner = _owner_scope(cast(str, getattr(record, "owner_scope")))
            return kind, record_id, owner
    raise UnifiedSourceIndexStoreError("unsupported USI store record")


def _is_forge_bound_record(
    record: StoreRecord,
    *,
    records: Mapping[StoreKey, _StoredEntry],
    staged: Mapping[StoreKey, Mutation],
) -> bool:
    if type(record) is SourceRecord:
        return (
            record.provider_ref == "forge.code"
            or record.canonical_ref.startswith("forge-code-source:")
        )
    if type(record) is SourceVersionRecord:
        if (
            record.provider_ref == "forge.code"
            or record.revision_ref.startswith("forge-code-version:")
        ):
            return True
        parent = _staged_or_live_record(
            (RecordKind.SOURCE, record.source_id),
            records=records,
            staged=staged,
        )
        return type(parent) is SourceRecord and _is_forge_bound_record(
            parent,
            records=records,
            staged=staged,
        )
    if type(record) is not ChunkRecord:
        return False
    if record.extractor_profile_ref.startswith("forge-code-"):
        return True
    parent = _staged_or_live_record(
        (RecordKind.SOURCE_VERSION, record.source_version_id),
        records=records,
        staged=staged,
    )
    return type(parent) is SourceVersionRecord and _is_forge_bound_record(
        parent,
        records=records,
        staged=staged,
    )


def _staged_or_live_record(
    key: StoreKey,
    *,
    records: Mapping[StoreKey, _StoredEntry],
    staged: Mapping[StoreKey, Mutation],
) -> StoreRecord | None:
    mutation = staged.get(key)
    if type(mutation) is _PutMutation:
        return _record_from_canonical_bytes(mutation.canonical_bytes)
    stored = records.get(key)
    return (
        None
        if stored is None
        else _record_from_canonical_bytes(stored.canonical_bytes)
    )


def _validate_forge_occurrence_state(
    records: Mapping[StoreKey, _StoredEntry],
    forge_occurrences: Mapping[str, bytes],
) -> None:
    """Prove that every live Forge child has one exact durable parent chain."""

    try:
        from src.unified_source_index_sources.forge_code import (
            validate_forge_code_occurrence_records,
        )

        decoded_records = {
            key: _record_from_canonical_bytes(
                _capture_stored_entry(key, item).canonical_bytes
            )
            for key, item in records.items()
        }
        forge_chunk_ids: set[str] = set()
        for (kind, record_id), item in records.items():
            record = decoded_records[(kind, record_id)]
            if (kind, record_id) != _record_descriptor(record)[:2]:
                raise UnifiedSourceIndexStoreError(
                    "Forge store record key failed integrity validation"
                )
            if type(record) is SourceRecord and _is_forge_bound_record(
                record,
                records=records,
                staged={},
            ) and not (
                record.provider_ref == "forge.code"
                and record.canonical_ref.startswith("forge-code-source:")
            ):
                raise UnifiedSourceIndexStoreError(
                    "Forge source has hybrid authority labels"
                )
            if type(record) is SourceVersionRecord and _is_forge_bound_record(
                record,
                records=records,
                staged={},
            ):
                if not (
                    record.provider_ref == "forge.code"
                    and record.revision_ref.startswith("forge-code-version:")
                ):
                    raise UnifiedSourceIndexStoreError(
                        "Forge source version has hybrid authority labels"
                    )
                source = decoded_records.get((RecordKind.SOURCE, record.source_id))
                if (
                    source is None
                    or type(source) is not SourceRecord
                    or not _is_forge_bound_record(
                        source,
                        records=records,
                        staged={},
                    )
                    or record.owner_scope != source.owner_scope
                    or record.policy_evidence != source.policy_evidence()
                ):
                    raise UnifiedSourceIndexStoreError(
                        "Forge source version has no canonical live source parent"
                    )
            if type(record) is not ChunkRecord or not _is_forge_bound_record(
                record,
                records=records,
                staged={},
            ):
                continue
            if not record.extractor_profile_ref.startswith("forge-code-"):
                raise UnifiedSourceIndexStoreError(
                    "Forge chunk has hybrid extractor authority"
                )
            forge_chunk_ids.add(record.chunk_id)
            version = decoded_records.get(
                (RecordKind.SOURCE_VERSION, record.source_version_id)
            )
            if version is None or type(version) is not SourceVersionRecord:
                raise UnifiedSourceIndexStoreError(
                    "Forge chunk has no canonical live version parent"
                )
            source = decoded_records.get((RecordKind.SOURCE, record.source_id))
            evidence_bytes = forge_occurrences.get(record.chunk_id)
            if (
                source is None
                or type(source) is not SourceRecord
                or type(evidence_bytes) is not bytes
            ):
                raise UnifiedSourceIndexStoreError(
                    "Forge chunk has no complete occurrence evidence chain"
                )
            evidence = _forge_evidence_from_canonical_bytes(evidence_bytes)
            occurrence = CodeOccurrenceRecords(
                source,
                version,
                record,
                evidence,
            )
            validate_forge_code_occurrence_records(occurrence)

        if any(type(chunk_id) is not str for chunk_id in forge_occurrences) or set(
            forge_occurrences
        ) != forge_chunk_ids:
            raise UnifiedSourceIndexStoreError(
                "Forge occurrence evidence does not match live Forge chunks"
            )
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        raise UnifiedSourceIndexStoreError(
            "Forge occurrence state failed integrity validation"
        ) from None


def _record_kind(value: RecordKind | str) -> RecordKind:
    if type(value) is RecordKind:
        return value
    if type(value) is not str:
        raise UnifiedSourceIndexStoreError("record_kind must be a RecordKind")
    try:
        return RecordKind(value)
    except ValueError as exc:
        raise UnifiedSourceIndexStoreError(f"invalid record_kind: {value!r}") from exc


def _record_id(kind: RecordKind, value: str) -> str:
    if type(kind) is not RecordKind or type(value) is not str:
        raise UnifiedSourceIndexStoreError(
            "record_id must use exact record-kind and text scalars"
        )
    try:
        record_id = RecordRef(kind, value).record_id
        if type(record_id) is not str:
            raise TypeError("record_id was not normalized to exact text")
        return record_id
    except (TypeError, ValueError) as exc:
        raise UnifiedSourceIndexStoreError("record_id does not match record_kind") from exc


def _owner_scope(value: str) -> str:
    if type(value) is not str or not _OWNER_SCOPE_RE.fullmatch(value):
        raise UnifiedSourceIndexStoreError("owner_scope is invalid or unbounded")
    return value


def _reason(value: str) -> str:
    if type(value) is not str or not _REASON_RE.fullmatch(value):
        raise UnifiedSourceIndexStoreError(
            "tombstone reason must be a bounded content-free token"
        )
    return value


def _revision(value: int, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise UnifiedSourceIndexStoreError(f"{field_name} must be a non-negative integer")
    return value


def _positive_revision(value: int) -> int:
    revision = _revision(value, "revision")
    if revision == 0:
        raise UnifiedSourceIndexStoreError("record revisions must be positive")
    return revision


def _count(value: int, field_name: str) -> int:
    return _revision(value, field_name)


def _page_limit(value: int) -> int:
    limit = _revision(value, "limit")
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise UnifiedSourceIndexStoreError(
            f"limit must be between 1 and {MAX_PAGE_SIZE}"
        )
    return limit


def _sha256(value: str, field_name: str) -> str:
    if type(value) is not str:
        raise UnifiedSourceIndexStoreError(f"{field_name} must be sha256 text")
    normalized = value.lower()
    if normalized.startswith("sha256:"):
        digest = normalized[7:]
    else:
        digest = normalized
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise UnifiedSourceIndexStoreError(f"{field_name} must be sha256 text")
    return "sha256:" + digest


def _snapshot_ref(
    revision: int,
    state_hash: str,
    record_count: int,
    tombstone_count: int,
) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "revision": revision,
                "state_hash": state_hash,
                "record_count": record_count,
                "tombstone_count": tombstone_count,
            }
        ).encode("utf-8")
    ).hexdigest()
    value = f"usi_snapshot_{digest}"
    if not _SNAPSHOT_REF_RE.fullmatch(value):
        raise UnifiedSourceIndexStoreError("failed to build snapshot_ref")
    return value


def _key_revision(
    records: Mapping[StoreKey, _StoredEntry],
    tombstones: Mapping[StoreKey, _TombstoneEntry],
    key: StoreKey,
) -> int:
    stored = records.get(key)
    if stored is not None:
        return stored.revision
    tombstone = tombstones.get(key)
    if tombstone is not None:
        return tombstone.revision
    return 0


def _require_revision_match(expected: int, actual: int, message: str) -> None:
    if expected != actual:
        raise StoreConflictError(
            message,
            expected_revision=expected,
            actual_revision=actual,
        )


def _page_item_id(item: StorePageItem) -> str:
    return item.record_id


def _encode_cursor(
    *,
    snapshot: StoreSnapshot,
    record_kind: RecordKind,
    owner_scope: str,
    after_record_id: str,
    include_tombstones: bool,
) -> str:
    snapshot = _copy_snapshot(snapshot)
    if type(record_kind) is not RecordKind:
        raise StoreCursorError("cursor record kind is invalid")
    owner_scope = _owner_scope(owner_scope)
    include_tombstones = _exact_bool(include_tombstones, "include_tombstones")
    body: dict[str, object] = {
        "schema": f"{USI_STORE_SCHEMA}.cursor",
        "snapshot_ref": snapshot.snapshot_ref,
        "snapshot_revision": snapshot.revision,
        "record_kind": record_kind.value,
        "owner_scope": owner_scope,
        "after_record_id": _record_id(record_kind, after_record_id),
        "include_tombstones": include_tombstones,
    }
    checksum = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    encoded = base64.urlsafe_b64encode(
        canonical_json({**body, "checksum": checksum}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    if len(encoded) > MAX_CURSOR_CHARS:
        raise StoreCursorError("generated cursor exceeds its bound")
    return encoded


def _decode_cursor(
    value: str,
    *,
    snapshot: StoreSnapshot,
    record_kind: RecordKind,
    owner_scope: str,
    include_tombstones: bool,
) -> str:
    if type(value) is not str:
        raise StoreCursorError("cursor is invalid or unbounded")
    if not value or len(value) > MAX_CURSOR_CHARS:
        raise StoreCursorError("cursor is invalid or unbounded")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(
            (value + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except Exception as exc:
        raise StoreCursorError("cursor is not valid canonical data") from exc
    if type(payload) is not dict:
        raise StoreCursorError("cursor payload must be an object")
    expected_fields = {
        "schema",
        "snapshot_ref",
        "snapshot_revision",
        "record_kind",
        "owner_scope",
        "after_record_id",
        "include_tombstones",
        "checksum",
    }
    if set(payload) != expected_fields:
        raise StoreCursorError("cursor fields are incomplete or unknown")
    checksum = payload.pop("checksum")
    expected_checksum = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    if type(checksum) is not str or checksum != expected_checksum:
        raise StoreCursorError("cursor checksum does not match")
    expected_values = {
        "schema": f"{USI_STORE_SCHEMA}.cursor",
        "snapshot_ref": snapshot.snapshot_ref,
        "snapshot_revision": snapshot.revision,
        "record_kind": record_kind.value,
        "owner_scope": owner_scope,
        "include_tombstones": include_tombstones,
    }
    if any(payload.get(key) != expected for key, expected in expected_values.items()):
        raise StoreCursorError("cursor belongs to another snapshot or query")
    after_record_id = payload.get("after_record_id")
    if type(after_record_id) is not str:
        raise StoreCursorError("cursor record id is invalid")
    return _record_id(record_kind, after_record_id)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str:
            raise ValueError("cursor object key must use exact text")
        if key in result:
            raise StoreCursorError(f"cursor contains duplicate field: {key}")
        result[key] = value
    return result


__all__ = [
    "ChunkStore",
    "EmbeddingStore",
    "GraphStore",
    "InMemoryStoreReadTransaction",
    "InMemoryStoreWriteTransaction",
    "InMemoryUnifiedSourceIndexStore",
    "JobStore",
    "MAX_PAGE_SIZE",
    "MemoryStore",
    "QueryCacheStore",
    "ReviewStore",
    "SourceStore",
    "StoreConflictError",
    "StoreCursorError",
    "StoreInterfaceKind",
    "StoreNotFoundError",
    "StorePage",
    "StoreReadTransaction",
    "StoreSnapshot",
    "StoreTombstoneError",
    "StoreTransactionClosedError",
    "StoreWriteTransaction",
    "StoredRecord",
    "TombstoneRecord",
    "TransactionalStore",
    "UnifiedSourceIndexStoreError",
]

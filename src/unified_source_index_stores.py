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
import bisect
import hashlib
import json
import re
from threading import RLock
from types import MappingProxyType
import weakref
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

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "state_hash": self.state_hash,
            "record_count": self.record_count,
            "tombstone_count": self.tombstone_count,
            "snapshot_ref": self.snapshot_ref,
        }


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


@dataclass(frozen=True, slots=True)
class _RetainedReadIndexGroup:
    record_ids: tuple[str, ...]
    keys: tuple[StoreKey, ...]


@dataclass(frozen=True, slots=True)
class _RetainedReadIndex:
    records: Mapping[tuple[RecordKind, str], _RetainedReadIndexGroup]
    including_tombstones: Mapping[tuple[RecordKind, str], _RetainedReadIndexGroup]


@dataclass(frozen=True, slots=True)
class _RetainedReadCache:
    original_state: _SnapshotState
    original_snapshot: StoreSnapshot
    original_records: Mapping[StoreKey, _StoredEntry]
    original_tombstones: Mapping[StoreKey, _TombstoneEntry]
    original_occurrences: Mapping[str, bytes]
    transaction_snapshot_binding: tuple[object, ...]
    original_binding: tuple[object, ...]
    detached_state: _SnapshotState
    detached_binding: tuple[object, ...]
    index: _RetainedReadIndex
    index_binding: tuple[object, ...]


_RETAINED_READ_CACHES: dict[
    int,
    tuple[weakref.ReferenceType[object], _RetainedReadCache],
] = {}
_RETAINED_READ_CACHE_LOCK = RLock()


class InMemoryStoreReadTransaction:
    """Immutable read view over exactly one committed snapshot."""

    def __init__(self, state: _SnapshotState) -> None:
        self._state = state
        self._snapshot_binding = _snapshot_values(state.snapshot)
        self._closed = False

    @property
    def snapshot(self) -> StoreSnapshot:
        state = self._capture_operation_state()
        try:
            return _copy_snapshot(state.snapshot)
        finally:
            self._finish_read_operation(state)

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
        try:
            return self._get_from_state(
                state,
                key,
                owner,
                include_tombstone=include_deleted,
            )
        finally:
            self._finish_read_operation(state)

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
        try:
            item = self._get_from_state(
                state,
                key,
                owner,
                include_tombstone=include_deleted,
            )
            if item is None:
                raise StoreNotFoundError("owner-scoped USI record was not found")
            return item
        finally:
            self._finish_read_operation(state)

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
        try:
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
        finally:
            self._finish_read_operation(state)

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
        try:
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
            cache = _retained_read_cache_for(self)
            index = (
                cache.index.including_tombstones
                if include_deleted
                else cache.index.records
            )
            group = index.get((kind, owner))
            if group is None:
                selected_keys: tuple[StoreKey, ...] = ()
                has_more = False
            else:
                start = 0 if not after_id else bisect.bisect_right(group.record_ids, after_id)
                selected_keys = group.keys[start : start + page_limit]
                has_more = start + page_limit < len(group.keys)
            selected = tuple(
                _stored_entry_to_public(key, state.records[key])
                if key in state.records
                else _tombstone_entry_to_public(key, state.tombstones[key])
                for key in selected_keys
            )
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
        finally:
            self._finish_read_operation(state)

    def close(self) -> None:
        if self._closed is True:
            _discard_retained_read_cache(self)
            return
        self._closed = True
        _discard_retained_read_cache(self)

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
        """Capture one validated retained state without trusting mutable aliases."""

        self._ensure_open()
        state = self._state
        binding = self._snapshot_binding
        _validate_retained_read_transaction_binding(binding)
        # The cache is published under the same lock used by the weakref
        # eviction callback.  Keep the initial lookup synchronized as well:
        # two readers may both build the immutable candidate, but neither may
        # observe or remove a publication concurrently with its reuse.
        with _RETAINED_READ_CACHE_LOCK:
            cache_entry = _RETAINED_READ_CACHES.get(id(self))
        if cache_entry is not None:
            cache_ref, cache = cache_entry
            if cache_ref() is not self:
                with _RETAINED_READ_CACHE_LOCK:
                    current = _RETAINED_READ_CACHES.get(id(self))
                    if current is cache_entry:
                        _RETAINED_READ_CACHES.pop(id(self), None)
                cache_entry = None
            else:
                if state is not cache.original_state:
                    raise UnifiedSourceIndexStoreError(
                        "retained read state failed integrity validation"
                    )
                _validate_retained_read_cache(cache, binding)
                return cache.detached_state

        if type(state) is not _SnapshotState:
            raise UnifiedSourceIndexStoreError(
                "retained read state failed integrity validation"
            )
        snapshot = _copy_snapshot(state.snapshot)
        decoded_records: dict[StoreKey, StoreRecord] = {}
        try:
            detached = InMemoryUnifiedSourceIndexStore._make_state(
                state.records,
                state.tombstones,
                state.forge_occurrences,
                snapshot.revision,
                decoded_records=decoded_records,
            )
            _validate_forge_occurrence_state(
                detached.records,
                detached.forge_occurrences,
                decoded_records=decoded_records,
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
        return _issue_retained_read_cache(self, state, binding, detached)

    def _finish_read_operation(self, state: _SnapshotState) -> None:
        cache = _retained_read_cache_for(self)
        if state is not cache.detached_state:
            raise UnifiedSourceIndexStoreError(
                "retained read state failed integrity validation"
            )
        _validate_retained_read_cache(cache, cache.transaction_snapshot_binding)

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


def _validate_retained_read_transaction_binding(binding: object) -> tuple[object, ...]:
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
    return binding


def _make_retained_read_index(state: _SnapshotState) -> _RetainedReadIndex:
    records: dict[tuple[RecordKind, str], list[StoreKey]] = {}
    complete: dict[tuple[RecordKind, str], list[StoreKey]] = {}
    for key, item in state.records.items():
        if type(key) is not tuple or len(key) != 2 or type(key[0]) is not RecordKind or type(key[1]) is not str:
            raise UnifiedSourceIndexStoreError("retained read index failed integrity validation")
        if type(item) is not _StoredEntry or type(item.owner_scope) is not str:
            raise UnifiedSourceIndexStoreError("retained read index failed integrity validation")
        scope = (key[0], item.owner_scope)
        records.setdefault(scope, []).append(key)
        complete.setdefault(scope, []).append(key)
    for key, item in state.tombstones.items():
        if type(key) is not tuple or len(key) != 2 or type(key[0]) is not RecordKind or type(key[1]) is not str:
            raise UnifiedSourceIndexStoreError("retained read index failed integrity validation")
        if type(item) is not _TombstoneEntry or type(item.owner_scope) is not str:
            raise UnifiedSourceIndexStoreError("retained read index failed integrity validation")
        complete.setdefault((key[0], item.owner_scope), []).append(key)

    def freeze(groups: dict[tuple[RecordKind, str], list[StoreKey]]) -> Mapping[tuple[RecordKind, str], _RetainedReadIndexGroup]:
        result: dict[tuple[RecordKind, str], _RetainedReadIndexGroup] = {}
        for scope, keys in groups.items():
            ordered = tuple(sorted(keys, key=lambda key: key[1]))
            record_ids = tuple(key[1] for key in ordered)
            if len(record_ids) != len(set(record_ids)):
                raise UnifiedSourceIndexStoreError("retained read index contains duplicate identities")
            result[scope] = _RetainedReadIndexGroup(record_ids, ordered)
        return MappingProxyType(result)

    return _RetainedReadIndex(freeze(records), freeze(complete))


def _retained_read_index_binding(index: object) -> tuple[object, ...]:
    if type(index) is not _RetainedReadIndex:
        raise UnifiedSourceIndexStoreError("retained read index failed integrity validation")

    def capture(
        groups: object,
    ) -> tuple[tuple[object, ...], ...]:
        if type(groups) is not _MAPPING_PROXY_TYPE:
            raise UnifiedSourceIndexStoreError("retained read index failed integrity validation")
        rows: list[tuple[object, ...]] = []
        for scope, group in groups.items():
            if (
                type(scope) is not tuple
                or len(scope) != 2
                or type(scope[0]) is not RecordKind
                or type(scope[1]) is not str
                or type(group) is not _RetainedReadIndexGroup
                or type(group.record_ids) is not tuple
                or type(group.keys) is not tuple
                or len(group.record_ids) != len(group.keys)
            ):
                raise UnifiedSourceIndexStoreError("retained read index failed integrity validation")
            previous = ""
            pairs: list[tuple[str, RecordKind, str]] = []
            for record_id, key in zip(group.record_ids, group.keys, strict=True):
                if (
                    type(record_id) is not str
                    or type(key) is not tuple
                    or len(key) != 2
                    or type(key[0]) is not RecordKind
                    or type(key[1]) is not str
                    or key[0] is not scope[0]
                    or key[1] != record_id
                    or (previous and record_id <= previous)
                ):
                    raise UnifiedSourceIndexStoreError("retained read index failed integrity validation")
                previous = record_id
                pairs.append((record_id, key[0], key[1]))
            rows.append((scope[0].value, scope[1], tuple(pairs)))
        return tuple(sorted(rows, key=lambda row: (cast(str, row[0]), cast(str, row[1]))))

    return (capture(index.records), capture(index.including_tombstones))


def _issue_retained_read_cache(
    transaction: InMemoryStoreReadTransaction,
    state: _SnapshotState,
    binding: tuple[object, ...],
    detached: _SnapshotState,
) -> _SnapshotState:
    try:
        original_binding = _snapshot_state_content_binding(state)
        detached_binding = _snapshot_state_content_binding(detached)
        if original_binding != detached_binding:
            raise ValueError
        index = _make_retained_read_index(detached)
        index_binding = _retained_read_index_binding(index)
        candidate = _RetainedReadCache(
            state,
            state.snapshot,
            state.records,
            state.tombstones,
            state.forge_occurrences,
            binding,
            original_binding,
            detached,
            detached_binding,
            index,
            index_binding,
        )
        key = id(transaction)
        with _RETAINED_READ_CACHE_LOCK:
            current = _RETAINED_READ_CACHES.get(key)
            if current is not None:
                cache_ref, cache = current
                if cache_ref() is transaction:
                    if (
                        cache.original_state is not state
                        or cache.transaction_snapshot_binding != binding
                        or cache.original_binding != original_binding
                        or cache.detached_binding != detached_binding
                        or cache.index_binding != index_binding
                    ):
                        raise ValueError
                    _validate_retained_read_cache(cache, binding)
                    return cache.detached_state
                _RETAINED_READ_CACHES.pop(key, None)

            def discard(ref: weakref.ReferenceType[object], *, cache_key: int = key) -> None:
                with _RETAINED_READ_CACHE_LOCK:
                    current = _RETAINED_READ_CACHES.get(cache_key)
                    if current is not None and current[0] is ref:
                        _RETAINED_READ_CACHES.pop(cache_key, None)

            reference = weakref.ref(transaction, discard)
            _RETAINED_READ_CACHES[key] = (reference, candidate)
            return candidate.detached_state
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        raise UnifiedSourceIndexStoreError(
            "retained read state failed integrity validation"
        ) from None


def _retained_read_cache_for(
    transaction: InMemoryStoreReadTransaction,
) -> _RetainedReadCache:
    with _RETAINED_READ_CACHE_LOCK:
        current = _RETAINED_READ_CACHES.get(id(transaction))
        if current is None or current[0]() is not transaction:
            if current is not None:
                _RETAINED_READ_CACHES.pop(id(transaction), None)
            raise UnifiedSourceIndexStoreError("retained read state failed integrity validation")
        cache = current[1]
        if type(cache) is not _RetainedReadCache:
            raise UnifiedSourceIndexStoreError("retained read state failed integrity validation")
        return cache


def _discard_retained_read_cache(transaction: InMemoryStoreReadTransaction) -> None:
    with _RETAINED_READ_CACHE_LOCK:
        current = _RETAINED_READ_CACHES.get(id(transaction))
        if current is not None and current[0]() is transaction:
            _RETAINED_READ_CACHES.pop(id(transaction), None)


def _validate_retained_read_cache(
    cache: _RetainedReadCache,
    expected_transaction_binding: tuple[object, ...],
) -> None:
    try:
        if (
            type(cache) is not _RetainedReadCache
            or cache.original_state.snapshot is not cache.original_snapshot
            or cache.original_state.records is not cache.original_records
            or cache.original_state.tombstones is not cache.original_tombstones
            or cache.original_state.forge_occurrences is not cache.original_occurrences
            or _validate_retained_read_transaction_binding(
                cache.transaction_snapshot_binding
            ) != expected_transaction_binding
            or _snapshot_state_content_binding(cache.original_state)
            != cache.original_binding
            or _snapshot_state_content_binding(cache.detached_state)
            != cache.detached_binding
            or _retained_read_index_binding(cache.index) != cache.index_binding
        ):
            raise ValueError
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        raise UnifiedSourceIndexStoreError(
            "retained read state failed integrity validation"
        ) from None


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
        self._bulk_capture_active = False
        self._bulk_capture_reentered = False
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

    def put_many_paged_truth(
        self,
        records: tuple[tuple[StoreRecord, int], ...],
    ) -> None:
        """Atomically stage one bounded paged-truth batch on the memory fake."""

        with self._lock:
            self._ensure_open()
            self._bulk_capture_active = True
            self._bulk_capture_reentered = False
            try:
                self._stage_paged_truth_batch(records)
            finally:
                self._bulk_capture_active = False
                self._bulk_capture_reentered = False

    def _stage_paged_truth_batch(
        self,
        records: tuple[tuple[StoreRecord, int], ...],
    ) -> None:
        from src.unified_source_index_adapters import MAX_PAGED_EXTRACTION_RECORDS

        if (
            type(records) is not tuple
            or len(records) > MAX_PAGED_EXTRACTION_RECORDS + 2
        ):
            raise UnifiedSourceIndexStoreError(
                "paged truth batch must use the existing bounded record limit"
            )

        original_state = self._state
        original_base_binding = self._base_snapshot_binding
        original_mutations = self._mutations
        original_occurrences = self._forge_occurrence_mutations
        original_mutation_items = tuple(original_mutations.items())
        original_occurrence_items = tuple(original_occurrences.items())
        staged = dict(original_mutations)
        captured_records: list[StoreRecord] = []

        for item in records:
            if type(item) is not tuple or len(item) != 2:
                raise UnifiedSourceIndexStoreError(
                    "paged truth entries must be exact record and revision pairs"
                )
            raw_record, raw_expected_revision = item
            if type(raw_expected_revision) is not int:
                raise UnifiedSourceIndexStoreError(
                    "paged truth expected revisions must use exact integers"
                )
            captured, canonical_bytes = _capture_store_record(raw_record)
            kind, record_id, owner = _record_descriptor(captured)
            key = (kind, record_id)
            if key in staged:
                raise UnifiedSourceIndexStoreError(
                    "a record may be mutated only once per transaction"
                )
            if key in self._state.tombstones:
                raise StoreTombstoneError(
                    "record identity is tombstoned; use restore with its revision"
                )
            actual = _key_revision(
                self._state.records,
                self._state.tombstones,
                key,
            )
            expected = _revision(
                raw_expected_revision,
                "expected_record_revision",
            )
            _require_revision_match(
                expected,
                actual,
                "record revision is stale",
            )
            staged[key] = _PutMutation(
                kind,
                record_id,
                owner,
                canonical_bytes,
                expected,
            )
            captured_records.append(captured)

        captured_by_key = {
            _record_descriptor(record)[:2]: record for record in captured_records
        }
        for record in captured_records:
            if _is_forge_bound_record(
                record,
                records=self._state.records,
                staged=staged,
                captured_records=captured_by_key,
            ):
                raise UnifiedSourceIndexStoreError(
                    "Forge-bound records require atomic put_code_occurrence"
                )

        # Serializers and validators are allowed to fail, so publish the local
        # mutation map only after the base and staged authority have survived a
        # fresh integrity check. The active flag rejects reentrant mutations.
        InMemoryUnifiedSourceIndexStore._assert_snapshot_state_integrity(
            self._state
        )
        if (
            self._bulk_capture_active is not True
            or self._bulk_capture_reentered is not False
            or self._phase != "open"
            or self._state is not original_state
            or self._base_snapshot_binding != original_base_binding
            or self._mutations is not original_mutations
            or self._forge_occurrence_mutations is not original_occurrences
            or len(original_mutations) != len(original_mutation_items)
            or any(
                key not in original_mutations
                or original_mutations[key] is not mutation
                for key, mutation in original_mutation_items
            )
            or len(original_occurrences) != len(original_occurrence_items)
            or any(
                key not in original_occurrences
                or original_occurrences[key] is not mutation
                for key, mutation in original_occurrence_items
            )
        ):
            original_mutations.clear()
            original_mutations.update(original_mutation_items)
            original_occurrences.clear()
            original_occurrences.update(original_occurrence_items)
            self._mutations = original_mutations
            self._forge_occurrence_mutations = original_occurrences
            raise UnifiedSourceIndexStoreError(
                "paged truth transaction changed during canonical capture"
            )
        self._mutations = staged

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

        def staged_tombstone(kind: RecordKind, record_id: str) -> bool:
            mutation = self._mutations.get((kind, record_id))
            return type(mutation) is _TombstoneMutation

        if not _is_forge_bound_record(
            record,
            records=self._state.records,
            staged=self._mutations,
        ):
            return
        if type(record) is SourceRecord and any(
            type(candidate) is SourceVersionRecord
            and candidate.source_id == record.source_id
            and not staged_tombstone(RecordKind.SOURCE_VERSION, candidate.source_version_id)
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
            and not staged_tombstone(RecordKind.CHUNK, candidate.chunk_id)
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
            if self._bulk_capture_active:
                self._bulk_capture_reentered = True
                raise UnifiedSourceIndexStoreError(
                    "cannot roll back during paged truth canonical capture"
                )
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
        if type(self._bulk_capture_active) is not bool:
            raise UnifiedSourceIndexStoreError(
                "paged truth capture phase failed integrity validation"
            )
        if self._bulk_capture_active:
            if type(self._bulk_capture_reentered) is bool:
                self._bulk_capture_reentered = True
            raise UnifiedSourceIndexStoreError(
                "reentrant transaction mutation during paged truth capture"
            )
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
            current_validated = False
            if snapshot is None:
                self._assert_authority_envelope()
                revision = self._revision
                history = self._history
                records = self._records
                tombstones = self._tombstones
                forge_occurrences = self._forge_occurrences
                authority = history.get(revision)
                if authority is None:
                    raise UnifiedSourceIndexStoreError(
                        "current store authority is unavailable"
                    )
                try:
                    preproof = (
                        revision,
                        id(history),
                        id(authority),
                        id(records),
                        id(tombstones),
                        id(forge_occurrences),
                        _snapshot_state_content_binding(authority),
                        _live_store_content_binding(
                            revision,
                            records,
                            tombstones,
                            forge_occurrences,
                        ),
                    )
                except UnifiedSourceIndexStoreError:
                    raise
                except Exception:
                    raise UnifiedSourceIndexStoreError(
                        "current store state failed integrity validation"
                    ) from None
                self._assert_current_state_integrity()
                try:
                    requested = _copy_snapshot(authority.snapshot)
                    self._assert_authority_envelope()
                    state = history.get(requested.revision)
                    postproof = (
                        self._revision,
                        id(self._history),
                        id(self._history.get(self._revision)),
                        id(self._records),
                        id(self._tombstones),
                        id(self._forge_occurrences),
                        _snapshot_state_content_binding(
                            self._history.get(self._revision)
                        ),
                        _live_store_content_binding(
                            self._revision,
                            self._records,
                            self._tombstones,
                            self._forge_occurrences,
                        ),
                    )
                except UnifiedSourceIndexStoreError:
                    raise
                except Exception:
                    raise UnifiedSourceIndexStoreError(
                        "current store state failed integrity validation"
                    ) from None
                if (
                    state is not authority
                    or postproof != preproof
                ):
                    raise UnifiedSourceIndexStoreError(
                        "current store state changed during read capture"
                    )
                current_validated = True
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
            if current_validated:
                try:
                    self._assert_authority_envelope()
                    final_revision = self._revision
                    final_history = self._history
                    final_authority = final_history.get(final_revision)
                    if final_authority is None:
                        raise UnifiedSourceIndexStoreError(
                            "current store authority is unavailable"
                        )
                    finalproof = (
                        final_revision,
                        id(final_history),
                        id(final_authority),
                        id(self._records),
                        id(self._tombstones),
                        id(self._forge_occurrences),
                        _snapshot_state_content_binding(final_authority),
                        _live_store_content_binding(
                            final_revision,
                            self._records,
                            self._tombstones,
                            self._forge_occurrences,
                        ),
                    )
                except UnifiedSourceIndexStoreError:
                    raise
                except Exception:
                    raise UnifiedSourceIndexStoreError(
                        "current store state failed integrity validation"
                    ) from None
                if (
                    finalproof != preproof
                    or final_authority is not authority
                    or state is not final_authority
                ):
                    raise UnifiedSourceIndexStoreError(
                        "current store state changed during read capture"
                    )
            if not current_validated:
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
        revision = self._revision
        history = self._history
        records = self._records
        tombstones = self._tombstones
        forge_occurrences = self._forge_occurrences
        authority = history.get(revision)
        if authority is None:
            raise UnifiedSourceIndexStoreError("current store authority is unavailable")
        try:
            authority_binding = _snapshot_state_content_binding(authority)
        except Exception:
            raise UnifiedSourceIndexStoreError(
                "current store state failed integrity validation"
            ) from None
        self._assert_snapshot_state_integrity(authority)
        if _snapshot_state_content_binding(authority) != authority_binding:
            raise UnifiedSourceIndexStoreError(
                "current store state failed integrity validation"
            )

        # Decode live records once for this operation. Forge validation consumes
        # that exact detached capture; the surrounding bindings check every
        # metadata field without decoding the records a second time.
        try:
            live_binding = _live_store_content_binding(
                revision,
                records,
                tombstones,
                forge_occurrences,
            )
            decoded_records: dict[StoreKey, StoreRecord] = {}
            current = self._make_state(
                records,
                tombstones,
                forge_occurrences,
                revision,
                decoded_records=decoded_records,
            )
            current_binding = _snapshot_state_content_binding(current)
            if current_binding[0][0] != revision or current_binding[1:] != live_binding:
                raise UnifiedSourceIndexStoreError(
                    "current store state failed integrity validation"
                )
            if current_binding != authority_binding:
                raise UnifiedSourceIndexStoreError(
                    "current store state failed integrity validation"
                )
        except Exception:
            raise UnifiedSourceIndexStoreError(
                "current store state failed integrity validation"
            ) from None

        # Keep Forge semantic diagnostics visible as they were before the live
        # capture optimization; only the capture and binding boundary projects
        # lower-level failures to the stable current-integrity error.
        _validate_forge_occurrence_state(
            current.records,
            current.forge_occurrences,
            decoded_records=decoded_records,
        )
        try:
            self._assert_authority_envelope()
            if (
                _snapshot_state_content_binding(current) != current_binding
                or _snapshot_state_content_binding(authority) != authority_binding
                or _live_store_content_binding(
                    revision,
                    records,
                    tombstones,
                    forge_occurrences,
                )
                != live_binding
                or current_binding != authority_binding
                or self._revision != revision
                or self._history is not history
                or self._records is not records
                or self._tombstones is not tombstones
                or self._forge_occurrences is not forge_occurrences
                or history.get(revision) is not authority
            ):
                raise UnifiedSourceIndexStoreError(
                    "current store state failed integrity validation"
                )
        except Exception:
            raise UnifiedSourceIndexStoreError(
                "current store state failed integrity validation"
            ) from None

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
        decoded_records: dict[StoreKey, StoreRecord] = {}
        try:
            recomputed = cls._make_state(
                state.records,
                state.tombstones,
                state.forge_occurrences,
                snapshot.revision,
                decoded_records=decoded_records,
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
            decoded_records=decoded_records,
        )

    @staticmethod
    def _make_state(
        records: Mapping[StoreKey, _StoredEntry],
        tombstones: Mapping[StoreKey, _TombstoneEntry],
        forge_occurrences: Mapping[str, bytes],
        revision: int,
        *,
        decoded_records: dict[StoreKey, StoreRecord] | None = None,
    ) -> _SnapshotState:
        if type(records) not in (dict, _MAPPING_PROXY_TYPE):
            raise UnifiedSourceIndexStoreError("store records must use an exact mapping")
        if type(tombstones) not in (dict, _MAPPING_PROXY_TYPE):
            raise UnifiedSourceIndexStoreError("store tombstones must use an exact mapping")
        if type(forge_occurrences) not in (dict, _MAPPING_PROXY_TYPE):
            raise UnifiedSourceIndexStoreError("Forge evidence must use an exact mapping")
        if decoded_records is not None and (
            type(decoded_records) is not dict or decoded_records
        ):
            raise UnifiedSourceIndexStoreError(
                "decoded record capture must start with an exact empty mapping"
            )
        captured_records: dict[StoreKey, _StoredEntry] = {}
        for raw_key, raw_item in records.items():
            key = _capture_store_key(raw_key)
            item = _capture_stored_entry(
                key,
                raw_item,
                decoded_records=decoded_records,
            )
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


def _capture_stored_entry(
    key: StoreKey,
    item: object,
    *,
    decoded_records: dict[StoreKey, StoreRecord] | None = None,
) -> _StoredEntry:
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
    if decoded_records is not None:
        if type(decoded_records) is not dict or key in decoded_records:
            raise UnifiedSourceIndexStoreError(
                "decoded record capture contains a duplicate or foreign key"
            )
        decoded_records[key] = record
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


def _snapshot_state_content_binding(state: object) -> tuple[object, ...]:
    """Capture all snapshot and entry metadata without decoding record bytes."""

    if type(state) is not _SnapshotState:
        raise UnifiedSourceIndexStoreError("snapshot state failed integrity validation")
    snapshot = _snapshot_values(state.snapshot)
    if any(
        type(mapping) not in (dict, _MAPPING_PROXY_TYPE)
        for mapping in (state.records, state.tombstones, state.forge_occurrences)
    ):
        raise UnifiedSourceIndexStoreError("snapshot mappings failed integrity validation")

    records: list[tuple[object, ...]] = []
    for raw_key, item in state.records.items():
        key = _capture_store_key(raw_key)
        if type(item) is not _StoredEntry or (
            type(item.record_kind) is not RecordKind
            or type(item.record_id) is not str
            or type(item.owner_scope) is not str
            or type(item.revision) is not int
            or type(item.canonical_bytes) is not bytes
            or (item.record_kind, item.record_id) != key
        ):
            raise UnifiedSourceIndexStoreError("stored entry failed integrity validation")
        records.append(
            (
                key[0].value,
                key[1],
                item.record_kind,
                item.record_id,
                item.owner_scope,
                item.revision,
                item.canonical_bytes,
            )
        )

    tombstones: list[tuple[object, ...]] = []
    for raw_key, item in state.tombstones.items():
        key = _capture_store_key(raw_key)
        captured = _capture_tombstone_entry(key, item)
        tombstones.append(
            (
                key[0].value,
                key[1],
                captured.record_kind,
                captured.record_id,
                captured.owner_scope,
                captured.previous_revision,
                captured.revision,
                captured.reason,
            )
        )

    occurrences: list[tuple[str, bytes]] = []
    for raw_chunk_id, raw_evidence in state.forge_occurrences.items():
        occurrences.append(
            (
                _capture_exact_text(raw_chunk_id, "Forge occurrence chunk_id"),
                _capture_exact_bytes(raw_evidence, "Forge occurrence evidence"),
            )
        )
    return (
        snapshot,
        len(records),
        tuple(sorted(records, key=lambda row: (cast(str, row[0]), cast(str, row[1])))),
        len(tombstones),
        tuple(sorted(tombstones, key=lambda row: (cast(str, row[0]), cast(str, row[1])))),
        len(occurrences),
        tuple(sorted(occurrences)),
    )


def _live_store_content_binding(
    revision: object,
    records: object,
    tombstones: object,
    forge_occurrences: object,
) -> tuple[object, ...]:
    """Bind the live mapping identities and contents around Forge validation."""

    if type(revision) is not int or any(
        type(mapping) not in (dict, _MAPPING_PROXY_TYPE)
        for mapping in (records, tombstones, forge_occurrences)
    ):
        raise UnifiedSourceIndexStoreError("current store mappings failed integrity validation")
    # The synthetic snapshot is deliberately ignored; live state has no
    # separately stored snapshot object. Entry metadata and all three mapping
    # contents are still captured with the same exact-type checks.
    record_rows: list[tuple[object, ...]] = []
    for raw_key, item in cast(Mapping[object, object], records).items():
        key = _capture_store_key(raw_key)
        if type(item) is not _StoredEntry or (
            type(item.record_kind) is not RecordKind
            or type(item.record_id) is not str
            or type(item.owner_scope) is not str
            or type(item.revision) is not int
            or type(item.canonical_bytes) is not bytes
            or (item.record_kind, item.record_id) != key
        ):
            raise UnifiedSourceIndexStoreError("stored entry failed integrity validation")
        record_rows.append(
            (
                key[0].value,
                key[1],
                item.record_kind,
                item.record_id,
                item.owner_scope,
                item.revision,
                item.canonical_bytes,
            )
        )
    tombstone_rows: list[tuple[object, ...]] = []
    for raw_key, item in cast(Mapping[object, object], tombstones).items():
        key = _capture_store_key(raw_key)
        captured = _capture_tombstone_entry(key, item)
        tombstone_rows.append(
            (
                key[0].value,
                key[1],
                captured.record_kind,
                captured.record_id,
                captured.owner_scope,
                captured.previous_revision,
                captured.revision,
                captured.reason,
            )
        )
    occurrence_rows = tuple(
        sorted(
            (
                _capture_exact_text(chunk_id, "Forge occurrence chunk_id"),
                _capture_exact_bytes(evidence, "Forge occurrence evidence"),
            )
            for chunk_id, evidence in cast(Mapping[object, object], forge_occurrences).items()
        )
    )
    return (
        len(record_rows),
        tuple(sorted(record_rows, key=lambda row: (cast(str, row[0]), cast(str, row[1])))),
        len(tombstone_rows),
        tuple(sorted(tombstone_rows, key=lambda row: (cast(str, row[0]), cast(str, row[1])))),
        len(occurrence_rows),
        occurrence_rows,
    )


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
    captured_records: Mapping[StoreKey, StoreRecord] | None = None,
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
            captured_records=captured_records,
        )
        return type(parent) is SourceRecord and _is_forge_bound_record(
            parent,
            records=records,
            staged=staged,
            captured_records=captured_records,
        )
    if type(record) is not ChunkRecord:
        return False
    if record.extractor_profile_ref.startswith("forge-code-"):
        return True
    parent = _staged_or_live_record(
        (RecordKind.SOURCE_VERSION, record.source_version_id),
        records=records,
        staged=staged,
        captured_records=captured_records,
    )
    return type(parent) is SourceVersionRecord and _is_forge_bound_record(
        parent,
        records=records,
        staged=staged,
        captured_records=captured_records,
    )


def _staged_or_live_record(
    key: StoreKey,
    *,
    records: Mapping[StoreKey, _StoredEntry],
    staged: Mapping[StoreKey, Mutation],
    captured_records: Mapping[StoreKey, StoreRecord] | None = None,
) -> StoreRecord | None:
    mutation = staged.get(key)
    if type(mutation) is _PutMutation:
        captured = None if captured_records is None else captured_records.get(key)
        return (
            _record_from_canonical_bytes(mutation.canonical_bytes)
            if captured is None
            else captured
        )
    if captured_records is not None and key in captured_records:
        return captured_records[key]
    stored = records.get(key)
    return (
        None
        if stored is None
        else _record_from_canonical_bytes(stored.canonical_bytes)
    )


def _validate_forge_occurrence_state(
    records: Mapping[StoreKey, _StoredEntry],
    forge_occurrences: Mapping[str, bytes],
    *,
    decoded_records: dict[StoreKey, StoreRecord] | None = None,
) -> None:
    """Prove that every live Forge child has one exact durable parent chain."""

    try:
        from src.unified_source_index_sources.forge_code import (
            validate_forge_code_occurrence_records,
        )

        if decoded_records is None:
            validated_records = {
                key: _record_from_canonical_bytes(
                    _capture_stored_entry(key, item).canonical_bytes
                )
                for key, item in records.items()
            }
        else:
            if (
                type(decoded_records) is not dict
                or set(decoded_records) != set(records)
                or any(
                    type(record) not in _RECORD_TYPES
                    or _record_descriptor(record)[:2] != key
                    for key, record in decoded_records.items()
                )
            ):
                raise UnifiedSourceIndexStoreError(
                    "Forge validation record capture is incomplete"
                )
            validated_records = decoded_records
        forge_chunk_ids: set[str] = set()
        for (kind, record_id), item in records.items():
            record = validated_records[(kind, record_id)]
            if (kind, record_id) != _record_descriptor(record)[:2]:
                raise UnifiedSourceIndexStoreError(
                    "Forge store record key failed integrity validation"
                )
            if type(record) is SourceRecord and _is_forge_bound_record(
                record,
                records=records,
                staged={},
                captured_records=validated_records,
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
                captured_records=validated_records,
            ):
                if not (
                    record.provider_ref == "forge.code"
                    and record.revision_ref.startswith("forge-code-version:")
                ):
                    raise UnifiedSourceIndexStoreError(
                        "Forge source version has hybrid authority labels"
                    )
                source = validated_records.get((RecordKind.SOURCE, record.source_id))
                if (
                    source is None
                    or type(source) is not SourceRecord
                    or not _is_forge_bound_record(
                        source,
                        records=records,
                        staged={},
                        captured_records=validated_records,
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
                captured_records=validated_records,
            ):
                continue
            if not record.extractor_profile_ref.startswith("forge-code-"):
                raise UnifiedSourceIndexStoreError(
                    "Forge chunk has hybrid extractor authority"
                )
            forge_chunk_ids.add(record.chunk_id)
            version = validated_records.get(
                (RecordKind.SOURCE_VERSION, record.source_version_id)
            )
            if version is None or type(version) is not SourceVersionRecord:
                raise UnifiedSourceIndexStoreError(
                    "Forge chunk has no canonical live version parent"
                )
            source = validated_records.get((RecordKind.SOURCE, record.source_id))
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


# ULO-02 v6 trusted concrete-store authority.  This layer is intentionally
# appended to the existing USI-02 contract so the older backend-neutral store
# surface remains unchanged.
_ULO02_AUTHORITY_RE = re.compile(r"^usi_authority_[0-9a-f]{64}$")
_ULO02_DIGEST_RE = re.compile(r"^(?:sha256:|usi_plan_)[0-9a-f]{64}$")
_ULO02_REASON_RE = re.compile(r"^ulo2:[a-z][a-z0-9_.:-]{0,127}:[0-9a-f]{64}$")
_ULO02_RLOCK_TYPE = type(RLock())
_ULO02_IN_MEMORY_METHODS = (
    InMemoryUnifiedSourceIndexStore.current_snapshot,
    InMemoryUnifiedSourceIndexStore.begin_read,
    InMemoryUnifiedSourceIndexStore.begin_write,
    InMemoryStoreReadTransaction.get,
    InMemoryStoreReadTransaction.require,
    InMemoryStoreReadTransaction.list_records,
    InMemoryStoreReadTransaction.close,
    InMemoryStoreWriteTransaction.tombstone,
    InMemoryStoreWriteTransaction.commit,
    InMemoryStoreWriteTransaction.rollback,
)


@dataclass(frozen=True, slots=True, init=False)
class TombstoneCommitReceipt:
    backend_kind: str
    authority_ref: str
    plan_digest: str
    base_snapshot: StoreSnapshot
    committed_snapshot: StoreSnapshot
    command_count: int
    command_digest: str
    readback_digest: str
    receipt_digest: str
    commands: tuple[object, ...]
    _store_binding: object
    _trusted_store: object

    def __init__(self, *args, **kwargs) -> None:
        raise UnifiedSourceIndexStoreError("tombstone receipt is factory-only") from None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": f"{USI_STORE_SCHEMA}.tombstone_commit_receipt.v1",
            "backend_kind": self.backend_kind,
            "authority_ref": self.authority_ref,
            "plan_digest": self.plan_digest,
            "base_snapshot": _ulo02_snapshot_payload(self.base_snapshot),
            "committed_snapshot": _ulo02_snapshot_payload(self.committed_snapshot),
            "command_count": self.command_count,
            "command_digest": self.command_digest,
            "readback_digest": self.readback_digest,
            "receipt_digest": self.receipt_digest,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True, init=False)
class VerifiedTombstoneCommit:
    receipt: TombstoneCommitReceipt
    _store_binding: object

    def __init__(self, *args, **kwargs) -> None:
        raise UnifiedSourceIndexStoreError("verified tombstone commit is factory-only") from None


def _ulo02_internal(cls, values: dict[str, object]):
    result = object.__new__(cls)
    for field_name, value in values.items():
        object.__setattr__(result, field_name, value)
    return result


def _ulo02_fail() -> None:
    raise UnifiedSourceIndexStoreError("ULO-02 trusted store verification failed") from None


def _ulo02_store_gate(store: object):
    if type(store) is InMemoryUnifiedSourceIndexStore:
        try:
            current_methods = (
                InMemoryUnifiedSourceIndexStore.current_snapshot,
                InMemoryUnifiedSourceIndexStore.begin_read,
                InMemoryUnifiedSourceIndexStore.begin_write,
                InMemoryStoreReadTransaction.get,
                InMemoryStoreReadTransaction.require,
                InMemoryStoreReadTransaction.list_records,
                InMemoryStoreReadTransaction.close,
                InMemoryStoreWriteTransaction.tombstone,
                InMemoryStoreWriteTransaction.commit,
                InMemoryStoreWriteTransaction.rollback,
            )
            if any(current is not pinned for current, pinned in zip(current_methods, _ULO02_IN_MEMORY_METHODS, strict=True)):
                _ulo02_fail()
            if set(vars(store)) != {
                "_lock", "_records", "_tombstones", "_forge_occurrences", "_revision", "_history"
            } or type(store._lock) is not _ULO02_RLOCK_TYPE or type(store._records) is not dict or type(store._tombstones) is not dict or type(store._forge_occurrences) is not dict or type(store._revision) is not int or type(store._history) is not dict:
                _ulo02_fail()
            current = InMemoryUnifiedSourceIndexStore.current_snapshot(store)
        except Exception:
            _ulo02_fail()
        return (
            "in_memory", current, InMemoryUnifiedSourceIndexStore.begin_read,
            InMemoryUnifiedSourceIndexStore.begin_write, id(store),
            InMemoryStoreReadTransaction, InMemoryStoreWriteTransaction,
        )
    try:
        from src.unified_source_index_sqlite import (
            SQLiteStoreReadTransaction,
            SQLiteStoreWriteTransaction,
            SQLiteUnifiedSourceIndexStore,
        )
    except Exception:
        _ulo02_fail()
    if type(store) is SQLiteUnifiedSourceIndexStore:
        try:
            if set(vars(store)) != {"_database_path", "_busy_timeout_ms"} or type(store._database_path) is not str or not store._database_path or type(store._busy_timeout_ms) is not int:
                _ulo02_fail()
            current = SQLiteUnifiedSourceIndexStore.current_snapshot(store)
        except Exception:
            _ulo02_fail()
        return (
            "sqlite", current, SQLiteUnifiedSourceIndexStore.begin_read,
            SQLiteUnifiedSourceIndexStore.begin_write, id(store),
            SQLiteStoreReadTransaction, SQLiteStoreWriteTransaction,
        )
    _ulo02_fail()


def _ulo02_exact_transaction(transaction: object, transaction_type: type, *, write: bool) -> None:
    if type(transaction) is not transaction_type:
        _ulo02_fail()
    keys = set(vars(transaction))
    if transaction_type is InMemoryStoreReadTransaction:
        expected = {"_state", "_snapshot_binding", "_closed"}
    elif transaction_type is InMemoryStoreWriteTransaction:
        expected = {
            "_store", "_state", "_base_snapshot_binding", "_mutations",
            "_forge_occurrence_mutations", "_phase", "_bulk_capture_active",
            "_bulk_capture_reentered", "_lock",
        }
    else:
        try:
            from src.unified_source_index_sqlite import (
                SQLiteStoreReadTransaction, SQLiteStoreWriteTransaction,
            )
        except Exception:
            _ulo02_fail()
        if transaction_type is SQLiteStoreReadTransaction:
            expected = {"_connection", "_snapshot", "_closed"}
        elif transaction_type is SQLiteStoreWriteTransaction:
            expected = {
                "_store", "_connection", "_base_snapshot", "_mutations",
                "_forge_occurrence_mutations", "_publication_mutation", "_closed",
            }
        else:
            _ulo02_fail()
    if keys != expected:
        _ulo02_fail()
    if transaction_type is InMemoryStoreWriteTransaction:
        is_closed = type(transaction._phase) is str and transaction._phase == "closed"
    else:
        is_closed = type(getattr(transaction, "_closed", None)) is bool and transaction._closed
    if write and is_closed:
        _ulo02_fail()


def _ulo02_read_records(
    store: object,
    snapshot: StoreSnapshot,
    *,
    capability: _TrustedTombstoneStore,
    begin_read: object,
    read_type: type,
    commands: tuple[tuple[RecordKind, str, int, str, str], ...],
    expected_live_snapshot: StoreSnapshot,
) -> tuple[object, ...]:
    try:
        if not _ulo02_capability_matches(capability, expected_snapshot=expected_live_snapshot):
            _ulo02_fail()
        read = capability.begin_read(capability.store, snapshot)
        _ulo02_exact_transaction(read, read_type, write=False)
        items = tuple(
            capability.read_get(
                read, command[0], command[1],
                owner_scope=command[4], include_tombstone=True,
            )
            for command in commands
        )
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        _ulo02_fail()
    finally:
        if "read" in locals():
            try:
                capability.read_close(read)
            except Exception:
                _ulo02_fail()
    return items


def _ulo02_command_values(command: object) -> tuple[RecordKind, str, int, str, str]:
    try:
        from src.unified_source_index_tombstone_policy import TombstoneInstruction
    except Exception:
        _ulo02_fail()
    if type(command) is not TombstoneInstruction:
        _ulo02_fail()
    try:
        ref = object.__getattribute__(command, "record_ref")
        revision = object.__getattribute__(command, "record_revision")
        reason = object.__getattribute__(command, "reason")
        owner = object.__getattribute__(command, "owner_scope")
    except Exception:
        _ulo02_fail()
    if type(ref) is not RecordRef:
        _ulo02_fail()
    try:
        kind = object.__getattribute__(ref, "record_kind")
        record_id = object.__getattribute__(ref, "record_id")
    except Exception:
        _ulo02_fail()
    if type(kind) is not RecordKind or type(record_id) is not str or not record_id:
        _ulo02_fail()
    if type(revision) is not int or revision < 1:
        _ulo02_fail()
    if type(reason) is not str or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", reason):
        _ulo02_fail()
    if type(owner) is not str:
        _ulo02_fail()
    return kind, record_id, revision, reason, owner


def _ulo02_command_projection(value: object) -> tuple[RecordKind, str, int, str, str]:
    if type(value) is tuple and len(value) == 5:
        kind, record_id, revision, reason, owner = value
        if (type(kind) is RecordKind and type(record_id) is str and bool(record_id)
                and type(revision) is int and revision > 0 and type(reason) is str
                and re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", reason)
                and type(owner) is str):
            return kind, record_id, revision, reason, owner
        _ulo02_fail()
    return _ulo02_command_values(value)


def _ulo02_owner_for_command(store: object, kind: RecordKind, record_id: str, hinted: str, backend: str, historical_state: object | None = None) -> str:
    if type(hinted) is str and hinted:
        return _owner_scope(hinted)
    if backend == "in_memory":
        if type(historical_state) is _SnapshotState:
            entry = historical_state.records.get((kind, record_id))
            if type(entry) is _StoredEntry and entry.record_kind is kind and entry.record_id == record_id:
                return _owner_scope(entry.owner_scope)
            _ulo02_fail()
        entry = store._records.get((kind, record_id))
        if type(entry) is _StoredEntry:
            return _owner_scope(entry.owner_scope)
    _ulo02_fail()


def _ulo02_command_digest(commands: tuple[object, ...]) -> str:
    values = []
    for command in commands:
        kind, record_id, revision, reason, owner = _ulo02_command_projection(command)
        values.append({"record_kind": kind.value, "record_id": record_id, "record_revision": revision, "reason": reason, "owner_scope": owner})
    return "sha256:" + hashlib.sha256(json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _ulo02_reason(authority_ref: str, plan_digest: str, kind: RecordKind, record_id: str, owner: str, revision: int, reason: str) -> str:
    payload = "|".join((authority_ref, plan_digest, kind.value, record_id, owner, str(revision), reason)).encode("ascii")
    return f"ulo2:{reason}:{hashlib.sha256(payload).hexdigest()}"


def _ulo02_snapshot_values(snapshot: StoreSnapshot) -> tuple[object, ...]:
    if type(snapshot) is not StoreSnapshot:
        _ulo02_fail()
    try:
        revision = object.__getattribute__(snapshot, "revision")
        state_hash = object.__getattribute__(snapshot, "state_hash")
        record_count = object.__getattribute__(snapshot, "record_count")
        tombstone_count = object.__getattribute__(snapshot, "tombstone_count")
        snapshot_ref = object.__getattribute__(snapshot, "snapshot_ref")
    except Exception:
        _ulo02_fail()
    if (
        type(revision) is not int or revision < 0
        or type(record_count) is not int or record_count < 0
        or type(tombstone_count) is not int or tombstone_count < 0
        or type(state_hash) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", state_hash) is None
        or type(snapshot_ref) is not str or re.fullmatch(r"usi_snapshot_[0-9a-f]{64}", snapshot_ref) is None
    ):
        _ulo02_fail()
    return (revision, state_hash, record_count, tombstone_count, snapshot_ref)


def _ulo02_snapshot_payload(snapshot: StoreSnapshot) -> dict[str, object]:
    revision, state_hash, record_count, tombstone_count, snapshot_ref = _ulo02_snapshot_values(snapshot)
    return {"revision": revision, "state_hash": state_hash, "record_count": record_count, "tombstone_count": tombstone_count, "snapshot_ref": snapshot_ref}


def _ulo02_receipt_digest(backend: str, authority_ref: str, plan_digest: str, base: StoreSnapshot, committed: StoreSnapshot, command_count: int, command_digest: str, readback_digest: str) -> str:
    if (
        type(backend) is not str
        or type(authority_ref) is not str
        or type(plan_digest) is not str
        or type(base) is not StoreSnapshot
        or type(committed) is not StoreSnapshot
        or type(command_count) is not int
        or command_count < 1
        or type(command_digest) is not str
        or type(readback_digest) is not str
    ):
        _ulo02_fail()
    data = {
        "backend_kind": backend,
        "authority_ref": authority_ref,
        "plan_digest": plan_digest,
        "base_snapshot": _ulo02_snapshot_payload(base),
        "committed_snapshot": _ulo02_snapshot_payload(committed),
        "command_count": command_count,
        "command_digest": command_digest,
        "readback_digest": readback_digest,
    }
    return "sha256:" + hashlib.sha256(json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _ulo02_store_capability(store: object) -> tuple[object, ...]:
    return _ulo02_capture_trusted_store(store)


@dataclass(frozen=True, slots=True, init=False)
class _TrustedTombstoneStore:
    store: object
    backend: str
    current_snapshot: object
    begin_read: object
    begin_write: object
    read_get: object
    read_require: object
    read_list: object
    read_close: object
    write_tombstone: object
    write_commit: object
    write_rollback: object
    read_type: type
    write_type: type
    binding: object
    field_signature: tuple[object, ...]
    snapshot_values: tuple[object, ...]

    def __init__(self, *args: object, **kwargs: object) -> None:
        _ulo02_fail()


def _ulo02_capture_trusted_store(store: object) -> _TrustedTombstoneStore:
    backend, current, begin_read, begin_write, binding, read_type, write_type = _ulo02_store_gate(store)
    if backend == "in_memory":
        fields = (
            id(store._lock), id(store._records), id(store._tombstones),
            id(store._forge_occurrences), store._revision, id(store._history),
        )
    else:
        fields = (store._database_path, store._busy_timeout_ms)
    values = {
        "store": store, "backend": backend, "current_snapshot": InMemoryUnifiedSourceIndexStore.current_snapshot if backend == "in_memory" else __import__("src.unified_source_index_sqlite", fromlist=["SQLiteUnifiedSourceIndexStore"]).SQLiteUnifiedSourceIndexStore.current_snapshot,
        "begin_read": begin_read, "begin_write": begin_write,
        "read_get": read_type.get, "read_require": read_type.require,
        "read_list": read_type.list_records, "read_close": read_type.close,
        "write_tombstone": write_type.tombstone, "write_commit": write_type.commit,
        "write_rollback": write_type.rollback, "read_type": read_type,
        "write_type": write_type, "binding": binding, "field_signature": fields,
        "snapshot_values": _ulo02_snapshot_values(current),
    }
    return _ulo02_internal(_TrustedTombstoneStore, values)


def _ulo02_capability_matches(capability: object, *, expected_snapshot: StoreSnapshot | None = None) -> bool:
    if type(capability) is not _TrustedTombstoneStore:
        return False
    try:
        store = object.__getattribute__(capability, "store")
        backend = object.__getattribute__(capability, "backend")
        if backend == "in_memory" and type(store) is InMemoryUnifiedSourceIndexStore:
            if set(vars(store)) != {"_lock", "_records", "_tombstones", "_forge_occurrences", "_revision", "_history"}:
                return False
            if (type(store._lock) is not _ULO02_RLOCK_TYPE or type(store._records) is not dict
                    or type(store._tombstones) is not dict or type(store._forge_occurrences) is not dict
                    or type(store._revision) is not int or type(store._history) is not dict):
                return False
            fields = (id(store._lock), id(store._records), id(store._tombstones), id(store._forge_occurrences), store._revision, id(store._history))
            methods = (
                InMemoryUnifiedSourceIndexStore.current_snapshot,
                InMemoryUnifiedSourceIndexStore.begin_read,
                InMemoryUnifiedSourceIndexStore.begin_write,
                InMemoryStoreReadTransaction.get, InMemoryStoreReadTransaction.require,
                InMemoryStoreReadTransaction.list_records, InMemoryStoreReadTransaction.close,
                InMemoryStoreWriteTransaction.tombstone, InMemoryStoreWriteTransaction.commit,
                InMemoryStoreWriteTransaction.rollback,
            )
        else:
            from src.unified_source_index_sqlite import (
                SQLiteStoreReadTransaction, SQLiteStoreWriteTransaction,
                SQLiteUnifiedSourceIndexStore,
            )
            if backend != "sqlite" or type(store) is not SQLiteUnifiedSourceIndexStore:
                return False
            if set(vars(store)) != {"_database_path", "_busy_timeout_ms"} or type(store._database_path) is not str or not store._database_path or type(store._busy_timeout_ms) is not int:
                return False
            fields = (store._database_path, store._busy_timeout_ms)
            methods = (
                SQLiteUnifiedSourceIndexStore.current_snapshot,
                SQLiteUnifiedSourceIndexStore.begin_read,
                SQLiteUnifiedSourceIndexStore.begin_write,
                SQLiteStoreReadTransaction.get, SQLiteStoreReadTransaction.require,
                SQLiteStoreReadTransaction.list_records, SQLiteStoreReadTransaction.close,
                SQLiteStoreWriteTransaction.tombstone, SQLiteStoreWriteTransaction.commit,
                SQLiteStoreWriteTransaction.rollback,
            )
        captured = (
            capability.current_snapshot, capability.begin_read, capability.begin_write,
            capability.read_get, capability.read_require, capability.read_list,
            capability.read_close, capability.write_tombstone, capability.write_commit,
            capability.write_rollback,
        )
        expected_fields = capability.field_signature
        if backend == "in_memory":
            fields_match = fields[0] == expected_fields[0]
            if expected_snapshot is None:
                fields_match = fields_match and fields[1:4] == expected_fields[1:4] and fields[4] == expected_fields[4] and fields[5] == expected_fields[5]
            else:
                fields_match = fields_match and fields[4] == expected_fields[4] + 1
        else:
            fields_match = fields == expected_fields
        if not fields_match or any(a is not b for a, b in zip(methods, captured, strict=True)):
            return False
        current = capability.current_snapshot(store)
        values = _ulo02_snapshot_values(current)
        prior = capability.snapshot_values
        if expected_snapshot is None and values == prior:
            return True
        if expected_snapshot is None:
            return False
        expected = _ulo02_snapshot_values(expected_snapshot)
        return (
            values == expected
            and expected[0] == prior[0] + 1
            and type(expected_snapshot) is StoreSnapshot
        )
    except Exception:
        return False


def _ulo02_make_receipt(backend: str, authority_ref: str, plan_digest: str, base: StoreSnapshot, committed: StoreSnapshot, commands: tuple[object, ...], readback_digest: str, binding: object, trusted_store: object, *, command_values: tuple[tuple[RecordKind, str, int, str, str], ...]) -> TombstoneCommitReceipt:
    command_digest = _ulo02_command_digest(command_values)
    receipt_digest = _ulo02_receipt_digest(backend, authority_ref, plan_digest, base, committed, len(commands), command_digest, readback_digest)
    return _ulo02_internal(TombstoneCommitReceipt, {"backend_kind": backend, "authority_ref": authority_ref, "plan_digest": plan_digest, "base_snapshot": base, "committed_snapshot": committed, "command_count": len(commands), "command_digest": command_digest, "readback_digest": readback_digest, "receipt_digest": receipt_digest, "commands": commands, "_store_binding": binding, "_trusted_store": trusted_store})


def commit_tombstone_batch(store: object, *, authority_ref: str, plan_digest: str, base_snapshot: StoreSnapshot, commands: tuple[object, ...], _trusted_capability: object | None = None) -> TombstoneCommitReceipt:
    if type(authority_ref) is not str or _ULO02_AUTHORITY_RE.fullmatch(authority_ref) is None or type(plan_digest) is not str or _ULO02_DIGEST_RE.fullmatch(plan_digest) is None or type(commands) is not tuple or not commands:
        _ulo02_fail()
    base_values = _ulo02_snapshot_values(base_snapshot)
    base_snapshot = StoreSnapshot(*base_values)
    normalized: list[tuple[RecordKind, str, int, str, str]] = []
    seen: set[tuple[RecordKind, str]] = set()
    for command in commands:
        values = _ulo02_command_values(command)
        kind, record_id, revision, reason, hinted_owner = values
        key = (kind, record_id)
        if key in seen:
            _ulo02_fail()
        seen.add(key)
        normalized.append(values)
    capability = _trusted_capability if _trusted_capability is not None else _ulo02_capture_trusted_store(store)
    if type(capability) is not _TrustedTombstoneStore or capability.store is not store:
        _ulo02_fail()
    if not _ulo02_capability_matches(capability):
        _ulo02_fail()
    backend = capability.backend
    binding = capability.binding
    read_type = capability.read_type
    write_type = capability.write_type
    begin_read = capability.begin_read
    begin_write = capability.begin_write
    current = capability.current_snapshot(capability.store)
    if _ulo02_snapshot_values(current) != base_values:
        _ulo02_fail()
    for index, (kind, record_id, revision, reason, hinted_owner) in enumerate(tuple(normalized)):
        owner = _ulo02_owner_for_command(store, kind, record_id, hinted_owner, backend)
        normalized[index] = (kind, record_id, revision, reason, owner)
    if len(normalized) != len(commands):
        _ulo02_fail()
    marker_reasons = [_ulo02_reason(authority_ref, plan_digest, kind, record_id, owner, revision, reason) for kind, record_id, revision, reason, owner in normalized]
    transaction = None
    committed_write = False
    try:
        transaction = capability.begin_write(capability.store, base_snapshot)
        _ulo02_exact_transaction(transaction, write_type, write=True)
        for (kind, record_id, revision, reason, owner), marker_reason in zip(normalized, marker_reasons, strict=True):
            if not _ulo02_capability_matches(capability):
                _ulo02_fail()
            capability.write_tombstone(transaction, kind, record_id, owner_scope=owner, expected_record_revision=revision, reason=marker_reason)
        if not _ulo02_capability_matches(capability):
            _ulo02_fail()
        committed = capability.write_commit(transaction)
        committed_write = True
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        _ulo02_fail()
    finally:
        if transaction is not None and not committed_write:
            try:
                capability.write_rollback(transaction)
            except Exception:
                _ulo02_fail()
    if type(committed) is not StoreSnapshot or committed.revision != base_snapshot.revision + 1:
        _ulo02_fail()
    base_items = _ulo02_read_records(store, base_snapshot, capability=capability, begin_read=begin_read, read_type=read_type, commands=tuple(normalized), expected_live_snapshot=committed)
    for item, (_kind, _record_id, revision, _reason, owner) in zip(base_items, normalized, strict=True):
        if type(item) is not StoredRecord or item.owner_scope != owner or item.revision != revision:
            _ulo02_fail()
    if (
        type(committed) is not StoreSnapshot
        or _ulo02_snapshot_values(committed)[0] != _ulo02_snapshot_values(base_snapshot)[0] + 1
    ):
        _ulo02_fail()
    if not _ulo02_capability_matches(capability, expected_snapshot=committed):
        _ulo02_fail()
    committed_items = _ulo02_read_records(store, committed, capability=capability, begin_read=begin_read, read_type=read_type, commands=tuple(normalized), expected_live_snapshot=committed)
    readback = []
    for item, (kind, record_id, revision, reason, owner), marker_reason in zip(committed_items, normalized, marker_reasons, strict=True):
        if type(item) is not TombstoneRecord or item.owner_scope != owner or item.previous_revision != revision or item.revision != committed.revision or item.reason != marker_reason:
            _ulo02_fail()
        readback.append({"record_kind": kind.value, "record_id": record_id, "owner_scope": owner, "previous_revision": revision, "revision": item.revision, "reason": marker_reason})
    readback_digest = "sha256:" + hashlib.sha256(json.dumps({"base": _ulo02_snapshot_payload(base_snapshot), "committed": _ulo02_snapshot_payload(committed), "items": readback}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    return _ulo02_make_receipt(backend, authority_ref, plan_digest, base_snapshot, committed, commands, readback_digest, binding, capability, command_values=tuple(normalized))


def _ulo02_receipt_from_json(value: str | bytes, expected_command_values: tuple[tuple[RecordKind, str, int, str, str], ...]) -> TombstoneCommitReceipt:
    try:
        data = json.loads(value, object_pairs_hook=_unique_json_object)
        if type(data) is not dict or data.get("schema") != f"{USI_STORE_SCHEMA}.tombstone_commit_receipt.v1":
            _ulo02_fail()
        base = StoreSnapshot(**data["base_snapshot"])
        committed = StoreSnapshot(**data["committed_snapshot"])
        return _ulo02_internal(TombstoneCommitReceipt, {"backend_kind": data["backend_kind"], "authority_ref": data["authority_ref"], "plan_digest": data["plan_digest"], "base_snapshot": base, "committed_snapshot": committed, "command_count": data["command_count"], "command_digest": data["command_digest"], "readback_digest": data["readback_digest"], "receipt_digest": data["receipt_digest"], "commands": expected_command_values, "_store_binding": None, "_trusted_store": None})
    except UnifiedSourceIndexStoreError:
        raise
    except Exception:
        _ulo02_fail()


def verify_tombstone_commit(store: object, receipt_or_canonical_bytes: TombstoneCommitReceipt | str | bytes, *, expected_authority_ref: str, expected_plan_digest: str, expected_commands: tuple[object, ...], _trusted_capability: object | None = None, _allow_own_commit_revision: bool = False) -> VerifiedTombstoneCommit:
    if (type(expected_commands) is not tuple or type(expected_authority_ref) is not str
            or _ULO02_AUTHORITY_RE.fullmatch(expected_authority_ref) is None
            or type(expected_plan_digest) is not str or _ULO02_DIGEST_RE.fullmatch(expected_plan_digest) is None):
        _ulo02_fail()
    command_values = tuple(_ulo02_command_values(item) for item in expected_commands)
    receipt = receipt_or_canonical_bytes if type(receipt_or_canonical_bytes) is TombstoneCommitReceipt else _ulo02_receipt_from_json(receipt_or_canonical_bytes, command_values)
    if type(receipt) is not TombstoneCommitReceipt:
        _ulo02_fail()
    try:
        receipt_backend = object.__getattribute__(receipt, "backend_kind")
        receipt_authority = object.__getattribute__(receipt, "authority_ref")
        receipt_plan = object.__getattribute__(receipt, "plan_digest")
        receipt_commands = object.__getattribute__(receipt, "commands")
        receipt_count = object.__getattribute__(receipt, "command_count")
        receipt_command_digest = object.__getattribute__(receipt, "command_digest")
        receipt_base = object.__getattribute__(receipt, "base_snapshot")
        receipt_committed = object.__getattribute__(receipt, "committed_snapshot")
        receipt_digest = object.__getattribute__(receipt, "receipt_digest")
        receipt_readback_digest = object.__getattribute__(receipt, "readback_digest")
        receipt_binding = object.__getattribute__(receipt, "_store_binding")
    except Exception:
        _ulo02_fail()
    base_values = _ulo02_snapshot_values(receipt_base)
    committed_values = _ulo02_snapshot_values(receipt_committed)
    receipt_base = StoreSnapshot(*base_values)
    receipt_committed = StoreSnapshot(*committed_values)
    if (type(receipt_backend) is not str or receipt_backend not in {"in_memory", "sqlite"}
            or type(receipt_authority) is not str or type(receipt_plan) is not str
            or type(receipt_count) is not int or type(receipt_command_digest) is not str
            or type(receipt_digest) is not str or type(receipt_readback_digest) is not str
            or type(receipt_commands) is not tuple):
        _ulo02_fail()
    stored_command_values = tuple(_ulo02_command_projection(item) for item in receipt_commands)
    if (stored_command_values != command_values or receipt_backend not in {"in_memory", "sqlite"}
            or receipt_authority != expected_authority_ref or receipt_plan != expected_plan_digest
            or receipt_count != len(command_values) or receipt_command_digest != _ulo02_command_digest(command_values)):
        _ulo02_fail()
    using_issued_capability = False
    if _trusted_capability is not None:
        capability = _trusted_capability
    elif type(receipt_or_canonical_bytes) is TombstoneCommitReceipt:
        try:
            issued_capability = object.__getattribute__(receipt, "_trusted_store")
        except Exception:
            issued_capability = None
        using_issued_capability = type(issued_capability) is _TrustedTombstoneStore
        capability = issued_capability if using_issued_capability else _ulo02_capture_trusted_store(store)
    else:
        capability = _ulo02_capture_trusted_store(store)
    if type(capability) is not _TrustedTombstoneStore or capability.store is not store or capability.backend != receipt_backend:
        _ulo02_fail()
    if _allow_own_commit_revision or using_issued_capability:
        if not _ulo02_capability_matches(capability, expected_snapshot=receipt_committed):
            _ulo02_fail()
    elif not _ulo02_capability_matches(capability):
        _ulo02_fail()
    backend = capability.backend
    binding = capability.binding
    read_type = capability.read_type
    begin_read = capability.begin_read
    verification_snapshot = receipt_committed if (_allow_own_commit_revision or using_issued_capability) else None
    current = capability.current_snapshot(store)
    if backend == "in_memory" and receipt_binding != binding:
        _ulo02_fail()
    if (
        receipt_count != len(expected_commands)
        or receipt_digest != _ulo02_receipt_digest(
            receipt_backend, receipt_authority, receipt_plan,
            receipt_base, receipt_committed, receipt_count,
            receipt_command_digest, receipt_readback_digest,
        )
    ):
        _ulo02_fail()
    if _ulo02_snapshot_values(current) != committed_values:
        _ulo02_fail()
    # Re-read both retained snapshots without issuing a second write.  The
    # digest must match the exact marker order and reason commitments.
    normalized = []
    base_read = None
    try:
        if not _ulo02_capability_matches(capability, expected_snapshot=verification_snapshot):
            _ulo02_fail()
        base_read = begin_read(store, receipt_base)
        _ulo02_exact_transaction(base_read, read_type, write=False)
        historical_state = read_type._capture_operation_state(base_read) if backend == "in_memory" else None
        for kind, record_id, revision, reason, hinted_owner in command_values:
            owner = _ulo02_owner_for_command(store, kind, record_id, hinted_owner, backend, historical_state)
            item = capability.read_get(base_read, kind, record_id, owner_scope=owner, include_tombstone=True)
            if type(item) is not StoredRecord or item.revision != revision or item.owner_scope != owner:
                _ulo02_fail()
            normalized.append((kind, record_id, revision, reason, owner))
    finally:
        if base_read is not None:
            try:
                capability.read_close(base_read)
            except Exception:
                _ulo02_fail()
    marker_reasons = [_ulo02_reason(expected_authority_ref, expected_plan_digest, kind, record_id, owner, revision, reason) for kind, record_id, revision, reason, owner in normalized]
    readback = []
    committed_read = None
    try:
        if not _ulo02_capability_matches(capability, expected_snapshot=verification_snapshot):
            _ulo02_fail()
        committed_read = begin_read(store, receipt_committed)
        _ulo02_exact_transaction(committed_read, read_type, write=False)
        for (kind, record_id, revision, reason, owner), marker_reason in zip(normalized, marker_reasons, strict=True):
            item = capability.read_get(committed_read, kind, record_id, owner_scope=owner, include_tombstone=True)
            if type(item) is not TombstoneRecord or item.previous_revision != revision or item.revision != committed_values[0] or item.owner_scope != owner or item.reason != marker_reason:
                _ulo02_fail()
            readback.append({"record_kind": kind.value, "record_id": record_id, "owner_scope": owner, "previous_revision": revision, "revision": item.revision, "reason": marker_reason})
    finally:
        if committed_read is not None:
            try:
                capability.read_close(committed_read)
            except Exception:
                _ulo02_fail()
    digest = "sha256:" + hashlib.sha256(json.dumps({"base": _ulo02_snapshot_payload(receipt_base), "committed": _ulo02_snapshot_payload(receipt_committed), "items": readback}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    if digest != receipt_readback_digest:
        _ulo02_fail()
    return _ulo02_internal(VerifiedTombstoneCommit, {"receipt": receipt, "_store_binding": binding})


__all__ += ["TombstoneCommitReceipt", "VerifiedTombstoneCommit", "commit_tombstone_batch", "verify_tombstone_commit"]

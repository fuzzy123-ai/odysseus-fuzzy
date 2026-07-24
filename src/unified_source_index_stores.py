"""Backend-neutral transactional store protocols for the Unified Source Index.

USI-02 deliberately contains no SQLite, Chroma, filesystem, or network code.
The in-memory implementation is an executable contract fake: it provides
snapshot-isolated reads, explicit optimistic writes, bounded cursor paging,
and tombstones that reserve deleted identities until an explicit restore.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import base64
import binascii
import hashlib
import json
import re
from threading import RLock
from typing import Generic, Mapping, Protocol, TypeAlias, TypeVar, cast, runtime_checkable

from src.unified_source_index_contract import (
    ChunkRecord,
    DerivedRunRecord,
    EntityRecord,
    IndexJobRecord,
    LineageRecord,
    ProjectionManifest,
    RecordKind,
    RecordRef,
    RelationRecord,
    SourceRecord,
    SourceVersionRecord,
    canonical_json,
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
        if not isinstance(self.snapshot, StoreSnapshot):
            raise UnifiedSourceIndexStoreError("page snapshot must be typed")
        if not isinstance(self.items, tuple) or len(self.items) > MAX_PAGE_SIZE:
            raise UnifiedSourceIndexStoreError("page items must be a bounded tuple")
        if not all(isinstance(item, (StoredRecord, TombstoneRecord)) for item in self.items):
            raise UnifiedSourceIndexStoreError("page contains an unsupported item")
        if not isinstance(self.next_cursor, str) or len(self.next_cursor) > MAX_CURSOR_CHARS:
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
    records: Mapping[StoreKey, StoredRecord[StoreRecord]]
    tombstones: Mapping[StoreKey, TombstoneRecord]


@dataclass(frozen=True, slots=True)
class _PutMutation:
    record: StoreRecord
    expected_revision: int
    restore: bool = False


@dataclass(frozen=True, slots=True)
class _TombstoneMutation:
    record_kind: RecordKind
    record_id: str
    owner_scope: str
    expected_revision: int
    reason: str


Mutation: TypeAlias = _PutMutation | _TombstoneMutation


class InMemoryStoreReadTransaction:
    """Immutable read view over exactly one committed snapshot."""

    def __init__(self, state: _SnapshotState) -> None:
        self._state = state
        self._closed = False

    @property
    def snapshot(self) -> StoreSnapshot:
        return self._state.snapshot

    @property
    def closed(self) -> bool:
        return self._closed

    def get(
        self,
        record_kind: RecordKind | str,
        record_id: str,
        *,
        owner_scope: str,
        include_tombstone: bool = False,
    ) -> StorePageItem | None:
        self._ensure_open()
        kind = _record_kind(record_kind)
        key = (kind, _record_id(kind, record_id))
        owner = _owner_scope(owner_scope)
        stored = self._state.records.get(key)
        if stored is not None:
            return stored if stored.owner_scope == owner else None
        if include_tombstone:
            tombstone = self._state.tombstones.get(key)
            if tombstone is not None and tombstone.owner_scope == owner:
                return tombstone
        return None

    def require(
        self,
        record_kind: RecordKind | str,
        record_id: str,
        *,
        owner_scope: str,
        include_tombstone: bool = False,
    ) -> StorePageItem:
        item = self.get(
            record_kind,
            record_id,
            owner_scope=owner_scope,
            include_tombstone=include_tombstone,
        )
        if item is None:
            raise StoreNotFoundError("owner-scoped USI record was not found")
        return item

    def list_records(
        self,
        record_kind: RecordKind | str,
        *,
        owner_scope: str,
        limit: int,
        cursor: str = "",
        include_tombstones: bool = False,
    ) -> StorePage:
        self._ensure_open()
        kind = _record_kind(record_kind)
        owner = _owner_scope(owner_scope)
        page_limit = _page_limit(limit)
        after_id = ""
        if cursor:
            after_id = _decode_cursor(
                cursor,
                snapshot=self.snapshot,
                record_kind=kind,
                owner_scope=owner,
                include_tombstones=include_tombstones,
            )

        items: list[StorePageItem] = [
            item
            for (item_kind, _), item in self._state.records.items()
            if item_kind is kind and item.owner_scope == owner
        ]
        if include_tombstones:
            items.extend(
                item
                for (item_kind, _), item in self._state.tombstones.items()
                if item_kind is kind and item.owner_scope == owner
            )
        items.sort(key=_page_item_id)
        if after_id:
            items = [item for item in items if _page_item_id(item) > after_id]

        selected = tuple(items[:page_limit])
        has_more = len(items) > page_limit
        next_cursor = ""
        if has_more and selected:
            next_cursor = _encode_cursor(
                snapshot=self.snapshot,
                record_kind=kind,
                owner_scope=owner,
                after_record_id=_page_item_id(selected[-1]),
                include_tombstones=include_tombstones,
            )
        return StorePage(selected, self.snapshot, next_cursor)

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> "InMemoryStoreReadTransaction":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreTransactionClosedError("read transaction is closed")


class InMemoryStoreWriteTransaction:
    """Explicit optimistic transaction; context exit rolls back by default."""

    def __init__(
        self,
        store: "InMemoryUnifiedSourceIndexStore",
        state: _SnapshotState,
    ) -> None:
        self._store = store
        self._state = state
        self._mutations: dict[StoreKey, Mutation] = {}
        self._closed = False

    @property
    def base_snapshot(self) -> StoreSnapshot:
        return self._state.snapshot

    @property
    def closed(self) -> bool:
        return self._closed

    def put(
        self,
        record: StoreRecord,
        *,
        expected_record_revision: int | None = None,
    ) -> None:
        self._ensure_open()
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
        self._mutations[key] = _PutMutation(record, expected)

    def compare_and_swap(
        self,
        record: StoreRecord,
        *,
        expected_record_revision: int,
    ) -> None:
        self.put(record, expected_record_revision=expected_record_revision)

    def tombstone(
        self,
        record_kind: RecordKind | str,
        record_id: str,
        *,
        owner_scope: str,
        expected_record_revision: int,
        reason: str,
    ) -> None:
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
        self._mutations[key] = _TombstoneMutation(
            kind,
            key[1],
            owner,
            expected,
            _reason(reason),
        )

    def restore(
        self,
        record: StoreRecord,
        *,
        expected_tombstone_revision: int,
    ) -> None:
        self._ensure_open()
        kind, record_id, owner = _record_descriptor(record)
        key = (kind, record_id)
        self._ensure_not_staged(key)
        tombstone = self._state.tombstones.get(key)
        if tombstone is None or tombstone.owner_scope != owner:
            raise StoreNotFoundError("matching owner-scoped tombstone was not found")
        expected = _positive_revision(expected_tombstone_revision)
        _require_revision_match(expected, tombstone.revision, "tombstone revision is stale")
        self._mutations[key] = _PutMutation(record, expected, restore=True)

    def commit(self) -> StoreSnapshot:
        self._ensure_open()
        if not self._mutations:
            raise UnifiedSourceIndexStoreError("empty write transaction cannot commit")
        try:
            snapshot = self._store._commit(self)
        except StoreConflictError:
            self._closed = True
            raise
        self._closed = True
        return snapshot

    def rollback(self) -> None:
        self._mutations.clear()
        self._closed = True

    def __enter__(self) -> "InMemoryStoreWriteTransaction":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._closed:
            self.rollback()

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreTransactionClosedError("write transaction is closed")

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
        self._records: dict[StoreKey, StoredRecord[StoreRecord]] = {}
        self._tombstones: dict[StoreKey, TombstoneRecord] = {}
        self._revision = 0
        initial = self._make_state(self._records, self._tombstones, self._revision)
        self._history: dict[int, _SnapshotState] = {0: initial}

    @property
    def store_interfaces(self) -> frozenset[StoreInterfaceKind]:
        return self._INTERFACES

    def current_snapshot(self) -> StoreSnapshot:
        with self._lock:
            return self._history[self._revision].snapshot

    def begin_read(
        self,
        snapshot: StoreSnapshot | None = None,
    ) -> InMemoryStoreReadTransaction:
        with self._lock:
            requested = self.current_snapshot() if snapshot is None else snapshot
            if not isinstance(requested, StoreSnapshot):
                raise UnifiedSourceIndexStoreError("snapshot must be typed")
            state = self._history.get(requested.revision)
            if state is None or state.snapshot != requested:
                raise StoreConflictError(
                    "snapshot is unknown or does not match retained state",
                    expected_revision=requested.revision,
                    actual_revision=self._revision,
                )
            return InMemoryStoreReadTransaction(state)

    def begin_write(
        self,
        expected: StoreSnapshot | int,
    ) -> InMemoryStoreWriteTransaction:
        with self._lock:
            if isinstance(expected, StoreSnapshot):
                expected_revision = expected.revision
                expected_snapshot = expected
            else:
                expected_revision = _revision(expected, "expected")
                expected_snapshot = None
            current = self._history[self._revision]
            if expected_revision != self._revision or (
                expected_snapshot is not None and expected_snapshot != current.snapshot
            ):
                raise StoreConflictError(
                    "write snapshot is stale",
                    expected_revision=expected_revision,
                    actual_revision=self._revision,
                )
            return InMemoryStoreWriteTransaction(self, current)

    def _commit(self, transaction: InMemoryStoreWriteTransaction) -> StoreSnapshot:
        with self._lock:
            expected_revision = transaction.base_snapshot.revision
            if expected_revision != self._revision:
                raise StoreConflictError(
                    "write snapshot became stale before commit",
                    expected_revision=expected_revision,
                    actual_revision=self._revision,
                )

            records = dict(self._records)
            tombstones = dict(self._tombstones)
            next_revision = self._revision + 1

            # Validate every CAS before applying any mutation.
            for key, mutation in transaction._mutations.items():
                actual = _key_revision(records, tombstones, key)
                _require_revision_match(
                    mutation.expected_revision,
                    actual,
                    "record changed before transaction commit",
                )
                if isinstance(mutation, _PutMutation):
                    if mutation.restore and key not in tombstones:
                        raise StoreTombstoneError("restore target is no longer tombstoned")
                    if not mutation.restore and key in tombstones:
                        raise StoreTombstoneError("record identity is tombstoned")
                elif key not in records:
                    raise StoreNotFoundError("tombstone target no longer exists")

            for key, mutation in transaction._mutations.items():
                if isinstance(mutation, _PutMutation):
                    tombstones.pop(key, None)
                    records[key] = StoredRecord(mutation.record, next_revision)
                    continue
                previous = records.pop(key)
                tombstones[key] = TombstoneRecord(
                    mutation.record_kind,
                    mutation.record_id,
                    mutation.owner_scope,
                    previous.revision,
                    next_revision,
                    mutation.reason,
                )

            self._records = records
            self._tombstones = tombstones
            self._revision = next_revision
            state = self._make_state(records, tombstones, next_revision)
            self._history[next_revision] = state
            return state.snapshot

    @staticmethod
    def _make_state(
        records: Mapping[StoreKey, StoredRecord[StoreRecord]],
        tombstones: Mapping[StoreKey, TombstoneRecord],
        revision: int,
    ) -> _SnapshotState:
        content_free_state = [
            {
                "record_kind": key[0].value,
                "record_id": key[1],
                "revision": item.revision,
                "record_hash": hashlib.sha256(
                    item.record.to_json().encode("utf-8")
                ).hexdigest(),
                "state": "record",
            }
            for key, item in records.items()
        ] + [
            {
                "record_kind": key[0].value,
                "record_id": key[1],
                "revision": item.revision,
                "state": "tombstone",
            }
            for key, item in tombstones.items()
        ]
        content_free_state.sort(
            key=lambda item: (cast(str, item["record_kind"]), cast(str, item["record_id"]))
        )
        state_hash = "sha256:" + hashlib.sha256(
            canonical_json(content_free_state).encode("utf-8")
        ).hexdigest()
        snapshot = StoreSnapshot(
            revision,
            state_hash,
            len(records),
            len(tombstones),
        )
        return _SnapshotState(snapshot, dict(records), dict(tombstones))


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


def _record_kind(value: RecordKind | str) -> RecordKind:
    if isinstance(value, RecordKind):
        return value
    if not isinstance(value, str):
        raise UnifiedSourceIndexStoreError("record_kind must be a RecordKind")
    try:
        return RecordKind(value)
    except ValueError as exc:
        raise UnifiedSourceIndexStoreError(f"invalid record_kind: {value!r}") from exc


def _record_id(kind: RecordKind, value: str) -> str:
    try:
        return RecordRef(kind, value).record_id
    except (TypeError, ValueError) as exc:
        raise UnifiedSourceIndexStoreError("record_id does not match record_kind") from exc


def _owner_scope(value: str) -> str:
    if not isinstance(value, str) or not _OWNER_SCOPE_RE.fullmatch(value):
        raise UnifiedSourceIndexStoreError("owner_scope is invalid or unbounded")
    return value


def _reason(value: str) -> str:
    if not isinstance(value, str) or not _REASON_RE.fullmatch(value):
        raise UnifiedSourceIndexStoreError(
            "tombstone reason must be a bounded content-free token"
        )
    return value


def _revision(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
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
    if not isinstance(value, str):
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
    records: Mapping[StoreKey, StoredRecord[StoreRecord]],
    tombstones: Mapping[StoreKey, TombstoneRecord],
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
    if not isinstance(value, str) or not value or len(value) > MAX_CURSOR_CHARS:
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
    except (
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise StoreCursorError("cursor is not valid canonical data") from exc
    if not isinstance(payload, dict):
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
    if not isinstance(checksum, str) or checksum != expected_checksum:
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
    if not isinstance(after_record_id, str):
        raise StoreCursorError("cursor record id is invalid")
    return _record_id(record_kind, after_record_id)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
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

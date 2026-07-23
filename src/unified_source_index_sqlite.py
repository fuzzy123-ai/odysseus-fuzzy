"""Transactional SQLite backend for the Unified Source Index.

The backend owns one explicit, filesystem-backed SQLite database.  It keeps
current typed rows for integrity and search, plus an append-only record history
for snapshot-stable reads.  It does not participate in Odysseus' application
database or global migration chain.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
from typing import Final, cast

from src.unified_source_index_contract import (
    ChunkRecord,
    DerivedRunRecord,
    EntityRecord,
    IndexJobRecord,
    LineageRecord,
    ProjectionManifest,
    RecordKind,
    RelationRecord,
    SourceRecord,
    SourceVersionRecord,
    canonical_json,
    record_from_json,
)
from src.unified_source_index_migrations import (
    DEFAULT_BUSY_TIMEOUT_MS,
    SQLitePragmas,
    configure_sqlite_connection,
    read_sqlite_pragmas,
    rebuild_chunk_fts,
    upgrade_schema,
)
from src.unified_source_index_stores import (
    StoreConflictError,
    StoreInterfaceKind,
    StoreNotFoundError,
    StorePage,
    StorePageItem,
    StoreRecord,
    StoreSnapshot,
    StoreTombstoneError,
    StoreTransactionClosedError,
    StoredRecord,
    TombstoneRecord,
    UnifiedSourceIndexStoreError,
    _decode_cursor,
    _encode_cursor,
    _owner_scope,
    _page_item_id,
    _page_limit,
    _positive_revision,
    _reason,
    _record_descriptor,
    _record_id,
    _record_kind,
    _require_revision_match,
    _revision,
)


MAX_FTS_QUERY_CHARS: Final = 1_024
MAX_FTS_RESULTS: Final = 1_000


class SQLiteUnifiedSourceIndexError(UnifiedSourceIndexStoreError):
    """Raised when SQLite cannot preserve the USI store contract."""


@dataclass(frozen=True, slots=True)
class ChunkSearchHit:
    """One owner-scoped current-state FTS result (lower rank is better)."""

    item: StoredRecord[ChunkRecord]
    rank: float


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


_Mutation = _PutMutation | _TombstoneMutation


@dataclass(frozen=True, slots=True)
class _TableSpec:
    table: str
    id_column: str
    rank: int


_TABLES: dict[RecordKind, _TableSpec] = {
    RecordKind.SOURCE: _TableSpec("usi_sources", "source_id", 0),
    RecordKind.SOURCE_VERSION: _TableSpec(
        "usi_source_versions", "source_version_id", 1
    ),
    RecordKind.CHUNK: _TableSpec("usi_chunks", "chunk_id", 2),
    RecordKind.ENTITY: _TableSpec("usi_entities", "entity_id", 2),
    RecordKind.RELATION: _TableSpec("usi_relations", "relation_id", 3),
    RecordKind.LINEAGE: _TableSpec("usi_lineage", "lineage_id", 3),
    RecordKind.PROJECTION: _TableSpec(
        "usi_projection_manifests", "projection_id", 3
    ),
    RecordKind.DERIVED_RUN: _TableSpec("usi_derived_runs", "derived_run_id", 3),
    RecordKind.JOB: _TableSpec("usi_jobs", "job_id", 3),
}


class SQLiteStoreReadTransaction:
    """Read transaction fixed to one retained USI snapshot."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        snapshot: StoreSnapshot,
    ) -> None:
        self._connection = connection
        self._snapshot = snapshot
        self._closed = False

    @property
    def snapshot(self) -> StoreSnapshot:
        return self._snapshot

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
        identity = _record_id(kind, record_id)
        owner = _owner_scope(owner_scope)
        row = self._connection.execute(
            """
            SELECT record_kind, record_id, owner_scope, revision, state,
                   record_json, previous_revision, reason
            FROM usi_record_history
            WHERE record_kind = ? AND record_id = ? AND owner_scope = ?
              AND revision <= ?
            ORDER BY revision DESC
            LIMIT 1
            """,
            (kind.value, identity, owner, self.snapshot.revision),
        ).fetchone()
        if row is None:
            return None
        item = _history_item(row)
        if isinstance(item, TombstoneRecord) and not include_tombstone:
            return None
        return item

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
        state_clause = "" if include_tombstones else "AND h.state = 'record'"
        rows = self._connection.execute(
            f"""
            WITH latest AS (
                SELECT record_id, MAX(revision) AS revision
                FROM usi_record_history
                WHERE record_kind = ? AND owner_scope = ?
                  AND revision <= ? AND record_id > ?
                GROUP BY record_id
            )
            SELECT h.record_kind, h.record_id, h.owner_scope, h.revision,
                   h.state, h.record_json, h.previous_revision, h.reason
            FROM usi_record_history AS h
            JOIN latest
              ON latest.record_id = h.record_id
             AND latest.revision = h.revision
            WHERE h.record_kind = ? AND h.owner_scope = ?
              {state_clause}
            ORDER BY h.record_id
            LIMIT ?
            """,
            (
                kind.value,
                owner,
                self.snapshot.revision,
                after_id,
                kind.value,
                owner,
                page_limit + 1,
            ),
        ).fetchall()
        items = tuple(_history_item(row) for row in rows[:page_limit])
        next_cursor = ""
        if len(rows) > page_limit and items:
            next_cursor = _encode_cursor(
                snapshot=self.snapshot,
                record_kind=kind,
                owner_scope=owner,
                after_record_id=_page_item_id(items[-1]),
                include_tombstones=include_tombstones,
            )
        return StorePage(items, self.snapshot, next_cursor)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._connection.rollback()
        finally:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "SQLiteStoreReadTransaction":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreTransactionClosedError("read transaction is closed")


class SQLiteStoreWriteTransaction:
    """Explicit SQLite write transaction with optimistic record revisions."""

    def __init__(
        self,
        store: "SQLiteUnifiedSourceIndexStore",
        connection: sqlite3.Connection,
        base_snapshot: StoreSnapshot,
    ) -> None:
        self._store = store
        self._connection = connection
        self._base_snapshot = base_snapshot
        self._mutations: dict[tuple[RecordKind, str], _Mutation] = {}
        self._closed = False

    @property
    def base_snapshot(self) -> StoreSnapshot:
        return self._base_snapshot

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
        current = _current_item(self._connection, kind, record_id)
        if isinstance(current, TombstoneRecord):
            raise StoreTombstoneError(
                "record identity is tombstoned; use restore with its revision"
            )
        actual = current.revision if isinstance(current, StoredRecord) else 0
        expected = (
            actual
            if expected_record_revision is None
            else _revision(expected_record_revision, "expected_record_revision")
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
        identity = _record_id(kind, record_id)
        key = (kind, identity)
        self._ensure_not_staged(key)
        owner = _owner_scope(owner_scope)
        current = _current_item(self._connection, kind, identity)
        if not isinstance(current, StoredRecord) or current.owner_scope != owner:
            raise StoreNotFoundError("owner-scoped USI record was not found")
        expected = _revision(expected_record_revision, "expected_record_revision")
        _require_revision_match(expected, current.revision, "record revision is stale")
        self._mutations[key] = _TombstoneMutation(
            kind,
            identity,
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
        current = _current_item(self._connection, kind, record_id)
        if not isinstance(current, TombstoneRecord) or current.owner_scope != owner:
            raise StoreNotFoundError("matching owner-scoped tombstone was not found")
        expected = _positive_revision(expected_tombstone_revision)
        _require_revision_match(expected, current.revision, "tombstone revision is stale")
        self._mutations[key] = _PutMutation(record, expected, restore=True)

    def commit(self) -> StoreSnapshot:
        self._ensure_open()
        if not self._mutations:
            raise UnifiedSourceIndexStoreError("empty write transaction cannot commit")
        try:
            snapshot = self._store._commit(self)
        except (sqlite3.DatabaseError, UnicodeError) as exc:
            self._rollback_and_close()
            raise SQLiteUnifiedSourceIndexError(
                "SQLite rejected the atomic USI write"
            ) from exc
        except Exception:
            self._rollback_and_close()
            raise
        self._connection.close()
        self._closed = True
        return snapshot

    def rollback(self) -> None:
        if not self._closed:
            self._rollback_and_close()

    def __enter__(self) -> "SQLiteStoreWriteTransaction":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._closed:
            self.rollback()

    def _rollback_and_close(self) -> None:
        try:
            self._connection.rollback()
        finally:
            self._connection.close()
            self._mutations.clear()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreTransactionClosedError("write transaction is closed")

    def _ensure_not_staged(self, key: tuple[RecordKind, str]) -> None:
        if key in self._mutations:
            raise UnifiedSourceIndexStoreError(
                "a record may be mutated only once per transaction"
            )


class SQLiteUnifiedSourceIndexStore:
    """Filesystem-backed USI SQLite store with retained snapshot history."""

    _INTERFACES = frozenset(StoreInterfaceKind)

    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        if not isinstance(database_path, (str, Path)) or not str(database_path):
            raise SQLiteUnifiedSourceIndexError("database_path must be non-empty")
        if str(database_path) == ":memory:":
            raise SQLiteUnifiedSourceIndexError(
                "USI SQLite storage must be filesystem-backed for reopen safety"
            )
        self._database_path = str(Path(database_path))
        self._busy_timeout_ms = busy_timeout_ms
        connection = self._connect()
        try:
            upgrade_schema(connection)
        finally:
            connection.close()

    @property
    def database_path(self) -> str:
        return self._database_path

    @property
    def store_interfaces(self) -> frozenset[StoreInterfaceKind]:
        return self._INTERFACES

    def current_snapshot(self) -> StoreSnapshot:
        connection = self._connect()
        try:
            return _current_snapshot(connection)
        finally:
            connection.close()

    def read_pragmas(self) -> SQLitePragmas:
        connection = self._connect()
        try:
            return read_sqlite_pragmas(connection)
        finally:
            connection.close()

    def begin_read(
        self,
        snapshot: StoreSnapshot | None = None,
    ) -> SQLiteStoreReadTransaction:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            current = _current_snapshot(connection)
            requested = current if snapshot is None else snapshot
            if not isinstance(requested, StoreSnapshot):
                raise UnifiedSourceIndexStoreError("snapshot must be typed")
            row = connection.execute(
                """
                SELECT revision, snapshot_ref, state_hash,
                       record_count, tombstone_count
                FROM usi_snapshots WHERE revision = ?
                """,
                (requested.revision,),
            ).fetchone()
            retained = _snapshot_from_row(row) if row is not None else None
            if retained != requested:
                raise StoreConflictError(
                    "snapshot is unknown or does not match retained state",
                    expected_revision=requested.revision,
                    actual_revision=current.revision,
                )
            return SQLiteStoreReadTransaction(connection, requested)
        except Exception:
            connection.rollback()
            connection.close()
            raise

    def begin_write(
        self,
        expected: StoreSnapshot | int,
    ) -> SQLiteStoreWriteTransaction:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = _current_snapshot(connection)
            if isinstance(expected, StoreSnapshot):
                expected_revision = expected.revision
                expected_snapshot = expected
            else:
                expected_revision = _revision(expected, "expected")
                expected_snapshot = None
            if expected_revision != current.revision or (
                expected_snapshot is not None and expected_snapshot != current
            ):
                raise StoreConflictError(
                    "write snapshot is stale",
                    expected_revision=expected_revision,
                    actual_revision=current.revision,
                )
            return SQLiteStoreWriteTransaction(self, connection, current)
        except Exception:
            connection.rollback()
            connection.close()
            raise

    def search_chunks(
        self,
        *,
        owner_scope: str,
        query: str,
        limit: int = 20,
    ) -> tuple[ChunkSearchHit, ...]:
        owner = _owner_scope(owner_scope)
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_FTS_QUERY_CHARS:
            raise SQLiteUnifiedSourceIndexError("FTS query must be non-empty and bounded")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_FTS_RESULTS:
            raise SQLiteUnifiedSourceIndexError(
                f"FTS limit must be between 1 and {MAX_FTS_RESULTS}"
            )
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT c.record_json, c.record_revision,
                       bm25(usi_chunk_fts) AS rank
                FROM usi_chunk_fts
                JOIN usi_chunks AS c ON c.chunk_rowid = usi_chunk_fts.rowid
                WHERE usi_chunk_fts MATCH ? AND c.owner_scope = ?
                ORDER BY rank, c.chunk_id
                LIMIT ?
                """,
                (query, owner, limit),
            ).fetchall()
            hits: list[ChunkSearchHit] = []
            for row in rows:
                record = record_from_json(cast(str, row["record_json"]))
                if not isinstance(record, ChunkRecord):
                    raise SQLiteUnifiedSourceIndexError(
                        "FTS shadow resolved to a non-chunk record"
                    )
                hits.append(
                    ChunkSearchHit(
                        StoredRecord(record, int(row["record_revision"])),
                        float(row["rank"]),
                    )
                )
            return tuple(hits)
        except sqlite3.DatabaseError as exc:
            raise SQLiteUnifiedSourceIndexError("SQLite FTS query failed") from exc
        finally:
            connection.close()

    def rebuild_chunk_fts(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rebuild_chunk_fts(connection)
            connection.commit()
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise SQLiteUnifiedSourceIndexError("SQLite FTS rebuild failed") from exc
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=self._busy_timeout_ms / 1_000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            configure_sqlite_connection(
                connection,
                busy_timeout_ms=self._busy_timeout_ms,
            )
            return connection
        except sqlite3.DatabaseError as exc:
            if connection is not None:
                connection.close()
            raise SQLiteUnifiedSourceIndexError(
                "could not open the isolated USI SQLite database"
            ) from exc
        except Exception:
            if connection is not None:
                connection.close()
            raise

    def _commit(self, transaction: SQLiteStoreWriteTransaction) -> StoreSnapshot:
        connection = transaction._connection
        current = _current_snapshot(connection)
        _require_revision_match(
            transaction.base_snapshot.revision,
            current.revision,
            "write snapshot became stale before commit",
        )

        # Validate all record CAS values before applying any row mutation.
        for (kind, record_id), mutation in transaction._mutations.items():
            item = _current_item(connection, kind, record_id)
            actual = item.revision if item is not None else 0
            _require_revision_match(
                mutation.expected_revision,
                actual,
                "record changed before transaction commit",
            )
            if isinstance(mutation, _PutMutation):
                if mutation.restore and not isinstance(item, TombstoneRecord):
                    raise StoreTombstoneError("restore target is no longer tombstoned")
                if not mutation.restore and isinstance(item, TombstoneRecord):
                    raise StoreTombstoneError("record identity is tombstoned")
            elif not isinstance(item, StoredRecord):
                raise StoreNotFoundError("tombstone target no longer exists")

        next_revision = current.revision + 1
        deletions = [
            mutation
            for mutation in transaction._mutations.values()
            if isinstance(mutation, _TombstoneMutation)
        ]
        deletions.sort(key=lambda item: _TABLES[item.record_kind].rank, reverse=True)
        puts = [
            mutation
            for mutation in transaction._mutations.values()
            if isinstance(mutation, _PutMutation)
        ]
        puts.sort(key=lambda item: _TABLES[_record_descriptor(item.record)[0]].rank)

        for mutation in deletions:
            _apply_tombstone(connection, mutation, next_revision)
        for mutation in puts:
            _apply_put(connection, mutation, next_revision)

        snapshot = _make_current_snapshot(connection, next_revision)
        connection.execute(
            """
            UPDATE usi_store_state
            SET revision = ?, snapshot_ref = ?, state_hash = ?,
                record_count = ?, tombstone_count = ?
            WHERE singleton = 1
            """,
            (
                snapshot.revision,
                snapshot.snapshot_ref,
                snapshot.state_hash,
                snapshot.record_count,
                snapshot.tombstone_count,
            ),
        )
        connection.execute(
            """
            INSERT INTO usi_snapshots(
                revision, snapshot_ref, state_hash, record_count, tombstone_count
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                snapshot.revision,
                snapshot.snapshot_ref,
                snapshot.state_hash,
                snapshot.record_count,
                snapshot.tombstone_count,
            ),
        )
        connection.commit()
        return snapshot


def _snapshot_from_row(row: sqlite3.Row) -> StoreSnapshot:
    return StoreSnapshot(
        int(row["revision"]),
        str(row["state_hash"]),
        int(row["record_count"]),
        int(row["tombstone_count"]),
        str(row["snapshot_ref"]),
    )


def _current_snapshot(connection: sqlite3.Connection) -> StoreSnapshot:
    row = connection.execute(
        """
        SELECT revision, snapshot_ref, state_hash, record_count, tombstone_count
        FROM usi_store_state WHERE singleton = 1
        """
    ).fetchone()
    if row is None:
        raise SQLiteUnifiedSourceIndexError("USI store state is missing")
    return _snapshot_from_row(row)


def _history_item(row: sqlite3.Row) -> StorePageItem:
    kind = _record_kind(str(row["record_kind"]))
    if row["state"] == "tombstone":
        return TombstoneRecord(
            kind,
            str(row["record_id"]),
            str(row["owner_scope"]),
            int(row["previous_revision"]),
            int(row["revision"]),
            str(row["reason"]),
        )
    record = record_from_json(cast(str, row["record_json"]))
    descriptor = _record_descriptor(cast(StoreRecord, record))
    if descriptor != (kind, str(row["record_id"]), str(row["owner_scope"])):
        raise SQLiteUnifiedSourceIndexError("stored USI record identity is inconsistent")
    return StoredRecord(cast(StoreRecord, record), int(row["revision"]))


def _current_item(
    connection: sqlite3.Connection,
    kind: RecordKind,
    record_id: str,
) -> StorePageItem | None:
    tombstone = connection.execute(
        """
        SELECT record_kind, record_id, owner_scope, revision, previous_revision, reason
        FROM usi_tombstones WHERE record_kind = ? AND record_id = ?
        """,
        (kind.value, record_id),
    ).fetchone()
    if tombstone is not None:
        return TombstoneRecord(
            kind,
            record_id,
            str(tombstone["owner_scope"]),
            int(tombstone["previous_revision"]),
            int(tombstone["revision"]),
            str(tombstone["reason"]),
        )
    spec = _TABLES[kind]
    row = connection.execute(
        f"SELECT record_json, record_revision FROM {spec.table} "
        f"WHERE {spec.id_column} = ?",
        (record_id,),
    ).fetchone()
    if row is None:
        return None
    record = record_from_json(cast(str, row["record_json"]))
    return StoredRecord(cast(StoreRecord, record), int(row["record_revision"]))


def _apply_tombstone(
    connection: sqlite3.Connection,
    mutation: _TombstoneMutation,
    revision: int,
) -> None:
    spec = _TABLES[mutation.record_kind]
    cursor = connection.execute(
        f"DELETE FROM {spec.table} WHERE {spec.id_column} = ?",
        (mutation.record_id,),
    )
    if cursor.rowcount != 1:
        raise StoreNotFoundError("tombstone target no longer exists")
    connection.execute(
        """
        INSERT INTO usi_tombstones(
            record_kind, record_id, owner_scope,
            previous_revision, revision, reason
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mutation.record_kind.value,
            mutation.record_id,
            mutation.owner_scope,
            mutation.expected_revision,
            revision,
            mutation.reason,
        ),
    )
    connection.execute(
        """
        INSERT INTO usi_record_history(
            record_kind, record_id, owner_scope, revision, state,
            record_json, record_hash, previous_revision, reason
        ) VALUES (?, ?, ?, ?, 'tombstone', NULL, NULL, ?, ?)
        """,
        (
            mutation.record_kind.value,
            mutation.record_id,
            mutation.owner_scope,
            revision,
            mutation.expected_revision,
            mutation.reason,
        ),
    )


def _apply_put(
    connection: sqlite3.Connection,
    mutation: _PutMutation,
    revision: int,
) -> None:
    record = mutation.record
    kind, record_id, owner = _record_descriptor(record)
    record_json = record.to_json()
    record_hash = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
    if mutation.restore:
        cursor = connection.execute(
            "DELETE FROM usi_tombstones WHERE record_kind = ? AND record_id = ?",
            (kind.value, record_id),
        )
        if cursor.rowcount != 1:
            raise StoreTombstoneError("restore target is no longer tombstoned")
    _upsert_current_record(connection, record, record_json, record_hash, revision)
    connection.execute(
        """
        INSERT INTO usi_record_history(
            record_kind, record_id, owner_scope, revision, state,
            record_json, record_hash, previous_revision, reason
        ) VALUES (?, ?, ?, ?, 'record', ?, ?, NULL, NULL)
        """,
        (kind.value, record_id, owner, revision, record_json, record_hash),
    )


def _upsert_current_record(
    connection: sqlite3.Connection,
    record: StoreRecord,
    record_json: str,
    record_hash: str,
    revision: int,
) -> None:
    if isinstance(record, SourceRecord):
        _upsert(
            connection,
            "usi_sources",
            "source_id",
            (
                "source_id", "owner_scope", "source_kind", "canonical_ref",
                "provider_ref", "classification", "content_policy",
                "record_json", "record_hash", "record_revision",
            ),
            (
                record.source_id, record.owner_scope, record.source_kind.value,
                record.canonical_ref, record.provider_ref, record.classification.value,
                record.content_policy.value, record_json, record_hash, revision,
            ),
        )
        return
    if isinstance(record, SourceVersionRecord):
        _upsert(
            connection,
            "usi_source_versions",
            "source_version_id",
            (
                "source_version_id", "source_id", "owner_scope", "revision_ref",
                "content_hash", "record_json", "record_hash", "record_revision",
            ),
            (
                record.source_version_id, record.source_id, record.owner_scope,
                record.revision_ref, record.content_hash, record_json, record_hash,
                revision,
            ),
        )
        return
    if isinstance(record, ChunkRecord):
        _upsert(
            connection,
            "usi_chunks",
            "chunk_id",
            (
                "chunk_id", "source_version_id", "source_id", "owner_scope",
                "locator_kind", "content", "content_hash", "record_json",
                "record_hash", "record_revision",
            ),
            (
                record.chunk_id, record.source_version_id, record.source_id,
                record.owner_scope, record.locator.kind.value, record.content,
                record.content_hash, record_json, record_hash, revision,
            ),
        )
        return
    if isinstance(record, EntityRecord):
        _upsert(
            connection,
            "usi_entities",
            "entity_id",
            (
                "entity_id", "source_version_id", "source_id", "owner_scope",
                "entity_kind", "locator_kind", "label", "record_json",
                "record_hash", "record_revision",
            ),
            (
                record.entity_id, record.source_version_id, record.source_id,
                record.owner_scope, record.entity_kind.value,
                record.locator.kind.value, record.label, record_json, record_hash,
                revision,
            ),
        )
        return
    if isinstance(record, RelationRecord):
        _upsert(
            connection,
            "usi_relations",
            "relation_id",
            (
                "relation_id", "owner_scope", "relation_kind",
                "source_record_kind", "source_record_id", "target_record_kind",
                "target_record_id", "record_json", "record_hash", "record_revision",
            ),
            (
                record.relation_id, record.owner_scope, record.relation_kind.value,
                record.source_ref.record_kind.value, record.source_ref.record_id,
                record.target_ref.record_kind.value, record.target_ref.record_id,
                record_json, record_hash, revision,
            ),
        )
        return
    if isinstance(record, LineageRecord):
        _upsert(
            connection,
            "usi_lineage",
            "lineage_id",
            (
                "lineage_id", "owner_scope", "previous_chunk_id",
                "current_chunk_id", "reason", "confidence", "record_json",
                "record_hash", "record_revision",
            ),
            (
                record.lineage_id, record.owner_scope, record.previous.record_id,
                record.current.record_id, record.reason.value, record.confidence,
                record_json, record_hash, revision,
            ),
        )
        return
    if isinstance(record, ProjectionManifest):
        _upsert(
            connection,
            "usi_projection_manifests",
            "projection_id",
            (
                "projection_id", "owner_scope", "projection_kind",
                "input_snapshot_ref", "output_generation_ref", "record_json",
                "record_hash", "record_revision",
            ),
            (
                record.projection_id, record.owner_scope, record.projection_kind.value,
                record.input_snapshot_ref, record.output_generation_ref, record_json,
                record_hash, revision,
            ),
        )
        return
    if isinstance(record, DerivedRunRecord):
        _upsert(
            connection,
            "usi_derived_runs",
            "derived_run_id",
            (
                "derived_run_id", "owner_scope", "derived_kind",
                "source_scope_id", "input_snapshot_ref", "record_json",
                "record_hash", "record_revision",
            ),
            (
                record.derived_run_id, record.owner_scope, record.derived_kind.value,
                record.source_scope.scope_id, record.input_snapshot_ref, record_json,
                record_hash, revision,
            ),
        )
        return
    if isinstance(record, IndexJobRecord):
        _upsert(
            connection,
            "usi_jobs",
            "job_id",
            (
                "job_id", "owner_scope", "job_kind", "source_scope_id",
                "status", "cursor", "record_json", "record_hash",
                "record_revision",
            ),
            (
                record.job_id, record.owner_scope, record.job_kind.value,
                record.source_scope.scope_id, record.status.value, record.cursor,
                record_json, record_hash, revision,
            ),
        )
        return
    raise SQLiteUnifiedSourceIndexError(
        f"unsupported USI store record: {type(record).__name__}"
    )


def _upsert(
    connection: sqlite3.Connection,
    table: str,
    id_column: str,
    columns: tuple[str, ...],
    values: tuple[object, ...],
) -> None:
    column_list = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(
        f"{column} = excluded.{column}" for column in columns if column != id_column
    )
    connection.execute(
        f"INSERT INTO {table}({column_list}) VALUES ({placeholders}) "
        f"ON CONFLICT({id_column}) DO UPDATE SET {updates}",
        values,
    )


def _make_current_snapshot(
    connection: sqlite3.Connection,
    revision: int,
) -> StoreSnapshot:
    state: list[dict[str, object]] = []
    for kind, spec in _TABLES.items():
        rows = connection.execute(
            f"SELECT {spec.id_column} AS record_id, record_revision, record_hash "
            f"FROM {spec.table}"
        ).fetchall()
        state.extend(
            {
                "record_kind": kind.value,
                "record_id": str(row["record_id"]),
                "revision": int(row["record_revision"]),
                "record_hash": str(row["record_hash"]),
                "state": "record",
            }
            for row in rows
        )
    tombstones = connection.execute(
        "SELECT record_kind, record_id, revision FROM usi_tombstones"
    ).fetchall()
    state.extend(
        {
            "record_kind": str(row["record_kind"]),
            "record_id": str(row["record_id"]),
            "revision": int(row["revision"]),
            "state": "tombstone",
        }
        for row in tombstones
    )
    state.sort(key=lambda item: (cast(str, item["record_kind"]), cast(str, item["record_id"])))
    state_hash = "sha256:" + hashlib.sha256(
        canonical_json(state).encode("utf-8")
    ).hexdigest()
    return StoreSnapshot(
        revision,
        state_hash,
        len(state) - len(tombstones),
        len(tombstones),
    )


__all__ = [
    "ChunkSearchHit",
    "MAX_FTS_QUERY_CHARS",
    "MAX_FTS_RESULTS",
    "SQLiteStoreReadTransaction",
    "SQLiteStoreWriteTransaction",
    "SQLiteUnifiedSourceIndexError",
    "SQLiteUnifiedSourceIndexStore",
]

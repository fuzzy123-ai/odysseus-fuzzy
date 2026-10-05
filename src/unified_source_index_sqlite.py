"""Transactional SQLite backend for the Unified Source Index.

The backend owns one explicit, filesystem-backed SQLite database.  It keeps
current typed rows for integrity and search, plus an append-only record history
for snapshot-stable reads.  It does not participate in Odysseus' application
database or global migration chain.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
from pathlib import Path
import sqlite3
import time
from typing import Final, cast

from src.unified_source_index_contract import (
    ChunkRecord,
    CodeOccurrenceRecords,
    DerivedRunRecord,
    EntityRecord,
    IndexJobRecord,
    LineageRecord,
    ProjectionManifest,
    RecordKind,
    RelationRecord,
    SourceRecord,
    SourceVersionRecord,
    ForgeCodeOccurrenceEvidence,
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
    _forge_evidence_from_canonical_bytes,
    _capture_forge_occurrence,
)
from src.unified_source_index_code_coverage import (
    COVERAGE_LEDGER_SCHEMA,
    CoverageLedgerError,
    CoverageManifest,
    CoverageManifestLedger,
)
from src.unified_source_index_sources.forge_code import validate_forge_code_occurrence_records


MAX_FTS_QUERY_CHARS: Final = 1_024
MAX_FTS_RESULTS: Final = 1_000
MAX_OWNER_CORPUS_CHUNKS: Final = 8_192
MAX_OWNER_CORPUS_RECORDS: Final = 24_576
MAX_OWNER_CORPUS_BYTES: Final = 8 * 1024 * 1024
MAX_OWNER_MANIFEST_BYTES: Final = 32 * 1024 * 1024
MAX_OWNER_HISTORY_ROWS_PER_RECORD: Final = 256
MAX_OWNER_HISTORY_ROWS: Final = 65_536
MAX_OWNER_HISTORY_BYTES: Final = 64 * 1024 * 1024


class SQLiteUnifiedSourceIndexError(UnifiedSourceIndexStoreError):
    """Raised when SQLite cannot preserve the USI store contract."""


class SQLiteForgeCoverageError(SQLiteUnifiedSourceIndexError):
    """Content-free fixed-code error for durable Forge publication operations."""

    _codes = frozenset({
        "invalid_occurrence", "occurrence_not_found", "invalid_manifest",
        "manifest_head_conflict", "selection_head_conflict", "truth_snapshot_mismatch",
        "terminal_job_mismatch", "selection_missing", "selection_invalidated",
        "selection_not_current", "selected_scope_mismatch", "selected_authority_mismatch",
        "durability_corrupt", "coverage_publication_required",
    })

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._codes else "durability_corrupt"
        super().__init__(self.code)

    def __str__(self) -> str:
        try:
            value = object.__getattribute__(self, "code")
        except BaseException:
            value = "durability_corrupt"
        return value if type(value) is str and value in self._codes else "durability_corrupt"


@dataclass(frozen=True, slots=True)
class SelectedForgeCoverage:
    manifest: CoverageManifest
    occurrences: tuple[CodeOccurrenceRecords, ...]
    source_snapshot: StoreSnapshot
    selection_snapshot: StoreSnapshot
    read_snapshot: StoreSnapshot
    selection_event_digest: str


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

    def require_code_occurrence(
        self,
        *,
        source_id: str,
        source_version_id: str,
        chunk_id: str,
        owner_scope: str,
    ) -> CodeOccurrenceRecords:
        self._ensure_open()
        owner = _owner_scope(owner_scope)
        try:
            chunk = self.get(RecordKind.CHUNK, chunk_id, owner_scope=owner)
            version = self.get(RecordKind.SOURCE_VERSION, source_version_id, owner_scope=owner)
            source = self.get(RecordKind.SOURCE, source_id, owner_scope=owner)
            evidence_row = self._connection.execute(
                """SELECT state, canonical_evidence, evidence_hash FROM usi_forge_occurrence_history
                   WHERE chunk_id = ? AND revision <= ? ORDER BY revision DESC LIMIT 1""",
                (chunk_id, self.snapshot.revision),
            ).fetchone()
            if (
                type(chunk) is not StoredRecord or type(chunk.record) is not ChunkRecord
                or type(version) is not StoredRecord or type(version.record) is not SourceVersionRecord
                or type(source) is not StoredRecord or type(source.record) is not SourceRecord
                or evidence_row is None or evidence_row["state"] == "deleted"
            ):
                raise ValueError
            evidence_bytes = bytes(evidence_row["canonical_evidence"])
            if hashlib.sha256(evidence_bytes).hexdigest() != str(evidence_row["evidence_hash"]):
                raise ValueError
            evidence = _forge_evidence_from_canonical_bytes(evidence_bytes)
            occurrence = CodeOccurrenceRecords(source.record, version.record, chunk.record, evidence)
            validated = validate_forge_code_occurrence_records(occurrence)
            if (
                validated.source.source_id != source_id
                or validated.source_version.source_version_id != source_version_id
                or validated.chunk.chunk_id != chunk_id
                or validated.chunk.owner_scope != owner
            ):
                raise ValueError
            return validated
        except Exception:
            raise StoreNotFoundError("code occurrence parent chain is unavailable") from None

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
        self._forge_occurrence_mutations: dict[str, object] = {}
        self._publication_mutation: dict[str, object] | None = None
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
        if _is_sqlite_forge_bound_record(self._connection, record, self._mutations):
            raise UnifiedSourceIndexStoreError(
                "Forge-bound records require atomic put_code_occurrence"
            )
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

    def put_code_occurrence(self, occurrence: CodeOccurrenceRecords) -> None:
        self._ensure_open()
        prior_mutations = dict(self._mutations)
        prior_occurrences = dict(self._forge_occurrence_mutations)
        try:
            if self._publication_mutation is not None:
                raise ValueError
            captured = _capture_forge_occurrence(occurrence)
            records = tuple(record_from_json(value.decode("utf-8")) for value in captured.record_bytes)
            if any(type(record) not in (SourceRecord, SourceVersionRecord, ChunkRecord) for record in records):
                raise ValueError
            source, version, chunk = cast(tuple[SourceRecord, SourceVersionRecord, ChunkRecord], records)
            chunk_id = chunk.chunk_id
            if chunk_id in self._forge_occurrence_mutations:
                raise ValueError
            evidence = _forge_evidence_from_canonical_bytes(captured.evidence_bytes)
            existing_scopes = {
                (
                    _forge_evidence_from_canonical_bytes(item.evidence_bytes).owner_scope,
                    _forge_evidence_from_canonical_bytes(item.evidence_bytes).repo_id,
                )
                for item in self._forge_occurrence_mutations.values()
            }
            if existing_scopes and existing_scopes != {(evidence.owner_scope, evidence.repo_id)}:
                raise ValueError
            existing_occurrence = self._connection.execute(
                "SELECT chunk_id FROM usi_forge_occurrence_evidence WHERE occurrence_id = ?",
                (evidence.occurrence_ref(chunk.extractor_profile_ref),),
            ).fetchone()
            if existing_occurrence is not None and str(existing_occurrence["chunk_id"]) != chunk_id:
                raise ValueError
            for record in (source, version, chunk):
                kind, record_id, _owner = _record_descriptor(record)
                key = (kind, record_id)
                staged = self._mutations.get(key)
                if staged is not None:
                    # Sibling occurrences may share their exact source and
                    # version records in one truth transaction. Reuse only
                    # byte-identical staged parents; chunks remain unique.
                    if (
                        kind in (RecordKind.SOURCE, RecordKind.SOURCE_VERSION)
                        and isinstance(staged, _PutMutation)
                        and staged.record.to_json().encode("utf-8") == record.to_json().encode("utf-8")
                    ):
                        continue
                    raise ValueError
                current = _current_item(self._connection, kind, record_id)
                if isinstance(current, TombstoneRecord):
                    raise StoreTombstoneError("record identity is tombstoned")
                expected = current.revision if isinstance(current, StoredRecord) else 0
                self._mutations[key] = _PutMutation(record, expected)
            self._forge_occurrence_mutations[chunk_id] = captured
        except Exception:
            self._mutations = prior_mutations
            self._forge_occurrence_mutations = prior_occurrences
            raise SQLiteForgeCoverageError("invalid_occurrence") from None

    def publish_selected_coverage(
        self,
        manifest: CoverageManifest,
        *,
        expected_ledger_head_digest: str,
        expected_selection_event_digest: str,
    ) -> None:
        self._ensure_open()
        if type(manifest) is not CoverageManifest or self._publication_mutation is not None:
            raise SQLiteForgeCoverageError("invalid_manifest")
        try:
            if self._forge_occurrence_mutations or _staged_forge_chunk_tombstone_scopes(
                self._connection, self._mutations
            ):
                raise SQLiteForgeCoverageError("invalid_manifest")
            detached = CoverageManifest.from_canonical_bytes(manifest.canonical_bytes())
            attempt = detached.attempt
            job_row = self._connection.execute(
                """SELECT record_json, record_revision FROM usi_jobs WHERE job_id = ?
                   AND owner_scope = ?""",
                (attempt.job_id, detached.owner_scope),
            ).fetchone()
            staged = self._mutations.get((RecordKind.JOB, attempt.job_id))
            if job_row is None or not isinstance(staged, _PutMutation) or type(staged.record) is not IndexJobRecord:
                raise SQLiteForgeCoverageError("terminal_job_mismatch")
            truth = self._connection.execute(
                """SELECT revision, state_hash, record_count, tombstone_count, snapshot_ref
                   FROM usi_snapshots WHERE revision = ?""",
                (attempt.store_revision,),
            ).fetchone()
            if truth is None:
                raise SQLiteForgeCoverageError("truth_snapshot_mismatch")
            running_job = cast(IndexJobRecord, record_from_json(str(job_row["record_json"])))
            terminal = staged.record
            _verify_persisted_attempt_receipt(
                detached,
                running_job=running_job,
                terminal_job=terminal,
                truth_snapshot=_forge_snapshot_from_row(truth),
                attempt_revision=int(job_row["record_revision"]),
            )
            if staged.expected_revision != attempt.store_revision:
                raise SQLiteForgeCoverageError("terminal_job_mismatch")
            if (expected_ledger_head_digest != _coverage_ledger(self._connection).head_digest):
                raise SQLiteForgeCoverageError("manifest_head_conflict")
            _replay_selection_events(self._connection)
            latest = self._connection.execute(
                """SELECT event_digest FROM usi_forge_coverage_selection_events
                   WHERE owner_scope = ? AND repo_id = ? ORDER BY event_revision DESC LIMIT 1""",
                (detached.owner_scope, detached.repo_id),
            ).fetchone()
            selection_head = "" if latest is None else str(latest["event_digest"])
            if expected_selection_event_digest != selection_head:
                raise SQLiteForgeCoverageError("selection_head_conflict")
            _validate_manifest_occurrences(self._connection, detached, attempt.store_revision)
            self._publication_mutation = {
                "manifest": detached,
                "expected_ledger_head_digest": expected_ledger_head_digest,
                "expected_selection_event_digest": expected_selection_event_digest,
                "job_id": attempt.job_id,
                "job_revision": attempt.store_revision,
            }
        except SQLiteForgeCoverageError:
            raise
        except CoverageLedgerError as exc:
            code = "manifest_head_conflict" if exc.code == "head_conflict" else "invalid_manifest"
            raise SQLiteForgeCoverageError(code) from None
        except Exception:
            raise SQLiteForgeCoverageError("invalid_manifest") from None

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
        if _is_sqlite_forge_bound_record(self._connection, record, self._mutations):
            raise UnifiedSourceIndexStoreError(
                "Forge-bound records require atomic put_code_occurrence"
            )
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
        if not self._mutations and not self._forge_occurrence_mutations and self._publication_mutation is None:
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
            self._forge_occurrence_mutations.clear()
            self._publication_mutation = None
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

    def search_historical_owner_corpus(
        self,
        *,
        owner_scope: str,
        records: tuple[StoreRecord, ...],
        query: str,
        limit: int,
        deadline: float,
    ) -> tuple[StoreSnapshot, tuple[ChunkSearchHit, ...]]:
        """Search complete exact manifest bytes at their earliest common snapshot."""
        owner = _owner_scope(owner_scope)
        if (type(records) is not tuple or len(records) > MAX_OWNER_CORPUS_RECORDS
                or not isinstance(query, str) or not query.strip()
                or len(query) > MAX_FTS_QUERY_CHARS
                or type(limit) is not int or not 1 <= limit <= 501
                or type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise SQLiteUnifiedSourceIndexError("owner corpus request is invalid")
        expected: dict[tuple[str, str], tuple[StoreRecord, str]] = {}
        manifest_bytes = corpus_bytes = chunk_count = 0
        for record in records:
            if time.monotonic() >= float(deadline):
                raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
            if type(record) not in (SourceRecord, SourceVersionRecord, ChunkRecord):
                raise SQLiteUnifiedSourceIndexError("owner corpus manifest is invalid")
            kind, record_id, record_owner = _record_descriptor(record)
            if record_owner != owner or kind not in {
                RecordKind.SOURCE, RecordKind.SOURCE_VERSION, RecordKind.CHUNK,
            }:
                raise SQLiteUnifiedSourceIndexError("owner corpus manifest is invalid")
            key = (kind.value, record_id)
            if key in expected:
                raise SQLiteUnifiedSourceIndexError("owner corpus manifest is invalid")
            raw = record.to_json()
            raw_size = len(raw.encode("utf-8"))
            manifest_bytes += raw_size
            if manifest_bytes > MAX_OWNER_MANIFEST_BYTES:
                raise SQLiteUnifiedSourceIndexError("owner manifest exceeded its bound")
            if type(record) is ChunkRecord:
                chunk_count += 1
                corpus_bytes += len((record.content or "").encode("utf-8"))
                if chunk_count > MAX_OWNER_CORPUS_CHUNKS or corpus_bytes > MAX_OWNER_CORPUS_BYTES:
                    raise SQLiteUnifiedSourceIndexError("owner corpus exceeded its bound")
            expected[key] = (record, raw)
        if time.monotonic() >= float(deadline):
            raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
        if not expected:
            return self.current_snapshot(), ()
        for (kind, _record_id), (record, _raw) in expected.items():
            if type(record) is ChunkRecord and (
                (RecordKind.SOURCE.value, record.source_id) not in expected
                or (RecordKind.SOURCE_VERSION.value, record.source_version_id) not in expected
            ):
                raise SQLiteUnifiedSourceIndexError("owner corpus manifest is incomplete")
            if type(record) is SourceVersionRecord and (
                (RecordKind.SOURCE.value, record.source_id) not in expected
            ):
                raise SQLiteUnifiedSourceIndexError("owner corpus manifest is incomplete")

        connection = self._connect()
        memory = sqlite3.connect(":memory:")
        memory.row_factory = sqlite3.Row
        intervals: dict[tuple[str, str], list[tuple[int, int, int]]] = {
            key: [] for key in expected
        }
        history_rows = history_bytes = 0
        try:
            if time.monotonic() >= float(deadline):
                raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
            connection.execute("BEGIN")
            current = _current_snapshot(connection)

            def timed_out() -> bool:
                return time.monotonic() >= float(deadline)

            def progress() -> int:
                return int(timed_out())

            connection.set_progress_handler(progress, 1_000)
            entries = list(expected.items())
            for offset in range(0, len(entries), 200):
                if timed_out():
                    raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
                batch = entries[offset:offset + 200]
                values = ",".join("(?,?,?)" for _ in batch)
                parameters: list[object] = []
                for (kind, record_id), (_record, raw) in batch:
                    parameters.extend((kind, record_id, raw))
                parameters.extend((
                    owner, current.revision,
                    MAX_OWNER_HISTORY_ROWS_PER_RECORD + 1,
                    MAX_OWNER_HISTORY_ROWS + 1 - history_rows,
                ))
                rows = connection.execute(f"""
                    WITH expected(record_kind,record_id,expected_json) AS (VALUES {values}),
                    ordered AS (
                      SELECT h.record_kind,h.record_id,h.revision,h.state,
                             length(CAST(h.record_json AS BLOB)) AS payload_bytes,
                             CASE WHEN h.state='record' AND h.record_json=e.expected_json
                                  THEN 1 ELSE 0 END AS exact_match,
                             row_number() OVER (
                               PARTITION BY h.record_kind,h.record_id ORDER BY h.revision
                             ) AS ordinal,
                             row_number() OVER (
                               ORDER BY h.record_kind,h.record_id,h.revision
                             ) AS total_ordinal
                      FROM usi_record_history h JOIN expected e
                        ON e.record_kind=h.record_kind AND e.record_id=h.record_id
                      WHERE h.owner_scope=? AND h.revision<=?
                    )
                    SELECT record_kind,record_id,revision,state,payload_bytes,exact_match,
                           ordinal,total_ordinal
                    FROM ordered WHERE ordinal<=? AND total_ordinal<=?
                    ORDER BY record_kind,record_id,revision
                """, tuple(parameters)).fetchall()
                if timed_out():
                    raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
                for row in rows:
                    if timed_out():
                        raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
                    key = (str(row["record_kind"]), str(row["record_id"]))
                    if key not in intervals:
                        raise SQLiteUnifiedSourceIndexError("owner history scope is invalid")
                    if int(row["ordinal"]) > MAX_OWNER_HISTORY_ROWS_PER_RECORD:
                        raise SQLiteUnifiedSourceIndexError("owner history exceeded its bound")
                    history_rows += 1
                    history_bytes += int(row["payload_bytes"] or 0)
                    if history_rows > MAX_OWNER_HISTORY_ROWS or history_bytes > MAX_OWNER_HISTORY_BYTES:
                        raise SQLiteUnifiedSourceIndexError("owner history exceeded its bound")
                    if row["state"] not in ("record", "tombstone"):
                        raise SQLiteUnifiedSourceIndexError("owner history is corrupt")
                    revision = int(row["revision"])
                    spans = intervals[key]
                    if spans and spans[-1][1] == current.revision:
                        start, _end, exact_revision = spans[-1]
                        spans[-1] = (start, revision - 1, exact_revision)
                    if row["state"] == "record" and row["exact_match"] == 1:
                        spans.append((revision, current.revision, revision))

            if timed_out():
                raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
            common: list[tuple[int, int]] | None = None
            for spans in intervals.values():
                if timed_out():
                    raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
                available = [(start, end) for start, end, _rev in spans if start <= end]
                if not available:
                    raise SQLiteUnifiedSourceIndexError("owner history is incomplete")
                if common is None:
                    common = available
                    continue
                overlap: list[tuple[int, int]] = []
                left = right = 0
                while left < len(common) and right < len(available):
                    if timed_out():
                        raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
                    start = max(common[left][0], available[right][0])
                    end = min(common[left][1], available[right][1])
                    if start <= end:
                        overlap.append((start, end))
                    if common[left][1] < available[right][1]:
                        left += 1
                    else:
                        right += 1
                common = overlap
                if not common:
                    raise SQLiteUnifiedSourceIndexError("owner history has no complete snapshot")
            assert common
            earliest = min(start for start, _end in common)
            snapshot = _retained_snapshot(connection, earliest)
            revisions: dict[tuple[str, str], int] = {}
            for key, spans in intervals.items():
                if timed_out():
                    raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
                found = [rev for start, end, rev in spans if start <= earliest <= end]
                if len(found) != 1:
                    raise SQLiteUnifiedSourceIndexError("owner history intersection is corrupt")
                revisions[key] = found[0]

            memory.set_progress_handler(progress, 1_000)
            memory.execute("CREATE VIRTUAL TABLE owner_chunks USING fts5(content, tokenize='unicode61')")
            chunks = [record for (kind, _rid), (record, _raw) in expected.items()
                      if kind == RecordKind.CHUNK.value]
            for offset in range(0, len(chunks), 100):
                if timed_out():
                    raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
                memory.executemany(
                    "INSERT INTO owner_chunks(content) VALUES (?)",
                    ((chunk.content or "",) for chunk in chunks[offset:offset + 100]),
                )
            if timed_out():
                raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
            hits = []
            for row in memory.execute(
                "SELECT rowid,bm25(owner_chunks) AS rank FROM owner_chunks "
                "WHERE owner_chunks MATCH ? ORDER BY rank,rowid LIMIT ?", (query, limit),
            ):
                if timed_out():
                    raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
                chunk = chunks[int(row["rowid"]) - 1]
                hits.append(ChunkSearchHit(
                    StoredRecord(chunk, revisions[(RecordKind.CHUNK.value, chunk.chunk_id)]),
                    float(row["rank"]),
                ))
            if timed_out():
                raise SQLiteUnifiedSourceIndexError("owner corpus deadline exceeded")
            return snapshot, tuple(hits)
        except SQLiteUnifiedSourceIndexError:
            raise
        except sqlite3.DatabaseError:
            raise SQLiteUnifiedSourceIndexError("owner corpus read failed") from None
        except Exception:
            raise SQLiteUnifiedSourceIndexError("owner corpus read failed") from None
        finally:
            try:
                connection.rollback()
            except sqlite3.DatabaseError:
                pass
            connection.close()
            memory.close()

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

    def require_current_selected_coverage(
        self,
        *,
        owner_scope: str,
        repo_id: str,
        forge_revision_id: str,
        commit_sha: str,
        forge_snapshot_digest: str,
        forge_manifest_digest: str,
        authority_binding: tuple[str, str, str, str],
        indexing_policy_generation: str,
    ) -> SelectedForgeCoverage:
        owner = _owner_scope(owner_scope)
        if type(authority_binding) is not tuple or len(authority_binding) != 4 or any(type(item) is not str for item in authority_binding):
            raise SQLiteForgeCoverageError("selected_authority_mismatch")
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            if not _schema_has_forge_sidecars(connection):
                raise SQLiteForgeCoverageError("selection_missing")
            read_snapshot = _current_snapshot(connection)
            event = connection.execute(
                """SELECT * FROM usi_forge_coverage_selection_events
                   WHERE owner_scope = ? AND repo_id = ? AND event_revision <= ?
                   ORDER BY event_revision DESC LIMIT 1""",
                (owner, repo_id, read_snapshot.revision),
            ).fetchone()
            if event is None:
                raise SQLiteForgeCoverageError("selection_missing")
            _replay_selection_events(connection)
            _validate_selection_event_row(event)
            if str(event["event_kind"]) == "invalidate":
                raise SQLiteForgeCoverageError("selection_invalidated")
            manifest_row = connection.execute(
                "SELECT canonical_manifest, published_revision FROM usi_forge_coverage_manifests WHERE coverage_id = ?",
                (str(event["coverage_id"]),),
            ).fetchone()
            if manifest_row is None:
                raise SQLiteForgeCoverageError("durability_corrupt")
            manifest = CoverageManifest.from_canonical_bytes(bytes(manifest_row["canonical_manifest"]))
            if (
                manifest.owner_scope != owner or manifest.repo_id != repo_id
                or manifest.forge_revision_id != forge_revision_id
                or manifest.commit_sha != commit_sha
                or manifest.forge_snapshot_digest != forge_snapshot_digest
                or manifest.forge_manifest_digest != forge_manifest_digest
                or manifest.forge_manifest_digest != str(event["forge_manifest_digest"])
            ):
                raise SQLiteForgeCoverageError("selected_scope_mismatch")
            if (
                manifest.authority_binding != authority_binding
                or manifest.attempt.indexing_policy_generation != indexing_policy_generation
                or canonical_json(list(authority_binding)) != str(event["authority_binding"])
                or indexing_policy_generation != str(event["indexing_policy_generation"])
            ):
                raise SQLiteForgeCoverageError("selected_authority_mismatch")
            event_revision = int(event["event_revision"])
            if int(manifest_row["published_revision"]) != event_revision:
                raise SQLiteForgeCoverageError("terminal_job_mismatch")
            source_snapshot = _forge_retained_snapshot(connection, manifest.attempt.store_revision)
            running_job = _historical_job_at(
                connection, manifest.attempt.job_id, owner, manifest.attempt.store_revision
            )
            terminal_job = _historical_job_at(
                connection, manifest.attempt.job_id, owner, event_revision
            )
            _verify_persisted_attempt_receipt(
                manifest,
                running_job=running_job,
                terminal_job=terminal_job,
                truth_snapshot=source_snapshot,
                attempt_revision=manifest.attempt.store_revision,
                terminal_revision=event_revision,
            )
            _verify_selection_event_receipt(event, manifest, event_revision)
            _replay_coverage_ledger(connection)
            attempt = manifest.attempt
            selection_snapshot = _forge_retained_snapshot(connection, int(event["event_revision"]))
            mapping_rows = connection.execute(
                """SELECT occurrence_id, chunk_id FROM usi_forge_coverage_occurrences
                   WHERE coverage_id = ? ORDER BY occurrence_id""",
                (manifest.coverage_id,),
            ).fetchall()
            occurrences: list[CodeOccurrenceRecords] = []
            for mapping in mapping_rows:
                occurrences.append(_require_code_occurrence_at(
                    connection,
                    source_snapshot,
                    chunk_id=str(mapping["chunk_id"]),
                    occurrence_id=str(mapping["occurrence_id"]),
                    owner_scope=owner,
                    repo_id=repo_id,
                ))
            _validate_manifest_occurrence_set(manifest, tuple(occurrences))
            return SelectedForgeCoverage(
                manifest, tuple(occurrences), source_snapshot,
                selection_snapshot, read_snapshot, str(event["event_digest"]),
            )
        except SQLiteForgeCoverageError:
            raise
        except Exception:
            raise SQLiteForgeCoverageError("durability_corrupt") from None
        finally:
            connection.rollback()
            connection.close()

    def verify_selected_coverage_replay(
        self,
        manifest: CoverageManifest,
        *,
        original_job: IndexJobRecord,
        terminal_job: IndexJobRecord,
        expected_ledger_head_digest: str,
        expected_selection_event_digest: str,
        adapter_authority_binding: tuple[str, str, str, str],
    ) -> StoreSnapshot:
        """Return the immutable P snapshot for an exact lost-response replay."""
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            if not _schema_has_forge_sidecars(connection):
                raise SQLiteForgeCoverageError("selection_missing")
            detached = CoverageManifest.from_canonical_bytes(manifest.canonical_bytes())
            attempt = detached.attempt
            if (
                type(original_job) is not IndexJobRecord
                or type(terminal_job) is not IndexJobRecord
                or type(adapter_authority_binding) is not tuple
                or len(adapter_authority_binding) != 4
                or detached.authority_binding != adapter_authority_binding
            ):
                raise SQLiteForgeCoverageError("terminal_job_mismatch")

            truth = _forge_retained_snapshot(connection, attempt.store_revision)
            historical_job = connection.execute(
                """SELECT record_json FROM usi_record_history
                   WHERE record_kind = ? AND record_id = ? AND owner_scope = ?
                     AND revision = ? AND state = 'record'""",
                (RecordKind.JOB.value, attempt.job_id, detached.owner_scope, attempt.store_revision),
            ).fetchone()
            if historical_job is None or str(historical_job["record_json"]) != original_job.to_json():
                raise SQLiteForgeCoverageError("terminal_job_mismatch")

            stored_job = connection.execute(
                """SELECT record_json, record_revision FROM usi_jobs
                   WHERE job_id = ? AND owner_scope = ?""",
                (attempt.job_id, detached.owner_scope),
            ).fetchone()
            manifest_row = connection.execute(
                """SELECT canonical_manifest, manifest_digest, sequence,
                          previous_entry_digest, published_revision
                   FROM usi_forge_coverage_manifests WHERE coverage_id = ?""",
                (detached.coverage_id,),
            ).fetchone()
            if stored_job is None or manifest_row is None:
                raise SQLiteForgeCoverageError("terminal_job_mismatch")
            if (
                bytes(manifest_row["canonical_manifest"]) != detached.canonical_bytes()
                or str(manifest_row["manifest_digest"]) != detached.manifest_digest
                or str(manifest_row["previous_entry_digest"]) != expected_ledger_head_digest
            ):
                raise SQLiteForgeCoverageError("invalid_manifest")

            ledger = _replay_coverage_ledger(connection)
            entry = next((item for item in ledger.entries() if item.coverage_id == detached.coverage_id), None)
            if entry is None or entry.previous_entry_digest != expected_ledger_head_digest:
                raise SQLiteForgeCoverageError("manifest_head_conflict")

            _replay_selection_events(connection)
            event = connection.execute(
                """SELECT * FROM usi_forge_coverage_selection_events
                   WHERE owner_scope = ? AND repo_id = ? AND coverage_id = ?
                     AND event_kind = 'select'""",
                (detached.owner_scope, detached.repo_id, detached.coverage_id),
            ).fetchone()
            if event is None:
                raise SQLiteForgeCoverageError("selection_missing")
            _validate_selection_event_row(event)
            event_revision = int(event["event_revision"])
            if (
                event_revision != int(manifest_row["published_revision"])
                or event_revision != int(stored_job["record_revision"])
                or str(event["previous_event_digest"]) != expected_selection_event_digest
                or str(event["forge_manifest_digest"]) != detached.forge_manifest_digest
                or str(event["authority_binding"]) != canonical_json(list(detached.authority_binding))
                or str(event["indexing_policy_generation"]) != attempt.indexing_policy_generation
                or str(event["forge_revision_id"]) != detached.forge_revision_id
                or str(event["commit_sha"]) != detached.commit_sha
                or str(event["forge_snapshot_digest"]) != detached.forge_snapshot_digest
                or str(stored_job["record_json"]) != terminal_job.to_json()
            ):
                raise SQLiteForgeCoverageError("terminal_job_mismatch")
            _verify_persisted_attempt_receipt(
                detached,
                running_job=original_job,
                terminal_job=terminal_job,
                truth_snapshot=truth,
                attempt_revision=attempt.store_revision,
                terminal_revision=int(stored_job["record_revision"]),
            )
            _verify_selection_event_receipt(event, detached, event_revision)

            mapping_rows = connection.execute(
                """SELECT occurrence_id, chunk_id FROM usi_forge_coverage_occurrences
                   WHERE coverage_id = ? ORDER BY occurrence_id""",
                (detached.coverage_id,),
            ).fetchall()
            actual_pairs = tuple(
                (str(row["occurrence_id"]), str(row["chunk_id"])) for row in mapping_rows
            )
            expected_pairs = _validate_manifest_occurrences(
                connection, detached, attempt.store_revision, historical_only=True
            )
            if actual_pairs != expected_pairs:
                raise SQLiteForgeCoverageError("durability_corrupt")
            return _forge_retained_snapshot(connection, event_revision)
        except SQLiteForgeCoverageError:
            raise
        except Exception:
            raise SQLiteForgeCoverageError("durability_corrupt") from None
        finally:
            connection.rollback()
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

        if transaction._publication_mutation is not None:
            publication = transaction._publication_mutation
            manifest = cast(CoverageManifest, publication["manifest"])
            if _coverage_ledger(connection).head_digest != publication["expected_ledger_head_digest"]:
                raise SQLiteForgeCoverageError("manifest_head_conflict")
            latest_event = connection.execute(
                """SELECT event_digest FROM usi_forge_coverage_selection_events
                   WHERE owner_scope = ? AND repo_id = ? ORDER BY event_revision DESC LIMIT 1""",
                (manifest.owner_scope, manifest.repo_id),
            ).fetchone()
            actual_event_head = "" if latest_event is None else str(latest_event["event_digest"])
            if actual_event_head != publication["expected_selection_event_digest"]:
                raise SQLiteForgeCoverageError("selection_head_conflict")
            _validate_manifest_occurrences(connection, manifest, int(publication["job_revision"]))

        occurrence_scopes = {
            (
                evidence.owner_scope,
                evidence.repo_id,
            )
            for captured in transaction._forge_occurrence_mutations.values()
            for evidence in (
                _forge_evidence_from_canonical_bytes(captured.evidence_bytes),
            )
        }
        tombstone_scopes = _staged_forge_chunk_tombstone_scopes(
            connection, transaction._mutations
        )
        affected_scopes = occurrence_scopes | tombstone_scopes
        selected_scopes = {
            scope for scope in affected_scopes
            if _selection_scope_is_currently_selected(connection, *scope)
        }
        if len(occurrence_scopes) > 1 or len(selected_scopes) > 1:
            raise SQLiteForgeCoverageError("invalid_occurrence")
        if transaction._publication_mutation is not None and selected_scopes:
            raise SQLiteForgeCoverageError("invalid_manifest")

        next_revision = current.revision + 1
        if transaction._publication_mutation is not None:
            publication = transaction._publication_mutation
            manifest = cast(CoverageManifest, publication["manifest"])
            attempt = manifest.attempt
            truth_row = connection.execute(
                """SELECT revision, state_hash, record_count, tombstone_count, snapshot_ref
                   FROM usi_snapshots WHERE revision = ?""",
                (attempt.store_revision,),
            ).fetchone()
            job_row = connection.execute(
                "SELECT record_json, record_revision FROM usi_jobs WHERE job_id = ? AND owner_scope = ?",
                (attempt.job_id, manifest.owner_scope),
            ).fetchone()
            staged = transaction._mutations.get((RecordKind.JOB, attempt.job_id))
            if truth_row is None:
                raise SQLiteForgeCoverageError("truth_snapshot_mismatch")
            if job_row is None or not isinstance(staged, _PutMutation) or type(staged.record) is not IndexJobRecord:
                raise SQLiteForgeCoverageError("terminal_job_mismatch")
            _verify_persisted_attempt_receipt(
                manifest,
                running_job=cast(IndexJobRecord, record_from_json(str(job_row["record_json"]))),
                terminal_job=cast(IndexJobRecord, staged.record),
                truth_snapshot=_forge_snapshot_from_row(truth_row),
                attempt_revision=int(job_row["record_revision"]),
                terminal_revision=next_revision,
            )
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

        for chunk_id, captured in transaction._forge_occurrence_mutations.items():
            try:
                records = tuple(record_from_json(value.decode("utf-8")) for value in captured.record_bytes)
                source, version, chunk = cast(tuple[SourceRecord, SourceVersionRecord, ChunkRecord], records)
                evidence = _forge_evidence_from_canonical_bytes(captured.evidence_bytes)
                occurrence_id = evidence.occurrence_ref(chunk.extractor_profile_ref)
                evidence_hash = hashlib.sha256(captured.evidence_bytes).hexdigest()
                connection.execute(
                    """INSERT INTO usi_forge_occurrence_evidence(
                       chunk_id, occurrence_id, source_id, source_version_id, owner_scope,
                       repo_id, canonical_evidence, evidence_hash, record_revision)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(chunk_id) DO UPDATE SET occurrence_id=excluded.occurrence_id,
                       source_id=excluded.source_id, source_version_id=excluded.source_version_id,
                       owner_scope=excluded.owner_scope, repo_id=excluded.repo_id,
                       canonical_evidence=excluded.canonical_evidence,
                       evidence_hash=excluded.evidence_hash, record_revision=excluded.record_revision""",
                    (chunk_id, occurrence_id, source.source_id, version.source_version_id,
                     source.owner_scope, evidence.repo_id, captured.evidence_bytes,
                     evidence_hash, next_revision),
                )
                connection.execute(
                    """INSERT INTO usi_forge_occurrence_history(
                       chunk_id, revision, state, canonical_evidence, evidence_hash,
                       occurrence_id, source_id, source_version_id, owner_scope, repo_id)
                       VALUES (?, ?, 'evidence', ?, ?, ?, ?, ?, ?, ?)""",
                    (chunk_id, next_revision, captured.evidence_bytes, evidence_hash,
                     occurrence_id, source.source_id, version.source_version_id,
                     source.owner_scope, evidence.repo_id),
                )
                _invalidate_selected_scope(connection, evidence, next_revision)
            except SQLiteForgeCoverageError:
                raise
            except Exception:
                raise SQLiteForgeCoverageError("durability_corrupt") from None

        if transaction._publication_mutation is not None:
            _apply_coverage_publication(
                connection,
                cast(CoverageManifest, transaction._publication_mutation["manifest"]),
                expected_ledger_head_digest=str(transaction._publication_mutation["expected_ledger_head_digest"]),
                expected_selection_event_digest=str(transaction._publication_mutation["expected_selection_event_digest"]),
                published_revision=next_revision,
            )

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


def _forge_snapshot_from_row(row: sqlite3.Row) -> StoreSnapshot:
    try:
        return _snapshot_from_row(row)
    except Exception:
        raise SQLiteForgeCoverageError("durability_corrupt") from None


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
    if _schema_has_forge_sidecars(connection) and mutation.record_kind is RecordKind.CHUNK:
        evidence_row = connection.execute(
            """SELECT canonical_evidence FROM usi_forge_occurrence_evidence
               WHERE chunk_id = ? AND owner_scope = ?""",
            (mutation.record_id, mutation.owner_scope),
        ).fetchone()
        if evidence_row is not None:
            raw = bytes(evidence_row["canonical_evidence"])
            evidence = _forge_evidence_from_canonical_bytes(raw)
            connection.execute(
                "DELETE FROM usi_forge_occurrence_evidence WHERE chunk_id = ?",
                (mutation.record_id,),
            )
            connection.execute(
                """INSERT INTO usi_forge_occurrence_history(
                   chunk_id, revision, state, canonical_evidence, evidence_hash,
                   occurrence_id, source_id, source_version_id, owner_scope, repo_id)
                   VALUES (?, ?, 'deleted', NULL, NULL, NULL, NULL, NULL, NULL, NULL)""",
                (mutation.record_id, revision),
            )
            _invalidate_selected_scope(connection, evidence, revision)
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
    if _schema_has_forge_sidecars(connection):
        state.extend(
            {
                "record_kind": "forge_occurrence_evidence",
                "record_id": str(row["chunk_id"]),
                "revision": revision,
                "record_hash": str(row["evidence_hash"]),
                "state": "record",
            }
            for row in connection.execute(
                "SELECT chunk_id, record_revision, evidence_hash FROM usi_forge_occurrence_evidence"
            )
        )
        state.extend(
            {
                "record_kind": "forge_coverage_manifest",
                "record_id": str(row["coverage_id"]),
                "revision": int(row["published_revision"]),
                "record_hash": str(row["manifest_digest"]),
                "state": "record",
            }
            for row in connection.execute(
                "SELECT coverage_id, published_revision, manifest_digest FROM usi_forge_coverage_manifests"
            )
        )
        for row in connection.execute(
            "SELECT coverage_id, occurrence_id, chunk_id FROM usi_forge_coverage_occurrences"
        ):
            mapping = {
                "coverage_id": str(row["coverage_id"]),
                "occurrence_id": str(row["occurrence_id"]),
                "chunk_id": str(row["chunk_id"]),
            }
            state.append({
                "record_kind": "forge_coverage_occurrence",
                "record_id": mapping["coverage_id"] + ":" + mapping["occurrence_id"],
                "revision": int(connection.execute(
                    "SELECT published_revision FROM usi_forge_coverage_manifests WHERE coverage_id = ?",
                    (mapping["coverage_id"],),
                ).fetchone()[0]),
                "record_hash": "sha256:" + hashlib.sha256(canonical_json(mapping).encode("utf-8")).hexdigest(),
                "state": "record",
            })
        state.extend(
            {
                "record_kind": "forge_selection_event",
                "record_id": str(row["event_revision"]),
                "revision": int(row["event_revision"]),
                "record_hash": str(row["event_digest"]),
                "state": "record",
            }
            for row in connection.execute(
                """SELECT e.event_revision, e.event_digest FROM usi_forge_coverage_selection_events e
                   WHERE e.event_revision = (SELECT MAX(x.event_revision)
                     FROM usi_forge_coverage_selection_events x
                     WHERE x.owner_scope=e.owner_scope AND x.repo_id=e.repo_id)"""
            )
        )
    state.sort(key=lambda item: (cast(str, item["record_kind"]), cast(str, item["record_id"])))
    state_hash = "sha256:" + hashlib.sha256(
        canonical_json(state).encode("utf-8")
    ).hexdigest()
    return StoreSnapshot(
        revision,
        state_hash,
        sum(int(connection.execute(f"SELECT COUNT(*) FROM {spec.table}").fetchone()[0]) for spec in _TABLES.values()),
        len(tombstones),
    )


def _schema_has_forge_sidecars(connection: sqlite3.Connection) -> bool:
    return int(connection.execute("PRAGMA user_version").fetchone()[0]) == 2


def _is_sqlite_forge_bound_record(
    connection: sqlite3.Connection,
    record: StoreRecord,
    staged: dict[tuple[RecordKind, str], _Mutation],
) -> bool:
    if type(record) is SourceRecord:
        return record.provider_ref == "forge.code" or record.canonical_ref.startswith("forge-code-source:")
    if type(record) is SourceVersionRecord:
        if record.provider_ref == "forge.code" or record.revision_ref.startswith("forge-code-version:"):
            return True
        parent_mutation = staged.get((RecordKind.SOURCE, record.source_id))
        if isinstance(parent_mutation, _PutMutation) and type(parent_mutation.record) is SourceRecord:
            return _is_sqlite_forge_bound_record(connection, parent_mutation.record, staged)
        parent = _current_item(connection, RecordKind.SOURCE, record.source_id)
        return type(parent) is StoredRecord and type(parent.record) is SourceRecord and _is_sqlite_forge_bound_record(connection, parent.record, staged)
    if type(record) is ChunkRecord:
        if record.extractor_profile_ref.startswith("forge-code-"):
            return True
        mutation = staged.get((RecordKind.SOURCE_VERSION, record.source_version_id))
        if isinstance(mutation, _PutMutation) and type(mutation.record) is SourceVersionRecord:
            return _is_sqlite_forge_bound_record(connection, mutation.record, staged)
        parent = _current_item(connection, RecordKind.SOURCE_VERSION, record.source_version_id)
        return type(parent) is StoredRecord and type(parent.record) is SourceVersionRecord and _is_sqlite_forge_bound_record(connection, parent.record, staged)
    return False


def _invalidate_selected_scope(
    connection: sqlite3.Connection,
    evidence: ForgeCodeOccurrenceEvidence,
    revision: int,
) -> None:
    latest = connection.execute(
        """SELECT * FROM usi_forge_coverage_selection_events
           WHERE owner_scope = ? AND repo_id = ? ORDER BY event_revision DESC LIMIT 1""",
        (evidence.owner_scope, evidence.repo_id),
    ).fetchone()
    if latest is None or str(latest["event_kind"]) != "select":
        return
    values = {
        "owner_scope": evidence.owner_scope,
        "repo_id": evidence.repo_id,
        "event_kind": "invalidate",
        "coverage_id": None,
        "forge_revision_id": str(latest["forge_revision_id"]),
        "commit_sha": str(latest["commit_sha"]),
        "forge_snapshot_digest": str(latest["forge_snapshot_digest"]),
        "forge_manifest_digest": str(latest["forge_manifest_digest"]),
        "authority_binding": str(latest["authority_binding"]),
        "indexing_policy_generation": str(latest["indexing_policy_generation"]),
        "reason_code": "occurrence_changed",
        "previous_event_digest": str(latest["event_digest"]),
        "event_revision": revision,
    }
    digest = "sha256:" + hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest()
    connection.execute(
        """INSERT INTO usi_forge_coverage_selection_events(
           event_revision, owner_scope, repo_id, event_kind, coverage_id,
           forge_revision_id, commit_sha, forge_snapshot_digest, forge_manifest_digest,
           authority_binding, indexing_policy_generation, reason_code,
           previous_event_digest, event_digest)
           VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (revision, evidence.owner_scope, evidence.repo_id, "invalidate",
         values["forge_revision_id"], values["commit_sha"], values["forge_snapshot_digest"],
         values["forge_manifest_digest"], values["authority_binding"],
         values["indexing_policy_generation"], "occurrence_changed",
         values["previous_event_digest"], digest),
    )


def _selection_scope_is_currently_selected(
    connection: sqlite3.Connection,
    owner_scope: str,
    repo_id: str,
) -> bool:
    row = connection.execute(
        """SELECT event_kind FROM usi_forge_coverage_selection_events
           WHERE owner_scope = ? AND repo_id = ?
           ORDER BY event_revision DESC LIMIT 1""",
        (owner_scope, repo_id),
    ).fetchone()
    return row is not None and str(row["event_kind"]) == "select"


def _staged_forge_chunk_tombstone_scopes(
    connection: sqlite3.Connection,
    mutations: dict[tuple[RecordKind, str], object],
) -> set[tuple[str, str]]:
    scopes: set[tuple[str, str]] = set()
    for mutation in mutations.values():
        if not isinstance(mutation, _TombstoneMutation) or mutation.record_kind is not RecordKind.CHUNK:
            continue
        evidence_row = connection.execute(
            """SELECT owner_scope, repo_id FROM usi_forge_occurrence_evidence
               WHERE chunk_id = ? AND owner_scope = ?""",
            (mutation.record_id, mutation.owner_scope),
        ).fetchone()
        if evidence_row is not None:
            scopes.add((str(evidence_row["owner_scope"]), str(evidence_row["repo_id"])))
    return scopes


def _coverage_ledger(connection: sqlite3.Connection) -> CoverageManifestLedger:
    rows = connection.execute(
        """SELECT sequence, coverage_id, scope_digest, supersedes_coverage_id,
           previous_entry_digest, entry_digest, canonical_manifest
           FROM usi_forge_coverage_manifests ORDER BY sequence"""
    ).fetchall()
    entries = tuple(canonical_json({
        "schema": COVERAGE_LEDGER_SCHEMA,
        "sequence": int(row["sequence"]),
        "coverage_id": str(row["coverage_id"]),
        "scope_digest": str(row["scope_digest"]),
        "supersedes_coverage_id": str(row["supersedes_coverage_id"]),
        "previous_entry_digest": str(row["previous_entry_digest"]),
        "canonical_bytes": bytes(row["canonical_manifest"]).hex(),
        "entry_digest": str(row["entry_digest"]),
    }).encode("utf-8") for row in rows)
    return CoverageManifestLedger.from_canonical_entries(entries)


def _replay_coverage_ledger(connection: sqlite3.Connection) -> CoverageManifestLedger:
    ledger = _coverage_ledger(connection)
    entries = ledger.entries()
    rows = connection.execute(
        """SELECT sequence, coverage_id, scope_digest, supersedes_coverage_id,
           previous_entry_digest, entry_digest, canonical_manifest, manifest_digest
           FROM usi_forge_coverage_manifests ORDER BY sequence"""
    ).fetchall()
    if len(entries) != len(rows):
        raise SQLiteForgeCoverageError("durability_corrupt")
    for entry, row in zip(entries, rows, strict=True):
        manifest = CoverageManifest.from_canonical_bytes(bytes(row["canonical_manifest"]))
        if (
            entry.sequence != int(row["sequence"])
            or entry.coverage_id != str(row["coverage_id"])
            or entry.scope_digest != str(row["scope_digest"])
            or entry.supersedes_coverage_id != str(row["supersedes_coverage_id"])
            or entry.previous_entry_digest != str(row["previous_entry_digest"])
            or entry.entry_digest != str(row["entry_digest"])
            or manifest.manifest_digest != str(row["manifest_digest"])
        ):
            raise SQLiteForgeCoverageError("durability_corrupt")
    return ledger


def _manifest_occurrence_ids(manifest: CoverageManifest) -> tuple[str, ...]:
    values = tuple(
        occurrence_id
        for file_record in manifest.files
        for occurrence_id in file_record.occurrence_ids
    )
    if len(values) != len(set(values)):
        raise SQLiteForgeCoverageError("invalid_manifest")
    return tuple(sorted(values))


def _validate_manifest_occurrences(
    connection: sqlite3.Connection,
    manifest: CoverageManifest,
    truth_revision: int,
    *,
    historical_only: bool = False,
) -> tuple[tuple[str, str], ...]:
    if not historical_only:
        newer_scope_truth = connection.execute(
            """SELECT 1 FROM usi_forge_occurrence_history
               WHERE owner_scope = ? AND repo_id = ? AND revision > ? LIMIT 1""",
            (manifest.owner_scope, manifest.repo_id, truth_revision),
        ).fetchone()
        if newer_scope_truth is not None:
            raise SQLiteForgeCoverageError("truth_snapshot_mismatch")
    occurrence_ids = _manifest_occurrence_ids(manifest)
    rows: list[tuple[str, str]] = []
    for occurrence_id in occurrence_ids:
        row = connection.execute(
            """SELECT chunk_id FROM usi_forge_occurrence_history
               WHERE occurrence_id = ? AND revision <= ? AND state = 'evidence'
               ORDER BY revision DESC LIMIT 1""",
            (occurrence_id, truth_revision),
        ).fetchone()
        if row is None:
            raise SQLiteForgeCoverageError("truth_snapshot_mismatch")
        chunk_id = str(row["chunk_id"])
        occurrence = _require_code_occurrence_at(
            connection,
            _forge_retained_snapshot(connection, truth_revision),
            chunk_id=chunk_id,
            occurrence_id=occurrence_id,
            owner_scope=manifest.owner_scope,
            repo_id=manifest.repo_id,
        )
        evidence = occurrence.forge_evidence
        assert evidence is not None
        if (
            evidence.version_id != manifest.forge_revision_id
            or evidence.commit_sha != manifest.commit_sha
            or evidence.snapshot_digest != manifest.forge_snapshot_digest
            or evidence.authority_binding != manifest.authority_binding
            or occurrence.source.source_id not in manifest.attempt.source_ids
            or occurrence.source_version.source_version_id not in manifest.attempt.source_version_ids
        ):
            raise SQLiteForgeCoverageError("truth_snapshot_mismatch")
        rows.append((occurrence_id, chunk_id))
    return tuple(rows)


def _validate_manifest_occurrence_set(
    manifest: CoverageManifest,
    occurrences: tuple[CodeOccurrenceRecords, ...],
) -> None:
    actual: list[str] = []
    for occurrence in occurrences:
        if occurrence.forge_evidence is None:
            raise SQLiteForgeCoverageError("durability_corrupt")
        actual.append(occurrence.forge_evidence.occurrence_ref(occurrence.chunk.extractor_profile_ref))
    if tuple(sorted(actual)) != _manifest_occurrence_ids(manifest):
        raise SQLiteForgeCoverageError("durability_corrupt")


def _require_code_occurrence_at(
    connection: sqlite3.Connection,
    snapshot: StoreSnapshot,
    *,
    chunk_id: str,
    occurrence_id: str,
    owner_scope: str,
    repo_id: str,
) -> CodeOccurrenceRecords:
    try:
        items = []
        for kind, record_id in (
            (RecordKind.SOURCE, _source_id_for_chunk(connection, snapshot, chunk_id)),
            (RecordKind.SOURCE_VERSION, _version_id_for_chunk(connection, snapshot, chunk_id)),
            (RecordKind.CHUNK, chunk_id),
        ):
            row = connection.execute(
                """SELECT record_kind, record_id, owner_scope, revision, state,
                   record_json, previous_revision, reason FROM usi_record_history
                   WHERE record_kind = ? AND record_id = ? AND owner_scope = ?
                   AND revision <= ? ORDER BY revision DESC LIMIT 1""",
                (kind.value, record_id, owner_scope, snapshot.revision),
            ).fetchone()
            if row is None:
                raise ValueError
            items.append(_history_item(row))
        source_item, version_item, chunk_item = items
        evidence_row = connection.execute(
            """SELECT state, canonical_evidence, evidence_hash, occurrence_id, owner_scope, repo_id
               FROM usi_forge_occurrence_history WHERE chunk_id = ? AND revision <= ?
               ORDER BY revision DESC LIMIT 1""",
            (chunk_id, snapshot.revision),
        ).fetchone()
        if (
            type(source_item) is not StoredRecord or type(source_item.record) is not SourceRecord
            or type(version_item) is not StoredRecord or type(version_item.record) is not SourceVersionRecord
            or type(chunk_item) is not StoredRecord or type(chunk_item.record) is not ChunkRecord
            or evidence_row is None or str(evidence_row["state"]) != "evidence"
            or str(evidence_row["owner_scope"]) != owner_scope or str(evidence_row["repo_id"]) != repo_id
            or str(evidence_row["occurrence_id"]) != occurrence_id
        ):
            raise ValueError
        raw = bytes(evidence_row["canonical_evidence"])
        if hashlib.sha256(raw).hexdigest() != str(evidence_row["evidence_hash"]):
            raise ValueError
        evidence = _forge_evidence_from_canonical_bytes(raw)
        result = validate_forge_code_occurrence_records(CodeOccurrenceRecords(
            source_item.record, version_item.record, chunk_item.record, evidence,
        ))
        if evidence.occurrence_ref(result.chunk.extractor_profile_ref) != occurrence_id:
            raise ValueError
        return result
    except Exception:
        raise SQLiteForgeCoverageError("durability_corrupt") from None


def _source_id_for_chunk(connection: sqlite3.Connection, snapshot: StoreSnapshot, chunk_id: str) -> str:
    row = connection.execute(
        """SELECT record_json FROM usi_record_history WHERE record_kind = 'chunk'
           AND record_id = ? AND revision <= ? ORDER BY revision DESC LIMIT 1""",
        (chunk_id, snapshot.revision),
    ).fetchone()
    if row is None:
        raise ValueError
    record = record_from_json(str(row["record_json"]))
    if type(record) is not ChunkRecord:
        raise ValueError
    return record.source_id


def _version_id_for_chunk(connection: sqlite3.Connection, snapshot: StoreSnapshot, chunk_id: str) -> str:
    row = connection.execute(
        """SELECT record_json FROM usi_record_history WHERE record_kind = 'chunk'
           AND record_id = ? AND revision <= ? ORDER BY revision DESC LIMIT 1""",
        (chunk_id, snapshot.revision),
    ).fetchone()
    if row is None:
        raise ValueError
    record = record_from_json(str(row["record_json"]))
    if type(record) is not ChunkRecord:
        raise ValueError
    return record.source_version_id


def _retained_snapshot(connection: sqlite3.Connection, revision: int) -> StoreSnapshot:
    row = connection.execute(
        """SELECT revision, snapshot_ref, state_hash, record_count, tombstone_count
           FROM usi_snapshots WHERE revision = ?""",
        (revision,),
    ).fetchone()
    if row is None:
        raise SQLiteForgeCoverageError("truth_snapshot_mismatch")
    return _snapshot_from_row(row)


def _forge_retained_snapshot(connection: sqlite3.Connection, revision: int) -> StoreSnapshot:
    try:
        return _retained_snapshot(connection, revision)
    except SQLiteForgeCoverageError:
        raise
    except Exception:
        raise SQLiteForgeCoverageError("durability_corrupt") from None


def _selection_event_values(
    *, event_revision: int, owner_scope: str, repo_id: str, event_kind: str,
    coverage_id: str | None, forge_revision_id: str, commit_sha: str,
    forge_snapshot_digest: str, forge_manifest_digest: str, authority_binding: str,
    indexing_policy_generation: str, reason_code: str, previous_event_digest: str,
) -> dict[str, object]:
    return {
        "event_revision": event_revision, "owner_scope": owner_scope, "repo_id": repo_id,
        "event_kind": event_kind, "coverage_id": coverage_id,
        "forge_revision_id": forge_revision_id, "commit_sha": commit_sha,
        "forge_snapshot_digest": forge_snapshot_digest,
        "forge_manifest_digest": forge_manifest_digest,
        "authority_binding": authority_binding,
        "indexing_policy_generation": indexing_policy_generation,
        "reason_code": reason_code, "previous_event_digest": previous_event_digest,
    }


def _historical_job_at(
    connection: sqlite3.Connection,
    job_id: str,
    owner_scope: str,
    revision: int,
) -> IndexJobRecord:
    row = connection.execute(
        """SELECT record_json FROM usi_record_history
           WHERE record_kind = ? AND record_id = ? AND owner_scope = ?
             AND revision = ? AND state = 'record'""",
        (RecordKind.JOB.value, job_id, owner_scope, revision),
    ).fetchone()
    if row is None:
        raise SQLiteForgeCoverageError("terminal_job_mismatch")
    record = record_from_json(str(row["record_json"]))
    if type(record) is not IndexJobRecord:
        raise SQLiteForgeCoverageError("terminal_job_mismatch")
    return cast(IndexJobRecord, record)


def _verify_persisted_attempt_receipt(
    manifest: CoverageManifest,
    *,
    running_job: IndexJobRecord,
    terminal_job: IndexJobRecord,
    truth_snapshot: StoreSnapshot,
    attempt_revision: int,
    terminal_revision: int | None = None,
) -> None:
    """Bind an immutable receipt to its persisted T and P job records."""
    try:
        if type(manifest) is not CoverageManifest or type(running_job) is not IndexJobRecord or type(terminal_job) is not IndexJobRecord:
            raise ValueError
        attempt = manifest.attempt
        from src.unified_source_index_jobs import JobStage, _decode_checkpoint

        checkpoint = _decode_checkpoint(running_job.cursor, running_job, None)
        terminal_checkpoint = _decode_checkpoint(terminal_job.cursor, terminal_job, None)
        authority = (
            attempt.adapter_id,
            attempt.adapter_version,
            attempt.adapter_generation,
            attempt.admission_policy_generation,
        )
        if (
            running_job.status.value != "running"
            or running_job.job_id != attempt.job_id
            or running_job.owner_scope != manifest.owner_scope
            or running_job.source_scope.scope_id != attempt.source_scope_id
            or running_job.source_scope.source_ids != attempt.source_ids
            or running_job.source_scope.source_version_ids != attempt.source_version_ids
            or running_job.attempt_count != attempt.attempt_count
            or checkpoint.stage is not JobStage.TRUTH_COMMIT
            or checkpoint.adapter_id != attempt.adapter_id
            or checkpoint.adapter_version != attempt.adapter_version
            or terminal_job.status.value != "completed"
            or replace(
                running_job,
                status=terminal_job.status,
                cursor=terminal_job.cursor,
                completed_at=terminal_job.completed_at,
            ) != terminal_job
            or terminal_checkpoint != replace(
                checkpoint,
                stage=JobStage.COMPLETE,
                lease_owner="",
                lease_expires_at="",
                error_code="",
            )
            or attempt.job_status.value != "completed"
            or attempt.job_record_digest
            != "sha256:" + hashlib.sha256(terminal_job.to_json().encode("utf-8")).hexdigest()
            or attempt.store_revision != attempt_revision
            or manifest.authority_binding != authority
        ):
            raise SQLiteForgeCoverageError("terminal_job_mismatch")
    except SQLiteForgeCoverageError:
        raise
    except Exception:
        raise SQLiteForgeCoverageError("terminal_job_mismatch") from None
    if (
        type(truth_snapshot) is not StoreSnapshot
        or (
            truth_snapshot.revision,
            truth_snapshot.state_hash,
            truth_snapshot.record_count,
            truth_snapshot.tombstone_count,
            truth_snapshot.snapshot_ref,
        )
        != (
            attempt.store_revision,
            attempt.store_state_hash,
            attempt.store_record_count,
            attempt.store_tombstone_count,
            attempt.store_snapshot_ref,
        )
    ):
        raise SQLiteForgeCoverageError("truth_snapshot_mismatch")
    if terminal_revision is not None and terminal_revision <= attempt_revision:
        raise SQLiteForgeCoverageError("terminal_job_mismatch")


def _verify_selection_event_receipt(
    event: sqlite3.Row,
    manifest: CoverageManifest,
    published_revision: int,
) -> None:
    attempt = manifest.attempt
    if (
        str(event["event_kind"]) != "select"
        or str(event["owner_scope"]) != manifest.owner_scope
        or str(event["repo_id"]) != manifest.repo_id
        or str(event["coverage_id"]) != manifest.coverage_id
        or str(event["forge_revision_id"]) != manifest.forge_revision_id
        or str(event["commit_sha"]) != manifest.commit_sha
        or str(event["forge_snapshot_digest"]) != manifest.forge_snapshot_digest
        or str(event["forge_manifest_digest"]) != manifest.forge_manifest_digest
        or str(event["authority_binding"]) != canonical_json(list(manifest.authority_binding))
        or str(event["indexing_policy_generation"]) != attempt.indexing_policy_generation
        or str(event["reason_code"]) != ""
        or int(event["event_revision"]) != published_revision
    ):
        raise SQLiteForgeCoverageError("terminal_job_mismatch")


def _validate_selection_event_row(row: sqlite3.Row) -> None:
    values = _selection_event_values(
        event_revision=int(row["event_revision"]), owner_scope=str(row["owner_scope"]),
        repo_id=str(row["repo_id"]), event_kind=str(row["event_kind"]),
        coverage_id=None if row["coverage_id"] is None else str(row["coverage_id"]),
        forge_revision_id=str(row["forge_revision_id"]), commit_sha=str(row["commit_sha"]),
        forge_snapshot_digest=str(row["forge_snapshot_digest"]),
        forge_manifest_digest=str(row["forge_manifest_digest"]),
        authority_binding=str(row["authority_binding"]),
        indexing_policy_generation=str(row["indexing_policy_generation"]),
        reason_code=str(row["reason_code"]), previous_event_digest=str(row["previous_event_digest"]),
    )
    digest = "sha256:" + hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest()
    if digest != str(row["event_digest"]):
        raise SQLiteForgeCoverageError("durability_corrupt")


def _replay_selection_events(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
    heads: dict[tuple[str, str], str] = {}
    rows = connection.execute(
        "SELECT * FROM usi_forge_coverage_selection_events ORDER BY event_revision"
    ).fetchall()
    for row in rows:
        _validate_selection_event_row(row)
        key = (str(row["owner_scope"]), str(row["repo_id"]))
        if str(row["previous_event_digest"]) != heads.get(key, ""):
            raise SQLiteForgeCoverageError("durability_corrupt")
        heads[key] = str(row["event_digest"])
    return heads


def _apply_coverage_publication(
    connection: sqlite3.Connection,
    manifest: CoverageManifest,
    *, expected_ledger_head_digest: str,
    expected_selection_event_digest: str,
    published_revision: int,
) -> None:
    ledger = _replay_coverage_ledger(connection)
    if ledger.head_digest != expected_ledger_head_digest:
        raise SQLiteForgeCoverageError("manifest_head_conflict")
    latest = connection.execute(
        """SELECT coverage_id FROM usi_forge_coverage_manifests
           WHERE scope_digest = ? ORDER BY sequence DESC LIMIT 1""",
        (manifest.scope_digest(),),
    ).fetchone()
    supersedes = "" if latest is None else str(latest["coverage_id"])
    try:
        entry = ledger.append(manifest, expected_head_digest=expected_ledger_head_digest,
                              supersedes_coverage_id=supersedes)
    except CoverageLedgerError:
        raise SQLiteForgeCoverageError("manifest_head_conflict") from None
    attempt = manifest.attempt
    connection.execute(
        """INSERT INTO usi_forge_coverage_manifests(
           sequence, coverage_id, owner_scope, repo_id, scope_digest, supersedes_coverage_id,
           previous_entry_digest, entry_digest, canonical_manifest, manifest_digest, published_revision)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (entry.sequence, entry.coverage_id, manifest.owner_scope, manifest.repo_id,
         entry.scope_digest, entry.supersedes_coverage_id, entry.previous_entry_digest,
         entry.entry_digest, entry.canonical_bytes, manifest.manifest_digest, published_revision),
    )
    pairs = _validate_manifest_occurrences(connection, manifest, attempt.store_revision)
    connection.executemany(
        "INSERT INTO usi_forge_coverage_occurrences(coverage_id, occurrence_id, chunk_id) VALUES (?, ?, ?)",
        ((manifest.coverage_id, occurrence_id, chunk_id) for occurrence_id, chunk_id in pairs),
    )
    selection_heads = _replay_selection_events(connection)
    latest_event = connection.execute(
        """SELECT event_digest FROM usi_forge_coverage_selection_events
           WHERE owner_scope = ? AND repo_id = ? ORDER BY event_revision DESC LIMIT 1""",
        (manifest.owner_scope, manifest.repo_id),
    ).fetchone()
    actual_event_head = "" if latest_event is None else str(latest_event["event_digest"])
    if actual_event_head != expected_selection_event_digest:
        raise SQLiteForgeCoverageError("selection_head_conflict")
    authority = canonical_json(list(manifest.authority_binding))
    values = _selection_event_values(
        event_revision=published_revision, owner_scope=manifest.owner_scope,
        repo_id=manifest.repo_id, event_kind="select", coverage_id=manifest.coverage_id,
        forge_revision_id=manifest.forge_revision_id, commit_sha=manifest.commit_sha,
        forge_snapshot_digest=manifest.forge_snapshot_digest,
        forge_manifest_digest=manifest.forge_manifest_digest, authority_binding=authority,
        indexing_policy_generation=attempt.indexing_policy_generation, reason_code="",
        previous_event_digest=actual_event_head,
    )
    event_digest = "sha256:" + hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest()
    connection.execute(
        """INSERT INTO usi_forge_coverage_selection_events(
           event_revision, owner_scope, repo_id, event_kind, coverage_id, forge_revision_id,
           commit_sha, forge_snapshot_digest, forge_manifest_digest, authority_binding,
           indexing_policy_generation, reason_code, previous_event_digest, event_digest)
           VALUES (?, ?, ?, 'select', ?, ?, ?, ?, ?, ?, ?, '', ?, ?)""",
        (published_revision, manifest.owner_scope, manifest.repo_id, manifest.coverage_id,
         manifest.forge_revision_id, manifest.commit_sha, manifest.forge_snapshot_digest,
         manifest.forge_manifest_digest, authority, attempt.indexing_policy_generation,
         actual_event_head, event_digest),
    )


__all__ = [
    "ChunkSearchHit",
    "MAX_FTS_QUERY_CHARS",
    "MAX_FTS_RESULTS",
    "SQLiteStoreReadTransaction",
    "SQLiteStoreWriteTransaction",
    "SQLiteForgeCoverageError",
    "SelectedForgeCoverage",
    "SQLiteUnifiedSourceIndexError",
    "SQLiteUnifiedSourceIndexStore",
]

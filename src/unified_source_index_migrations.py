"""Isolated SQLite schema migrations for the Unified Source Index.

This module never touches Odysseus' global application migrations. Callers
must provide an explicit USI-owned SQLite connection, normally backed by a
temporary database during USI-03 verification.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3
from typing import Final

from src.unified_source_index_contract import canonical_json
from src.unified_source_index_stores import StoreSnapshot


USI_SQLITE_SCHEMA_VERSION: Final = 1
USI_SQLITE_APPLICATION_ID: Final = 0x4F445955  # "ODYU"
DEFAULT_BUSY_TIMEOUT_MS: Final = 5_000


class UnifiedSourceIndexMigrationError(RuntimeError):
    """Raised when a USI-only schema migration cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class SQLitePragmas:
    journal_mode: str
    foreign_keys: bool
    busy_timeout_ms: int
    application_id: int
    schema_version: int


_TABLE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE usi_store_state (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        revision INTEGER NOT NULL CHECK (revision >= 0),
        snapshot_ref TEXT NOT NULL UNIQUE,
        state_hash TEXT NOT NULL,
        record_count INTEGER NOT NULL CHECK (record_count >= 0),
        tombstone_count INTEGER NOT NULL CHECK (tombstone_count >= 0)
    )
    """,
    """
    CREATE TABLE usi_snapshots (
        revision INTEGER PRIMARY KEY CHECK (revision >= 0),
        snapshot_ref TEXT NOT NULL UNIQUE,
        state_hash TEXT NOT NULL,
        record_count INTEGER NOT NULL CHECK (record_count >= 0),
        tombstone_count INTEGER NOT NULL CHECK (tombstone_count >= 0)
    )
    """,
    """
    CREATE TABLE usi_sources (
        source_id TEXT PRIMARY KEY,
        owner_scope TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        canonical_ref TEXT NOT NULL,
        provider_ref TEXT NOT NULL,
        classification TEXT NOT NULL,
        content_policy TEXT NOT NULL,
        record_json TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        record_revision INTEGER NOT NULL CHECK (record_revision > 0),
        UNIQUE (owner_scope, source_kind, canonical_ref)
    )
    """,
    """
    CREATE TABLE usi_source_versions (
        source_version_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        owner_scope TEXT NOT NULL,
        revision_ref TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        record_json TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        record_revision INTEGER NOT NULL CHECK (record_revision > 0),
        FOREIGN KEY (source_id) REFERENCES usi_sources(source_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT,
        UNIQUE (source_version_id, source_id),
        UNIQUE (source_id, revision_ref, content_hash)
    )
    """,
    """
    CREATE TABLE usi_chunks (
        chunk_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
        chunk_id TEXT NOT NULL UNIQUE,
        source_version_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        owner_scope TEXT NOT NULL,
        locator_kind TEXT NOT NULL,
        content TEXT,
        content_hash TEXT NOT NULL,
        record_json TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        record_revision INTEGER NOT NULL CHECK (record_revision > 0),
        FOREIGN KEY (source_version_id, source_id)
            REFERENCES usi_source_versions(source_version_id, source_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE usi_entities (
        entity_id TEXT PRIMARY KEY,
        source_version_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        owner_scope TEXT NOT NULL,
        entity_kind TEXT NOT NULL,
        locator_kind TEXT NOT NULL,
        label TEXT NOT NULL,
        record_json TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        record_revision INTEGER NOT NULL CHECK (record_revision > 0),
        FOREIGN KEY (source_version_id, source_id)
            REFERENCES usi_source_versions(source_version_id, source_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE usi_relations (
        relation_id TEXT PRIMARY KEY,
        owner_scope TEXT NOT NULL,
        relation_kind TEXT NOT NULL,
        source_record_kind TEXT NOT NULL,
        source_record_id TEXT NOT NULL,
        target_record_kind TEXT NOT NULL,
        target_record_id TEXT NOT NULL,
        record_json TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        record_revision INTEGER NOT NULL CHECK (record_revision > 0)
    )
    """,
    """
    CREATE TABLE usi_lineage (
        lineage_id TEXT PRIMARY KEY,
        owner_scope TEXT NOT NULL,
        previous_chunk_id TEXT NOT NULL,
        current_chunk_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
        record_json TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        record_revision INTEGER NOT NULL CHECK (record_revision > 0),
        FOREIGN KEY (previous_chunk_id) REFERENCES usi_chunks(chunk_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT,
        FOREIGN KEY (current_chunk_id) REFERENCES usi_chunks(chunk_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT,
        CHECK (previous_chunk_id <> current_chunk_id)
    )
    """,
    """
    CREATE TABLE usi_projection_manifests (
        projection_id TEXT PRIMARY KEY,
        owner_scope TEXT NOT NULL,
        projection_kind TEXT NOT NULL,
        input_snapshot_ref TEXT NOT NULL,
        output_generation_ref TEXT NOT NULL,
        record_json TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        record_revision INTEGER NOT NULL CHECK (record_revision > 0)
    )
    """,
    """
    CREATE TABLE usi_derived_runs (
        derived_run_id TEXT PRIMARY KEY,
        owner_scope TEXT NOT NULL,
        derived_kind TEXT NOT NULL,
        source_scope_id TEXT NOT NULL,
        input_snapshot_ref TEXT NOT NULL,
        record_json TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        record_revision INTEGER NOT NULL CHECK (record_revision > 0)
    )
    """,
    """
    CREATE TABLE usi_jobs (
        job_id TEXT PRIMARY KEY,
        owner_scope TEXT NOT NULL,
        job_kind TEXT NOT NULL,
        source_scope_id TEXT NOT NULL,
        status TEXT NOT NULL,
        cursor TEXT NOT NULL,
        record_json TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        record_revision INTEGER NOT NULL CHECK (record_revision > 0)
    )
    """,
    """
    CREATE TABLE usi_tombstones (
        record_kind TEXT NOT NULL,
        record_id TEXT NOT NULL,
        owner_scope TEXT NOT NULL,
        previous_revision INTEGER NOT NULL CHECK (previous_revision > 0),
        revision INTEGER NOT NULL CHECK (revision > previous_revision),
        reason TEXT NOT NULL,
        PRIMARY KEY (record_kind, record_id)
    )
    """,
    """
    CREATE TABLE usi_record_history (
        record_kind TEXT NOT NULL,
        record_id TEXT NOT NULL,
        owner_scope TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        state TEXT NOT NULL CHECK (state IN ('record', 'tombstone')),
        record_json TEXT,
        record_hash TEXT,
        previous_revision INTEGER,
        reason TEXT,
        PRIMARY KEY (record_kind, record_id, revision),
        CHECK (
            (state = 'record' AND record_json IS NOT NULL
                AND record_hash IS NOT NULL AND previous_revision IS NULL
                AND reason IS NULL)
            OR
            (state = 'tombstone' AND record_json IS NULL
                AND record_hash IS NULL AND previous_revision IS NOT NULL
                AND reason IS NOT NULL)
        )
    )
    """,
)

_INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX usi_sources_owner_kind ON usi_sources(owner_scope, source_kind, source_id)",
    "CREATE INDEX usi_versions_source ON usi_source_versions(source_id, source_version_id)",
    "CREATE INDEX usi_versions_owner ON usi_source_versions(owner_scope, source_version_id)",
    "CREATE INDEX usi_chunks_version ON usi_chunks(source_version_id, chunk_id)",
    "CREATE INDEX usi_chunks_owner ON usi_chunks(owner_scope, chunk_id)",
    "CREATE INDEX usi_entities_version ON usi_entities(source_version_id, entity_id)",
    "CREATE INDEX usi_entities_owner_kind ON usi_entities(owner_scope, entity_kind, entity_id)",
    "CREATE INDEX usi_relations_owner_kind ON usi_relations(owner_scope, relation_kind, relation_id)",
    "CREATE INDEX usi_lineage_owner ON usi_lineage(owner_scope, lineage_id)",
    "CREATE INDEX usi_jobs_owner_status ON usi_jobs(owner_scope, status, job_id)",
    "CREATE INDEX usi_history_snapshot ON usi_record_history(record_kind, owner_scope, revision, record_id)",
    "CREATE INDEX usi_history_record ON usi_record_history(record_kind, record_id, revision DESC)",
)

_FTS_STATEMENT = """
CREATE VIRTUAL TABLE usi_chunk_fts USING fts5(
    content,
    content='usi_chunks',
    content_rowid='chunk_rowid',
    tokenize='unicode61'
)
"""

_TRIGGER_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TRIGGER usi_chunks_fts_insert AFTER INSERT ON usi_chunks BEGIN
        INSERT INTO usi_chunk_fts(rowid, content)
        VALUES (new.chunk_rowid, coalesce(new.content, ''));
    END
    """,
    """
    CREATE TRIGGER usi_chunks_fts_delete AFTER DELETE ON usi_chunks BEGIN
        INSERT INTO usi_chunk_fts(usi_chunk_fts, rowid, content)
        VALUES ('delete', old.chunk_rowid, coalesce(old.content, ''));
    END
    """,
    """
    CREATE TRIGGER usi_chunks_fts_update AFTER UPDATE OF content ON usi_chunks BEGIN
        INSERT INTO usi_chunk_fts(usi_chunk_fts, rowid, content)
        VALUES ('delete', old.chunk_rowid, coalesce(old.content, ''));
        INSERT INTO usi_chunk_fts(rowid, content)
        VALUES (new.chunk_rowid, coalesce(new.content, ''));
    END
    """,
)

_REQUIRED_TABLES = frozenset(
    {
        "usi_store_state",
        "usi_snapshots",
        "usi_sources",
        "usi_source_versions",
        "usi_chunks",
        "usi_entities",
        "usi_relations",
        "usi_lineage",
        "usi_projection_manifests",
        "usi_derived_runs",
        "usi_jobs",
        "usi_tombstones",
        "usi_record_history",
        "usi_chunk_fts",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "usi_chunks_fts_insert",
        "usi_chunks_fts_delete",
        "usi_chunks_fts_update",
    }
)


def configure_sqlite_connection(
    connection: sqlite3.Connection,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    wal: bool = True,
) -> SQLitePragmas:
    """Apply the mandatory per-connection safety/runtime pragmas."""

    timeout = _bounded_timeout(busy_timeout_ms)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {timeout}")
    if wal:
        connection.execute("PRAGMA journal_mode = WAL")
    pragmas = read_sqlite_pragmas(connection)
    if not pragmas.foreign_keys:
        raise UnifiedSourceIndexMigrationError(
            "SQLite foreign-key enforcement could not be enabled"
        )
    if pragmas.busy_timeout_ms != timeout:
        raise UnifiedSourceIndexMigrationError(
            "SQLite busy timeout could not be configured"
        )
    if wal and pragmas.journal_mode != "wal":
        raise UnifiedSourceIndexMigrationError("SQLite WAL mode could not be enabled")
    return pragmas


def read_sqlite_pragmas(connection: sqlite3.Connection) -> SQLitePragmas:
    return SQLitePragmas(
        journal_mode=str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
        foreign_keys=bool(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
        busy_timeout_ms=int(connection.execute("PRAGMA busy_timeout").fetchone()[0]),
        application_id=int(connection.execute("PRAGMA application_id").fetchone()[0]),
        schema_version=current_schema_version(connection),
    )


def current_schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def upgrade_schema(
    connection: sqlite3.Connection,
    *,
    target_version: int = USI_SQLITE_SCHEMA_VERSION,
) -> int:
    """Upgrade an empty/older USI database atomically to ``target_version``."""

    current = current_schema_version(connection)
    if target_version != USI_SQLITE_SCHEMA_VERSION:
        raise UnifiedSourceIndexMigrationError(
            f"unsupported USI target schema version: {target_version}"
        )
    if current == target_version:
        validate_schema(connection)
        return current
    if current != 0:
        raise UnifiedSourceIndexMigrationError(
            f"unsupported USI source schema version: {current}"
        )
    _require_idle_connection(connection)

    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in _TABLE_STATEMENTS:
            connection.execute(statement)
        for statement in _INDEX_STATEMENTS:
            connection.execute(statement)
        connection.execute(_FTS_STATEMENT)
        for statement in _TRIGGER_STATEMENTS:
            connection.execute(statement)

        initial = _initial_snapshot()
        values = (
            initial.revision,
            initial.snapshot_ref,
            initial.state_hash,
            initial.record_count,
            initial.tombstone_count,
        )
        connection.execute(
            """
            INSERT INTO usi_store_state(
                singleton, revision, snapshot_ref, state_hash,
                record_count, tombstone_count
            ) VALUES (1, ?, ?, ?, ?, ?)
            """,
            values,
        )
        connection.execute(
            """
            INSERT INTO usi_snapshots(
                revision, snapshot_ref, state_hash, record_count, tombstone_count
            ) VALUES (?, ?, ?, ?, ?)
            """,
            values,
        )
        connection.execute(f"PRAGMA application_id = {USI_SQLITE_APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version = {USI_SQLITE_SCHEMA_VERSION}")
        connection.commit()
    except (sqlite3.DatabaseError, ValueError) as exc:
        connection.rollback()
        raise UnifiedSourceIndexMigrationError("USI schema upgrade failed") from exc

    validate_schema(connection)
    return current_schema_version(connection)


def downgrade_schema(
    connection: sqlite3.Connection,
    *,
    target_version: int = 0,
) -> int:
    """Drop only the isolated USI schema; intended for temp-db proof/rollback."""

    if target_version != 0:
        raise UnifiedSourceIndexMigrationError(
            f"unsupported USI downgrade target: {target_version}"
        )
    current = current_schema_version(connection)
    if current == 0:
        return 0
    if current != USI_SQLITE_SCHEMA_VERSION:
        raise UnifiedSourceIndexMigrationError(
            f"unsupported USI source schema version: {current}"
        )
    validate_schema(connection)
    _require_idle_connection(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for trigger in sorted(_REQUIRED_TRIGGERS):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute("DROP TABLE IF EXISTS usi_chunk_fts")
        for table in (
            "usi_record_history",
            "usi_tombstones",
            "usi_jobs",
            "usi_derived_runs",
            "usi_projection_manifests",
            "usi_lineage",
            "usi_relations",
            "usi_entities",
            "usi_chunks",
            "usi_source_versions",
            "usi_sources",
            "usi_snapshots",
            "usi_store_state",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute("PRAGMA application_id = 0")
        connection.execute("PRAGMA user_version = 0")
        connection.commit()
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        raise UnifiedSourceIndexMigrationError("USI schema downgrade failed") from exc
    return current_schema_version(connection)


def rebuild_chunk_fts(connection: sqlite3.Connection) -> None:
    """Rebuild the external-content FTS5 shadow from current chunk truth."""

    validate_schema(connection)
    connection.execute("INSERT INTO usi_chunk_fts(usi_chunk_fts) VALUES ('rebuild')")


def validate_schema(connection: sqlite3.Connection) -> None:
    """Fail closed on missing objects, wrong version, or FK corruption."""

    version = current_schema_version(connection)
    if version != USI_SQLITE_SCHEMA_VERSION:
        raise UnifiedSourceIndexMigrationError(
            f"expected USI schema version {USI_SQLITE_SCHEMA_VERSION}, got {version}"
        )
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    if application_id != USI_SQLITE_APPLICATION_ID:
        raise UnifiedSourceIndexMigrationError(
            "SQLite database is not identified as an isolated USI store"
        )
    rows = connection.execute(
        "SELECT type, name FROM sqlite_master WHERE name LIKE 'usi_%'"
    ).fetchall()
    tables = {str(row[1]) for row in rows if row[0] == "table"}
    triggers = {str(row[1]) for row in rows if row[0] == "trigger"}
    missing_tables = _REQUIRED_TABLES - tables
    missing_triggers = _REQUIRED_TRIGGERS - triggers
    if missing_tables or missing_triggers:
        missing = sorted((*missing_tables, *missing_triggers))
        raise UnifiedSourceIndexMigrationError(
            f"USI schema objects are missing: {', '.join(missing)}"
        )
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise UnifiedSourceIndexMigrationError("USI foreign-key check failed")


def _initial_snapshot() -> StoreSnapshot:
    state_hash = "sha256:" + hashlib.sha256(canonical_json([]).encode("utf-8")).hexdigest()
    return StoreSnapshot(0, state_hash, 0, 0)


def _bounded_timeout(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 60_000:
        raise UnifiedSourceIndexMigrationError(
            "busy_timeout_ms must be an integer between 1 and 60000"
        )
    return value


def _require_idle_connection(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise UnifiedSourceIndexMigrationError(
            "schema migration requires an idle SQLite connection"
        )


__all__ = [
    "DEFAULT_BUSY_TIMEOUT_MS",
    "SQLitePragmas",
    "USI_SQLITE_APPLICATION_ID",
    "USI_SQLITE_SCHEMA_VERSION",
    "UnifiedSourceIndexMigrationError",
    "configure_sqlite_connection",
    "current_schema_version",
    "downgrade_schema",
    "read_sqlite_pragmas",
    "rebuild_chunk_fts",
    "upgrade_schema",
    "validate_schema",
]

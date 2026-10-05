"""Isolated SQLite schema migrations for the Unified Source Index.

This module never touches Odysseus' global application migrations. Callers
must provide an explicit USI-owned SQLite connection, normally backed by a
temporary database during USI-03 verification.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Final

from src.unified_source_index_contract import canonical_json
from src.unified_source_index_stores import StoreSnapshot


USI_SQLITE_SCHEMA_VERSION: Final = 2
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

_FORGE_SIDECAR_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE usi_forge_occurrence_evidence (
        chunk_id TEXT PRIMARY KEY,
        occurrence_id TEXT NOT NULL UNIQUE,
        source_id TEXT NOT NULL,
        source_version_id TEXT NOT NULL,
        owner_scope TEXT NOT NULL,
        repo_id TEXT NOT NULL,
        canonical_evidence BLOB NOT NULL,
        evidence_hash TEXT NOT NULL,
        record_revision INTEGER NOT NULL CHECK (record_revision > 0),
        FOREIGN KEY (chunk_id) REFERENCES usi_chunks(chunk_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT,
        FOREIGN KEY (source_id) REFERENCES usi_sources(source_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT,
        FOREIGN KEY (source_version_id, source_id)
            REFERENCES usi_source_versions(source_version_id, source_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE usi_forge_occurrence_history (
        chunk_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        state TEXT NOT NULL CHECK (state IN ('evidence', 'deleted')),
        canonical_evidence BLOB,
        evidence_hash TEXT,
        occurrence_id TEXT,
        source_id TEXT,
        source_version_id TEXT,
        owner_scope TEXT,
        repo_id TEXT,
        PRIMARY KEY (chunk_id, revision),
        FOREIGN KEY (revision) REFERENCES usi_snapshots(revision)
            ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
        CHECK ((state = 'evidence' AND canonical_evidence IS NOT NULL
            AND evidence_hash IS NOT NULL AND occurrence_id IS NOT NULL
            AND source_id IS NOT NULL AND source_version_id IS NOT NULL
            AND owner_scope IS NOT NULL AND repo_id IS NOT NULL)
          OR (state = 'deleted' AND canonical_evidence IS NULL
            AND evidence_hash IS NULL AND occurrence_id IS NULL
            AND source_id IS NULL AND source_version_id IS NULL
            AND owner_scope IS NULL AND repo_id IS NULL))
    )
    """,
    """
    CREATE TABLE usi_forge_coverage_manifests (
        sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
        coverage_id TEXT NOT NULL UNIQUE,
        owner_scope TEXT NOT NULL,
        repo_id TEXT NOT NULL,
        scope_digest TEXT NOT NULL,
        supersedes_coverage_id TEXT NOT NULL,
        previous_entry_digest TEXT NOT NULL,
        entry_digest TEXT NOT NULL UNIQUE,
        canonical_manifest BLOB NOT NULL,
        manifest_digest TEXT NOT NULL,
        published_revision INTEGER NOT NULL CHECK (published_revision > 0),
        FOREIGN KEY (published_revision) REFERENCES usi_snapshots(revision)
            ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
    )
    """,
    """
    CREATE TABLE usi_forge_coverage_occurrences (
        coverage_id TEXT NOT NULL,
        occurrence_id TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        PRIMARY KEY (coverage_id, occurrence_id),
        UNIQUE (coverage_id, chunk_id),
        FOREIGN KEY (coverage_id) REFERENCES usi_forge_coverage_manifests(coverage_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE usi_forge_coverage_selection_events (
        event_revision INTEGER PRIMARY KEY CHECK (event_revision > 0),
        owner_scope TEXT NOT NULL,
        repo_id TEXT NOT NULL,
        event_kind TEXT NOT NULL CHECK (event_kind IN ('select', 'invalidate')),
        coverage_id TEXT,
        forge_revision_id TEXT NOT NULL,
        commit_sha TEXT NOT NULL,
        forge_snapshot_digest TEXT NOT NULL,
        forge_manifest_digest TEXT NOT NULL,
        authority_binding TEXT NOT NULL,
        indexing_policy_generation TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        previous_event_digest TEXT NOT NULL,
        event_digest TEXT NOT NULL UNIQUE,
        FOREIGN KEY (event_revision) REFERENCES usi_snapshots(revision)
            ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY (coverage_id) REFERENCES usi_forge_coverage_manifests(coverage_id)
            ON UPDATE RESTRICT ON DELETE RESTRICT,
        CHECK ((event_kind = 'select' AND coverage_id IS NOT NULL AND reason_code = '')
          OR (event_kind = 'invalidate' AND coverage_id IS NULL AND reason_code <> ''))
    )
    """,
)

_FORGE_SIDECAR_INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX usi_forge_occurrence_scope ON usi_forge_occurrence_evidence(owner_scope, repo_id, chunk_id)",
    "CREATE INDEX usi_forge_occurrence_history_snapshot ON usi_forge_occurrence_history(owner_scope, repo_id, revision, chunk_id)",
    "CREATE INDEX usi_forge_coverage_scope ON usi_forge_coverage_manifests(owner_scope, repo_id, sequence)",
    "CREATE INDEX usi_forge_selection_scope ON usi_forge_coverage_selection_events(owner_scope, repo_id, event_revision DESC)",
)

_FORGE_SIDECAR_TABLES = frozenset({
    "usi_forge_occurrence_evidence", "usi_forge_occurrence_history",
    "usi_forge_coverage_manifests", "usi_forge_coverage_occurrences",
    "usi_forge_coverage_selection_events",
})
_FORGE_SIDECAR_INDEXES = frozenset({
    "usi_forge_occurrence_scope", "usi_forge_occurrence_history_snapshot",
    "usi_forge_coverage_scope", "usi_forge_selection_scope",
})
_FORGE_SIDECAR_TABLE_DDL: Final[dict[str, str]] = dict(zip(
    (
        "usi_forge_occurrence_evidence",
        "usi_forge_occurrence_history",
        "usi_forge_coverage_manifests",
        "usi_forge_coverage_occurrences",
        "usi_forge_coverage_selection_events",
    ),
    _FORGE_SIDECAR_STATEMENTS,
))
_FORGE_SIDECAR_INDEX_DDL: Final[dict[str, str]] = dict(zip(
    (
        "usi_forge_occurrence_scope",
        "usi_forge_occurrence_history_snapshot",
        "usi_forge_coverage_scope",
        "usi_forge_selection_scope",
    ),
    _FORGE_SIDECAR_INDEX_STATEMENTS,
))
_SIDECAR_INDEX_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "usi_forge_occurrence_scope": ("owner_scope", "repo_id", "chunk_id"),
    "usi_forge_occurrence_history_snapshot": ("owner_scope", "repo_id", "revision", "chunk_id"),
    "usi_forge_coverage_scope": ("owner_scope", "repo_id", "sequence"),
    "usi_forge_selection_scope": ("owner_scope", "repo_id", "event_revision"),
}
_SIDECAR_INDEX_DIRECTIONS: Final[dict[str, tuple[bool, ...]]] = {
    "usi_forge_occurrence_scope": (False, False, False),
    "usi_forge_occurrence_history_snapshot": (False, False, False, False),
    "usi_forge_coverage_scope": (False, False, False),
    "usi_forge_selection_scope": (False, False, True),
}
_SIDECAR_FOREIGN_KEYS: Final[dict[str, tuple[tuple[str, str, str], ...]]] = {
    "usi_forge_occurrence_evidence": (
        ("usi_chunks", "chunk_id", "chunk_id"),
        ("usi_sources", "source_id", "source_id"),
        ("usi_source_versions", "source_version_id", "source_version_id"),
        ("usi_source_versions", "source_id", "source_id"),
    ),
    "usi_forge_occurrence_history": (("usi_snapshots", "revision", "revision"),),
    "usi_forge_coverage_manifests": (("usi_snapshots", "published_revision", "revision"),),
    "usi_forge_coverage_occurrences": (("usi_forge_coverage_manifests", "coverage_id", "coverage_id"),),
    "usi_forge_coverage_selection_events": (
        ("usi_snapshots", "event_revision", "revision"),
        ("usi_forge_coverage_manifests", "coverage_id", "coverage_id"),
    ),
}

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
    if current == 1 and target_version == 2:
        validate_schema(connection)
        _reject_legacy_forge_footprint(connection)
        _require_idle_connection(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            _create_forge_sidecars(connection)
            connection.execute("PRAGMA user_version = 2")
            validate_schema(connection)
            connection.commit()
        except (sqlite3.DatabaseError, ValueError, UnifiedSourceIndexMigrationError) as exc:
            connection.rollback()
            raise UnifiedSourceIndexMigrationError("USI schema upgrade failed") from exc
        return 2
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
        _create_forge_sidecars(connection)

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
        validate_schema(connection)
        connection.commit()
    except (sqlite3.DatabaseError, ValueError, UnifiedSourceIndexMigrationError) as exc:
        connection.rollback()
        raise UnifiedSourceIndexMigrationError("USI schema upgrade failed") from exc

    return current_schema_version(connection)


def downgrade_schema(
    connection: sqlite3.Connection,
    *,
    target_version: int = 0,
) -> int:
    """Drop only the isolated USI schema; intended for temp-db proof/rollback."""

    if target_version not in {0, 1}:
        raise UnifiedSourceIndexMigrationError(
            f"unsupported USI downgrade target: {target_version}"
        )
    current = current_schema_version(connection)
    if current == 0:
        return 0
    if current == 2 and target_version == 1:
        validate_schema(connection)
        _require_empty_forge_sidecars(connection)
        state = connection.execute(
            "SELECT revision, record_count, tombstone_count FROM usi_store_state WHERE singleton = 1"
        ).fetchone()
        if state is None or tuple(map(int, state)) != (0, 0, 0):
            raise UnifiedSourceIndexMigrationError("USI schema downgrade requires a virgin store")
        _require_idle_connection(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            _drop_forge_sidecars(connection)
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise UnifiedSourceIndexMigrationError("USI schema downgrade failed") from exc
        validate_schema(connection)
        return 1
    if current != USI_SQLITE_SCHEMA_VERSION:
        raise UnifiedSourceIndexMigrationError(
            f"unsupported USI source schema version: {current}"
        )
    validate_schema(connection)
    _require_idle_connection(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if current == 2:
            _drop_forge_sidecars(connection)
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
    if version not in {1, USI_SQLITE_SCHEMA_VERSION}:
        raise UnifiedSourceIndexMigrationError(
            f"unsupported USI schema version: {version}"
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
    required_tables = _REQUIRED_TABLES | (_FORGE_SIDECAR_TABLES if version == 2 else frozenset())
    missing_tables = required_tables - tables
    missing_triggers = _REQUIRED_TRIGGERS - triggers
    if missing_tables or missing_triggers:
        missing = sorted((*missing_tables, *missing_triggers))
        raise UnifiedSourceIndexMigrationError(
            f"USI schema objects are missing: {', '.join(missing)}"
        )
    if version == 2:
        _validate_forge_sidecar_schema(connection)
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise UnifiedSourceIndexMigrationError("USI foreign-key check failed")


def _initial_snapshot() -> StoreSnapshot:
    state_hash = "sha256:" + hashlib.sha256(canonical_json([]).encode("utf-8")).hexdigest()
    return StoreSnapshot(0, state_hash, 0, 0)


def _create_forge_sidecars(connection: sqlite3.Connection) -> None:
    for statement in _FORGE_SIDECAR_STATEMENTS:
        connection.execute(statement)
    for statement in _FORGE_SIDECAR_INDEX_STATEMENTS:
        connection.execute(statement)


def _drop_forge_sidecars(connection: sqlite3.Connection) -> None:
    for table in (
        "usi_forge_coverage_selection_events", "usi_forge_coverage_occurrences",
        "usi_forge_coverage_manifests", "usi_forge_occurrence_history",
        "usi_forge_occurrence_evidence",
    ):
        connection.execute(f"DROP TABLE {table}")


def _require_empty_forge_sidecars(connection: sqlite3.Connection) -> None:
    for table in sorted(_FORGE_SIDECAR_TABLES):
        if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
            raise UnifiedSourceIndexMigrationError("USI schema downgrade requires empty Forge sidecars")


def _reject_legacy_forge_footprint(connection: sqlite3.Connection) -> None:
    for provider_ref, canonical_ref, record_json in connection.execute(
        "SELECT provider_ref, canonical_ref, record_json FROM usi_sources"
    ):
        if provider_ref == "forge.code" or _has_forge_prefix(canonical_ref, "forge-code-source:"):
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge footprint requires explicit recovery"
            )
        payload = _legacy_record_payload(record_json, _LEGACY_SOURCE_SCHEMA)
        structured_provider = _required_structural_string(payload, "provider_ref")
        structured_ref = _required_structural_string(payload, "canonical_ref")
        if (structured_provider, structured_ref) != (provider_ref, canonical_ref):
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge structural evidence is inconsistent"
            )
        if structured_provider == "forge.code" or _has_forge_prefix(
            structured_ref, "forge-code-source:"
        ):
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge footprint requires explicit recovery"
            )

    for revision_ref, record_json in connection.execute(
        "SELECT revision_ref, record_json FROM usi_source_versions"
    ):
        if _has_forge_prefix(revision_ref, "forge-code-version:"):
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge footprint requires explicit recovery"
            )
        payload = _legacy_record_payload(record_json, _LEGACY_SOURCE_VERSION_SCHEMA)
        structured_provider = _required_structural_string(payload, "provider_ref")
        structured_revision = _required_structural_string(payload, "revision_ref")
        if structured_revision != revision_ref:
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge structural evidence is inconsistent"
            )
        if structured_provider == "forge.code":
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge footprint requires explicit recovery"
            )
        if structured_provider == "forge.code" or _has_forge_prefix(
            structured_revision, "forge-code-version:"
        ):
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge footprint requires explicit recovery"
            )

    for locator_kind, record_json in connection.execute(
        "SELECT locator_kind, record_json FROM usi_chunks"
    ):
        payload = _legacy_record_payload(record_json, _LEGACY_CHUNK_SCHEMA)
        profile_ref = _required_structural_string(payload, "extractor_profile_ref")
        locator = payload.get("locator")
        if type(locator) is not dict or type(locator.get("kind")) is not str:
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge structural evidence is malformed"
            )
        if locator["kind"] != locator_kind:
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge structural evidence is inconsistent"
            )
        is_forge_profile = profile_ref.startswith("forge-code-")
        if is_forge_profile and locator_kind != "code_range":
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge structural evidence is malformed"
            )
        if is_forge_profile and locator_kind == "code_range":
            raise UnifiedSourceIndexMigrationError(
                "legacy Forge footprint requires explicit recovery"
            )

    for record_kind, record_json in connection.execute(
        "SELECT record_kind, record_json FROM usi_record_history WHERE state = 'record'"
    ):
        if record_kind == "source":
            payload = _legacy_record_payload(record_json, _LEGACY_SOURCE_SCHEMA)
            if (
                _required_structural_string(payload, "provider_ref") == "forge.code"
                or _has_forge_prefix(
                    _required_structural_string(payload, "canonical_ref"),
                    "forge-code-source:",
                )
            ):
                raise UnifiedSourceIndexMigrationError(
                    "legacy Forge footprint requires explicit recovery"
                )
        elif record_kind == "source_version":
            payload = _legacy_record_payload(record_json, _LEGACY_SOURCE_VERSION_SCHEMA)
            if (
                _required_structural_string(payload, "provider_ref") == "forge.code"
                or _has_forge_prefix(
                    _required_structural_string(payload, "revision_ref"),
                    "forge-code-version:",
                )
            ):
                raise UnifiedSourceIndexMigrationError(
                    "legacy Forge footprint requires explicit recovery"
                )
        elif record_kind == "chunk":
            payload = _legacy_record_payload(record_json, _LEGACY_CHUNK_SCHEMA)
            profile_ref = _required_structural_string(payload, "extractor_profile_ref")
            locator = payload.get("locator")
            if type(locator) is not dict or type(locator.get("kind")) is not str:
                raise UnifiedSourceIndexMigrationError(
                    "legacy Forge structural evidence is malformed"
                )
            is_forge_profile = profile_ref.startswith("forge-code-")
            if is_forge_profile and locator["kind"] != "code_range":
                raise UnifiedSourceIndexMigrationError(
                    "legacy Forge structural evidence is malformed"
                )
            if is_forge_profile and locator["kind"] == "code_range":
                raise UnifiedSourceIndexMigrationError(
                    "legacy Forge footprint requires explicit recovery"
                )


_LEGACY_SOURCE_SCHEMA: Final = "odysseus.unified_source_index.contract.v1.source"
_LEGACY_SOURCE_VERSION_SCHEMA: Final = "odysseus.unified_source_index.contract.v1.source_version"
_LEGACY_CHUNK_SCHEMA: Final = "odysseus.unified_source_index.contract.v1.chunk"
_LEGACY_FORGE_EVIDENCE_SCHEMA: Final = (
    "odysseus.unified_source_index.contract.v1.forge_code_occurrence_evidence"
)


def _legacy_record_payload(raw: object, expected_schema: str) -> dict[str, object]:
    try:
        payload = json.loads(raw) if type(raw) is str else None
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = None
    if type(payload) is not dict or type(payload.get("schema")) is not str:
        raise UnifiedSourceIndexMigrationError(
            "legacy Forge structural evidence is malformed"
        )
    schema = payload["schema"]
    if schema == _LEGACY_FORGE_EVIDENCE_SCHEMA:
        raise UnifiedSourceIndexMigrationError(
            "legacy Forge footprint requires explicit recovery"
        )
    if schema != expected_schema:
        raise UnifiedSourceIndexMigrationError(
            "legacy Forge structural evidence is malformed"
        )
    return payload


def _required_structural_string(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if type(value) is not str:
        raise UnifiedSourceIndexMigrationError(
            "legacy Forge structural evidence is malformed"
        )
    return value


def _has_forge_prefix(value: object, prefix: str) -> bool:
    if type(value) is not str:
        raise UnifiedSourceIndexMigrationError(
            "legacy Forge structural evidence is malformed"
        )
    return value.startswith(prefix)


_SIDECAR_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "usi_forge_occurrence_evidence": ("chunk_id", "occurrence_id", "source_id", "source_version_id", "owner_scope", "repo_id", "canonical_evidence", "evidence_hash", "record_revision"),
    "usi_forge_occurrence_history": ("chunk_id", "revision", "state", "canonical_evidence", "evidence_hash", "occurrence_id", "source_id", "source_version_id", "owner_scope", "repo_id"),
    "usi_forge_coverage_manifests": ("sequence", "coverage_id", "owner_scope", "repo_id", "scope_digest", "supersedes_coverage_id", "previous_entry_digest", "entry_digest", "canonical_manifest", "manifest_digest", "published_revision"),
    "usi_forge_coverage_occurrences": ("coverage_id", "occurrence_id", "chunk_id"),
    "usi_forge_coverage_selection_events": ("event_revision", "owner_scope", "repo_id", "event_kind", "coverage_id", "forge_revision_id", "commit_sha", "forge_snapshot_digest", "forge_manifest_digest", "authority_binding", "indexing_policy_generation", "reason_code", "previous_event_digest", "event_digest"),
}
_SIDECAR_COLUMN_SIGNATURES: Final[dict[str, tuple[tuple[str, str, int, object, int], ...]]] = {
    "usi_forge_occurrence_evidence": (
        ("chunk_id", "TEXT", 0, None, 1),
        ("occurrence_id", "TEXT", 1, None, 0),
        ("source_id", "TEXT", 1, None, 0),
        ("source_version_id", "TEXT", 1, None, 0),
        ("owner_scope", "TEXT", 1, None, 0),
        ("repo_id", "TEXT", 1, None, 0),
        ("canonical_evidence", "BLOB", 1, None, 0),
        ("evidence_hash", "TEXT", 1, None, 0),
        ("record_revision", "INTEGER", 1, None, 0),
    ),
    "usi_forge_occurrence_history": (
        ("chunk_id", "TEXT", 1, None, 1),
        ("revision", "INTEGER", 1, None, 2),
        ("state", "TEXT", 1, None, 0),
        ("canonical_evidence", "BLOB", 0, None, 0),
        ("evidence_hash", "TEXT", 0, None, 0),
        ("occurrence_id", "TEXT", 0, None, 0),
        ("source_id", "TEXT", 0, None, 0),
        ("source_version_id", "TEXT", 0, None, 0),
        ("owner_scope", "TEXT", 0, None, 0),
        ("repo_id", "TEXT", 0, None, 0),
    ),
    "usi_forge_coverage_manifests": (
        ("sequence", "INTEGER", 0, None, 1),
        ("coverage_id", "TEXT", 1, None, 0),
        ("owner_scope", "TEXT", 1, None, 0),
        ("repo_id", "TEXT", 1, None, 0),
        ("scope_digest", "TEXT", 1, None, 0),
        ("supersedes_coverage_id", "TEXT", 1, None, 0),
        ("previous_entry_digest", "TEXT", 1, None, 0),
        ("entry_digest", "TEXT", 1, None, 0),
        ("canonical_manifest", "BLOB", 1, None, 0),
        ("manifest_digest", "TEXT", 1, None, 0),
        ("published_revision", "INTEGER", 1, None, 0),
    ),
    "usi_forge_coverage_occurrences": (
        ("coverage_id", "TEXT", 1, None, 1),
        ("occurrence_id", "TEXT", 1, None, 2),
        ("chunk_id", "TEXT", 1, None, 0),
    ),
    "usi_forge_coverage_selection_events": (
        ("event_revision", "INTEGER", 0, None, 1),
        ("owner_scope", "TEXT", 1, None, 0),
        ("repo_id", "TEXT", 1, None, 0),
        ("event_kind", "TEXT", 1, None, 0),
        ("coverage_id", "TEXT", 0, None, 0),
        ("forge_revision_id", "TEXT", 1, None, 0),
        ("commit_sha", "TEXT", 1, None, 0),
        ("forge_snapshot_digest", "TEXT", 1, None, 0),
        ("forge_manifest_digest", "TEXT", 1, None, 0),
        ("authority_binding", "TEXT", 1, None, 0),
        ("indexing_policy_generation", "TEXT", 1, None, 0),
        ("reason_code", "TEXT", 1, None, 0),
        ("previous_event_digest", "TEXT", 1, None, 0),
        ("event_digest", "TEXT", 1, None, 0),
    ),
}
_SIDECAR_FOREIGN_KEY_ACTIONS: Final[dict[str, tuple[tuple[str, str, str, str, str, str], ...]]] = {
    table: tuple(
        (parent, source, target, "RESTRICT", "RESTRICT", "NONE")
        for parent, source, target in references
    )
    for table, references in _SIDECAR_FOREIGN_KEYS.items()
}


def _validate_forge_sidecar_schema(connection: sqlite3.Connection) -> None:
    for table, expected in _SIDECAR_COLUMNS.items():
        actual_info = tuple(
            (
                str(row[1]), str(row[2]).upper(), int(row[3]), row[4], int(row[5])
            )
            for row in connection.execute(f"PRAGMA table_info({table})")
        )
        actual_columns = tuple(row[0] for row in actual_info)
        if actual_columns != expected or actual_info != _SIDECAR_COLUMN_SIGNATURES[table]:
            raise UnifiedSourceIndexMigrationError("USI Forge sidecar columns are invalid")

    schema_objects = {
        str(row[1]): (str(row[0]), None if row[2] is None else str(row[2]))
        for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name GLOB 'usi_forge_*'"
        )
    }
    expected_object_names = _FORGE_SIDECAR_TABLES | _FORGE_SIDECAR_INDEXES
    if set(schema_objects) != expected_object_names:
        raise UnifiedSourceIndexMigrationError("USI Forge sidecar objects are invalid")
    for table, statement in _FORGE_SIDECAR_TABLE_DDL.items():
        object_type, actual_sql = schema_objects[table]
        if object_type != "table" or actual_sql is None or _canonical_ddl(actual_sql) != _canonical_ddl(statement):
            raise UnifiedSourceIndexMigrationError("USI Forge sidecar table constraints are invalid")
    for name, statement in _FORGE_SIDECAR_INDEX_DDL.items():
        object_type, actual_sql = schema_objects[name]
        if object_type != "index" or actual_sql is None or _canonical_ddl(actual_sql) != _canonical_ddl(statement):
            raise UnifiedSourceIndexMigrationError("USI Forge sidecar index definitions are invalid")

    for name, columns in _SIDECAR_INDEX_COLUMNS.items():
        expected_index = tuple(
            (column, descending, "BINARY")
            for column, descending in zip(columns, _SIDECAR_INDEX_DIRECTIONS[name])
        )
        actual_index = tuple(
            (str(item[2]), bool(item[3]), str(item[4]).upper())
            for item in connection.execute(f"PRAGMA index_xinfo({name})")
            if int(item[5]) == 1
        )
        if actual_index != expected_index:
            raise UnifiedSourceIndexMigrationError("USI Forge sidecar index columns are invalid")

    indexes = {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name GLOB 'usi_forge_*'"
        )
    }
    if indexes != _FORGE_SIDECAR_INDEXES:
        raise UnifiedSourceIndexMigrationError("USI Forge sidecar indexes are invalid")
    for table in _FORGE_SIDECAR_TABLES:
        if connection.execute(f"PRAGMA foreign_key_check({table})").fetchall():
            raise UnifiedSourceIndexMigrationError("USI Forge sidecar foreign keys are invalid")
        foreign_keys = tuple(sorted(
            (
                str(row[2]), str(row[3]), str(row[4]),
                str(row[5]).upper(), str(row[6]).upper(), str(row[7]).upper(),
            )
            for row in connection.execute(f"PRAGMA foreign_key_list({table})")
        ))
        if foreign_keys != tuple(sorted(_SIDECAR_FOREIGN_KEY_ACTIONS[table])):
            raise UnifiedSourceIndexMigrationError("USI Forge sidecar foreign keys are invalid")


def _canonical_ddl(statement: str) -> str:
    text = statement.strip().rstrip(";").strip()
    output: list[str] = []
    quote: str | None = None
    pending_space = False
    index = 0
    while index < len(text):
        character = text[index]
        if quote is not None:
            output.append(character)
            if character == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 1
                    output.append(text[index])
                else:
                    quote = None
        elif character in {"'", '"', "`"}:
            if pending_space and output and output[-1] != " ":
                output.append(" ")
            pending_space = False
            quote = character
            output.append(character)
        elif character == "[":
            if pending_space and output and output[-1] != " ":
                output.append(" ")
            pending_space = False
            quote = "]"
            output.append(character)
        elif character.isspace():
            pending_space = True
        else:
            if pending_space and output and output[-1] != " ":
                output.append(" ")
            pending_space = False
            output.append(character.lower())
        index += 1
    return "".join(output).strip()


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

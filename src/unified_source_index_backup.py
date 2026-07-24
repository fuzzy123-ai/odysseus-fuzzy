"""Contained SQLite backup, restore and projection-rebuild primitives for USI.

These primitives operate only on an explicitly supplied USI SQLite store and
temporary target roots.  They neither discover productive data nor select a
runtime database.  External projections remain injected because their sink,
worker and replay configuration are not stored in USI manifests.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from typing import Protocol, runtime_checkable

from src.unified_source_index_migrations import (
    DEFAULT_BUSY_TIMEOUT_MS,
    configure_sqlite_connection,
    rebuild_chunk_fts,
    validate_schema,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_stores import StoreSnapshot


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_REBUILDERS = 8


class UnifiedSourceIndexBackupError(RuntimeError):
    """Raised when a contained USI backup/recovery operation is unsafe."""


@dataclass(frozen=True, slots=True)
class SQLiteSnapshotProof:
    snapshot: StoreSnapshot
    database_sha256: str
    database_bytes: int


@dataclass(frozen=True, slots=True)
class SQLiteBackupReceipt:
    database_path: Path
    source_snapshot: StoreSnapshot
    backup_snapshot: StoreSnapshot
    proof: SQLiteSnapshotProof


@dataclass(frozen=True, slots=True)
class SQLiteRestoreReceipt:
    database_path: Path
    backup_snapshot: StoreSnapshot
    restored_snapshot: StoreSnapshot
    proof: SQLiteSnapshotProof


@dataclass(frozen=True, slots=True)
class RestoredSQLiteStore:
    store: SQLiteUnifiedSourceIndexStore
    database_path: Path
    backup_snapshot: StoreSnapshot
    restored_snapshot: StoreSnapshot
    receipt: SQLiteRestoreReceipt


@dataclass(frozen=True, slots=True)
class ProjectionRebuildAttestation:
    name: str
    snapshot: StoreSnapshot
    status: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name):
            raise UnifiedSourceIndexBackupError("rebuilder name is invalid")
        if not isinstance(self.snapshot, StoreSnapshot):
            raise UnifiedSourceIndexBackupError("rebuilder snapshot must be typed")
        if self.status not in {"rebuilt", "unavailable", "failed"}:
            raise UnifiedSourceIndexBackupError("rebuilder status is invalid")


@runtime_checkable
class ProjectionRebuilder(Protocol):
    name: str

    def rebuild(
        self,
        store: SQLiteUnifiedSourceIndexStore,
        snapshot: StoreSnapshot,
    ) -> ProjectionRebuildAttestation: ...


@dataclass(frozen=True, slots=True)
class ProjectionRebuildReceipt:
    snapshot: StoreSnapshot
    fts_status: str
    external: tuple[ProjectionRebuildAttestation, ...]
    missing_rebuilders: tuple[str, ...]
    status: str


def backup_sqlite_store(
    store: SQLiteUnifiedSourceIndexStore,
    *,
    target_root: str | Path,
) -> SQLiteBackupReceipt:
    """Create a validated consistent SQLite backup in a fresh contained child."""

    source = _store_path(store)
    child = _fresh_child(target_root, "usi-backup-")
    destination = child / "source-index.sqlite3"
    try:
        source_snapshot = store.current_snapshot()
        _copy_sqlite(source, destination)
        _assert_self_contained(destination)
        proof = _proof(destination)
        if proof.snapshot != source_snapshot:
            raise UnifiedSourceIndexBackupError("backup snapshot does not match source")
        return SQLiteBackupReceipt(destination, source_snapshot, proof.snapshot, proof)
    except Exception as exc:
        _remove_child(child)
        if isinstance(exc, UnifiedSourceIndexBackupError):
            raise
        raise UnifiedSourceIndexBackupError("SQLite backup failed") from exc


def restore_sqlite_backup(
    receipt: SQLiteBackupReceipt,
    *,
    target_root: str | Path,
) -> RestoredSQLiteStore:
    """Restore a validated backup only into a new contained temporary child."""

    if not isinstance(receipt, SQLiteBackupReceipt):
        raise UnifiedSourceIndexBackupError("backup receipt must be typed")
    backup_path = receipt.database_path.resolve()
    child = _fresh_child(target_root, "usi-restore-")
    destination = child / "source-index.sqlite3"
    try:
        _assert_self_contained(backup_path)
        backup_proof = _proof(backup_path)
        if backup_proof != receipt.proof or backup_proof.snapshot != receipt.backup_snapshot:
            raise UnifiedSourceIndexBackupError("backup receipt no longer matches artifact")
        _copy_sqlite(backup_path, destination)
        restored_proof = _proof(destination)
        if restored_proof.snapshot != receipt.backup_snapshot:
            raise UnifiedSourceIndexBackupError("restored snapshot does not match backup")
        restored_store = SQLiteUnifiedSourceIndexStore(destination)
        if restored_store.current_snapshot() != restored_proof.snapshot:
            raise UnifiedSourceIndexBackupError("restored store snapshot changed during validation")
        restore_receipt = SQLiteRestoreReceipt(
            destination,
            receipt.backup_snapshot,
            restored_proof.snapshot,
            restored_proof,
        )
        return RestoredSQLiteStore(
            restored_store,
            destination,
            receipt.backup_snapshot,
            restored_proof.snapshot,
            restore_receipt,
        )
    except Exception as exc:
        _remove_child(child)
        if isinstance(exc, UnifiedSourceIndexBackupError):
            raise
        raise UnifiedSourceIndexBackupError("SQLite restore failed") from exc


def rebuild_projections(
    store: SQLiteUnifiedSourceIndexStore,
    *,
    rebuilders: tuple[ProjectionRebuilder, ...],
    required_rebuilders: tuple[str, ...] = (),
) -> ProjectionRebuildReceipt:
    """Rebuild FTS and attest injected external rebuilders against one snapshot.

    FTS is rebuilt directly from persisted chunk truth.  Embedding and RAPTOR
    execution remains injected: this module has neither a provider sink nor a
    worker binding, and must therefore not invent one.
    """

    source = _store_path(store)
    snapshot = store.current_snapshot()
    required = _rebuild_names(required_rebuilders, "required_rebuilders")
    if not isinstance(rebuilders, tuple) or len(rebuilders) > _MAX_REBUILDERS:
        raise UnifiedSourceIndexBackupError("rebuilders must be a bounded tuple")
    names = _rebuild_names(tuple(getattr(item, "name", None) for item in rebuilders), "rebuilders")
    if len(names) != len(rebuilders):
        raise UnifiedSourceIndexBackupError("rebuilder names must be unique")

    try:
        _rebuild_fts_at_snapshot(source, snapshot)
        fts_status = "rebuilt"
    except Exception:
        fts_status = "failed"

    attestations: list[ProjectionRebuildAttestation] = []
    for rebuilder in rebuilders:
        try:
            if store.current_snapshot() != snapshot:
                raise UnifiedSourceIndexBackupError("index truth changed during projection rebuild")
            attestation = rebuilder.rebuild(store, snapshot)
            if not isinstance(attestation, ProjectionRebuildAttestation):
                raise UnifiedSourceIndexBackupError("rebuilder must return an attestation")
            if attestation.name != rebuilder.name or attestation.snapshot != snapshot:
                raise UnifiedSourceIndexBackupError("rebuilder did not attest the fixed snapshot")
            if store.current_snapshot() != snapshot:
                raise UnifiedSourceIndexBackupError("index truth changed during projection rebuild")
            attestations.append(attestation)
        except Exception:
            name = getattr(rebuilder, "name", "invalid")
            if isinstance(name, str) and _NAME_RE.fullmatch(name):
                attestations.append(ProjectionRebuildAttestation(name, snapshot, "failed"))
            else:
                raise UnifiedSourceIndexBackupError("rebuilder name is invalid")

    if store.current_snapshot() != snapshot:
        raise UnifiedSourceIndexBackupError("index truth changed during projection rebuild")

    by_name = {item.name: item for item in attestations}
    missing = tuple(name for name in required if name not in by_name)
    all_required_rebuilt = not missing and all(
        by_name[name].status == "rebuilt" for name in required
    )
    complete = fts_status == "rebuilt" and all_required_rebuilt and all(
        item.status == "rebuilt" for item in attestations
    )
    return ProjectionRebuildReceipt(
        snapshot,
        fts_status,
        tuple(attestations),
        missing,
        "complete" if complete else "incomplete",
    )


def _store_path(store: SQLiteUnifiedSourceIndexStore) -> Path:
    if not isinstance(store, SQLiteUnifiedSourceIndexStore):
        raise UnifiedSourceIndexBackupError("store must be the isolated SQLite USI store")
    path = Path(store.database_path).resolve()
    if not path.is_file():
        raise UnifiedSourceIndexBackupError("USI SQLite database is unavailable")
    return path


def _fresh_child(target_root: str | Path, prefix: str) -> Path:
    root = Path(target_root).resolve()
    if not root.is_dir():
        raise UnifiedSourceIndexBackupError("temporary target root must already exist")
    child = Path(tempfile.mkdtemp(prefix=prefix, dir=root)).resolve()
    if child.parent != root:
        _remove_child(child)
        raise UnifiedSourceIndexBackupError("temporary target escaped its root")
    return child


def _remove_child(child: Path) -> None:
    if child.exists():
        shutil.rmtree(child, ignore_errors=True)


def _copy_sqlite(source: Path, destination: Path) -> None:
    if destination.exists():
        raise UnifiedSourceIndexBackupError("backup destination must be fresh")
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        source_connection.execute("PRAGMA query_only = ON")
        validate_schema(source_connection)
        destination_connection = sqlite3.connect(destination)
        source_connection.backup(destination_connection)
        destination_connection.commit()
        destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError as exc:
        raise UnifiedSourceIndexBackupError("SQLite backup API failed") from exc
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()


def _proof(path: Path) -> SQLiteSnapshotProof:
    if not path.is_file():
        raise UnifiedSourceIndexBackupError("SQLite artifact is unavailable")
    _assert_self_contained(path)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        validate_schema(connection)
        row = connection.execute(
            "SELECT revision, state_hash, record_count, tombstone_count, snapshot_ref "
            "FROM usi_store_state WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise UnifiedSourceIndexBackupError("USI snapshot state is missing")
        snapshot = StoreSnapshot(*row)
    except sqlite3.DatabaseError as exc:
        raise UnifiedSourceIndexBackupError("SQLite artifact validation failed") from exc
    finally:
        if connection is not None:
            connection.close()
    return SQLiteSnapshotProof(snapshot, _sha256(path), path.stat().st_size)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _assert_self_contained(path: Path) -> None:
    wal = Path(str(path) + "-wal")
    if wal.is_file() and wal.stat().st_size > 0:
        raise UnifiedSourceIndexBackupError("SQLite artifact has a non-empty WAL dependency")


def _rebuild_names(values: tuple[object, ...], field_name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > _MAX_REBUILDERS:
        raise UnifiedSourceIndexBackupError(f"{field_name} must be a bounded tuple")
    if not all(isinstance(value, str) and _NAME_RE.fullmatch(value) for value in values):
        raise UnifiedSourceIndexBackupError(f"{field_name} contains an invalid name")
    names = tuple(values)
    if len(set(names)) != len(names):
        raise UnifiedSourceIndexBackupError(f"{field_name} names must be unique")
    return names


def _rebuild_fts_at_snapshot(path: Path, expected: StoreSnapshot) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path, isolation_level=None)
        configure_sqlite_connection(connection, busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT revision, state_hash, record_count, tombstone_count, snapshot_ref "
            "FROM usi_store_state WHERE singleton = 1"
        ).fetchone()
        if row is None or StoreSnapshot(*row) != expected:
            raise UnifiedSourceIndexBackupError("index truth changed before FTS rebuild")
        rebuild_chunk_fts(connection)
        connection.commit()
    except sqlite3.DatabaseError as exc:
        if connection is not None:
            connection.rollback()
        raise UnifiedSourceIndexBackupError("FTS rebuild failed") from exc
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


__all__ = [
    "ProjectionRebuildAttestation",
    "ProjectionRebuildReceipt",
    "ProjectionRebuilder",
    "RestoredSQLiteStore",
    "SQLiteBackupReceipt",
    "SQLiteRestoreReceipt",
    "SQLiteSnapshotProof",
    "UnifiedSourceIndexBackupError",
    "backup_sqlite_store",
    "rebuild_projections",
    "restore_sqlite_backup",
]

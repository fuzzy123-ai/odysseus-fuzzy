from pathlib import Path

import pytest

from src.unified_source_index_backup import (
    ProjectionRebuildAttestation,
    UnifiedSourceIndexBackupError,
    backup_sqlite_store,
    rebuild_projections,
    restore_sqlite_backup,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    Classification,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_stores import RecordKind, StoredRecord


NOW = "2026-07-23T19:00:00Z"


def _store(tmp_path: Path):
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "source.sqlite3")
    source = SourceRecord(
        owner_scope="user:alice",
        source_kind=SourceKind.DOCUMENT,
        canonical_ref="synthetic:backup",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
        provider_ref="fixture",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref="fixture:1",
        content_hash=content_hash("synthetic backup content"),
        version_observed_at=NOW,
        indexed_at=NOW,
    )
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(0, 24),
        extractor_profile_ref="fixture-v1",
        content_hash=content_hash("synthetic backup content"),
        content="synthetic backup content",
        indexed_at=NOW,
    )
    write = store.begin_write(store.current_snapshot())
    for record in (source, version, chunk):
        write.put(record)
    write.commit()
    return store, source, version, chunk


def test_sqlite_backup_and_contained_restore_preserve_snapshot_and_content_hashes(tmp_path):
    store, source, version, chunk = _store(tmp_path)
    backup_root = tmp_path / "backups"
    restore_root = tmp_path / "restores"
    backup_root.mkdir()
    restore_root.mkdir()

    backup = backup_sqlite_store(store, target_root=backup_root)
    backup_wal = Path(str(backup.database_path) + "-wal")
    assert not backup_wal.exists() or backup_wal.stat().st_size == 0
    restored = restore_sqlite_backup(backup, target_root=restore_root)

    assert backup.source_snapshot == backup.backup_snapshot
    assert restored.backup_snapshot == backup.backup_snapshot
    assert restored.restored_snapshot == backup.source_snapshot
    assert restored.database_path.parent.parent == restore_root.resolve()
    with restored.store.begin_read() as read:
        item = read.get(RecordKind.CHUNK, chunk.chunk_id, owner_scope="user:alice")
    assert isinstance(item, StoredRecord)
    assert item.record.content_hash == chunk.content_hash

    later = ChunkRecord.create(
        version,
        locator=TextRangeLocator(25, 30),
        extractor_profile_ref="fixture-v1",
        content_hash=content_hash("later"),
        content="later",
        indexed_at=NOW,
    )
    write = store.begin_write(store.current_snapshot())
    write.put(later)
    write.commit()
    assert store.current_snapshot() != restored.restored_snapshot
    assert restored.store.current_snapshot() == restored.restored_snapshot
    assert source.source_id


def test_restore_rejects_tampered_backup_before_exposing_a_store(tmp_path):
    store, *_ = _store(tmp_path)
    backup_root = tmp_path / "backups"
    restore_root = tmp_path / "restores"
    backup_root.mkdir()
    restore_root.mkdir()
    backup = backup_sqlite_store(store, target_root=backup_root)
    backup.database_path.write_bytes(b"not a SQLite backup")

    with pytest.raises(UnifiedSourceIndexBackupError):
        restore_sqlite_backup(backup, target_root=restore_root)
    assert list(restore_root.iterdir()) == []


def test_rebuild_fts_and_injected_rebuilders_attest_one_fixed_snapshot(tmp_path):
    store, _source, version, _chunk = _store(tmp_path)

    class Rebuilder:
        def __init__(self, name):
            self.name = name
            self.seen = None

        def rebuild(self, _store, snapshot):
            self.seen = snapshot
            return ProjectionRebuildAttestation(self.name, snapshot, "rebuilt")

    embedding = Rebuilder("embedding")
    raptor = Rebuilder("raptor")
    complete = rebuild_projections(
        store,
        rebuilders=(embedding, raptor),
        required_rebuilders=("embedding", "raptor"),
    )
    assert complete.status == "complete"
    assert complete.fts_status == "rebuilt"
    assert embedding.seen == complete.snapshot
    assert raptor.seen == complete.snapshot
    assert store.search_chunks(owner_scope="user:alice", query="synthetic", limit=2)

    incomplete = rebuild_projections(
        store,
        rebuilders=(embedding,),
        required_rebuilders=("embedding", "raptor"),
    )
    assert incomplete.status == "incomplete"
    assert incomplete.missing_rebuilders == ("raptor",)

    class MutatingRebuilder:
        name = "embedding"

        def rebuild(self, target, snapshot):
            later = ChunkRecord.create(
                version,
                locator=TextRangeLocator(25, 30),
                extractor_profile_ref="fixture-v1",
                content_hash=content_hash("later"),
                content="later",
                indexed_at=NOW,
            )
            write = target.begin_write(target.current_snapshot())
            write.put(later)
            write.commit()
            return ProjectionRebuildAttestation(self.name, snapshot, "rebuilt")

    with pytest.raises(UnifiedSourceIndexBackupError, match="changed during projection rebuild"):
        rebuild_projections(
            store,
            rebuilders=(MutatingRebuilder(),),
            required_rebuilders=("embedding",),
        )

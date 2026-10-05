import sqlite3
import json
from dataclasses import replace

import pytest

import src.unified_source_index_migrations as migrations
from src.unified_source_index_contract import (
    Classification, ContentPolicy, RecordKind, SourceKind, SourceRecord,
    SourceVersionRecord, TextRangeLocator, ChunkRecord, content_hash,
)
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.unified_source_index_migrations import (
    USI_SQLITE_APPLICATION_ID,
    USI_SQLITE_SCHEMA_VERSION,
    UnifiedSourceIndexMigrationError,
    configure_sqlite_connection,
    current_schema_version,
    downgrade_schema,
    read_sqlite_pragmas,
    upgrade_schema,
    validate_schema,
)


def _source():
    return SourceRecord(
        owner_scope="user:alice", source_kind=SourceKind.TEXT,
        canonical_ref="text:alpha", classification=Classification.PUBLIC,
        content_policy=ContentPolicy.INLINE_LOCAL, provider_ref="local",
        first_seen_at="2026-01-01T00:00:00Z", source_modified_at="2026-01-01T00:00:00Z",
        valid_from="2026-01-01T00:00:00Z",
    )


def _version(source):
    return SourceVersionRecord.create(
        source, revision_ref="r1", content_hash=content_hash("r1"),
        version_observed_at="2026-01-01T00:00:00Z",
    )


def _chunk(version, content):
    return ChunkRecord.create(
        version, locator=TextRangeLocator(0, len(content)),
        extractor_profile_ref="text-v1", content_hash=content_hash(content),
        content=content, indexed_at="2026-01-01T00:00:00Z",
    )


def _insert(store, *records):
    write = store.begin_write(store.current_snapshot())
    for record in records:
        write.put(record)
    return write.commit()


def _connection(path):
    connection = sqlite3.connect(path, isolation_level=None)
    configure_sqlite_connection(connection, busy_timeout_ms=2_750)
    return connection


def _as_schema_one(connection):
    migrations._drop_forge_sidecars(connection)
    connection.execute("PRAGMA user_version = 1")


def _core_database_snapshot(connection, *, include_forge=False):
    objects = tuple(sorted(
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )
        if include_forge
        or (not str(row[1]).startswith("usi_forge_")
            and not str(row[2]).startswith("usi_forge_"))
    ))
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        if include_forge or not str(row[0]).startswith("usi_forge_")
    ]
    contents = []
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        rows = tuple(sorted(connection.execute(f"SELECT * FROM {quoted}").fetchall(), key=repr))
        contents.append((table, rows))
    return (
        int(connection.execute("PRAGMA application_id").fetchone()[0]),
        objects,
        tuple(contents),
    )


def _make_populated_v1(path):
    store = SQLiteUnifiedSourceIndexStore(path)
    source = _source()
    version = _version(source)
    retained = _chunk(version, "retainedword survives upgrade")
    removed = _chunk(version, "tombstoneword disappears")
    snapshot = _insert(store, source, version, retained, removed)
    update = store.begin_write(snapshot)
    update.compare_and_swap(
        replace(source, source_modified_at="2026-01-02T00:00:00Z"),
        expected_record_revision=1,
    )
    snapshot = update.commit()
    deletion = store.begin_write(snapshot)
    deletion.tombstone(
        RecordKind.CHUNK,
        removed.chunk_id,
        owner_scope=source.owner_scope,
        expected_record_revision=1,
        reason="migration-evidence",
    )
    deletion.commit()

    connection = _connection(path)
    connection.execute(
        """INSERT INTO usi_jobs(
               job_id, owner_scope, job_kind, source_scope_id, status, cursor,
               record_json, record_hash, record_revision
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "migration-job", source.owner_scope, "reindex", source.source_id,
            "queued", "", '{"schema":"test.job","job_id":"migration-job"}',
            "sha256:" + "0" * 64, 1,
        ),
    )
    _as_schema_one(connection)
    return connection, retained, removed


def _altered_sidecar_statements(corruption):
    tables = list(migrations._FORGE_SIDECAR_STATEMENTS)
    indexes = list(migrations._FORGE_SIDECAR_INDEX_STATEMENTS)
    if corruption == "type":
        tables[0] = tables[0].replace(
            "canonical_evidence BLOB NOT NULL", "canonical_evidence TEXT NOT NULL", 1
        )
        target = ("table", 0)
    elif corruption == "nullability":
        tables[0] = tables[0].replace(
            "occurrence_id TEXT NOT NULL UNIQUE", "occurrence_id TEXT UNIQUE", 1
        )
        target = ("table", 0)
    elif corruption == "primary_key":
        tables[0] = tables[0].replace(
            "chunk_id TEXT PRIMARY KEY", "chunk_id TEXT NOT NULL UNIQUE", 1
        )
        target = ("table", 0)
    elif corruption == "unique":
        tables[0] = tables[0].replace(
            "occurrence_id TEXT NOT NULL UNIQUE", "occurrence_id TEXT NOT NULL", 1
        )
        target = ("table", 0)
    elif corruption == "check":
        tables[0] = tables[0].replace(
            "record_revision INTEGER NOT NULL CHECK (record_revision > 0)",
            "record_revision INTEGER NOT NULL CHECK (record_revision >= 0)",
            1,
        )
        target = ("table", 0)
    elif corruption == "foreign_key_action":
        tables[0] = tables[0].replace(
            "ON UPDATE RESTRICT ON DELETE RESTRICT",
            "ON UPDATE RESTRICT ON DELETE CASCADE",
            1,
        )
        target = ("table", 0)
    elif corruption == "deferral":
        tables[1] = tables[1].replace(
            "DEFERRABLE INITIALLY DEFERRED", "NOT DEFERRABLE", 1
        )
        target = ("table", 1)
    elif corruption == "missing_index":
        indexes.pop(0)
        target = ("index", 0)
    elif corruption == "index_direction":
        indexes[3] = indexes[3].replace("event_revision DESC", "event_revision ASC", 1)
        target = ("index", 3)
    else:
        raise AssertionError("unknown schema corruption fixture")
    return tables, indexes, target


def test_upgrade_configures_wal_foreign_keys_timeout_and_version_idempotently(tmp_path):
    connection = _connection(tmp_path / "usi.db")

    assert upgrade_schema(connection) == USI_SQLITE_SCHEMA_VERSION
    assert upgrade_schema(connection) == USI_SQLITE_SCHEMA_VERSION
    pragmas = read_sqlite_pragmas(connection)

    assert pragmas.journal_mode == "wal"
    assert pragmas.foreign_keys is True
    assert pragmas.busy_timeout_ms == 2_750
    assert pragmas.application_id == USI_SQLITE_APPLICATION_ID
    assert pragmas.schema_version == USI_SQLITE_SCHEMA_VERSION
    validate_schema(connection)

    state = connection.execute(
        "SELECT revision, record_count, tombstone_count FROM usi_store_state"
    ).fetchone()
    assert state == (0, 0, 0)
    connection.close()


def test_schema_has_typed_indexes_foreign_keys_and_fts_shadow_triggers(tmp_path):
    connection = _connection(tmp_path / "usi.db")
    upgrade_schema(connection)

    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'usi_%'"
        )
    }
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'usi_%'"
        )
    }
    chunk_foreign_keys = connection.execute(
        "PRAGMA foreign_key_list(usi_chunks)"
    ).fetchall()

    assert {
        "usi_sources_owner_kind",
        "usi_versions_source",
        "usi_chunks_version",
        "usi_entities_version",
        "usi_history_snapshot",
    } <= indexes
    assert triggers == {
        "usi_chunks_fts_insert",
        "usi_chunks_fts_delete",
        "usi_chunks_fts_update",
    }
    assert {(row[2], row[3], row[4]) for row in chunk_foreign_keys} == {
        ("usi_source_versions", "source_version_id", "source_version_id"),
        ("usi_source_versions", "source_id", "source_id"),
    }
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_downgrade_drops_only_usi_objects_and_supports_clean_reupgrade(tmp_path):
    connection = _connection(tmp_path / "usi.db")
    connection.execute("CREATE TABLE unrelated_sentinel(value TEXT NOT NULL)")
    connection.execute("INSERT INTO unrelated_sentinel VALUES ('preserved')")
    upgrade_schema(connection)

    assert downgrade_schema(connection) == 0
    assert current_schema_version(connection) == 0
    assert connection.execute("PRAGMA application_id").fetchone()[0] == 0
    assert connection.execute(
        "SELECT value FROM unrelated_sentinel"
    ).fetchone()[0] == "preserved"
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'usi_%'"
    ).fetchall() == []

    assert upgrade_schema(connection) == USI_SQLITE_SCHEMA_VERSION
    validate_schema(connection)
    connection.close()


def test_unsupported_versions_and_active_transactions_fail_before_mutation(tmp_path):
    connection = _connection(tmp_path / "usi.db")

    with pytest.raises(UnifiedSourceIndexMigrationError, match="target schema"):
        upgrade_schema(connection, target_version=3)
    assert current_schema_version(connection) == 0

    connection.execute("BEGIN")
    with pytest.raises(UnifiedSourceIndexMigrationError, match="idle"):
        upgrade_schema(connection)
    connection.rollback()

    upgrade_schema(connection)
    assert downgrade_schema(connection, target_version=1) == 1
    assert current_schema_version(connection) == 1
    assert upgrade_schema(connection) == USI_SQLITE_SCHEMA_VERSION
    connection.close()


def test_failed_upgrade_rolls_back_every_partial_schema_object(tmp_path, monkeypatch):
    connection = _connection(tmp_path / "usi.db")
    monkeypatch.setattr(
        migrations,
        "_TABLE_STATEMENTS",
        (*migrations._TABLE_STATEMENTS, "CREATE TABLE broken("),
    )

    with pytest.raises(UnifiedSourceIndexMigrationError, match="upgrade failed"):
        upgrade_schema(connection)

    assert current_schema_version(connection) == 0
    assert connection.execute("PRAGMA application_id").fetchone()[0] == 0
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'usi_%'"
    ).fetchall() == []
    connection.close()


def test_populated_schema_one_migrates_additively_without_changing_core_rows(tmp_path):
    path = tmp_path / "usi.db"
    connection, retained, removed = _make_populated_v1(path)
    before = _core_database_snapshot(connection)
    assert connection.execute("SELECT count(*) FROM usi_record_history").fetchone()[0] > 0
    assert connection.execute("SELECT count(*) FROM usi_tombstones").fetchone()[0] == 1
    assert connection.execute("SELECT count(*) FROM usi_jobs").fetchone()[0] == 1
    assert connection.execute(
        "SELECT count(*) FROM usi_chunk_fts WHERE usi_chunk_fts MATCH 'retainedword'"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT count(*) FROM usi_chunk_fts WHERE usi_chunk_fts MATCH 'tombstoneword'"
    ).fetchone()[0] == 0

    assert upgrade_schema(connection) == 2
    validate_schema(connection)
    assert current_schema_version(connection) == 2
    assert _core_database_snapshot(connection) == before
    assert connection.execute(
        "SELECT count(*) FROM usi_chunk_fts WHERE usi_chunk_fts MATCH 'retainedword'"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT count(*) FROM usi_chunk_fts WHERE usi_chunk_fts MATCH 'tombstoneword'"
    ).fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM usi_record_history").fetchone()[0] > 0
    assert connection.execute("SELECT count(*) FROM usi_tombstones").fetchone()[0] == 1
    assert connection.execute("SELECT count(*) FROM usi_jobs").fetchone()[0] == 1
    connection.close()


@pytest.mark.parametrize("record_kind", ("source", "version", "chunk"))
def test_schema_one_rejects_structural_legacy_forge_records_unchanged(tmp_path, record_kind):
    connection, retained, _removed = _make_populated_v1(tmp_path / f"legacy-{record_kind}.db")
    if record_kind == "source":
        connection.execute(
            "UPDATE usi_sources SET provider_ref='forge.code' WHERE source_id=?",
            (retained.source_id,),
        )
    elif record_kind == "version":
        connection.execute(
            "UPDATE usi_source_versions SET revision_ref='forge-code-version:legacy' "
            "WHERE source_version_id=?",
            (retained.source_version_id,),
        )
        payload = json.loads(connection.execute(
            "SELECT record_json FROM usi_source_versions WHERE source_version_id=?",
            (retained.source_version_id,),
        ).fetchone()[0])
        payload["revision_ref"] = "forge-code-version:legacy"
        connection.execute(
            "UPDATE usi_source_versions SET record_json=? WHERE source_version_id=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), retained.source_version_id),
        )
    else:
        payload = json.loads(connection.execute(
            "SELECT record_json FROM usi_chunks WHERE chunk_id=?", (retained.chunk_id,)
        ).fetchone()[0])
        payload["locator"]["kind"] = "code_range"
        payload["extractor_profile_ref"] = "forge-code-legacy-profile"
        connection.execute(
            "UPDATE usi_chunks SET locator_kind='code_range', record_json=? WHERE chunk_id=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), retained.chunk_id),
        )
    before = _core_database_snapshot(connection)

    with pytest.raises(UnifiedSourceIndexMigrationError, match="legacy Forge"):
        upgrade_schema(connection)

    assert current_schema_version(connection) == 1
    assert _core_database_snapshot(connection) == before
    assert not any(
        str(row[0]).startswith("usi_forge_")
        for row in connection.execute("SELECT name FROM sqlite_master")
    )
    connection.close()


@pytest.mark.parametrize("record_kind", ("source", "source_version", "chunk"))
def test_schema_one_rejects_history_only_legacy_forge_records_unchanged(tmp_path, record_kind):
    connection, retained, _removed = _make_populated_v1(tmp_path / f"history-legacy-{record_kind}.db")
    row = connection.execute(
        "SELECT record_kind, record_id, revision, record_json FROM usi_record_history "
        "WHERE record_kind=? AND state='record' ORDER BY revision LIMIT 1",
        (record_kind,),
    ).fetchone()
    assert row is not None
    payload = json.loads(row[3])
    if record_kind == "source":
        payload["provider_ref"] = "forge.code"
    elif record_kind == "source_version":
        payload["revision_ref"] = "forge-code-version:history"
    else:
        payload["locator"]["kind"] = "code_range"
        payload["extractor_profile_ref"] = "forge-code-history-profile"
    connection.execute(
        "UPDATE usi_record_history SET record_json=? WHERE record_kind=? AND record_id=? AND revision=?",
        (json.dumps(payload, sort_keys=True, separators=(",", ":")), row[0], row[1], row[2]),
    )
    before = _core_database_snapshot(connection)

    with pytest.raises(UnifiedSourceIndexMigrationError, match="legacy Forge"):
        upgrade_schema(connection)

    assert current_schema_version(connection) == 1
    assert _core_database_snapshot(connection) == before
    assert not any(
        str(row[0]).startswith("usi_forge_")
        for row in connection.execute("SELECT name FROM sqlite_master")
    )
    connection.close()


def test_schema_one_ordinary_content_with_forge_spelling_and_like_wildcard_migrates(tmp_path):
    path = tmp_path / "ordinary-content.db"
    store = SQLiteUnifiedSourceIndexStore(path)
    source = _source()
    version = _version(source)
    chunk = _chunk(version, "Ordinary text mentions forge_code and forgeXcode.")
    _insert(store, source, version, chunk)
    connection = _connection(path)
    _as_schema_one(connection)
    before = _core_database_snapshot(connection)

    assert upgrade_schema(connection) == 2

    assert _core_database_snapshot(connection) == before
    assert current_schema_version(connection) == 2
    connection.close()


def test_schema_one_malformed_structural_json_fails_closed_without_echoing_data(tmp_path):
    connection, retained, _removed = _make_populated_v1(tmp_path / "malformed.db")
    marker = "PRIVATE-MALFORMED-ROW-SENTINEL"
    connection.execute(
        "UPDATE usi_sources SET record_json=? WHERE source_id=?",
        ("{" + marker, retained.source_id),
    )
    before = _core_database_snapshot(connection)

    with pytest.raises(UnifiedSourceIndexMigrationError, match="malformed") as error:
        upgrade_schema(connection)

    assert marker not in str(error.value)
    assert current_schema_version(connection) == 1
    assert _core_database_snapshot(connection) == before
    connection.close()


def test_schema_one_unknown_chunk_schema_fails_closed_without_scanning_text(tmp_path):
    connection, retained, _removed = _make_populated_v1(tmp_path / "unknown-schema.db")
    payload = {
        "schema": "unknown.chunk.v9",
        "extractor_profile_ref": "forge-code-legacy-profile",
        "locator": {"kind": "code_range"},
        "content": "ordinary text forge_code",
    }
    connection.execute(
        "UPDATE usi_chunks SET record_json=? WHERE chunk_id=?",
        (json.dumps(payload, sort_keys=True, separators=(",", ":")), retained.chunk_id),
    )
    before = _core_database_snapshot(connection)

    with pytest.raises(UnifiedSourceIndexMigrationError, match="malformed") as error:
        upgrade_schema(connection)

    assert "ordinary text forge_code" not in str(error.value)
    assert current_schema_version(connection) == 1
    assert _core_database_snapshot(connection) == before
    connection.close()


@pytest.mark.parametrize("fault_index", range(9))
def test_each_sidecar_ddl_fault_rolls_back_schema_one_upgrade(tmp_path, monkeypatch, fault_index):
    connection = _connection(tmp_path / f"ddl-fault-{fault_index}.db")
    upgrade_schema(connection)
    _as_schema_one(connection)
    before = _core_database_snapshot(connection)
    table_statements = list(migrations._FORGE_SIDECAR_STATEMENTS)
    index_statements = list(migrations._FORGE_SIDECAR_INDEX_STATEMENTS)
    if fault_index < len(table_statements):
        table_statements[fault_index] = "CREATE TABLE broken("
    else:
        index_statements[fault_index - len(table_statements)] = (
            "CREATE INDEX broken ON missing_table(value)"
        )
    monkeypatch.setattr(migrations, "_FORGE_SIDECAR_STATEMENTS", tuple(table_statements))
    monkeypatch.setattr(migrations, "_FORGE_SIDECAR_INDEX_STATEMENTS", tuple(index_statements))

    with pytest.raises(UnifiedSourceIndexMigrationError, match="upgrade failed"):
        upgrade_schema(connection)

    assert current_schema_version(connection) == 1
    assert _core_database_snapshot(connection) == before
    assert not any(
        str(row[0]).startswith("usi_forge_")
        for row in connection.execute("SELECT name FROM sqlite_master")
    )
    connection.close()


@pytest.mark.parametrize(
    "corruption",
    (
        "type", "nullability", "primary_key", "unique", "check", "foreign_key_action",
        "deferral", "missing_index", "index_direction",
    ),
)
def test_v2_schema_constraint_corruption_rolls_back_initial_upgrade(
    tmp_path, monkeypatch, corruption
):
    tables, indexes, _target = _altered_sidecar_statements(corruption)
    monkeypatch.setattr(migrations, "_FORGE_SIDECAR_STATEMENTS", tuple(tables))
    monkeypatch.setattr(migrations, "_FORGE_SIDECAR_INDEX_STATEMENTS", tuple(indexes))
    connection = _connection(tmp_path / f"schema-corruption-{corruption}.db")

    with pytest.raises(UnifiedSourceIndexMigrationError, match="upgrade failed"):
        upgrade_schema(connection)

    assert current_schema_version(connection) == 0
    assert connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE name GLOB 'usi_*'"
    ).fetchone()[0] == 0
    connection.close()


@pytest.mark.parametrize(
    "corruption",
    (
        "type", "nullability", "primary_key", "unique", "check", "foreign_key_action",
        "deferral", "missing_index", "index_direction",
    ),
)
def test_existing_v2_schema_corruption_is_rejected_without_repair(
    tmp_path, corruption
):
    tables, indexes, target = _altered_sidecar_statements(corruption)
    connection = _connection(tmp_path / f"corrupt-v2-{corruption}.db")
    upgrade_schema(connection)
    if target[0] == "table":
        table_name = tuple(migrations._FORGE_SIDECAR_TABLE_DDL)[target[1]]
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(f'DROP TABLE "{table_name}"')
        connection.execute(tables[target[1]])
        for statement in migrations._FORGE_SIDECAR_INDEX_STATEMENTS:
            if f" ON {table_name}(" in statement:
                connection.execute(statement)
        connection.execute("PRAGMA foreign_keys = ON")
    else:
        index_name = tuple(migrations._FORGE_SIDECAR_INDEX_DDL)[target[1]]
        connection.execute(f'DROP INDEX "{index_name}"')
        if corruption != "missing_index":
            connection.execute(indexes[target[1]])
    before = (
        current_schema_version(connection),
        _core_database_snapshot(connection, include_forge=True),
    )

    with pytest.raises(UnifiedSourceIndexMigrationError):
        upgrade_schema(connection)

    after = (
        current_schema_version(connection),
        _core_database_snapshot(connection, include_forge=True),
    )
    assert after == before
    connection.close()


def test_only_virgin_empty_v2_store_can_downgrade_to_v1(tmp_path):
    virgin = _connection(tmp_path / "virgin-downgrade.db")
    upgrade_schema(virgin)
    virgin_core_before = _core_database_snapshot(virgin)

    assert downgrade_schema(virgin, target_version=1) == 1

    assert current_schema_version(virgin) == 1
    assert not any(
        str(row[0]).startswith("usi_forge_")
        for row in virgin.execute("SELECT name FROM sqlite_master")
    )
    assert _core_database_snapshot(virgin) == virgin_core_before
    virgin.close()

    populated, _retained, _removed = _make_populated_v1(tmp_path / "nonvirgin.db")
    upgrade_schema(populated)
    populated_before = (
        current_schema_version(populated),
        _core_database_snapshot(populated, include_forge=True),
    )
    with pytest.raises(UnifiedSourceIndexMigrationError, match="virgin store"):
        downgrade_schema(populated, target_version=1)
    assert (
        current_schema_version(populated),
        _core_database_snapshot(populated, include_forge=True),
    ) == populated_before
    populated.close()

    sidecar = _connection(tmp_path / "nonempty-sidecar.db")
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "nonempty-sidecar.db")
    _insert(store, _source())
    sidecar.execute(
        """INSERT INTO usi_forge_occurrence_history(
               chunk_id, revision, state, canonical_evidence, evidence_hash,
               occurrence_id, source_id, source_version_id, owner_scope, repo_id
           ) VALUES ('legacy', 1, 'deleted', NULL, NULL, NULL, NULL, NULL, NULL, NULL)"""
    )
    sidecar_before = (
        current_schema_version(sidecar),
        _core_database_snapshot(sidecar, include_forge=True),
    )
    with pytest.raises(UnifiedSourceIndexMigrationError, match="empty Forge sidecars"):
        downgrade_schema(sidecar, target_version=1)
    assert (
        current_schema_version(sidecar),
        _core_database_snapshot(sidecar, include_forge=True),
    ) == sidecar_before
    sidecar.close()


def test_busy_timeout_is_bounded(tmp_path):
    connection = sqlite3.connect(tmp_path / "usi.db", isolation_level=None)
    for invalid in (0, 60_001, True, 1.5):
        with pytest.raises(UnifiedSourceIndexMigrationError, match="busy_timeout"):
            configure_sqlite_connection(connection, busy_timeout_ms=invalid)
    connection.close()


def test_wal_and_application_identity_fail_closed(tmp_path):
    memory_connection = sqlite3.connect(":memory:", isolation_level=None)
    with pytest.raises(UnifiedSourceIndexMigrationError, match="WAL"):
        configure_sqlite_connection(memory_connection)
    memory_connection.close()

    connection = _connection(tmp_path / "usi.db")
    upgrade_schema(connection)
    connection.execute("PRAGMA application_id = 7")
    with pytest.raises(UnifiedSourceIndexMigrationError, match="isolated USI"):
        validate_schema(connection)
    connection.close()

import sqlite3

import pytest

import src.unified_source_index_migrations as migrations
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


def _connection(path):
    connection = sqlite3.connect(path, isolation_level=None)
    configure_sqlite_connection(connection, busy_timeout_ms=2_750)
    return connection


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
        upgrade_schema(connection, target_version=2)
    assert current_schema_version(connection) == 0

    connection.execute("BEGIN")
    with pytest.raises(UnifiedSourceIndexMigrationError, match="idle"):
        upgrade_schema(connection)
    connection.rollback()

    upgrade_schema(connection)
    with pytest.raises(UnifiedSourceIndexMigrationError, match="downgrade target"):
        downgrade_schema(connection, target_version=1)
    assert current_schema_version(connection) == USI_SQLITE_SCHEMA_VERSION
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

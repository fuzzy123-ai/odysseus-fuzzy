import sqlite3

import pytest
from sqlalchemy import create_engine, text


def test_clarification_table_migration_creates_run_and_event_tables(monkeypatch):
    import core.database as database
    import core.database_migrations as migrations

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "DATABASE_URL", "sqlite:///:memory:")

    migrations._migrate_clarification_tables()

    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }

    assert "clarification_runs" in tables
    assert "clarification_events" in tables


def test_mcp_owner_subject_migration_is_nullable_and_never_backfills(tmp_path, monkeypatch):
    import core.database as database
    import core.database_migrations as migrations

    db_path = tmp_path / "legacy-api-tokens.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE api_tokens (id TEXT PRIMARY KEY, owner TEXT, name TEXT NOT NULL, "
            "token_hash TEXT NOT NULL, token_prefix TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT 'chat', "
            "is_active BOOLEAN DEFAULT 1)"
        )
        conn.execute(
            "INSERT INTO api_tokens (id, owner, name, token_hash, token_prefix, scopes) "
            "VALUES ('legacy-1', 'admin', 'legacy', 'hash', 'ody_demo', 'chat')"
        )
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    migrations._migrate_add_api_token_owner_subject_id_column()
    migrations._migrate_add_api_token_owner_subject_id_column()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(api_tokens)")}
        row = conn.execute(
            "SELECT owner_subject_id FROM api_tokens WHERE id='legacy-1'"
        ).fetchone()
    assert columns["owner_subject_id"][3] == 0
    assert row == (None,)


def test_clarification_creator_subject_migration_is_nullable_indexed_and_idempotent(tmp_path, monkeypatch):
    import core.database as database
    import core.database_migrations as migrations

    engine = create_engine(f"sqlite:///{(tmp_path / 'runs.db').as_posix()}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(migrations, "engine", engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE clarification_runs (id TEXT PRIMARY KEY, owner TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO clarification_runs (id, owner) VALUES ('legacy', 'alice')"))
    migrations._migrate_add_clarification_creator_subject_id()
    migrations._migrate_add_clarification_creator_subject_id()
    with engine.connect() as conn:
        columns = {row[1]: row for row in conn.execute(text("PRAGMA table_info(clarification_runs)"))}
        row = conn.execute(text("SELECT creator_subject_id FROM clarification_runs WHERE id='legacy'")).fetchone()
        indexes = {row[1] for row in conn.execute(text("PRAGMA index_list(clarification_runs)"))}
    assert columns["creator_subject_id"][3] == 0 and row == (None,)
    assert "ix_clarification_runs_creator_subject_id" in indexes


def test_clarification_subject_migration_fails_loudly_without_a_run_table(monkeypatch):
    import core.database as database
    import core.database_migrations as migrations

    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(migrations, "engine", engine)
    with pytest.raises(RuntimeError, match="clarification_creator_subject_migration_failed"):
        migrations._migrate_add_clarification_creator_subject_id()


def test_clarification_subject_sqlite_index_failure_rolls_back_column_then_restarts(tmp_path, monkeypatch):
    import core.database as database
    import core.database_migrations as migrations

    db_path = tmp_path / "atomic-runs.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(migrations, "engine", engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE clarification_runs (id TEXT PRIMARY KEY, owner TEXT NOT NULL)"))

    raw_connection = engine.raw_connection
    class FailingCursor:
        def __init__(self, cursor): self._cursor = cursor
        def execute(self, sql):
            if sql.startswith("CREATE INDEX"):
                raise sqlite3.OperationalError("injected index failure")
            return self._cursor.execute(sql)
        def __iter__(self): return iter(self._cursor)
        def close(self): self._cursor.close()
    class FailingRaw:
        def __init__(self, raw): self._raw = raw
        def cursor(self): return FailingCursor(self._raw.cursor())
        def commit(self): self._raw.commit()
        def rollback(self): self._raw.rollback()
        def close(self): self._raw.close()
    monkeypatch.setattr(engine, "raw_connection", lambda: FailingRaw(raw_connection()))
    with pytest.raises(RuntimeError, match="clarification_creator_subject_migration_failed"):
        migrations._migrate_add_clarification_creator_subject_id()
    monkeypatch.undo()

    with sqlite3.connect(db_path) as db:
        assert "creator_subject_id" not in {row[1] for row in db.execute("PRAGMA table_info(clarification_runs)")}
        assert "ix_clarification_runs_creator_subject_id" not in {row[1] for row in db.execute("PRAGMA index_list(clarification_runs)")}

    engine.dispose()
    reopened = create_engine(f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(database, "engine", reopened)
    monkeypatch.setattr(migrations, "engine", reopened)
    migrations._migrate_add_clarification_creator_subject_id()
    migrations._migrate_add_clarification_creator_subject_id()
    with sqlite3.connect(db_path) as db:
        assert "creator_subject_id" in {row[1] for row in db.execute("PRAGMA table_info(clarification_runs)")}
        assert "ix_clarification_runs_creator_subject_id" in {row[1] for row in db.execute("PRAGMA index_list(clarification_runs)")}


_DOCUMENT_SID_A = "owner_" + "a" * 32
_DOCUMENT_SID_B = "owner_" + "b" * 32


def _create_legacy_documents(path, *, provenance=False, creator_subject=False):
    columns = ["id TEXT PRIMARY KEY", "owner TEXT"]
    if provenance:
        columns.extend(("source_kind TEXT", "source_ref_hash TEXT"))
    if creator_subject:
        columns.append("creator_subject_id TEXT")
    with sqlite3.connect(path) as connection:
        connection.execute(f"CREATE TABLE documents ({', '.join(columns)})")


def _document_schema(path):
    with sqlite3.connect(path) as connection:
        columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(documents)")
        }
        indexes = {
            row[1]: row for row in connection.execute("PRAGMA index_list(documents)")
        }
        index_sql = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name='documents'"
            )
        }
        triggers = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='trigger' AND tbl_name='documents'"
            )
        }
    return columns, indexes, index_sql, triggers


def _use_document_migration(monkeypatch, path):
    import core.database as database
    import core.database_migrations as migrations

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(migrations, "engine", engine)
    return database, migrations, engine


def test_document_subject_fresh_metadata_schema_has_split_indexes_and_guards(
    tmp_path, monkeypatch,
):
    database, _migrations, engine = _use_document_migration(
        monkeypatch, tmp_path / "fresh-documents.db"
    )
    database.Base.metadata.create_all(bind=engine)
    path = tmp_path / "fresh-documents.db"
    columns, indexes, index_sql, triggers = _document_schema(path)

    assert columns["creator_subject_id"][3] == 0
    assert set(indexes) >= {
        "ix_documents_creator_subject_id",
        "ux_documents_universal_inbox_subject_source",
        "ux_documents_universal_inbox_legacy_source",
    }
    assert "creator_subject_id IS NOT NULL" in index_sql[
        "ux_documents_universal_inbox_subject_source"
    ]
    assert "creator_subject_id IS NULL" in index_sql[
        "ux_documents_universal_inbox_legacy_source"
    ]
    assert set(triggers) == {
        "trg_documents_creator_subject_id_insert",
        "trg_documents_creator_subject_id_immutable",
    }

    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO documents "
            "(id, title, current_content, created_at, updated_at, creator_subject_id) "
            "VALUES ('valid', 'title', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)",
            (_DOCUMENT_SID_A,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO documents "
                "(id, title, current_content, created_at, updated_at, creator_subject_id) "
                "VALUES ('invalid', 'title', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)",
                ("owner_" + "A" * 32,),
            )


def test_document_subject_legacy_convergence_is_nullable_indexed_and_idempotent(
    tmp_path, monkeypatch,
):
    path = tmp_path / "legacy-documents.db"
    _create_legacy_documents(path, creator_subject=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO documents (id, owner, creator_subject_id) VALUES (?, ?, ?)",
            ("bound", "old-alias", _DOCUMENT_SID_A),
        )
        connection.execute(
            "INSERT INTO documents (id, owner, creator_subject_id) VALUES (?, ?, NULL)",
            ("legacy", "legacy-alias"),
        )
    _database, migrations, _engine = _use_document_migration(monkeypatch, path)

    migrations._migrate_add_universal_inbox_working_copy_cols()
    migrations._migrate_add_universal_inbox_working_copy_cols()

    columns, indexes, _index_sql, triggers = _document_schema(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT id, owner, creator_subject_id FROM documents ORDER BY id"
        ).fetchall()
    assert columns["creator_subject_id"][3] == 0
    assert {"source_kind", "source_ref_hash"} <= set(columns)
    assert "ux_documents_universal_inbox_source" not in indexes
    assert set(indexes) == {
        "ix_documents_creator_subject_id",
        "ux_documents_universal_inbox_subject_source",
        "ux_documents_universal_inbox_legacy_source",
        "sqlite_autoindex_documents_1",
    }
    assert set(triggers) == {
        "trg_documents_creator_subject_id_insert",
        "trg_documents_creator_subject_id_immutable",
    }
    assert rows == [
        ("bound", "old-alias", _DOCUMENT_SID_A),
        ("legacy", "legacy-alias", None),
    ]


@pytest.mark.parametrize(
    "invalid_sid",
    (
        "owner_" + "a" * 31,
        "owner_" + "a" * 33,
        "owner_" + "A" * 32,
        "owner_" + "a" * 31 + "-",
        "subject_" + "a" * 32,
        _DOCUMENT_SID_A + "\x00suffix",
        _DOCUMENT_SID_A + "\x00",
        sqlite3.Binary(_DOCUMENT_SID_A.encode("ascii")),
    ),
)
def test_document_subject_insert_grammar_is_exact(tmp_path, monkeypatch, invalid_sid):
    path = tmp_path / "document-sid-grammar.db"
    _create_legacy_documents(path)
    _database, migrations, _engine = _use_document_migration(monkeypatch, path)
    migrations._migrate_add_universal_inbox_working_copy_cols()

    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO documents (id, owner, creator_subject_id) VALUES (?, ?, ?)",
            ("valid", "alias", _DOCUMENT_SID_A),
        )
        connection.execute(
            "INSERT INTO documents (id, owner, creator_subject_id) VALUES (?, ?, NULL)",
            ("legacy", "alias"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO documents (id, owner, creator_subject_id) VALUES (?, ?, ?)",
                ("invalid", "alias", invalid_sid),
            )


def test_document_subject_is_permanently_immutable_but_same_value_updates_work(
    tmp_path, monkeypatch,
):
    path = tmp_path / "document-sid-immutable.db"
    _create_legacy_documents(path)
    _database, migrations, _engine = _use_document_migration(monkeypatch, path)
    migrations._migrate_add_universal_inbox_working_copy_cols()
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO documents (id, owner, creator_subject_id) VALUES (?, ?, ?)",
            (("legacy", "alias", None), ("bound", "alias", _DOCUMENT_SID_A)),
        )
        connection.commit()
        connection.execute(
            "UPDATE documents SET creator_subject_id = ? WHERE id = 'bound'",
            (_DOCUMENT_SID_A,),
        )
        connection.execute(
            "UPDATE documents SET creator_subject_id = NULL WHERE id = 'legacy'"
        )
        connection.commit()
        for document_id, value in (
            ("legacy", _DOCUMENT_SID_A),
            ("bound", None),
            ("bound", _DOCUMENT_SID_B),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE documents SET creator_subject_id = ? WHERE id = ?",
                    (value, document_id),
                )
            connection.rollback()


def test_document_working_copy_uniqueness_partitions_bound_and_legacy_rows(
    tmp_path, monkeypatch,
):
    path = tmp_path / "document-sid-unique.db"
    _create_legacy_documents(path)
    _database, migrations, _engine = _use_document_migration(monkeypatch, path)
    migrations._migrate_add_universal_inbox_working_copy_cols()
    insert = (
        "INSERT INTO documents "
        "(id, owner, creator_subject_id, source_kind, source_ref_hash) "
        "VALUES (?, ?, ?, 'email', 'sha256-ref')"
    )
    with sqlite3.connect(path) as connection:
        connection.execute(insert, ("bound-a", "alias", _DOCUMENT_SID_A))
        connection.execute(insert, ("bound-b", "alias", _DOCUMENT_SID_B))
        connection.execute(insert, ("legacy-a", "alias", None))
        connection.execute(insert, ("legacy-b", "other-alias", None))
        connection.commit()
        for row in (
            ("duplicate-bound", "renamed-alias", _DOCUMENT_SID_A),
            ("duplicate-legacy", "alias", None),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, row)
            connection.rollback()


def test_document_subject_migration_preserves_duplicate_legacy_rows_on_failure(
    tmp_path, monkeypatch,
):
    path = tmp_path / "document-sid-duplicates.db"
    _create_legacy_documents(path, provenance=True)
    rows = (
        ("one", "alias", "email", "same-ref"),
        ("two", "alias", "email", "same-ref"),
    )
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO documents (id, owner, source_kind, source_ref_hash) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
    _database, migrations, _engine = _use_document_migration(monkeypatch, path)

    with pytest.raises(RuntimeError) as caught:
        migrations._migrate_add_universal_inbox_working_copy_cols()
    assert caught.value.args == ("document_creator_subject_schema_migration_failed",)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    with sqlite3.connect(path) as connection:
        preserved = connection.execute(
            "SELECT id, owner, source_kind, source_ref_hash FROM documents ORDER BY id"
        ).fetchall()
    columns, indexes, _index_sql, triggers = _document_schema(path)
    assert preserved == list(rows)
    assert "creator_subject_id" not in columns
    assert set(indexes) == {"sqlite_autoindex_documents_1"}
    assert triggers == {}


def test_document_subject_migration_rejects_preexisting_nul_without_rewriting(
    tmp_path, monkeypatch,
):
    path = tmp_path / "document-sid-preexisting-nul.db"
    _create_legacy_documents(path, creator_subject=True)
    adversarial_sid = _DOCUMENT_SID_A + "\x00hidden-tail"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO documents (id, owner, creator_subject_id) VALUES (?, ?, ?)",
            ("preserved", "alias", adversarial_sid),
        )
    _database, migrations, _engine = _use_document_migration(monkeypatch, path)

    with pytest.raises(RuntimeError) as caught:
        migrations._migrate_add_universal_inbox_working_copy_cols()
    assert caught.value.args == ("document_creator_subject_schema_migration_failed",)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT id, owner, creator_subject_id FROM documents"
        ).fetchone()
    columns, indexes, _index_sql, triggers = _document_schema(path)
    assert row == ("preserved", "alias", adversarial_sid)
    assert "source_kind" not in columns and "source_ref_hash" not in columns
    assert set(indexes) == {"sqlite_autoindex_documents_1"}
    assert triggers == {}


def test_document_subject_late_ddl_failure_rolls_back_and_restart_converges(
    tmp_path, monkeypatch,
):
    path = tmp_path / "document-sid-late-ddl.db"
    _create_legacy_documents(path, provenance=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE UNIQUE INDEX ux_documents_universal_inbox_source "
            "ON documents (owner, source_kind, source_ref_hash)"
        )
    _database, migrations, engine = _use_document_migration(monkeypatch, path)
    original_raw_connection = engine.raw_connection

    class FailingCursor:
        def __init__(self, cursor):
            self._cursor = cursor

        def execute(self, sql):
            if sql.startswith(
                "CREATE UNIQUE INDEX ux_documents_universal_inbox_subject_source"
            ):
                raise sqlite3.OperationalError("injected late DDL failure")
            return self._cursor.execute(sql)

        def close(self):
            self._cursor.close()

    class FailingRaw:
        def __init__(self, raw):
            self._raw = raw

        def cursor(self):
            return FailingCursor(self._raw.cursor())

        def commit(self):
            self._raw.commit()

        def rollback(self):
            self._raw.rollback()

        def close(self):
            self._raw.close()

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(
            engine, "raw_connection", lambda: FailingRaw(original_raw_connection())
        )
        with pytest.raises(RuntimeError) as caught:
            migrations._migrate_add_universal_inbox_working_copy_cols()
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    columns, indexes, _index_sql, triggers = _document_schema(path)
    assert "creator_subject_id" not in columns
    assert set(indexes) == {
        "sqlite_autoindex_documents_1",
        "ux_documents_universal_inbox_source",
    }
    assert triggers == {}

    migrations._migrate_add_universal_inbox_working_copy_cols()
    migrations._migrate_add_universal_inbox_working_copy_cols()
    columns, indexes, _index_sql, triggers = _document_schema(path)
    assert "creator_subject_id" in columns
    assert "ux_documents_universal_inbox_source" not in indexes
    assert set(indexes) >= {
        "ix_documents_creator_subject_id",
        "ux_documents_universal_inbox_subject_source",
        "ux_documents_universal_inbox_legacy_source",
    }
    assert set(triggers) == {
        "trg_documents_creator_subject_id_insert",
        "trg_documents_creator_subject_id_immutable",
    }

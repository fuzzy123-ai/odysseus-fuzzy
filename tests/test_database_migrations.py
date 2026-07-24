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

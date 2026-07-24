"""
update_database.py

This script updates the database schema by adding new columns to the sessions table
and populating them with appropriate values. It handles SQLite's limitations
with ALTER TABLE operations by checking if columns exist before attempting to add them.

The following columns are added:
- last_accessed (DateTime): Set to created_at for existing records
- is_important (Boolean): Set to False for existing records
- message_count (Integer): Calculated from the number of messages in chat_messages table

Usage:
    python update_database.py
"""

import json
import os
from pathlib import Path
import sqlite3


def migrate_tool_usage_schema(engine):
    """Create or verify the versioned TUA2 tables without touching domain data."""
    from sqlalchemy import inspect, insert, select
    from src.tool_usage_store import (
        TOOL_USAGE_SCHEMA_COMPONENT,
        TOOL_USAGE_SCHEMA_VERSION,
        TOOL_USAGE_TABLES,
        ToolUsageSchemaVersion,
    )

    before = set(inspect(engine).get_table_names())
    ToolUsageSchemaVersion.metadata.create_all(bind=engine, tables=TOOL_USAGE_TABLES)
    with engine.begin() as connection:
        existing = connection.execute(
            select(ToolUsageSchemaVersion.version).where(
                ToolUsageSchemaVersion.component == TOOL_USAGE_SCHEMA_COMPONENT
            )
        ).scalar_one_or_none()
        if existing is None:
            from datetime import datetime, timezone

            connection.execute(
                insert(ToolUsageSchemaVersion).values(
                    component=TOOL_USAGE_SCHEMA_COMPONENT,
                    version=TOOL_USAGE_SCHEMA_VERSION,
                    applied_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
        elif existing != TOOL_USAGE_SCHEMA_VERSION:
            raise RuntimeError("unsupported tool usage schema version")
    after = set(inspect(engine).get_table_names())
    created = sorted(after - before)
    return {
        "schema_version": TOOL_USAGE_SCHEMA_VERSION,
        "created_table_count": len(created),
        "changed": bool(created),
        "domain_tables_touched": False,
        "raw_content_visible": False,
    }


def rollback_tool_usage_schema(engine):
    """Drop only TUA2-owned tables; intended for explicit migration rollback."""
    from sqlalchemy import inspect
    from src.tool_usage_store import TOOL_USAGE_SCHEMA_VERSION, TOOL_USAGE_TABLES

    before = set(inspect(engine).get_table_names())
    for table in reversed(TOOL_USAGE_TABLES):
        table.drop(bind=engine, checkfirst=True)
    after = set(inspect(engine).get_table_names())
    return {
        "schema_version": TOOL_USAGE_SCHEMA_VERSION,
        "dropped_table_count": len(before - after),
        "rollback_applied": bool(before - after),
        "domain_tables_touched": False,
        "raw_content_visible": False,
    }


def _render_settings(settings):
    return json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _rendered_file_bytes(text):
    """Match text-mode newline translation used by atomic_write_text."""
    return text.replace("\n", os.linesep).encode("utf-8")


def migrate_tool_settings_file(path):
    """Apply TAX9 to one local JSON file and return redacted diagnostics."""
    from core.atomic_io import atomic_write_text
    from src.settings import migrate_tool_settings

    target = Path(path)
    if target.exists():
        original_bytes = target.read_bytes()
        settings = json.loads(original_bytes.decode("utf-8"))
    else:
        original_bytes = None
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError("settings file must contain a JSON object")

    migrated, report = migrate_tool_settings(settings)
    rendered = _render_settings(migrated)
    file_changed = _rendered_file_bytes(rendered) != original_bytes
    if file_changed:
        atomic_write_text(str(target), rendered)
    return {**report, "file_changed": file_changed}


def rollback_tool_settings_file(path):
    """Restore the pre-TAX9 settings shape in one local JSON file."""
    from core.atomic_io import atomic_write_text
    from src.settings import rollback_tool_settings

    target = Path(path)
    settings = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise ValueError("settings file must contain a JSON object")
    restored = rollback_tool_settings(settings)
    rendered = _render_settings(restored)
    changed = target.read_bytes() != _rendered_file_bytes(rendered)
    if changed:
        atomic_write_text(str(target), rendered)
    return {"schema_version": 1, "file_changed": changed, "rollback_applied": True}

def check_column_exists(engine, table_name, column_name):
    """Check if a column exists in a table."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    columns = inspector.get_columns(table_name)
    return any(col['name'] == column_name for col in columns)

def add_column_sqlite(db_path, table_name, column_name, column_type, default_value=None):
    """
    Add a column to a SQLite table by creating a new table, copying data, and renaming.
    This is necessary because SQLite has limited ALTER TABLE support.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get current table info
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]
    
    # Create new table with additional column
    new_table_name = f"{table_name}_new"
    
    # Build new column list
    new_columns = []
    for col in columns:
        new_columns.append(f"{col[1]} {col[2]}")
    
    # Add the new column
    new_column_def = f"{column_name} {column_type}"
    if default_value is not None:
        new_column_def += f" DEFAULT {default_value}"
    new_columns.append(new_column_def)
    
    # Create new table
    columns_sql = ", ".join(new_columns)
    create_sql = f"CREATE TABLE {new_table_name} ({columns_sql})"
    cursor.execute(create_sql)
    
    # Copy data from old table to new table
    column_names_str = ", ".join(column_names)
    insert_sql = f"INSERT INTO {new_table_name} ({column_names_str}) SELECT {column_names_str} FROM {table_name}"
    cursor.execute(insert_sql)
    
    # Drop old table and rename new table
    cursor.execute(f"DROP TABLE {table_name}")
    cursor.execute(f"ALTER TABLE {new_table_name} RENAME TO {table_name}")
    
    conn.commit()
    conn.close()

def update_database():
    """Update the database schema and populate new columns."""
    from database import DATABASE_URL, SessionLocal
    from sqlalchemy import create_engine, text
    from src.constants import SETTINGS_FILE

    settings_report = migrate_tool_settings_file(SETTINGS_FILE)
    print(
        "Tool settings migration: "
        f"version={settings_report['schema_version']} "
        f"changed={settings_report['file_changed']} "
        f"aliases={settings_report['alias_migration_count']} "
        f"quarantined={settings_report['quarantine_count']}"
    )
    # Create engine from DATABASE_URL
    engine = create_engine(DATABASE_URL)
    usage_schema_report = migrate_tool_usage_schema(engine)
    print(
        "Tool usage schema migration: "
        f"version={usage_schema_report['schema_version']} "
        f"created={usage_schema_report['created_table_count']}"
    )
    
    # Extract database path from DATABASE_URL for SQLite
    db_path = None
    if "sqlite" in DATABASE_URL:
        db_path = DATABASE_URL.replace("sqlite:///", "")
        # Handle relative paths
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.path.dirname(__file__), db_path)
    
    print("Updating database schema...")
    
    # Start a transaction
    db = SessionLocal()
    try:
        # Add last_accessed column if it doesn't exist
        if not check_column_exists(engine, 'sessions', 'last_accessed'):
            print("Adding last_accessed column...")
            if db_path:  # SQLite
                add_column_sqlite(db_path, 'sessions', 'last_accessed', 'DATETIME')
            else:  # Other databases
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE sessions ADD COLUMN last_accessed DATETIME"))
                    conn.commit()
        
        # Add is_important column if it doesn't exist
        if not check_column_exists(engine, 'sessions', 'is_important'):
            print("Adding is_important column...")
            if db_path:  # SQLite
                add_column_sqlite(db_path, 'sessions', 'is_important', 'BOOLEAN', '0')
            else:  # Other databases
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE sessions ADD COLUMN is_important BOOLEAN DEFAULT FALSE"))
                    conn.commit()
        
        # Add message_count column if it doesn't exist
        if not check_column_exists(engine, 'sessions', 'message_count'):
            print("Adding message_count column...")
            if db_path:  # SQLite
                add_column_sqlite(db_path, 'sessions', 'message_count', 'INTEGER', '0')
            else:  # Other databases
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE sessions ADD COLUMN message_count INTEGER DEFAULT 0"))
                    conn.commit()
        
        # Populate last_accessed with created_at for existing records where last_accessed is NULL
        print("Populating last_accessed column...")
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE sessions 
                SET last_accessed = created_at 
                WHERE last_accessed IS NULL
            """))
            conn.commit()
        
        # Populate is_important with FALSE for existing records where is_important is NULL
        print("Populating is_important column...")
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE sessions 
                SET is_important = 0 
                WHERE is_important IS NULL
            """))
            conn.commit()
        
        # Calculate and populate message_count from chat_messages table
        print("Calculating and populating message_count column...")
        with engine.connect() as conn:
            # First, set all message_count to 0
            conn.execute(text("UPDATE sessions SET message_count = 0"))
            
            # Then, count messages for each session and update
            conn.execute(text("""
                UPDATE sessions 
                SET message_count = (
                    SELECT COUNT(*) 
                    FROM chat_messages 
                    WHERE chat_messages.session_id = sessions.id
                )
            """))
            conn.commit()

        # TUA2: create the privacy-safe tool-usage event and daily aggregate
        # tables only for the current SQLite foundation. The migration is
        # idempotent and stores no events by itself.
        if db_path:
            from src.tool_usage_store import ToolUsageStore

            print("Ensuring privacy-safe tool usage schema...")
            with ToolUsageStore(db_path) as tool_usage_store:
                tool_usage_store.migrate()

        # TAX9: canonicalize legacy tool aliases and apply deferred-family
        # defaults once. The helper preserves an exact rollback packet and
        # returns aggregate counts only, so this script never prints IDs or
        # settings/provider values.
        from src.settings import migrate_tool_settings_file

        print("Ensuring versioned tool settings schema...")
        tool_settings_report = migrate_tool_settings_file()
        if tool_settings_report is not None:
            print(
                "Tool settings schema ready: "
                f"version={tool_settings_report['to_version']} "
                f"aliases={tool_settings_report['alias_rewrite_count']} "
                f"quarantined={tool_settings_report['quarantined_count']}"
            )
        
        print("Database update completed successfully!")
        
    except Exception as e:
        print(f"Database update failed ({type(e).__name__}); details were redacted.")
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    update_database()

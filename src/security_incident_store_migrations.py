"""Deterministic, isolated SQLite migrations for the incident action store."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
import re
from typing import Final


SCHEMA_VERSION: Final = 1
SQLITE_APPLICATION_ID: Final = 0x4F534952  # "OSIR"


class SecurityIncidentMigrationError(RuntimeError):
    """Raised when a local incident-store schema is not the expected store."""


_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "incidents": frozenset({"incident_id", "incident_ref", "version", "created_at"}),
    "actions": frozenset({
        "action_id", "incident_id", "action_type", "state", "version",
        "scope_fingerprint", "policy_revision", "idempotency_key",
        "request_fingerprint", "ttl_seconds", "expires_at", "receipt_ref",
        "verification_ref", "failure_ref", "rollback_ref", "created_at", "updated_at",
    }),
    "approvals": frozenset({
        "approval_id", "action_id", "action_version", "scope_fingerprint",
        "policy_revision", "approval_ref", "approved_at", "consumed_at",
    }),
    "audit_references": frozenset({
        "sequence", "incident_id", "action_id", "action_version", "event_type",
        "reference", "occurred_at",
    }),
}
_TABLE_INFO: Final = {
    "incidents": {"incident_id": (0, 1), "incident_ref": (1, 0), "version": (1, 0), "created_at": (1, 0)},
    "actions": {
        "action_id": (0, 1), "incident_id": (1, 0), "action_type": (1, 0), "state": (1, 0), "version": (1, 0),
        "scope_fingerprint": (1, 0), "policy_revision": (1, 0), "idempotency_key": (1, 0), "request_fingerprint": (1, 0),
        "ttl_seconds": (1, 0), "expires_at": (1, 0), "receipt_ref": (1, 0), "verification_ref": (1, 0),
        "failure_ref": (1, 0), "rollback_ref": (1, 0), "created_at": (1, 0), "updated_at": (1, 0),
    },
    "approvals": {
        "approval_id": (0, 1), "action_id": (1, 0), "action_version": (1, 0), "scope_fingerprint": (1, 0),
        "policy_revision": (1, 0), "approval_ref": (1, 0), "approved_at": (1, 0), "consumed_at": (0, 0),
    },
    "audit_references": {
        "sequence": (0, 1), "incident_id": (1, 0), "action_id": (0, 0), "action_version": (1, 0),
        "event_type": (1, 0), "reference": (1, 0), "occurred_at": (1, 0),
    },
}
_FOREIGN_KEYS: Final = {
    "actions": frozenset({("incident_id", "incidents", "incident_id", "RESTRICT", "RESTRICT")}),
    "approvals": frozenset({("action_id", "actions", "action_id", "RESTRICT", "RESTRICT")}),
    "audit_references": frozenset({
        ("incident_id", "incidents", "incident_id", "RESTRICT", "RESTRICT"),
        ("action_id", "actions", "action_id", "RESTRICT", "RESTRICT"),
    }),
}
_INDEXES: Final = {
    "actions_incident_idx": ("actions", ("incident_id", "created_at"), False),
    "audit_action_idx": ("audit_references", ("action_id", "sequence"), False),
}
_TABLE_SQL: Final = {
    "incidents": """CREATE TABLE incidents (
            incident_id TEXT PRIMARY KEY,
            incident_ref TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version >= 1),
            created_at REAL NOT NULL
        )""",
    "actions": """CREATE TABLE actions (
            action_id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('proposed','prepared','approved','denied','expired','executing','executed','verified','failed','rolled_back')),
            version INTEGER NOT NULL CHECK(version >= 1),
            scope_fingerprint TEXT NOT NULL,
            policy_revision TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            request_fingerprint TEXT NOT NULL,
            ttl_seconds REAL NOT NULL CHECK(ttl_seconds >= 1 AND ttl_seconds <= 86400),
            expires_at REAL NOT NULL,
            receipt_ref TEXT NOT NULL DEFAULT '',
            verification_ref TEXT NOT NULL DEFAULT '',
            failure_ref TEXT NOT NULL DEFAULT '',
            rollback_ref TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(incident_id) REFERENCES incidents(incident_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT
        )""",
    "approvals": """CREATE TABLE approvals (
            approval_id TEXT PRIMARY KEY,
            action_id TEXT NOT NULL UNIQUE,
            action_version INTEGER NOT NULL CHECK(action_version >= 1),
            scope_fingerprint TEXT NOT NULL,
            policy_revision TEXT NOT NULL,
            approval_ref TEXT NOT NULL,
            approved_at REAL NOT NULL,
            consumed_at REAL,
            FOREIGN KEY(action_id) REFERENCES actions(action_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT
        )""",
    "audit_references": """CREATE TABLE audit_references (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            action_id TEXT,
            action_version INTEGER NOT NULL DEFAULT 0 CHECK(action_version >= 0),
            event_type TEXT NOT NULL,
            reference TEXT NOT NULL,
            occurred_at REAL NOT NULL,
            FOREIGN KEY(incident_id) REFERENCES incidents(incident_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT,
            FOREIGN KEY(action_id) REFERENCES actions(action_id)
                ON UPDATE RESTRICT ON DELETE RESTRICT,
            CHECK((action_id IS NULL AND action_version = 0) OR (action_id IS NOT NULL AND action_version >= 1))
        )""",
}
_INDEX_SQL: Final = {
    "actions_incident_idx": "CREATE INDEX actions_incident_idx ON actions(incident_id, created_at)",
    "audit_action_idx": "CREATE INDEX audit_action_idx ON audit_references(action_id, sequence)",
}
_TRIGGER_SQL: Final = {
    "security_incident_audit_no_update": """CREATE TRIGGER security_incident_audit_no_update
        BEFORE UPDATE ON audit_references BEGIN
            SELECT RAISE(ABORT, 'incident audit is append-only');
        END""",
    "security_incident_audit_no_delete": """CREATE TRIGGER security_incident_audit_no_delete
        BEFORE DELETE ON audit_references BEGIN
            SELECT RAISE(ABORT, 'incident audit is append-only');
        END""",
}
_REQUIRED_TRIGGERS: Final = frozenset({
    "security_incident_audit_no_update", "security_incident_audit_no_delete",
})


def _migration_1(db: sqlite3.Connection) -> None:
    for statement in _TABLE_SQL.values():
        db.execute(statement)
    for statement in _INDEX_SQL.values():
        db.execute(statement)
    for statement in _TRIGGER_SQL.values():
        db.execute(statement)


_MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_migration_1,)


def apply_migrations(db: sqlite3.Connection) -> int:
    """Atomically create or validate exactly this store's current schema."""

    if db.in_transaction:
        raise SecurityIncidentMigrationError("migration connection is already in a transaction")
    current = int(db.execute("PRAGMA user_version").fetchone()[0])
    application_id = int(db.execute("PRAGMA application_id").fetchone()[0])
    if current < 0 or current > SCHEMA_VERSION:
        raise SecurityIncidentMigrationError("unsupported incident-store schema version")
    if current == SCHEMA_VERSION:
        validate_schema(db)
        return current
    if current != 0 or application_id != 0:
        raise SecurityIncidentMigrationError("SQLite database is not an empty incident store")
    existing = db.execute(
        "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    if existing is not None:
        raise SecurityIncidentMigrationError("SQLite database is not empty for incident-store migration")
    try:
        db.execute("BEGIN IMMEDIATE")
        for version in range(1, SCHEMA_VERSION + 1):
            _MIGRATIONS[version - 1](db)
            db.execute(f"PRAGMA user_version={version}")
        db.execute(f"PRAGMA application_id={SQLITE_APPLICATION_ID}")
        db.execute("COMMIT")
    except BaseException as exc:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise SecurityIncidentMigrationError("incident-store migration failed") from exc
    validate_schema(db)
    return SCHEMA_VERSION


def validate_schema(db: sqlite3.Connection) -> None:
    """Fail closed on identity, version, object, or foreign-key mismatch."""

    if int(db.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
        raise SecurityIncidentMigrationError("unexpected incident-store schema version")
    if int(db.execute("PRAGMA application_id").fetchone()[0]) != SQLITE_APPLICATION_ID:
        raise SecurityIncidentMigrationError("SQLite database has the wrong incident-store identity")
    for table, required_columns in _TABLE_COLUMNS.items():
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        actual = {str(row[1]) for row in rows}
        contract = {str(row[1]): (int(row[3]), int(row[5])) for row in rows}
        if actual != required_columns or contract != _TABLE_INFO[table]:
            raise SecurityIncidentMigrationError("incident-store table schema does not match")
        sql_row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        sql = "" if sql_row is None or sql_row[0] is None else _normalize_sql(str(sql_row[0]))
        if sql != _normalize_sql(_TABLE_SQL[table]):
            raise SecurityIncidentMigrationError("incident-store table schema contract does not match")
    for table, expected in _FOREIGN_KEYS.items():
        actual = frozenset(
            (str(row[3]), str(row[2]), str(row[4]), str(row[5]).upper(), str(row[6]).upper())
            for row in db.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        )
        if actual != expected:
            raise SecurityIncidentMigrationError("incident-store foreign-key schema does not match")
    for index, (table, columns, unique) in _INDEXES.items():
        index_row = next((row for row in db.execute(f"PRAGMA index_list({table})").fetchall() if str(row[1]) == index), None)
        if index_row is None or bool(index_row[2]) != unique:
            raise SecurityIncidentMigrationError("incident-store indexes do not match")
        actual_columns = tuple(str(row[2]) for row in db.execute(f"PRAGMA index_info({index})").fetchall())
        if actual_columns != columns:
            raise SecurityIncidentMigrationError("incident-store index columns do not match")
        sql_row = db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index,)).fetchone()
        if sql_row is None or sql_row[0] is None or _normalize_sql(str(sql_row[0])) != _normalize_sql(_INDEX_SQL[index]):
            raise SecurityIncidentMigrationError("incident-store index schema contract does not match")
    triggers = {
        str(row[0])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
    }
    if not _REQUIRED_TRIGGERS.issubset(triggers):
        raise SecurityIncidentMigrationError("incident-store audit append-only triggers are missing")
    for trigger in _REQUIRED_TRIGGERS:
        sql_row = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,)).fetchone()
        sql = "" if sql_row is None or sql_row[0] is None else _normalize_sql(str(sql_row[0]))
        if sql != _normalize_sql(_TRIGGER_SQL[trigger]):
            raise SecurityIncidentMigrationError("incident-store audit append-only trigger does not match")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SecurityIncidentMigrationError("incident-store foreign-key check failed")


def _normalize_sql(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


__all__ = [
    "SCHEMA_VERSION",
    "SQLITE_APPLICATION_ID",
    "SecurityIncidentMigrationError",
    "apply_migrations",
    "validate_schema",
]

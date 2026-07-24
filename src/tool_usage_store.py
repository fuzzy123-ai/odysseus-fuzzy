"""SQLite persistence and retention foundation for privacy-safe tool usage."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading
from typing import Iterable

from src.tool_usage_events import ToolUsageEventKind, ToolUsageEventV1


SCHEMA_NAME = "odysseus.tool_usage_store"
SCHEMA_VERSION = 2
DEFAULT_EVENT_RETENTION_DAYS = 90
DEFAULT_DAILY_RETENTION_DAYS = 400
_MAX_FAILURE_COUNT = 1_000_000
DURATION_BUCKET_BOUNDS_MS = (10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 60000)
DURATION_BUCKET_COLUMNS = tuple(f"duration_le_{bound}" for bound in DURATION_BUCKET_BOUNDS_MS)
DURATION_OVERFLOW_COLUMN = "duration_gt_60000"


class ToolUsageStoreError(RuntimeError):
    """Raised for bounded store contract failures without raw DB content."""


@dataclass(frozen=True, slots=True)
class StoreWriteResult:
    accepted_count: int
    duplicate_count: int
    persistence_rejected_count: int
    failure_count: int

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "accepted_count": self.accepted_count,
            "duplicate_count": self.duplicate_count,
            "persistence_rejected_count": self.persistence_rejected_count,
            "failure_count": self.failure_count,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class RetentionResult:
    dry_run: bool
    event_cutoff: str
    daily_cutoff: str
    scanned_event_count: int
    deletable_event_count: int
    protected_event_count: int
    deletable_daily_count: int
    deleted_event_count: int
    deleted_daily_count: int

    def to_dict(self) -> dict[str, int | str | bool]:
        return {
            "dry_run": self.dry_run,
            "event_cutoff": self.event_cutoff,
            "daily_cutoff": self.daily_cutoff,
            "scanned_event_count": self.scanned_event_count,
            "deletable_event_count": self.deletable_event_count,
            "protected_event_count": self.protected_event_count,
            "deletable_daily_count": self.deletable_daily_count,
            "deleted_event_count": self.deleted_event_count,
            "deleted_daily_count": self.deleted_daily_count,
            "raw_content_visible": False,
        }


_MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS tool_usage_schema_meta (
        schema_name TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_usage_events (
        event_id TEXT PRIMARY KEY,
        invocation_id TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        duration_ms INTEGER,
        tool_analytics_id TEXT NOT NULL,
        tool_family TEXT NOT NULL,
        tool_source TEXT NOT NULL,
        surface TEXT NOT NULL,
        status TEXT,
        error_class TEXT,
        blocked_reason_code TEXT,
        retry_ordinal INTEGER NOT NULL,
        argument_size_bucket TEXT NOT NULL,
        result_size_bucket TEXT NOT NULL,
        result_shape_bucket TEXT NOT NULL,
        owner_ref TEXT,
        session_ref TEXT,
        run_ref TEXT,
        correlation_ref TEXT,
        reference_state TEXT NOT NULL,
        model_scope TEXT NOT NULL,
        agent_mode TEXT NOT NULL,
        app_version TEXT NOT NULL,
        UNIQUE(invocation_id, event_kind)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_usage_daily (
        day TEXT NOT NULL,
        tool_analytics_id TEXT NOT NULL,
        tool_family TEXT NOT NULL,
        tool_source TEXT NOT NULL,
        surface TEXT NOT NULL,
        status TEXT NOT NULL,
        invocation_count INTEGER NOT NULL DEFAULT 0,
        duration_count INTEGER NOT NULL DEFAULT 0,
        duration_total_ms INTEGER NOT NULL DEFAULT 0,
        distinct_owner_count INTEGER NOT NULL DEFAULT 0,
        distinct_session_count INTEGER NOT NULL DEFAULT 0,
        retry_count INTEGER NOT NULL DEFAULT 0,
        unknown_identity_count INTEGER NOT NULL DEFAULT 0,
        duration_le_10 INTEGER NOT NULL DEFAULT 0,
        duration_le_50 INTEGER NOT NULL DEFAULT 0,
        duration_le_100 INTEGER NOT NULL DEFAULT 0,
        duration_le_250 INTEGER NOT NULL DEFAULT 0,
        duration_le_500 INTEGER NOT NULL DEFAULT 0,
        duration_le_1000 INTEGER NOT NULL DEFAULT 0,
        duration_le_2500 INTEGER NOT NULL DEFAULT 0,
        duration_le_5000 INTEGER NOT NULL DEFAULT 0,
        duration_le_10000 INTEGER NOT NULL DEFAULT 0,
        duration_le_60000 INTEGER NOT NULL DEFAULT 0,
        duration_gt_60000 INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(day, tool_analytics_id, surface, status)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_usage_daily_quality (
        day TEXT PRIMARY KEY,
        invocation_count INTEGER NOT NULL DEFAULT 0,
        started_count INTEGER NOT NULL DEFAULT 0,
        terminal_count INTEGER NOT NULL DEFAULT 0,
        complete_count INTEGER NOT NULL DEFAULT 0,
        incomplete_count INTEGER NOT NULL DEFAULT 0,
        distinct_owner_count INTEGER NOT NULL DEFAULT 0,
        distinct_session_count INTEGER NOT NULL DEFAULT 0,
        unknown_identity_count INTEGER NOT NULL DEFAULT 0,
        duplicates_rejected INTEGER NOT NULL DEFAULT 0,
        writer_failures INTEGER NOT NULL DEFAULT 0,
        aggregation_complete INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_tool_usage_events_occurred_at ON tool_usage_events(occurred_at)",
    "CREATE INDEX IF NOT EXISTS ix_tool_usage_events_tool_status ON tool_usage_events(tool_analytics_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_tool_usage_events_refs ON tool_usage_events(owner_ref, session_ref, run_ref)",
    "CREATE INDEX IF NOT EXISTS ix_tool_usage_daily_day ON tool_usage_daily(day)",
    "CREATE INDEX IF NOT EXISTS ix_tool_usage_daily_quality_day ON tool_usage_daily_quality(day)",
)


_EVENT_COLUMNS = (
    "event_id",
    "invocation_id",
    "event_kind",
    "occurred_at",
    "duration_ms",
    "tool_analytics_id",
    "tool_family",
    "tool_source",
    "surface",
    "status",
    "error_class",
    "blocked_reason_code",
    "retry_ordinal",
    "argument_size_bucket",
    "result_size_bucket",
    "result_shape_bucket",
    "owner_ref",
    "session_ref",
    "run_ref",
    "correlation_ref",
    "reference_state",
    "model_scope",
    "agent_mode",
    "app_version",
)


class ToolUsageStore:
    def __init__(self, database: str | Path | sqlite3.Connection):
        self._lock = threading.RLock()
        self._failure_counts: Counter[str] = Counter()
        self._quality_counts: Counter[str] = Counter()
        if isinstance(database, sqlite3.Connection):
            self._connection = database
            self._owns_connection = False
        else:
            self._connection = sqlite3.connect(
                str(database),
                timeout=30,
                check_same_thread=False,
                isolation_level=None,
            )
            self._owns_connection = True
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")

    def migrate(self) -> dict[str, int | str | bool]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                for statement in _MIGRATION_STATEMENTS:
                    self._connection.execute(statement)
                row = self._connection.execute(
                    "SELECT schema_version FROM tool_usage_schema_meta WHERE schema_name = ?",
                    (SCHEMA_NAME,),
                ).fetchone()
                if row is None:
                    self._connection.execute(
                        "INSERT INTO tool_usage_schema_meta(schema_name, schema_version) VALUES (?, ?)",
                        (SCHEMA_NAME, SCHEMA_VERSION),
                    )
                elif int(row["schema_version"]) == 1:
                    self._migrate_v1_daily_columns()
                    self._connection.execute(
                        "UPDATE tool_usage_schema_meta SET schema_version = ? WHERE schema_name = ?",
                        (SCHEMA_VERSION, SCHEMA_NAME),
                    )
                elif int(row["schema_version"]) != SCHEMA_VERSION:
                    raise ToolUsageStoreError("unsupported tool usage schema version")
                self._connection.commit()
            except ToolUsageStoreError:
                self._rollback_safely()
                raise
            except sqlite3.Error as exc:
                self._rollback_safely()
                raise ToolUsageStoreError("tool usage schema migration failed") from exc
        return {
            "schema_name": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "event_table_ready": True,
            "daily_table_ready": True,
            "raw_content_visible": False,
        }

    def _migrate_v1_daily_columns(self) -> None:
        existing = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(tool_usage_daily)"
            ).fetchall()
        }
        additions = {
            "distinct_owner_count": "INTEGER NOT NULL DEFAULT 0",
            "distinct_session_count": "INTEGER NOT NULL DEFAULT 0",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "unknown_identity_count": "INTEGER NOT NULL DEFAULT 0",
            **{
                column: "INTEGER NOT NULL DEFAULT 0"
                for column in (*DURATION_BUCKET_COLUMNS, DURATION_OVERFLOW_COLUMN)
            },
        }
        for column, declaration in additions.items():
            if column not in existing:
                self._connection.execute(
                    f"ALTER TABLE tool_usage_daily ADD COLUMN {column} {declaration}"
                )

    def append_events(self, events: Iterable[ToolUsageEventV1]) -> StoreWriteResult:
        batch = tuple(events)
        if not all(isinstance(event, ToolUsageEventV1) for event in batch):
            raise ToolUsageStoreError("event batch must contain ToolUsageEventV1 only")
        accepted = 0
        duplicates = 0
        rejected = 0
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                for event in batch:
                    if not event.persistence_allowed:
                        rejected += 1
                        continue
                    payload = event.to_dict()
                    placeholders = ",".join("?" for _ in _EVENT_COLUMNS)
                    columns = ",".join(_EVENT_COLUMNS)
                    cursor = self._connection.execute(
                        f"INSERT OR IGNORE INTO tool_usage_events ({columns}) VALUES ({placeholders})",
                        tuple(payload[column] for column in _EVENT_COLUMNS),
                    )
                    if cursor.rowcount != 1:
                        duplicates += 1
                        continue
                    accepted += 1
                    if event.event_kind == ToolUsageEventKind.TERMINAL:
                        self._upsert_daily(event)
                self._connection.commit()
            except sqlite3.Error as exc:
                self._rollback_safely()
                raise ToolUsageStoreError("tool usage event transaction failed") from exc
            except Exception:
                self._rollback_safely()
                raise
            self._quality_counts["duplicates_rejected"] = min(
                _MAX_FAILURE_COUNT,
                self._quality_counts["duplicates_rejected"] + duplicates,
            )
            self._quality_counts["persistence_rejected"] = min(
                _MAX_FAILURE_COUNT,
                self._quality_counts["persistence_rejected"] + rejected,
            )
        return StoreWriteResult(accepted, duplicates, rejected, 0)

    def append_best_effort(self, events: Iterable[ToolUsageEventV1]) -> StoreWriteResult:
        batch = tuple(events)
        try:
            return self.append_events(batch)
        except ToolUsageStoreError:
            self._record_failure("store_failure")
            self._record_quality("writer_failures")
            return StoreWriteResult(0, 0, 0, len(batch))
        except Exception:
            self._record_failure("validation_failure")
            self._record_quality("writer_failures")
            return StoreWriteResult(0, 0, 0, len(batch))

    def _upsert_daily(self, event: ToolUsageEventV1) -> None:
        status = event.status.value if event.status else "unknown"
        duration_count = 1 if event.duration_ms is not None else 0
        duration_total = event.duration_ms or 0
        bucket_counts = tuple(
            1
            if event.duration_ms is not None and event.duration_ms <= bound
            else 0
            for bound in DURATION_BUCKET_BOUNDS_MS
        )
        overflow_count = (
            1
            if event.duration_ms is not None
            and event.duration_ms > DURATION_BUCKET_BOUNDS_MS[-1]
            else 0
        )
        retry_count = int(event.retry_ordinal)
        unknown_identity_count = (
            1 if event.tool_analytics_id.endswith(".unclassified") else 0
        )
        self._connection.execute(
            """
            INSERT INTO tool_usage_daily (
                day, tool_analytics_id, tool_family, tool_source, surface, status,
                invocation_count, duration_count, duration_total_ms,
                distinct_owner_count, distinct_session_count, retry_count,
                unknown_identity_count, duration_le_10, duration_le_50,
                duration_le_100, duration_le_250, duration_le_500,
                duration_le_1000, duration_le_2500, duration_le_5000,
                duration_le_10000, duration_le_60000, duration_gt_60000
            ) VALUES (
                ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, 0, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(day, tool_analytics_id, surface, status) DO UPDATE SET
                invocation_count = invocation_count + 1,
                duration_count = duration_count + excluded.duration_count,
                duration_total_ms = duration_total_ms + excluded.duration_total_ms,
                retry_count = retry_count + excluded.retry_count,
                unknown_identity_count = unknown_identity_count + excluded.unknown_identity_count,
                duration_le_10 = duration_le_10 + excluded.duration_le_10,
                duration_le_50 = duration_le_50 + excluded.duration_le_50,
                duration_le_100 = duration_le_100 + excluded.duration_le_100,
                duration_le_250 = duration_le_250 + excluded.duration_le_250,
                duration_le_500 = duration_le_500 + excluded.duration_le_500,
                duration_le_1000 = duration_le_1000 + excluded.duration_le_1000,
                duration_le_2500 = duration_le_2500 + excluded.duration_le_2500,
                duration_le_5000 = duration_le_5000 + excluded.duration_le_5000,
                duration_le_10000 = duration_le_10000 + excluded.duration_le_10000,
                duration_le_60000 = duration_le_60000 + excluded.duration_le_60000,
                duration_gt_60000 = duration_gt_60000 + excluded.duration_gt_60000
            """,
            (
                event.occurred_at[:10],
                event.tool_analytics_id,
                event.tool_family.value,
                event.tool_source.value,
                event.surface.value,
                status,
                duration_count,
                duration_total,
                retry_count,
                unknown_identity_count,
                *bucket_counts,
                overflow_count,
            ),
        )

    def _aggregation_event_rows(self, day: str) -> tuple[dict[str, object], ...]:
        """Internal aggregate-only input; never exposed through an HTTP/raw API."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT invocation_id, event_kind, duration_ms, tool_analytics_id,
                       tool_family, tool_source, surface, status, retry_ordinal,
                       owner_ref, session_ref
                FROM tool_usage_events
                WHERE substr(occurred_at, 1, 10) = ?
                ORDER BY invocation_id, event_kind
                """,
                (day,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _replace_daily_analytics(
        self,
        day: str,
        rows: Iterable[dict[str, object]],
        quality: dict[str, int],
    ) -> None:
        batch = tuple(rows)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    "DELETE FROM tool_usage_daily WHERE day = ?",
                    (day,),
                )
                for row in batch:
                    self._connection.execute(
                        """
                        INSERT INTO tool_usage_daily (
                            day, tool_analytics_id, tool_family, tool_source,
                            surface, status, invocation_count, duration_count,
                            duration_total_ms, distinct_owner_count,
                            distinct_session_count, retry_count,
                            unknown_identity_count, duration_le_10,
                            duration_le_50, duration_le_100, duration_le_250,
                            duration_le_500, duration_le_1000,
                            duration_le_2500, duration_le_5000,
                            duration_le_10000, duration_le_60000,
                            duration_gt_60000
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            day,
                            row["tool_analytics_id"],
                            row["tool_family"],
                            row["tool_source"],
                            row["surface"],
                            row["status"],
                            row["invocation_count"],
                            row["duration_count"],
                            row["duration_total_ms"],
                            row["distinct_owner_count"],
                            row["distinct_session_count"],
                            row["retry_count"],
                            row["unknown_identity_count"],
                            *(row[column] for column in DURATION_BUCKET_COLUMNS),
                            row[DURATION_OVERFLOW_COLUMN],
                        ),
                    )
                self._connection.execute(
                    """
                    INSERT INTO tool_usage_daily_quality (
                        day, invocation_count, started_count, terminal_count,
                        complete_count, incomplete_count, distinct_owner_count,
                        distinct_session_count, unknown_identity_count,
                        duplicates_rejected, writer_failures,
                        aggregation_complete
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(day) DO UPDATE SET
                        invocation_count = excluded.invocation_count,
                        started_count = excluded.started_count,
                        terminal_count = excluded.terminal_count,
                        complete_count = excluded.complete_count,
                        incomplete_count = excluded.incomplete_count,
                        distinct_owner_count = excluded.distinct_owner_count,
                        distinct_session_count = excluded.distinct_session_count,
                        unknown_identity_count = excluded.unknown_identity_count,
                        duplicates_rejected = excluded.duplicates_rejected,
                        writer_failures = excluded.writer_failures,
                        aggregation_complete = 1
                    """,
                    (
                        day,
                        quality["invocation_count"],
                        quality["started_count"],
                        quality["terminal_count"],
                        quality["complete_count"],
                        quality["incomplete_count"],
                        quality["distinct_owner_count"],
                        quality["distinct_session_count"],
                        quality["unknown_identity_count"],
                        quality["duplicates_rejected"],
                        quality["writer_failures"],
                    ),
                )
                self._connection.commit()
            except sqlite3.Error as exc:
                self._rollback_safely()
                raise ToolUsageStoreError("tool usage daily aggregation failed") from exc

    def _daily_analytics_rows(
        self,
        start_day: str,
        end_day: str,
    ) -> tuple[dict[str, object], ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM tool_usage_daily WHERE day BETWEEN ? AND ? ORDER BY day, tool_analytics_id, surface, status",
                (start_day, end_day),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _daily_quality_rows(
        self,
        start_day: str,
        end_day: str,
    ) -> tuple[dict[str, object], ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM tool_usage_daily_quality WHERE day BETWEEN ? AND ? ORDER BY day",
                (start_day, end_day),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _event_days_before(self, cutoff_day: str) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT DISTINCT substr(occurred_at, 1, 10) AS day
                FROM tool_usage_events
                WHERE substr(occurred_at, 1, 10) < ?
                ORDER BY day
                """,
                (cutoff_day,),
            ).fetchall()
        return tuple(str(row["day"]) for row in rows)

    def apply_retention(
        self,
        *,
        now: datetime | None = None,
        event_days: int = DEFAULT_EVENT_RETENTION_DAYS,
        daily_days: int = DEFAULT_DAILY_RETENTION_DAYS,
        dry_run: bool = True,
    ) -> RetentionResult:
        if isinstance(event_days, bool) or not isinstance(event_days, int) or event_days < 1:
            raise ToolUsageStoreError("event_days must be a positive integer")
        if isinstance(daily_days, bool) or not isinstance(daily_days, int) or daily_days < event_days:
            raise ToolUsageStoreError("daily_days must be an integer not smaller than event_days")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ToolUsageStoreError("retention time must be timezone-aware")
        event_cutoff = (current.astimezone(timezone.utc) - timedelta(days=event_days)).date().isoformat()
        daily_cutoff = (current.astimezone(timezone.utc) - timedelta(days=daily_days)).date().isoformat()
        with self._lock:
            try:
                old_rows = self._connection.execute(
                    """
                    SELECT substr(occurred_at, 1, 10) AS day, COUNT(*) AS count
                    FROM tool_usage_events
                    WHERE substr(occurred_at, 1, 10) < ?
                    GROUP BY substr(occurred_at, 1, 10)
                    """,
                    (event_cutoff,),
                ).fetchall()
                aggregate_days = {
                    row["day"]
                    for row in self._connection.execute(
                        "SELECT DISTINCT day FROM tool_usage_daily"
                    ).fetchall()
                }
                scanned = sum(int(row["count"]) for row in old_rows)
                deletable = sum(
                    int(row["count"]) for row in old_rows if row["day"] in aggregate_days
                )
                protected = scanned - deletable
                daily_count = int(
                    self._connection.execute(
                        "SELECT COUNT(*) AS count FROM tool_usage_daily WHERE day < ?",
                        (daily_cutoff,),
                    ).fetchone()["count"]
                )
                deleted_events = 0
                deleted_daily = 0
                if not dry_run:
                    self._connection.execute("BEGIN IMMEDIATE")
                    deleted_events = self._connection.execute(
                        """
                        DELETE FROM tool_usage_events
                        WHERE substr(occurred_at, 1, 10) < ?
                          AND substr(occurred_at, 1, 10) IN (
                              SELECT day FROM tool_usage_daily
                          )
                        """,
                        (event_cutoff,),
                    ).rowcount
                    deleted_daily = self._connection.execute(
                        "DELETE FROM tool_usage_daily WHERE day < ?",
                        (daily_cutoff,),
                    ).rowcount
                    self._connection.execute(
                        "DELETE FROM tool_usage_daily_quality WHERE day < ?",
                        (daily_cutoff,),
                    )
                    self._connection.commit()
            except sqlite3.Error as exc:
                self._rollback_safely()
                raise ToolUsageStoreError("tool usage retention failed") from exc
        return RetentionResult(
            dry_run=dry_run,
            event_cutoff=event_cutoff,
            daily_cutoff=daily_cutoff,
            scanned_event_count=scanned,
            deletable_event_count=deletable,
            protected_event_count=protected,
            deletable_daily_count=daily_count,
            deleted_event_count=deleted_events,
            deleted_daily_count=deleted_daily,
        )

    def counts(self) -> dict[str, int | bool]:
        with self._lock:
            event_count = int(
                self._connection.execute(
                    "SELECT COUNT(*) AS count FROM tool_usage_events"
                ).fetchone()["count"]
            )
            daily_count = int(
                self._connection.execute(
                    "SELECT COUNT(*) AS count FROM tool_usage_daily"
                ).fetchone()["count"]
            )
        return {
            "event_count": event_count,
            "daily_aggregate_count": daily_count,
            "raw_content_visible": False,
        }

    def failure_counts(self) -> dict[str, int | bool]:
        with self._lock:
            values: dict[str, int | bool] = dict(sorted(self._failure_counts.items()))
        values["raw_content_visible"] = False
        return values

    def quality_counts(self) -> dict[str, int | bool]:
        """Return bounded process-local counters; null/deferred use is not failure."""

        with self._lock:
            values: dict[str, int | bool] = {
                "duplicates_rejected": int(
                    self._quality_counts.get("duplicates_rejected", 0)
                ),
                "persistence_rejected": int(
                    self._quality_counts.get("persistence_rejected", 0)
                ),
                "writer_failures": int(
                    self._quality_counts.get("writer_failures", 0)
                ),
                "raw_content_visible": False,
            }
        return values

    def _record_failure(self, category: str) -> None:
        with self._lock:
            self._failure_counts[category] = min(
                _MAX_FAILURE_COUNT,
                self._failure_counts[category] + 1,
            )

    def _record_quality(self, category: str) -> None:
        with self._lock:
            self._quality_counts[category] = min(
                _MAX_FAILURE_COUNT,
                self._quality_counts[category] + 1,
            )

    def _rollback_safely(self) -> None:
        try:
            self._connection.rollback()
        except sqlite3.Error:
            pass

    def close(self) -> None:
        if self._owns_connection:
            self._connection.close()

    def __enter__(self) -> "ToolUsageStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

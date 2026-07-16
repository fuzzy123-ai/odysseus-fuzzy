"""Content-free persistence foundation for privacy-safe tool usage analytics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
import re
from threading import Lock
from typing import Any, Callable, Iterable

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.database import Base
from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageEvent,
    ToolUsageModelScope,
    ToolUsageStatus,
    ToolUsageSurface,
)


TOOL_USAGE_SCHEMA_COMPONENT = "tool_usage_analytics"
TOOL_USAGE_SCHEMA_VERSION = 1
EVENT_RETENTION_DAYS = 90
AGGREGATE_RETENTION_DAYS = 400
MAX_FAILURE_COUNT = 1_000_000
_OPAQUE_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_ANALYTICS_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ToolUsageSchemaVersion(Base):
    __tablename__ = "tool_usage_schema_versions"

    component = Column(String(64), primary_key=True)
    version = Column(Integer, nullable=False)
    applied_at = Column(DateTime, nullable=False)


class ToolUsageEventRecord(Base):
    __tablename__ = "tool_usage_events"

    event_id = Column(String(80), primary_key=True)
    invocation_id = Column(String(80), nullable=False)
    event_kind = Column(String(16), nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    event_day = Column(Date, nullable=False)
    duration_ms = Column(Integer, nullable=True)
    tool_analytics_id = Column(String(80), nullable=False)
    tool_family = Column(String(48), nullable=False)
    tool_source = Column(String(32), nullable=False)
    surface = Column(String(32), nullable=False)
    status = Column(String(24), nullable=True)
    error_class = Column(String(32), nullable=True)
    blocked_reason_code = Column(String(32), nullable=True)
    retry_ordinal = Column(Integer, nullable=False)
    argument_size_bucket = Column(String(8), nullable=False)
    result_size_bucket = Column(String(8), nullable=False)
    result_shape_bucket = Column(String(16), nullable=False)
    owner_ref = Column(String(64), nullable=True)
    session_ref = Column(String(64), nullable=True)
    run_ref = Column(String(64), nullable=True)
    correlation_ref = Column(String(64), nullable=True)
    model_scope = Column(String(16), nullable=False)
    agent_mode = Column(String(16), nullable=False)
    app_version = Column(String(32), nullable=False)
    aggregated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "invocation_id",
            "event_kind",
            name="uq_tool_usage_events_invocation_kind",
        ),
        Index("ix_tool_usage_events_day", "event_day"),
        Index("ix_tool_usage_events_analytics_id", "tool_analytics_id"),
        Index("ix_tool_usage_events_family", "tool_family"),
        Index("ix_tool_usage_events_source", "tool_source"),
        Index("ix_tool_usage_events_surface", "surface"),
        Index("ix_tool_usage_events_status", "status"),
        Index("ix_tool_usage_events_retention", "event_day", "aggregated_at"),
    )


class ToolUsageDailyAggregateRecord(Base):
    __tablename__ = "tool_usage_daily_aggregates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    day = Column(Date, nullable=False)
    tool_analytics_id = Column(String(80), nullable=False)
    tool_family = Column(String(48), nullable=False)
    tool_source = Column(String(32), nullable=False)
    surface = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False)
    event_count = Column(Integer, nullable=False)
    invocation_count = Column(Integer, nullable=False)
    aggregation_complete = Column(Boolean, nullable=False, default=False)
    aggregated_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "day",
            "tool_analytics_id",
            "tool_family",
            "tool_source",
            "surface",
            "status",
            name="uq_tool_usage_daily_dimensions",
        ),
        Index("ix_tool_usage_daily_day", "day"),
        Index("ix_tool_usage_daily_analytics_id", "tool_analytics_id"),
        Index("ix_tool_usage_daily_family", "tool_family"),
        Index("ix_tool_usage_daily_source", "tool_source"),
        Index("ix_tool_usage_daily_surface", "surface"),
        Index("ix_tool_usage_daily_status", "status"),
    )


TOOL_USAGE_TABLES = (
    ToolUsageSchemaVersion.__table__,
    ToolUsageEventRecord.__table__,
    ToolUsageDailyAggregateRecord.__table__,
)


class ToolUsageAggregateStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class ToolUsageDailyAggregate:
    day: date
    tool_analytics_id: str
    tool_family: ToolFamily
    tool_source: ToolSource
    surface: ToolUsageSurface
    status: ToolUsageAggregateStatus
    event_count: int
    invocation_count: int
    aggregation_complete: bool
    aggregated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.day, date) or isinstance(self.day, datetime):
            raise ValueError("day must be a UTC calendar date")
        if not isinstance(self.tool_analytics_id, str) or not _ANALYTICS_ID_RE.fullmatch(
            self.tool_analytics_id
        ):
            raise ValueError("tool_analytics_id must be a canonical bounded slug")
        for value, expected, field_name in (
            (self.tool_family, ToolFamily, "tool_family"),
            (self.tool_source, ToolSource, "tool_source"),
            (self.surface, ToolUsageSurface, "surface"),
            (self.status, ToolUsageAggregateStatus, "status"),
        ):
            if not isinstance(value, expected):
                raise ValueError(f"{field_name} must be normalized")
        for value, field_name in (
            (self.event_count, "event_count"),
            (self.invocation_count, "invocation_count"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 2**63 - 1
            ):
                raise ValueError(f"{field_name} must be a bounded non-negative integer")
        if not isinstance(self.aggregation_complete, bool):
            raise ValueError("aggregation_complete must be a boolean")
        if not isinstance(self.aggregated_at, datetime) or self.aggregated_at.tzinfo is None:
            raise ValueError("aggregated_at must be timezone-aware")

    def to_record(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "tool_analytics_id": self.tool_analytics_id,
            "tool_family": self.tool_family.value,
            "tool_source": self.tool_source.value,
            "surface": self.surface.value,
            "status": self.status.value,
            "event_count": self.event_count,
            "invocation_count": self.invocation_count,
            "aggregation_complete": self.aggregation_complete,
            "aggregated_at": _naive_utc(self.aggregated_at),
        }


@dataclass(frozen=True, slots=True)
class ToolUsageWriteResult:
    attempted: int
    inserted: int
    duplicates: int
    failures: int

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "failures": self.failures,
            "raw_content_visible": False,
        }


@dataclass(frozen=True, slots=True)
class ToolUsageRetentionResult:
    dry_run: bool
    event_retention_days: int
    aggregate_retention_days: int
    eligible_event_count: int
    eligible_aggregate_count: int
    deleted_event_count: int
    deleted_aggregate_count: int
    failures: int

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TOOL_USAGE_SCHEMA_VERSION,
            "dry_run": self.dry_run,
            "event_retention_days": self.event_retention_days,
            "aggregate_retention_days": self.aggregate_retention_days,
            "eligible_event_count": self.eligible_event_count,
            "eligible_aggregate_count": self.eligible_aggregate_count,
            "deleted_event_count": self.deleted_event_count,
            "deleted_aggregate_count": self.deleted_aggregate_count,
            "failures": self.failures,
            "raw_content_visible": False,
            "identifiers_visible": False,
        }


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _event_record(event: ToolUsageEvent) -> dict[str, Any]:
    if not isinstance(event, ToolUsageEvent):
        raise ValueError("store accepts normalized ToolUsageEvent values only")
    occurred_at = _naive_utc(event.occurred_at)
    return {
        "event_id": event.event_id,
        "invocation_id": event.invocation_id,
        "event_kind": event.event_kind.value,
        "occurred_at": occurred_at,
        "event_day": occurred_at.date(),
        "duration_ms": event.duration_ms,
        "tool_analytics_id": event.tool_analytics_id,
        "tool_family": event.tool_family.value,
        "tool_source": event.tool_source.value,
        "surface": event.surface.value,
        "status": event.status.value if event.status else None,
        "error_class": event.error_class.value if event.error_class else None,
        "blocked_reason_code": (
            event.blocked_reason_code.value if event.blocked_reason_code else None
        ),
        "retry_ordinal": event.retry_ordinal,
        "argument_size_bucket": event.argument_size_bucket.value,
        "result_size_bucket": event.result_size_bucket.value,
        "result_shape_bucket": event.result_shape_bucket.value,
        "owner_ref": event.owner_ref,
        "session_ref": event.session_ref,
        "run_ref": event.run_ref,
        "correlation_ref": event.correlation_ref,
        "model_scope": event.model_scope.value,
        "agent_mode": event.agent_mode.value,
        "app_version": event.app_version,
        "aggregated_at": None,
    }


class ToolUsageStore:
    """Best-effort store; failures never escape into tool execution."""

    def __init__(self, session_factory: Callable[[], Any] | None = None) -> None:
        if session_factory is None:
            from core.database import SessionLocal

            session_factory = SessionLocal
        self._session_factory = session_factory
        self._failure_counts: Counter[str] = Counter()
        self._failure_lock = Lock()

    def _record_failure(self, reason: str) -> None:
        with self._failure_lock:
            self._failure_counts[reason] = min(
                self._failure_counts[reason] + 1,
                MAX_FAILURE_COUNT,
            )

    def failure_counts(self) -> dict[str, int]:
        with self._failure_lock:
            return dict(sorted(self._failure_counts.items()))

    def write_events(self, events: Iterable[ToolUsageEvent]) -> ToolUsageWriteResult:
        try:
            records = tuple(_event_record(event) for event in events)
        except Exception:
            self._record_failure("invalid_event")
            return ToolUsageWriteResult(0, 0, 0, 1)
        if not records:
            return ToolUsageWriteResult(0, 0, 0, 0)

        session = None
        inserted = 0
        duplicates = 0
        try:
            session = self._session_factory()
            dialect_name = session.get_bind().dialect.name
            for record in records:
                if dialect_name == "sqlite":
                    statement = sqlite_insert(ToolUsageEventRecord).values(**record)
                    statement = statement.on_conflict_do_nothing(
                        index_elements=("invocation_id", "event_kind")
                    )
                    result = session.execute(statement)
                    if result.rowcount == 1:
                        inserted += 1
                    else:
                        duplicates += 1
                    continue
                try:
                    with session.begin_nested():
                        session.execute(insert(ToolUsageEventRecord).values(**record))
                    inserted += 1
                except IntegrityError:
                    duplicates += 1
            session.commit()
            return ToolUsageWriteResult(len(records), inserted, duplicates, 0)
        except Exception:
            if session is not None:
                session.rollback()
            self._record_failure("writer_failure")
            return ToolUsageWriteResult(len(records), 0, 0, len(records))
        finally:
            if session is not None:
                session.close()

    def write_daily_aggregates(
        self,
        aggregates: Iterable[ToolUsageDailyAggregate],
    ) -> ToolUsageWriteResult:
        try:
            records = tuple(aggregate.to_record() for aggregate in aggregates)
        except Exception:
            self._record_failure("invalid_aggregate")
            return ToolUsageWriteResult(0, 0, 0, 1)
        if not records:
            return ToolUsageWriteResult(0, 0, 0, 0)

        session = None
        try:
            session = self._session_factory()
            for record in records:
                dimensions = {
                    key: record[key]
                    for key in (
                        "day",
                        "tool_analytics_id",
                        "tool_family",
                        "tool_source",
                        "surface",
                        "status",
                    )
                }
                existing_id = session.execute(
                    select(ToolUsageDailyAggregateRecord.id).filter_by(**dimensions)
                ).scalar_one_or_none()
                if existing_id is None:
                    session.execute(
                        insert(ToolUsageDailyAggregateRecord).values(**record)
                    )
                else:
                    session.execute(
                        update(ToolUsageDailyAggregateRecord)
                        .where(ToolUsageDailyAggregateRecord.id == existing_id)
                        .values(**record)
                    )
            session.commit()
            return ToolUsageWriteResult(len(records), len(records), 0, 0)
        except Exception:
            if session is not None:
                session.rollback()
            self._record_failure("aggregate_writer_failure")
            return ToolUsageWriteResult(len(records), 0, 0, len(records))
        finally:
            if session is not None:
                session.close()

    def mark_events_aggregated(
        self,
        event_ids: Iterable[str],
        *,
        aggregated_at: datetime | None = None,
    ) -> int:
        normalized_ids = tuple(dict.fromkeys(event_ids))
        if not normalized_ids or any(
            not isinstance(event_id, str) or not _OPAQUE_EVENT_ID_RE.fullmatch(event_id)
            for event_id in normalized_ids
        ):
            self._record_failure("invalid_aggregation_marker")
            return 0
        marked_at = _naive_utc(aggregated_at or datetime.now(timezone.utc))
        session = None
        try:
            session = self._session_factory()
            result = session.execute(
                update(ToolUsageEventRecord)
                .where(ToolUsageEventRecord.event_id.in_(normalized_ids))
                .values(aggregated_at=marked_at)
            )
            session.commit()
            return max(int(result.rowcount or 0), 0)
        except Exception:
            if session is not None:
                session.rollback()
            self._record_failure("aggregation_marker_failure")
            return 0
        finally:
            if session is not None:
                session.close()

    def enforce_retention(
        self,
        *,
        now: datetime | None = None,
        dry_run: bool = True,
        event_retention_days: int = EVENT_RETENTION_DAYS,
        aggregate_retention_days: int = AGGREGATE_RETENTION_DAYS,
    ) -> ToolUsageRetentionResult:
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be a boolean")
        for value, field_name in (
            (event_retention_days, "event_retention_days"),
            (aggregate_retention_days, "aggregate_retention_days"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 3_650
            ):
                raise ValueError(f"{field_name} must be between 1 and 3650")
        reference = now or datetime.now(timezone.utc)
        if not isinstance(reference, datetime) or reference.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        today = reference.astimezone(timezone.utc).date()
        event_cutoff = today - timedelta(days=event_retention_days)
        aggregate_cutoff = today - timedelta(days=aggregate_retention_days)

        session = None
        try:
            session = self._session_factory()
            event_filter = (
                ToolUsageEventRecord.event_day < event_cutoff,
                ToolUsageEventRecord.aggregated_at.is_not(None),
            )
            aggregate_filter = (ToolUsageDailyAggregateRecord.day < aggregate_cutoff,)
            eligible_events = session.query(ToolUsageEventRecord).filter(*event_filter).count()
            eligible_aggregates = (
                session.query(ToolUsageDailyAggregateRecord)
                .filter(*aggregate_filter)
                .count()
            )
            deleted_events = 0
            deleted_aggregates = 0
            if not dry_run:
                deleted_events = max(
                    int(
                        session.execute(
                            delete(ToolUsageEventRecord).where(*event_filter)
                        ).rowcount
                        or 0
                    ),
                    0,
                )
                deleted_aggregates = max(
                    int(
                        session.execute(
                            delete(ToolUsageDailyAggregateRecord).where(*aggregate_filter)
                        ).rowcount
                        or 0
                    ),
                    0,
                )
                session.commit()
            else:
                session.rollback()
            return ToolUsageRetentionResult(
                dry_run=dry_run,
                event_retention_days=event_retention_days,
                aggregate_retention_days=aggregate_retention_days,
                eligible_event_count=eligible_events,
                eligible_aggregate_count=eligible_aggregates,
                deleted_event_count=deleted_events,
                deleted_aggregate_count=deleted_aggregates,
                failures=0,
            )
        except Exception:
            if session is not None:
                session.rollback()
            self._record_failure("retention_failure")
            return ToolUsageRetentionResult(
                dry_run=True,
                event_retention_days=event_retention_days,
                aggregate_retention_days=aggregate_retention_days,
                eligible_event_count=0,
                eligible_aggregate_count=0,
                deleted_event_count=0,
                deleted_aggregate_count=0,
                failures=1,
            )
        finally:
            if session is not None:
                session.close()

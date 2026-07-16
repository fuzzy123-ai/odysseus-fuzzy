"""Deterministic aggregate and retention service for privacy-safe tool usage."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
import math
import re
from typing import Callable, Iterable

from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_events import ToolUsageEventKind, ToolUsageStatus, ToolUsageSurface
from src.tool_usage_store import (
    ToolUsageAggregateStatus,
    ToolUsageDailyAggregate,
    ToolUsageRetentionResult,
    ToolUsageStore,
    ToolUsageStoredEvent,
)


TOOL_USAGE_ANALYTICS_SCHEMA_VERSION = "odysseus.tool_usage_analytics.v1"
MAX_QUERY_SPAN_DAYS = 90
MAX_QUERY_RESULT_ROWS = 200
MAX_DURATION_MS = 7 * 24 * 60 * 60 * 1_000
DURATION_HISTOGRAM_BOUNDS_MS = (
    1,
    5,
    10,
    25,
    50,
    100,
    250,
    500,
    1_000,
    2_500,
    5_000,
    10_000,
    30_000,
    60_000,
    300_000,
    900_000,
    3_600_000,
    21_600_000,
    86_400_000,
    MAX_DURATION_MS,
)
_ANALYTICS_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ToolUsageExpectedState(StrEnum):
    ACTIVE = "active"
    DEFERRED = "deferred"
    DEFAULT_OFF = "default_off"


class ToolUsageObservedState(StrEnum):
    OBSERVED = "observed"
    NO_USAGE = "no_usage"
    DEFERRED = "deferred"
    DEFAULT_OFF = "default_off"
    READ_FAILED = "read_failed"


class ToolUsageQualityWarning(StrEnum):
    INCOMPLETE_INVOCATIONS = "incomplete_invocations"
    UNKNOWN_IDENTITY = "unknown_identity"
    DUPLICATES_REJECTED = "duplicates_rejected"
    WRITER_FAILURES = "writer_failures"
    DETAIL_COVERAGE_PARTIAL = "detail_coverage_partial"
    RESULT_TRUNCATED = "result_truncated"


@dataclass(frozen=True, slots=True)
class ToolUsageDataQuality:
    coverage_rate: float | None
    incomplete: int
    duplicates_rejected: int
    writer_failures: int
    unknown_identity: int
    aggregation_complete: bool
    instrumentation_error: bool

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "coverage_rate": self.coverage_rate,
            "incomplete": self.incomplete,
            "duplicates_rejected": self.duplicates_rejected,
            "writer_failures": self.writer_failures,
            "unknown_identity": self.unknown_identity,
            "aggregation_complete": self.aggregation_complete,
            "instrumentation_error": self.instrumentation_error,
        }


@dataclass(frozen=True, slots=True)
class ToolUsageAggregateRow:
    day: date
    tool_analytics_id: str
    tool_family: ToolFamily
    tool_source: ToolSource
    surface: ToolUsageSurface
    status: ToolUsageAggregateStatus
    event_count: int
    invocation_count: int

    def to_store_value(
        self,
        *,
        aggregated_at: datetime,
        aggregation_complete: bool,
    ) -> ToolUsageDailyAggregate:
        return ToolUsageDailyAggregate(
            day=self.day,
            tool_analytics_id=self.tool_analytics_id,
            tool_family=self.tool_family,
            tool_source=self.tool_source,
            surface=self.surface,
            status=self.status,
            event_count=self.event_count,
            invocation_count=self.invocation_count,
            aggregation_complete=aggregation_complete,
            aggregated_at=aggregated_at,
        )

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "day": self.day.isoformat(),
            "tool_analytics_id": self.tool_analytics_id,
            "tool_family": self.tool_family.value,
            "tool_source": self.tool_source.value,
            "surface": self.surface.value,
            "status": self.status.value,
            "event_count": self.event_count,
            "invocation_count": self.invocation_count,
        }


@dataclass(frozen=True, slots=True)
class ToolUsageDailyAnalytics:
    day: date
    expected_state: ToolUsageExpectedState
    observed_state: ToolUsageObservedState
    rows: tuple[ToolUsageAggregateRow, ...]
    invocations_total: int
    terminal_invocations: int
    retry_invocations: int
    distinct_owner_count: int
    distinct_session_count: int
    duration_histogram_counts: tuple[int, ...]
    duration_p50_ms: int | None
    duration_p95_ms: int | None
    quality: ToolUsageDataQuality

    def to_safe_dict(self) -> dict[str, object]:
        status_counts = {
            status.value: sum(
                row.invocation_count for row in self.rows if row.status == status
            )
            for status in ToolUsageAggregateStatus
        }
        status_rates = {
            status: (
                count / self.terminal_invocations
                if self.terminal_invocations
                and status != ToolUsageAggregateStatus.INCOMPLETE.value
                else None
            )
            for status, count in status_counts.items()
        }
        return {
            "schema_version": TOOL_USAGE_ANALYTICS_SCHEMA_VERSION,
            "day": self.day.isoformat(),
            "expected_state": self.expected_state.value,
            "observed_state": self.observed_state.value,
            "rows": [row.to_safe_dict() for row in self.rows],
            "summary": {
                "invocations_total": self.invocations_total,
                "terminal_invocations": self.terminal_invocations,
                "retry_invocations": self.retry_invocations,
                "distinct_owner_count": self.distinct_owner_count,
                "distinct_session_count": self.distinct_session_count,
                "status_counts": status_counts,
                "status_rates": status_rates,
                "duration_samples": sum(self.duration_histogram_counts),
                "duration_p50_ms": self.duration_p50_ms,
                "duration_p95_ms": self.duration_p95_ms,
            },
            "duration_histogram": {
                "bounds_ms": list(DURATION_HISTOGRAM_BOUNDS_MS),
                "counts": list(self.duration_histogram_counts),
            },
            "quality": self.quality.to_safe_dict(),
            "raw_content_visible": False,
            "direct_identifiers_visible": False,
        }


@dataclass(frozen=True, slots=True)
class ToolUsageAggregationRetentionResult:
    analytics: ToolUsageDailyAnalytics
    retention_attempted: bool
    retention: ToolUsageRetentionResult | None

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "analytics": self.analytics.to_safe_dict(),
            "retention_attempted": self.retention_attempted,
            "retention": self.retention.to_safe_dict() if self.retention else None,
            "raw_content_visible": False,
            "direct_identifiers_visible": False,
        }


@dataclass(frozen=True, slots=True)
class ToolUsageRangeRow:
    tool_analytics_id: str
    tool_family: ToolFamily
    tool_source: ToolSource
    surface: ToolUsageSurface
    status: ToolUsageAggregateStatus
    calls: int

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "tool_analytics_id": self.tool_analytics_id,
            "tool_family": self.tool_family.value,
            "tool_source": self.tool_source.value,
            "surface": self.surface.value,
            "status": self.status.value,
            "calls": self.calls,
        }


@dataclass(frozen=True, slots=True)
class ToolUsageRangeAnalytics:
    start_day: date
    end_day: date
    rows: tuple[ToolUsageRangeRow, ...]
    calls: int
    active_days: int
    terminal_invocations: int
    retry_invocations: int
    distinct_owner_count: int
    distinct_session_count: int
    duration_histogram_counts: tuple[int, ...]
    duration_p50_ms: int | None
    duration_p95_ms: int | None
    coverage_rate: float | None
    status_counts: tuple[int, ...]
    incomplete: int
    unknown_identity: int
    duplicates_rejected: int
    writer_failures: int
    warnings: tuple[ToolUsageQualityWarning, ...]
    result_truncated: bool
    tool_filter: str | None
    family_filter: ToolFamily | None
    source_filter: ToolSource | None
    surface_filter: ToolUsageSurface | None
    status_filter: ToolUsageAggregateStatus | None

    def to_safe_dict(self) -> dict[str, object]:
        status_counts = {
            status.value: count
            for status, count in zip(ToolUsageAggregateStatus, self.status_counts)
        }
        status_rates = {
            status: (
                count / self.terminal_invocations
                if self.terminal_invocations
                and status != ToolUsageAggregateStatus.INCOMPLETE.value
                else None
            )
            for status, count in status_counts.items()
        }
        return {
            "schema_version": TOOL_USAGE_ANALYTICS_SCHEMA_VERSION,
            "range": {
                "start": self.start_day.isoformat(),
                "end": self.end_day.isoformat(),
                "days": (self.end_day - self.start_day).days + 1,
            },
            "filters": {
                "tool": self.tool_filter,
                "family": self.family_filter.value if self.family_filter else None,
                "source": self.source_filter.value if self.source_filter else None,
                "surface": self.surface_filter.value if self.surface_filter else None,
                "status": self.status_filter.value if self.status_filter else None,
            },
            "summary": {
                "calls": self.calls,
                "active_days": self.active_days,
                "pseudonymous_distinct_owner_count": self.distinct_owner_count,
                "pseudonymous_distinct_session_count": self.distinct_session_count,
                "status_counts": status_counts,
                "status_rates": status_rates,
                "duration_samples": sum(self.duration_histogram_counts),
                "duration_p50_ms": self.duration_p50_ms,
                "duration_p95_ms": self.duration_p95_ms,
                "retry_invocations": self.retry_invocations,
                "calls_per_session": (
                    self.calls / self.distinct_session_count
                    if self.distinct_session_count
                    else None
                ),
                "coverage_rate": self.coverage_rate,
            },
            "quality": {
                "incomplete": self.incomplete,
                "duplicates_rejected": self.duplicates_rejected,
                "writer_failures": self.writer_failures,
                "unknown_identity": self.unknown_identity,
                "warnings": [warning.value for warning in self.warnings],
                "query_complete": ToolUsageQualityWarning.DETAIL_COVERAGE_PARTIAL
                not in self.warnings,
                "result_truncated": self.result_truncated,
            },
            "duration_histogram": {
                "bounds_ms": list(DURATION_HISTOGRAM_BOUNDS_MS),
                "counts": list(self.duration_histogram_counts),
            },
            "rows": [row.to_safe_dict() for row in self.rows],
            "raw_records_visible": False,
            "raw_content_visible": False,
            "direct_identifiers_visible": False,
        }


@dataclass(frozen=True, slots=True)
class _BuiltDay:
    rows: tuple[ToolUsageAggregateRow, ...]
    event_ids: tuple[str, ...]
    invocations_total: int
    terminal_invocations: int
    retry_invocations: int
    distinct_owner_count: int
    distinct_session_count: int
    histogram_counts: tuple[int, ...]
    duration_p50_ms: int | None
    duration_p95_ms: int | None
    incomplete: int
    unknown_identity: int


def _duration_histogram(values: Iterable[int]) -> tuple[int, ...]:
    counts = [0] * len(DURATION_HISTOGRAM_BOUNDS_MS)
    for value in values:
        bounded = min(max(int(value), 0), MAX_DURATION_MS)
        for index, upper_bound in enumerate(DURATION_HISTOGRAM_BOUNDS_MS):
            if bounded <= upper_bound:
                counts[index] += 1
                break
    return tuple(counts)


def _histogram_percentile(counts: tuple[int, ...], percentile: float) -> int | None:
    total = sum(counts)
    if total == 0:
        return None
    rank = max(1, math.ceil(total * percentile))
    cumulative = 0
    for upper_bound, count in zip(DURATION_HISTOGRAM_BOUNDS_MS, counts):
        cumulative += count
        if cumulative >= rank:
            return upper_bound
    return DURATION_HISTOGRAM_BOUNDS_MS[-1]


def _identity(
    event: ToolUsageStoredEvent,
) -> tuple[str, ToolFamily, ToolSource, ToolUsageSurface, bool]:
    unknown = False
    analytics_id = event.tool_analytics_id
    if not _ANALYTICS_ID_RE.fullmatch(analytics_id):
        analytics_id = "dynamic-unclassified"
        unknown = True
    try:
        family = ToolFamily(event.tool_family)
    except ValueError:
        family = ToolFamily.UNCLASSIFIED_DYNAMIC
        unknown = True
    try:
        source = ToolSource(event.tool_source)
    except ValueError:
        source = ToolSource.DYNAMIC
        unknown = True
    try:
        surface = ToolUsageSurface(event.surface)
    except ValueError:
        surface = ToolUsageSurface.SYSTEM
        unknown = True
    if (
        analytics_id == "dynamic-unclassified"
        or family == ToolFamily.UNCLASSIFIED_DYNAMIC
        or source == ToolSource.DYNAMIC
    ):
        unknown = True
    return analytics_id, family, source, surface, unknown


def _build_day(day: date, events: tuple[ToolUsageStoredEvent, ...]) -> _BuiltDay:
    by_invocation: dict[str, list[ToolUsageStoredEvent]] = defaultdict(list)
    for event in events:
        by_invocation[event.invocation_id].append(event)

    counts: dict[
        tuple[str, ToolFamily, ToolSource, ToolUsageSurface, ToolUsageAggregateStatus],
        list[int],
    ] = {}
    durations: list[int] = []
    owners: set[str] = set()
    sessions: set[str] = set()
    terminal_invocations = 0
    retry_invocations = 0
    incomplete = 0
    unknown_identity = 0

    for invocation_events in by_invocation.values():
        invocation_events.sort(key=lambda item: (item.occurred_at, item.event_id))
        terminal = next(
            (
                item
                for item in invocation_events
                if item.event_kind == ToolUsageEventKind.TERMINAL.value
            ),
            None,
        )
        representative = terminal or invocation_events[0]
        identity = _identity(representative)
        analytics_id, family, source, surface, identity_unknown = identity
        if any(_identity(item)[:4] != identity[:4] for item in invocation_events):
            analytics_id = "dynamic-unclassified"
            family = ToolFamily.UNCLASSIFIED_DYNAMIC
            source = ToolSource.DYNAMIC
            surface = ToolUsageSurface.SYSTEM
            identity_unknown = True

        if terminal is None:
            status = ToolUsageAggregateStatus.INCOMPLETE
            incomplete += 1
        else:
            terminal_invocations += 1
            try:
                status = ToolUsageAggregateStatus(ToolUsageStatus(terminal.status).value)
            except (TypeError, ValueError):
                status = ToolUsageAggregateStatus.REJECTED
                identity_unknown = True
            if (
                isinstance(terminal.duration_ms, int)
                and not isinstance(terminal.duration_ms, bool)
                and 0 <= terminal.duration_ms <= MAX_DURATION_MS
            ):
                durations.append(terminal.duration_ms)

        if identity_unknown:
            unknown_identity += 1
        if any(item.retry_ordinal > 0 for item in invocation_events):
            retry_invocations += 1
        owners.update(item.owner_ref for item in invocation_events if item.owner_ref)
        sessions.update(item.session_ref for item in invocation_events if item.session_ref)

        key = (analytics_id, family, source, surface, status)
        bucket = counts.setdefault(key, [0, 0])
        bucket[0] += len(invocation_events)
        bucket[1] += 1

    rows = tuple(
        ToolUsageAggregateRow(
            day=day,
            tool_analytics_id=key[0],
            tool_family=key[1],
            tool_source=key[2],
            surface=key[3],
            status=key[4],
            event_count=value[0],
            invocation_count=value[1],
        )
        for key, value in sorted(
            counts.items(),
            key=lambda item: tuple(
                component.value if isinstance(component, StrEnum) else component
                for component in item[0]
            ),
        )
    )
    histogram = _duration_histogram(durations)
    return _BuiltDay(
        rows=rows,
        event_ids=tuple(event.event_id for event in events),
        invocations_total=len(by_invocation),
        terminal_invocations=terminal_invocations,
        retry_invocations=retry_invocations,
        distinct_owner_count=len(owners),
        distinct_session_count=len(sessions),
        histogram_counts=histogram,
        duration_p50_ms=_histogram_percentile(histogram, 0.50),
        duration_p95_ms=_histogram_percentile(histogram, 0.95),
        incomplete=incomplete,
        unknown_identity=unknown_identity,
    )


class ToolUsageAnalyticsService:
    """Aggregate one UTC day and permit retention only after durable completion."""

    def __init__(
        self,
        store: ToolUsageStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(store, ToolUsageStore):
            raise ValueError("store must be a ToolUsageStore")
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def aggregate_day(
        self,
        day: date,
        *,
        expected_state: ToolUsageExpectedState = ToolUsageExpectedState.ACTIVE,
    ) -> ToolUsageDailyAnalytics:
        if not isinstance(day, date) or isinstance(day, datetime):
            raise ValueError("day must be a UTC calendar date")
        if not isinstance(expected_state, ToolUsageExpectedState):
            raise ValueError("expected_state must be normalized")
        aggregated_at = self._clock()
        if not isinstance(aggregated_at, datetime) or aggregated_at.tzinfo is None:
            raise ValueError("clock must return a timezone-aware timestamp")
        aggregated_at = aggregated_at.astimezone(timezone.utc)

        read = self._store.read_events_for_day(day)
        built = _build_day(day, read.events)
        current_failed = read.failures > 0
        if not current_failed:
            completed = tuple(
                row.to_store_value(
                    aggregated_at=aggregated_at,
                    aggregation_complete=True,
                )
                for row in built.rows
            )
            committed = self._store.commit_day_aggregation(
                day,
                completed,
                built.event_ids,
                aggregated_at=aggregated_at,
            )
            current_failed = (
                committed.failures > 0
                or committed.marked_event_count != len(built.event_ids)
            )

        quality_counts = self._store.quality_counts()
        coverage = (
            built.terminal_invocations / built.invocations_total
            if built.invocations_total
            else None
        )
        if read.failures:
            observed_state = ToolUsageObservedState.READ_FAILED
        elif built.invocations_total:
            observed_state = ToolUsageObservedState.OBSERVED
        elif expected_state == ToolUsageExpectedState.DEFERRED:
            observed_state = ToolUsageObservedState.DEFERRED
        elif expected_state == ToolUsageExpectedState.DEFAULT_OFF:
            observed_state = ToolUsageObservedState.DEFAULT_OFF
        else:
            observed_state = ToolUsageObservedState.NO_USAGE

        return ToolUsageDailyAnalytics(
            day=day,
            expected_state=expected_state,
            observed_state=observed_state,
            rows=built.rows,
            invocations_total=built.invocations_total,
            terminal_invocations=built.terminal_invocations,
            retry_invocations=built.retry_invocations,
            distinct_owner_count=built.distinct_owner_count,
            distinct_session_count=built.distinct_session_count,
            duration_histogram_counts=built.histogram_counts,
            duration_p50_ms=built.duration_p50_ms,
            duration_p95_ms=built.duration_p95_ms,
            quality=ToolUsageDataQuality(
                coverage_rate=coverage,
                incomplete=built.incomplete,
                duplicates_rejected=quality_counts["duplicates_rejected"],
                writer_failures=quality_counts["writer_failures"],
                unknown_identity=built.unknown_identity,
                aggregation_complete=not current_failed,
                instrumentation_error=current_failed,
            ),
        )

    def aggregate_then_retain(
        self,
        day: date,
        *,
        now: datetime,
        dry_run: bool = True,
        expected_state: ToolUsageExpectedState = ToolUsageExpectedState.ACTIVE,
    ) -> ToolUsageAggregationRetentionResult:
        analytics = self.aggregate_day(day, expected_state=expected_state)
        if not analytics.quality.aggregation_complete:
            return ToolUsageAggregationRetentionResult(
                analytics=analytics,
                retention_attempted=False,
                retention=None,
            )
        retention = self._store.enforce_retention(now=now, dry_run=dry_run)
        return ToolUsageAggregationRetentionResult(
            analytics=analytics,
            retention_attempted=True,
            retention=retention,
        )


class ToolUsageAnalyticsQueryService:
    """Read bounded recent aggregate projections without exposing source rows."""

    def __init__(self, store: ToolUsageStore) -> None:
        if not isinstance(store, ToolUsageStore):
            raise ValueError("store must be a ToolUsageStore")
        self._store = store

    def query_range(
        self,
        *,
        start_day: date,
        end_day: date,
        tool_filter: str | None = None,
        family_filter: ToolFamily | None = None,
        source_filter: ToolSource | None = None,
        surface_filter: ToolUsageSurface | None = None,
        status_filter: ToolUsageAggregateStatus | None = None,
        limit: int = 100,
    ) -> ToolUsageRangeAnalytics:
        if any(
            not isinstance(value, date) or isinstance(value, datetime)
            for value in (start_day, end_day)
        ):
            raise ValueError("range bounds must be UTC calendar dates")
        span_days = (end_day - start_day).days + 1
        if span_days < 1:
            raise ValueError("range start must not be after range end")
        if span_days > MAX_QUERY_SPAN_DAYS:
            raise ValueError(f"range must not exceed {MAX_QUERY_SPAN_DAYS} days")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_QUERY_RESULT_ROWS:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_RESULT_ROWS}")

        all_events: list[ToolUsageStoredEvent] = []
        read_failures = 0
        current_day = start_day
        while current_day <= end_day:
            read = self._store.read_events_for_day(current_day)
            all_events.extend(read.events)
            read_failures += read.failures
            current_day += timedelta(days=1)

        by_invocation: dict[str, list[ToolUsageStoredEvent]] = defaultdict(list)
        for event in all_events:
            by_invocation[event.invocation_id].append(event)

        row_counts: dict[
            tuple[str, ToolFamily, ToolSource, ToolUsageSurface, ToolUsageAggregateStatus],
            int,
        ] = defaultdict(int)
        status_counts = {status: 0 for status in ToolUsageAggregateStatus}
        histogram_counts = [0] * len(DURATION_HISTOGRAM_BOUNDS_MS)
        owners: set[str] = set()
        sessions: set[str] = set()
        active_days: set[date] = set()
        calls = 0
        terminal_invocations = 0
        retry_invocations = 0
        incomplete = 0
        unknown_identity = 0

        for invocation_events in by_invocation.values():
            invocation_events.sort(key=lambda item: (item.occurred_at, item.event_id))
            terminal = next(
                (
                    item
                    for item in invocation_events
                    if item.event_kind == ToolUsageEventKind.TERMINAL.value
                ),
                None,
            )
            representative = terminal or invocation_events[0]
            built = _build_day(representative.occurred_at.date(), tuple(invocation_events))
            if not built.rows:
                continue
            row = built.rows[0]
            if tool_filter is not None and row.tool_analytics_id != tool_filter:
                continue
            if family_filter is not None and row.tool_family != family_filter:
                continue
            if source_filter is not None and row.tool_source != source_filter:
                continue
            if surface_filter is not None and row.surface != surface_filter:
                continue
            if status_filter is not None and row.status != status_filter:
                continue

            key = (
                row.tool_analytics_id,
                row.tool_family,
                row.tool_source,
                row.surface,
                row.status,
            )
            row_counts[key] += 1
            status_counts[row.status] += 1
            calls += 1
            terminal_invocations += built.terminal_invocations
            retry_invocations += built.retry_invocations
            incomplete += built.incomplete
            unknown_identity += built.unknown_identity
            active_days.add(representative.occurred_at.date())
            owners.update(item.owner_ref for item in invocation_events if item.owner_ref)
            sessions.update(item.session_ref for item in invocation_events if item.session_ref)
            for index, count in enumerate(built.histogram_counts):
                histogram_counts[index] += count

        all_rows = tuple(
            ToolUsageRangeRow(
                tool_analytics_id=key[0],
                tool_family=key[1],
                tool_source=key[2],
                surface=key[3],
                status=key[4],
                calls=count,
            )
            for key, count in sorted(
                row_counts.items(),
                key=lambda item: (
                    -item[1],
                    item[0][0],
                    item[0][1].value,
                    item[0][2].value,
                    item[0][3].value,
                    item[0][4].value,
                ),
            )
        )
        result_truncated = len(all_rows) > limit
        bounded_rows = all_rows[:limit]
        quality_counts = self._store.quality_counts()
        warnings: list[ToolUsageQualityWarning] = []
        for condition, warning in (
            (incomplete > 0, ToolUsageQualityWarning.INCOMPLETE_INVOCATIONS),
            (unknown_identity > 0, ToolUsageQualityWarning.UNKNOWN_IDENTITY),
            (
                quality_counts["duplicates_rejected"] > 0,
                ToolUsageQualityWarning.DUPLICATES_REJECTED,
            ),
            (
                quality_counts["writer_failures"] > 0,
                ToolUsageQualityWarning.WRITER_FAILURES,
            ),
            (read_failures > 0, ToolUsageQualityWarning.DETAIL_COVERAGE_PARTIAL),
            (result_truncated, ToolUsageQualityWarning.RESULT_TRUNCATED),
        ):
            if condition:
                warnings.append(warning)
        histogram = tuple(histogram_counts)
        return ToolUsageRangeAnalytics(
            start_day=start_day,
            end_day=end_day,
            rows=bounded_rows,
            calls=calls,
            active_days=len(active_days),
            terminal_invocations=terminal_invocations,
            retry_invocations=retry_invocations,
            distinct_owner_count=len(owners),
            distinct_session_count=len(sessions),
            duration_histogram_counts=histogram,
            duration_p50_ms=_histogram_percentile(histogram, 0.50),
            duration_p95_ms=_histogram_percentile(histogram, 0.95),
            coverage_rate=terminal_invocations / calls if calls else None,
            status_counts=tuple(
                status_counts[status] for status in ToolUsageAggregateStatus
            ),
            incomplete=incomplete,
            unknown_identity=unknown_identity,
            duplicates_rejected=quality_counts["duplicates_rejected"],
            writer_failures=quality_counts["writer_failures"],
            warnings=tuple(warnings),
            result_truncated=result_truncated,
            tool_filter=tool_filter,
            family_filter=family_filter,
            source_filter=source_filter,
            surface_filter=surface_filter,
            status_filter=status_filter,
        )


def _parse_query_day(value: str | None, *, field_name: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"invalid {field_name} date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {field_name} date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"invalid {field_name} date")
    return parsed


def _parse_query_enum(enum_type, value: str | None, *, field_name: str):
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError(f"invalid {field_name} filter")
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name} filter") from exc


def read_tool_usage_analytics(
    *,
    start: str | None = None,
    end: str | None = None,
    tool: str | None = None,
    family: str | None = None,
    source: str | None = None,
    surface: str | None = None,
    status: str | None = None,
    limit: int = 100,
    session_factory=None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return one bounded, aggregate-only Admin diagnostics projection."""

    reference = now or datetime.now(timezone.utc)
    if not isinstance(reference, datetime) or reference.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    default_end = reference.astimezone(timezone.utc).date()
    end_day = _parse_query_day(end, field_name="end") or default_end
    start_day = _parse_query_day(start, field_name="start") or (
        end_day - timedelta(days=29)
    )
    normalized_tool = None
    if tool is not None:
        if (
            not isinstance(tool, str)
            or len(tool) > 80
            or not _ANALYTICS_ID_RE.fullmatch(tool)
        ):
            raise ValueError("invalid tool filter")
        normalized_tool = tool
    store = ToolUsageStore(session_factory)
    result = ToolUsageAnalyticsQueryService(store).query_range(
        start_day=start_day,
        end_day=end_day,
        tool_filter=normalized_tool,
        family_filter=_parse_query_enum(ToolFamily, family, field_name="family"),
        source_filter=_parse_query_enum(ToolSource, source, field_name="source"),
        surface_filter=_parse_query_enum(
            ToolUsageSurface,
            surface,
            field_name="surface",
        ),
        status_filter=_parse_query_enum(
            ToolUsageAggregateStatus,
            status,
            field_name="status",
        ),
        limit=limit,
    )
    return result.to_safe_dict()

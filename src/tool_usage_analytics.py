"""Deterministic aggregate-only analytics for privacy-safe tool usage."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import math
import re
from typing import Any, Mapping

from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_events import ToolUsageStatus, ToolUsageSurface
from src.tool_usage_store import (
    DEFAULT_DAILY_RETENTION_DAYS,
    DEFAULT_EVENT_RETENTION_DAYS,
    DURATION_BUCKET_BOUNDS_MS,
    DURATION_BUCKET_COLUMNS,
    DURATION_OVERFLOW_COLUMN,
    ToolUsageStore,
)


ANALYTICS_SCHEMA = "odysseus.tool_usage_analytics.v1"
_MAX_QUALITY_COUNT = 1_000_000_000
MAX_API_SPAN_DAYS = 90
MAX_RESULT_ROWS = 250
_ANALYTICS_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,119}$")


class ToolUsageAnalyticsError(ValueError):
    """Raised when an aggregate request cannot be represented safely."""


class ToolUsageAnalyticsService:
    def __init__(self, store: ToolUsageStore):
        if not isinstance(store, ToolUsageStore):
            raise TypeError("analytics requires ToolUsageStore")
        self._store = store

    def aggregate_day(
        self,
        day: str,
        *,
        duplicates_rejected: int = 0,
        writer_failures: int = 0,
    ) -> dict[str, Any]:
        normalized_day = _day(day)
        duplicate_count = _count(duplicates_rejected, "duplicates_rejected")
        writer_failure_count = _count(writer_failures, "writer_failures")
        events = self._store._aggregation_event_rows(normalized_day)
        started = {
            str(row["invocation_id"])
            for row in events
            if row["event_kind"] == "started"
        }
        terminal = {
            str(row["invocation_id"])
            for row in events
            if row["event_kind"] == "terminal"
        }
        groups: dict[tuple[str, ...], dict[str, Any]] = {}
        all_owner_refs: set[str] = set()
        all_session_refs: set[str] = set()
        unknown_total = 0

        for event in events:
            if event["event_kind"] != "terminal":
                continue
            analytics_id = str(event["tool_analytics_id"])
            key = (
                analytics_id,
                str(event["tool_family"]),
                str(event["tool_source"]),
                str(event["surface"]),
                str(event["status"] or "unknown"),
            )
            group = groups.setdefault(
                key,
                {
                    "invocations": set(),
                    "durations": [],
                    "owners": set(),
                    "sessions": set(),
                    "retry_count": 0,
                    "unknown_identity_count": 0,
                },
            )
            group["invocations"].add(str(event["invocation_id"]))
            duration = event["duration_ms"]
            if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0:
                group["durations"].append(duration)
            owner_ref = event["owner_ref"]
            session_ref = event["session_ref"]
            if owner_ref:
                group["owners"].add(str(owner_ref))
                all_owner_refs.add(str(owner_ref))
            if session_ref:
                group["sessions"].add(str(session_ref))
                all_session_refs.add(str(session_ref))
            group["retry_count"] += max(0, int(event["retry_ordinal"] or 0))
            if analytics_id.endswith(".unclassified"):
                group["unknown_identity_count"] += 1
                unknown_total += 1

        aggregate_rows = []
        for key, group in sorted(groups.items()):
            analytics_id, family, source, surface, status = key
            durations = tuple(group["durations"])
            row: dict[str, Any] = {
                "tool_analytics_id": analytics_id,
                "tool_family": family,
                "tool_source": source,
                "surface": surface,
                "status": status,
                "invocation_count": len(group["invocations"]),
                "duration_count": len(durations),
                "duration_total_ms": sum(durations),
                "distinct_owner_count": len(group["owners"]),
                "distinct_session_count": len(group["sessions"]),
                "retry_count": group["retry_count"],
                "unknown_identity_count": group["unknown_identity_count"],
            }
            for bound, column in zip(
                DURATION_BUCKET_BOUNDS_MS,
                DURATION_BUCKET_COLUMNS,
            ):
                row[column] = sum(1 for duration in durations if duration <= bound)
            row[DURATION_OVERFLOW_COLUMN] = sum(
                1 for duration in durations if duration > DURATION_BUCKET_BOUNDS_MS[-1]
            )
            aggregate_rows.append(row)

        invocation_ids = started | terminal
        quality = {
            "invocation_count": len(invocation_ids),
            "started_count": len(started),
            "terminal_count": len(terminal),
            "complete_count": len(started & terminal),
            "incomplete_count": len(started ^ terminal),
            "distinct_owner_count": len(all_owner_refs),
            "distinct_session_count": len(all_session_refs),
            "unknown_identity_count": unknown_total,
            "duplicates_rejected": duplicate_count,
            "writer_failures": writer_failure_count,
        }
        self._store._replace_daily_analytics(
            normalized_day,
            aggregate_rows,
            quality,
        )
        return {
            "schema": ANALYTICS_SCHEMA,
            "day": normalized_day,
            "group_count": len(aggregate_rows),
            **quality,
            "coverage": _rate(quality["complete_count"], quality["invocation_count"]),
            "raw_content_visible": False,
        }

    def summarize(
        self,
        start_day: str,
        end_day: str,
        *,
        tool_analytics_id: str | None = None,
        tool_family: ToolFamily | str | None = None,
        tool_source: ToolSource | str | None = None,
        surface: ToolUsageSurface | str | None = None,
        status: ToolUsageStatus | str | None = None,
        row_limit: int = MAX_RESULT_ROWS,
        max_span_days: int = 400,
    ) -> dict[str, Any]:
        start = _day(start_day)
        end = _day(end_day)
        if start > end:
            raise ToolUsageAnalyticsError("start_day must not be after end_day")
        span_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        if (
            isinstance(max_span_days, bool)
            or not isinstance(max_span_days, int)
            or max_span_days < 1
        ):
            raise ToolUsageAnalyticsError("max_span_days must be a positive integer")
        if span_days > max_span_days:
            raise ToolUsageAnalyticsError("requested period exceeds the maximum span")
        if (
            isinstance(row_limit, bool)
            or not isinstance(row_limit, int)
            or row_limit < 1
            or row_limit > MAX_RESULT_ROWS
        ):
            raise ToolUsageAnalyticsError("row_limit is outside the bounded range")
        filters = {
            "tool_analytics_id": _canonical_tool_id(tool_analytics_id),
            "tool_family": _enum_filter(ToolFamily, tool_family, "tool_family"),
            "tool_source": _enum_filter(ToolSource, tool_source, "tool_source"),
            "surface": _enum_filter(ToolUsageSurface, surface, "surface"),
            "status": _enum_filter(ToolUsageStatus, status, "status"),
        }
        all_rows = self._store._daily_analytics_rows(start, end)
        rows = tuple(
            row
            for row in all_rows
            if all(
                expected is None or str(row[field]) == expected
                for field, expected in filters.items()
            )
        )
        quality_rows = self._store._daily_quality_rows(start, end)
        calls = sum(int(row["invocation_count"]) for row in rows)
        duration_count = sum(int(row["duration_count"]) for row in rows)
        duration_total = sum(int(row["duration_total_ms"]) for row in rows)
        histogram = {
            column: sum(int(row[column]) for row in rows)
            for column in DURATION_BUCKET_COLUMNS
        }
        overflow = sum(int(row[DURATION_OVERFLOW_COLUMN]) for row in rows)
        status_counts = Counter()
        for row in rows:
            status_counts[str(row["status"])] += int(row["invocation_count"])

        quality_totals = {
            key: sum(int(row[key]) for row in quality_rows)
            for key in (
                "invocation_count",
                "started_count",
                "terminal_count",
                "complete_count",
                "incomplete_count",
                "distinct_owner_count",
                "distinct_session_count",
                "unknown_identity_count",
                "duplicates_rejected",
                "writer_failures",
            )
        }
        complete_days = {
            str(row["day"])
            for row in quality_rows
            if int(row["aggregation_complete"]) == 1
        }
        row_days = {str(row["day"]) for row in rows}
        missing_complete_days = row_days - complete_days
        warnings = []
        if quality_totals["incomplete_count"]:
            warnings.append("incomplete_invocations")
        if quality_totals["unknown_identity_count"]:
            warnings.append("unknown_identity")
        if quality_totals["duplicates_rejected"]:
            warnings.append("duplicates_rejected")
        if quality_totals["writer_failures"]:
            warnings.append("writer_failures")
        if missing_complete_days:
            warnings.append("aggregation_incomplete")
        if duration_count and not histogram[DURATION_BUCKET_COLUMNS[-1]] and not overflow:
            warnings.append("histogram_incomplete")

        public_rows = tuple(
            {
                "day": str(row["day"]),
                "tool_analytics_id": str(row["tool_analytics_id"]),
                "tool_family": str(row["tool_family"]),
                "tool_source": str(row["tool_source"]),
                "surface": str(row["surface"]),
                "status": str(row["status"]),
                "invocation_count": int(row["invocation_count"]),
                "duration_count": int(row["duration_count"]),
                "duration_total_ms": int(row["duration_total_ms"]),
                "distinct_owner_count": int(row["distinct_owner_count"]),
                "distinct_session_count": int(row["distinct_session_count"]),
                "retry_count": int(row["retry_count"]),
                "unknown_identity_count": int(row["unknown_identity_count"]),
            }
            for row in rows[:row_limit]
        )
        active_days = len(
            {
                str(row["day"])
                for row in rows
                if int(row["invocation_count"]) > 0
            }
        )
        return {
            "schema": ANALYTICS_SCHEMA,
            "start_day": start,
            "end_day": end,
            "filters": {
                key: value for key, value in filters.items() if value is not None
            },
            "calls": calls,
            "active_days": active_days,
            "duration_count": duration_count,
            "duration_total_ms": duration_total,
            "duration_mean_ms": (
                round(duration_total / duration_count, 3)
                if duration_count
                else None
            ),
            "duration_p50_ms": _percentile(histogram, overflow, duration_count, 0.50),
            "duration_p95_ms": _percentile(histogram, overflow, duration_count, 0.95),
            "duration_overflow_count": overflow,
            "retry_count": sum(int(row["retry_count"]) for row in rows),
            "status_counts": dict(sorted(status_counts.items())),
            "status_rates": {
                key: _rate(value, calls)
                for key, value in sorted(status_counts.items())
            },
            "daily_distinct_owner_total": quality_totals["distinct_owner_count"],
            "daily_distinct_session_total": quality_totals["distinct_session_count"],
            "filtered_group_distinct_owner_total": sum(
                int(row["distinct_owner_count"]) for row in rows
            ),
            "filtered_group_distinct_session_total": sum(
                int(row["distinct_session_count"]) for row in rows
            ),
            "coverage": _rate(
                quality_totals["complete_count"],
                quality_totals["invocation_count"],
            ),
            "quality": {
                **quality_totals,
                "scope": "period_global",
                "aggregation_complete_day_count": len(complete_days),
                "warning_codes": tuple(warnings),
                "raw_content_visible": False,
            },
            "row_count": len(rows),
            "rows_truncated": len(rows) > row_limit,
            "rows": public_rows,
            "raw_content_visible": False,
        }

    def aggregate_then_retain(
        self,
        *,
        now: datetime | None = None,
        event_days: int = DEFAULT_EVENT_RETENTION_DAYS,
        daily_days: int = DEFAULT_DAILY_RETENTION_DAYS,
        dry_run: bool = True,
        quality_by_day: Mapping[str, Mapping[str, int]] | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ToolUsageAnalyticsError("retention time must be timezone-aware")
        if isinstance(event_days, bool) or not isinstance(event_days, int) or event_days < 1:
            raise ToolUsageAnalyticsError("event_days must be a positive integer")
        if (
            isinstance(daily_days, bool)
            or not isinstance(daily_days, int)
            or daily_days < event_days
        ):
            raise ToolUsageAnalyticsError(
                "daily_days must be an integer not smaller than event_days"
            )
        cutoff = (
            current.astimezone(timezone.utc) - timedelta(days=event_days)
        ).date().isoformat()
        days = self._store._event_days_before(cutoff)
        for day in days:
            supplied = (quality_by_day or {}).get(day, {})
            self.aggregate_day(
                day,
                duplicates_rejected=supplied.get("duplicates_rejected", 0),
                writer_failures=supplied.get("writer_failures", 0),
            )
        retention = self._store.apply_retention(
            now=current,
            event_days=event_days,
            daily_days=daily_days,
            dry_run=dry_run,
        )
        return {
            "schema": ANALYTICS_SCHEMA,
            "aggregated_day_count": len(days),
            "retention": retention.to_dict(),
            "raw_content_visible": False,
        }


def _day(value: Any) -> str:
    text = str(value or "")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ToolUsageAnalyticsError("day must be YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise ToolUsageAnalyticsError("day must be canonical YYYY-MM-DD")
    return text


def _count(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolUsageAnalyticsError(f"{field_name} must be an integer")
    if value < 0 or value > _MAX_QUALITY_COUNT:
        raise ToolUsageAnalyticsError(f"{field_name} is outside the bounded range")
    return value


def _canonical_tool_id(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if not _ANALYTICS_ID_RE.fullmatch(text):
        raise ToolUsageAnalyticsError("tool_analytics_id is not canonical")
    from src.builtin_tool_catalog import build_tool_analytics_identity_contract

    contract = build_tool_analytics_identity_contract()
    allowed = {
        descriptor.analytics_id for descriptor in contract.catalog.descriptors
    }
    allowed.update(value for _, value in contract.audit_dict()["dynamic_source_buckets"])
    if text not in allowed:
        raise ToolUsageAnalyticsError("tool_analytics_id is not in the TAX contract")
    return text


def _enum_filter(enum_type, value: Any, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        return enum_type(str(value)).value
    except (TypeError, ValueError) as exc:
        raise ToolUsageAnalyticsError(f"{field_name} is not a controlled value") from exc


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _percentile(
    histogram: Mapping[str, int],
    overflow: int,
    count: int,
    quantile: float,
) -> int | None:
    if count <= 0:
        return None
    rank = max(1, math.ceil(count * quantile))
    for bound, column in zip(DURATION_BUCKET_BOUNDS_MS, DURATION_BUCKET_COLUMNS):
        if int(histogram[column]) >= rank:
            return bound
    if overflow > 0:
        return DURATION_BUCKET_BOUNDS_MS[-1]
    return None

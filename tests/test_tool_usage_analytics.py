from datetime import date, datetime, timedelta, timezone
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.update_database import migrate_tool_usage_schema
from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_analytics import (
    DURATION_HISTOGRAM_BOUNDS_MS,
    ToolUsageAnalyticsService,
    ToolUsageExpectedState,
    ToolUsageObservedState,
)
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageBlockedReason,
    ToolUsageErrorClass,
    ToolUsageEventBuilder,
    ToolUsageEventKind,
    ToolUsageModelScope,
    ToolUsageResultShape,
    ToolUsageSizeBucket,
    ToolUsageStatus,
    ToolUsageSurface,
    pseudonymize_reference,
)
from src.tool_usage_store import (
    ToolUsageDailyAggregateRecord,
    ToolUsageEventRecord,
    ToolUsageStore,
)


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
DAY = NOW.date()
HMAC_KEY = b"synthetic-aggregate-key"


def _session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _store(engine):
    migrate_tool_usage_schema(engine)
    return ToolUsageStore(_session_factory(engine))


def _build_event(
    *,
    index,
    invocation_index,
    event_kind,
    occurred_at,
    tool_analytics_id="read-file",
    tool_family=ToolFamily.CODE_FILESYSTEM,
    tool_source=ToolSource.BUILTIN,
    surface=ToolUsageSurface.AGENT,
    status=None,
    duration_ms=None,
    retry_ordinal=0,
    owner_ref=None,
    session_ref=None,
):
    kwargs = {}
    if status == ToolUsageStatus.FAILED:
        kwargs["error_class"] = ToolUsageErrorClass.EXECUTION_ERROR
    elif status in {ToolUsageStatus.BLOCKED, ToolUsageStatus.REJECTED}:
        kwargs["blocked_reason_code"] = ToolUsageBlockedReason.UNKNOWN_TOOL
    built = ToolUsageEventBuilder.build(
        event_id=f"evt_{index:016d}",
        invocation_id=f"inv_{invocation_index:016d}",
        event_kind=event_kind,
        occurred_at=occurred_at,
        duration_ms=duration_ms,
        tool_analytics_id=tool_analytics_id,
        tool_family=tool_family,
        tool_source=tool_source,
        surface=surface,
        status=status,
        retry_ordinal=retry_ordinal,
        argument_size_bucket=ToolUsageSizeBucket.XS,
        result_size_bucket=(
            ToolUsageSizeBucket.S
            if event_kind == ToolUsageEventKind.TERMINAL
            else ToolUsageSizeBucket.NONE
        ),
        result_shape_bucket=(
            ToolUsageResultShape.SCALAR
            if event_kind == ToolUsageEventKind.TERMINAL
            else ToolUsageResultShape.NONE
        ),
        owner_ref=owner_ref,
        session_ref=session_ref,
        model_scope=ToolUsageModelScope.LOCAL,
        agent_mode=ToolUsageAgentMode.AGENT,
        app_version="0.25.0",
        **kwargs,
    )
    assert built.event is not None
    return built.event


def _event_pair(index, *, duration_ms, status=ToolUsageStatus.SUCCEEDED, **kwargs):
    return (
        _build_event(
            index=index * 2,
            invocation_index=index,
            event_kind=ToolUsageEventKind.STARTED,
            occurred_at=NOW + timedelta(milliseconds=index),
            retry_ordinal=kwargs.get("retry_ordinal", 0),
            owner_ref=kwargs.get("owner_ref"),
            session_ref=kwargs.get("session_ref"),
            tool_analytics_id=kwargs.get("tool_analytics_id", "read-file"),
            tool_family=kwargs.get("tool_family", ToolFamily.CODE_FILESYSTEM),
            tool_source=kwargs.get("tool_source", ToolSource.BUILTIN),
            surface=kwargs.get("surface", ToolUsageSurface.AGENT),
        ),
        _build_event(
            index=index * 2 + 1,
            invocation_index=index,
            event_kind=ToolUsageEventKind.TERMINAL,
            occurred_at=NOW + timedelta(milliseconds=index + duration_ms),
            duration_ms=duration_ms,
            status=status,
            **kwargs,
        ),
    )


def _ref(kind, value):
    return pseudonymize_reference(value, hmac_key=HMAC_KEY, kind=kind)


def test_daily_dimensions_percentiles_rates_and_distinct_counts_are_deterministic():
    engine = create_engine("sqlite:///:memory:")
    store = _store(engine)
    owner_a = _ref("owner", "synthetic-owner-a")
    owner_b = _ref("owner", "synthetic-owner-b")
    session_a = _ref("session", "synthetic-session-a")
    session_b = _ref("session", "synthetic-session-b")
    events = (
        *_event_pair(1, duration_ms=1, owner_ref=owner_a, session_ref=session_a),
        *_event_pair(
            2,
            duration_ms=10,
            owner_ref=owner_a,
            session_ref=session_b,
            retry_ordinal=1,
        ),
        *_event_pair(
            3,
            duration_ms=100,
            status=ToolUsageStatus.FAILED,
            owner_ref=owner_b,
            session_ref=session_b,
            tool_analytics_id="usage-plugin",
            tool_family=ToolFamily.PLUGINS_MCP,
            tool_source=ToolSource.PLUGIN,
            surface=ToolUsageSurface.SCHEDULER,
        ),
        *_event_pair(
            4,
            duration_ms=1_000,
            status=ToolUsageStatus.REJECTED,
            tool_analytics_id="dynamic-unclassified",
            tool_family=ToolFamily.UNCLASSIFIED_DYNAMIC,
            tool_source=ToolSource.DYNAMIC,
            surface=ToolUsageSurface.SYSTEM,
        ),
    )
    assert store.write_events(events).inserted == 8

    analytics = ToolUsageAnalyticsService(store, clock=lambda: NOW).aggregate_day(DAY)
    safe = analytics.to_safe_dict()

    assert analytics.invocations_total == 4
    assert analytics.terminal_invocations == 4
    assert analytics.retry_invocations == 1
    assert analytics.distinct_owner_count == 2
    assert analytics.distinct_session_count == 2
    assert analytics.duration_p50_ms == 10
    assert analytics.duration_p95_ms == 1_000
    assert len(analytics.duration_histogram_counts) == len(DURATION_HISTOGRAM_BOUNDS_MS)
    assert sum(analytics.duration_histogram_counts) == 4
    assert analytics.quality.coverage_rate == 1.0
    assert analytics.quality.incomplete == 0
    assert analytics.quality.unknown_identity == 1
    assert analytics.quality.aggregation_complete is True
    assert safe["summary"]["status_counts"]["succeeded"] == 2
    assert safe["summary"]["status_rates"]["failed"] == 0.25
    assert safe["summary"]["status_rates"]["rejected"] == 0.25
    encoded = json.dumps(safe, sort_keys=True)
    assert owner_a not in encoded
    assert owner_b not in encoded
    assert session_a not in encoded
    assert "evt_" not in encoded
    assert safe["raw_content_visible"] is False
    assert safe["direct_identifiers_visible"] is False


def test_repeated_day_replaces_counts_and_reports_incomplete_without_doubling():
    engine = create_engine("sqlite:///:memory:")
    factory = _session_factory(engine)
    store = _store(engine)
    started_only = _build_event(
        index=20,
        invocation_index=20,
        event_kind=ToolUsageEventKind.STARTED,
        occurred_at=NOW,
    )
    terminal_only = _build_event(
        index=21,
        invocation_index=21,
        event_kind=ToolUsageEventKind.TERMINAL,
        occurred_at=NOW,
        duration_ms=25,
        status=ToolUsageStatus.SUCCEEDED,
    )
    assert store.write_events([started_only, terminal_only]).inserted == 2
    duplicate = store.write_events([started_only, terminal_only])
    assert duplicate.duplicates == 2
    service = ToolUsageAnalyticsService(store, clock=lambda: NOW)

    first = service.aggregate_day(DAY)
    second = service.aggregate_day(DAY)

    assert first.to_safe_dict() == second.to_safe_dict()
    assert second.invocations_total == 2
    assert second.terminal_invocations == 1
    assert second.quality.coverage_rate == 0.5
    assert second.quality.incomplete == 1
    assert second.quality.duplicates_rejected == 2
    with factory() as session:
        rows = session.query(ToolUsageDailyAggregateRecord).all()
        events = session.query(ToolUsageEventRecord).all()
        assert len(rows) == 2
        assert sum(row.event_count for row in rows) == 2
        assert sum(row.invocation_count for row in rows) == 2
        assert all(row.aggregation_complete for row in rows)
        assert all(row.aggregated_at is not None for row in events)

    late_terminal = _build_event(
        index=22,
        invocation_index=20,
        event_kind=ToolUsageEventKind.TERMINAL,
        occurred_at=NOW + timedelta(milliseconds=50),
        duration_ms=50,
        status=ToolUsageStatus.SUCCEEDED,
    )
    assert store.write_events([late_terminal]).inserted == 1
    completed = service.aggregate_day(DAY)
    assert completed.quality.incomplete == 0
    assert completed.quality.coverage_rate == 1.0
    with factory() as session:
        rows = session.query(ToolUsageDailyAggregateRecord).all()
        assert len(rows) == 1
        assert rows[0].status == "succeeded"
        assert rows[0].event_count == 3
        assert rows[0].invocation_count == 2


def test_empty_deferred_default_off_and_active_days_are_stable_not_errors():
    engine = create_engine("sqlite:///:memory:")
    service = ToolUsageAnalyticsService(_store(engine), clock=lambda: NOW)

    deferred = service.aggregate_day(DAY, expected_state=ToolUsageExpectedState.DEFERRED)
    default_off = service.aggregate_day(
        DAY,
        expected_state=ToolUsageExpectedState.DEFAULT_OFF,
    )
    active = service.aggregate_day(DAY)

    assert deferred.observed_state == ToolUsageObservedState.DEFERRED
    assert default_off.observed_state == ToolUsageObservedState.DEFAULT_OFF
    assert active.observed_state == ToolUsageObservedState.NO_USAGE
    for result in (deferred, default_off, active):
        assert result.rows == ()
        assert result.invocations_total == 0
        assert result.duration_p50_ms is None
        assert result.duration_p95_ms is None
        assert result.quality.coverage_rate is None
        assert result.quality.incomplete == 0
        assert result.quality.instrumentation_error is False
        assert result.quality.aggregation_complete is True

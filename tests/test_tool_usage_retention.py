from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.update_database import migrate_tool_usage_schema
from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_analytics import ToolUsageAnalyticsService, ToolUsageObservedState
from src.tool_usage_events import (
    ToolUsageAgentMode,
    ToolUsageEventBuilder,
    ToolUsageEventKind,
    ToolUsageModelScope,
    ToolUsageResultShape,
    ToolUsageSizeBucket,
    ToolUsageStatus,
    ToolUsageSurface,
)
from src.tool_usage_store import (
    EVENT_RETENTION_DAYS,
    ToolUsageAggregationCommitResult,
    ToolUsageDailyAggregateRecord,
    ToolUsageDayReadResult,
    ToolUsageEventRecord,
    ToolUsageStore,
    ToolUsageStoredEvent,
)


NOW = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)


def _session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _event(*, index, event_kind, occurred_at, duration_ms=None, status=None):
    built = ToolUsageEventBuilder.build(
        event_id=f"evt_{index:016d}",
        invocation_id="inv_0000000000000100",
        event_kind=event_kind,
        occurred_at=occurred_at,
        duration_ms=duration_ms,
        tool_analytics_id="read-file",
        tool_family=ToolFamily.CODE_FILESYSTEM,
        tool_source=ToolSource.BUILTIN,
        surface=ToolUsageSurface.SCHEDULER,
        status=status,
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
        model_scope=ToolUsageModelScope.LOCAL,
        agent_mode=ToolUsageAgentMode.BACKGROUND,
        app_version="0.25.0",
    )
    assert built.event is not None
    return built.event


def test_old_events_are_retained_until_successful_aggregation_then_deleted():
    engine = create_engine("sqlite:///:memory:")
    migrate_tool_usage_schema(engine)
    factory = _session_factory(engine)
    store = ToolUsageStore(factory)
    old_time = NOW - timedelta(days=EVENT_RETENTION_DAYS + 2)
    events = (
        _event(index=100, event_kind=ToolUsageEventKind.STARTED, occurred_at=old_time),
        _event(
            index=101,
            event_kind=ToolUsageEventKind.TERMINAL,
            occurred_at=old_time + timedelta(milliseconds=10),
            duration_ms=10,
            status=ToolUsageStatus.SUCCEEDED,
        ),
    )
    assert store.write_events(events).inserted == 2

    before_aggregation = store.enforce_retention(now=NOW, dry_run=False)
    assert before_aggregation.eligible_event_count == 0
    assert before_aggregation.deleted_event_count == 0

    result = ToolUsageAnalyticsService(store, clock=lambda: NOW).aggregate_then_retain(
        old_time.date(),
        now=NOW,
        dry_run=False,
    )

    assert result.analytics.quality.aggregation_complete is True
    assert result.retention_attempted is True
    assert result.retention is not None
    assert result.retention.deleted_event_count == 2
    assert result.retention.deleted_aggregate_count == 0
    with factory() as session:
        assert session.query(ToolUsageEventRecord).count() == 0
        aggregates = session.query(ToolUsageDailyAggregateRecord).all()
        assert len(aggregates) == 1
        assert aggregates[0].aggregation_complete is True


def test_default_dry_run_follows_aggregation_and_preserves_rows():
    engine = create_engine("sqlite:///:memory:")
    migrate_tool_usage_schema(engine)
    factory = _session_factory(engine)
    store = ToolUsageStore(factory)
    old_time = NOW - timedelta(days=EVENT_RETENTION_DAYS + 2)
    assert store.write_events(
        [
            _event(index=110, event_kind=ToolUsageEventKind.STARTED, occurred_at=old_time),
            _event(
                index=111,
                event_kind=ToolUsageEventKind.TERMINAL,
                occurred_at=old_time + timedelta(milliseconds=25),
                duration_ms=25,
                status=ToolUsageStatus.SUCCEEDED,
            ),
        ]
    ).inserted == 2

    result = ToolUsageAnalyticsService(store, clock=lambda: NOW).aggregate_then_retain(
        old_time.date(),
        now=NOW,
    )

    assert result.retention_attempted is True
    assert result.retention is not None
    assert result.retention.dry_run is True
    assert result.retention.eligible_event_count == 2
    assert result.retention.deleted_event_count == 0
    with factory() as session:
        assert session.query(ToolUsageEventRecord).count() == 2


class _AggregateWriterFailureStore(ToolUsageStore):
    def __init__(self):
        super().__init__(lambda: None)
        self.commit_called = False
        self.retention_called = False

    def read_events_for_day(self, day):
        return ToolUsageDayReadResult(
            events=(
                ToolUsageStoredEvent(
                    event_id="evt_0000000000000200",
                    invocation_id="inv_0000000000000200",
                    event_kind=ToolUsageEventKind.STARTED.value,
                    occurred_at=NOW,
                    duration_ms=None,
                    tool_analytics_id="read-file",
                    tool_family=ToolFamily.CODE_FILESYSTEM.value,
                    tool_source=ToolSource.BUILTIN.value,
                    surface=ToolUsageSurface.SYSTEM.value,
                    status=None,
                    retry_ordinal=0,
                    owner_ref=None,
                    session_ref=None,
                ),
            ),
            failures=0,
        )

    def commit_day_aggregation(self, day, aggregates, event_ids, *, aggregated_at=None):
        self.commit_called = True
        self._record_quality("writer_failures")
        return ToolUsageAggregationCommitResult(0, 0, 1)

    def enforce_retention(self, **kwargs):
        self.retention_called = True
        raise AssertionError("retention must not run after aggregate failure")


def test_aggregate_writer_failure_blocks_marking_and_retention():
    store = _AggregateWriterFailureStore()

    result = ToolUsageAnalyticsService(store, clock=lambda: NOW).aggregate_then_retain(
        NOW.date(),
        now=NOW,
        dry_run=False,
    )

    assert result.analytics.observed_state == ToolUsageObservedState.OBSERVED
    assert result.analytics.quality.aggregation_complete is False
    assert result.analytics.quality.instrumentation_error is True
    assert result.analytics.quality.writer_failures == 1
    assert result.retention_attempted is False
    assert result.retention is None
    assert store.commit_called is True
    assert store.retention_called is False


def test_reader_failure_is_stable_and_never_attempts_retention():
    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("synthetic unavailable")

    store = ToolUsageStore(BrokenFactory())
    result = ToolUsageAnalyticsService(store, clock=lambda: NOW).aggregate_then_retain(
        NOW.date(),
        now=NOW,
        dry_run=False,
    )

    assert result.analytics.observed_state == ToolUsageObservedState.READ_FAILED
    assert result.analytics.quality.aggregation_complete is False
    assert result.analytics.quality.instrumentation_error is True
    assert result.retention_attempted is False
    assert result.retention is None

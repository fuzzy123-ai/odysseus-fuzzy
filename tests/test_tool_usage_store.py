from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from scripts.update_database import migrate_tool_usage_schema
from src.tool_catalog import ToolFamily, ToolSource
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
    AGGREGATE_RETENTION_DAYS,
    EVENT_RETENTION_DAYS,
    ToolUsageAggregateStatus,
    ToolUsageDailyAggregate,
    ToolUsageDailyAggregateRecord,
    ToolUsageEventRecord,
    ToolUsageStore,
)


NOW = datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc)


def _session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _event(*, event_id: str, invocation_id: str, occurred_at: datetime = NOW):
    result = ToolUsageEventBuilder.build(
        event_id=event_id,
        invocation_id=invocation_id,
        event_kind=ToolUsageEventKind.TERMINAL,
        occurred_at=occurred_at,
        duration_ms=12,
        tool_analytics_id="read-file",
        tool_family=ToolFamily.CODE_FILESYSTEM,
        tool_source=ToolSource.BUILTIN,
        surface=ToolUsageSurface.AGENT,
        status=ToolUsageStatus.SUCCEEDED,
        argument_size_bucket=ToolUsageSizeBucket.XS,
        result_size_bucket=ToolUsageSizeBucket.S,
        result_shape_bucket=ToolUsageResultShape.SCALAR,
        model_scope=ToolUsageModelScope.LOCAL,
        agent_mode=ToolUsageAgentMode.AGENT,
        app_version="0.25.0",
    )
    assert result.persistence_allowed is True
    assert result.event is not None
    return result.event


def _store(engine):
    migrate_tool_usage_schema(engine)
    return ToolUsageStore(_session_factory(engine))


def test_batch_writer_is_idempotent_and_duplicate_is_noop():
    engine = create_engine("sqlite:///:memory:")
    store = _store(engine)
    event = _event(
        event_id="evt_0000000000000001",
        invocation_id="inv_0000000000000001",
    )

    first = store.write_events([event])
    second = store.write_events([event])

    assert first.to_safe_dict() == {
        "attempted": 1,
        "inserted": 1,
        "duplicates": 0,
        "failures": 0,
        "raw_content_visible": False,
    }
    assert second.inserted == 0
    assert second.duplicates == 1
    with _session_factory(engine)() as session:
        assert session.query(ToolUsageEventRecord).count() == 1


def test_parallel_same_invocation_event_is_stored_at_most_once(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'usage.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    store = _store(engine)
    left = _event(
        event_id="evt_0000000000000002",
        invocation_id="inv_0000000000000002",
    )
    right = _event(
        event_id="evt_0000000000000003",
        invocation_id="inv_0000000000000002",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(store.write_events, ([left], [right])))

    assert sum(result.inserted for result in results) == 1
    assert sum(result.duplicates for result in results) == 1
    assert sum(result.failures for result in results) == 0
    with _session_factory(engine)() as session:
        assert session.query(ToolUsageEventRecord).count() == 1


def test_invalid_event_and_store_failure_are_isolated_and_bounded():
    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("synthetic store unavailable")

    store = ToolUsageStore(BrokenFactory())
    event = _event(
        event_id="evt_0000000000000004",
        invocation_id="inv_0000000000000004",
    )

    invalid = store.write_events([{"raw": "forbidden"}])
    failed = store.write_events([event])

    assert invalid.failures == 1
    assert failed.failures == 1
    assert store.failure_counts() == {"invalid_event": 1, "writer_failure": 1}
    assert "synthetic store unavailable" not in str(store.failure_counts())


def test_daily_aggregate_upsert_is_idempotent_not_additive():
    engine = create_engine("sqlite:///:memory:")
    store = _store(engine)
    aggregate = ToolUsageDailyAggregate(
        day=date(2026, 7, 15),
        tool_analytics_id="read-file",
        tool_family=ToolFamily.CODE_FILESYSTEM,
        tool_source=ToolSource.BUILTIN,
        surface=ToolUsageSurface.AGENT,
        status=ToolUsageAggregateStatus.SUCCEEDED,
        event_count=4,
        invocation_count=2,
        aggregation_complete=True,
        aggregated_at=NOW,
    )

    assert store.write_daily_aggregates([aggregate]).failures == 0
    assert store.write_daily_aggregates([aggregate]).failures == 0
    with _session_factory(engine)() as session:
        rows = session.query(ToolUsageDailyAggregateRecord).all()
        assert len(rows) == 1
        assert rows[0].event_count == 4
        assert rows[0].invocation_count == 2


def test_retention_dry_run_is_count_only_and_keeps_unaggregated_events():
    engine = create_engine("sqlite:///:memory:")
    factory = _session_factory(engine)
    store = _store(engine)
    old_day = NOW - timedelta(days=EVENT_RETENTION_DAYS + 2)
    recent_day = NOW - timedelta(days=10)
    old_aggregated = _event(
        event_id="evt_0000000000000005",
        invocation_id="inv_0000000000000005",
        occurred_at=old_day,
    )
    old_unaggregated = _event(
        event_id="evt_0000000000000006",
        invocation_id="inv_0000000000000006",
        occurred_at=old_day,
    )
    recent = _event(
        event_id="evt_0000000000000007",
        invocation_id="inv_0000000000000007",
        occurred_at=recent_day,
    )
    assert store.write_events([old_aggregated, old_unaggregated, recent]).inserted == 3
    assert store.mark_events_aggregated([old_aggregated.event_id, recent.event_id], aggregated_at=NOW) == 2

    with factory() as session:
        session.add(
            ToolUsageDailyAggregateRecord(
                day=(NOW - timedelta(days=AGGREGATE_RETENTION_DAYS + 2)).date(),
                tool_analytics_id="read-file",
                tool_family=ToolFamily.CODE_FILESYSTEM.value,
                tool_source=ToolSource.BUILTIN.value,
                surface=ToolUsageSurface.AGENT.value,
                status=ToolUsageAggregateStatus.SUCCEEDED.value,
                event_count=1,
                invocation_count=1,
                aggregation_complete=True,
                aggregated_at=NOW.replace(tzinfo=None),
            )
        )
        session.commit()

    dry_run = store.enforce_retention(now=NOW)
    assert dry_run.to_safe_dict() == {
        "schema_version": 1,
        "dry_run": True,
        "event_retention_days": 90,
        "aggregate_retention_days": 400,
        "eligible_event_count": 1,
        "eligible_aggregate_count": 1,
        "deleted_event_count": 0,
        "deleted_aggregate_count": 0,
        "failures": 0,
        "raw_content_visible": False,
        "identifiers_visible": False,
    }
    with factory() as session:
        assert session.query(ToolUsageEventRecord).count() == 3
        assert session.query(ToolUsageDailyAggregateRecord).count() == 1

    applied = store.enforce_retention(now=NOW, dry_run=False)
    assert applied.deleted_event_count == 1
    assert applied.deleted_aggregate_count == 1
    with factory() as session:
        remaining = session.query(ToolUsageEventRecord).all()
        assert {row.event_id for row in remaining} == {
            old_unaggregated.event_id,
            recent.event_id,
        }


def test_retention_failure_returns_count_only_noop():
    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("synthetic retention failure")

    result = ToolUsageStore(BrokenFactory()).enforce_retention(now=NOW, dry_run=False)

    assert result.failures == 1
    assert result.deleted_event_count == 0
    assert result.deleted_aggregate_count == 0
    assert result.dry_run is True

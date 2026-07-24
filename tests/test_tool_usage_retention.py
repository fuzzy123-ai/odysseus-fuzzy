from datetime import datetime, timezone
import sqlite3

from src.tool_usage_analytics import ToolUsageAnalyticsService
from src.tool_usage_store import SCHEMA_NAME, SCHEMA_VERSION, ToolUsageStore
from tests.test_tool_usage_analytics import _pair


def _store(tmp_path):
    store = ToolUsageStore(tmp_path / "retention.sqlite3")
    store.migrate()
    return store


def test_schema_v1_upgrades_idempotently_to_histogram_and_quality_schema(tmp_path):
    database = tmp_path / "v1.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE tool_usage_schema_meta (schema_name TEXT PRIMARY KEY, schema_version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO tool_usage_schema_meta(schema_name, schema_version) VALUES (?, 1)",
            (SCHEMA_NAME,),
        )
        connection.execute(
            """
            CREATE TABLE tool_usage_daily (
                day TEXT NOT NULL,
                tool_analytics_id TEXT NOT NULL,
                tool_family TEXT NOT NULL,
                tool_source TEXT NOT NULL,
                surface TEXT NOT NULL,
                status TEXT NOT NULL,
                invocation_count INTEGER NOT NULL DEFAULT 0,
                duration_count INTEGER NOT NULL DEFAULT 0,
                duration_total_ms INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(day, tool_analytics_id, surface, status)
            )
            """
        )

    with ToolUsageStore(database) as store:
        first = store.migrate()
        second = store.migrate()

    assert first == second
    assert first["schema_version"] == SCHEMA_VERSION == 2
    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT schema_version FROM tool_usage_schema_meta WHERE schema_name = ?",
            (SCHEMA_NAME,),
        ).fetchone()[0]
        daily_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(tool_usage_daily)")
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert version == 2
    assert {
        "distinct_owner_count",
        "distinct_session_count",
        "retry_count",
        "unknown_identity_count",
        "duration_le_10",
        "duration_le_60000",
        "duration_gt_60000",
    } <= daily_columns
    assert "tool_usage_daily_quality" in tables


def test_retention_service_aggregates_first_and_is_dry_run_safe(tmp_path):
    store = _store(tmp_path)
    complete = _pair(30, day="2024-01-03")
    incomplete = _pair(31, day="2024-01-02")[0]
    store.append_events([*complete, incomplete])
    analytics = ToolUsageAnalyticsService(store)
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)

    dry_run = analytics.aggregate_then_retain(
        now=now,
        dry_run=True,
        quality_by_day={
            "2024-01-03": {
                "duplicates_rejected": 2,
                "writer_failures": 1,
            }
        },
    )
    report = analytics.summarize("2024-01-02", "2024-01-03")

    assert dry_run["aggregated_day_count"] == 2
    assert dry_run["retention"]["dry_run"] is True
    assert dry_run["retention"]["scanned_event_count"] == 3
    assert dry_run["retention"]["deletable_event_count"] == 2
    assert dry_run["retention"]["protected_event_count"] == 1
    assert dry_run["retention"]["deleted_event_count"] == 0
    assert store.counts()["event_count"] == 3
    assert report["calls"] == 1
    assert report["quality"]["incomplete_count"] == 1
    assert report["quality"]["duplicates_rejected"] == 2
    assert report["quality"]["writer_failures"] == 1

    applied = analytics.aggregate_then_retain(
        now=now,
        dry_run=False,
        quality_by_day={
            "2024-01-03": {
                "duplicates_rejected": 2,
                "writer_failures": 1,
            }
        },
    )

    assert applied["aggregated_day_count"] == 2
    assert applied["retention"]["deleted_event_count"] == 2
    assert applied["retention"]["deleted_daily_count"] == 1
    assert store.counts() == {
        "event_count": 1,
        "daily_aggregate_count": 0,
        "raw_content_visible": False,
    }
    store.close()


def test_retention_validation_happens_before_any_aggregation_write(tmp_path):
    store = _store(tmp_path)
    store.append_events(_pair(40, day="2024-01-03"))
    analytics = ToolUsageAnalyticsService(store)

    try:
        analytics.aggregate_then_retain(
            now=datetime(2026, 7, 18, tzinfo=timezone.utc),
            event_days=90,
            daily_days=10,
            dry_run=False,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("invalid retention window must fail")

    assert store._daily_quality_rows("2024-01-03", "2024-01-03") == ()
    assert store.counts()["event_count"] == 2
    store.close()


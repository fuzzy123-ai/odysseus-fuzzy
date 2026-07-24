import sqlite3

from src.tool_catalog import (
    ToolAvailability,
    ToolDescriptorV2,
    ToolEffectClass,
    ToolFamily,
    ToolLifecycle,
    ToolPermission,
    ToolRiskLevel,
    ToolSource,
    ToolVisibility,
)
from src.tool_usage_analytics import ToolUsageAnalyticsService
from src.tool_usage_events import ToolUsageEventBuilder
from src.tool_usage_store import ToolUsageStore


HMAC_KEY = b"analytics-fixture-key-" * 2


def _descriptor(*, dynamic=False):
    return ToolDescriptorV2.create(
        tool_id="dynamic.plugin.unclassified" if dynamic else "read_file",
        analytics_id="dynamic.plugin.unclassified" if dynamic else "read_file",
        display_name="Aggregate Source" if dynamic else "Read File",
        description="Bounded aggregate fixture.",
        family=(
            ToolFamily.UNCLASSIFIED_DYNAMIC
            if dynamic
            else ToolFamily.CODE_FILESYSTEM
        ),
        source=ToolSource.PLUGIN if dynamic else ToolSource.BUILTIN,
        source_id="analytics-contract" if dynamic else None,
        lifecycle=ToolLifecycle.EXPERIMENTAL if dynamic else ToolLifecycle.ACTIVE,
        availability=(
            ToolAvailability.UNAVAILABLE if dynamic else ToolAvailability.AVAILABLE
        ),
        availability_reason="unclassified-dynamic-source" if dynamic else None,
        default_enabled=not dynamic,
        default_visibility=(
            ToolVisibility.UNAVAILABLE if dynamic else ToolVisibility.VISIBLE
        ),
        risk_level=ToolRiskLevel.DANGEROUS if dynamic else ToolRiskLevel.SAFE,
        permission=ToolPermission.ADMIN if dynamic else ToolPermission.USER,
        effect_class=ToolEffectClass.CONTROL if dynamic else ToolEffectClass.READ,
        requires_confirmation=dynamic,
        schema_ref="analytics:fixture" if dynamic else "function:read_file",
        handler_ref="analytics:fixture" if dynamic else "builtin:read_file",
        prompt_ref=None if dynamic else "index:read_file",
        introduced_in="0.24.0",
    )


def _pair(
    index,
    *,
    day="2026-07-17",
    duration=10,
    status="succeeded",
    owner="owner-a",
    session="session-a",
    retry=0,
    surface="agent",
    dynamic=False,
):
    invocation_id = "tui_" + f"{index:032x}"
    builder = ToolUsageEventBuilder(app_version="0.25.0", hmac_key=HMAC_KEY)
    common = {
        "descriptor": _descriptor(dynamic=dynamic),
        "surface": surface,
        "agent_mode": "agent",
        "model_scope": "local",
        "owner_identity": owner,
        "session_identity": session,
        "run_identity": "run-a",
        "correlation_identity": "correlation-a",
        "retry_ordinal": retry,
        "invocation_id": invocation_id,
    }
    start = builder.build(
        **common,
        event_kind="started",
        event_id="tue_" + f"{index * 2:032x}",
        occurred_at=f"{day}T10:00:00.000Z",
    )
    terminal_kwargs = {}
    if status == "failed":
        terminal_kwargs["error_class"] = "execution"
    elif status == "blocked":
        terminal_kwargs["blocked_reason_code"] = "policy"
    terminal = builder.build(
        **common,
        **terminal_kwargs,
        event_kind="terminal",
        status=status,
        duration_ms=duration,
        result_size_bytes=20,
        result_shape="mapping",
        event_id="tue_" + f"{index * 2 + 1:032x}",
        occurred_at=f"{day}T10:00:01.000Z",
    )
    return start, terminal


def _store(tmp_path):
    store = ToolUsageStore(tmp_path / "usage.sqlite3")
    store.migrate()
    return store


def test_daily_rebuild_is_idempotent_and_histogram_percentiles_are_deterministic(tmp_path):
    store = _store(tmp_path)
    fixtures = (
        _pair(1, duration=5, owner="owner-a", session="session-a"),
        _pair(
            2,
            duration=20,
            status="failed",
            owner="owner-a",
            session="session-b",
        ),
        _pair(
            3,
            duration=200,
            status="blocked",
            owner="owner-b",
            session="session-c",
            surface="chat",
        ),
        _pair(
            4,
            duration=2000,
            owner="owner-b",
            session="session-c",
            retry=2,
            dynamic=True,
        ),
    )
    store.append_events(event for pair in fixtures for event in pair)
    analytics = ToolUsageAnalyticsService(store)

    first = analytics.aggregate_day(
        "2026-07-17",
        duplicates_rejected=2,
        writer_failures=1,
    )
    first_report = analytics.summarize("2026-07-17", "2026-07-17")
    second = analytics.aggregate_day(
        "2026-07-17",
        duplicates_rejected=2,
        writer_failures=1,
    )
    second_report = analytics.summarize("2026-07-17", "2026-07-17")

    assert first == second
    assert first_report == second_report
    assert first["group_count"] == 4
    assert first["invocation_count"] == 4
    assert first["coverage"] == 1.0
    assert first_report["calls"] == 4
    assert first_report["duration_mean_ms"] == 556.25
    assert first_report["duration_p50_ms"] == 50
    assert first_report["duration_p95_ms"] == 2500
    assert first_report["retry_count"] == 2
    assert first_report["status_counts"] == {
        "blocked": 1,
        "failed": 1,
        "succeeded": 2,
    }
    assert first_report["status_rates"] == {
        "blocked": 0.25,
        "failed": 0.25,
        "succeeded": 0.5,
    }
    assert first_report["daily_distinct_owner_total"] == 2
    assert first_report["daily_distinct_session_total"] == 3
    assert first_report["quality"]["unknown_identity_count"] == 1
    assert first_report["quality"]["duplicates_rejected"] == 2
    assert first_report["quality"]["writer_failures"] == 1
    assert first_report["quality"]["warning_codes"] == (
        "unknown_identity",
        "duplicates_rejected",
        "writer_failures",
    )
    encoded = repr(first_report)
    for private_value in (
        "owner-a",
        "owner-b",
        "session-a",
        "session-b",
        "session-c",
        "correlation-a",
    ):
        assert private_value not in encoded
    assert first_report["raw_content_visible"] is False
    store.close()


def test_partial_and_empty_periods_have_stable_non_error_quality(tmp_path):
    store = _store(tmp_path)
    analytics = ToolUsageAnalyticsService(store)
    complete = _pair(10, day="2026-07-16")
    start_only = _pair(11, day="2026-07-16")[0]
    terminal_only = _pair(12, day="2026-07-16")[1]
    store.append_events([*complete, start_only, terminal_only])

    partial = analytics.aggregate_day("2026-07-16")
    partial_report = analytics.summarize("2026-07-16", "2026-07-16")
    empty = analytics.aggregate_day("2026-07-15")
    empty_report = analytics.summarize("2026-07-15", "2026-07-15")
    deferred_report = analytics.summarize("2026-07-14", "2026-07-14")

    assert partial["invocation_count"] == 3
    assert partial["complete_count"] == 1
    assert partial["incomplete_count"] == 2
    assert partial["coverage"] == 0.333333
    assert partial_report["calls"] == 2
    assert partial_report["quality"]["warning_codes"] == (
        "incomplete_invocations",
    )
    assert empty["invocation_count"] == 0
    assert empty["coverage"] is None
    for report in (empty_report, deferred_report):
        assert report["calls"] == 0
        assert report["active_days"] == 0
        assert report["duration_p50_ms"] is None
        assert report["duration_p95_ms"] is None
        assert report["coverage"] is None
        assert report["quality"]["writer_failures"] == 0
        assert report["quality"]["warning_codes"] == ()
    store.close()


def test_runtime_quality_counts_keep_privacy_rejection_out_of_writer_failures(tmp_path):
    store = _store(tmp_path)
    pair = _pair(20)
    store.append_events(pair)
    duplicate = store.append_events(pair)
    store.close()
    failed = store.append_best_effort(pair)

    assert duplicate.duplicate_count == 2
    assert failed.failure_count == 2
    assert store.quality_counts() == {
        "duplicates_rejected": 2,
        "persistence_rejected": 0,
        "writer_failures": 1,
        "raw_content_visible": False,
    }


def test_daily_and_quality_tables_contain_aggregate_columns_only(tmp_path):
    database = tmp_path / "aggregate-columns.sqlite3"
    with ToolUsageStore(database) as store:
        store.migrate()
    with sqlite3.connect(database) as connection:
        daily = {
            row[1]
            for row in connection.execute("PRAGMA table_info(tool_usage_daily)")
        }
        quality = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(tool_usage_daily_quality)"
            )
        }

    forbidden = {
        "args",
        "command",
        "content",
        "correlation_ref",
        "metadata",
        "output",
        "owner_ref",
        "payload",
        "prompt",
        "result",
        "run_ref",
        "session_ref",
        "url",
    }
    assert not (daily & forbidden)
    assert not (quality & forbidden)

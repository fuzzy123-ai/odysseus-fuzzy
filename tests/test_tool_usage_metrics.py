from __future__ import annotations

from itertools import product

import pytest

from src.observability_metrics import (
    MAX_TOOL_USAGE_LABELSETS,
    ObservabilityMetricsError,
    TOOL_USAGE_DURATION_BOUNDS_SECONDS,
    build_runtime_histogram_sample,
    build_runtime_metric_sample,
    build_tool_usage_metrics_snapshot,
    render_prometheus_text,
)
from src.tool_catalog import ToolFamily, ToolSource
from src.tool_usage_events import ToolUsageStatus, ToolUsageSurface
from src.tool_usage_store import (
    DURATION_BUCKET_BOUNDS_MS,
    DURATION_BUCKET_COLUMNS,
    DURATION_OVERFLOW_COLUMN,
    ToolUsageStore,
)


def _aggregate_row(
    tool_id: str,
    *,
    day: str = "2026-07-17",
    family: str = "code_filesystem",
    source: str = "builtin",
    surface: str = "agent",
    status: str = "succeeded",
    durations: tuple[int, ...] = (5,),
    invocation_count: int | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "day": day,
        "tool_analytics_id": tool_id,
        "tool_family": family,
        "tool_source": source,
        "surface": surface,
        "status": status,
        "invocation_count": (
            len(durations) if invocation_count is None else invocation_count
        ),
        "duration_count": len(durations),
        "duration_total_ms": sum(durations),
        "distinct_owner_count": 0,
        "distinct_session_count": 0,
        "retry_count": 0,
        "unknown_identity_count": 0,
    }
    for bound, column in zip(DURATION_BUCKET_BOUNDS_MS, DURATION_BUCKET_COLUMNS):
        row[column] = sum(1 for duration in durations if duration <= bound)
    row[DURATION_OVERFLOW_COLUMN] = sum(
        1 for duration in durations if duration > DURATION_BUCKET_BOUNDS_MS[-1]
    )
    return row


def test_projects_daily_aggregates_into_bounded_counters_and_histograms():
    rows = (
        _aggregate_row("read_file", durations=(5, 20)),
        _aggregate_row("write_file", durations=(200,)),
        _aggregate_row(
            "github_issue_read",
            family="projects_repositories",
            source="plugin",
            surface="chat",
            status="failed",
            durations=(1500,),
        ),
        _aggregate_row(
            "run_command",
            family="code_filesystem",
            source="builtin",
            surface="system",
            status="blocked",
            durations=(70_000,),
        ),
    )

    snapshot = build_tool_usage_metrics_snapshot(rows)
    text = render_prometheus_text(snapshot)

    assert snapshot["source_kind"] == "daily_aggregates"
    assert snapshot["aggregate_row_count"] == 4
    assert snapshot["labelset_count"] == 3
    assert snapshot["allowed_labels"] == ("family", "source", "status", "surface")
    assert snapshot["tool_id_label_allowed"] is False
    assert snapshot["pseudonymous_reference_labels_allowed"] is False
    assert snapshot["raw_content_visible"] is False
    assert "# TYPE tool_usage_invocations_total counter" in text
    assert "# TYPE tool_usage_duration_seconds histogram" in text
    assert (
        'tool_usage_invocations_total{family="code_filesystem",source="builtin",'
        'status="succeeded",surface="agent"} 3'
    ) in text
    assert (
        'tool_usage_failures_total{family="projects_repositories",source="plugin",'
        'status="failed",surface="chat"} 1'
    ) in text
    assert (
        'tool_usage_blocked_total{family="code_filesystem",source="builtin",'
        'status="blocked",surface="system"} 1'
    ) in text
    assert (
        'tool_usage_duration_seconds_bucket{family="code_filesystem",le="0.01",'
        'source="builtin",status="succeeded",surface="agent"} 1'
    ) in text
    assert (
        'tool_usage_duration_seconds_bucket{family="code_filesystem",le="+Inf",'
        'source="builtin",status="blocked",surface="system"} 1'
    ) in text
    assert (
        'tool_usage_duration_seconds_sum{family="code_filesystem",source="builtin",'
        'status="succeeded",surface="agent"} 0.225'
    ) in text
    for forbidden in (
        "read_file",
        "write_file",
        "github_issue_read",
        "run_command",
        "tool_analytics_id",
        "owner_ref",
        "session_ref",
        "correlation_ref",
    ):
        assert forbidden not in text


def test_round_trips_histograms_from_serialized_snapshot():
    snapshot = build_tool_usage_metrics_snapshot(
        [_aggregate_row("read_file", durations=(10, 60_001))]
    )

    first = render_prometheus_text(snapshot)
    serialized = {**snapshot, "samples": tuple(dict(sample) for sample in snapshot["samples"])}
    second = render_prometheus_text(serialized)

    assert first == second
    assert "le=\"60\"" in second
    assert "le=\"+Inf\"" in second
    assert "tool_usage_duration_seconds_count" in second


def test_projects_the_persistent_daily_store_contract_without_raw_events(tmp_path):
    store = ToolUsageStore(tmp_path / "tool-usage.sqlite3")
    store.migrate()
    row = _aggregate_row("read_file", durations=(5, 20))
    store._replace_daily_analytics(
        "2026-07-17",
        [row],
        {
            "invocation_count": 2,
            "started_count": 2,
            "terminal_count": 2,
            "complete_count": 2,
            "incomplete_count": 0,
            "distinct_owner_count": 0,
            "distinct_session_count": 0,
            "unknown_identity_count": 0,
            "duplicates_rejected": 0,
            "writer_failures": 0,
        },
    )

    daily_rows = store._daily_analytics_rows("2026-07-17", "2026-07-17")
    snapshot = build_tool_usage_metrics_snapshot(daily_rows)
    text = render_prometheus_text(snapshot)
    store.close()

    assert snapshot["aggregate_row_count"] == 1
    assert "tool_usage_invocations_total" in text
    assert "read_file" not in text
    assert "invocation_id" not in text


def test_rejects_raw_event_fields_pseudonymous_refs_and_uncontrolled_labels():
    row = _aggregate_row("read_file")
    with pytest.raises(ObservabilityMetricsError, match="unsupported tool usage aggregate field"):
        build_tool_usage_metrics_snapshot([{**row, "owner_ref": "hmac-ref"}])
    with pytest.raises(ObservabilityMetricsError, match="unsupported tool usage aggregate field"):
        build_tool_usage_metrics_snapshot(
            [{"event_kind": "terminal", "invocation_id": "opaque"}]
        )
    with pytest.raises(ObservabilityMetricsError, match="unsupported controlled tool family"):
        build_tool_usage_metrics_snapshot(
            [{**row, "tool_family": "customer-private-family"}]
        )
    with pytest.raises(ObservabilityMetricsError, match="unsupported metric label"):
        build_runtime_metric_sample(
            "tool_usage_invocations_total",
            1,
            labels={"owner": "opaque"},
        )
    with pytest.raises(ObservabilityMetricsError, match="unsupported controlled family label"):
        build_runtime_metric_sample(
            "tool_usage_invocations_total",
            1,
            labels={"family": "customer-private-family"},
        )


def test_rejects_unbounded_cardinality_duplicate_rows_and_invalid_histograms():
    dimensions = product(
        (item.value for item in ToolFamily),
        (item.value for item in ToolSource),
        (item.value for item in ToolUsageSurface),
        (item.value for item in ToolUsageStatus),
    )
    rows = [
        _aggregate_row(
            f"tool_{index}",
            family=family,
            source=source,
            surface=surface,
            status=status,
        )
        for index, (family, source, surface, status) in zip(
            range(MAX_TOOL_USAGE_LABELSETS + 1), dimensions
        )
    ]
    with pytest.raises(ObservabilityMetricsError, match="cardinality limit"):
        build_tool_usage_metrics_snapshot(rows)

    duplicate = _aggregate_row("read_file")
    with pytest.raises(ObservabilityMetricsError, match="duplicate"):
        build_tool_usage_metrics_snapshot([duplicate, duplicate])

    invalid_histogram = _aggregate_row("read_file", durations=(5,))
    invalid_histogram[DURATION_BUCKET_COLUMNS[1]] = 0
    with pytest.raises(ObservabilityMetricsError, match="cumulative duration histogram"):
        build_tool_usage_metrics_snapshot([invalid_histogram])

    wrong_bounds = dict.fromkeys(TOOL_USAGE_DURATION_BOUNDS_SECONDS, 0)
    wrong_bounds.pop(TOOL_USAGE_DURATION_BOUNDS_SECONDS[-1])
    wrong_bounds[120.0] = 0
    with pytest.raises(ObservabilityMetricsError, match="fixed contract"):
        build_runtime_histogram_sample(
            "tool_usage_duration_seconds",
            count=0,
            sum_value=0,
            buckets=wrong_bounds,
            labels={
                "family": "code_filesystem",
                "source": "builtin",
                "surface": "agent",
                "status": "succeeded",
            },
        )

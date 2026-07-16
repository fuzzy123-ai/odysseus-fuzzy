from __future__ import annotations

import json

import pytest

from src.observability_metrics import (
    ObservabilityMetricsError,
    build_runtime_metric_sample,
    build_tool_usage_metrics_from_aggregate,
    render_prometheus_text,
)


def _aggregate_snapshot():
    return {
        "schema_version": "odysseus.tool_usage_analytics.v1",
        "range": {"start": "2026-07-10", "end": "2026-07-16", "days": 7},
        "filters": {
            "tool": None,
            "family": None,
            "source": None,
            "surface": None,
            "status": None,
        },
        "summary": {
            "calls": 8,
            "duration_samples": 4,
            "pseudonymous_distinct_owner_count": 2,
            "pseudonymous_distinct_session_count": 3,
        },
        "quality": {"result_truncated": False},
        "duration_histogram": {
            "bounds_ms": [1, 10, 100],
            "counts": [1, 2, 1],
        },
        "rows": [
            {
                "tool_analytics_id": "read-file",
                "tool_family": "code_filesystem",
                "tool_source": "builtin",
                "surface": "agent",
                "status": "succeeded",
                "calls": 2,
            },
            {
                "tool_analytics_id": "write-file",
                "tool_family": "code_filesystem",
                "tool_source": "builtin",
                "surface": "agent",
                "status": "succeeded",
                "calls": 3,
            },
            {
                "tool_analytics_id": "usage-plugin",
                "tool_family": "plugins_mcp",
                "tool_source": "plugin",
                "surface": "scheduler",
                "status": "failed",
                "calls": 2,
            },
            {
                "tool_analytics_id": "dynamic-unclassified",
                "tool_family": "unclassified_dynamic",
                "tool_source": "dynamic",
                "surface": "system",
                "status": "rejected",
                "calls": 1,
            },
        ],
        "raw_records_visible": False,
        "raw_content_visible": False,
        "direct_identifiers_visible": False,
    }


def test_projects_only_bounded_dimensions_and_coalesces_tool_ids():
    metrics = build_tool_usage_metrics_from_aggregate(_aggregate_snapshot())
    samples = metrics["samples"]

    succeeded = next(
        sample
        for sample in samples
        if sample["name"] == "tool_usage_invocations_total"
        and sample["labels"]["status"] == "succeeded"
    )
    assert succeeded == {
        "name": "tool_usage_invocations_total",
        "value": 5.0,
        "labels": {
            "family": "code_filesystem",
            "source": "builtin",
            "status": "succeeded",
            "surface": "agent",
        },
    }
    assert sum(sample["value"] for sample in samples if sample["name"] == "tool_usage_failures_total") == 2
    assert sum(sample["value"] for sample in samples if sample["name"] == "tool_usage_blocked_total") == 1
    assert all(set(sample["labels"]) <= {"family", "source", "surface", "status"} for sample in samples)

    encoded = json.dumps(metrics, sort_keys=True)
    assert "read-file" not in encoded
    assert "write-file" not in encoded
    assert "usage-plugin" not in encoded
    assert "dynamic-unclassified" not in encoded
    assert "synthetic-owner" not in encoded


def test_renders_fixed_aggregate_histogram_without_raw_events_or_references():
    aggregate = _aggregate_snapshot()
    aggregate["summary"]["private_reference_for_test"] = "synthetic-value-that-is-never-projected"

    text = render_prometheus_text(build_tool_usage_metrics_from_aggregate(aggregate))

    assert "# TYPE tool_usage_duration_milliseconds histogram" in text
    assert 'tool_usage_duration_milliseconds_bucket{le="1"} 1' in text
    assert 'tool_usage_duration_milliseconds_bucket{le="10"} 3' in text
    assert 'tool_usage_duration_milliseconds_bucket{le="+Inf"} 4' in text
    assert "tool_usage_duration_milliseconds_count 4" in text
    assert "private_reference_for_test" not in text
    assert "synthetic-value-that-is-never-projected" not in text
    assert "tool_analytics_id" not in text


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["rows"][0].update(tool_family="customer-123"), "label value"),
        (lambda value: value["rows"][0].update(owner="synthetic-owner"), "row field"),
        (lambda value: value.update(events=[]), "raw event"),
        (lambda value: value["filters"].update(tool="read-file"), "tool-filtered"),
        (lambda value: value["quality"].update(result_truncated=True), "truncated"),
    ],
)
def test_rejects_unknown_high_cardinality_raw_or_ambiguous_aggregate_shapes(mutation, message):
    aggregate = _aggregate_snapshot()
    mutation(aggregate)

    with pytest.raises(ObservabilityMetricsError, match=message):
        build_tool_usage_metrics_from_aggregate(aggregate)


def test_tool_usage_metric_samples_reject_noncanonical_or_cross_contract_labels():
    with pytest.raises(ObservabilityMetricsError, match="label value"):
        build_runtime_metric_sample(
            "tool_usage_invocations_total",
            1,
            labels={"family": "tenant-123"},
        )

    with pytest.raises(ObservabilityMetricsError, match="tool usage metric label"):
        build_runtime_metric_sample(
            "tool_usage_invocations_total",
            1,
            labels={"component": "scheduler"},
        )


def test_rejects_unbounded_or_inconsistent_histograms():
    aggregate = _aggregate_snapshot()
    aggregate["duration_histogram"]["counts"] = [1, 2, 2]
    with pytest.raises(ObservabilityMetricsError, match="sample count mismatch"):
        build_tool_usage_metrics_from_aggregate(aggregate)

    aggregate = _aggregate_snapshot()
    aggregate["duration_histogram"]["bounds_ms"] = [1, 1, 100]
    with pytest.raises(ObservabilityMetricsError, match="strictly increasing"):
        build_tool_usage_metrics_from_aggregate(aggregate)

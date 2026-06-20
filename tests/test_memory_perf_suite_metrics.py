import pytest

from src.memory_perf_suite_metrics import MetricsCollector, summarize_latency


def test_latency_summary_calculates_percentiles():
    summary = summarize_latency("append", [1, 2, 3, 4, 5])

    assert summary.to_dict() == {
        "name": "append",
        "count": 5,
        "p50_ms": 3.0,
        "p95_ms": 4.8,
        "p99_ms": 4.96,
    }
    assert [metric.name for metric in summary.to_metrics()] == [
        "append_count",
        "append_p50_ms",
        "append_p95_ms",
        "append_p99_ms",
    ]


def test_metrics_collector_records_counters_and_phases():
    collector = MetricsCollector()

    collector.increment("events-committed", 2)
    collector.observe_latency("replay", 4)
    collector.observe_latency("replay", 6)
    metrics = {metric.name: metric.value for metric in collector.metrics()}

    assert metrics["events_committed"] == 2
    assert metrics["replay_count"] == 2
    assert metrics["replay_p50_ms"] == 5


def test_metrics_collector_rejects_unstarted_phase():
    collector = MetricsCollector()

    with pytest.raises(ValueError, match="not started"):
        collector.end_phase("missing")

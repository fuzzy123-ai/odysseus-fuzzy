import pytest

from src.memory_perf_suite_metrics import (
    MetricsCollector,
    ResourceMonitor,
    ResourceObservation,
    evaluate_performance_gate,
    summarize_latency,
)
from src.memory_perf_suite_models import ResourceBudget


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


def test_resource_monitor_observes_runtime_memory_and_temp_disk(tmp_path):
    (tmp_path / "artifact.bin").write_bytes(b"x" * 128)
    monitor = ResourceMonitor(tmp_path)

    monitor.start()
    payload = [str(index) for index in range(100)]
    monitor.sample()
    observation = monitor.finish()

    assert payload[0] == "0"
    assert observation.runtime_seconds >= 0
    assert observation.peak_rss_delta_mb >= 0
    assert observation.peak_traced_memory_mb >= 0
    assert observation.temp_disk_bytes == 128


def test_performance_gate_fails_on_temp_disk_budget():
    budget = ResourceBudget.create(
        max_events=10,
        max_event_bytes=4096,
        max_log_bytes=10,
        max_runtime_seconds=60,
        max_memory_mb=1024,
    )

    result = evaluate_performance_gate(
        ResourceObservation(
            runtime_seconds=1,
            peak_rss_delta_mb=1,
            peak_traced_memory_mb=1,
            temp_disk_bytes=128,
        ),
        budget,
    )

    assert result.passed is False
    assert result.failures == ("temp_disk_budget_exceeded",)

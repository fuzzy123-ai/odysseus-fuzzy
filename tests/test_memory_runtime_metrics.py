import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.memory_runtime_metrics import (
    DROP_METRIC_NAME,
    MAX_MEMORY_RUNTIME_SERIES,
    MEMORY_RUNTIME_HISTOGRAM_BUCKETS,
    MEMORY_RUNTIME_LABEL_ENUMS,
    MEMORY_RUNTIME_METRIC_DEFINITIONS,
    MemoryRuntimeMetricsError,
    MemoryRuntimeMetricsRegistry,
    get_memory_runtime_metrics_registry,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "plans" / "graphrag-raptor-memory-metrics-contract.md"


def _contract() -> dict:
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    raw = text.split("<!-- GRO_CONTRACT_JSON_BEGIN -->", 1)[1].split(
        "<!-- GRO_CONTRACT_JSON_END -->", 1
    )[0]
    return json.loads(raw.split("```json", 1)[1].split("```", 1)[0])


def _labels(**overrides: str) -> dict[str, str]:
    labels = {
        "component": "memory",
        "operation": "query",
        "phase": "total",
        "outcome": "success",
        "runtime": "app",
    }
    labels.update(overrides)
    return labels


def _sample(registry: MemoryRuntimeMetricsRegistry, name: str):
    return next(sample for sample in registry.snapshot().samples if sample.name == name)


def test_runtime_contract_exactly_matches_frozen_machine_contract():
    contract = _contract()

    assert contract["registry"]["max_series"] == MAX_MEMORY_RUNTIME_SERIES
    assert {
        key: set(values) for key, values in contract["labels"].items()
    } == {
        key: set(values) for key, values in MEMORY_RUNTIME_LABEL_ENUMS.items()
    }
    assert {
        key: tuple(values)
        for key, values in contract["histogram_buckets_seconds"].items()
    } == dict(MEMORY_RUNTIME_HISTOGRAM_BUCKETS)
    assert {
        row["name"]: (
            row["type"],
            tuple(row["labels"]),
            row.get("buckets"),
        )
        for row in contract["metrics"]
    } == {
        name: (definition.kind, definition.labels, definition.bucket_family)
        for name, definition in MEMORY_RUNTIME_METRIC_DEFINITIONS.items()
    }


def test_counter_is_monotonic_and_gauge_is_replaceable():
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    counter_labels = {
        "component": "memory",
        "operation": "query",
        "outcome": "success",
        "runtime": "app",
    }
    assert registry.increment_counter(
        "odysseus_memory_operations_total", counter_labels, 2
    )
    assert registry.increment_counter(
        "odysseus_memory_operations_total", counter_labels, 3.5
    )
    assert not registry.increment_counter(
        "odysseus_memory_operations_total", counter_labels, -1
    )
    assert registry.set_gauge(
        "odysseus_query_cache_entries", {"runtime": "app"}, 7
    )
    assert registry.set_gauge(
        "odysseus_query_cache_entries", {"runtime": "app"}, 2
    )

    counter = _sample(registry, "odysseus_memory_operations_total")
    gauge = _sample(registry, "odysseus_query_cache_entries")
    assert counter.value == 5.5
    assert gauge.value == 2.0
    assert registry.snapshot().dropped_samples_total == 1


def test_histogram_uses_fixed_cumulative_buckets_sum_and_count():
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    name = "odysseus_memory_operation_duration_seconds"
    labels = _labels()
    for value in (0.0005, 0.006, 100.0):
        assert registry.observe_histogram(name, labels, value)

    sample = _sample(registry, name)
    counts = [bucket.cumulative_count for bucket in sample.buckets]
    assert sample.count == 3
    assert sample.sum_value == pytest.approx(100.0065)
    assert tuple(bucket.upper_bound for bucket in sample.buckets) == (
        MEMORY_RUNTIME_HISTOGRAM_BUCKETS["operation"]
    )
    assert counts[0] == 1
    assert counts[5] == 2
    assert counts[-1] == 2
    assert counts == sorted(counts)


def test_concurrent_updates_and_snapshots_are_atomic():
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    counter_name = "odysseus_raptor_cache_requests_total"
    counter_labels = {"cache_result": "hit", "runtime": "worker"}
    histogram_name = "odysseus_memory_event_loop_lag_seconds"
    histogram_labels = {
        "component": "raptorgraph",
        "operation": "raptor_status",
        "runtime": "worker",
    }

    def update() -> None:
        for _ in range(1000):
            assert registry.increment_counter(counter_name, counter_labels)
            assert registry.observe_histogram(histogram_name, histogram_labels, 0.005)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(update) for _ in range(8)]
        while any(not future.done() for future in futures):
            snapshot = registry.snapshot()
            for sample in snapshot.samples:
                if sample.kind == "histogram":
                    counts = [bucket.cumulative_count for bucket in sample.buckets]
                    assert counts == sorted(counts)
                    assert all(count <= (sample.count or 0) for count in counts)
        for future in futures:
            future.result()

    assert _sample(registry, counter_name).value == 8000.0
    histogram = _sample(registry, histogram_name)
    assert histogram.count == 8000
    assert histogram.sum_value == pytest.approx(40.0)


def test_cardinality_cap_counts_prometheus_histogram_expansion():
    registry = MemoryRuntimeMetricsRegistry.for_tests(max_series=4)
    name = "odysseus_memory_operations_total"
    base = {
        "component": "memory",
        "operation": "query",
        "outcome": "success",
        "runtime": "app",
    }
    assert registry.increment_counter(name, base)
    assert registry.increment_counter(name, {**base, "outcome": "blocked"})
    assert registry.increment_counter(name, {**base, "outcome": "error"})
    assert not registry.increment_counter(name, {**base, "outcome": "cancelled"})
    snapshot = registry.snapshot()
    assert snapshot.prometheus_series_count == 4
    assert snapshot.labelset_count == 4
    assert snapshot.dropped_samples_total == 1

    histogram_registry = MemoryRuntimeMetricsRegistry.for_tests(max_series=20)
    assert not histogram_registry.observe_histogram(
        "odysseus_memory_operation_duration_seconds", _labels(), 0.1
    )
    assert histogram_registry.snapshot().prometheus_series_count == 1
    assert histogram_registry.snapshot().dropped_samples_total == 1


@pytest.mark.parametrize(
    ("method", "name", "labels", "value"),
    [
        ("counter", "unknown_total", {}, 1),
        (
            "counter",
            "odysseus_memory_operations_total",
            {"component": "memory", "operation": "query", "outcome": "success"},
            1,
        ),
        (
            "counter",
            "odysseus_memory_operations_total",
            {
                "component": "memory",
                "operation": "query",
                "outcome": "success",
                "runtime": "private-runtime-value",
            },
            1,
        ),
        ("counter", DROP_METRIC_NAME, {}, 1),
        ("gauge", "odysseus_query_cache_entries", {"runtime": "app"}, -1),
        (
            "histogram",
            "odysseus_memory_operation_duration_seconds",
            _labels(query_text="private-marker"),
            0.1,
        ),
        (
            "histogram",
            "odysseus_memory_operation_duration_seconds",
            _labels(),
            math.inf,
        ),
    ],
)
def test_unknown_private_or_invalid_samples_fail_closed_without_payload(
    method: str, name: str, labels: dict[str, str], value: float
):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    if method == "counter":
        accepted = registry.increment_counter(name, labels, value)
    elif method == "gauge":
        accepted = registry.set_gauge(name, labels, value)
    else:
        accepted = registry.observe_histogram(name, labels, value)

    assert accepted is False
    encoded = json.dumps(registry.snapshot().to_dict(), sort_keys=True)
    assert "private-marker" not in encoded
    assert "private-runtime-value" not in encoded
    assert registry.snapshot().dropped_samples_total == 1


def test_signed_rss_delta_is_the_only_negative_gauge():
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    assert registry.set_gauge(
        "odysseus_raptor_rebuild_rss_delta_bytes", {"runtime": "benchmark"}, -4096
    )
    assert _sample(registry, "odysseus_raptor_rebuild_rss_delta_bytes").value == -4096


def test_injected_clock_drives_timer_and_negative_elapsed_is_dropped():
    ticks = iter((10.0, 10.125, 20.0, 19.0))
    registry = MemoryRuntimeMetricsRegistry.for_tests(clock=lambda: next(ticks))
    name = "odysseus_metrics_render_duration_seconds"
    labels = {"outcome": "success", "runtime": "benchmark"}

    with registry.time_histogram(name, labels):
        pass
    with registry.time_histogram(name, labels):
        pass

    sample = _sample(registry, name)
    assert sample.count == 1
    assert sample.sum_value == pytest.approx(0.125)
    assert registry.snapshot().dropped_samples_total == 1


def test_reset_is_available_only_on_explicit_test_registry():
    runtime_registry = get_memory_runtime_metrics_registry()
    with pytest.raises(MemoryRuntimeMetricsError, match="unavailable"):
        runtime_registry.reset_for_tests()

    registry = MemoryRuntimeMetricsRegistry.for_tests()
    assert registry.set_gauge(
        "odysseus_raptor_cache_entries", {"runtime": "app"}, 3
    )
    registry.reset_for_tests()
    snapshot = registry.snapshot()
    assert snapshot.prometheus_series_count == 1
    assert snapshot.labelset_count == 1
    assert snapshot.dropped_samples_total == 0


@pytest.mark.parametrize("invalid", [True, 0, -1, 257, 1.5, "256"])
def test_registry_rejects_invalid_or_unbounded_series_caps(invalid):
    with pytest.raises(MemoryRuntimeMetricsError):
        MemoryRuntimeMetricsRegistry(max_series=invalid)


def test_usi12_extension_has_closed_labels_and_exact_metric_definitions():
    assert MEMORY_RUNTIME_LABEL_ENUMS["component"] == frozenset(
        {"memory", "raptorgraph", "source_index"}
    )
    assert MEMORY_RUNTIME_LABEL_ENUMS["operation"] == frozenset(
        {
            "query",
            "memory_status",
            "raptor_status",
            "rebuild",
            "cache_lookup",
            "automation",
            "index",
            "projection",
            "delete",
        }
    )
    assert MEMORY_RUNTIME_LABEL_ENUMS["record_kind"] == frozenset(
        {
            "source",
            "source_version",
            "chunk",
            "entity",
            "relation",
            "lineage",
            "projection_manifest",
            "derived_run",
            "job",
            "tombstone",
        }
    )
    assert {
        name: (definition.kind, definition.labels, definition.bucket_family)
        for name, definition in MEMORY_RUNTIME_METRIC_DEFINITIONS.items()
        if name.startswith("odysseus_usi_")
    } == {
        "odysseus_usi_operations_total": (
            "counter",
            ("operation", "outcome", "runtime"),
            None,
        ),
        "odysseus_usi_operation_duration_seconds": (
            "histogram",
            ("operation", "phase", "outcome", "runtime"),
            "operation",
        ),
        "odysseus_usi_queue_depth": (
            "gauge",
            ("operation", "runtime"),
            None,
        ),
        "odysseus_usi_stale_projections": (
            "gauge",
            ("runtime",),
            None,
        ),
        "odysseus_usi_records": (
            "gauge",
            ("record_kind", "runtime"),
            None,
        ),
    }

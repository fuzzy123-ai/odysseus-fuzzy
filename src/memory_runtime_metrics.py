"""Bounded, content-free runtime metrics for Memory, RaptorGraph and USI.

This module owns only the in-process registry. Exporter integration and runtime
instrumentation are separate GRO slices. Metric names, labels and histogram
buckets mirror the frozen GRO-00 contract and reject everything else.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping


MEMORY_RUNTIME_METRICS_SCHEMA = "odysseus.memory_runtime_metrics.v1"
MAX_MEMORY_RUNTIME_SERIES = 256
DROP_METRIC_NAME = "odysseus_metrics_samples_dropped_total"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    kind: str
    labels: tuple[str, ...]
    bucket_family: str | None = None
    allow_negative: bool = False
    internal_only: bool = False


_LABEL_ENUMS = {
    "component": frozenset({"memory", "raptorgraph", "source_index"}),
    "operation": frozenset(
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
    ),
    "phase": frozenset(
        {
            "total",
            "load_index",
            "discover",
            "read_hash",
            "build_graph",
            "cluster",
            "serialize",
            "write_artifact",
            "retrieve",
            "rank",
            "build_response",
            "invalidate",
        }
    ),
    "outcome": frozenset({"success", "blocked", "error", "cancelled"}),
    "cache_result": frozenset({"hit", "miss", "stale", "evicted", "bypass"}),
    "profile": frozenset({"quick", "standard", "stress"}),
    "runtime": frozenset({"app", "worker", "benchmark"}),
    "record_kind": frozenset(
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
    ),
}
MEMORY_RUNTIME_LABEL_ENUMS: Mapping[str, frozenset[str]] = MappingProxyType(_LABEL_ENUMS)


_HISTOGRAM_BUCKETS = {
    "operation": (
        0.0005,
        0.001,
        0.0025,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
        15.0,
        30.0,
        60.0,
    ),
    "event_loop_lag": (
        0.001,
        0.0025,
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        1.0,
    ),
    "rebuild": (
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        20.0,
        30.0,
        45.0,
        60.0,
        90.0,
        120.0,
        300.0,
    ),
    "render": (
        0.0005,
        0.001,
        0.0025,
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
    ),
}
MEMORY_RUNTIME_HISTOGRAM_BUCKETS: Mapping[str, tuple[float, ...]] = MappingProxyType(
    _HISTOGRAM_BUCKETS
)


_METRIC_DEFINITIONS = {
    "odysseus_memory_operations_total": MetricDefinition(
        "counter", ("component", "operation", "outcome", "runtime")
    ),
    "odysseus_memory_operation_duration_seconds": MetricDefinition(
        "histogram",
        ("component", "operation", "phase", "outcome", "runtime"),
        "operation",
    ),
    "odysseus_memory_event_loop_lag_seconds": MetricDefinition(
        "histogram", ("component", "operation", "runtime"), "event_loop_lag"
    ),
    "odysseus_memory_worker_queue_depth": MetricDefinition(
        "gauge", ("component", "operation", "runtime")
    ),
    "odysseus_raptor_cache_requests_total": MetricDefinition(
        "counter", ("cache_result", "runtime")
    ),
    "odysseus_raptor_cache_entries": MetricDefinition("gauge", ("runtime",)),
    "odysseus_query_cache_entries": MetricDefinition("gauge", ("runtime",)),
    "odysseus_query_cache_bytes": MetricDefinition("gauge", ("runtime",)),
    "odysseus_raptor_rebuild_duration_seconds": MetricDefinition(
        "histogram", ("phase", "outcome", "runtime"), "rebuild"
    ),
    "odysseus_raptor_rebuild_sources": MetricDefinition("gauge", ("runtime",)),
    "odysseus_raptor_rebuild_sources_per_second": MetricDefinition(
        "gauge", ("runtime",)
    ),
    "odysseus_raptor_rebuild_rss_delta_bytes": MetricDefinition(
        "gauge", ("runtime",), allow_negative=True
    ),
    "odysseus_raptor_artifact_age_seconds": MetricDefinition("gauge", ("runtime",)),
    "odysseus_usi_operations_total": MetricDefinition(
        "counter", ("operation", "outcome", "runtime")
    ),
    "odysseus_usi_operation_duration_seconds": MetricDefinition(
        "histogram", ("operation", "phase", "outcome", "runtime"), "operation"
    ),
    "odysseus_usi_queue_depth": MetricDefinition(
        "gauge", ("operation", "runtime")
    ),
    "odysseus_usi_stale_projections": MetricDefinition("gauge", ("runtime",)),
    "odysseus_usi_records": MetricDefinition(
        "gauge", ("record_kind", "runtime")
    ),
    "odysseus_metrics_render_duration_seconds": MetricDefinition(
        "histogram", ("outcome", "runtime"), "render"
    ),
    DROP_METRIC_NAME: MetricDefinition("counter", (), internal_only=True),
}
MEMORY_RUNTIME_METRIC_DEFINITIONS: Mapping[str, MetricDefinition] = MappingProxyType(
    _METRIC_DEFINITIONS
)


class MemoryRuntimeMetricsError(ValueError):
    """Raised for invalid registry construction or forbidden test operations."""


@dataclass(frozen=True, slots=True)
class HistogramBucketSnapshot:
    upper_bound: float
    cumulative_count: int

    def to_dict(self) -> dict[str, float | int]:
        return {"upper_bound": self.upper_bound, "cumulative_count": self.cumulative_count}


@dataclass(frozen=True, slots=True)
class RuntimeMetricSnapshot:
    name: str
    kind: str
    labels: tuple[tuple[str, str], ...]
    value: float | None = None
    count: int | None = None
    sum_value: float | None = None
    buckets: tuple[HistogramBucketSnapshot, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "labels": dict(self.labels),
        }
        if self.kind == "histogram":
            payload.update(
                {
                    "count": self.count,
                    "sum": self.sum_value,
                    "buckets": tuple(bucket.to_dict() for bucket in self.buckets),
                }
            )
        else:
            payload["value"] = self.value
        return payload


@dataclass(frozen=True, slots=True)
class MemoryRuntimeMetricsSnapshot:
    samples: tuple[RuntimeMetricSnapshot, ...]
    prometheus_series_count: int
    dropped_samples_total: int
    schema: str = MEMORY_RUNTIME_METRICS_SCHEMA

    @property
    def labelset_count(self) -> int:
        return len(self.samples)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "prometheus_series_count": self.prometheus_series_count,
            "labelset_count": self.labelset_count,
            "dropped_samples_total": self.dropped_samples_total,
            "samples": tuple(sample.to_dict() for sample in self.samples),
        }


@dataclass(slots=True)
class _HistogramState:
    count: int
    sum_value: float
    cumulative_counts: list[int]


_SeriesKey = tuple[str, tuple[tuple[str, str], ...]]


class MemoryRuntimeMetricsRegistry:
    """Thread-safe fixed-contract registry capped by Prometheus series cost."""

    def __init__(
        self,
        *,
        max_series: int = MAX_MEMORY_RUNTIME_SERIES,
        clock: Callable[[], float] | None = None,
        _test_mode: bool = False,
    ) -> None:
        if (
            isinstance(max_series, bool)
            or not isinstance(max_series, int)
            or max_series < 1
            or max_series > MAX_MEMORY_RUNTIME_SERIES
        ):
            raise MemoryRuntimeMetricsError(
                f"max_series must be an int between 1 and {MAX_MEMORY_RUNTIME_SERIES}"
            )
        self._lock = threading.RLock()
        self._max_series = max_series
        self._clock = clock or time.perf_counter
        self._test_mode = bool(_test_mode)
        self._series: dict[_SeriesKey, float | _HistogramState] = {}
        self._prometheus_series_count = 0
        self._initialize_drop_counter()

    @classmethod
    def for_tests(
        cls,
        *,
        max_series: int = MAX_MEMORY_RUNTIME_SERIES,
        clock: Callable[[], float] | None = None,
    ) -> "MemoryRuntimeMetricsRegistry":
        return cls(max_series=max_series, clock=clock, _test_mode=True)

    @property
    def max_series(self) -> int:
        return self._max_series

    def increment_counter(
        self,
        name: str,
        labels: Mapping[str, str],
        amount: float = 1.0,
    ) -> bool:
        validated = self._validate(name, "counter", labels)
        numeric = self._finite_number(amount)
        if validated is None or numeric is None or numeric <= 0.0:
            self._drop()
            return False
        definition, key = validated
        with self._lock:
            state = self._get_or_create_scalar(key, definition)
            if state is None:
                return False
            updated = float(state) + numeric
            if not math.isfinite(updated):
                self._drop_locked()
                return False
            self._series[key] = updated
        return True

    def set_gauge(
        self,
        name: str,
        labels: Mapping[str, str],
        value: float,
    ) -> bool:
        validated = self._validate(name, "gauge", labels)
        numeric = self._finite_number(value)
        if validated is None or numeric is None:
            self._drop()
            return False
        definition, key = validated
        if numeric < 0.0 and not definition.allow_negative:
            self._drop()
            return False
        with self._lock:
            state = self._get_or_create_scalar(key, definition)
            if state is None:
                return False
            self._series[key] = numeric
        return True

    def observe_histogram(
        self,
        name: str,
        labels: Mapping[str, str],
        value: float,
    ) -> bool:
        validated = self._validate(name, "histogram", labels)
        numeric = self._finite_number(value)
        if validated is None or numeric is None or numeric < 0.0:
            self._drop()
            return False
        definition, key = validated
        bounds = MEMORY_RUNTIME_HISTOGRAM_BUCKETS[definition.bucket_family or ""]
        with self._lock:
            state = self._series.get(key)
            if state is None:
                if not self._reserve_series_locked(definition):
                    return False
                state = _HistogramState(0, 0.0, [0 for _ in bounds])
                self._series[key] = state
            if not isinstance(state, _HistogramState):
                self._drop_locked()
                return False
            updated_sum = state.sum_value + numeric
            if not math.isfinite(updated_sum):
                self._drop_locked()
                return False
            state.count += 1
            state.sum_value = updated_sum
            for index, upper_bound in enumerate(bounds):
                if numeric <= upper_bound:
                    state.cumulative_counts[index] += 1
        return True

    @contextmanager
    def time_histogram(
        self,
        name: str,
        labels: Mapping[str, str],
    ) -> Iterator[None]:
        started = self._clock()
        try:
            yield
        finally:
            finished = self._clock()
            elapsed = self._finite_number(finished)
            start_value = self._finite_number(started)
            if elapsed is None or start_value is None:
                self._drop()
            else:
                self.observe_histogram(name, labels, elapsed - start_value)

    def snapshot(self) -> MemoryRuntimeMetricsSnapshot:
        with self._lock:
            samples = tuple(
                self._snapshot_sample(key, state)
                for key, state in sorted(self._series.items(), key=lambda item: item[0])
            )
            drop_key = (DROP_METRIC_NAME, ())
            dropped = int(float(self._series[drop_key]))
            return MemoryRuntimeMetricsSnapshot(
                samples=samples,
                prometheus_series_count=self._prometheus_series_count,
                dropped_samples_total=dropped,
            )

    def reset_for_tests(self) -> None:
        if not self._test_mode:
            raise MemoryRuntimeMetricsError("reset_for_tests is unavailable on runtime registries")
        with self._lock:
            self._series.clear()
            self._prometheus_series_count = 0
            self._initialize_drop_counter_locked()

    def _initialize_drop_counter(self) -> None:
        with self._lock:
            self._initialize_drop_counter_locked()

    def _initialize_drop_counter_locked(self) -> None:
        self._series[(DROP_METRIC_NAME, ())] = 0.0
        self._prometheus_series_count = 1

    def _validate(
        self,
        name: str,
        expected_kind: str,
        labels: Mapping[str, str],
    ) -> tuple[MetricDefinition, _SeriesKey] | None:
        if not isinstance(name, str) or not isinstance(labels, Mapping):
            return None
        definition = MEMORY_RUNTIME_METRIC_DEFINITIONS.get(name)
        if (
            definition is None
            or definition.kind != expected_kind
            or definition.internal_only
            or set(labels) != set(definition.labels)
        ):
            return None
        normalized: list[tuple[str, str]] = []
        for label_name in definition.labels:
            value = labels.get(label_name)
            if not isinstance(value, str) or value not in MEMORY_RUNTIME_LABEL_ENUMS[label_name]:
                return None
            normalized.append((label_name, value))
        return definition, (name, tuple(normalized))

    @staticmethod
    def _finite_number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None

    def _get_or_create_scalar(
        self,
        key: _SeriesKey,
        definition: MetricDefinition,
    ) -> float | None:
        state = self._series.get(key)
        if state is None:
            if not self._reserve_series_locked(definition):
                return None
            self._series[key] = 0.0
            return 0.0
        if isinstance(state, _HistogramState):
            self._drop_locked()
            return None
        return float(state)

    def _reserve_series_locked(self, definition: MetricDefinition) -> bool:
        cost = self._prometheus_cost(definition)
        if self._prometheus_series_count + cost > self._max_series:
            self._drop_locked()
            return False
        self._prometheus_series_count += cost
        return True

    @staticmethod
    def _prometheus_cost(definition: MetricDefinition) -> int:
        if definition.kind != "histogram":
            return 1
        finite_buckets = MEMORY_RUNTIME_HISTOGRAM_BUCKETS[definition.bucket_family or ""]
        return len(finite_buckets) + 3  # finite buckets, +Inf, _sum and _count

    def _drop(self) -> None:
        with self._lock:
            self._drop_locked()

    def _drop_locked(self) -> None:
        key = (DROP_METRIC_NAME, ())
        self._series[key] = float(self._series.get(key, 0.0)) + 1.0

    @staticmethod
    def _snapshot_sample(
        key: _SeriesKey,
        state: float | _HistogramState,
    ) -> RuntimeMetricSnapshot:
        name, labels = key
        definition = MEMORY_RUNTIME_METRIC_DEFINITIONS[name]
        if isinstance(state, _HistogramState):
            bounds = MEMORY_RUNTIME_HISTOGRAM_BUCKETS[definition.bucket_family or ""]
            return RuntimeMetricSnapshot(
                name=name,
                kind=definition.kind,
                labels=labels,
                count=state.count,
                sum_value=state.sum_value,
                buckets=tuple(
                    HistogramBucketSnapshot(bound, count)
                    for bound, count in zip(bounds, state.cumulative_counts)
                ),
            )
        return RuntimeMetricSnapshot(
            name=name,
            kind=definition.kind,
            labels=labels,
            value=float(state),
        )


_MEMORY_RUNTIME_METRICS_REGISTRY = MemoryRuntimeMetricsRegistry()


def get_memory_runtime_metrics_registry() -> MemoryRuntimeMetricsRegistry:
    """Return the process-local registry without exposing a runtime reset hook."""

    return _MEMORY_RUNTIME_METRICS_REGISTRY

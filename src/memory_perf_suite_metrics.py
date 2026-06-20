"""Small metrics helpers for Memory Durability Performance Suite runs."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import time
import tracemalloc
from typing import Any, Iterable

from src.memory_perf_suite_models import ResourceBudget, SuiteMetric


class MemoryPerfSuiteMetricsError(ValueError):
    """Raised when metric inputs are invalid."""


def _normalize_name(value: Any, *, field_name: str = "name") -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if not text:
        raise MemoryPerfSuiteMetricsError(f"{field_name} is required")
    return text


def _nonnegative_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise MemoryPerfSuiteMetricsError(f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MemoryPerfSuiteMetricsError(f"{field_name} must be numeric") from None
    if number < 0:
        raise MemoryPerfSuiteMetricsError(f"{field_name} must be non-negative")
    return number


@dataclass(frozen=True, slots=True)
class LatencySummary:
    name: str
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float

    def to_metrics(self) -> tuple[SuiteMetric, ...]:
        prefix = _normalize_name(self.name)
        return (
            SuiteMetric.create(name=f"{prefix}_count", value=self.count, unit="count"),
            SuiteMetric.create(name=f"{prefix}_p50_ms", value=self.p50_ms, unit="ms"),
            SuiteMetric.create(name=f"{prefix}_p95_ms", value=self.p95_ms, unit="ms"),
            SuiteMetric.create(name=f"{prefix}_p99_ms", value=self.p99_ms, unit="ms"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
        }


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    runtime_seconds: float
    peak_rss_delta_mb: float
    peak_traced_memory_mb: float
    temp_disk_bytes: int

    def to_metrics(self) -> tuple[SuiteMetric, ...]:
        return (
            SuiteMetric.create(name="runtime_seconds", value=self.runtime_seconds, unit="seconds"),
            SuiteMetric.create(name="peak_rss_delta_mb", value=self.peak_rss_delta_mb, unit="mb"),
            SuiteMetric.create(name="peak_traced_memory_mb", value=self.peak_traced_memory_mb, unit="mb"),
            SuiteMetric.create(name="temp_disk_bytes", value=self.temp_disk_bytes, unit="bytes"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_seconds": self.runtime_seconds,
            "peak_rss_delta_mb": self.peak_rss_delta_mb,
            "peak_traced_memory_mb": self.peak_traced_memory_mb,
            "temp_disk_bytes": self.temp_disk_bytes,
        }


@dataclass(frozen=True, slots=True)
class PerformanceGateResult:
    status: str
    observations: ResourceObservation
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_metrics(self) -> tuple[SuiteMetric, ...]:
        return (
            *self.observations.to_metrics(),
            SuiteMetric.create(name="performance_gate_passed", value=1 if self.passed else 0, unit="bool"),
            SuiteMetric.create(name="performance_gate_failures", value=len(self.failures), unit="count"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failures": self.failures,
            "observations": self.observations.to_dict(),
        }


@dataclass(slots=True)
class ResourceMonitor:
    """Tracks runtime, process memory growth and run-directory disk use."""

    run_dir: Path | str
    _started_at: float = 0.0
    _baseline_rss_mb: float = 0.0
    _peak_rss_mb: float = 0.0
    _peak_traced_memory_mb: float = 0.0
    _tracemalloc_started: bool = False

    def start(self) -> None:
        self._started_at = time.perf_counter()
        self._baseline_rss_mb = _current_rss_mb()
        self._peak_rss_mb = self._baseline_rss_mb
        if not tracemalloc.is_tracing():
            tracemalloc.start()
            self._tracemalloc_started = True
        self.sample()

    def sample(self) -> None:
        if self._started_at <= 0:
            raise MemoryPerfSuiteMetricsError("resource monitor was not started")
        self._peak_rss_mb = max(self._peak_rss_mb, _current_rss_mb())
        if tracemalloc.is_tracing():
            _current, peak = tracemalloc.get_traced_memory()
            self._peak_traced_memory_mb = max(self._peak_traced_memory_mb, peak / (1024 * 1024))

    def finish(self) -> ResourceObservation:
        self.sample()
        runtime_seconds = time.perf_counter() - self._started_at
        observation = ResourceObservation(
            runtime_seconds=round(runtime_seconds, 6),
            peak_rss_delta_mb=round(max(0.0, self._peak_rss_mb - self._baseline_rss_mb), 6),
            peak_traced_memory_mb=round(self._peak_traced_memory_mb, 6),
            temp_disk_bytes=_directory_size_bytes(Path(self.run_dir)),
        )
        if self._tracemalloc_started:
            tracemalloc.stop()
        return observation


def evaluate_performance_gate(
    observations: ResourceObservation,
    budget: ResourceBudget,
) -> PerformanceGateResult:
    failures: list[str] = []
    if observations.runtime_seconds > budget.max_runtime_seconds:
        failures.append("runtime_budget_exceeded")
    if observations.peak_rss_delta_mb > budget.max_memory_mb:
        failures.append("memory_budget_exceeded")
    if observations.temp_disk_bytes > budget.max_log_bytes:
        failures.append("temp_disk_budget_exceeded")
    return PerformanceGateResult(
        status="failed" if failures else "passed",
        observations=observations,
        failures=tuple(failures),
    )


def summarize_latency(name: str, samples_ms: Iterable[float]) -> LatencySummary:
    samples = sorted(_nonnegative_number(sample, field_name="sample") for sample in samples_ms)
    if not samples:
        return LatencySummary(name=_normalize_name(name), count=0, p50_ms=0.0, p95_ms=0.0, p99_ms=0.0)
    return LatencySummary(
        name=_normalize_name(name),
        count=len(samples),
        p50_ms=_percentile(samples, 0.50),
        p95_ms=_percentile(samples, 0.95),
        p99_ms=_percentile(samples, 0.99),
    )


@dataclass(slots=True)
class MetricsCollector:
    """Collects phase timings and counter metrics without external services."""

    _phase_starts: dict[str, float] = field(default_factory=dict)
    _phase_durations_ms: dict[str, list[float]] = field(default_factory=dict)
    _counters: dict[str, float] = field(default_factory=dict)

    def start_phase(self, name: str) -> None:
        phase = _normalize_name(name, field_name="phase")
        self._phase_starts[phase] = time.perf_counter()

    def end_phase(self, name: str) -> float:
        phase = _normalize_name(name, field_name="phase")
        started = self._phase_starts.pop(phase, None)
        if started is None:
            raise MemoryPerfSuiteMetricsError(f"phase was not started: {phase}")
        duration_ms = (time.perf_counter() - started) * 1000
        self.observe_latency(phase, duration_ms)
        return duration_ms

    def observe_latency(self, name: str, duration_ms: float) -> None:
        phase = _normalize_name(name)
        self._phase_durations_ms.setdefault(phase, []).append(
            _nonnegative_number(duration_ms, field_name="duration_ms")
        )

    def increment(self, name: str, value: float = 1) -> None:
        counter = _normalize_name(name)
        self._counters[counter] = self._counters.get(counter, 0.0) + _nonnegative_number(
            value,
            field_name="counter increment",
        )

    def metrics(self) -> tuple[SuiteMetric, ...]:
        values: list[SuiteMetric] = []
        for name in sorted(self._counters):
            values.append(SuiteMetric.create(name=name, value=self._counters[name], unit="count"))
        for name in sorted(self._phase_durations_ms):
            values.extend(summarize_latency(name, self._phase_durations_ms[name]).to_metrics())
        return tuple(values)


def _percentile(sorted_samples: list[float], percentile: float) -> float:
    if len(sorted_samples) == 1:
        return round(sorted_samples[0], 6)
    index = percentile * (len(sorted_samples) - 1)
    lower = int(index)
    upper = min(lower + 1, len(sorted_samples) - 1)
    if lower == upper:
        return round(sorted_samples[lower], 6)
    fraction = index - lower
    return round(sorted_samples[lower] + (sorted_samples[upper] - sorted_samples[lower]) * fraction, 6)


def _current_rss_mb() -> float:
    try:
        import psutil  # type: ignore[import-not-found]

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        if tracemalloc.is_tracing():
            current, _peak = tracemalloc.get_traced_memory()
            return current / (1024 * 1024)
        return 0.0


def _directory_size_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total

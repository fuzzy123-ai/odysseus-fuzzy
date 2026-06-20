"""Small metrics helpers for Memory Durability Performance Suite runs."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Iterable

from src.memory_perf_suite_models import SuiteMetric


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

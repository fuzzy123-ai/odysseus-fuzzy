"""Content-free Unified Source Index diagnostics on the shared GRO registry.

The adapter owns no store, exporter, event ledger or background worker. It
accepts closed enum values and aggregate numbers only, writes into the existing
process-local ``MemoryRuntimeMetricsRegistry``, and treats instrumentation
failure as fail-soft so indexing and query results are never blocked.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import time
from typing import Any, Callable, Iterator

from src.memory_runtime_metrics import (
    MEMORY_RUNTIME_LABEL_ENUMS,
    MemoryRuntimeMetricsRegistry,
    get_memory_runtime_metrics_registry,
)


USI_DIAGNOSTICS_SCHEMA = "odysseus.unified_source_index.diagnostics.v1"
USI_METRIC_PREFIX = "odysseus_usi_"
USI_OPERATIONS = frozenset({"query", "index", "projection", "rebuild", "delete"})
USI_OUTCOMES = frozenset({"success", "blocked", "error", "cancelled"})
USI_RUNTIMES = frozenset({"app", "worker", "benchmark"})
USI_RECORD_KINDS = frozenset(MEMORY_RUNTIME_LABEL_ENUMS["record_kind"])
USI_PHASES = frozenset(MEMORY_RUNTIME_LABEL_ENUMS["phase"])


@dataclass(frozen=True, slots=True)
class DiagnosticsWriteResult:
    accepted: bool
    attempted_metrics: int
    accepted_metrics: int
    reason: str
    schema: str = USI_DIAGNOSTICS_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "accepted": self.accepted,
            "attempted_metrics": self.attempted_metrics,
            "accepted_metrics": self.accepted_metrics,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class UnifiedSourceIndexDiagnosticsSnapshot:
    samples: tuple[dict[str, Any], ...]
    prometheus_series_count: int
    dropped_samples_total: int
    schema: str = USI_DIAGNOSTICS_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "metric_family": "unified_source_index",
            "sample_count": len(self.samples),
            "prometheus_series_count": self.prometheus_series_count,
            "dropped_samples_total": self.dropped_samples_total,
            "samples": self.samples,
            "raw_content_visible": False,
            "owner_source_path_query_labels_allowed": False,
            "high_cardinality_labels_allowed": False,
            "productive_source_reads": 0,
            "productive_source_writes": 0,
            "network_calls": 0,
            "model_calls": 0,
            "live_activation_authorized": False,
        }


class UnifiedSourceIndexDiagnostics:
    """Small fail-soft adapter over the one process-local GRO registry."""

    def __init__(
        self,
        registry: MemoryRuntimeMetricsRegistry | Any | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._registry = registry or get_memory_runtime_metrics_registry()
        self._clock = clock or time.perf_counter

    def record_operation(
        self,
        operation: str,
        *,
        outcome: str,
        runtime: str = "app",
        phase: str = "total",
        duration_seconds: float | None = None,
    ) -> DiagnosticsWriteResult:
        if not self._valid_token(operation, USI_OPERATIONS):
            return self._rejected("operation_rejected")
        if not self._valid_token(outcome, USI_OUTCOMES):
            return self._rejected("outcome_rejected")
        if not self._valid_token(runtime, USI_RUNTIMES):
            return self._rejected("runtime_rejected")
        if not self._valid_token(phase, USI_PHASES):
            return self._rejected("phase_rejected")
        if duration_seconds is not None and self._nonnegative_number(duration_seconds) is None:
            return self._rejected("duration_rejected")

        calls: list[tuple[Callable[..., bool], tuple[Any, ...]]] = [
            (
                self._registry.increment_counter,
                (
                    "odysseus_usi_operations_total",
                    {
                        "operation": operation,
                        "outcome": outcome,
                        "runtime": runtime,
                    },
                ),
            )
        ]
        if duration_seconds is not None:
            calls.append(
                (
                    self._registry.observe_histogram,
                    (
                        "odysseus_usi_operation_duration_seconds",
                        {
                            "operation": operation,
                            "phase": phase,
                            "outcome": outcome,
                            "runtime": runtime,
                        },
                        float(duration_seconds),
                    ),
                )
            )
        return self._write(calls)

    def set_queue_depth(
        self,
        operation: str,
        depth: int,
        *,
        runtime: str = "worker",
    ) -> DiagnosticsWriteResult:
        if not self._valid_token(operation, USI_OPERATIONS):
            return self._rejected("operation_rejected")
        if not self._valid_token(runtime, USI_RUNTIMES):
            return self._rejected("runtime_rejected")
        numeric = self._nonnegative_integer(depth)
        if numeric is None:
            return self._rejected("queue_depth_rejected")
        return self._write(
            [
                (
                    self._registry.set_gauge,
                    (
                        "odysseus_usi_queue_depth",
                        {"operation": operation, "runtime": runtime},
                        numeric,
                    ),
                )
            ]
        )

    def set_stale_projections(
        self,
        count: int,
        *,
        runtime: str = "worker",
    ) -> DiagnosticsWriteResult:
        if not self._valid_token(runtime, USI_RUNTIMES):
            return self._rejected("runtime_rejected")
        numeric = self._nonnegative_integer(count)
        if numeric is None:
            return self._rejected("stale_projection_count_rejected")
        return self._write(
            [
                (
                    self._registry.set_gauge,
                    ("odysseus_usi_stale_projections", {"runtime": runtime}, numeric),
                )
            ]
        )

    def set_record_count(
        self,
        record_kind: str,
        count: int,
        *,
        runtime: str = "app",
    ) -> DiagnosticsWriteResult:
        if not self._valid_token(record_kind, USI_RECORD_KINDS):
            return self._rejected("record_kind_rejected")
        if not self._valid_token(runtime, USI_RUNTIMES):
            return self._rejected("runtime_rejected")
        numeric = self._nonnegative_integer(count)
        if numeric is None:
            return self._rejected("record_count_rejected")
        return self._write(
            [
                (
                    self._registry.set_gauge,
                    (
                        "odysseus_usi_records",
                        {"record_kind": record_kind, "runtime": runtime},
                        numeric,
                    ),
                )
            ]
        )

    @contextmanager
    def time_operation(
        self,
        operation: str,
        *,
        runtime: str = "app",
        phase: str = "total",
    ) -> Iterator[None]:
        started = self._clock()
        try:
            yield
        except BaseException:
            self._finish_timer(
                operation,
                runtime=runtime,
                phase=phase,
                outcome="error",
                started=started,
            )
            raise
        else:
            self._finish_timer(
                operation,
                runtime=runtime,
                phase=phase,
                outcome="success",
                started=started,
            )

    def snapshot(self) -> UnifiedSourceIndexDiagnosticsSnapshot:
        try:
            snapshot = self._registry.snapshot()
            samples = tuple(
                sample.to_dict()
                for sample in snapshot.samples
                if sample.name.startswith(USI_METRIC_PREFIX)
            )
            return UnifiedSourceIndexDiagnosticsSnapshot(
                samples=samples,
                prometheus_series_count=snapshot.prometheus_series_count,
                dropped_samples_total=snapshot.dropped_samples_total,
            )
        except Exception:
            return UnifiedSourceIndexDiagnosticsSnapshot(
                samples=(),
                prometheus_series_count=0,
                dropped_samples_total=0,
            )

    def _finish_timer(
        self,
        operation: str,
        *,
        runtime: str,
        phase: str,
        outcome: str,
        started: Any,
    ) -> None:
        try:
            finished = self._clock()
            start_value = self._nonnegative_number(started)
            end_value = self._nonnegative_number(finished)
            duration = None if start_value is None or end_value is None else end_value - start_value
            self.record_operation(
                operation,
                outcome=outcome,
                runtime=runtime,
                phase=phase,
                duration_seconds=duration,
            )
        except Exception:
            return

    def _write(
        self,
        calls: list[tuple[Callable[..., bool], tuple[Any, ...]]],
    ) -> DiagnosticsWriteResult:
        accepted = 0
        try:
            for method, args in calls:
                if method(*args) is True:
                    accepted += 1
        except Exception:
            return DiagnosticsWriteResult(
                accepted=False,
                attempted_metrics=len(calls),
                accepted_metrics=accepted,
                reason="registry_unavailable",
            )
        return DiagnosticsWriteResult(
            accepted=accepted == len(calls),
            attempted_metrics=len(calls),
            accepted_metrics=accepted,
            reason="accepted" if accepted == len(calls) else "registry_rejected",
        )

    @staticmethod
    def _valid_token(value: Any, allowed: frozenset[str]) -> bool:
        return isinstance(value, str) and value in allowed

    @staticmethod
    def _nonnegative_number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        return numeric if math.isfinite(numeric) and numeric >= 0.0 else None

    @staticmethod
    def _nonnegative_integer(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    @staticmethod
    def _rejected(reason: str) -> DiagnosticsWriteResult:
        return DiagnosticsWriteResult(
            accepted=False,
            attempted_metrics=0,
            accepted_metrics=0,
            reason=reason,
        )


_USI_DIAGNOSTICS = UnifiedSourceIndexDiagnostics()


def get_unified_source_index_diagnostics() -> UnifiedSourceIndexDiagnostics:
    """Return the shared-registry adapter without exposing any reset hook."""

    return _USI_DIAGNOSTICS


__all__ = [
    "DiagnosticsWriteResult",
    "USI_DIAGNOSTICS_SCHEMA",
    "USI_OPERATIONS",
    "USI_OUTCOMES",
    "USI_PHASES",
    "USI_RECORD_KINDS",
    "USI_RUNTIMES",
    "UnifiedSourceIndexDiagnostics",
    "UnifiedSourceIndexDiagnosticsSnapshot",
    "get_unified_source_index_diagnostics",
]

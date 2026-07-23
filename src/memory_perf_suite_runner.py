"""Headless Memory Durability Performance Suite runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.local_model_scheduler import maintenance_cpu_checkpoint
from src.memory_perf_suite_data import SyntheticMemoryEvent, generate_synthetic_memory_events
from src.memory_perf_suite_eventlog import AppendOnlyJsonlEventLog
from src.memory_perf_suite_invariants import InvariantCheckResult, check_recovery_invariants
from src.memory_perf_suite_metrics import (
    MetricsCollector,
    PerformanceGateResult,
    ResourceMonitor,
    evaluate_performance_gate,
)
from src.memory_perf_suite_models import (
    MetricsEnvelope,
    ReportEnvelope,
    ScenarioPreset,
    SuiteMetric,
    build_scenario_preset,
)


CRASH_POINTS = (
    "after_intent",
    "after_memory_write",
    "after_commit",
    "after_derived_write",
    "after_archive",
)
RUNNER_SCHEMA = "odysseus.memory_perf_suite.runner.v1"


class MemoryPerfSuiteRunnerError(ValueError):
    """Raised when a suite run cannot be executed safely."""


@dataclass(frozen=True, slots=True)
class MemoryPerfSuiteRunResult:
    run_id: str
    status: str
    preset: ScenarioPreset
    crash_point: str | None
    event_log_path: str
    committed_event_count: int
    duplicate_count: int
    recovery: InvariantCheckResult
    performance_gate: PerformanceGateResult
    metrics: MetricsEnvelope
    warnings: tuple[str, ...] = ()
    schema: str = RUNNER_SCHEMA

    def to_report_envelope(self) -> ReportEnvelope:
        return ReportEnvelope(
            run_id=self.run_id,
            status="passed" if self.status == "passed" else "failed",
            preset=self.preset,
            budget_estimate=self.preset.estimate_budget(),
            metrics=self.metrics,
            warnings=self.warnings,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "status": self.status,
            "preset": self.preset.to_dict(),
            "crash_point": self.crash_point,
            "event_log_path": self.event_log_path,
            "committed_event_count": self.committed_event_count,
            "duplicate_count": self.duplicate_count,
            "recovery": self.recovery.to_dict(),
            "performance_gate": self.performance_gate.to_dict(),
            "metrics": self.metrics.to_dict(),
            "warnings": self.warnings,
        }


def run_memory_durability_scenario(
    preset: ScenarioPreset | str = "quick",
    *,
    run_dir: str | Path,
    seed: int | None = None,
    event_count: int | None = None,
    crash_point: str | None = None,
    maintenance_yield_func: Callable[[], Any] | None = None,
) -> MemoryPerfSuiteRunResult:
    scenario = build_scenario_preset(preset, seed=seed) if isinstance(preset, str) else preset
    if crash_point is not None and crash_point not in CRASH_POINTS:
        raise MemoryPerfSuiteRunnerError("unsupported crash point")
    events = generate_synthetic_memory_events(scenario, count=event_count)
    target_count = scenario.event_count if event_count is None else event_count
    if target_count > scenario.budget.max_events:
        raise MemoryPerfSuiteRunnerError("event_count exceeds scenario budget")

    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    monitor = ResourceMonitor(root)
    monitor.start()
    log_path = root / "events.jsonl"
    log = AppendOnlyJsonlEventLog(log_path)
    collector = MetricsCollector()
    memory_store: dict[str, SyntheticMemoryEvent] = {}
    derived_index: dict[str, str] = {}
    expected_committed: list[SyntheticMemoryEvent] = []
    duplicate_count = 0
    checkpoint = maintenance_yield_func or maintenance_cpu_checkpoint

    for index, event in enumerate(events):
        checkpoint()
        collector.start_phase("event_log_append")
        if log.contains_event(event_id=event.event_id, source_hash=event.source_hash):
            duplicate_count += 1
            collector.end_phase("event_log_append")
            continue
        log.append_intent(event)
        collector.end_phase("event_log_append")
        monitor.sample()
        if _should_crash(crash_point, "after_intent", index):
            break

        collector.start_phase("memory_abstraction_write")
        memory_store[event.event_id] = event
        collector.end_phase("memory_abstraction_write")
        monitor.sample()
        if _should_crash(crash_point, "after_memory_write", index):
            break

        collector.start_phase("event_log_commit")
        log.commit_event(event)
        expected_committed.append(event)
        collector.end_phase("event_log_commit")
        monitor.sample()
        if _should_crash(crash_point, "after_commit", index):
            break

        collector.start_phase("graph_index_write")
        derived_index[event.event_id] = event.subject_hash
        collector.end_phase("graph_index_write")
        monitor.sample()
        if _should_crash(crash_point, "after_derived_write", index):
            break

    checkpoint()
    collector.start_phase("recovery_replay")
    recovered_log = AppendOnlyJsonlEventLog(log_path)
    recovery = check_recovery_invariants(expected_committed, recovered_log)
    collector.end_phase("recovery_replay")
    performance_gate = evaluate_performance_gate(monitor.finish(), scenario.budget)
    collector.increment("events_requested", len(events))
    collector.increment("events_committed", len(expected_committed))
    collector.increment("duplicates", duplicate_count)
    collector.increment("recovered_events", recovery.recovered_event_count)
    collector.increment("derived_index_entries_before_recovery", len(derived_index))
    metrics = MetricsEnvelope(
        scenario_id=_run_id(scenario, crash_point),
        preset_name=scenario.name,
        metrics=(
            *collector.metrics(),
            *performance_gate.to_metrics(),
            SuiteMetric.create(name="event_log_records", value=len(recovered_log.records), unit="count"),
        ),
    )
    status = "passed" if recovery.passed and performance_gate.passed else "failed"
    if crash_point == "after_archive" and status == "passed":
        warnings = ("archive_crash_simulated_after_recovery",)
    elif not performance_gate.passed:
        warnings = ("performance_budget_exceeded",)
    else:
        warnings = ()
    return MemoryPerfSuiteRunResult(
        run_id=_run_id(scenario, crash_point),
        status=status,
        preset=scenario,
        crash_point=crash_point,
        event_log_path=str(log_path.name),
        committed_event_count=len(expected_committed),
        duplicate_count=duplicate_count,
        recovery=recovery,
        performance_gate=performance_gate,
        metrics=metrics,
        warnings=warnings,
    )


def run_memory_durability_crash_matrix(
    preset: ScenarioPreset | str = "quick",
    *,
    run_root: str | Path,
    seed: int | None = None,
    event_count: int | None = None,
) -> tuple[MemoryPerfSuiteRunResult, ...]:
    scenario = build_scenario_preset(preset, seed=seed) if isinstance(preset, str) else preset
    results = []
    for crash_point in CRASH_POINTS:
        results.append(
            run_memory_durability_scenario(
                scenario,
                run_dir=Path(run_root) / crash_point,
                event_count=event_count,
                crash_point=crash_point,
            )
        )
    return tuple(results)


def _should_crash(selected: str | None, current: str, index: int) -> bool:
    return selected == current and index == 0


def _run_id(scenario: ScenarioPreset, crash_point: str | None) -> str:
    suffix = crash_point or "complete"
    return f"{scenario.name}-seed-{scenario.seed}-{suffix}"

"""Safe report archive helpers for Memory Durability Performance Suite runs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from src.memory_perf_suite_data import FORBIDDEN_DURABLE_KEYS
from src.memory_perf_suite_runner import MemoryPerfSuiteRunResult


REPORTS_SCHEMA = "odysseus.memory_perf_suite.reports.v1"


class MemoryPerfSuiteReportError(ValueError):
    """Raised when a report would be unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class ArchivedSuiteReport:
    run_id: str
    archive_dir: str
    scenario_path: str
    report_json_path: str
    report_md_path: str
    metrics_jsonl_path: str
    performance_summary_path: str
    schema: str = REPORTS_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "archive_dir": self.archive_dir,
            "scenario_path": self.scenario_path,
            "report_json_path": self.report_json_path,
            "report_md_path": self.report_md_path,
            "metrics_jsonl_path": self.metrics_jsonl_path,
            "performance_summary_path": self.performance_summary_path,
        }


def archive_suite_report(result: MemoryPerfSuiteRunResult, archive_root: str | Path) -> ArchivedSuiteReport:
    report = result.to_report_envelope()
    report_payload = {
        "suite": result.to_dict(),
        "report": report.to_dict(),
    }
    scenario_payload = result.preset.to_dict()
    metrics_payloads = tuple(metric.to_dict() for metric in result.metrics.metrics)
    _assert_report_safe(report_payload)
    _assert_report_safe(scenario_payload)
    _assert_report_safe(metrics_payloads)

    archive_dir = Path(archive_root) / _safe_path_token(result.run_id)
    if archive_dir.exists() and any(archive_dir.iterdir()):
        raise MemoryPerfSuiteReportError("archive directory already exists")
    archive_dir.mkdir(parents=True, exist_ok=True)
    scenario_path = archive_dir / "scenario.json"
    report_json_path = archive_dir / "report.json"
    report_md_path = archive_dir / "report.md"
    metrics_jsonl_path = archive_dir / "metrics.jsonl"
    performance_summary_path = archive_dir / "performance_summary.json"

    scenario_path.write_text(_json(scenario_payload), encoding="utf-8")
    report_json_path.write_text(_json(report_payload), encoding="utf-8")
    report_md_path.write_text(render_suite_report_markdown(result), encoding="utf-8")
    metrics_jsonl_path.write_text(
        "".join(_json(metric) + "\n" for metric in metrics_payloads),
        encoding="utf-8",
    )
    performance_summary_path.write_text(_json(build_performance_summary(result)), encoding="utf-8")

    return ArchivedSuiteReport(
        run_id=result.run_id,
        archive_dir=str(archive_dir.name),
        scenario_path=str(scenario_path.name),
        report_json_path=str(report_json_path.name),
        report_md_path=str(report_md_path.name),
        metrics_jsonl_path=str(metrics_jsonl_path.name),
        performance_summary_path=str(performance_summary_path.name),
    )


def render_suite_report_markdown(result: MemoryPerfSuiteRunResult) -> str:
    recovery = result.recovery
    lines = [
        f"# Memory Durability Performance Report: {result.run_id}",
        "",
        f"- Status: `{result.status}`",
        f"- Preset: `{result.preset.name}`",
        f"- Crash point: `{result.crash_point or 'none'}`",
        f"- Committed events: `{result.committed_event_count}`",
        f"- Recovered events: `{recovery.recovered_event_count}`",
        f"- Duplicate events: `{result.duplicate_count}`",
        f"- Recovery: `{recovery.status}`",
        f"- Performance: `{result.performance_gate.status}`",
    ]
    if result.performance_gate.failures:
        lines.append(f"- Performance failures: `{', '.join(result.performance_gate.failures)}`")
    if recovery.failures:
        lines.append(f"- Failures: `{', '.join(recovery.failures)}`")
    if result.warnings:
        lines.append(f"- Warnings: `{', '.join(result.warnings)}`")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Synthetic data only.",
            "- No raw content persisted.",
            "- No secrets persisted.",
            "- Paths are archive-relative.",
        ]
    )
    markdown = "\n".join(lines) + "\n"
    _assert_report_safe(markdown)
    return markdown


@dataclass(frozen=True, slots=True)
class PerformanceComparison:
    current_run_id: str
    baseline_run_id: str
    deltas: Mapping[str, float]
    regressions: tuple[str, ...]
    schema: str = REPORTS_SCHEMA

    @property
    def passed(self) -> bool:
        return not self.regressions

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "current_run_id": self.current_run_id,
            "baseline_run_id": self.baseline_run_id,
            "deltas": dict(self.deltas),
            "regressions": self.regressions,
            "passed": self.passed,
        }


def build_performance_summary(result: MemoryPerfSuiteRunResult) -> dict[str, Any]:
    metrics = {metric.name: metric.value for metric in result.metrics.metrics}
    return {
        "schema": REPORTS_SCHEMA,
        "run_id": result.run_id,
        "status": result.status,
        "performance_gate": result.performance_gate.to_dict(),
        "metrics": metrics,
    }


def compare_performance_summaries(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    max_regression_ratio: float = 0.25,
) -> PerformanceComparison:
    current_metrics = _metric_mapping(current)
    baseline_metrics = _metric_mapping(baseline)
    deltas: dict[str, float] = {}
    regressions: list[str] = []
    tracked = ("runtime_seconds", "peak_rss_delta_mb", "temp_disk_bytes")
    for name in tracked:
        current_value = float(current_metrics.get(name, 0))
        baseline_value = float(baseline_metrics.get(name, 0))
        delta = current_value - baseline_value
        deltas[name] = round(delta, 6)
        if baseline_value > 0 and delta / baseline_value > max_regression_ratio:
            regressions.append(f"{name}_regressed")
    return PerformanceComparison(
        current_run_id=str(current.get("run_id") or ""),
        baseline_run_id=str(baseline.get("run_id") or ""),
        deltas=deltas,
        regressions=tuple(regressions),
    )


def _assert_report_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_DURABLE_KEYS:
                raise MemoryPerfSuiteReportError(f"forbidden report key: {normalized}")
            _assert_report_safe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_report_safe(item)
    elif isinstance(value, str):
        lowered = value.lower()
        forbidden_values = ("raw_text", "chat_id", "password", "api_key", "credential")
        if any(token in lowered for token in forbidden_values):
            raise MemoryPerfSuiteReportError("forbidden report text")


def _safe_path_token(value: str) -> str:
    token = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value.strip())
    if not token:
        raise MemoryPerfSuiteReportError("archive token is required")
    return token[:120]


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _metric_mapping(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    metrics = summary.get("metrics") or {}
    if not isinstance(metrics, Mapping):
        raise MemoryPerfSuiteReportError("performance summary metrics must be a mapping")
    return metrics

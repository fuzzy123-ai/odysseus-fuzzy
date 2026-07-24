"""Redacted Gemma-vs-DeepSeek maintenance benchmark comparison.

The comparison uses the synthetic maintenance benchmark cases from
``src.gemma_memory_benchmark``. It never stores prompts or raw model outputs;
reports contain only aggregate metrics, per-case pass/fail deltas, and hashes
already produced by the benchmark layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Awaitable, Callable, Mapping

from src.gemma_memory_benchmark import (
    BenchmarkCase,
    BenchmarkReport,
    ModelCaller,
    default_benchmark_cases,
    deterministic_fixture_call,
    run_benchmark,
)
from src.maintenance_model_policy import DEFAULT_MAINTENANCE_MODEL


COMPARISON_SCHEMA = "odysseus.gemma_deepseek_maintenance_comparison.v1"


@dataclass(frozen=True, slots=True)
class ComparisonModelSpec:
    label: str
    model: str
    provider: str
    live: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "model": self.model,
            "provider": self.provider,
            "live": self.live,
        }


@dataclass(frozen=True, slots=True)
class MaintenanceComparisonCaseDelta:
    case_id: str
    score_delta: float
    latency_delta_ms: int
    gemma_status: str
    deepseek_status: str
    winner: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "score_delta": round(self.score_delta, 2),
            "latency_delta_ms": self.latency_delta_ms,
            "gemma_status": self.gemma_status,
            "deepseek_status": self.deepseek_status,
            "winner": self.winner,
        }


@dataclass(frozen=True, slots=True)
class MaintenanceComparisonReport:
    gemma: ComparisonModelSpec
    deepseek: ComparisonModelSpec
    gemma_report: BenchmarkReport
    deepseek_report: BenchmarkReport
    case_deltas: tuple[MaintenanceComparisonCaseDelta, ...]
    started_at: str
    finished_at: str
    schema: str = COMPARISON_SCHEMA

    @property
    def score_delta(self) -> float:
        return float(self.deepseek_report.score) - float(self.gemma_report.score)

    @property
    def winner(self) -> str:
        if self.score_delta > 0:
            return "deepseek"
        if self.score_delta < 0:
            return "gemma"
        return "tie"

    @property
    def status(self) -> str:
        both_passed = self.gemma_report.status == "passed" and self.deepseek_report.status == "passed"
        local_only_ok = (
            _metric(self.gemma_report, "local_only_gate_pass_rate") == 100.0
            and _metric(self.deepseek_report, "local_only_gate_pass_rate") == 100.0
        )
        return "passed" if both_passed and local_only_ok else "review"

    def to_redacted_dict(self) -> dict[str, Any]:
        gemma_payload = self.gemma_report.to_redacted_dict()
        deepseek_payload = self.deepseek_report.to_redacted_dict()
        return {
            "schema": self.schema,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "winner": self.winner,
            "score_delta_deepseek_minus_gemma": round(self.score_delta, 2),
            "models": {
                "gemma": self.gemma.to_dict(),
                "deepseek": self.deepseek.to_dict(),
            },
            "metrics": {
                "gemma": dict(gemma_payload.get("metrics") or {}),
                "deepseek": dict(deepseek_payload.get("metrics") or {}),
                "latency_delta_ms": int(deepseek_payload.get("total_duration_ms") or 0)
                - int(gemma_payload.get("total_duration_ms") or 0),
                "json_valid_rate_delta": round(
                    _metric(self.deepseek_report, "json_valid_rate") - _metric(self.gemma_report, "json_valid_rate"),
                    2,
                ),
                "local_only_gate_pass_rate_delta": round(
                    _metric(self.deepseek_report, "local_only_gate_pass_rate")
                    - _metric(self.gemma_report, "local_only_gate_pass_rate"),
                    2,
                ),
            },
            "case_deltas": tuple(delta.to_dict() for delta in self.case_deltas),
            "raw_prompts_persisted": False,
            "raw_outputs_persisted": False,
            "private_content_persisted": False,
        }


async def run_maintenance_comparison(
    *,
    gemma_call_model: ModelCaller = deterministic_fixture_call,
    deepseek_call_model: ModelCaller = deterministic_fixture_call,
    gemma_model: str = DEFAULT_MAINTENANCE_MODEL,
    gemma_provider: str = "local_ollama",
    deepseek_model: str = "deepseek-v4-flash",
    deepseek_provider: str = "deepseek",
    cases: tuple[BenchmarkCase, ...] | None = None,
    gemma_live: bool = False,
    deepseek_live: bool = False,
) -> MaintenanceComparisonReport:
    selected = cases or default_benchmark_cases()
    started = _now_iso()
    gemma_report = await run_benchmark(
        model=gemma_model,
        provider=gemma_provider,
        call_model=gemma_call_model,
        cases=selected,
    )
    deepseek_report = await run_benchmark(
        model=deepseek_model,
        provider=deepseek_provider,
        call_model=deepseek_call_model,
        cases=selected,
    )
    return MaintenanceComparisonReport(
        gemma=ComparisonModelSpec("gemma", gemma_model, gemma_provider, live=gemma_live),
        deepseek=ComparisonModelSpec("deepseek", deepseek_model, deepseek_provider, live=deepseek_live),
        gemma_report=gemma_report,
        deepseek_report=deepseek_report,
        case_deltas=_case_deltas(gemma_report, deepseek_report),
        started_at=started,
        finished_at=_now_iso(),
    )


def comparison_report_to_json(report: MaintenanceComparisonReport) -> str:
    return json.dumps(report.to_redacted_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def _case_deltas(
    gemma_report: BenchmarkReport,
    deepseek_report: BenchmarkReport,
) -> tuple[MaintenanceComparisonCaseDelta, ...]:
    deepseek_by_id = {case.case_id: case for case in deepseek_report.cases}
    deltas: list[MaintenanceComparisonCaseDelta] = []
    for gemma_case in gemma_report.cases:
        deepseek_case = deepseek_by_id.get(gemma_case.case_id)
        if deepseek_case is None:
            continue
        score_delta = float(deepseek_case.score) - float(gemma_case.score)
        winner = "tie"
        if score_delta > 0:
            winner = "deepseek"
        elif score_delta < 0:
            winner = "gemma"
        deltas.append(
            MaintenanceComparisonCaseDelta(
                case_id=gemma_case.case_id,
                score_delta=score_delta,
                latency_delta_ms=int(deepseek_case.duration_ms) - int(gemma_case.duration_ms),
                gemma_status=_case_status(gemma_case),
                deepseek_status=_case_status(deepseek_case),
                winner=winner,
            )
        )
    return tuple(deltas)


def _case_status(case: Any) -> str:
    return "passed" if bool(case.schema_valid and case.local_only_pass and case.pipeline_valid) else "review"


def _metric(report: BenchmarkReport, name: str) -> float:
    value = report.to_redacted_dict().get("metrics", {}).get(name, 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def weak_deepseek_fixture_call(prompt: str) -> str:
    """Test fixture that simulates a weaker API model without leaking prompts."""

    if "case_id: smalltalk_skip_memory" in prompt:
        return await deterministic_fixture_call(prompt)
    return json.dumps(
        {
            "classification": "private",
            "document_type": "reference",
            "should_remember": True,
            "memory_write_intent_status": "ready",
            "local_only_required": False,
            "api_escalation_allowed": True,
            "raptor_target": "generic",
            "recall_answer": "Synthetic weak answer.",
            "tags": ["generic"],
        },
        sort_keys=True,
    )

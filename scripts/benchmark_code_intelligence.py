#!/usr/bin/env python3
"""Evaluate content-free three-way code-intelligence benchmark receipts.

The module can drive in-process fake providers for contract tests, or aggregate
already collected content-free receipts. It never starts an engine, reads a
source repository, invokes a model, opens a listener, or performs network I/O.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Protocol, Sequence


REPORT_SCHEMA = "odysseus.code_intelligence.evaluation_report.v1"
INPUT_SCHEMA = "odysseus.code_intelligence.evaluation_input.v1"
QUESTION_SCHEMA = "odysseus.code_intelligence.question_matrix.v1"
ARMS = ("grep_read", "cbm_only", "cbm_plus_exact_read")
QUESTION_CATEGORIES = (
    "structural",
    "exact_exhaustive",
    "semantic",
    "architecture",
    "impact",
    "negative",
)
GROUND_TRUTH_METHODS = frozenset({"source", "ast_lsp", "git", "manual_edge"})
QUALITY_OUTCOMES = frozenset(
    {"success", "partial", "failed", "timeout", "provider_error", "budget_exceeded"}
)
PERFORMANCE_SCENARIOS = (
    "empty_projection_cold_start",
    "warm_reopen",
    "first_query",
    "noop_sync",
    "edit_sync",
    "add_sync",
    "delete_sync",
    "rename_sync",
    "multi_file_burst",
)
FORBIDDEN_FIELDS = frozenset(
    {
        "prompt",
        "question",
        "source_text",
        "snippet",
        "content",
        "response",
        "model_output",
        "absolute_path",
        "host_path",
    }
)
ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,95}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class BenchmarkContractError(ValueError):
    """Raised when a receipt could bias the comparison or leak content."""


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    question_id: str
    category: str
    expected_fact_ids: tuple[str, ...]
    ground_truth_methods: tuple[str, ...]
    negative_case: bool


class BenchmarkProvider(Protocol):
    """Minimal fake/adapter contract; providers return aggregate receipts only."""

    def observe(self, question: QuestionSpec, run_ordinal: int) -> Mapping[str, Any]: ...

    def measure(self, scenario: str, run_ordinal: int) -> Mapping[str, Any]: ...


def load_question_matrix(path: Path) -> tuple[QuestionSpec, ...]:
    payload = _read_json(path)
    if payload.get("schema") != QUESTION_SCHEMA:
        raise BenchmarkContractError("unsupported question matrix schema")
    if payload.get("raw_question_text_present") is not False:
        raise BenchmarkContractError("question matrix must not contain raw question text")
    return validate_questions(payload.get("questions"))


def validate_questions(value: Any) -> tuple[QuestionSpec, ...]:
    if not isinstance(value, list) or not 30 <= len(value) <= 50:
        raise BenchmarkContractError("question matrix must contain 30 to 50 questions")
    questions: list[QuestionSpec] = []
    seen: set[str] = set()
    category_counts = {category: 0 for category in QUESTION_CATEGORIES}
    for row in value:
        _reject_forbidden_payload(row)
        if not isinstance(row, Mapping) or set(row) != {
            "id",
            "category",
            "expected_fact_ids",
            "ground_truth_methods",
            "negative_case",
        }:
            raise BenchmarkContractError("invalid question shape")
        question_id = _identifier(row["id"], "question id")
        if question_id in seen:
            raise BenchmarkContractError("duplicate question id")
        seen.add(question_id)
        category = str(row["category"])
        if category not in QUESTION_CATEGORIES:
            raise BenchmarkContractError("unsupported question category")
        expected = _identifier_sequence(row["expected_fact_ids"], "fact id", maximum=8)
        methods = _identifier_sequence(
            row["ground_truth_methods"], "ground truth method", maximum=4
        )
        if not methods or not set(methods).issubset(GROUND_TRUTH_METHODS):
            raise BenchmarkContractError("unsupported ground truth method")
        negative = row["negative_case"]
        if not isinstance(negative, bool):
            raise BenchmarkContractError("negative_case must be boolean")
        if negative != (category == "negative"):
            raise BenchmarkContractError("negative category and flag must agree")
        if negative and expected:
            raise BenchmarkContractError("negative questions cannot expect facts")
        if not negative and not expected:
            raise BenchmarkContractError("positive questions require expected facts")
        category_counts[category] += 1
        questions.append(
            QuestionSpec(question_id, category, expected, methods, negative)
        )
    if any(count < 5 for count in category_counts.values()):
        raise BenchmarkContractError("every question category requires at least five cases")
    return tuple(questions)


def run_fake_providers(
    questions: Sequence[QuestionSpec],
    providers: Mapping[str, BenchmarkProvider],
    settings: Mapping[str, Any],
    *,
    quality_repeats: int = 2,
    performance_repeats: int = 3,
) -> dict[str, Any]:
    """Collect deterministic receipts from injected providers only.

    No provider discovery, subprocess, filesystem source read or network path
    exists here. The caller explicitly supplies in-process provider objects.
    """

    normalized_settings = _settings(settings)
    if set(providers) != set(ARMS):
        raise BenchmarkContractError("exactly the three frozen arms are required")
    if isinstance(quality_repeats, bool) or quality_repeats < 2:
        raise BenchmarkContractError("at least two quality repeats are required")
    if isinstance(performance_repeats, bool) or performance_repeats < 3:
        raise BenchmarkContractError("at least three performance repeats are required")

    quality_receipts: list[dict[str, Any]] = []
    for arm in ARMS:
        provider = providers[arm]
        for question in questions:
            for run_ordinal in range(1, quality_repeats + 1):
                receipt = dict(provider.observe(question, run_ordinal))
                receipt.update(
                    {
                        "arm": arm,
                        "question_id": question.question_id,
                        "run_ordinal": run_ordinal,
                    }
                )
                quality_receipts.append(receipt)

    performance_receipts: list[dict[str, Any]] = []
    performance_provider = providers["cbm_only"]
    for scenario in PERFORMANCE_SCENARIOS:
        for run_ordinal in range(1, performance_repeats + 1):
            receipt = dict(performance_provider.measure(scenario, run_ordinal))
            receipt.update({"scenario": scenario, "run_ordinal": run_ordinal})
            performance_receipts.append(receipt)

    return {
        "schema": INPUT_SCHEMA,
        "settings": normalized_settings,
        "questions": [
            {
                "id": item.question_id,
                "category": item.category,
                "expected_fact_ids": list(item.expected_fact_ids),
                "ground_truth_methods": list(item.ground_truth_methods),
                "negative_case": item.negative_case,
            }
            for item in questions
        ],
        "quality_receipts": quality_receipts,
        "performance_receipts": performance_receipts,
        "raw_content_present": False,
        "engine_invoked": False,
        "model_invoked": False,
        "productive_source_reads": 0,
        "network_calls": 0,
    }


def evaluate_benchmark(payload: Mapping[str, Any]) -> dict[str, Any]:
    _reject_forbidden_payload(payload)
    if payload.get("schema") != INPUT_SCHEMA:
        raise BenchmarkContractError("unsupported evaluation input schema")
    required_safety = {
        "raw_content_present": False,
        "engine_invoked": False,
        "model_invoked": False,
        "productive_source_reads": 0,
        "network_calls": 0,
    }
    if any(payload.get(key) != value for key, value in required_safety.items()):
        raise BenchmarkContractError("evaluation input violates the offline safety boundary")
    settings = _settings(payload.get("settings"))
    questions = validate_questions(payload.get("questions"))
    question_map = {item.question_id: item for item in questions}
    quality = _quality_receipts(payload.get("quality_receipts"), question_map, settings)
    performance = _performance_receipts(payload.get("performance_receipts"))

    arm_summaries = {
        arm: _quality_summary(
            [row for row in quality if row["arm"] == arm], question_map
        )
        for arm in ARMS
    }
    performance_summaries = {
        scenario: _performance_summary(
            [row for row in performance if row["scenario"] == scenario]
        )
        for scenario in PERFORMANCE_SCENARIOS
    }
    core = {
        "schema": REPORT_SCHEMA,
        "settings": settings,
        "question_matrix": {
            "count": len(questions),
            "category_counts": {
                category: sum(item.category == category for item in questions)
                for category in QUESTION_CATEGORIES
            },
            "raw_question_text_present": False,
        },
        "quality": {
            "arms": arm_summaries,
            "ranking_by_mean_f1_then_cost": sorted(
                ARMS,
                key=lambda arm: (
                    -arm_summaries[arm]["mean_f1"],
                    arm_summaries[arm]["mean_calls"],
                    arm_summaries[arm]["mean_tokens"],
                    arm,
                ),
            ),
            "receipt_count": len(quality),
            "fairness_contract_equal": True,
        },
        "performance": {
            "scenarios": performance_summaries,
            "receipt_count": len(performance),
            "content_free": True,
        },
        "safety": {
            "raw_content_visible": False,
            "raw_prompt_visible": False,
            "source_snippet_visible": False,
            "model_output_visible": False,
            "host_path_visible": False,
            "engine_invoked": False,
            "model_invoked": False,
            "productive_source_reads": 0,
            "network_calls": 0,
            "processes_started": 0,
            "listeners_started": 0,
            "live_actions_performed": False,
        },
    }
    digest = hashlib.sha256(_canonical(core).encode("utf-8")).hexdigest()
    report = {**core, "evidence_digest_sha256": digest}
    _reject_forbidden_payload(report)
    return report


def _quality_receipts(
    value: Any,
    question_map: Mapping[str, QuestionSpec],
    settings: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise BenchmarkContractError("quality receipts must be a list")
    normalized: list[dict[str, Any]] = []
    matrix: dict[tuple[str, str], set[int]] = {}
    seen_cells: set[tuple[str, str, int]] = set()
    for row in value:
        _reject_forbidden_payload(row)
        if not isinstance(row, Mapping) or set(row) != {
            "arm",
            "question_id",
            "run_ordinal",
            "returned_fact_ids",
            "false_fact_ids",
            "calls",
            "tokens",
            "duration_ms",
            "outcome",
            "exact_read_used",
            "coverage_complete",
            "raw_content_visible",
        }:
            raise BenchmarkContractError("invalid quality receipt shape")
        arm = str(row["arm"])
        question_id = str(row["question_id"])
        if arm not in ARMS or question_id not in question_map:
            raise BenchmarkContractError("unknown arm or question")
        run = _bounded_int(row["run_ordinal"], "run ordinal", minimum=1, maximum=100)
        cell = (arm, question_id, run)
        if cell in seen_cells:
            raise BenchmarkContractError("duplicate quality run ordinal")
        seen_cells.add(cell)
        returned = _identifier_sequence(row["returned_fact_ids"], "returned fact", maximum=32)
        false = _identifier_sequence(row["false_fact_ids"], "false fact", maximum=32)
        if set(returned) & set(false):
            raise BenchmarkContractError("returned and false fact sets overlap")
        calls = _bounded_int(row["calls"], "calls", minimum=0, maximum=settings["tool_budget"])
        tokens = _bounded_int(row["tokens"], "tokens", minimum=0, maximum=10_000_000)
        duration = _bounded_number(
            row["duration_ms"], "duration", minimum=0, maximum=settings["time_budget_ms"]
        )
        outcome = str(row["outcome"])
        if outcome not in QUALITY_OUTCOMES:
            raise BenchmarkContractError("unsupported quality outcome")
        for field in ("exact_read_used", "coverage_complete", "raw_content_visible"):
            if not isinstance(row[field], bool):
                raise BenchmarkContractError(f"{field} must be boolean")
        if row["raw_content_visible"] is not False:
            raise BenchmarkContractError("raw content in quality receipt")
        if arm == "cbm_plus_exact_read" and not row["exact_read_used"]:
            raise BenchmarkContractError("hybrid arm must use the exact reader")
        if arm != "cbm_plus_exact_read" and row["exact_read_used"]:
            raise BenchmarkContractError("non-hybrid arm cannot claim exact reader use")
        matrix.setdefault((arm, question_id), set()).add(run)
        normalized.append(
            {
                "arm": arm,
                "question_id": question_id,
                "run_ordinal": run,
                "returned_fact_ids": returned,
                "false_fact_ids": false,
                "calls": calls,
                "tokens": tokens,
                "duration_ms": duration,
                "outcome": outcome,
                "exact_read_used": row["exact_read_used"],
                "coverage_complete": row["coverage_complete"],
            }
        )
    expected_keys = {(arm, question_id) for arm in ARMS for question_id in question_map}
    if set(matrix) != expected_keys:
        raise BenchmarkContractError("quality receipt matrix is incomplete")
    run_sets = {tuple(sorted(runs)) for runs in matrix.values()}
    if len(run_sets) != 1 or len(next(iter(run_sets))) < 2:
        raise BenchmarkContractError("every quality cell requires identical repeated runs")
    return tuple(normalized)


def _quality_summary(
    rows: Sequence[Mapping[str, Any]], question_map: Mapping[str, QuestionSpec]
) -> dict[str, Any]:
    scores: list[dict[str, float]] = []
    failures = {outcome: 0 for outcome in sorted(QUALITY_OUTCOMES - {"success"})}
    categories: dict[str, list[float]] = {category: [] for category in QUESTION_CATEGORIES}
    for row in rows:
        question = question_map[str(row["question_id"])]
        expected = set(question.expected_fact_ids)
        returned = set(row["returned_fact_ids"])
        false = set(row["false_fact_ids"])
        true_positive = len(expected & returned)
        false_positive = len((returned - expected) | false)
        false_negative = len(expected - returned)
        if not expected:
            precision = recall = f1 = 1.0 if false_positive == 0 else 0.0
        else:
            precision = true_positive / max(1, true_positive + false_positive)
            recall = true_positive / max(1, true_positive + false_negative)
            f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        scores.append({"precision": precision, "recall": recall, "f1": f1})
        categories[question.category].append(f1)
        if row["outcome"] != "success":
            failures[str(row["outcome"])] += 1
    durations = [float(row["duration_ms"]) for row in rows]
    return {
        "question_runs": len(rows),
        "mean_precision": _mean(item["precision"] for item in scores),
        "mean_recall": _mean(item["recall"] for item in scores),
        "mean_f1": _mean(item["f1"] for item in scores),
        "category_mean_f1": {
            category: _mean(values) for category, values in categories.items()
        },
        "success_rate": _mean(row["outcome"] == "success" for row in rows),
        "coverage_complete_rate": _mean(row["coverage_complete"] for row in rows),
        "exact_read_run_count": sum(bool(row["exact_read_used"]) for row in rows),
        "calls_total": sum(int(row["calls"]) for row in rows),
        "mean_calls": _mean(float(row["calls"]) for row in rows),
        "tokens_total": sum(int(row["tokens"]) for row in rows),
        "mean_tokens": _mean(float(row["tokens"]) for row in rows),
        "duration_ms_p50": _percentile(durations, 0.50),
        "duration_ms_p95": _percentile(durations, 0.95),
        "failure_categories": failures,
    }


def _performance_receipts(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise BenchmarkContractError("performance receipts must be a list")
    normalized: list[dict[str, Any]] = []
    matrix: dict[str, set[int]] = {}
    seen_cells: set[tuple[str, int]] = set()
    for row in value:
        _reject_forbidden_payload(row)
        if not isinstance(row, Mapping) or set(row) != {
            "scenario",
            "run_ordinal",
            "wall_time_ms",
            "cpu_time_ms",
            "peak_rss_bytes",
            "database_bytes",
            "database_growth_bytes",
            "touched_files",
            "touched_bytes",
            "outcome",
            "raw_content_visible",
        }:
            raise BenchmarkContractError("invalid performance receipt shape")
        scenario = str(row["scenario"])
        if scenario not in PERFORMANCE_SCENARIOS:
            raise BenchmarkContractError("unsupported performance scenario")
        run = _bounded_int(row["run_ordinal"], "run ordinal", minimum=1, maximum=100)
        cell = (scenario, run)
        if cell in seen_cells:
            raise BenchmarkContractError("duplicate performance run ordinal")
        seen_cells.add(cell)
        outcome = str(row["outcome"])
        if outcome not in QUALITY_OUTCOMES:
            raise BenchmarkContractError("unsupported performance outcome")
        if row["raw_content_visible"] is not False:
            raise BenchmarkContractError("raw content in performance receipt")
        matrix.setdefault(scenario, set()).add(run)
        normalized.append(
            {
                "scenario": scenario,
                "run_ordinal": run,
                "wall_time_ms": _bounded_number(row["wall_time_ms"], "wall time", 0, 86_400_000),
                "cpu_time_ms": _bounded_number(row["cpu_time_ms"], "cpu time", 0, 86_400_000),
                "peak_rss_bytes": _bounded_int(row["peak_rss_bytes"], "peak RSS", 0, 2**63 - 1),
                "database_bytes": _bounded_int(row["database_bytes"], "database bytes", 0, 2**63 - 1),
                "database_growth_bytes": _bounded_int(row["database_growth_bytes"], "database growth", -(2**63) + 1, 2**63 - 1),
                "touched_files": _bounded_int(row["touched_files"], "touched files", 0, 100_000),
                "touched_bytes": _bounded_int(row["touched_bytes"], "touched bytes", 0, 2**63 - 1),
                "outcome": outcome,
            }
        )
    if set(matrix) != set(PERFORMANCE_SCENARIOS):
        raise BenchmarkContractError("performance scenario matrix is incomplete")
    run_sets = {tuple(sorted(runs)) for runs in matrix.values()}
    if len(run_sets) != 1 or len(next(iter(run_sets))) < 3:
        raise BenchmarkContractError("every performance scenario requires identical repeated runs")
    return tuple(normalized)


def _performance_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "runs": len(rows),
        "wall_time_ms_p50": _percentile([float(row["wall_time_ms"]) for row in rows], 0.50),
        "wall_time_ms_p95": _percentile([float(row["wall_time_ms"]) for row in rows], 0.95),
        "cpu_time_ms_p50": _percentile([float(row["cpu_time_ms"]) for row in rows], 0.50),
        "cpu_time_ms_p95": _percentile([float(row["cpu_time_ms"]) for row in rows], 0.95),
        "peak_rss_bytes_max": max(int(row["peak_rss_bytes"]) for row in rows),
        "database_bytes_max": max(int(row["database_bytes"]) for row in rows),
        "database_growth_bytes_p50": _percentile([float(row["database_growth_bytes"]) for row in rows], 0.50),
        "touched_files_max": max(int(row["touched_files"]) for row in rows),
        "touched_bytes_max": max(int(row["touched_bytes"]) for row in rows),
        "failure_count": sum(row["outcome"] != "success" for row in rows),
    }


def _settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "model_id",
        "prompt_fingerprint",
        "tool_budget",
        "time_budget_ms",
        "repository_commit",
        "engine_commit",
        "configuration_fingerprint",
        "hardware_profile",
        "os",
    }:
        raise BenchmarkContractError("invalid benchmark settings shape")
    result = {
        "model_id": _identifier(value["model_id"], "model id"),
        "prompt_fingerprint": str(value["prompt_fingerprint"]),
        "tool_budget": _bounded_int(value["tool_budget"], "tool budget", 1, 100),
        "time_budget_ms": _bounded_int(value["time_budget_ms"], "time budget", 1, 3_600_000),
        "repository_commit": str(value["repository_commit"]),
        "engine_commit": str(value["engine_commit"]),
        "configuration_fingerprint": str(value["configuration_fingerprint"]),
        "hardware_profile": _identifier(value["hardware_profile"], "hardware profile"),
        "os": _identifier(value["os"], "os"),
    }
    for key in ("prompt_fingerprint", "configuration_fingerprint"):
        if not SHA256_RE.fullmatch(result[key]):
            raise BenchmarkContractError(f"{key} must be a lowercase SHA-256")
    for key in ("repository_commit", "engine_commit"):
        if not COMMIT_RE.fullmatch(result[key]):
            raise BenchmarkContractError(f"{key} must be a lowercase commit SHA")
    return result


def _reject_forbidden_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_FIELDS:
                raise BenchmarkContractError("raw content field is forbidden")
            _reject_forbidden_payload(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_forbidden_payload(child)


def _identifier(value: Any, field: str) -> str:
    text = str(value or "")
    if not ID_RE.fullmatch(text):
        raise BenchmarkContractError(f"invalid {field}")
    return text


def _identifier_sequence(value: Any, field: str, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise BenchmarkContractError(f"invalid {field} list")
    result = tuple(_identifier(item, field) for item in value)
    if len(set(result)) != len(result):
        raise BenchmarkContractError(f"duplicate {field}")
    return result


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise BenchmarkContractError(f"{field} is outside its bounded integer range")
    return value


def _bounded_number(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkContractError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise BenchmarkContractError(f"{field} is outside its bounded range")
    return number


def _mean(values) -> float:
    items = [float(value) for value in values]
    return round(sum(items) / len(items), 6) if items else 0.0


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 6)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkContractError(f"cannot read benchmark JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise BenchmarkContractError("benchmark JSON must be an object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = evaluate_benchmark(_read_json(args.input))
    except BenchmarkContractError as exc:
        print(f"CODE_INTELLIGENCE_BENCHMARK_INVALID {exc}")
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if args.output is not None:
        if args.check:
            current = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
            if current != rendered:
                print("CODE_INTELLIGENCE_BENCHMARK_DRIFT")
                return 1
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print("CODE_INTELLIGENCE_BENCHMARK_VALID " + report["evidence_digest_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.benchmark_code_intelligence import (
    ARMS,
    PERFORMANCE_SCENARIOS,
    QUESTION_CATEGORIES,
    BenchmarkContractError,
    QuestionSpec,
    evaluate_benchmark,
    load_question_matrix,
    main,
    run_fake_providers,
)


ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = ROOT / "tests" / "fixtures" / "code_intelligence" / "questions.json"


def _settings() -> dict:
    return {
        "model_id": "synthetic_model_v1",
        "prompt_fingerprint": "1" * 64,
        "tool_budget": 12,
        "time_budget_ms": 30_000,
        "repository_commit": "a" * 40,
        "engine_commit": "b637e3330c96cfe452da623db068c241aaa3ec01",
        "configuration_fingerprint": "2" * 64,
        "hardware_profile": "synthetic_8c_16g",
        "os": "synthetic_windows",
    }


class _FakeProvider:
    def __init__(self, arm: str) -> None:
        self.arm = arm

    def observe(self, question: QuestionSpec, run_ordinal: int) -> dict:
        expected = list(question.expected_fact_ids)
        false: list[str] = []
        if question.negative_case:
            returned: list[str] = []
            if self.arm == "cbm_only" and question.question_id == "negative_01":
                false = ["fact.false.negative01"]
        elif self.arm == "grep_read":
            returned = expected[:1]
        elif self.arm == "cbm_only":
            returned = expected if question.category != "exact_exhaustive" else expected[:1]
        else:
            returned = expected
        return {
            "returned_fact_ids": returned,
            "false_fact_ids": false,
            "calls": {"grep_read": 5, "cbm_only": 2, "cbm_plus_exact_read": 3}[self.arm],
            "tokens": {"grep_read": 900, "cbm_only": 300, "cbm_plus_exact_read": 450}[self.arm],
            "duration_ms": {"grep_read": 80, "cbm_only": 20, "cbm_plus_exact_read": 35}[self.arm]
            + run_ordinal,
            "outcome": "success",
            "exact_read_used": self.arm == "cbm_plus_exact_read",
            "coverage_complete": self.arm != "cbm_only",
            "raw_content_visible": False,
        }

    def measure(self, scenario: str, run_ordinal: int) -> dict:
        index = PERFORMANCE_SCENARIOS.index(scenario) + 1
        return {
            "wall_time_ms": index * 10 + run_ordinal,
            "cpu_time_ms": index * 6 + run_ordinal,
            "peak_rss_bytes": 10_000_000 + index * 1000 + run_ordinal,
            "database_bytes": 2_000_000 + index * 100 + run_ordinal,
            "database_growth_bytes": 0 if scenario == "noop_sync" else index * 10,
            "touched_files": 0 if scenario in {"warm_reopen", "first_query", "noop_sync"} else index,
            "touched_bytes": 0 if scenario in {"warm_reopen", "first_query", "noop_sync"} else index * 100,
            "outcome": "success",
            "raw_content_visible": False,
        }


def _payload() -> dict:
    questions = load_question_matrix(QUESTIONS_PATH)
    providers = {arm: _FakeProvider(arm) for arm in ARMS}
    return run_fake_providers(questions, providers, _settings())


def test_question_fixture_has_36_balanced_content_free_cases():
    questions = load_question_matrix(QUESTIONS_PATH)

    assert len(questions) == 36
    assert {item.category for item in questions} == set(QUESTION_CATEGORIES)
    assert {
        category: sum(item.category == category for item in questions)
        for category in QUESTION_CATEGORIES
    } == {category: 6 for category in QUESTION_CATEGORIES}
    text = QUESTIONS_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("prompt", "source_text", "snippet", "model_output", "host_path"):
        assert forbidden not in text


def test_fake_providers_build_complete_three_arm_and_performance_matrices():
    payload = _payload()

    assert len(payload["quality_receipts"]) == 36 * 3 * 2
    assert len(payload["performance_receipts"]) == len(PERFORMANCE_SCENARIOS) * 3
    assert {row["arm"] for row in payload["quality_receipts"]} == set(ARMS)
    assert {row["scenario"] for row in payload["performance_receipts"]} == set(
        PERFORMANCE_SCENARIOS
    )
    assert payload["engine_invoked"] is False
    assert payload["model_invoked"] is False
    assert payload["productive_source_reads"] == 0
    assert payload["network_calls"] == 0


def test_report_ranks_hybrid_first_and_preserves_typed_metrics():
    report = evaluate_benchmark(_payload())
    arms = report["quality"]["arms"]

    assert report["quality"]["ranking_by_mean_f1_then_cost"][0] == "cbm_plus_exact_read"
    assert arms["cbm_plus_exact_read"]["mean_f1"] == 1.0
    assert arms["cbm_plus_exact_read"]["mean_f1"] > arms["cbm_only"]["mean_f1"]
    assert arms["cbm_plus_exact_read"]["mean_f1"] > arms["grep_read"]["mean_f1"]
    assert arms["grep_read"]["mean_calls"] == 5.0
    assert arms["cbm_only"]["mean_tokens"] == 300.0
    assert arms["cbm_plus_exact_read"]["duration_ms_p95"] == 37.0
    assert arms["cbm_plus_exact_read"]["exact_read_run_count"] == 72
    assert report["performance"]["scenarios"]["noop_sync"]["runs"] == 3
    assert report["performance"]["scenarios"]["noop_sync"]["touched_files_max"] == 0


def test_report_is_deterministic_and_content_free():
    payload = _payload()
    first = evaluate_benchmark(payload)
    second = evaluate_benchmark(deepcopy(payload))

    assert first == second
    assert first["evidence_digest_sha256"] == second["evidence_digest_sha256"]
    encoded = json.dumps(first, sort_keys=True).lower()
    for forbidden in (
        '"prompt"',
        '"question"',
        '"source_text"',
        '"snippet"',
        '"model_output"',
        '"absolute_path"',
        '"host_path"',
    ):
        assert forbidden not in encoded
    assert first["safety"] == {
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
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"prompt": "private"}),
        lambda payload: payload.update({"engine_invoked": True}),
        lambda payload: payload.update({"network_calls": 1}),
        lambda payload: payload["quality_receipts"].pop(),
        lambda payload: payload["performance_receipts"].pop(),
        lambda payload: payload["quality_receipts"].append(
            deepcopy(payload["quality_receipts"][0])
        ),
    ],
)
def test_private_unsafe_incomplete_or_duplicate_inputs_fail_closed(mutation):
    payload = _payload()
    mutation(payload)

    with pytest.raises(BenchmarkContractError):
        evaluate_benchmark(payload)


def test_hybrid_and_nonhybrid_exact_reader_claims_are_enforced():
    payload = _payload()
    hybrid = next(row for row in payload["quality_receipts"] if row["arm"] == "cbm_plus_exact_read")
    hybrid["exact_read_used"] = False
    with pytest.raises(BenchmarkContractError, match="hybrid arm"):
        evaluate_benchmark(payload)

    payload = _payload()
    baseline = next(row for row in payload["quality_receipts"] if row["arm"] == "grep_read")
    baseline["exact_read_used"] = True
    with pytest.raises(BenchmarkContractError, match="non-hybrid"):
        evaluate_benchmark(payload)


def test_budget_and_fingerprint_contracts_reject_bias():
    payload = _payload()
    payload["quality_receipts"][0]["calls"] = payload["settings"]["tool_budget"] + 1
    with pytest.raises(BenchmarkContractError, match="bounded integer"):
        evaluate_benchmark(payload)

    payload = _payload()
    payload["settings"]["prompt_fingerprint"] = "not-a-sha"
    with pytest.raises(BenchmarkContractError, match="SHA-256"):
        evaluate_benchmark(payload)


def test_cli_writes_and_checks_canonical_report(tmp_path: Path):
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "report.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")

    assert main(["--input", str(input_path), "--output", str(output_path)]) == 0
    assert main(
        ["--input", str(input_path), "--output", str(output_path), "--check"]
    ) == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schema"] == "odysseus.code_intelligence.evaluation_report.v1"
    assert report["quality"]["receipt_count"] == 216
    assert report["performance"]["receipt_count"] == 27


def test_cli_returns_two_for_invalid_input(tmp_path: Path):
    input_path = tmp_path / "invalid.json"
    input_path.write_text('{"schema":"wrong"}', encoding="utf-8")

    assert main(["--input", str(input_path)]) == 2

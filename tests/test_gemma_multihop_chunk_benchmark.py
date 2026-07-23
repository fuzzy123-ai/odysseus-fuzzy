import asyncio
import json

from src.gemma_multihop_chunk_benchmark import (
    build_adversarial_chunk_corpus,
    build_synthetic_chunk_corpus,
    default_multihop_cases,
    deterministic_multihop_fixture_call,
    report_to_json,
    retrieve_multihop_chunks,
    run_multihop_benchmark,
)


def test_multihop_retrieval_finds_linked_runtime_memory_chunks_without_legacy():
    corpus = build_synthetic_chunk_corpus()
    case = default_multihop_cases()[0]
    retrieval = retrieve_multihop_chunks(case.query, corpus, budget=6)
    selected = {chunk.chunk_id for chunk in retrieval.selected_chunks}

    assert {"ops_runtime_001", "ops_memory_002"}.issubset(selected)
    assert "legacy_model_099" not in selected
    assert len(selected) <= 6


def test_multihop_retrieval_budget_four_has_no_distractors_for_default_cases():
    corpus = build_synthetic_chunk_corpus()

    for case in default_multihop_cases():
        retrieval = retrieve_multihop_chunks(case.query, corpus, budget=4)
        selected = {chunk.chunk_id for chunk in retrieval.selected_chunks}

        assert set(case.required_chunk_ids).issubset(selected), case.case_id
        assert not any(chunk_id.startswith("distractor_") for chunk_id in selected), case.case_id
        assert not any(chunk_id in selected for chunk_id in case.forbidden_chunk_ids), case.case_id
        assert len(selected) <= 4


def test_adversarial_retrieval_budget_sweep_keeps_precision():
    corpus = build_adversarial_chunk_corpus()

    for budget in (2, 3, 4, 6):
        for case in default_multihop_cases():
            retrieval = retrieve_multihop_chunks(case.query, corpus, budget=budget)
            selected = {chunk.chunk_id for chunk in retrieval.selected_chunks}

            assert set(case.required_chunk_ids).issubset(selected), (case.case_id, budget, selected)
            assert not any(chunk_id.startswith("distractor_") for chunk_id in selected), (
                case.case_id,
                budget,
                selected,
            )
            assert not any(chunk_id.startswith("legacy_") for chunk_id in selected), (
                case.case_id,
                budget,
                selected,
            )
            assert len(selected) <= budget


def test_deterministic_multihop_benchmark_passes_without_raw_chunk_text():
    report = asyncio.run(
        run_multihop_benchmark(
            model="gemma3:4b",
            provider="local_ollama",
            call_model=deterministic_multihop_fixture_call,
        )
    )
    payload = report.to_redacted_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "passed"
    assert payload["score"] == 100.0
    assert payload["corpus_chunk_count"] >= 80
    assert payload["metrics"]["retrieval_pass_rate"] == 100.0
    assert payload["metrics"]["retrieval_precision_pass_rate"] == 100.0
    assert payload["metrics"]["avg_budget_waste_rate"] == 0.0
    assert payload["metrics"]["evidence_pass_rate"] == 100.0
    assert payload["metrics"]["policy_pass_rate"] == 100.0
    assert "rootless Podman pods" not in encoded
    assert "redacted invoice-like document" not in encoded
    assert "raw_prompt" not in encoded
    assert "raw_output" not in encoded


def test_deterministic_multihop_budget_four_passes_precision_gates():
    report = asyncio.run(
        run_multihop_benchmark(
            model="gemma3:4b",
            provider="local_ollama",
            call_model=deterministic_multihop_fixture_call,
            retrieval_budget=4,
        )
    )
    payload = report.to_redacted_dict()

    assert payload["status"] == "passed"
    assert payload["metrics"]["retrieval_precision_pass_rate"] == 100.0
    assert all(case["retrieval_irrelevant_selected_count"] == 0 for case in payload["cases"])
    assert all(case["retrieval_budget_waste_rate"] == 0.0 for case in payload["cases"])


def test_adversarial_benchmark_budget_four_passes_precision_gates():
    report = asyncio.run(
        run_multihop_benchmark(
            model="gemma3:4b",
            provider="local_ollama",
            call_model=deterministic_multihop_fixture_call,
            corpus=build_adversarial_chunk_corpus(),
            retrieval_budget=4,
        )
    )
    payload = report.to_redacted_dict()

    assert payload["status"] == "passed"
    assert payload["corpus_chunk_count"] > 80
    assert payload["metrics"]["retrieval_precision_pass_rate"] == 100.0
    assert payload["metrics"]["avg_budget_waste_rate"] == 0.0


def test_multihop_benchmark_fails_when_retrieval_budget_too_small():
    report = asyncio.run(
        run_multihop_benchmark(
            model="gemma3:4b",
            provider="local_ollama",
            call_model=deterministic_multihop_fixture_call,
            retrieval_budget=1,
        )
    )

    assert report.status == "failed"
    assert any("required_chunks_not_retrieved" in case.failure_reasons for case in report.cases)


def test_report_json_is_redacted_and_parseable():
    report = asyncio.run(
        run_multihop_benchmark(
            model="gemma3:4b",
            provider="local_ollama",
            call_model=deterministic_multihop_fixture_call,
        )
    )

    payload = json.loads(report_to_json(report))
    assert payload["schema"] == "odysseus.gemma_multihop_chunk_benchmark.v1"
    assert payload["cases"][0]["retrieval"]["selected_chunk_hashes"]

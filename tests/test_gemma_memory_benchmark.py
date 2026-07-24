import asyncio
import json

from src.gemma_memory_benchmark import (
    default_benchmark_cases,
    deterministic_fixture_call,
    normalize_model_triage,
    parse_model_json,
    report_to_json,
    run_benchmark,
)


def test_deterministic_benchmark_passes_without_raw_material():
    report = asyncio.run(
        run_benchmark(
            model="gemma4:e4b",
            provider="local_ollama",
            call_model=deterministic_fixture_call,
        )
    )
    payload = report.to_redacted_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "passed"
    assert payload["score"] == 100.0
    assert payload["metrics"]["json_valid_rate"] == 100.0
    assert payload["metrics"]["retry_count_total"] == 0
    assert payload["metrics"]["local_only_gate_pass_rate"] == 100.0
    assert payload["metrics"]["avg_chunk_score"] >= 85.0
    assert payload["cases"][0]["retry_count"] == 0
    assert payload["cases"][0]["chunk_score"] >= 85.0
    assert "redacted_context" not in encoded
    assert "raw_output" not in encoded
    assert "raw_prompt" not in encoded
    assert "The operator states" not in encoded
    assert "A redacted invoice-like document" not in encoded


def test_dsgvo_case_requires_local_only_and_review():
    report = asyncio.run(
        run_benchmark(
            model="gemma4:e4b",
            provider="local_ollama",
            call_model=deterministic_fixture_call,
            cases=(default_benchmark_cases()[1],),
        )
    )
    case = report.cases[0].to_redacted_dict()

    assert case["case_id"] == "dsgvo_sensitive_invoice"
    assert case["local_only_pass"] is True
    assert case["parsed_summary"]["classification"] == "sensitive"
    assert case["parsed_summary"]["api_escalation_allowed"] is False
    assert case["pipeline"]["intent_status"] == "review"


def test_podman_reference_is_scored_as_project_memory():
    async def weak_podman_call(prompt: str) -> str:
        return json.dumps(
            {
                "classification": "private",
                "document_type": "reference",
                "should_remember": True,
                "memory_write_intent_status": "ready",
                "local_only_required": False,
                "api_escalation_allowed": True,
                "raptor_target": "project_decisions",
                "recall_answer": "Odysseus server operations use Podman instead of Docker.",
                "tags": ["odysseus", "podman", "server"],
            }
        )

    report = asyncio.run(
        run_benchmark(
            model="gemma4:e4b",
            provider="local_ollama",
            call_model=weak_podman_call,
            cases=(default_benchmark_cases()[0],),
        )
    )
    case = report.cases[0].to_redacted_dict()

    assert case["score"] == 100.0
    assert case["parsed_summary"]["document_type"] == "project"
    assert case["failure_reasons"] == ()


def test_empty_sensitive_invoice_recall_uses_redacted_fallback():
    async def empty_invoice_recall_call(prompt: str) -> str:
        return json.dumps(
            {
                "classification": "sensitive",
                "document_type": "invoice",
                "should_remember": True,
                "memory_write_intent_status": "review",
                "local_only_required": True,
                "api_escalation_allowed": False,
                "raptor_target": "review_queue",
                "recall_answer": "",
                "tags": ["invoice", "review", "dsgvo"],
            }
        )

    report = asyncio.run(
        run_benchmark(
            model="gemma4:e4b",
            provider="local_ollama",
            call_model=empty_invoice_recall_call,
            cases=(default_benchmark_cases()[1],),
        )
    )
    case = report.cases[0].to_redacted_dict()

    assert case["score"] == 100.0
    assert case["retrieval_pass"] is True
    assert case["parsed_summary"]["recall_answer_hash"] != (
        "sha256:e3b0c44298fc1c149afbf4c8996fb924"
        "27ae41e4649b934ca495991b7852b855"
    )
    assert case["failure_reasons"] == ()


def test_normalizer_corrects_overconservative_local_only_gate():
    case = default_benchmark_cases()[0]
    parsed = normalize_model_triage(
        case,
        {
            "classification": "private",
            "document_type": "project",
            "should_remember": True,
            "memory_write_intent_status": "ready",
            "local_only_required": True,
            "api_escalation_allowed": False,
            "raptor_target": "project_decisions",
            "recall_answer": "Odysseus server operations use Podman instead of Docker.",
            "tags": ["odysseus", "podman", "server"],
        },
    )

    assert parsed["local_only_required"] is False
    assert parsed["api_escalation_allowed"] is True
    assert parsed["memory_write_intent_status"] == "ready"


def test_normalizer_skips_transient_smalltalk_memory():
    case = default_benchmark_cases()[3]
    parsed = normalize_model_triage(
        case,
        {
            "classification": "public",
            "document_type": "transient",
            "should_remember": True,
            "memory_write_intent_status": "ready",
            "local_only_required": True,
            "api_escalation_allowed": False,
            "raptor_target": "conversation",
            "recall_answer": "Short friendly smalltalk.",
            "tags": ["smalltalk"],
        },
    )

    assert parsed["should_remember"] is False
    assert parsed["memory_write_intent_status"] == "skipped"
    assert parsed["local_only_required"] is False
    assert parsed["api_escalation_allowed"] is True


def test_invalid_json_fails_schema_without_leaking_output():
    async def bad_call(prompt: str) -> str:
        return "not-json with pretend chain of thought"

    report = asyncio.run(
        run_benchmark(
            model="gemma4:e4b",
            provider="local_ollama",
            call_model=bad_call,
            cases=(default_benchmark_cases()[0],),
        )
    )
    payload = report.to_redacted_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert report.status == "failed"
    assert payload["cases"][0]["schema_valid"] is False
    assert payload["metrics"]["json_valid_rate"] == 0.0
    assert payload["metrics"]["retry_count_total"] == 1
    assert payload["cases"][0]["retry_count"] == 1
    assert "not-json" not in encoded
    assert "chain of thought" not in encoded


def test_parse_model_json_accepts_fenced_json():
    parsed, error = parse_model_json(
        '```json\n{"classification":"private","tags":[]}\n```'
    )

    assert error is None
    assert parsed["classification"] == "private"

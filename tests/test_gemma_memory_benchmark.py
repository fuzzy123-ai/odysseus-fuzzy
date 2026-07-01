import asyncio
import json

from src.gemma_memory_benchmark import (
    default_benchmark_cases,
    deterministic_fixture_call,
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
    assert "not-json" not in encoded
    assert "chain of thought" not in encoded


def test_parse_model_json_accepts_fenced_json():
    parsed, error = parse_model_json(
        '```json\n{"classification":"private","tags":[]}\n```'
    )

    assert error is None
    assert parsed["classification"] == "private"

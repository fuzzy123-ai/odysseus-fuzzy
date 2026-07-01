import asyncio
import json

from src.gemma_maintenance_comparison import (
    comparison_report_to_json,
    run_maintenance_comparison,
    weak_deepseek_fixture_call,
)
from src.gemma_memory_benchmark import deterministic_fixture_call


def test_deterministic_comparison_passes_without_raw_material():
    report = asyncio.run(run_maintenance_comparison())
    payload = report.to_redacted_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == "odysseus.gemma_deepseek_maintenance_comparison.v1"
    assert payload["status"] == "passed"
    assert payload["winner"] == "tie"
    assert payload["models"]["gemma"]["live"] is False
    assert payload["models"]["deepseek"]["live"] is False
    assert payload["metrics"]["gemma"]["json_valid_rate"] == 100.0
    assert payload["metrics"]["deepseek"]["local_only_gate_pass_rate"] == 100.0
    assert payload["raw_prompts_persisted"] is False
    assert payload["raw_outputs_persisted"] is False
    assert "redacted_context" not in encoded
    assert "The operator states" not in encoded
    assert "A redacted invoice-like document" not in encoded


def test_comparison_flags_weaker_deepseek_fixture_for_review():
    report = asyncio.run(
        run_maintenance_comparison(
            gemma_call_model=deterministic_fixture_call,
            deepseek_call_model=weak_deepseek_fixture_call,
        )
    )
    payload = report.to_redacted_dict()

    assert payload["status"] == "review"
    assert payload["winner"] == "gemma"
    assert payload["score_delta_deepseek_minus_gemma"] < 0
    assert any(delta["winner"] == "gemma" for delta in payload["case_deltas"])


def test_comparison_report_json_is_redacted():
    report = asyncio.run(run_maintenance_comparison())
    encoded = comparison_report_to_json(report)
    payload = json.loads(encoded)

    assert payload["private_content_persisted"] is False
    assert payload["raw_prompts_persisted"] is False
    assert payload["raw_outputs_persisted"] is False
    assert "redacted_context" not in encoded
    assert "The operator states" not in encoded

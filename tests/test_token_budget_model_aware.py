import builtins
import json
import socket
from pathlib import Path

from src.context_budget import DEFAULT_HARD_MAX
from src.model_context import estimate_tokens
from src.token_budget import TokenBudget, count_text_tokens, split_budget
from src.token_estimator import (
    MODEL_ESTIMATOR_ROUTES,
    MODEL_HINT_PROPAGATION_GATE,
    PROVIDER_TOKENIZER_ASSET_GATE,
    estimate_text_tokens,
)


FIXTURE = Path(__file__).parent / "fixtures" / "token_budget" / "synthetic_offline_corpus.json"


def _corpus():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["samples"]


def test_model_hint_selects_ordered_exact_and_assetless_fallback_routes():
    text = "abcdefghij"

    exact = estimate_text_tokens(text, "odysseus-utf8-byte-v1")
    openai = estimate_text_tokens(text, "openai/gpt-4o")
    local = estimate_text_tokens(text, "local-gemma-3")
    unknown = estimate_text_tokens(text, "unknown-provider/model-v1")

    assert [route.route_id for route in MODEL_ESTIMATOR_ROUTES] == [
        "explicit_utf8_byte_v1",
        "openai_family_no_asset_fallback",
        "anthropic_family_no_asset_fallback",
        "sentencepiece_family_no_asset_fallback",
    ]
    assert exact.exact_or_fallback == "exact"
    assert exact.adapter_id == "utf8_byte_exact"
    assert openai.exact_or_fallback == "fallback"
    assert openai.adapter_id == "openai_family_conservative_utf8_upper_bound"
    assert local.adapter_id == "sentencepiece_family_conservative_utf8_upper_bound"
    assert unknown.exact_or_fallback == "fallback"
    assert unknown.adapter_id == "unknown_model_conservative_utf8_upper_bound"
    assert exact.count == openai.count == local.count == unknown.count == 10
    assert openai.confidence == local.confidence == unknown.confidence == "conservative_upper_bound"


def test_exact_adapter_matches_frozen_synthetic_ground_truth():
    for sample in _corpus():
        result = estimate_text_tokens(sample["text"], "odysseus-utf8-byte-v1")
        assert result.exact is True
        assert result.count == sample["utf8_byte_upper_bound_units"], sample["kind"]


def test_assetless_known_and_unknown_routes_never_underflow_utf8_upper_bound():
    model_hints = (
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4",
        "local-gemma-3",
        "unregistered/model",
    )
    for sample in _corpus():
        expected_upper_bound = sample["utf8_byte_upper_bound_units"]
        assert expected_upper_bound == len(sample["text"].encode("utf-8"))
        for model_hint in model_hints:
            result = estimate_text_tokens(sample["text"], model_hint)
            underflow = max(0, expected_upper_bound - result.count)
            assert result.fallback is True
            assert result.confidence == "conservative_upper_bound"
            assert result.count >= expected_upper_bound, (sample["kind"], model_hint)
            assert underflow == 0


def test_missing_optional_tokenizer_never_imports_or_uses_network(monkeypatch):
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.partition(".")[0] in {"tiktoken", "tokenizers", "transformers"}:
            raise AssertionError(f"optional tokenizer import attempted: {name}")
        return real_import(name, *args, **kwargs)

    def blocked_socket(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(socket, "socket", blocked_socket)

    first = estimate_text_tokens("synthetic Ω code()", "missing/private-model")
    second = estimate_text_tokens("synthetic Ω code()", "missing/private-model")

    assert first == second
    assert first.fallback is True
    assert first.confidence == "conservative_upper_bound"


def test_legacy_calls_remain_compatible_but_model_aware_message_count_routes():
    text = "abcdefghij"
    assert count_text_tokens(text) == 3
    assert estimate_tokens([{"role": "user", "content": text}]) == 7

    model_count = count_text_tokens(text, model_hint="unregistered/model")
    assert model_count == 10
    assert estimate_tokens(
        [{"role": "user", "content": text}],
        model_hint="unregistered/model",
    ) == 4 + model_count


def test_split_budget_discloses_selected_adapter_without_changing_limits():
    budget = split_budget(32_000, 1_000, model_hint="openai/gpt-4o")
    direct = TokenBudget(32_000, 1_000, model_hint="openai/gpt-4o")

    assert direct.to_dict() == budget.to_dict()
    assert budget.max_tokens == 32_000
    assert budget.overlap_tokens == 1_000
    assert budget.to_dict()["estimator"] == "openai_family_conservative_utf8_upper_bound"
    assert budget.to_dict()["estimator_revision"] == "1"
    assert budget.to_dict()["estimator_mode"] == "fallback"
    assert budget.to_dict()["estimator_confidence"] == "conservative_upper_bound"
    assert DEFAULT_HARD_MAX == 32_000


def test_provider_asset_gate_remains_open_while_model_hint_propagation_is_green():
    assert PROVIDER_TOKENIZER_ASSET_GATE["state"] == "unresolved_no_bundled_provider_tokenizer_assets"
    assert PROVIDER_TOKENIZER_ASSET_GATE["safe_default"] == "conservative_utf8_byte_upper_bound"
    assert MODEL_HINT_PROPAGATION_GATE["state"] == "satisfied_focused_and_compatibility_green"
    assert MODEL_HINT_PROPAGATION_GATE["compatible_api_hook"].endswith("model_hint=None)")
    required = "\n".join(MODEL_HINT_PROPAGATION_GATE["required_callsites"])
    assert "src/context_compactor.py" in required
    assert "src/agent_loop.py" in required
    assert "routes/chat_routes.py" in required
    assert MODEL_HINT_PROPAGATION_GATE["required_tests"] == (
        "tests/test_context_compactor_model_hint_propagation.py",
        "tests/test_agent_loop_model_hint_budget.py",
        "tests/test_chat_routes_model_hint_usage.py",
        "tests/test_context_orchestrator_model_hint_budget.py",
        "tests/test_history_routes_model_hint_usage.py",
        "tests/test_chat_helpers_model_hint_budget.py",
        "tests/test_session_manager_model_hint_budget.py",
        "tests/test_context_provider_model_hint_budget.py",
        "plugins/obsidian/tests/test_context_provider_model_hint_budget.py",
    )
    assert MODEL_HINT_PROPAGATION_GATE["evidence"] == {
        "focused": "19 passed",
        "compatibility": "264 passed",
        "no_hint_allowlist_count": 3,
        "network_or_provider_calls": False,
    }

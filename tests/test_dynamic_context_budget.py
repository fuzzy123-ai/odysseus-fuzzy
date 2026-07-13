"""Focused regressions for model/provider-aware agent input budgets."""

import asyncio

import pytest

from src.context_budget import DEFAULT_BUDGET, DEFAULT_HARD_MAX, resolve_input_token_budget
from src.model_context import _lookup_known


DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-pro"


def _resolve(*, overrides=None, configured=900, context_length=1_000_000,
             explicit=True, endpoint_url=DEEPSEEK_URL, model=MODEL,
             output_reserve=0):
    return resolve_input_token_budget(
        configured,
        context_length,
        explicit,
        endpoint_url=endpoint_url,
        model=model,
        overrides=overrides,
        output_reserve=output_reserve,
        hard_max=DEFAULT_HARD_MAX,
    )


def test_budget_precedence_model_provider_legacy_then_auto():
    overrides = {
        "providers": {"deepseek": 64_000},
        "models": {" DeepSeek-V4-Pro ": 128_000},
    }

    model = _resolve(overrides=overrides, model=" DEEPSEEK-V4-PRO ")
    assert (model.input_budget, model.source, model.provider) == (
        128_000, "model_override", "deepseek",
    )

    provider = _resolve(overrides={"providers": {"deepseek": 64_000}, "models": {}})
    assert (provider.input_budget, provider.source) == (64_000, "provider_override")

    legacy = _resolve(overrides={"providers": {}, "models": {}})
    assert (legacy.input_budget, legacy.source) == (900, "legacy_explicit")

    auto = _resolve(
        overrides={}, configured=DEFAULT_BUDGET, explicit=False,
    )
    assert (auto.input_budget, auto.source) == (DEFAULT_HARD_MAX, "auto_hard_max")


@pytest.mark.parametrize("overrides", [
    None,
    [],
    "not-an-object",
    {"providers": [], "models": []},
    {"providers": "deepseek", "models": "deepseek-v4-pro"},
])
def test_non_object_override_shapes_fall_back_to_legacy(overrides):
    decision = _resolve(overrides=overrides)
    assert (decision.input_budget, decision.source) == (900, "legacy_explicit")


@pytest.mark.parametrize("invalid_cap", [True, "128000", 128000.0, 0, -1])
def test_invalid_model_caps_fall_through_to_valid_provider(invalid_cap):
    decision = _resolve(overrides={
        "providers": {"deepseek": 64_000},
        "models": {MODEL: invalid_cap},
    })
    assert (decision.input_budget, decision.source) == (64_000, "provider_override")


@pytest.mark.parametrize("invalid_cap", [True, "64000", 64000.0, 0, -1])
def test_invalid_provider_caps_fall_through_to_legacy(invalid_cap):
    decision = _resolve(overrides={
        "providers": {"deepseek": invalid_cap},
        "models": {},
    })
    assert (decision.input_budget, decision.source) == (900, "legacy_explicit")


def test_empty_override_keys_are_ignored():
    decision = _resolve(overrides={
        "providers": {"": 77_000},
        "models": {"  ": 88_000},
    })
    assert (decision.input_budget, decision.source) == (900, "legacy_explicit")


def test_unknown_model_and_window_stay_conservative():
    decision = _resolve(
        configured=DEFAULT_BUDGET,
        context_length=0,
        explicit=False,
        endpoint_url="https://proxy.example.invalid/v1/chat/completions",
        model="unknown-model",
        overrides={},
    )
    assert decision.input_budget == DEFAULT_BUDGET
    assert decision.source == "auto_unknown_window"
    assert decision.provider == "unknown"


def test_production_900_cap_does_not_subtract_output_reserve_twice():
    decision = _resolve(output_reserve=1024)
    assert decision.input_budget == 900
    assert decision.output_reserve == 1024
    assert decision.source == "legacy_explicit"


def test_agent_loop_passes_resolved_input_cap_to_trimmer_without_second_reserve(monkeypatch):
    """Integration guard for the exact call boundary that produced 900-1024."""
    import src.agent_loop as agent_loop
    import src.context_compactor as context_compactor
    import src.model_context as model_context

    captured = {}

    def fake_get_setting(key, default=None):
        values = {
            "agent_input_token_budget": 900,
            "agent_input_token_hard_max": DEFAULT_HARD_MAX,
            "agent_input_token_budget_overrides": {"providers": {}, "models": {}},
        }
        return values.get(key, default)

    def fake_trim(messages, context_length, reserve_tokens=512):
        captured["input_budget"] = context_length
        captured["reserve_tokens"] = reserve_tokens
        return messages

    async def fake_stream(*args, **kwargs):
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_setting", fake_get_setting)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(
        agent_loop,
        "_build_system_prompt",
        lambda messages, *args, **kwargs: (list(messages), []),
    )
    monkeypatch.setattr(
        agent_loop,
        "_inject_context_provider_messages",
        lambda messages, **kwargs: messages,
    )
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    # Some legacy test modules install import-time MagicMock stubs for
    # src.agent_tools during collection. Keep this integration guard hermetic
    # when it is run as part of the broader settings/tool suite.
    monkeypatch.setattr(
        agent_loop,
        "strip_tool_blocks",
        lambda text, **kwargs: str(text or ""),
    )
    monkeypatch.setattr(model_context, "budget_context_for_model", lambda *args, **kwargs: 1_000_000)
    monkeypatch.setattr(context_compactor, "trim_for_context", fake_trim)

    async def collect():
        return [chunk async for chunk in agent_loop.stream_agent_loop(
            DEEPSEEK_URL,
            MODEL,
            [{"role": "user", "content": "follow up"}],
            max_tokens=1024,
            max_rounds=1,
            relevant_tools=set(),
        )]

    asyncio.run(collect())
    assert captured == {"input_budget": 900, "reserve_tokens": 0}


def test_cap_at_context_window_applies_reserve_exactly_once():
    decision = _resolve(
        configured=1_000_000,
        context_length=1_000_000,
        output_reserve=1024,
    )
    assert decision.input_budget == 998_976


def test_reserve_larger_than_context_window_never_creates_negative_budget():
    decision = _resolve(configured=900, context_length=512, output_reserve=1024)
    assert decision.input_budget == 1
    assert decision.input_budget >= 0


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "deepseek-v4-pro"])
def test_deepseek_v4_models_have_one_million_token_context(model):
    assert _lookup_known(model) == 1_000_000

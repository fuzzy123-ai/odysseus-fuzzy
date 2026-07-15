from __future__ import annotations

from src.context_orchestrator import final_trim_guard, preload_provider_context
from src.model_context import estimate_tokens
from src.plugin_system import register_context_provider, unregister_context_provider


MODEL = "odysseus-utf8-byte-v1"


def test_final_trim_guard_uses_selected_model_budget() -> None:
    messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "old-" + ("界" * 100)},
        {"role": "user", "content": "current"},
    ]

    trimmed = final_trim_guard(messages, max_tokens=64, model_hint=MODEL)

    assert estimate_tokens(trimmed, model_hint=MODEL) <= 64
    assert trimmed[-1]["content"] == "current"
    assert all("old-" not in str(message.get("content")) for message in trimmed)


def test_opted_in_provider_receives_model_hint_once() -> None:
    calls: list[dict] = []

    def retrieve(**kwargs):
        calls.append(kwargs)
        return {"snippets": []}

    register_context_provider({
        "id": "test.model-aware",
        "label": "model-aware",
        "priority": 1,
        "capabilities": ["sar-model-hint-budget"],
        "retrieve": retrieve,
        "accepts_model_hint": True,
    })
    try:
        payloads, warnings = preload_provider_context(
            owner="alice",
            query="q",
            budget_tokens=100,
            mode="sar-model-hint-budget",
            model_hint=MODEL,
        )
    finally:
        unregister_context_provider("test.model-aware")

    assert warnings == []
    assert len(payloads) == 1
    assert len(calls) == 1
    assert calls[0]["model_hint"] == MODEL
    assert list(calls[0]).count("model_hint") == 1

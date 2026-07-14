from __future__ import annotations

from src.context_orchestrator import preload_provider_context
from src.plugin_system import register_context_provider, unregister_context_provider


MODEL = "odysseus-utf8-byte-v1"


def test_legacy_four_argument_provider_receives_no_new_keyword() -> None:
    calls: list[tuple] = []

    def retrieve(owner, query, budget, mode):
        calls.append((owner, query, budget, mode))
        return {"snippets": []}

    register_context_provider({
        "id": "test.legacy-four-args",
        "label": "legacy",
        "priority": 1,
        "capabilities": ["sar-legacy-provider"],
        "retrieve": retrieve,
    })
    try:
        payloads, warnings = preload_provider_context(
            owner="alice",
            query="q",
            budget_tokens=120,
            mode="sar-legacy-provider",
            model_hint=MODEL,
        )
    finally:
        unregister_context_provider("test.legacy-four-args")

    assert warnings == []
    assert len(payloads) == 1
    assert calls == [("alice", "q", 120, "sar-legacy-provider")]


def test_opted_in_provider_internal_type_error_is_not_retried() -> None:
    call_count = 0

    def retrieve(owner, query, budget, mode, model_hint=None):
        nonlocal call_count
        call_count += 1
        raise TypeError("provider-internal-error")

    register_context_provider({
        "id": "test.type-error-once",
        "label": "type-error",
        "priority": 1,
        "capabilities": ["sar-type-error-provider"],
        "retrieve": retrieve,
        "accepts_model_hint": True,
    })
    try:
        payloads, warnings = preload_provider_context(
            owner="alice",
            query="q",
            budget_tokens=120,
            mode="sar-type-error-provider",
            model_hint=MODEL,
        )
    finally:
        unregister_context_provider("test.type-error-once")

    assert payloads == []
    assert call_count == 1
    assert warnings == ["context provider test.type-error-once failed: provider-internal-error"]

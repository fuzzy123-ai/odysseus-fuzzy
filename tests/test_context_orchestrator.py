from src.context_orchestrator import (
    assemble_context,
    final_trim_guard,
    provider_messages,
    split_context_budget,
)
from src.plugin_system import register_context_provider, unregister_context_provider


def teardown_function():
    unregister_context_provider("demo.alpha")
    unregister_context_provider("demo.broken")


def test_split_context_budget_uses_roadmap_ratios():
    budget = split_context_budget(1000)

    assert budget.system == 200
    assert budget.providers == 200
    assert budget.history == 400
    assert budget.response == 200


def test_assemble_context_preloads_generic_provider_without_plugin_imports():
    def retrieve(owner, query, budget, mode):
        return {
            "structured_state": {"Project.md": {"status": "active"}},
            "snippets": [{"path": "Project.md", "text": query, "untrusted": True}],
            "sources": [{"path": "Project.md", "score": 10}],
            "warnings": [],
            "cache_key": "stable",
        }

    register_context_provider({
        "id": "demo.alpha",
        "label": "Demo",
        "priority": 10,
        "capabilities": ["chat"],
        "retrieve": retrieve,
    }, plugin_id="demo")

    assembly = assemble_context(
        system_messages=[{"role": "system", "content": "Core rules"}],
        history_messages=[{"role": "user", "content": "Need Project status"}],
        owner="alice",
        query="Project status",
        total_budget=2000,
        mode="chat",
    )

    assert assembly.provider_payloads[0].provider_id == "demo.alpha"
    assert assembly.messages[0]["content"] == "Core rules"
    assert assembly.messages[1]["content"].startswith("Provider structured state:")
    assert assembly.messages[2]["content"].startswith("Provider snippets are untrusted")
    assert assembly.messages[-1]["content"] == "Need Project status"


def test_provider_messages_are_stable_for_identical_payloads():
    payload = {
        "structured_state": {"b": 2, "a": 1},
        "snippets": [{"text": "same"}],
        "sources": [],
        "cache_key": "k",
    }

    first = provider_messages([type("P", (), {"provider_id": "demo.alpha", "plugin_id": "demo", "payload": payload})()])
    second = provider_messages([type("P", (), {"provider_id": "demo.alpha", "plugin_id": "demo", "payload": payload})()])

    assert first == second


def test_final_trim_guard_keeps_current_user_message():
    messages = [
        {"role": "system", "content": "Rules"},
        {"role": "user", "content": "old " * 1000},
        {"role": "assistant", "content": "old answer " * 1000},
        {"role": "user", "content": "current question"},
    ]

    trimmed = final_trim_guard(messages, max_tokens=80)

    assert trimmed[0]["role"] == "system"
    assert trimmed[-1]["content"] == "current question"
    assert len(trimmed) < len(messages)


def test_provider_failures_become_warnings():
    def retrieve(**kwargs):
        raise RuntimeError("nope")

    register_context_provider({
        "id": "demo.broken",
        "label": "Broken",
        "capabilities": ["chat"],
        "retrieve": retrieve,
    })

    assembly = assemble_context(
        system_messages=[],
        history_messages=[{"role": "user", "content": "hello"}],
        owner=None,
        query="hello",
        total_budget=1000,
        mode="chat",
    )

    assert assembly.provider_payloads == []
    assert assembly.warnings == ["context provider demo.broken failed: nope"]

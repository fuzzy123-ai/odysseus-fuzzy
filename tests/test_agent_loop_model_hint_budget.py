from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "src" / "agent_loop.py"


def _calls(name: str) -> list[ast.Call]:
    tree = ast.parse(PATH.read_text(encoding="utf-8"), filename=str(PATH))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def _keyword_name(call: ast.Call, keyword: str) -> str:
    value = next(item.value for item in call.keywords if item.arg == keyword)
    assert isinstance(value, ast.Name)
    return value.id


def test_agent_loop_uses_selected_model_for_all_context_estimates() -> None:
    calls = _calls("estimate_tokens")

    assert len(calls) == 2
    assert {_keyword_name(call, "model_hint") for call in calls} == {"model"}


def test_agent_loop_passes_selected_model_to_trim_and_provider_budget() -> None:
    trim_calls = _calls("trim_for_context")
    provider_calls = _calls("_inject_context_provider_messages")

    assert len(trim_calls) == 1
    assert _keyword_name(trim_calls[0], "model_hint") == "model"
    assert len(provider_calls) == 1
    assert _keyword_name(provider_calls[0], "model_hint") == "model"

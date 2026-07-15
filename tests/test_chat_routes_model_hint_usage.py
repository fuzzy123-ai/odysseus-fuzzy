from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "routes" / "chat_routes.py"


def test_fallback_usage_model_priority_and_estimates_are_exact() -> None:
    tree = ast.parse(PATH.read_text(encoding="utf-8"), filename=str(PATH))
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_usage_model_hint" for target in node.targets)
    ]

    assert len(assignments) == 1
    value = assignments[0].value
    assert isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or)
    assert [item.id for item in value.values if isinstance(item, ast.Name)] == [
        "_actual_model",
        "_answered_by",
        "_requested_model",
    ]

    estimate_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "estimate_tokens"
    ]
    assert len(estimate_calls) == 2
    for call in estimate_calls:
        keyword = next(item for item in call.keywords if item.arg == "model_hint")
        assert isinstance(keyword.value, ast.Name)
        assert keyword.value.id == "_usage_model_hint"


def test_fallback_output_usage_is_not_character_division() -> None:
    source = PATH.read_text(encoding="utf-8")

    assert "_est_out = len(full_response) // 4" not in source
    assert '{"role": "assistant", "content": full_response}' in source

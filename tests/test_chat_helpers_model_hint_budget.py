from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "routes" / "chat_helpers.py"


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def _assert_session_model_keyword(call: ast.Call) -> None:
    keyword = next(item for item in call.keywords if item.arg == "model_hint")
    assert isinstance(keyword.value, ast.Attribute)
    assert isinstance(keyword.value.value, ast.Name)
    assert keyword.value.value.id == "sess"
    assert keyword.value.attr == "model"


def test_chat_context_uses_session_model_for_every_budget_boundary() -> None:
    tree = ast.parse(PATH.read_text(encoding="utf-8"), filename=str(PATH))

    for name, expected_count in (
        ("estimate_tokens", 1),
        ("get_recent_context_messages", 1),
        ("maybe_compact", 1),
        ("trim_for_context", 1),
    ):
        calls = _calls(tree, name)
        assert len(calls) == expected_count
        _assert_session_model_keyword(calls[0])

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "routes" / "history_routes.py"


def test_manual_history_compaction_uses_session_model_before_and_after() -> None:
    tree = ast.parse(PATH.read_text(encoding="utf-8"), filename=str(PATH))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "estimate_tokens"
    ]

    assert len(calls) == 2
    for call in calls:
        keyword = next(item for item in call.keywords if item.arg == "model_hint")
        assert isinstance(keyword.value, ast.Attribute)
        assert isinstance(keyword.value.value, ast.Name)
        assert keyword.value.value.id == "session"
        assert keyword.value.attr == "model"

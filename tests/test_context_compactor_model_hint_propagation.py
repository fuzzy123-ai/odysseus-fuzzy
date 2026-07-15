from __future__ import annotations

import ast
from pathlib import Path

from src.context_compactor import (
    _truncate_message_to_token_budget,
    latest_dialog_pair_preserved,
    trim_for_context,
)
from src.model_context import estimate_tokens


ROOT = Path(__file__).resolve().parents[1]
MODEL = "odysseus-utf8-byte-v1"
NO_HINT_ALLOWLIST = {
    "core/session_serialization.py::estimate_message_tokens_dict",
    "scripts/performance_baseline.py::profile_long_chat::estimate_probe",
    "scripts/performance_baseline.py::profile_long_chat::after_tokens",
}


def _production_python_paths() -> list[Path]:
    paths: list[Path] = []
    for root_name in ("core", "routes", "scripts", "src", "plugins"):
        for path in (ROOT / root_name).rglob("*.py"):
            relative = path.relative_to(ROOT)
            if "tests" not in relative.parts and "venv" not in relative.parts:
                paths.append(path)
    return paths


def _no_hint_label(path: Path, tree: ast.AST, call: ast.Call) -> str:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    functions: list[str] = []
    target_name = ""
    current: ast.AST | None = call
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(current.name)
        if not target_name and isinstance(current, (ast.Assign, ast.AnnAssign)):
            targets = current.targets if isinstance(current, ast.Assign) else [current.target]
            if len(targets) == 1 and isinstance(targets[0], ast.Name):
                target_name = targets[0].id
        current = parents.get(current)

    relative = path.relative_to(ROOT).as_posix()
    function_path = "::".join(reversed(functions))
    if relative == "scripts/performance_baseline.py":
        return f"{relative}::{function_path}::{target_name}"
    return f"{relative}::{function_path}"


def test_productive_estimate_tokens_calls_have_exact_model_hint_allowlist() -> None:
    observed: list[str] = []
    for path in _production_python_paths():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else ""
            )
            if name != "estimate_tokens":
                continue
            if any(keyword.arg == "model_hint" for keyword in node.keywords):
                continue
            observed.append(_no_hint_label(path, tree, node))

    assert set(observed) == NO_HINT_ALLOWLIST
    assert len(observed) == len(NO_HINT_ALLOWLIST)


def test_model_aware_trim_bounds_unicode_json_and_preserves_latest_dialog() -> None:
    original = [
        {"role": "system", "content": "stable-system"},
        {"role": "user", "content": '{"old":"' + ("ä" * 300) + '"}'},
        {"role": "assistant", "content": "assistant-head " + ("界" * 300) + " assistant-tail"},
        {"role": "user", "content": "current " + ("🙂" * 100)},
    ]

    trimmed = trim_for_context(
        original,
        context_length=260,
        reserve_tokens=0,
        model_hint=MODEL,
    )

    assert estimate_tokens(trimmed, model_hint=MODEL) <= 260
    assert latest_dialog_pair_preserved(original, trimmed, model_hint=MODEL) is True
    assert any(message.get("role") == "assistant" for message in trimmed)
    assert trimmed[-1]["role"] == "user"


def test_model_aware_tool_arguments_fit_without_breaking_pairing_fields() -> None:
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "create_document", "arguments": "界" * 5000},
            }
        ],
    }

    bounded = _truncate_message_to_token_budget(message, 160, model_hint=MODEL)

    assert estimate_tokens([bounded], model_hint=MODEL) <= 160
    assert bounded["tool_calls"][0]["id"] == "call-1"
    assert bounded["tool_calls"][0]["function"]["name"] == "create_document"


def test_explicit_none_preserves_legacy_trimming_behavior() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "x" * 5000},
    ]

    assert trim_for_context(messages, 400, reserve_tokens=0) == trim_for_context(
        messages,
        400,
        reserve_tokens=0,
        model_hint=None,
    )

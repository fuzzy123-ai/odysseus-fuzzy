"""Regression guards for agent-mode chat streaming tool-usage wiring."""

import ast
from pathlib import Path


_CHAT_ROUTES = Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"


def _find_async_function(root: ast.AST, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(root):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} async function not found")


def _assigned_names_before(func: ast.AsyncFunctionDef, line_number: int) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(func):
        if getattr(node, "lineno", line_number + 1) >= line_number:
            continue
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _calls_stream_agent_loop(func: ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "stream_agent_loop"
        for node in ast.walk(func)
    )


def test_agent_stream_passes_bound_tool_usage_values_to_agent_loop():
    """The streaming agent path must not pass an unbound instrumentation name."""

    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)
    chat_stream = _find_async_function(tree, "chat_stream")
    agent_stream_generators = [
        node
        for node in ast.walk(chat_stream)
        if node is not chat_stream
        and isinstance(node, ast.AsyncFunctionDef)
        and _calls_stream_agent_loop(node)
    ]
    assert len(agent_stream_generators) == 1
    agent_stream = agent_stream_generators[0]

    agent_loop_calls = [
        node
        for node in ast.walk(agent_stream)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "stream_agent_loop"
    ]
    assert len(agent_loop_calls) == 1

    call = agent_loop_calls[0]
    kwargs = {keyword.arg: keyword.value for keyword in call.keywords}
    for keyword_name in ("tool_usage_context", "tool_usage_instrumentation"):
        assert keyword_name in kwargs
        assert isinstance(kwargs[keyword_name], ast.Name)
        passed_name = kwargs[keyword_name].id
        assert passed_name in _assigned_names_before(agent_stream, call.lineno)

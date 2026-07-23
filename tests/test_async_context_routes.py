from __future__ import annotations

import ast
import asyncio
from pathlib import Path
import time

import pytest

from routes import chat_helpers
from src.model_context import ContextLengthSnapshot


ROUTE_PATHS = (
    Path("routes/chat_helpers.py"),
    Path("routes/history_routes.py"),
    Path("routes/session_routes.py"),
)


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def test_route_census_contains_no_synchronous_context_probe() -> None:
    sync_calls = []
    for path in ROUTE_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in {
                "get_context_length",
                "get_context_length_known",
                "budget_context_for_model",
            }:
                sync_calls.append((str(path), node.lineno, _call_name(node)))

    assert sync_calls == []


def test_all_three_route_context_calls_are_explicitly_awaited() -> None:
    observed = []
    for path in ROUTE_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name not in {
                "resolve_context_budget_tokens_async",
                "resolve_request_context_snapshot",
                "get_context_length_async",
            }:
                continue
            observed.append((path.name, name))
            assert isinstance(parents.get(node), ast.Await), (
                path,
                node.lineno,
                name,
            )

    assert observed == [
        ("chat_helpers.py", "resolve_request_context_snapshot"),
        ("chat_helpers.py", "resolve_context_budget_tokens_async"),
        ("history_routes.py", "resolve_request_context_snapshot"),
        ("session_routes.py", "get_context_length_async"),
    ]


@pytest.mark.asyncio
async def test_slow_chat_context_probe_does_not_block_heartbeat(monkeypatch) -> None:
    timestamps = []
    running = True

    async def slow_snapshot(_endpoint_url, _model):
        await asyncio.sleep(0.15)
        return ContextLengthSnapshot(32_000, True, "probe", 1)

    async def heartbeat():
        while running:
            timestamps.append(time.perf_counter())
            await asyncio.sleep(0.005)

    monkeypatch.setattr(
        chat_helpers,
        "resolve_request_context_snapshot",
        slow_snapshot,
    )
    task = asyncio.create_task(heartbeat())
    try:
        value = await chat_helpers.resolve_context_budget_tokens_async(
            "https://context.example.test/v1",
            "foreign-model",
        )
    finally:
        running = False
        await task

    gaps = [right - left for left, right in zip(timestamps, timestamps[1:])]
    assert value == 32_000
    assert len(timestamps) >= 3
    assert gaps and max(gaps) < 0.1

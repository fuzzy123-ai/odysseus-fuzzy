import asyncio
import json
import sys
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.memory import MemoryManager
from src.memory_category_policy import (
    ALLOWED_MEMORY_CATEGORIES,
    MemoryCategoryPolicyError,
)
from src.request_models import MemoryAddRequest, MemoryUpdateRequest


def test_request_models_normalize_allowed_and_reject_todos():
    for category in ALLOWED_MEMORY_CATEGORIES:
        assert MemoryAddRequest(text="x", category=f" {category} ").category == category
        assert MemoryUpdateRequest(text="x", category=category).category == category
    assert MemoryUpdateRequest(text="x").category is None
    for category in ("task", "todo", "aufgabe", "todo-list", "unknown", "", None, 1):
        with pytest.raises(ValidationError):
            MemoryAddRequest(text="x", category=category)
    for category in ("task", "todo", "aufgaben", "todo_list", "unknown", "", 1):
        with pytest.raises(ValidationError):
            MemoryUpdateRequest(text="x", category=category)


def test_direct_manager_rejects_todo_but_legacy_task_remains_readable(tmp_path):
    manager = MemoryManager(str(tmp_path))
    assert manager.add_entry("fact")["category"] == "fact"
    for category in ("task", "todo", "unknown", "", None):
        with pytest.raises(MemoryCategoryPolicyError):
            manager.add_entry("x", category=category)
    Path(manager.memory_file).write_text(
        json.dumps([{"id": "legacy", "text": "x", "category": "task"}]),
        encoding="utf-8",
    )
    assert manager.load_all()[0]["category"] == "task"


def test_agent_writer_rejects_before_side_effects(monkeypatch):
    import src.ai_interaction as ai

    class Manager:
        def add_entry(self, *args, **kwargs):
            raise AssertionError("must not add")

        def load_all(self):
            raise AssertionError("must not load")

        def save(self, values):
            raise AssertionError("must not save")

    class Vector:
        healthy = True

        def add(self, *args):
            raise AssertionError("must not index")

    event_bus = types.SimpleNamespace(
        fire_event=lambda *args: (_ for _ in ()).throw(
            AssertionError("must not emit event")
        )
    )
    monkeypatch.setattr(ai, "_memory_manager", Manager())
    monkeypatch.setattr(ai, "_memory_vector", Vector())
    monkeypatch.setitem(sys.modules, "src.event_bus", event_bus)

    rejected_payloads = (
        ("add\nprivate\ntodo", "todo_storage_forbidden", "manage_todos"),
        ("add\nprivate\naufgabe", "todo_storage_forbidden", "manage_todos"),
        ("add\nprivate\n", "memory_category_invalid", None),
        (
            '{"action":"add","text":"private","category":null}',
            "memory_category_invalid",
            None,
        ),
        (
            '{"action":"add","text":"private","category":"unknown"}',
            "memory_category_invalid",
            None,
        ),
    )
    for payload, error_code, use_tool in rejected_payloads:
        result = asyncio.run(ai.do_manage_memory(payload))
        assert result["status"] == "rejected"
        assert result["error_code"] == error_code
        assert result["exit_code"] == 1
        assert result.get("use_tool") == use_tool
        assert "private" not in str(result)


def test_agent_writer_allows_normal_side_effects_and_schema_parity(monkeypatch):
    import src.ai_interaction as ai
    from src.tool_schema_definitions import FUNCTION_TOOL_SCHEMAS

    calls = []
    events = []

    monkeypatch.setitem(
        sys.modules,
        "src.event_bus",
        types.SimpleNamespace(fire_event=lambda *args: events.append(args)),
    )

    class Manager:
        def add_entry(self, text, **kwargs):
            calls.append(("add", kwargs))
            return {
                "id": "id",
                "text": text,
                "category": kwargs["category"],
            }

        def load_all(self):
            return []

        def save(self, values):
            calls.append(("save", values))

    class Vector:
        healthy = True

        def add(self, *args):
            calls.append(("vector", args))

    monkeypatch.setattr(ai, "_memory_manager", Manager())
    monkeypatch.setattr(ai, "_memory_vector", Vector())

    for category in ("fact", "preference"):
        assert asyncio.run(ai.do_manage_memory(f"add\nx\n{category}"))["action"] == "add"

    schema = next(
        item["function"]
        for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "manage_memory"
    )
    category_enum = schema["parameters"]["properties"]["category"]["enum"]
    assert tuple(category_enum) == ALLOWED_MEMORY_CATEGORIES
    assert [call[0] for call in calls].count("add") == 2
    assert [call[0] for call in calls].count("save") == 2
    assert [call[0] for call in calls].count("vector") == 2
    assert events == [("memory_added", None), ("memory_added", None)]


def test_route_update_rejects_before_load_and_preserves_legacy_on_omission():
    import routes.memory_routes as routes
    from fastapi import HTTPException

    class Manager:
        def load_all(self):
            raise AssertionError("must not load")

        def save(self, rows):
            raise AssertionError("must not save")

    manager = Manager()
    router = routes.setup_memory_routes(manager, object())
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/memory/{memory_id}" and "PUT" in route.methods
    )
    request = type("Request", (), {"state": type("State", (), {"current_user": "alice"})()})()

    rejected_categories = {
        "todo": {
            "error_code": "todo_storage_forbidden",
            "use_tool": "manage_todos",
        },
        "unknown": {"error_code": "memory_category_invalid"},
        "": {"error_code": "memory_category_invalid"},
    }
    for category, expected_detail in rejected_categories.items():
        with pytest.raises(HTTPException) as exc:
            endpoint(request, "id", "text", category)
        assert exc.value.status_code == 422
        assert exc.value.detail == expected_detail
        if category == "unknown":
            assert category not in str(exc.value.detail)

    legacy = [{"id": "id", "owner": "alice", "text": "old", "category": "task"}]
    calls = []
    manager.load_all = lambda: legacy
    manager.save = lambda rows: calls.append(rows)

    assert endpoint(request, "id", "text", None)["ok"] is True
    assert legacy[0]["category"] == "task"
    assert len(calls) == 1

    calls.clear()
    assert endpoint(request, "id", "text", " Preference ")["ok"] is True
    assert legacy[0]["category"] == "preference"
    assert len(calls) == 1

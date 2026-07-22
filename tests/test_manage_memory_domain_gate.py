from __future__ import annotations

import asyncio
import json

from src import ai_interaction
from src.memory import MemoryManager


def _run(payload, *, owner="alice"):
    return asyncio.run(
        ai_interaction.do_manage_memory(json.dumps(payload), owner=owner)
    )


def test_invalid_or_todo_memory_category_never_reaches_storage(monkeypatch, tmp_path):
    manager = MemoryManager(str(tmp_path))
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    todo = _run({"action": "add", "text": "Synthetic task", "category": "todo"})
    unknown = _run({"action": "add", "text": "Synthetic fact", "category": "other"})

    assert todo["status"] == "domain_mismatch"
    assert todo["redirect_tool"] == "manage_todos"
    assert unknown["status"] == "domain_mismatch"
    assert manager.load_all() == []


def test_explicit_todo_payload_in_fact_category_fails_closed(monkeypatch, tmp_path):
    manager = MemoryManager(str(tmp_path))
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    result = _run({
        "action": "add",
        "text": "Neue To-do: Aufgabe Alpha",
        "category": "fact",
    })

    assert result["exit_code"] == 1
    assert result["redirect_tool"] == "manage_todos"
    assert manager.load_all() == []


def test_normal_fact_and_preference_memory_writes_remain_valid(monkeypatch, tmp_path):
    manager = MemoryManager(str(tmp_path))
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    fact = _run({"action": "add", "text": "User lives in Lisbon", "category": "fact"})
    preference = _run({
        "action": "add",
        "text": "User prefers compact todo lists",
        "category": "preference",
    })

    assert fact["action"] == "add"
    assert preference["action"] == "add"
    assert {entry["category"] for entry in manager.load_all()} == {"fact", "preference"}


def test_invalid_memory_list_filter_fails_closed(monkeypatch, tmp_path):
    manager = MemoryManager(str(tmp_path))
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)

    result = _run({"action": "list", "category": "todo"})

    assert result["status"] == "domain_mismatch"
    assert result["exit_code"] == 1


def test_edit_cannot_turn_an_existing_memory_into_todo_state(monkeypatch, tmp_path):
    manager = MemoryManager(str(tmp_path))
    entry = manager.add_entry("User lives in Lisbon", category="fact", owner="alice")
    manager.save([entry])
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    result = _run({
        "action": "edit",
        "memory_id": entry["id"],
        "text": "Todo: prepare release",
    })

    assert result["status"] == "domain_mismatch"
    assert manager.load_all()[0]["text"] == "User lives in Lisbon"

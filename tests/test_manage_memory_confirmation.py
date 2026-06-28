import asyncio
import json

from src import ai_interaction


class FakeMemoryManager:
    def __init__(self):
        self.memories = [
            {
                "id": "aaaaaaaa-0000-0000-0000-000000000000",
                "owner": "alice",
                "text": "Alice likes green tea",
                "category": "preference",
            }
        ]
        self.saved = []

    def load_all(self):
        return list(self.memories)

    def save(self, memories):
        self.saved.append(list(memories))
        self.memories = list(memories)


class FakeVector:
    healthy = True

    def __init__(self):
        self.removed = []

    def remove(self, memory_id):
        self.removed.append(memory_id)


def test_manage_memory_delete_requires_confirmation(monkeypatch):
    manager = FakeMemoryManager()
    vector = FakeVector()
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", vector)

    result = asyncio.run(ai_interaction.do_manage_memory(
        json.dumps({"action": "delete", "memory_id": "aaaaaaaa"}),
        owner="alice",
    ))

    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert len(manager.memories) == 1
    assert manager.saved == []
    assert vector.removed == []


def test_manage_memory_delete_runs_after_confirmation(monkeypatch):
    manager = FakeMemoryManager()
    vector = FakeVector()
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", vector)

    result = asyncio.run(ai_interaction.do_manage_memory(
        json.dumps({"action": "delete", "memory_id": "aaaaaaaa", "confirmed": True}),
        owner="alice",
    ))

    assert result["action"] == "delete"
    assert manager.memories == []
    assert vector.removed == ["aaaaaaaa-0000-0000-0000-000000000000"]


def test_manage_memory_function_call_preserves_confirmation():
    from src.agent_tools import function_call_to_tool_block

    block = function_call_to_tool_block(
        "manage_memory",
        json.dumps({"action": "delete", "memory_id": "aaaaaaaa", "confirmed": True}),
    )

    assert block.tool_type == "manage_memory"
    assert json.loads(block.content)["confirmed"] is True

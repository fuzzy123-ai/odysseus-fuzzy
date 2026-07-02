import asyncio
import json

import src.ai_interaction as ai_interaction
from src.memory import MemoryManager


def test_manage_memory_add_returns_internal_reference(monkeypatch, tmp_path):
    manager = MemoryManager(str(tmp_path))
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    result = asyncio.run(
        ai_interaction.do_manage_memory(
            json.dumps({"action": "add", "text": "User prefers compact summaries", "category": "preference"}),
            owner="alice",
        )
    )

    assert result["action"] == "add"
    assert result["memory_id"]
    assert result["internal_ref"]["kind"] == "memory"
    assert result["internal_ref"]["entity_id"] == result["memory_id"]
    assert result["chat_href"] == result["internal_ref"]["chat_href"]
    assert result["markdown_link"] == f"[Memory oeffnen]({result['chat_href']})"
    assert result["markdown_link"] in result["results"]


def test_manage_memory_edit_returns_full_memory_reference(monkeypatch, tmp_path):
    manager = MemoryManager(str(tmp_path))
    entry = manager.add_entry("Old text", owner="alice")
    manager.save([entry])
    monkeypatch.setattr(ai_interaction, "_memory_manager", manager)
    monkeypatch.setattr(ai_interaction, "_memory_vector", None)

    result = asyncio.run(
        ai_interaction.do_manage_memory(
            json.dumps({"action": "edit", "memory_id": entry["id"][:8], "text": "Updated text"}),
            owner="alice",
        )
    )

    assert result["action"] == "edit"
    assert result["memory_id"] == entry["id"]
    assert result["internal_ref"]["uri"] == f"odysseus://memory/{entry['id']}"
    assert result["markdown_link"] in result["results"]

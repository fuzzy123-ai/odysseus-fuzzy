import asyncio
import json

import services.memory.skills as skills_mod
from src.tool_implementations import do_manage_skills


class FakeSkillsManager:
    instances = []

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.deleted = []
        FakeSkillsManager.instances.append(self)

    def delete_skill(self, name, owner=None):
        self.deleted.append((name, owner))
        return True


def _install_fake_manager(monkeypatch):
    FakeSkillsManager.instances = []
    monkeypatch.setattr(skills_mod, "SkillsManager", FakeSkillsManager)


def test_manage_skills_delete_requires_confirmation(monkeypatch):
    _install_fake_manager(monkeypatch)

    result = asyncio.run(do_manage_skills(
        json.dumps({"action": "delete", "name": "demo-skill"}),
        owner="alice",
    ))

    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert FakeSkillsManager.instances[0].deleted == []


def test_manage_skills_delete_runs_after_confirmation(monkeypatch):
    _install_fake_manager(monkeypatch)

    result = asyncio.run(do_manage_skills(
        json.dumps({"action": "delete", "name": "demo-skill", "confirmed": True}),
        owner="alice",
    ))

    assert result["results"] == "Deleted skill `demo-skill`."
    assert FakeSkillsManager.instances[0].deleted == [("demo-skill", "alice")]

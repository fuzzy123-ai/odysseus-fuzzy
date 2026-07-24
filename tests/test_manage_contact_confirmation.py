import asyncio
import importlib
import json

from src.tool_implementations import do_manage_contact


def test_manage_contact_delete_requires_confirmation(monkeypatch):
    calls = []
    contacts = importlib.import_module("routes.contacts_routes")

    def fake_delete(uid):
        calls.append(uid)
        return True

    monkeypatch.setattr(contacts, "_delete_contact", fake_delete)

    result = asyncio.run(do_manage_contact(
        json.dumps({"action": "delete", "uid": "contact-1"}),
        owner="alice",
    ))

    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert calls == []


def test_manage_contact_delete_runs_after_confirmation(monkeypatch):
    calls = []
    contacts = importlib.import_module("routes.contacts_routes")

    def fake_delete(uid):
        calls.append(uid)
        return True

    monkeypatch.setattr(contacts, "_delete_contact", fake_delete)

    result = asyncio.run(do_manage_contact(
        json.dumps({"action": "delete", "uid": "contact-1", "confirmed": True}),
        owner="alice",
    ))

    assert result["exit_code"] == 0
    assert result["output"] == "Contact deleted."
    assert calls == ["contact-1"]

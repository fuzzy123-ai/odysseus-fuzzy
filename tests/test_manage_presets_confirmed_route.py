import asyncio
import json

import httpx

from src.tool_implementations import do_manage_presets


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = json.dumps(data)

    def json(self):
        return self._data


class _FakeAsyncClient:
    calls = []

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/templates"):
            return _FakeResponse([{"id": "tmpl1", "name": "Helper"}])
        if url.endswith("/groups"):
            return _FakeResponse({"groups": [{"name": "Pair"}]})
        return _FakeResponse({"custom": {"name": "Custom"}, "reason": {"name": "Reason"}})

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/custom"):
            return _FakeResponse({"success": True, "message": "Custom preset updated"})
        if url.endswith("/templates"):
            body = kwargs.get("json", {})
            return _FakeResponse({"success": True, "template": {"id": body.get("id") or "tmpl2", **body}})
        if url.endswith("/groups"):
            return _FakeResponse({"ok": True})
        return _FakeResponse({"ok": True})

    async def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return _FakeResponse({"success": True})


def _install_fake_client(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient.calls


def test_manage_presets_list_uses_presets_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_presets(json.dumps({"action": "list"}), owner="admin"))

    assert result["exit_code"] == 0
    assert result["presets"]["custom"]["name"] == "Custom"
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/presets")


def test_manage_presets_template_views_are_read_only(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    templates = asyncio.run(do_manage_presets(json.dumps({"action": "templates"}), owner="admin"))
    groups = asyncio.run(do_manage_presets(json.dumps({"action": "groups"}), owner="admin"))

    assert templates["templates"][0]["id"] == "tmpl1"
    assert groups["groups"] == {"groups": [{"name": "Pair"}]}
    assert [call[0] for call in calls] == ["GET", "GET"]
    assert calls[0][1].endswith("/api/presets/templates")
    assert calls[1][1].endswith("/api/presets/groups")


def test_manage_presets_update_custom_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_presets(json.dumps({
        "action": "update_custom",
        "name": "Focus",
        "system_prompt": "Be concise.",
    }), owner="admin"))
    updated = asyncio.run(do_manage_presets(json.dumps({
        "action": "update_custom",
        "name": "Focus",
        "temperature": 0.4,
        "system_prompt": "Be concise.",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert updated["exit_code"] == 0
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/presets/custom")
    assert calls[0][2]["json"]["name"] == "Focus"
    assert calls[0][2]["json"]["temperature"] == 0.4


def test_manage_presets_save_and_delete_template_require_confirmation(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_presets(json.dumps({
        "action": "save_template",
        "name": "Helper",
    }), owner="admin"))
    saved = asyncio.run(do_manage_presets(json.dumps({
        "action": "save_template",
        "template_id": "tmpl1",
        "name": "Helper",
        "system_prompt": "Help.",
        "confirmed": True,
    }), owner="admin"))
    deleted = asyncio.run(do_manage_presets(json.dumps({
        "action": "delete_template",
        "template_id": "tmpl1",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert saved["result"]["template"]["id"] == "tmpl1"
    assert deleted["exit_code"] == 0
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/presets/templates")
    assert calls[1][0] == "DELETE"
    assert calls[1][1].endswith("/api/presets/templates/tmpl1")


def test_manage_presets_save_groups_requires_list_and_confirmation(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_presets(json.dumps({
        "action": "save_groups",
        "groups": [{"name": "Pair"}],
    }), owner="admin"))
    invalid = asyncio.run(do_manage_presets(json.dumps({
        "action": "save_groups",
        "groups": "bad",
        "confirmed": True,
    }), owner="admin"))
    saved = asyncio.run(do_manage_presets(json.dumps({
        "action": "save_groups",
        "groups": [{"name": "Pair"}],
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert invalid["exit_code"] == 1
    assert saved["exit_code"] == 0
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/presets/groups")
    assert calls[0][2]["json"] == {"groups": [{"name": "Pair"}]}

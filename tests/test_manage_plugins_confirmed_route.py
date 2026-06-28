import asyncio
import json

import httpx

from src.tool_implementations import do_manage_plugins


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
        if url.endswith("/api/plugins"):
            return _FakeResponse({"plugins": [{"id": "telegram", "enabled": True, "status": "loaded"}]})
        if url.endswith("/registry"):
            return _FakeResponse({"plugins": [{"id": "obsidian", "installed": False}], "sources": []})
        if url.endswith("/registries"):
            return _FakeResponse({"registries": ["https://example.invalid/plugins.json"], "custom": []})
        if url.endswith("/telegram/status"):
            return _FakeResponse({"plugin": "telegram", "ready": True})
        return _FakeResponse({"detail": "not found"}, status_code=404)

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/install"):
            return _FakeResponse({"id": kwargs.get("json", {}).get("id"), "installed": True})
        if url.endswith("/rescan"):
            return _FakeResponse({"plugins": [{"id": "telegram"}]})
        if url.endswith("/uninstall"):
            return _FakeResponse({"id": url.split("/api/plugins/", 1)[-1].split("/", 1)[0], "removed": True})
        if url.endswith("/registries"):
            return _FakeResponse({"registries": [kwargs.get("json", {}).get("url")]})
        plugin_id = url.split("/api/plugins/", 1)[-1].split("/", 1)[0]
        action = url.rsplit("/", 1)[-1]
        return _FakeResponse({"id": plugin_id, "action": action, "ok": True})

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _FakeResponse({"registries": []})


def _install_fake_client(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient.calls


def test_manage_plugins_list_registry_and_status_are_read_only(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    listed = asyncio.run(do_manage_plugins(json.dumps({"action": "list"}), owner="admin"))
    registry = asyncio.run(do_manage_plugins(json.dumps({"action": "registry"}), owner="admin"))
    status = asyncio.run(do_manage_plugins(json.dumps({
        "action": "status",
        "plugin_id": "telegram",
    }), owner="admin"))

    assert listed["plugins"][0]["id"] == "telegram"
    assert registry["registry"]["plugins"][0]["id"] == "obsidian"
    assert status["status"]["ready"] is True
    assert [call[0] for call in calls] == ["GET", "GET", "GET"]
    assert calls[2][1].endswith("/api/plugins/telegram/status")


def test_manage_plugins_enable_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_plugins(json.dumps({
        "action": "enable",
        "plugin_id": "telegram",
    }), owner="admin"))
    enabled = asyncio.run(do_manage_plugins(json.dumps({
        "action": "enable",
        "plugin_id": "telegram",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert enabled["plugin"]["action"] == "enable"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/plugins/telegram/enable")


def test_manage_plugins_install_requires_registry_id_and_confirmation(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_plugins(json.dumps({
        "action": "install",
        "plugin_id": "obsidian",
    }), owner="admin"))
    installed = asyncio.run(do_manage_plugins(json.dumps({
        "action": "install",
        "plugin_id": "obsidian",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert installed["plugin"]["id"] == "obsidian"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/plugins/install")
    assert calls[0][2]["json"] == {"id": "obsidian"}


def test_manage_plugins_install_blocks_direct_url_before_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_plugins(json.dumps({
        "action": "install",
        "plugin_id": "demo",
        "url": "https://example.invalid/demo.zip",
        "confirmed": True,
    }), owner="admin"))

    assert result["exit_code"] == 1
    assert "Direct plugin ZIP URL installs stay Plugins UI-only" in result["error"]
    assert calls == []


def test_manage_plugins_registries_require_confirmation_then_use_routes(monkeypatch):
    calls = _install_fake_client(monkeypatch)
    url = "https://example.invalid/plugins.json"

    blocked = asyncio.run(do_manage_plugins(json.dumps({
        "action": "add_registry",
        "url": url,
    }), owner="admin"))
    added = asyncio.run(do_manage_plugins(json.dumps({
        "action": "add_registry",
        "url": url,
        "confirmed": True,
    }), owner="admin"))
    removed = asyncio.run(do_manage_plugins(json.dumps({
        "action": "remove_registry",
        "url": url,
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert added["exit_code"] == 0
    assert removed["exit_code"] == 0
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/plugins/registries")
    assert calls[1][0] == "DELETE"
    assert calls[1][1].endswith("/api/plugins/registries")


def test_manage_plugins_rejects_bad_plugin_id_before_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_plugins(json.dumps({
        "action": "status",
        "plugin_id": "../telegram",
    }), owner="admin"))

    assert result["exit_code"] == 1
    assert "valid plugin_id" in result["error"]
    assert calls == []

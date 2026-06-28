import asyncio
import json

import httpx

from src.tool_implementations import do_manage_webhooks


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
        return _FakeResponse([
            {
                "id": "wh1",
                "name": "Discord",
                "url": "https://discord.example/hooks/secret-token",
                "events": ["chat.completed"],
                "is_active": False,
            }
        ])

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/test"):
            return _FakeResponse({"status": "sent"})
        return _FakeResponse({"id": "wh2", "name": kwargs.get("data", {}).get("name")})

    async def patch(self, url, **kwargs):
        self.calls.append(("PATCH", url, kwargs))
        return _FakeResponse({"id": url.rsplit("/", 1)[-1], "is_active": True})

    async def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return _FakeResponse({"status": "deleted"})


def _install_fake_client(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient.calls


def test_manage_webhooks_list_masks_urls(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_webhooks(json.dumps({"action": "list"}), owner="admin"))

    encoded = json.dumps(result)
    assert result["exit_code"] == 0
    assert result["webhooks"][0]["url"] == "https://discord.example/..."
    assert result["webhooks"][0]["has_url"] is True
    assert "secret-token" not in encoded
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/webhooks")


def test_manage_webhooks_add_requires_confirmation(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_webhooks(json.dumps({
        "action": "add",
        "name": "Discord",
        "url": "https://discord.example/hooks/secret-token",
    }), owner="admin"))

    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert calls == []


def test_manage_webhooks_add_confirmed_uses_route_without_echoing_url(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_webhooks(json.dumps({
        "action": "add",
        "name": "Discord",
        "url": "https://discord.example/hooks/secret-token",
        "events": "chat.completed",
        "confirmed": True,
    }), owner="admin"))

    assert result["exit_code"] == 0
    assert result["webhook"]["id"] == "wh2"
    assert "secret-token" not in json.dumps(result)
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/webhooks")
    assert calls[0][2]["data"]["url"] == "https://discord.example/hooks/secret-token"


def test_manage_webhooks_enable_confirmed_toggles_only_when_needed(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_webhooks(json.dumps({
        "action": "enable",
        "webhook_id": "wh1",
        "confirmed": True,
    }), owner="admin"))

    assert result["exit_code"] == 0
    assert result["webhook"]["is_active"] is True
    assert calls[0][0] == "GET"
    assert calls[1][0] == "PATCH"
    assert calls[1][1].endswith("/api/webhooks/wh1")


def test_manage_webhooks_test_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_webhooks(json.dumps({
        "action": "test",
        "webhook_id": "wh1",
    }), owner="admin"))
    sent = asyncio.run(do_manage_webhooks(json.dumps({
        "action": "test",
        "webhook_id": "wh1",
        "confirmed": True,
    }), owner="admin"))

    encoded = json.dumps(sent)
    assert blocked["status"] == "confirmation_required"
    assert sent["exit_code"] == 0
    assert sent["result"] == {"status": "sent"}
    assert "secret-token" not in encoded
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/webhooks/wh1/test")


def test_manage_webhooks_delete_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_webhooks(json.dumps({
        "action": "delete",
        "webhook_id": "wh1",
    }), owner="admin"))
    deleted = asyncio.run(do_manage_webhooks(json.dumps({
        "action": "delete",
        "webhook_id": "wh1",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert deleted["exit_code"] == 0
    assert calls[0][0] == "DELETE"
    assert calls[0][1].endswith("/api/webhooks/wh1")

import asyncio
import json

import httpx

from src.tool_implementations import do_manage_tokens


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
            {"id": "tok1", "name": "n8n", "token_prefix": "ody_1234", "is_active": True}
        ])

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _FakeResponse({
            "id": "tok2",
            "name": kwargs.get("data", {}).get("name"),
            "token": "ody_secret_token",
            "token_prefix": "ody_secr",
            "scopes": ["chat"],
        })

    async def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return _FakeResponse({"status": "deleted"})


def _install_fake_client(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient.calls


def test_manage_tokens_list_uses_internal_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_tokens(json.dumps({"action": "list"}), owner="admin"))

    assert result["exit_code"] == 0
    assert result["tokens"][0]["id"] == "tok1"
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/tokens")
    assert "X-Odysseus-Owner" in calls[0][2]["headers"]


def test_manage_tokens_create_requires_confirmation(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_tokens(json.dumps({
        "action": "create",
        "name": "n8n",
    }), owner="admin"))

    assert result["exit_code"] == 0
    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert calls == []


def test_manage_tokens_create_confirmed_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_tokens(json.dumps({
        "action": "create",
        "name": "n8n",
        "scopes": ["chat"],
        "confirmed": True,
    }), owner="admin"))

    assert result["exit_code"] == 0
    assert result["token"] == "ody_secret_token"
    assert result["token_meta"]["id"] == "tok2"
    assert "token" not in result["token_meta"]
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/tokens")
    assert calls[0][2]["data"]["scopes"] == "chat"


def test_manage_tokens_delete_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_tokens(json.dumps({
        "action": "delete",
        "token_id": "tok1",
    }), owner="admin"))
    deleted = asyncio.run(do_manage_tokens(json.dumps({
        "action": "delete",
        "token_id": "tok1",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert deleted["exit_code"] == 0
    assert calls[0][0] == "DELETE"
    assert calls[0][1].endswith("/api/tokens/tok1")

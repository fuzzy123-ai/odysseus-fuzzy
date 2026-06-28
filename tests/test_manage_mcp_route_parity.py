import asyncio
import json

import httpx

from src.tool_implementations import do_manage_mcp


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
        if url.endswith("/api/mcp/tools"):
            return _FakeResponse([
                {"name": "read_note", "server_name": "vault", "description": "Read a note"},
            ])
        return _FakeResponse([
            {
                "id": "srv1",
                "name": "Vault",
                "transport": "stdio",
                "command": "hidden-command",
                "env": {"API_KEY": "secret"},
                "is_enabled": True,
                "status": "connected",
                "tool_count": 3,
                "disabled_tool_count": 1,
                "enabled_tool_count": 2,
            }
        ])

    async def patch(self, url, **kwargs):
        self.calls.append(("PATCH", url, kwargs))
        return _FakeResponse({"id": url.rsplit("/", 1)[-1], "is_enabled": kwargs.get("data", {}).get("is_enabled") == "true"})

    async def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return _FakeResponse({"status": "deleted"})


def _install_fake_client(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient.calls


def test_manage_mcp_lists_via_internal_route_without_env_or_command(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_mcp(json.dumps({"action": "list"}), owner="admin"))

    assert result["exit_code"] == 0
    assert result["servers"][0]["id"] == "srv1"
    assert "command" not in result["servers"][0]
    assert "env" not in result["servers"][0]
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/mcp/servers")
    assert "X-Odysseus-Owner" in calls[0][2]["headers"]


def test_manage_mcp_enable_and_delete_use_internal_routes(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    enabled = asyncio.run(do_manage_mcp(json.dumps({
        "action": "enable",
        "server_id": "srv1",
        "confirmed": True,
    }), owner="admin"))
    deleted = asyncio.run(do_manage_mcp(json.dumps({
        "action": "delete",
        "server_id": "srv1",
        "confirmed": True,
    }), owner="admin"))

    assert enabled["exit_code"] == 0
    assert deleted["exit_code"] == 0
    assert calls[0][0] == "PATCH"
    assert calls[0][1].endswith("/api/mcp/servers/srv1")
    assert calls[0][2]["data"] == {"is_enabled": "true"}
    assert calls[1][0] == "DELETE"
    assert calls[1][1].endswith("/api/mcp/servers/srv1")


def test_manage_mcp_list_tools_uses_internal_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_mcp(json.dumps({"action": "list_tools"}), owner="admin"))

    assert result["exit_code"] == 0
    assert result["tools"] == [{"name": "read_note", "server": "vault", "description": "Read a note"}]
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/mcp/tools")

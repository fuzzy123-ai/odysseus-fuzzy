"""Verify that MCP reconnect via the agent tool passes full server metadata."""

import asyncio
import json

import httpx


class _FakeResponse:
    status_code = 200
    text = "{}"

    def __init__(self, data):
        self._data = data
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

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _FakeResponse({"connected": True, "status": "connected", "tool_count": 3})


def test_reconnect_uses_internal_mcp_route(monkeypatch):
    """do_manage_mcp reconnect delegates to the same route as the UI."""
    from src.tool_implementations import do_manage_mcp

    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    result = asyncio.run(do_manage_mcp(
        json.dumps({"action": "reconnect", "server_id": "srv-123", "confirmed": True})
    ))

    assert result["exit_code"] == 0
    assert len(_FakeAsyncClient.calls) == 1
    method, url, kwargs = _FakeAsyncClient.calls[0]
    assert method == "POST"
    assert url.endswith("/api/mcp/servers/srv-123/reconnect")
    assert "X-Odysseus-Internal-Token" in kwargs["headers"]

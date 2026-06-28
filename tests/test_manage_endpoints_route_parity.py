import asyncio
import json

import httpx

from src.tool_implementations import do_manage_endpoints


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
                "id": "ep1",
                "name": "Local",
                "base_url": "http://127.0.0.1:11434/v1",
                "is_enabled": True,
                "models": ["qwen"],
            }
        ])

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _FakeResponse({
            "id": "ep2",
            "name": kwargs.get("data", {}).get("name") or "New",
            "base_url": kwargs.get("data", {}).get("base_url"),
            "has_key": False,
        })

    async def patch(self, url, **kwargs):
        self.calls.append(("PATCH", url, kwargs))
        return _FakeResponse({
            "id": url.rsplit("/", 1)[-1],
            "name": "Updated",
            "is_enabled": kwargs.get("json", {}).get("is_enabled", True),
        })

    async def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return _FakeResponse({"deleted": True, "cleared_settings": []})


def _install_fake_client(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient.calls


def test_manage_endpoints_lists_via_internal_model_endpoint_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_endpoints(json.dumps({"action": "list"}), owner="admin"))

    assert result["exit_code"] == 0
    assert result["endpoints"][0]["id"] == "ep1"
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/model-endpoints")
    assert "X-Odysseus-Owner" in calls[0][2]["headers"]


def test_manage_endpoints_adds_via_internal_route_without_secret(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_endpoints(json.dumps({
        "action": "add",
        "name": "Local",
        "base_url": "http://127.0.0.1:11434/v1",
        "skip_probe": True,
        "pinned_models": ["qwen"],
        "shared": False,
        "confirmed": True,
    }), owner="admin"))

    assert result["exit_code"] == 0
    assert result["endpoint"]["id"] == "ep2"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/model-endpoints")
    assert calls[0][2]["data"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert calls[0][2]["data"]["pinned_models"] == '["qwen"]'
    assert "api_key" not in calls[0][2]["data"]


def test_manage_endpoints_add_requires_confirmation(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_endpoints(json.dumps({
        "action": "add",
        "name": "Local",
        "base_url": "http://127.0.0.1:11434/v1",
    }), owner="admin"))

    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert calls == []


def test_manage_endpoints_blocks_raw_api_keys_without_route_call(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_endpoints(json.dumps({
        "action": "add",
        "name": "Cloud",
        "base_url": "https://api.example.test/v1",
        "api_key": "secret-value",
    }), owner="admin"))

    assert result["exit_code"] == 0
    assert result["status"] == "secret_handoff_required"
    assert calls == []
    assert "secret-value" not in json.dumps(result)


def test_manage_endpoints_delete_and_enable_use_route_cleanup_paths(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_endpoints(json.dumps({
        "action": "delete",
        "endpoint_id": "ep1",
    }), owner="admin"))
    deleted = asyncio.run(do_manage_endpoints(json.dumps({
        "action": "delete",
        "endpoint_id": "ep1",
        "confirmed": True,
    }), owner="admin"))
    enabled = asyncio.run(do_manage_endpoints(json.dumps({
        "action": "enable",
        "endpoint_id": "ep1",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert blocked["requires_confirmation"] is True
    assert deleted["exit_code"] == 0
    assert enabled["exit_code"] == 0
    assert calls[0][0] == "DELETE"
    assert calls[0][1].endswith("/api/model-endpoints/ep1")
    assert calls[1][0] == "PATCH"
    assert calls[1][1].endswith("/api/model-endpoints/ep1")
    assert calls[1][2]["json"] == {"is_enabled": True}


def test_manage_endpoints_update_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_endpoints(json.dumps({
        "action": "update",
        "endpoint_id": "ep1",
        "name": "Updated",
    }), owner="admin"))
    updated = asyncio.run(do_manage_endpoints(json.dumps({
        "action": "update",
        "endpoint_id": "ep1",
        "name": "Updated",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert blocked["requires_confirmation"] is True
    assert updated["exit_code"] == 0
    assert len(calls) == 1
    assert calls[0][0] == "PATCH"
    assert calls[0][1].endswith("/api/model-endpoints/ep1")
    assert calls[0][2]["json"] == {"name": "Updated"}

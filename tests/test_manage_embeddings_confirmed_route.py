import asyncio
import json

import httpx

from src.tool_implementations import do_manage_embeddings


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
        if url.endswith("/endpoint"):
            return _FakeResponse({"url": "http://embed.local/v1", "model": "bge", "active": True})
        if url.endswith("/status"):
            return _FakeResponse({"model": "BAAI/bge-small-en-v1.5", "downloaded": True, "downloading": False})
        return _FakeResponse([
            {
                "model": "BAAI/bge-small-en-v1.5",
                "downloaded": True,
                "active": True,
                "recommended": True,
            }
        ])

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _FakeResponse({"status": "downloaded", "model": url.split("/models/", 1)[-1].split("/download", 1)[0]})

    async def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        if url.endswith("/endpoint"):
            return _FakeResponse({"success": True})
        return _FakeResponse({"deleted": True, "model": url.split("/models/", 1)[-1]})


def _install_fake_client(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient.calls


def test_manage_embeddings_list_uses_models_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_embeddings(json.dumps({"action": "list"}), owner="admin"))

    assert result["exit_code"] == 0
    assert result["models"][0]["model"] == "BAAI/bge-small-en-v1.5"
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/embeddings/models")


def test_manage_embeddings_status_uses_status_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_embeddings(json.dumps({
        "action": "status",
        "model_name": "BAAI/bge-small-en-v1.5",
    }), owner="admin"))

    assert result["exit_code"] == 0
    assert result["status"]["downloaded"] is True
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/embeddings/models/BAAI/bge-small-en-v1.5/status")


def test_manage_embeddings_endpoint_is_read_only(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_embeddings(json.dumps({"action": "endpoint"}), owner="admin"))

    assert result["exit_code"] == 0
    assert result["endpoint"]["active"] is True
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/embeddings/endpoint")


def test_manage_embeddings_download_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_embeddings(json.dumps({
        "action": "download",
        "model_name": "BAAI/bge-small-en-v1.5",
    }), owner="admin"))
    downloaded = asyncio.run(do_manage_embeddings(json.dumps({
        "action": "download",
        "model_name": "BAAI/bge-small-en-v1.5",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert downloaded["exit_code"] == 0
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/embeddings/models/BAAI/bge-small-en-v1.5/download")


def test_manage_embeddings_delete_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_embeddings(json.dumps({
        "action": "delete",
        "model_name": "BAAI/bge-small-en-v1.5",
    }), owner="admin"))
    deleted = asyncio.run(do_manage_embeddings(json.dumps({
        "action": "delete",
        "model_name": "BAAI/bge-small-en-v1.5",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert deleted["exit_code"] == 0
    assert calls[0][0] == "DELETE"
    assert calls[0][1].endswith("/api/embeddings/models/BAAI/bge-small-en-v1.5")


def test_manage_embeddings_clear_endpoint_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_embeddings(json.dumps({"action": "clear_endpoint"}), owner="admin"))
    cleared = asyncio.run(do_manage_embeddings(json.dumps({
        "action": "clear_endpoint",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert cleared["exit_code"] == 0
    assert calls[0][0] == "DELETE"
    assert calls[0][1].endswith("/api/embeddings/endpoint")


def test_manage_embeddings_set_endpoint_stays_ui_only(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_embeddings(json.dumps({
        "action": "set_endpoint",
        "url": "http://embed.local/v1",
        "confirmed": True,
    }), owner="admin"))

    assert result["exit_code"] == 1
    assert "secure-handoff-only" in result["error"]
    assert calls == []

import asyncio
import json

import httpx

from src.tool_implementations import do_manage_personal_docs


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
        return _FakeResponse({
            "files": [{"name": "notes.md", "size": 12, "path": "/personal/notes.md"}],
            "directories": ["/personal"],
        })

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/reload"):
            return _FakeResponse({"ok": True, "count": 3})
        if url.endswith("/add_directory"):
            directory = kwargs.get("json", {}).get("directory")
            return _FakeResponse({
                "success": True,
                "message": f"Successfully indexed 2 chunks from {directory}",
                "indexed_count": 2,
                "failed_count": 0,
                "directory": directory,
            })
        return _FakeResponse({"ok": True})

    async def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        if url.endswith("/remove_directory"):
            directory = kwargs.get("params", {}).get("directory")
            return _FakeResponse({
                "success": True,
                "message": f"Successfully removed {directory} from RAG index",
                "directory": directory,
            })
        if url.endswith("/file"):
            return _FakeResponse({
                "success": True,
                "removed_chunks": 4,
                "deleted_from_disk": False,
            })
        return _FakeResponse({"success": True})


def _install_fake_client(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient.calls


def test_manage_personal_docs_list_uses_personal_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_personal_docs(json.dumps({"action": "list"}), owner="admin"))

    assert result["exit_code"] == 0
    assert result["personal_docs"]["files"][0]["name"] == "notes.md"
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/personal")


def test_manage_personal_docs_reload_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_personal_docs(json.dumps({"action": "reload"}), owner="admin"))
    reloaded = asyncio.run(do_manage_personal_docs(json.dumps({
        "action": "reload",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert reloaded["exit_code"] == 0
    assert reloaded["result"] == {"ok": True, "count": 3}
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/personal/reload")


def test_manage_personal_docs_add_directory_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_personal_docs(json.dumps({
        "action": "add_directory",
        "directory": "projects",
    }), owner="admin"))
    added = asyncio.run(do_manage_personal_docs(json.dumps({
        "action": "add_directory",
        "directory": "projects",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert added["exit_code"] == 0
    assert added["result"]["indexed_count"] == 2
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/personal/add_directory")
    assert calls[0][2]["json"] == {"directory": "projects"}


def test_manage_personal_docs_remove_directory_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_personal_docs(json.dumps({
        "action": "remove_directory",
        "directory": "projects",
    }), owner="admin"))
    removed = asyncio.run(do_manage_personal_docs(json.dumps({
        "action": "remove_directory",
        "directory": "projects",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert removed["exit_code"] == 0
    assert calls[0][0] == "DELETE"
    assert calls[0][1].endswith("/api/personal/remove_directory")
    assert calls[0][2]["params"] == {"directory": "projects"}


def test_manage_personal_docs_delete_file_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_personal_docs(json.dumps({
        "action": "delete_file",
        "filepath": "/personal/notes.md",
    }), owner="admin"))
    deleted = asyncio.run(do_manage_personal_docs(json.dumps({
        "action": "delete_file",
        "filepath": "/personal/notes.md",
        "confirmed": True,
    }), owner="admin"))

    assert blocked["status"] == "confirmation_required"
    assert deleted["exit_code"] == 0
    assert deleted["result"]["removed_chunks"] == 4
    assert calls[0][0] == "DELETE"
    assert calls[0][1].endswith("/api/personal/file")
    assert calls[0][2]["params"] == {"filepath": "/personal/notes.md"}


def test_manage_personal_docs_upload_stays_ui_only(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_personal_docs(json.dumps({"action": "upload"}), owner="admin"))

    assert result["exit_code"] == 1
    assert "UI-only" in result["error"]
    assert calls == []

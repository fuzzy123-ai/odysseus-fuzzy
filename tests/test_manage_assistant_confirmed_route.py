import asyncio
import json

import httpx

from src.tool_implementations import do_manage_assistant


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
        if url.endswith("/session"):
            return _FakeResponse({"session_id": "s1", "crew_member_id": "c1", "name": "Ody"})
        if url.endswith("/settings"):
            return _FakeResponse({
                "crew": {"id": "c1", "name": "Ody", "timezone": "Europe/Berlin"},
                "check_ins": [{"id": "t1", "name": "Morning", "enabled": True}],
                "task_ids": ["t1"],
            })
        if url.endswith("/available-timezones"):
            return _FakeResponse({"timezones": ["Europe/Berlin", "UTC"]})
        if url.endswith("/run-status/t1"):
            return _FakeResponse({"status": "done", "result_status": "success"})
        return _FakeResponse({"detail": "not found"}, status_code=404)

    async def patch(self, url, **kwargs):
        self.calls.append(("PATCH", url, kwargs))
        return _FakeResponse({
            "crew": kwargs.get("json") or {},
            "check_ins": (kwargs.get("json") or {}).get("check_ins") or [],
            "task_ids": ["t1"],
        })

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _FakeResponse({"started": True})


def _install_fake_client(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient.calls


def test_manage_assistant_settings_is_read_only_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_assistant(json.dumps({"action": "settings"}), owner="niklas"))

    assert result["exit_code"] == 0
    assert result["assistant"]["crew"]["name"] == "Ody"
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/assistant/settings")


def test_manage_assistant_session_and_timezones_are_read_only(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    session = asyncio.run(do_manage_assistant(json.dumps({"action": "session"}), owner="niklas"))
    timezones = asyncio.run(do_manage_assistant(json.dumps({"action": "timezones"}), owner="niklas"))

    assert session["session"]["session_id"] == "s1"
    assert "Europe/Berlin" in timezones["timezones"]
    assert [call[0] for call in calls] == ["GET", "GET"]
    assert calls[0][1].endswith("/api/assistant/session")
    assert calls[1][1].endswith("/api/assistant/available-timezones")


def test_manage_assistant_update_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)
    payload = {
        "action": "update",
        "name": "Ody Night Shift",
        "timezone": "Europe/Berlin",
        "check_ins": [{"id": "t1", "scheduled_time": "21:00", "enabled": True}],
    }

    blocked = asyncio.run(do_manage_assistant(json.dumps(payload), owner="niklas"))
    updated = asyncio.run(do_manage_assistant(json.dumps({**payload, "confirmed": True}), owner="niklas"))

    assert blocked["status"] == "confirmation_required"
    assert updated["exit_code"] == 0
    assert calls[0][0] == "PATCH"
    assert calls[0][1].endswith("/api/assistant/settings")
    assert calls[0][2]["json"]["name"] == "Ody Night Shift"
    assert calls[0][2]["json"]["check_ins"][0]["id"] == "t1"


def test_manage_assistant_update_rejects_endpoint_url_before_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    result = asyncio.run(do_manage_assistant(json.dumps({
        "action": "update",
        "endpoint_url": "https://api.example.invalid/v1",
        "confirmed": True,
    }), owner="niklas"))

    assert result["exit_code"] == 1
    assert "endpoint_url stays UI/manage_endpoints-only" in result["error"]
    assert calls == []


def test_manage_assistant_run_requires_confirmation_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    blocked = asyncio.run(do_manage_assistant(json.dumps({
        "action": "run",
        "task_id": "t1",
    }), owner="niklas"))
    started = asyncio.run(do_manage_assistant(json.dumps({
        "action": "run",
        "task_id": "t1",
        "confirmed": True,
    }), owner="niklas"))

    assert blocked["status"] == "confirmation_required"
    assert started["exit_code"] == 0
    assert started["result"]["started"] is True
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/assistant/run/t1")


def test_manage_assistant_run_status_requires_task_id_then_uses_route(monkeypatch):
    calls = _install_fake_client(monkeypatch)

    missing = asyncio.run(do_manage_assistant(json.dumps({"action": "run_status"}), owner="niklas"))
    status = asyncio.run(do_manage_assistant(json.dumps({
        "action": "run_status",
        "task_id": "t1",
    }), owner="niklas"))

    assert missing["exit_code"] == 1
    assert status["status"]["result_status"] == "success"
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/assistant/run-status/t1")

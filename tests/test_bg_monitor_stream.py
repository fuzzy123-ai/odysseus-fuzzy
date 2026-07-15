import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from src import bg_monitor


def test_drain_agent_ignores_non_string_deltas(monkeypatch):
    async def fake_stream_agent_loop(*args, **kwargs):
        yield 'data: {"delta": null}'
        yield 'data: {"delta": ["bad"]}'
        yield 'data: {"delta": "ok"}'
        yield 'data: {"type": "agent_step", "round": 2}'
        yield 'data: {"type": "tool_output", "tool": "shell", "output": "done"}'
        yield "data: [DONE]"

    agent_loop = types.ModuleType("src.agent_loop")
    agent_loop.stream_agent_loop = fake_stream_agent_loop
    monkeypatch.setitem(sys.modules, "src.agent_loop", agent_loop)

    sess = SimpleNamespace(
        endpoint_url="http://example.test",
        model="model",
        headers=None,
        context_length=0,
        id="s1",
    )

    full, events = asyncio.run(bg_monitor._drain_agent(sess, []))

    assert full == "ok"
    assert events == [{
        "round": 2,
        "tool": "shell",
        "command": None,
        "output": "done",
        "exit_code": None,
    }]


def test_monitor_marks_only_the_matching_successful_lease(monkeypatch):
    calls = []
    record = {
        "id": "job-1",
        "followup_lease": {"token": "lease-1"},
    }

    monkeypatch.setattr(
        bg_monitor.bg_jobs,
        "lease_pending_followups",
        lambda **kwargs: calls.append(("lease", kwargs)) or [record],
    )

    async def fake_run_followup(rec):
        calls.append(("run", rec["id"]))
        return True

    monkeypatch.setattr(bg_monitor, "_run_followup", fake_run_followup)
    monkeypatch.setattr(
        bg_monitor.bg_jobs,
        "mark_followed_up",
        lambda job_id, **kwargs: calls.append(("mark", job_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        bg_monitor.bg_jobs,
        "release_followup_lease",
        lambda *args, **kwargs: calls.append(("release", args, kwargs)),
    )

    async def stop_after_tick(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(bg_monitor.asyncio, "sleep", stop_after_tick)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(bg_monitor._loop())

    assert calls[0] == ("lease", {"lease_owner": bg_monitor._FOLLOWUP_LEASE_OWNER})
    assert ("run", "job-1") in calls
    assert ("mark", "job-1", {"lease_token": "lease-1"}) in calls
    assert not any(call[0] == "release" for call in calls)


@pytest.mark.parametrize("raises", [False, True])
def test_monitor_releases_lease_when_followup_does_not_complete(monkeypatch, raises):
    released = []
    record = {
        "id": "job-2",
        "followup_lease": {"token": "lease-2"},
    }
    monkeypatch.setattr(
        bg_monitor.bg_jobs,
        "lease_pending_followups",
        lambda **kwargs: [record],
    )

    async def fake_run_followup(_rec):
        if raises:
            raise RuntimeError("transient")
        return False

    monkeypatch.setattr(bg_monitor, "_run_followup", fake_run_followup)
    monkeypatch.setattr(
        bg_monitor.bg_jobs,
        "release_followup_lease",
        lambda job_id, token: released.append((job_id, token)) or True,
    )

    async def stop_after_tick(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(bg_monitor.asyncio, "sleep", stop_after_tick)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(bg_monitor._loop())

    assert released == [("job-2", "lease-2")]

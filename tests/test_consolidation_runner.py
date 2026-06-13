import asyncio

from src.consolidation_runner import run_consolidation_jobs
from src.plugin_system import register_consolidation_job, unregister_consolidation_job


def teardown_function():
    for job_id in ("demo.high", "demo.low", "demo.other", "demo.broken", "demo.async"):
        unregister_consolidation_job(job_id)


def test_runner_filters_and_sorts_jobs_by_capability():
    calls = []

    register_consolidation_job({
        "id": "demo.low",
        "label": "Low",
        "priority": 1,
        "capabilities": ["chat_completed"],
        "run": lambda owner=None, **kwargs: calls.append(("low", owner)) or {"low": True},
    }, plugin_id="demo")
    register_consolidation_job({
        "id": "demo.high",
        "label": "High",
        "priority": 10,
        "capabilities": ["chat_completed"],
        "run": lambda owner=None, **kwargs: calls.append(("high", owner)) or {"high": True},
    }, plugin_id="demo")
    register_consolidation_job({
        "id": "demo.other",
        "label": "Other",
        "capabilities": ["manual"],
        "run": lambda **kwargs: calls.append(("other", None)),
    }, plugin_id="demo")

    results = asyncio.run(run_consolidation_jobs(owner="alice", capability="chat_completed"))

    assert [result.job_id for result in results] == ["demo.high", "demo.low"]
    assert calls == [("high", "alice"), ("low", "alice")]
    assert all(result.ok for result in results)


def test_runner_isolates_failures_and_continues():
    calls = []

    def broken(**kwargs):
        calls.append("broken")
        raise RuntimeError("boom")

    register_consolidation_job({
        "id": "demo.broken",
        "label": "Broken",
        "priority": 10,
        "capabilities": ["chat_completed"],
        "run": broken,
    })
    register_consolidation_job({
        "id": "demo.low",
        "label": "Low",
        "priority": 1,
        "capabilities": ["chat_completed"],
        "run": lambda **kwargs: calls.append("low") or {"ok": True},
    })

    results = asyncio.run(run_consolidation_jobs(capability="chat_completed"))

    assert calls == ["broken", "low"]
    assert results[0].ok is False
    assert results[0].error == "boom"
    assert results[1].ok is True


def test_runner_supports_async_jobs_and_context_kwargs():
    async def run(owner=None, trigger=None, context=None):
        return {"owner": owner, "trigger": trigger, "session": context["session_id"]}

    register_consolidation_job({
        "id": "demo.async",
        "label": "Async",
        "capabilities": ["chat_completed"],
        "run": run,
    }, plugin_id="demo")

    results = asyncio.run(run_consolidation_jobs(
        owner="alice",
        capability="chat_completed",
        trigger="chat.completed",
        context={"session_id": "s1"},
    ))

    assert results[0].ok is True
    assert results[0].result == {"owner": "alice", "trigger": "chat.completed", "session": "s1"}

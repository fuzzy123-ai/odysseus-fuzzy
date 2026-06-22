"""Owner-scope tests for the remaining _resolve_model call sites.

Both the teacher-escalation path and the skill-audit teacher resolution map a
model spec to an endpoint (and its decrypted api_key). Like /presets/expand,
that lookup must be scoped to the calling user, otherwise it can resolve another
owner's ModelEndpoint in a multi-user deployment. See #2283.
"""

import asyncio
import json

import src.teacher_escalation as teacher_escalation
import routes.skills_routes as skills_routes


def test_call_teacher_scopes_model_resolution_to_owner(monkeypatch):
    seen = {}

    def fake_resolve_model(spec, owner=None):
        seen["spec"] = spec
        seen["owner"] = owner
        return ("http://endpoint.local/v1", "teacher-model", {})

    async def fake_llm_call_async(url, model, messages, **kwargs):
        return "teacher reply"

    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)
    monkeypatch.setattr("src.ai_interaction._TEACHER_SYSTEM_PROMPT", "sys", raising=False)
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    result = asyncio.run(
        teacher_escalation._call_teacher("teacher-model", "prompt", owner="alice")
    )

    assert result == "teacher reply"
    assert seen["owner"] == "alice"
    assert seen["spec"] == "teacher-model"


def test_audit_teacher_resolution_scoped_to_owner(monkeypatch):
    seen = {}

    def fake_resolve_endpoint(role, owner=None):
        return ("http://worker.local/v1", "worker-model", {})

    def fake_get_setting(key, default=None):
        return {"teacher_enabled": True, "teacher_model": "teacher-model"}.get(key, default)

    def fake_resolve_model(spec, owner=None):
        seen["spec"] = spec
        seen["owner"] = owner
        return ("http://endpoint.local/v1", "teacher-model", {})

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr("src.settings.get_setting", fake_get_setting)
    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)
    # list_model_ids is best-effort; force it to no-op so the worker model passes through.
    monkeypatch.setattr("src.llm_core.list_model_ids", lambda url, headers=None: [])

    url, model, headers, teacher = skills_routes._resolve_audit_models(owner="alice")

    assert (url, model) == ("http://worker.local/v1", "worker-model")
    assert teacher == ("http://endpoint.local/v1", "teacher-model", {})
    assert seen["owner"] == "alice"
    assert seen["spec"] == "teacher-model"


def test_teacher_capability_status_redacts_endpoint_and_warns_for_local_model(monkeypatch):
    seen = {}

    def fake_get_setting(key, default=None):
        return {"teacher_enabled": True, "teacher_model": "local-teacher"}.get(key, default)

    def fake_resolve_model(spec, owner=None):
        seen["spec"] = spec
        seen["owner"] = owner
        return (
            "http://localhost:11434/v1/chat/completions",
            "small-local-teacher",
            {"Authorization": "Bearer super-secret"},
        )

    monkeypatch.setattr("src.settings.get_setting", fake_get_setting)
    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)
    monkeypatch.setattr("src.model_context.get_context_length", lambda url, model: 8000)
    monkeypatch.setattr("src.model_context.is_local_endpoint", lambda url: True)

    status = teacher_escalation.resolve_teacher_capability_status(owner="alice")

    assert seen == {"spec": "local-teacher", "owner": "alice"}
    assert status["role"] == "teacher.escalation"
    assert status["enabled"] is True
    assert status["configured"] is True
    assert status["selected_model"] == "small-local-teacher"
    assert status["provider"] == "Local"
    assert status["mode"] == "local"
    assert status["model_context_tokens"] == 8000
    assert "model_context_below_recommended_16k" in status["model_capability_warnings"]
    assert "teacher_model_local_capability_may_be_limited" in status["model_capability_warnings"]
    assert status["orca_context_contract"] == {
        "inherits_agent_context_providers": True,
        "provider_mode": "agent",
        "untrusted_context_boundary": True,
        "recursion_guard": True,
    }
    assert "localhost" not in str(status).lower()
    assert "secret" not in str(status).lower()


def test_teacher_capability_status_degrades_when_teacher_missing(monkeypatch):
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: {"teacher_enabled": True, "teacher_model": ""}.get(key, default),
    )

    status = teacher_escalation.resolve_teacher_capability_status(owner="alice")

    assert status["enabled"] is True
    assert status["configured"] is False
    assert status["selected_model"] == ""
    assert status["model_capability_warnings"] == ["teacher_model_not_configured"]


def test_run_teacher_inline_surfaces_capability_status_and_reuses_agent_context(monkeypatch):
    seen = {}

    def fake_get_setting(key, default=None):
        return {"teacher_enabled": True, "teacher_model": "local-teacher"}.get(key, default)

    def fake_resolve_model(spec, owner=None):
        seen.setdefault("resolutions", []).append({"spec": spec, "owner": owner})
        return (
            "http://localhost:11434/v1/chat/completions",
            "small-local-teacher",
            {"Authorization": "Bearer super-secret"},
        )

    async def fake_stream_agent_loop(**kwargs):
        seen["stream_kwargs"] = kwargs
        yield 'data: {"delta":"done"}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_call_teacher(*args, **kwargs):
        return "NO_SKILL"

    monkeypatch.setattr("src.settings.get_setting", fake_get_setting)
    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)
    monkeypatch.setattr("src.model_context.get_context_length", lambda url, model: 8000)
    monkeypatch.setattr("src.model_context.is_local_endpoint", lambda url: True)
    monkeypatch.setattr("src.agent_loop.stream_agent_loop", fake_stream_agent_loop)
    monkeypatch.setattr(teacher_escalation, "_call_teacher", fake_call_teacher)

    events = []
    async def collect():
        async for raw in teacher_escalation.run_teacher_inline(
            student_endpoint_url="http://localhost:11434/v1/chat/completions",
            student_messages=[{"role": "user", "content": "Fix the ORCA plan."}],
            student_tool_events=[],
            student_reply="I don't have a tool for that.",
            owner="alice",
        ):
            if raw.strip() == "data: [DONE]":
                continue
            assert raw.startswith("data: ")
            events.append(json.loads(raw[6:].strip()))

    asyncio.run(collect())

    assert events[0]["type"] == "teacher_capability_status"
    assert events[0]["status"]["mode"] == "local"
    assert "teacher_model_local_capability_may_be_limited" in events[0]["status"]["model_capability_warnings"]
    assert "secret" not in str(events[0]).lower()
    assert events[1]["type"] == "teacher_takeover"
    assert seen["stream_kwargs"]["owner"] == "alice"
    assert seen["stream_kwargs"]["_is_teacher_run"] is True
    assert seen["stream_kwargs"]["messages"][-1]["content"].startswith("Fix the ORCA plan.")
    assert seen["resolutions"] == [
        {"spec": "local-teacher", "owner": "alice"},
        {"spec": "local-teacher", "owner": "alice"},
    ]

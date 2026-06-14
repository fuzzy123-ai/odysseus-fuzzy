import json

import pytest

from src.reflector_agent import run_reflector_assessment


@pytest.mark.asyncio
async def test_reflector_uses_teacher_model_setting(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: "teacher-model@endpoint" if key == "teacher_model" else default,
    )

    def fake_resolve_model(spec, owner=None):
        captured["spec"] = spec
        captured["owner"] = owner
        return "http://teacher.test/v1", "teacher-model", {"Authorization": "Bearer teacher"}

    async def fake_llm_call_async(url, model, messages, **kwargs):
        captured["url"] = url
        captured["model"] = model
        captured["messages"] = messages
        captured["headers"] = kwargs.get("headers")
        return json.dumps({
            "status": "risk",
            "assessment": "The run is drifting.",
            "risks": ["Too many repeated delegations."],
            "next_step": "Ask a narrower worker question.",
            "state_doc_note": "Refocus on the stated goal.",
        })

    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    result = await run_reflector_assessment(
        owner="alice",
        user_request="Ship the reflector.",
        state_doc_content="# Active Run",
        actions_snapshot="delegate ok",
        trigger="periodic",
        round_num=3,
    )

    assert result["status"] == "risk"
    assert result["assessment"] == "The run is drifting."
    assert result["risks"] == ["Too many repeated delegations."]
    assert result["teacher_model"] == "teacher-model@endpoint"
    assert captured["spec"] == "teacher-model@endpoint"
    assert captured["owner"] == "alice"
    assert captured["url"] == "http://teacher.test/v1"
    assert captured["headers"] == {"Authorization": "Bearer teacher"}
    assert "Ship the reflector." in captured["messages"][1]["content"]


@pytest.mark.asyncio
async def test_reflector_skips_when_teacher_model_missing(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: "" if key == "teacher_model" else default)

    result = await run_reflector_assessment(
        owner="alice",
        user_request="Goal",
        state_doc_content="",
        actions_snapshot="",
        trigger="periodic",
    )

    assert result is None


@pytest.mark.asyncio
async def test_reflector_skips_when_teacher_unresolvable(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: "missing-teacher" if key == "teacher_model" else default)

    def fake_resolve_model(spec, owner=None):
        raise ValueError("not found")

    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)

    result = await run_reflector_assessment(
        owner="alice",
        user_request="Goal",
        state_doc_content="",
        actions_snapshot="",
        trigger="periodic",
    )

    assert result is None


@pytest.mark.asyncio
async def test_reflector_invalid_json_becomes_fallback_assessment(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: "teacher" if key == "teacher_model" else default)
    monkeypatch.setattr("src.ai_interaction._resolve_model", lambda spec, owner=None: ("http://teacher", "teacher", {}))

    async def fake_llm_call_async(*args, **kwargs):
        return "The orchestrator is still aligned, but should narrow the next delegation."

    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    result = await run_reflector_assessment(
        owner=None,
        user_request="Goal",
        state_doc_content="",
        actions_snapshot="",
        trigger="periodic",
    )

    assert result["status"] == "ok"
    assert "still aligned" in result["assessment"]
    assert result["teacher_model"] == "teacher"


@pytest.mark.asyncio
async def test_reflector_teacher_exception_does_not_block(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: "teacher" if key == "teacher_model" else default)
    monkeypatch.setattr("src.ai_interaction._resolve_model", lambda spec, owner=None: ("http://teacher", "teacher", {}))

    async def fake_llm_call_async(*args, **kwargs):
        raise RuntimeError("teacher down")

    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    result = await run_reflector_assessment(
        owner=None,
        user_request="Goal",
        state_doc_content="",
        actions_snapshot="",
        trigger="periodic",
    )

    assert result is None

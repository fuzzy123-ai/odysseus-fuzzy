import asyncio
import os
import sys

from fastapi import HTTPException


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend import model_router


def test_model_router_status_uses_defaults_and_hides_secrets(monkeypatch):
    settings = {
        "memory.router_model": "heuristic",
        "memory.answer_model": "default",
        "memory.answer_fallback_models": [],
        "memory.summarize_model": "default",
        "memory.graph_extract_model": "default",
        "memory.global_synthesis_model": "default",
        "memory.embedding_model": "",
        "default_endpoint_id": "endpoint-cloud",
    }

    def fake_get_user_setting(key, owner="", default=None):
        return settings.get(key, default)

    def fake_resolve_endpoint(prefix, owner=None):
        if prefix == "default":
            return "https://api.deepseek.com/v1/chat/completions", "deepseek-chat", {"Authorization": "Bearer super-secret"}
        return "http://localhost:11434/v1/chat/completions", "gemma-4", {"Authorization": "Bearer local-secret"}

    monkeypatch.setattr(model_router, "get_user_setting", fake_get_user_setting)
    monkeypatch.setattr(model_router, "resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr(model_router, "resolve_chat_fallback_candidates", lambda owner=None: [])
    monkeypatch.setattr(model_router, "resolve_endpoint_by_id", lambda endpoint_id, model, owner=None: None)
    monkeypatch.setattr(model_router, "get_context_length", lambda url, model: 64000 if "deepseek" in url else 32000)
    monkeypatch.setattr(model_router, "is_local_endpoint", lambda url: "localhost" in url)

    status = model_router.resolve_memory_role_status("alice")

    assert status["roles"]["memory.router"]["selected_model"] == "heuristic"
    assert status["roles"]["memory.answer"]["selected_model"] == "deepseek-chat"
    assert status["roles"]["memory.answer"]["selected_endpoint_id"] == "endpoint-cloud"
    assert status["roles"]["memory.answer"]["provider"] == "DeepSeek"
    assert "secret" not in str(status).lower()
    assert status["warnings"] == []


def test_model_router_falls_back_from_primary_to_local(monkeypatch):
    settings = {
        "memory.answer_model": "default",
        "memory.answer_fallback_models": [{"endpoint_id": "endpoint-local", "model": "gemma-4"}],
        "default_endpoint_id": "endpoint-cloud",
    }

    def fake_get_user_setting(key, owner="", default=None):
        return settings.get(key, default)

    def fake_resolve_endpoint(prefix, owner=None):
        return "https://api.deepseek.com/v1/chat/completions", "deepseek-chat", {}

    def fake_resolve_endpoint_by_id(endpoint_id, model, owner=None):
        if endpoint_id == "endpoint-local":
            return "http://localhost:11434/v1/chat/completions", "gemma-4", {}
        return None

    async def fake_llm_call_async(url, model, messages, **kwargs):
        if "deepseek" in url:
            raise HTTPException(504, "upstream timeout")
        return "Local grounded answer."

    monkeypatch.setattr(model_router, "get_user_setting", fake_get_user_setting)
    monkeypatch.setattr(model_router, "resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr(model_router, "resolve_chat_fallback_candidates", lambda owner=None: [])
    monkeypatch.setattr(model_router, "resolve_endpoint_by_id", fake_resolve_endpoint_by_id)
    monkeypatch.setattr(model_router, "get_context_length", lambda url, model: 64000 if "deepseek" in url else 32000)
    monkeypatch.setattr(model_router, "is_local_endpoint", lambda url: "localhost" in url)
    monkeypatch.setattr(model_router, "llm_call_async", fake_llm_call_async)

    result = asyncio.run(
        model_router.synthesize_answer(
            owner="alice",
            query="blob citations",
            citations=[{"path": "Gamma.md", "title": "Gamma", "score": 3, "snippets": ["Blob citations stay grounded."]}],
            requested_mode="auto",
            confidence="high",
        )
    )

    assert result["answer_mode"] == "local"
    assert result["selected_model"] == "gemma-4"
    assert result["selected_endpoint_id"] == "endpoint-local"
    assert result["fallback_reason"] == "provider_timeout"
    assert result["answer"] == "Local grounded answer."


def test_model_router_degrades_to_extractive_when_every_candidate_fails(monkeypatch):
    settings = {
        "memory.answer_model": "default",
        "memory.answer_fallback_models": [],
        "default_endpoint_id": "endpoint-cloud",
    }

    def fake_get_user_setting(key, owner="", default=None):
        return settings.get(key, default)

    async def fake_llm_call_async(url, model, messages, **kwargs):
        raise HTTPException(429, "rate limited")

    monkeypatch.setattr(model_router, "get_user_setting", fake_get_user_setting)
    monkeypatch.setattr(model_router, "resolve_endpoint", lambda prefix, owner=None: ("https://api.deepseek.com/v1/chat/completions", "deepseek-chat", {}))
    monkeypatch.setattr(model_router, "resolve_chat_fallback_candidates", lambda owner=None: [])
    monkeypatch.setattr(model_router, "resolve_endpoint_by_id", lambda endpoint_id, model, owner=None: None)
    monkeypatch.setattr(model_router, "get_context_length", lambda url, model: 64000)
    monkeypatch.setattr(model_router, "is_local_endpoint", lambda url: False)
    monkeypatch.setattr(model_router, "llm_call_async", fake_llm_call_async)

    result = asyncio.run(
        model_router.synthesize_answer(
            owner="alice",
            query="blob citations",
            citations=[{"path": "Gamma.md", "title": "Gamma", "score": 3, "snippets": ["Blob citations stay grounded."]}],
            requested_mode="cloud",
            confidence="high",
        )
    )

    assert result["answer_mode"] == "extractive"
    assert result["selected_model"] == "extractive"
    assert result["fallback_reason"] == "provider_rate_limited"
    assert "provider_rate_limited" in result["model_capability_warnings"]

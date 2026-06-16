import asyncio
import json
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.routes as obsidian_routes
from backend.derived_index import build_derived_index
from backend.query_layer import QUERY_CACHE_PATH, answer_query, query_layer_status


def test_query_layer_answers_with_citations_and_confidence():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Alpha.md"), "w", encoding="utf-8") as f:
            f.write("# Alpha\n\nThe architecture decision keeps the blob cache local.\n")
        with open(os.path.join(tmpdir, "Beta.md"), "w", encoding="utf-8") as f:
            f.write("# Beta\n\nBlob ingestion uses a local review queue and citations.\n")

        build_derived_index(tmpdir)
        status = query_layer_status(tmpdir)
        result = answer_query(tmpdir, "blob citations", top_k=5, answer_mode="extractive")

        assert status["readiness"]["ready"] is True
        assert result["query"] == "blob citations"
        assert result["answer"]
        assert result["answer_mode"] == "extractive"
        assert result["selected_role"] == "memory.answer"
        assert result["selected_model"] == "extractive"
        assert result["selected_endpoint_id"] == ""
        assert result["fallback_reason"] == ""
        assert result["model_context_tokens"] == 0
        assert result["model_capability_warnings"] == []
        assert result["citations"]
        assert result["citations"][0]["path"] in {"Alpha.md", "Beta.md"}
        assert result["confidence"] in {"medium", "high"}
        assert result["confidence_score"] > 0
        assert result["summary"]["matched_chunks"] >= 1
        assert result["summary"]["matched_sources"] >= 1
        assert result["summary"]["cache_hit"] is False
        assert result["readiness_gate"]["state"] == "ready"


def test_query_layer_status_blocks_when_index_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        status = query_layer_status(tmpdir)
        result = answer_query(tmpdir, "anything", top_k=3, answer_mode="extractive")

        assert status["readiness"]["state"] == "not_configured"
        assert status["readiness"]["gaps"] == ["query_index_missing", "query_index_not_ready", "query_index_empty"]
        assert result["answer"] == ""
        assert result["citations"] == []
        assert result["confidence"] == "low"
        assert result["answer_mode"] == "extractive"
        assert result["fallback_reason"] == "query_layer_not_ready"
        assert result["readiness_gate"]["state"] == "blocked"


def test_query_layer_caches_repeated_query_and_tracks_subtree_filter():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "Inbox"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Blob.md"), "w", encoding="utf-8") as f:
            f.write("# Blob\n\nBlob cache result stays in the project subtree.\n")
        with open(os.path.join(tmpdir, "Inbox", "Blob.md"), "w", encoding="utf-8") as f:
            f.write("# Blob\n\nInbox-only blob result.\n")

        build_derived_index(tmpdir)
        first = answer_query(tmpdir, "blob", top_k=5, path_prefix="Projects", answer_mode="extractive")
        second = answer_query(tmpdir, "blob", top_k=5, path_prefix="Projects", answer_mode="extractive")
        status = query_layer_status(tmpdir)
        cache_path = os.path.join(tmpdir, *QUERY_CACHE_PATH.split("/"))

        assert first["citations"]
        assert {item["path"] for item in first["citations"]} == {"Projects/Blob.md"}
        assert first["summary"]["cache_hit"] is False
        assert second["summary"]["cache_hit"] is True
        assert status["cache"]["entries"] >= 1
        assert status["cache"]["hits"] >= 1
        assert status["summary"]["cache_entries"] >= 1
        assert os.path.exists(cache_path)
        with open(cache_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        assert payload["stats"]["hits"] >= 1


@pytest.mark.asyncio
async def test_query_layer_auto_uses_cloud_then_falls_back_to_extractive(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Gamma.md"), "w", encoding="utf-8") as f:
            f.write("# Gamma\n\nBlob citations stay grounded in the retrieved memory chunk.\n")

        build_derived_index(tmpdir)

        async def fake_synthesize_answer(**kwargs):
            return {
                "answer_mode": "cloud",
                "provider": "DeepSeek",
                "selected_role": "memory.answer",
                "selected_model": "deepseek-chat",
                "selected_endpoint_id": "endpoint-cloud",
                "fallback_reason": "",
                "model_context_tokens": 64000,
                "model_capability_warnings": [],
                "warnings": [],
                "answer": "Grounded blob answer.",
            }

        monkeypatch.setattr("backend.query_layer.synthesize_answer", fake_synthesize_answer)
        result = await obsidian_routes.answer_query_async(tmpdir, "blob citations", top_k=5, answer_mode="auto")

        assert result["answer"] == "Grounded blob answer."
        assert result["answer_mode"] == "cloud"
        assert result["selected_role"] == "memory.answer"
        assert result["selected_model"] == "deepseek-chat"
        assert result["selected_endpoint_id"] == "endpoint-cloud"
        assert result["fallback_reason"] == ""
        assert result["model_context_tokens"] == 64000
        assert result["model_capability_warnings"] == []


@pytest.mark.asyncio
async def test_query_layer_marks_low_confidence_auto_as_extractive(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Weak.md"), "w", encoding="utf-8") as f:
            f.write("# Weak\n\nBlob.\n")

        build_derived_index(tmpdir)

        async def fake_synthesize_answer(**kwargs):
            return {
                "answer_mode": "extractive",
                "provider": "",
                "selected_role": "memory.answer",
                "selected_model": "extractive",
                "selected_endpoint_id": "",
                "fallback_reason": "low_confidence_retrieval",
                "model_context_tokens": 0,
                "model_capability_warnings": ["low_confidence_retrieval"],
                "warnings": ["low_confidence_retrieval"],
                "answer": "",
            }

        monkeypatch.setattr("backend.query_layer.synthesize_answer", fake_synthesize_answer)
        result = await obsidian_routes.answer_query_async(tmpdir, "blob", top_k=1, answer_mode="auto")

        assert result["answer_mode"] == "extractive"
        assert result["fallback_reason"] in {"", "low_confidence_retrieval"}


@pytest.mark.asyncio
async def test_query_layer_routes_expose_status_and_answer(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Gamma.md"), "w", encoding="utf-8") as f:
            f.write("# Gamma\n\nThe blob path keeps confidence and source citations.\n")

        build_derived_index(tmpdir)
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(
            obsidian_routes,
            "answer_query_async",
            lambda vault_dir, q, **kwargs: asyncio.sleep(0, result={
                "query": q,
                "path_prefix": "",
                "answer": "Synthesized answer",
                "requested_answer_mode": "auto",
                "answer_mode": "cloud",
                "provider": "DeepSeek",
                "selected_role": "memory.answer",
                "selected_model": "deepseek-chat",
                "selected_endpoint_id": "endpoint-cloud",
                "fallback_reason": "",
                "model_context_tokens": 64000,
                "model_capability_warnings": [],
                "citations": [{"path": "Gamma.md", "title": "Gamma", "score": 2, "source_hash": "", "snippets": ["The blob path keeps confidence and source citations."]}],
                "confidence": "high",
                "confidence_score": 0.9,
                "summary": {"matched_chunks": 1, "matched_sources": 1, "cache_hit": False},
                "readiness": {"ready": True, "state": "ready", "gaps": [], "writes_supported": False},
                "readiness_gate": {"state": "ready"},
                "warnings": [],
            }),
        )

        request = SimpleNamespace(state=SimpleNamespace(api_token=False))
        status = await obsidian_routes.query_layer_status_route(request)
        result = await obsidian_routes.query_layer_route(request, "blob confidence", top_k=5, path_prefix="", answer_mode="auto")

        assert status["readiness"]["ready"] is True
        assert "model_router" in status
        assert result["citations"][0]["path"] == "Gamma.md"
        assert result["summary"]["matched_sources"] == 1
        assert result["answer_mode"] == "cloud"

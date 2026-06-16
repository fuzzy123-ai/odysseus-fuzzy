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
from backend.query_layer import answer_query, query_layer_status


def test_query_layer_answers_with_citations_and_confidence():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Alpha.md"), "w", encoding="utf-8") as f:
            f.write("# Alpha\n\nThe architecture decision keeps the blob cache local.\n")
        with open(os.path.join(tmpdir, "Beta.md"), "w", encoding="utf-8") as f:
            f.write("# Beta\n\nBlob ingestion uses a local review queue and citations.\n")

        build_derived_index(tmpdir)
        status = query_layer_status(tmpdir)
        result = answer_query(tmpdir, "blob citations", top_k=5)

        assert status["readiness"]["ready"] is True
        assert result["query"] == "blob citations"
        assert result["answer"]
        assert result["citations"]
        assert result["citations"][0]["path"] in {"Alpha.md", "Beta.md"}
        assert result["confidence"] in {"medium", "high"}
        assert result["confidence_score"] > 0
        assert result["summary"]["matched_chunks"] >= 1
        assert result["summary"]["matched_sources"] >= 1
        assert result["readiness_gate"]["state"] == "ready"


def test_query_layer_status_blocks_when_index_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        status = query_layer_status(tmpdir)
        result = answer_query(tmpdir, "anything", top_k=3)

        assert status["readiness"]["state"] == "not_configured"
        assert status["readiness"]["gaps"] == ["query_index_missing", "query_index_not_ready", "query_index_empty"]
        assert result["answer"] == ""
        assert result["citations"] == []
        assert result["confidence"] == "low"
        assert result["readiness_gate"]["state"] == "blocked"


@pytest.mark.asyncio
async def test_query_layer_routes_expose_status_and_answer(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Gamma.md"), "w", encoding="utf-8") as f:
            f.write("# Gamma\n\nThe blob path keeps confidence and source citations.\n")

        build_derived_index(tmpdir)
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)

        request = SimpleNamespace(state=SimpleNamespace(api_token=False))
        status = await obsidian_routes.query_layer_status_route(request)
        result = await obsidian_routes.query_layer_route(request, "blob confidence", top_k=5)

        assert status["readiness"]["ready"] is True
        assert result["citations"][0]["path"] == "Gamma.md"
        assert result["summary"]["matched_sources"] == 1

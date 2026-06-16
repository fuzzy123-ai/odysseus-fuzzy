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
from backend.rebuild_proof import REBUILD_PROOF_PATH, rebuild_proof_status, run_rebuild_proof


def test_rebuild_proof_runs_full_pipeline_and_writes_report():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Blob.md"), "w", encoding="utf-8") as f:
            f.write("# Blob\n\nBlob path proof with citations.\n")
        with open(os.path.join(tmpdir, "Notes.md"), "w", encoding="utf-8") as f:
            f.write("# Notes\n\nSecondary source for rebuild proof.\n")

        result = run_rebuild_proof(tmpdir, query="blob citations", top_k=5)
        status = rebuild_proof_status(tmpdir)
        report_path = os.path.join(tmpdir, *REBUILD_PROOF_PATH.split("/"))

        assert os.path.exists(report_path)
        assert result["summary"]["ledger_ready"] is True
        assert result["summary"]["derived_index_ready"] is True
        assert result["summary"]["query_layer_ready"] is True
        assert result["summary"]["source_count"] == 2
        assert result["summary"]["chunk_count"] >= 2
        assert result["summary"]["query_citations"] >= 1
        assert status["configured"] is True
        assert status["summary"]["source_count"] == 2


def test_rebuild_proof_rebuilds_after_change_and_delete():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "Mutable.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Mutable\n\nOriginal proof text.\n")

        first = run_rebuild_proof(tmpdir, query="original", top_k=5)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Mutable\n\nUpdated proof text.\n")
        second = run_rebuild_proof(tmpdir, query="updated", top_k=5)
        os.remove(path)
        third = run_rebuild_proof(tmpdir, query="updated", top_k=5)

        assert first["summary"]["query_citations"] == 1
        assert second["summary"]["query_citations"] == 1
        assert second["query_result"]["citations"][0]["path"] == "Mutable.md"
        assert third["summary"]["source_count"] == 0
        assert third["summary"]["query_citations"] == 0


@pytest.mark.asyncio
async def test_rebuild_proof_routes_expose_status_and_run(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Route.md"), "w", encoding="utf-8") as f:
            f.write("# Route\n\nRoute rebuild proof text.\n")

        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "_require_vault_scope", lambda request, required: "alice")

        request = SimpleNamespace(state=SimpleNamespace(api_token=False))
        before = await obsidian_routes.rebuild_proof_status_route(request)
        after = await obsidian_routes.rebuild_proof_run_route(request, q="route proof", top_k=5)
        status = await obsidian_routes.rebuild_proof_status_route(request)

        assert before["configured"] is False
        assert after["summary"]["derived_index_ready"] is True
        assert status["configured"] is True
        assert status["summary"]["query_citations"] >= 1

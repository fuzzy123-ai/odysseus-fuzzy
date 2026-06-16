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
from backend.derived_index import build_derived_index, derived_index_status, retrieve_derived_chunks
from backend.memory_ledger import memory_ledger_status


def test_derived_index_builds_chunks_graph_and_updates_ledger():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "AI Memory", "Inbox"), exist_ok=True)
        with open(os.path.join(tmpdir, "Canon.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "title: Canon\n"
                "---\n"
                "# Canon\n\nLinks to [[Inbox]].\n\nalpha beta gamma\n" * 20
            )
        with open(os.path.join(tmpdir, "AI Memory", "Inbox", "Inbox.md"), "w", encoding="utf-8") as f:
            f.write("# Inbox\n\nbeta gamma delta\n")
        with open(os.path.join(tmpdir, "Manual.txt"), "w", encoding="utf-8") as f:
            f.write("plain text document\nwith searchable content\n")
        with open(os.path.join(tmpdir, "Binary.pdf"), "wb") as f:
            f.write(b"%PDF-1.7 binary")

        status = build_derived_index(tmpdir, chunk_size=80, overlap=10)
        ledger = memory_ledger_status(tmpdir)

        assert status["configured"] is True
        assert status["summary"]["source_count"] == 4
        assert status["summary"]["chunk_count"] >= 3
        assert status["summary"]["graph_nodes"] == 4
        assert status["summary"]["graph_edges"] >= 1
        assert status["summary"]["pending_sources"] == 0
        assert ledger["summary"]["indexed_sources"] == 4
        assert ledger["summary"]["pending_sources"] == 0
        assert ledger["summary"]["total_chunks"] == status["summary"]["chunk_count"]
        assert any("Binary.pdf" in warning for warning in status["warnings"])

        retrieval = retrieve_derived_chunks(tmpdir, "searchable gamma", top_k=3)
        assert retrieval["summary"]["returned"] >= 1
        assert retrieval["results"][0]["source_path"] in {"Canon.md", "AI Memory/Inbox/Inbox.md", "Manual.txt"}


def test_derived_index_status_detects_dirty_lineage_after_source_change():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Source.md"), "w", encoding="utf-8") as f:
            f.write("# Source\n\nalpha beta\n")

        build_derived_index(tmpdir, chunk_size=80, overlap=10)
        with open(os.path.join(tmpdir, "Source.md"), "w", encoding="utf-8") as f:
            f.write("# Source\n\nalpha beta changed\n")

        dirty = derived_index_status(tmpdir)

        assert dirty["readiness"]["state"] == "dirty"
        assert dirty["readiness"]["gaps"] == ["derived_index_dirty"]
        assert dirty["summary"]["changed_sources"] == 1
        assert dirty["lineage"]["changed_sources"][0]["path"] == "Source.md"


@pytest.mark.asyncio
async def test_derived_index_routes_cover_status_rebuild_and_retrieval(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Source.md"), "w", encoding="utf-8") as f:
            f.write("# Source\n\nroute retrieval demo\n")

        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "_require_vault_scope", lambda request, required: "alice")

        request = SimpleNamespace(state=SimpleNamespace(api_token=False))
        rebuilt = await obsidian_routes.derived_index_rebuild_route(request)
        status = await obsidian_routes.derived_index_route(request)
        retrieval = await obsidian_routes.derived_index_retrieve_route(request, "retrieval", top_k=5)

        assert rebuilt["summary"]["source_count"] == 1
        assert status["configured"] is True
        assert retrieval["summary"]["returned"] == 1
        assert retrieval["results"][0]["source_path"] == "Source.md"

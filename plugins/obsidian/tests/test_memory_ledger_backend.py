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
from backend.memory_ledger import (
    LEDGER_DB_PATH,
    ledger_db_abspath,
    mark_source_failed,
    mark_source_indexed,
    memory_ledger_status,
    sync_memory_ledger,
)


def test_memory_ledger_sync_tracks_create_change_delete_and_source_types():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "AI Memory", "Inbox"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "Files"), exist_ok=True)
        with open(os.path.join(tmpdir, "Note.md"), "w", encoding="utf-8") as f:
            f.write("# Note\n")
        with open(os.path.join(tmpdir, "AI Memory", "Inbox", "Captured.md"), "w", encoding="utf-8") as f:
            f.write("# Captured\n")
        with open(os.path.join(tmpdir, "Files", "Guide.pdf"), "wb") as f:
            f.write(b"%PDF-1.7 demo")
        with open(os.path.join(tmpdir, "Files", "Diagram.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

        first = sync_memory_ledger(tmpdir)
        status = memory_ledger_status(tmpdir)

        assert first["summary"] == {
            "scanned_sources": 4,
            "created": 4,
            "changed": 0,
            "unchanged": 0,
            "deleted": 0,
        }
        assert status["storage"]["db_path"] == ledger_db_abspath(tmpdir)
        assert status["storage"]["db_path"].endswith(LEDGER_DB_PATH.replace("/", os.sep))
        assert status["summary"]["total_sources"] == 4
        assert status["summary"]["status_counts"] == {"pending": 4}
        assert status["summary"]["source_types"] == {
            "attachment": 1,
            "chat_capture": 1,
            "document": 1,
            "markdown": 1,
        }
        assert status["readiness"] == {
            "ready": False,
            "state": "pending",
            "gaps": ["ledger_pending_sources"],
            "writes_supported": True,
        }

        mark_source_indexed(tmpdir, "Note.md", chunk_count=3)
        updated = memory_ledger_status(tmpdir)
        assert updated["summary"]["indexed_sources"] == 1
        assert updated["summary"]["chunked_sources"] == 1
        assert updated["summary"]["total_chunks"] == 3

        with open(os.path.join(tmpdir, "Note.md"), "w", encoding="utf-8") as f:
            f.write("# Note\nchanged\n")
        os.remove(os.path.join(tmpdir, "Files", "Diagram.png"))

        second = sync_memory_ledger(tmpdir)
        changed_status = memory_ledger_status(tmpdir)

        assert second["summary"] == {
            "scanned_sources": 3,
            "created": 0,
            "changed": 1,
            "unchanged": 2,
            "deleted": 1,
        }
        assert second["changed"] == ["Note.md"]
        assert second["deleted"] == [
            {
                "path": "Files/Diagram.png",
                "source_type": "attachment",
                "status": "pending",
                "chunk_count": None,
            }
        ]
        assert changed_status["summary"]["total_sources"] == 3
        assert changed_status["summary"]["status_counts"] == {"pending": 2, "stale": 1}
        note_entry = next(entry for entry in changed_status["entries"] if entry["path"] == "Note.md")
        assert note_entry["status"] == "stale"
        assert note_entry["chunk_count"] is None


def test_memory_ledger_tracks_failed_and_ready_states():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Fact.md"), "w", encoding="utf-8") as f:
            f.write("# Fact\n")

        sync_memory_ledger(tmpdir)
        mark_source_failed(tmpdir, "Fact.md", "Embedding lane unavailable")
        failed = memory_ledger_status(tmpdir)

        assert failed["summary"]["failed_sources"] == 1
        assert failed["readiness"] == {
            "ready": False,
            "state": "failed",
            "gaps": ["ledger_failed_sources"],
            "writes_supported": True,
        }
        assert failed["entries"][0]["last_error"] == "Embedding lane unavailable"

        mark_source_indexed(tmpdir, "Fact.md", chunk_count=1)
        ready = memory_ledger_status(tmpdir)

        assert ready["summary"]["status_counts"] == {"indexed": 1}
        assert ready["summary"]["last_indexed_at"].endswith("Z")
        assert ready["readiness"] == {
            "ready": True,
            "state": "ready",
            "gaps": [],
            "writes_supported": True,
        }


@pytest.mark.asyncio
async def test_memory_ledger_routes_expose_status_and_sync(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Doc.md"), "w", encoding="utf-8") as f:
            f.write("# Doc\n")

        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "_require_vault_scope", lambda request, required: "alice")

        request = SimpleNamespace(state=SimpleNamespace(api_token=False))
        synced = await obsidian_routes.memory_ledger_sync_route(request)
        status = await obsidian_routes.memory_ledger_route(request)

        assert synced["success"] is True
        assert synced["summary"]["created"] == 1
        assert status["summary"]["total_sources"] == 1
        assert status["entries"][0]["path"] == "Doc.md"

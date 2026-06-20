import os
import sys
import tempfile


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.raptor_cache import clear_raptor_cache
from backend.raptor_rebuild import rebuild_raptor_artifacts
from backend.raptor_warming import raptor_cache_warming_status, warm_raptor_cache


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def test_raptor_cache_warming_preloads_status_and_graph(monkeypatch):
    clear_raptor_cache()
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED", "true")
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            os.path.join(tmpdir, "A.md"),
            "---\nstatus: active\ntype: canonical\nupdated: 2026-06-20\n---\n# A\n[[B]]\n",
        )
        _write(
            os.path.join(tmpdir, "B.md"),
            "---\nstatus: active\ntype: canonical\nupdated: 2026-06-20\n---\n# B\n",
        )
        rebuild_raptor_artifacts(tmpdir)

        before = raptor_cache_warming_status(tmpdir)
        warmed = warm_raptor_cache(tmpdir)
        after = raptor_cache_warming_status(tmpdir)

        assert before["pending"] is True
        assert warmed["skipped"] is False
        assert warmed["warmed"] == ["raptor_status", "raptor_graph_view"]
        assert warmed["safety"] == {
            "source_note_writes": False,
            "derived_data_writes_only": True,
            "provider_calls": False,
        }
        assert after["pending"] is False
        assert after["cache"]["entry_count"] >= 2


def test_raptor_cache_warming_can_be_disabled(monkeypatch):
    clear_raptor_cache()
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_CACHE_WARMING_ENABLED", "false")
    with tempfile.TemporaryDirectory() as tmpdir:
        result = warm_raptor_cache(tmpdir)

        assert result == {"skipped": True, "reason": "raptor_cache_warming_disabled", "warmed": []}
        assert raptor_cache_warming_status(tmpdir)["enabled"] is False

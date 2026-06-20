import json
import os
import sys
import tempfile


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.hybrid_retrieval import raptor_status
from backend.raptor_rebuild import (
    RAPTOR_INDEX_PATH,
    RAPTOR_REBUILD_REPORT_PATH,
    RAPTOR_SUMMARIES_PATH,
    rebuild_raptor_artifacts,
)


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _read_json(vault_dir: str, rel_path: str):
    with open(os.path.join(vault_dir, rel_path.replace("/", os.sep)), "r", encoding="utf-8") as handle:
        return json.load(handle)


def test_raptor_rebuild_is_blocked_without_explicit_rebuild_flag(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    monkeypatch.delenv("ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED", raising=False)
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "Canon.md"), "---\ntype: canonical\nupdated: 2026-06-14\n---\n# Canon\n")

        result = rebuild_raptor_artifacts(tmpdir)

        assert result["success"] is False
        assert result["blocked"] is True
        assert result["write_gate"]["gaps"] == ["raptor_rebuild_feature_flag_disabled"]
        assert not os.path.exists(os.path.join(tmpdir, RAPTOR_INDEX_PATH.replace("/", os.sep)))


def test_raptor_rebuild_writes_atomic_derived_artifacts(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED", "true")
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "Canon.md"), "---\ntype: canonical\nupdated: 2026-06-14\n---\n# Canon\nLinks to [[Notes/Peer]].\n")
        _write(os.path.join(tmpdir, "Notes", "Peer.md"), "---\ntype: canonical\nupdated: 2026-06-14\n---\n# Peer\n")
        calls = []
        original_replace = os.replace

        def capture_replace(src, dst):
            calls.append((src, dst))
            original_replace(src, dst)

        monkeypatch.setattr("backend.raptor_rebuild.os.replace", capture_replace)

        result = rebuild_raptor_artifacts(tmpdir, max_edges=1)

        assert result["success"] is True
        assert [path for path in result["artifacts"]] == [
            RAPTOR_INDEX_PATH,
            RAPTOR_SUMMARIES_PATH,
            RAPTOR_REBUILD_REPORT_PATH,
        ]
        assert len(calls) == 3
        assert all(src.endswith(".tmp") for src, _dst in calls)
        assert {os.path.relpath(dst, tmpdir).replace("\\", "/") for _src, dst in calls} == {
            RAPTOR_INDEX_PATH,
            RAPTOR_SUMMARIES_PATH,
            RAPTOR_REBUILD_REPORT_PATH,
        }
        assert not any(name.endswith(".tmp") for name in os.listdir(os.path.join(tmpdir, ".obsidian", "odysseus", "raptor")))


def test_raptor_rebuild_artifacts_do_not_store_raw_note_content_or_host_paths(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED", "true")
    with tempfile.TemporaryDirectory() as tmpdir:
        secret = "SECRET-RAW-CONTENT-DO-NOT-STORE"
        _write(
            os.path.join(tmpdir, "Canon.md"),
            "---\ntitle: Canon\ntype: canonical\nupdated: 2026-06-14\ntags: [safe]\n---\n# Heading\n"
            f"{secret}\nProvider output must not appear here.\n",
        )

        rebuild_raptor_artifacts(tmpdir)
        serialized = "\n".join(
            json.dumps(_read_json(tmpdir, path), sort_keys=True)
            for path in (RAPTOR_INDEX_PATH, RAPTOR_SUMMARIES_PATH, RAPTOR_REBUILD_REPORT_PATH)
        )

        assert secret not in serialized
        assert "Provider output must not appear here" not in serialized
        assert tmpdir.replace("\\", "/") not in serialized.replace("\\", "/")
        assert '"raw_note_content_stored": false' in serialized


def test_raptor_rebuild_respects_superseded_status_and_bounded_graph(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED", "true")
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "Canon.md"), "---\ntype: canonical\nupdated: 2026-06-14\n---\n# Canon\n[[Old]]\n")
        _write(os.path.join(tmpdir, "Old.md"), "---\nstatus: deprecated\nsuperseded_by: Canon.md\n---\n# Old\n")

        result = rebuild_raptor_artifacts(tmpdir, max_edges=0)
        index_payload = _read_json(tmpdir, RAPTOR_INDEX_PATH)

        assert result["summary"]["isolated_sources"] == 1
        assert index_payload["summary"]["status_counts"]["superseded"] == 1
        old_source = next(source for source in index_payload["sources"] if source["path"] == "Old.md")
        assert old_source["status"] == "superseded"
        assert old_source["default_retrieval"] is False
        assert old_source["superseded_by"] == "Canon.md"
        assert index_payload["graph"]["edge_count"] == 1
        assert index_payload["graph"]["stored_edge_count"] == 0
        assert index_payload["graph"]["clipped"] is True


def test_raptor_rebuild_lineage_turns_dirty_after_source_change(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED", "true")
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "Canon.md"), "---\ntype: canonical\nupdated: 2026-06-14\n---\n# Canon\nStable source.\n")

        rebuild_raptor_artifacts(tmpdir)
        clean = raptor_status(tmpdir)
        _write(os.path.join(tmpdir, "Canon.md"), "---\ntype: canonical\nupdated: 2026-06-14\n---\n# Canon\nChanged source.\n")
        dirty = raptor_status(tmpdir)

        assert clean["readiness"]["state"] == "ready"
        assert clean["writes_supported"] is True
        assert dirty["readiness"]["state"] == "dirty"
        assert dirty["readiness"]["gaps"] == ["source_hash_changed"]
        assert dirty["writes_supported"] is False
        assert dirty["lineage"]["dirty_sources"][0]["path"] == "Canon.md"

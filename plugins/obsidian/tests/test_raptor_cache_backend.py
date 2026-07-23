import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend import raptor_cache
from backend.raptor_cache import (
    RAPTOR_INDEX_PATH,
    bounded_raptor_graph_view,
    build_raptor_cache_key,
    cached_raptor_payload,
    clear_raptor_cache,
    notify_raptor_vault_changed,
    raptor_cache_diagnostics,
)
from backend.hybrid_retrieval import raptor_status
from backend.raptor_rebuild import rebuild_raptor_artifacts
from backend import vault_service
from src.memory_runtime_metrics import MemoryRuntimeMetricsRegistry


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def test_cached_raptor_payload_hits_until_mutation_generation_changes():
    clear_raptor_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "A.md"), "# A\n")
        calls = {"count": 0}

        def loader():
            calls["count"] += 1
            return {"value": calls["count"], "summary": {}}

        first = cached_raptor_payload(tmpdir, "status", {}, loader)
        second = cached_raptor_payload(tmpdir, "status", {}, loader)
        time.sleep(0.001)
        _write(os.path.join(tmpdir, "A.md"), "# A changed\n")
        notify_raptor_vault_changed(tmpdir, event="watcher")
        third = cached_raptor_payload(tmpdir, "status", {}, loader)

        assert first["value"] == 1
        assert first["cache"]["hit"] is False
        assert second["value"] == 1
        assert second["cache"]["hit"] is True
        assert third["value"] == 2
        assert third["cache"]["hit"] is False
        assert third["cache"]["result"] == "stale"
        assert calls["count"] == 2


def test_cached_raptor_payload_expires_and_evicts_entries():
    clear_raptor_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "A.md"), "# A\n")
        calls = {"count": 0}

        def loader():
            calls["count"] += 1
            return {"value": calls["count"]}

        cached_raptor_payload(tmpdir, "one", {}, loader, ttl_seconds=0.001)
        time.sleep(0.01)
        stale = cached_raptor_payload(tmpdir, "one", {}, loader, ttl_seconds=0.001)
        cached_raptor_payload(tmpdir, "two", {}, loader, max_entries=1)

        diagnostics = raptor_cache_diagnostics()
        assert stale["value"] == 2
        assert diagnostics["stale"] >= 1
        assert diagnostics["evictions"] >= 1
        assert diagnostics["entry_count"] == 1


def test_clear_raptor_cache_can_target_one_vault():
    clear_raptor_cache()
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        _write(os.path.join(first, "A.md"), "# A\n")
        _write(os.path.join(second, "B.md"), "# B\n")
        cached_raptor_payload(first, "status", {}, lambda: {"vault": "first"})
        cached_raptor_payload(second, "status", {}, lambda: {"vault": "second"})

        result = clear_raptor_cache(first)

        assert result["cleared"] == 1
        assert result["entry_count"] == 1
        assert clear_raptor_cache()["cleared"] == 1


def test_cache_key_uses_metadata_not_raw_note_content():
    clear_raptor_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        secret = "SECRET-RAW-CONTENT-DO-NOT-CACHE"
        _write(os.path.join(tmpdir, "A.md"), f"# A\n\n{secret}\n")

        key = build_raptor_cache_key(tmpdir, "status", {"query": "hello"})

        assert len(key) == 64
        assert secret not in key
        assert tmpdir.replace("\\", "/") not in key


def test_bounded_raptor_graph_view_is_cached_and_clipped():
    clear_raptor_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        graph_path = os.path.join(tmpdir, RAPTOR_INDEX_PATH.replace("/", os.sep))
        payload = {
            "graph": {
                "node_count": 4,
                "edge_count": 4,
                "stored_edge_count": 4,
                "clipped": False,
                "edges": [
                    {"source": "A.md", "target": "B.md", "type": "wiki_link"},
                    {"source": "B.md", "target": "C.md", "type": "wiki_link"},
                    {"source": "C.md", "target": "D.md", "type": "wiki_link"},
                    {"source": "D.md", "target": "A.md", "type": "wiki_link"},
                ],
            }
        }
        _write(graph_path, json.dumps(payload))

        first = bounded_raptor_graph_view(tmpdir, edge_offset=1, limit=2)
        second = bounded_raptor_graph_view(tmpdir, edge_offset=1, limit=2)

        assert first["returned_edge_count"] == 2
        assert first["cursor"] == {"next_edge_offset": 3}
        assert first["clipped"] is True
        assert len(first["edges"]) == 2
        assert first["cache"]["hit"] is False
        assert second["cache"]["hit"] is True


def test_raptor_status_cache_invalidates_when_source_changes(monkeypatch):
    clear_raptor_cache()
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED", "true")
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "A.md"), "---\nstatus: active\ntype: canonical\nupdated: 2026-06-20\n---\n# A\n")
        rebuild_raptor_artifacts(tmpdir)

        first = raptor_status(tmpdir)
        second = raptor_status(tmpdir)
        time.sleep(0.001)
        _write(os.path.join(tmpdir, "A.md"), "---\nstatus: active\ntype: canonical\nupdated: 2026-06-20\n---\n# A changed\n")
        notify_raptor_vault_changed(tmpdir, event="watcher")
        dirty = raptor_status(tmpdir)

        assert first["cache"]["hit"] is False
        assert second["cache"]["hit"] is True
        assert dirty["cache"]["hit"] is False
        assert dirty["readiness"]["state"] == "dirty"
        assert dirty["readiness"]["gaps"] == ["source_hash_changed"]


def test_raptor_rebuild_clears_dynamic_cache(monkeypatch):
    clear_raptor_cache()
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_OBSIDIAN_RAPTOR_REBUILD_ENABLED", "true")
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "A.md"), "---\nstatus: active\ntype: canonical\nupdated: 2026-06-20\n---\n# A\n")
        rebuild_raptor_artifacts(tmpdir)
        assert raptor_status(tmpdir)["cache"]["hit"] is False
        assert raptor_status(tmpdir)["cache"]["hit"] is True

        time.sleep(0.001)
        _write(os.path.join(tmpdir, "A.md"), "---\nstatus: active\ntype: canonical\nupdated: 2026-06-20\n---\n# A changed\n")
        rebuild_raptor_artifacts(tmpdir)
        after_rebuild = raptor_status(tmpdir)

        assert after_rebuild["cache"]["hit"] is False
        assert after_rebuild["readiness"]["state"] == "ready"


def test_warm_hits_do_not_rescan_markdown_sources(monkeypatch):
    clear_raptor_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        for index in range(50):
            _write(os.path.join(tmpdir, f"Note-{index}.md"), f"# Note {index}\n")
        scans = {"count": 0}
        original = raptor_cache.source_signature

        def counted_signature(vault_dir):
            scans["count"] += 1
            return original(vault_dir)

        monkeypatch.setattr(raptor_cache, "source_signature", counted_signature)
        monkeypatch.setattr(raptor_cache, "_CLOCK", lambda: 100.0)

        cached_raptor_payload(tmpdir, "status", {}, lambda: {"value": 1})
        for _ in range(100):
            result = cached_raptor_payload(tmpdir, "status", {}, lambda: {"value": 2})

        assert result["cache"]["hit"] is True
        assert scans["count"] == 1


def test_external_change_fallback_is_detected_at_five_seconds(monkeypatch):
    clear_raptor_cache()
    clock = {"now": 10.0}
    monkeypatch.setattr(raptor_cache, "_CLOCK", lambda: clock["now"])
    with tempfile.TemporaryDirectory() as tmpdir:
        note_path = os.path.join(tmpdir, "A.md")
        _write(note_path, "# A\n")
        calls = {"count": 0}

        def loader():
            calls["count"] += 1
            return {"value": calls["count"]}

        assert cached_raptor_payload(tmpdir, "status", {}, loader)["cache"]["result"] == "miss"
        _write(note_path, "# A changed outside Odysseus\n")
        clock["now"] = 14.999
        assert cached_raptor_payload(tmpdir, "status", {}, loader)["cache"]["result"] == "hit"
        clock["now"] = 15.0
        refreshed = cached_raptor_payload(tmpdir, "status", {}, loader)

        assert refreshed["value"] == 2
        assert refreshed["cache"]["result"] == "stale"


def test_vault_service_write_advances_generation_without_source_scan(monkeypatch):
    clear_raptor_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_service.write_file(tmpdir, "A.md", "# A\n", owner="test", tool="test")
        cached_raptor_payload(tmpdir, "status", {}, lambda: {"value": 1})
        monkeypatch.setattr(
            raptor_cache,
            "source_signature",
            lambda _vault_dir: (_ for _ in ()).throw(AssertionError("unexpected source scan")),
        )

        vault_service.write_file(tmpdir, "A.md", "# A changed\n", owner="test", tool="test")
        refreshed = cached_raptor_payload(tmpdir, "status", {}, lambda: {"value": 2})

        assert refreshed["value"] == 2
        assert refreshed["cache"]["result"] == "stale"


def test_cache_is_thread_safe_and_lru_bounded():
    clear_raptor_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "A.md"), "# A\n")
        barrier = threading.Barrier(8)

        def load(index):
            barrier.wait()
            return cached_raptor_payload(
                tmpdir,
                f"namespace-{index}",
                {},
                lambda: {"value": index},
                max_entries=4,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(load, range(8)))

        assert sorted(result["value"] for result in results) == list(range(8))
        assert raptor_cache_diagnostics()["entry_count"] == 4


def test_cache_evicts_least_recently_used_variant():
    clear_raptor_cache()
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "A.md"), "# A\n")
        loads = {"one": 0, "two": 0, "three": 0}

        def load(name):
            loads[name] += 1
            return {"value": name}

        cached_raptor_payload(tmpdir, "one", {}, lambda: load("one"), max_entries=2)
        cached_raptor_payload(tmpdir, "two", {}, lambda: load("two"), max_entries=2)
        assert cached_raptor_payload(
            tmpdir, "one", {}, lambda: load("one"), max_entries=2
        )["cache"]["result"] == "hit"
        cached_raptor_payload(tmpdir, "three", {}, lambda: load("three"), max_entries=2)

        assert cached_raptor_payload(
            tmpdir, "one", {}, lambda: load("one"), max_entries=2
        )["cache"]["result"] == "hit"
        assert cached_raptor_payload(
            tmpdir, "two", {}, lambda: load("two"), max_entries=2
        )["cache"]["result"] == "miss"
        assert loads == {"one": 1, "two": 2, "three": 1}


def test_cache_metrics_cover_hit_miss_stale_and_eviction(monkeypatch):
    clear_raptor_cache()
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    monkeypatch.setattr(raptor_cache, "get_memory_runtime_metrics_registry", lambda: registry)
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(os.path.join(tmpdir, "A.md"), "# A\n")
        cached_raptor_payload(tmpdir, "one", {}, lambda: {"value": 1}, max_entries=1)
        cached_raptor_payload(tmpdir, "one", {}, lambda: {"value": 2}, max_entries=1)
        notify_raptor_vault_changed(tmpdir, event="watcher")
        cached_raptor_payload(tmpdir, "one", {}, lambda: {"value": 3}, max_entries=1)
        cached_raptor_payload(tmpdir, "two", {}, lambda: {"value": 4}, max_entries=1)

    samples = {
        dict(sample.labels)["cache_result"]: sample.value
        for sample in registry.snapshot().samples
        if sample.name == "odysseus_raptor_cache_requests_total"
    }
    assert samples == {"hit": 1.0, "miss": 2.0, "stale": 1.0, "evicted": 1.0}

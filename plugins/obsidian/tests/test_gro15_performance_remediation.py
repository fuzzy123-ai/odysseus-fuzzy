import os
import sys
import tempfile


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv(
    "ODYSSEUS_ROOT",
    os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")),
)

for _path in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from backend import derived_index, memory_status, query_layer, raptor_cache


def _write_source(vault_dir: str, body: str = "alpha synthetic relationship") -> str:
    path = os.path.join(vault_dir, "Source.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"# Source\n\n{body}\n")
    return path


def test_derived_status_reuses_hashes_but_detects_direct_source_change(monkeypatch):
    with tempfile.TemporaryDirectory() as vault_dir:
        source_path = _write_source(vault_dir)
        derived_index.build_derived_index(vault_dir)
        derived_index._clear_derived_status_cache(vault_dir)

        original_hash = derived_index._sha256_file
        calls = {"hashes": 0}

        def counted_hash(*args, **kwargs):
            calls["hashes"] += 1
            return original_hash(*args, **kwargs)

        monkeypatch.setattr(derived_index, "_sha256_file", counted_hash)
        first = derived_index.derived_index_status(vault_dir)
        hashes_after_first = calls["hashes"]
        second = derived_index.derived_index_status(vault_dir)

        assert first["readiness"]["state"] == "ready"
        assert second == first
        assert hashes_after_first == 1
        assert calls["hashes"] == hashes_after_first

        with open(source_path, "a", encoding="utf-8") as handle:
            handle.write("changed\n")
        dirty = derived_index.derived_index_status(vault_dir)

        assert dirty["readiness"]["state"] == "dirty"
        assert dirty["summary"]["changed_sources"] == 1
        assert calls["hashes"] == hashes_after_first + 1


def test_query_warm_hit_skips_derived_status_and_retrieval(monkeypatch):
    with tempfile.TemporaryDirectory() as vault_dir:
        _write_source(vault_dir)
        derived_index.build_derived_index(vault_dir)
        first = query_layer.answer_query(
            vault_dir,
            "synthetic relationship",
            top_k=5,
            answer_mode="extractive",
        )

        def forbidden(*_args, **_kwargs):
            raise AssertionError("warm query cache hit entered an O(N) backend path")

        monkeypatch.setattr(query_layer, "derived_index_status", forbidden)
        monkeypatch.setattr(query_layer, "retrieve_derived_chunks", forbidden)
        second = query_layer.answer_query(
            vault_dir,
            "synthetic relationship",
            top_k=5,
            answer_mode="extractive",
        )

        assert first["summary"]["cache_hit"] is False
        assert second["summary"]["cache_hit"] is True
        assert second["query"] == "synthetic relationship"


def test_query_generation_changes_immediately_on_watcher_signal():
    with tempfile.TemporaryDirectory() as vault_dir:
        _write_source(vault_dir)
        derived_index.build_derived_index(vault_dir)
        raptor_cache.clear_raptor_cache(vault_dir)

        before = query_layer._query_cache_generation(vault_dir)
        raptor_cache.notify_raptor_vault_changed(vault_dir, event="test_watcher")
        after = query_layer._query_cache_generation(vault_dir)

        assert len(before) == 64
        assert len(after) == 64
        assert after != before


def test_query_generation_detects_unannounced_document_change_within_five_seconds(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(query_layer, "_QUERY_GENERATION_CLOCK", lambda: clock["now"])
    with tempfile.TemporaryDirectory() as vault_dir:
        path = os.path.join(vault_dir, "Source.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("synthetic document")
        query_layer._clear_query_generation_state(vault_dir)
        raptor_cache.clear_raptor_cache(vault_dir)

        before = query_layer._query_cache_generation(vault_dir)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(" changed")
        clock["now"] += 4.999
        bounded_stale = query_layer._query_cache_generation(vault_dir)
        clock["now"] += 0.002
        after = query_layer._query_cache_generation(vault_dir)

        assert bounded_stale == before
        assert after != before


def test_memory_status_snapshot_is_bounded_and_watcher_invalidated(monkeypatch):
    with tempfile.TemporaryDirectory() as vault_dir:
        _write_source(vault_dir)
        derived_index.build_derived_index(vault_dir)
        memory_status._clear_memory_status_cache(vault_dir)
        raptor_cache.clear_raptor_cache(vault_dir)

        original_impl = memory_status._memory_status_impl
        calls = {"snapshots": 0}

        def counted_impl(*args, **kwargs):
            calls["snapshots"] += 1
            return original_impl(*args, **kwargs)

        monkeypatch.setattr(memory_status, "_memory_status_impl", counted_impl)
        first = memory_status.memory_status(vault_dir)
        second = memory_status.memory_status(vault_dir)

        assert first == second
        assert calls["snapshots"] == 1
        assert memory_status.MEMORY_STATUS_CACHE_TTL_SECONDS == 5.0
        assert memory_status._MEMORY_STATUS_CACHE_MAX_VAULTS == 32

        raptor_cache.notify_raptor_vault_changed(vault_dir, event="test_watcher")
        third = memory_status.memory_status(vault_dir)

        assert third["summary"]["derived_index_sources"] == 1
        assert calls["snapshots"] == 2

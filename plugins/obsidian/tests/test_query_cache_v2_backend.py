import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
import tempfile

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv(
    "ODYSSEUS_ROOT",
    os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")),
)

for _path in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from backend import query_layer


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def _derived(vault_dir, built_at="2026-07-18T12:00:00Z"):
    _write(
        os.path.join(vault_dir, query_layer.DERIVED_INDEX_PATH.replace("/", os.sep)),
        json.dumps({"built_at": built_at, "chunks": []}),
    )
    return {"built_at": built_at, "readiness": {"ready": True}}


def _extractive(query):
    return {
        "query": query,
        "path_prefix": "",
        "answer": "Grounded synthetic answer.",
        "requested_answer_mode": "auto",
        "answer_mode": "extractive",
        "provider": "",
        "selected_role": "memory.answer",
        "selected_model": "extractive",
        "selected_endpoint_id": "",
        "fallback_reason": "",
        "model_context_tokens": 0,
        "model_capability_warnings": [],
        "citations": [{"path": "Synthetic.md", "title": "Synthetic", "snippets": []}],
        "confidence": "medium",
        "confidence_score": 0.5,
        "summary": {"matched_chunks": 1, "matched_sources": 1, "cache_hit": False},
        "readiness": {"ready": True, "state": "ready", "gaps": []},
        "readiness_gate": {"state": "ready"},
        "warnings": [],
    }


def test_cache_hit_precedes_retrieval_and_never_rewrites_or_persists_query(monkeypatch):
    secret_query = "SECRET-QUERY-DO-NOT-PERSIST"
    with tempfile.TemporaryDirectory() as vault_dir:
        derived = _derived(vault_dir)
        calls = {"retrieve": 0, "synthesize": 0}
        monkeypatch.setattr(query_layer, "derived_index_status", lambda _vault: derived)

        def fake_extractive(_vault, query, **_kwargs):
            calls["retrieve"] += 1
            return _extractive(query)

        async def fake_synthesis(**_kwargs):
            calls["synthesize"] += 1
            return {
                "answer_mode": "extractive",
                "provider": "",
                "selected_role": "memory.answer",
                "selected_model": "extractive",
                "selected_endpoint_id": "",
                "fallback_reason": "",
                "model_context_tokens": 0,
                "model_capability_warnings": [],
                "warnings": [],
                "answer": "",
            }

        monkeypatch.setattr(query_layer, "_extractive_result", fake_extractive)
        monkeypatch.setattr(query_layer, "synthesize_answer", fake_synthesis)

        first = asyncio.run(query_layer._answer_query_async_impl(vault_dir, secret_query))
        cache_path = query_layer._cache_abspath(vault_dir)
        before = _read_bytes(cache_path)
        before_mtime = os.stat(cache_path).st_mtime_ns
        second = asyncio.run(query_layer._answer_query_async_impl(vault_dir, secret_query))
        after = _read_bytes(cache_path)

        assert first["summary"]["cache_hit"] is False
        assert second["summary"]["cache_hit"] is True
        assert second["query"] == secret_query
        assert calls == {"retrieve": 1, "synthesize": 1}
        assert before == after
        assert before_mtime == os.stat(cache_path).st_mtime_ns
        assert secret_query.encode("utf-8") not in after
        payload = json.loads(after)
        assert payload["schema"] == query_layer.QUERY_CACHE_SCHEMA_VERSION
        assert all(len(key) == 64 for key in payload["entries"])
        assert all("query" not in entry for entry in payload["entries"].values())


def test_cache_ttl_expires_after_seven_days(monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(query_layer, "_WALL_CLOCK", lambda: clock["now"])
    with tempfile.TemporaryDirectory() as vault_dir:
        key = "a" * 64
        query_layer._cache_store(vault_dir, key, _extractive("ephemeral"))
        clock["now"] += query_layer.QUERY_CACHE_TTL_SECONDS
        assert query_layer._cache_lookup(vault_dir, key) is not None
        clock["now"] += 0.001
        assert query_layer._cache_lookup(vault_dir, key) is None
        assert query_layer._load_cache(vault_dir)["entries"] == {}


def test_cache_uses_lru_and_enforces_entry_and_byte_bounds(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(query_layer, "_WALL_CLOCK", lambda: clock["now"])
    monkeypatch.setattr(query_layer, "QUERY_CACHE_MAX_ENTRIES", 3)
    with tempfile.TemporaryDirectory() as vault_dir:
        keys = [character * 64 for character in "abcd"]
        for key in keys[:3]:
            query_layer._cache_store(vault_dir, key, _extractive(key[0]))
            clock["now"] += 1.0
        assert query_layer._cache_lookup(vault_dir, keys[0]) is not None
        clock["now"] += 1.0
        query_layer._cache_store(vault_dir, keys[3], _extractive("d"))
        entries = query_layer._load_cache(vault_dir)["entries"]
        assert set(entries) == {keys[0], keys[2], keys[3]}

        monkeypatch.setattr(query_layer, "QUERY_CACHE_MAX_BYTES", 900)
        query_layer._cache_store(
            vault_dir,
            "e" * 64,
            {**_extractive("e"), "answer": "x" * 2_000},
        )
        payload = query_layer._load_cache(vault_dir)
        assert query_layer._serialized_cache_bytes(payload) <= 900
        assert os.path.getsize(query_layer._cache_abspath(vault_dir)) <= 900
        assert len(payload["entries"]) <= 3


def test_atomic_replace_failure_preserves_previous_cache(monkeypatch):
    with tempfile.TemporaryDirectory() as vault_dir:
        query_layer._cache_store(vault_dir, "a" * 64, _extractive("first"))
        cache_path = query_layer._cache_abspath(vault_dir)
        before = _read_bytes(cache_path)

        def fail_replace(_source, _target):
            raise OSError("synthetic replace failure")

        monkeypatch.setattr(query_layer.os, "replace", fail_replace)
        with pytest.raises(OSError, match="synthetic replace failure"):
            query_layer._cache_store(vault_dir, "b" * 64, _extractive("second"))

        assert _read_bytes(cache_path) == before
        assert not [name for name in os.listdir(os.path.dirname(cache_path)) if ".tmp-" in name]


def test_parallel_stores_keep_valid_bounded_json(monkeypatch):
    monkeypatch.setattr(query_layer, "QUERY_CACHE_MAX_ENTRIES", 32)
    with tempfile.TemporaryDirectory() as vault_dir:
        keys = [hashlib.sha256(str(index).encode("ascii")).hexdigest() for index in range(64)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(
                executor.map(
                    lambda item: query_layer._cache_store(
                        vault_dir, item[1], _extractive(f"synthetic-{item[0]}")
                    ),
                    enumerate(keys),
                )
            )

        with open(query_layer._cache_abspath(vault_dir), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        assert payload["schema"] == query_layer.QUERY_CACHE_SCHEMA_VERSION
        assert len(payload["entries"]) == 32
        assert not [name for name in os.listdir(os.path.dirname(query_layer._cache_abspath(vault_dir))) if ".tmp-" in name]


def test_v1_cache_migrates_atomically_to_hashed_query_free_v2(monkeypatch):
    clock = 1_752_840_000.0
    monkeypatch.setattr(query_layer, "_WALL_CLOCK", lambda: clock)
    secret_query = "MIGRATE-THIS-SECRET-QUERY"
    with tempfile.TemporaryDirectory() as vault_dir:
        built_at = "2025-07-18T12:00:00Z"
        derived = _derived(vault_dir, built_at=built_at)
        legacy_key = json.dumps(
            {
                "query": secret_query.lower(),
                "top_k": 5,
                "path_prefix": "",
                "answer_mode": "auto",
                "derived_built_at": built_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        legacy = {
            "entries": {
                legacy_key: {
                    **_extractive(secret_query),
                    "cached_at": datetime.fromtimestamp(clock, timezone.utc).isoformat(),
                }
            },
            "stats": {"hits": 7, "misses": 3},
        }
        cache_path = query_layer._cache_abspath(vault_dir)
        _write(cache_path, json.dumps(legacy))

        migrated = query_layer._load_cache(vault_dir)
        disk = _read_bytes(cache_path)
        key = query_layer._cache_key(vault_dir, secret_query, 5, "", "auto", derived)

        assert migrated["schema"] == query_layer.QUERY_CACHE_SCHEMA_VERSION
        assert migrated["legacy_stats"] == {"hits": 7, "misses": 3}
        assert list(migrated["entries"]) == [key]
        assert query_layer._cache_lookup(vault_dir, key) is not None
        assert secret_query.encode("utf-8") not in disk
        assert b'"query"' not in disk


def test_production_query_cache_limits_match_contract():
    assert query_layer.QUERY_CACHE_TTL_SECONDS == 7 * 24 * 60 * 60
    assert query_layer.QUERY_CACHE_MAX_ENTRIES == 512
    assert query_layer.QUERY_CACHE_MAX_BYTES == 8 * 1024 * 1024

"""Deterministic contract tests for the process-local LLM response cache."""

import json
import threading
import time

import pytest

from src.llm_response_cache import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_TTL_SECONDS,
    LLMResponseCache,
    MAX_TTL_SECONDS,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_defaults_and_ttl_configuration_boundary():
    cache = LLMResponseCache()

    assert cache.max_entries == DEFAULT_MAX_ENTRIES == 128
    assert cache.ttl_seconds == DEFAULT_TTL_SECONDS == 300.0
    assert LLMResponseCache(ttl_seconds=MAX_TTL_SECONDS).ttl_seconds == 3600.0
    with pytest.raises(ValueError, match="at most 3600"):
        LLMResponseCache(ttl_seconds=MAX_TTL_SECONDS + 0.1)


def test_hit_promotes_exact_least_recently_used_entry():
    cache = LLMResponseCache(max_entries=2)
    cache.set("first", "a")
    cache.set("second", "b")

    assert cache.get("first") == "a"
    cache.set("third", "c")

    assert cache.get("second") is None
    assert cache.get("first") == "a"
    assert cache.get("third") == "c"
    assert cache.metrics()["evictions"] == 1


def test_overwrite_promotes_and_refreshes_ttl():
    clock = _Clock()
    cache = LLMResponseCache(max_entries=2, ttl_seconds=10, clock=clock)
    cache.set("first", "old")
    cache.set("second", "b")
    clock.now = 5

    cache.set("first", "new")
    cache.set("third", "c")

    assert cache.get("second") is None
    assert cache.get("first") == "new"
    clock.now = 14.999
    assert cache.get("first") == "new"
    clock.now = 15
    assert cache.get("first") is None


def test_entry_129_evicts_exact_lru_without_overflow():
    cache = LLMResponseCache()
    for index in range(128):
        cache.set(f"key-{index}", f"response-{index}")
        assert len(cache) <= 128

    assert cache.get("key-0") == "response-0"
    cache.set("key-128", "response-128")

    assert len(cache) == 128
    assert cache.get("key-1") is None
    assert cache.get("key-0") == "response-0"
    assert cache.get("key-128") == "response-128"
    assert cache.metrics()["evictions"] == 1


def test_expired_response_is_removed_and_never_returned():
    clock = _Clock()
    cache = LLMResponseCache(ttl_seconds=10, clock=clock)
    cache.set("secret-key", "secret-response")
    clock.now = 10

    assert cache.get("secret-key") is None
    assert len(cache) == 0
    assert cache.metrics()["expirations"] == 1
    assert cache.metrics()["misses"] == 1


def test_metrics_are_content_free():
    clock = _Clock()
    cache = LLMResponseCache(max_entries=1, ttl_seconds=1, clock=clock)
    cache.set("PRIVATE REQUEST KEY", "PRIVATE RESPONSE BODY")
    assert cache.get("PRIVATE REQUEST KEY") == "PRIVATE RESPONSE BODY"
    cache.set("another-private-key", "another-private-response")
    assert cache.get("missing-private-key") is None
    clock.now = 1

    metrics = cache.metrics()
    encoded = json.dumps(metrics, sort_keys=True)
    assert set(metrics) == {
        "hits",
        "misses",
        "expirations",
        "evictions",
        "current_size",
        "max_entries",
        "ttl_seconds",
    }
    assert "PRIVATE" not in encoded
    assert "key" not in encoded.lower()
    assert "response" not in encoded.lower()
    assert metrics["hits"] == 1
    assert metrics["misses"] == 1
    assert metrics["expirations"] == 1
    assert metrics["evictions"] == 1


def test_32_thread_stress_is_capacity_exact_and_exception_free():
    thread_count = 32
    rounds = 8
    cache = LLMResponseCache()
    round_start = threading.Barrier(thread_count)
    round_end = threading.Barrier(thread_count)
    observations: list[int] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def worker(worker_id: int) -> None:
        try:
            for round_id in range(rounds):
                round_start.wait()
                cache.set(f"{round_id}:{worker_id}", f"value-{round_id}:{worker_id}")
                size = len(cache)
                with results_lock:
                    observations.append(size)
                round_end.wait()
        except BaseException as exc:  # pragma: no cover - failure diagnostics
            with results_lock:
                errors.append(exc)
            round_start.abort()
            round_end.abort()

    threads = [
        threading.Thread(target=worker, args=(index,), daemon=True)
        for index in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 10
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))

    assert not [thread for thread in threads if thread.is_alive()]
    assert errors == []
    assert len(observations) == thread_count * rounds
    assert max(observations) <= DEFAULT_MAX_ENTRIES
    assert len(cache) == DEFAULT_MAX_ENTRIES
    assert cache.metrics()["evictions"] == (thread_count * rounds) - DEFAULT_MAX_ENTRIES

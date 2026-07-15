"""Regression tests for llm_core's process-shared runtime state (issue #659).

The synchronous llm_call() runs inside FastAPI's threadpool while
llm_call_async() runs on the event loop. The response cache therefore owns a
locked transaction boundary, and the dead-host maps retain their separate lock.
"""

import threading
import time

from src import llm_core
from src.llm_response_cache import LLMResponseCache


def test_llm_core_cache_wrappers_use_exact_lru_cache():
    original = llm_core._response_cache
    cache = LLMResponseCache(max_entries=2)
    llm_core._response_cache = cache
    activity_token = llm_core._ai_activity_cache_hit.set(False)
    try:
        llm_core._set_cached_response("first", "a")
        llm_core._set_cached_response("second", "b")
        assert llm_core._get_cached_response("first") == "a"
        llm_core._set_cached_response("third", "c")

        assert llm_core._get_cached_response("second") is None
        assert llm_core._get_cached_response("first") == "a"
        assert llm_core._get_cached_response("third") == "c"
        assert len(cache) == 2
    finally:
        llm_core._ai_activity_cache_hit.reset(activity_token)
        llm_core._response_cache = original


def test_host_fail_counter_has_no_lost_updates():
    """Concurrent _mark_host_dead calls must each count exactly once.

    A SlowGetDict widens the read-modify-write window so the unguarded
    get()+1+set() loses every update but one; the lock serializes them.
    """
    url = "http://race.example:1234/v1/chat/completions"
    key = llm_core._host_key(url)

    class SlowGetDict(dict):
        def get(self, *args, **kwargs):
            value = super().get(*args, **kwargs)
            time.sleep(0.01)  # widen the gap between the read and the caller's write
            return value

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    original_fails = llm_core._host_fails
    original_threshold = llm_core._HOST_FAIL_THRESHOLD
    llm_core._host_fails = SlowGetDict()
    llm_core._HOST_FAIL_THRESHOLD = 10 ** 9  # never cool: every call is a pure +1
    try:
        def worker():
            barrier.wait()  # all threads enter the read window together
            llm_core._mark_host_dead(url)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert dict.get(llm_core._host_fails, key) == n_threads
    finally:
        llm_core._host_fails = original_fails
        llm_core._HOST_FAIL_THRESHOLD = original_threshold

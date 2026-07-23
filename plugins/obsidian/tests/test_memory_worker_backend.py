import asyncio
import os
import sys
import tempfile
import threading

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv(
    "ODYSSEUS_ROOT",
    os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")),
)
for _path in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from backend import memory_worker
from backend.memory_worker import MemoryWorkerLane, MemoryWorkerQueueFull, run_memory_work
from src.memory_runtime_metrics import MemoryRuntimeMetricsRegistry


async def _wait_until(predicate, attempts=100):
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached")


@pytest.mark.asyncio
async def test_lane_is_bounded_and_emits_exact_queue_depth(monkeypatch):
    registry = MemoryRuntimeMetricsRegistry.for_tests()
    monkeypatch.setattr(memory_worker, "get_memory_runtime_metrics_registry", lambda: registry)
    lane = MemoryWorkerLane(read_concurrency=1, queue_limit=1)
    release = threading.Event()
    first = asyncio.create_task(lane.run("query", "read", release.wait))
    await _wait_until(lambda: lane.snapshot()["active_readers"] == 1)
    second = asyncio.create_task(lane.run("query", "read", lambda: "second"))
    await _wait_until(lambda: lane.snapshot()["waiting_total"] == 1)

    with pytest.raises(MemoryWorkerQueueFull):
        await lane.run("query", "read", lambda: "overflow")

    queued = [
        sample
        for sample in registry.snapshot().samples
        if sample.name == "odysseus_memory_worker_queue_depth"
    ]
    assert queued[-1].value == 1
    release.set()
    assert await second == "second"
    await first
    completed = [
        sample
        for sample in registry.snapshot().samples
        if sample.name == "odysseus_memory_worker_queue_depth"
    ]
    assert completed[-1].value == 0


@pytest.mark.asyncio
async def test_writer_is_exclusive_and_prevents_reader_barging():
    lane = MemoryWorkerLane(read_concurrency=2, queue_limit=4)
    release_first = threading.Event()
    order = []

    def first_read():
        order.append("read-1-start")
        release_first.wait()
        order.append("read-1-end")

    first = asyncio.create_task(lane.run("memory_status", "read", first_read))
    await _wait_until(lambda: lane.snapshot()["active_readers"] == 1)
    writer = asyncio.create_task(
        lane.run("rebuild", "write", lambda: order.append("write"))
    )
    await _wait_until(lambda: lane.snapshot()["waiting_writers"] == 1)
    second = asyncio.create_task(
        lane.run("memory_status", "read", lambda: order.append("read-2"))
    )
    await _wait_until(lambda: lane.snapshot()["waiting_total"] == 2)

    release_first.set()
    await asyncio.gather(first, writer, second)
    assert order == ["read-1-start", "read-1-end", "write", "read-2"]


@pytest.mark.asyncio
async def test_cancellation_defers_conflict_release_until_thread_finishes():
    lane = MemoryWorkerLane(read_concurrency=1, queue_limit=4)
    started = threading.Event()
    release = threading.Event()

    def slow_write():
        started.set()
        release.wait()
        return "finished"

    write_task = asyncio.create_task(lane.run("rebuild", "write", slow_write))
    await asyncio.to_thread(started.wait)
    write_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await write_task
    assert lane.snapshot()["writer_active"] is True

    read_task = asyncio.create_task(
        lane.run("memory_status", "read", lambda: "read-after-write")
    )
    await _wait_until(lambda: lane.snapshot()["waiting_total"] == 1)
    assert not read_task.done()
    release.set()
    assert await read_task == "read-after-write"
    await _wait_until(lambda: lane.snapshot()["writer_active"] is False)


@pytest.mark.asyncio
async def test_different_vaults_use_independent_non_global_lanes():
    with tempfile.TemporaryDirectory() as first_vault, tempfile.TemporaryDirectory() as second_vault:
        first_started = threading.Event()
        second_started = threading.Event()
        release = threading.Event()

        def blocked(started):
            started.set()
            release.wait()

        first = asyncio.create_task(
            run_memory_work(first_vault, "rebuild", "write", blocked, first_started)
        )
        second = asyncio.create_task(
            run_memory_work(second_vault, "rebuild", "write", blocked, second_started)
        )
        await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(first_started.wait),
                asyncio.to_thread(second_started.wait),
            ),
            timeout=1.0,
        )
        release.set()
        await asyncio.gather(first, second)


def test_worker_limits_and_labels_are_fixed():
    assert memory_worker.DEFAULT_MEMORY_WORKER_READ_CONCURRENCY == 4
    assert memory_worker.DEFAULT_MEMORY_WORKER_QUEUE_LIMIT == 32
    assert memory_worker.MEMORY_WORKER_OPERATIONS == {
        "query",
        "memory_status",
        "raptor_status",
        "rebuild",
        "cache_lookup",
        "automation",
    }

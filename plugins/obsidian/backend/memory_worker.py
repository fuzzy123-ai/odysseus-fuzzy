"""Per-vault bounded worker lanes for synchronous Memory and RAPTOR work."""

from __future__ import annotations

import asyncio
from collections import defaultdict
import os
import threading
from typing import Any, Callable, Literal, TypeVar
from weakref import WeakKeyDictionary

from src.memory_runtime_metrics import get_memory_runtime_metrics_registry


DEFAULT_MEMORY_WORKER_READ_CONCURRENCY = 4
DEFAULT_MEMORY_WORKER_QUEUE_LIMIT = 32
MEMORY_WORKER_OPERATIONS = frozenset(
    {"query", "memory_status", "raptor_status", "rebuild", "cache_lookup", "automation"}
)
MemoryAccess = Literal["read", "write"]
_Result = TypeVar("_Result")


class MemoryWorkerQueueFull(RuntimeError):
    """Raised before submission when one vault's bounded wait queue is full."""


class MemoryWorkerLane:
    """A fair per-vault reader/writer lane backed by asyncio.to_thread."""

    def __init__(
        self,
        *,
        read_concurrency: int = DEFAULT_MEMORY_WORKER_READ_CONCURRENCY,
        queue_limit: int = DEFAULT_MEMORY_WORKER_QUEUE_LIMIT,
    ) -> None:
        if isinstance(read_concurrency, bool) or int(read_concurrency) < 1:
            raise ValueError("read_concurrency must be at least one")
        if isinstance(queue_limit, bool) or int(queue_limit) < 1:
            raise ValueError("queue_limit must be at least one")
        self._read_concurrency = int(read_concurrency)
        self._queue_limit = int(queue_limit)
        self._condition = asyncio.Condition()
        self._active_readers = 0
        self._writer_active = False
        self._waiting_total = 0
        self._waiting_writers = 0
        self._waiting_by_operation: dict[str, int] = defaultdict(int)
        self._cleanup_tasks: set[asyncio.Task[Any]] = set()

    @property
    def queue_limit(self) -> int:
        return self._queue_limit

    def snapshot(self) -> dict[str, Any]:
        return {
            "active_readers": self._active_readers,
            "writer_active": self._writer_active,
            "waiting_total": self._waiting_total,
            "waiting_writers": self._waiting_writers,
            "waiting_by_operation": dict(self._waiting_by_operation),
            "read_concurrency": self._read_concurrency,
            "queue_limit": self._queue_limit,
        }

    async def run(
        self,
        operation: str,
        access: MemoryAccess,
        function: Callable[..., _Result],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _Result:
        normalized_operation = _validate_operation(operation)
        normalized_access = _validate_access(access)
        await self._acquire(normalized_operation, normalized_access)
        worker_task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        release_deferred = False
        try:
            return await asyncio.shield(worker_task)
        except asyncio.CancelledError:
            release_deferred = True
            cleanup = asyncio.create_task(
                self._release_after_worker(worker_task, normalized_access)
            )
            self._cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(self._cleanup_tasks.discard)
            raise
        finally:
            if not release_deferred:
                await self._release(normalized_access)

    async def _acquire(self, operation: str, access: MemoryAccess) -> None:
        async with self._condition:
            if self._can_start(access):
                self._activate(access)
                return
            if self._waiting_total >= self._queue_limit:
                raise MemoryWorkerQueueFull("memory worker queue is full")
            self._waiting_total += 1
            self._waiting_by_operation[operation] += 1
            if access == "write":
                self._waiting_writers += 1
            self._record_queue_depth(operation)
            try:
                await self._condition.wait_for(lambda: self._can_start(access))
            except BaseException:
                self._finish_wait(operation, access)
                self._condition.notify_all()
                raise
            self._finish_wait(operation, access)
            self._activate(access)

    async def _release(self, access: MemoryAccess) -> None:
        async with self._condition:
            if access == "write":
                self._writer_active = False
            else:
                self._active_readers = max(0, self._active_readers - 1)
            self._condition.notify_all()

    async def _release_after_worker(
        self,
        worker_task: asyncio.Task[Any],
        access: MemoryAccess,
    ) -> None:
        try:
            await worker_task
        except BaseException:
            pass
        finally:
            await self._release(access)

    def _can_start(self, access: MemoryAccess) -> bool:
        if access == "write":
            return not self._writer_active and self._active_readers == 0
        return (
            not self._writer_active
            and self._waiting_writers == 0
            and self._active_readers < self._read_concurrency
        )

    def _activate(self, access: MemoryAccess) -> None:
        if access == "write":
            self._writer_active = True
        else:
            self._active_readers += 1

    def _finish_wait(self, operation: str, access: MemoryAccess) -> None:
        self._waiting_total = max(0, self._waiting_total - 1)
        self._waiting_by_operation[operation] = max(
            0, self._waiting_by_operation.get(operation, 0) - 1
        )
        if self._waiting_by_operation[operation] == 0:
            self._waiting_by_operation.pop(operation, None)
        if access == "write":
            self._waiting_writers = max(0, self._waiting_writers - 1)
        self._record_queue_depth(operation)

    def _record_queue_depth(self, operation: str) -> None:
        try:
            component = (
                "raptorgraph"
                if operation in {"raptor_status", "rebuild", "cache_lookup"}
                else "memory"
            )
            get_memory_runtime_metrics_registry().set_gauge(
                "odysseus_memory_worker_queue_depth",
                {"component": component, "operation": operation, "runtime": "worker"},
                self._waiting_by_operation.get(operation, 0),
            )
        except Exception:
            pass


_LANES_LOCK = threading.RLock()
_LANES_BY_LOOP: WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, MemoryWorkerLane]
] = WeakKeyDictionary()


def get_memory_worker_lane(vault_dir: str) -> MemoryWorkerLane:
    loop = asyncio.get_running_loop()
    target = os.path.normcase(os.path.abspath(vault_dir))
    with _LANES_LOCK:
        loop_lanes = _LANES_BY_LOOP.setdefault(loop, {})
        lane = loop_lanes.get(target)
        if lane is None:
            lane = MemoryWorkerLane()
            loop_lanes[target] = lane
        return lane


async def run_memory_work(
    vault_dir: str,
    operation: str,
    access: MemoryAccess,
    function: Callable[..., _Result],
    /,
    *args: Any,
    **kwargs: Any,
) -> _Result:
    lane = get_memory_worker_lane(vault_dir)
    return await lane.run(operation, access, function, *args, **kwargs)


def _validate_operation(operation: str) -> str:
    normalized = str(operation or "")
    if normalized not in MEMORY_WORKER_OPERATIONS:
        raise ValueError(f"unsupported memory worker operation: {normalized}")
    return normalized


def _validate_access(access: str) -> MemoryAccess:
    if access not in {"read", "write"}:
        raise ValueError(f"unsupported memory worker access: {access}")
    return access  # type: ignore[return-value]

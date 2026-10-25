"""Harbour Lifecycle Supervision.

Provides task-supervision, bounded cancellation, and ownership tracking
for application startup/shutdown tasks and background workers.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Sequence

logger = logging.getLogger("uvicorn.error")

_STARTUP_TASK_SHUTDOWN_TIMEOUT_SECONDS: float = 2.0
_default_app_state: Any = None


def set_default_app_state(state: Any) -> None:
    """Set the default FastAPI app.state to use when no explicit state is provided."""
    global _default_app_state
    _default_app_state = state


def _resolve_app_state(app_state: Any = None) -> Any:
    """Resolve target app.state from explicit parameter, module fallback, or sys.modules."""
    if app_state is not None:
        return app_state
    if _default_app_state is not None:
        return _default_app_state
    import sys
    app_module = sys.modules.get("app")
    if app_module is not None and hasattr(app_module, "app"):
        return app_module.app.state
    raise RuntimeError("app_state not provided and default app.state not registered")


def observe_finished_startup_tasks(tasks: Sequence[asyncio.Task]) -> int:
    """Consume completed task exceptions without exposing task details."""
    failed = 0
    for task in tasks:
        try:
            if task.exception() is not None:
                failed += 1
        except asyncio.CancelledError:
            pass
        except BaseException:
            failed += 1
    return failed


def retain_registered_startup_task(task: asyncio.Task, *, app_state: Any = None) -> None:
    """Retain a task in the registered startup tasks list if not already present."""
    state = _resolve_app_state(app_state)
    startup_tasks = getattr(state, "_startup_tasks", None)
    if startup_tasks is None:
        startup_tasks = []
        state._startup_tasks = startup_tasks
    if not any(registered is task for registered in startup_tasks):
        startup_tasks.append(task)


async def await_recent_changes_snapshot_shutdown(
    stop_event: threading.Event | None,
    task: asyncio.Task | None,
    *,
    timeout_seconds: float = _STARTUP_TASK_SHUTDOWN_TIMEOUT_SECONDS,
    app_state: Any = None,
) -> bool:
    """Signal the cooperative snapshot and retain ownership if it outlives shutdown."""
    state = _resolve_app_state(app_state)
    if stop_event is not None:
        stop_event.set()
    if task is None:
        return True
    done, pending = await asyncio.wait((task,), timeout=timeout_seconds)
    if pending or task.cancelled():
        retain_registered_startup_task(task, app_state=state)
        if pending:
            def clear_if_completed(completed: asyncio.Task) -> None:
                if completed.cancelled():
                    logger.warning("Recent-change snapshot retained incomplete task")
                    return
                failures = observe_finished_startup_tasks((completed,))
                if failures:
                    logger.warning("Recent-change snapshot observed %d failed task(s)", failures)
                if getattr(state, "_startup_recent_changes_task", None) is completed:
                    state._startup_recent_changes_task = None
                    if getattr(state, "_startup_recent_changes_stop_event", None) is stop_event:
                        state._startup_recent_changes_stop_event = None
                startup_tasks = getattr(state, "_startup_tasks", [])
                state._startup_tasks = [registered for registered in startup_tasks if registered is not completed]

            task.add_done_callback(clear_if_completed)
            logger.warning("Recent-change snapshot shutdown retained %d pending task(s)", len(pending))
        else:
            logger.warning("Recent-change snapshot retained incomplete task")
        return False
    failures = observe_finished_startup_tasks(tuple(done))
    if failures:
        logger.warning("Recent-change snapshot observed %d failed task(s)", failures)
    return True


async def shutdown_registered_startup_tasks(
    *,
    excluded: tuple[asyncio.Task | None, ...] = (),
    timeout_seconds: float = _STARTUP_TASK_SHUTDOWN_TIMEOUT_SECONDS,
    app_state: Any = None,
) -> None:
    """Cancel only registered tasks we own and retain any noncooperative task."""
    state = _resolve_app_state(app_state)
    current = asyncio.current_task()
    excluded_ids = {id(task) for task in excluded if task is not None}
    seen: set[int] = set()
    retained: list[asyncio.Task] = []
    cancelling: list[asyncio.Task] = []
    finished: list[asyncio.Task] = []
    unsupported = 0
    for task in tuple(getattr(state, "_startup_tasks", ())):
        if not isinstance(task, asyncio.Task):
            unsupported += 1
            continue
        task_id = id(task)
        if task_id in seen:
            continue
        seen.add(task_id)
        if task is current or task_id in excluded_ids:
            retained.append(task)
        elif task.done():
            finished.append(task)
        else:
            task.cancel()
            cancelling.append(task)
    pending: tuple[asyncio.Task, ...] = ()
    if cancelling:
        done, pending_set = await asyncio.wait(cancelling, timeout=timeout_seconds)
        finished.extend(done)
        pending = tuple(pending_set)
        retained.extend(pending)
    state._startup_tasks = retained
    failed = observe_finished_startup_tasks(tuple(finished))
    if pending:
        logger.warning("Startup task shutdown retained %d pending task(s)", len(pending))
    if failed:
        logger.warning("Startup task shutdown observed %d failed task(s)", failed)
    if unsupported:
        logger.warning("Startup task shutdown observed %d unsupported reference(s)", unsupported)


async def stop_dedicated_startup_task(
    task: asyncio.Task,
    *,
    label: str,
    timeout_seconds: float = _STARTUP_TASK_SHUTDOWN_TIMEOUT_SECONDS,
    app_state: Any = None,
) -> bool:
    """Bound a dedicated task's existing shutdown slot without losing its ref."""
    state = _resolve_app_state(app_state)
    startup_tasks = getattr(state, "_startup_tasks", [])
    if not task.done():
        task.cancel()
        done, pending = await asyncio.wait((task,), timeout=timeout_seconds)
        if pending:
            if not any(registered is task for registered in startup_tasks):
                startup_tasks.append(task)
                state._startup_tasks = startup_tasks
            logger.warning("%s shutdown retained %d pending task(s)", label, len(pending))
            return False
        observe_finished_startup_tasks(tuple(done))
    else:
        observe_finished_startup_tasks((task,))
    state._startup_tasks = [registered for registered in getattr(state, "_startup_tasks", []) if registered is not task]
    return True


# Backward compatibility aliases
_observe_finished_startup_tasks = observe_finished_startup_tasks
_retain_registered_startup_task = retain_registered_startup_task
_await_recent_changes_snapshot_shutdown = await_recent_changes_snapshot_shutdown
_shutdown_registered_startup_tasks = shutdown_registered_startup_tasks
_stop_dedicated_startup_task = stop_dedicated_startup_task

__all__ = [
    "_STARTUP_TASK_SHUTDOWN_TIMEOUT_SECONDS",
    "set_default_app_state",
    "observe_finished_startup_tasks",
    "retain_registered_startup_task",
    "await_recent_changes_snapshot_shutdown",
    "shutdown_registered_startup_tasks",
    "stop_dedicated_startup_task",
    "_observe_finished_startup_tasks",
    "_retain_registered_startup_task",
    "_await_recent_changes_snapshot_shutdown",
    "_shutdown_registered_startup_tasks",
    "_stop_dedicated_startup_task",
]

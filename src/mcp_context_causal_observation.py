"""Fail-closed, request-bound observation for one manifest diagnostic.

The module is a stdlib-only leaf.  It is inert unless the fixed presence flag
is set, never records request or provider content, and exposes only bounded
fixed-literal projections.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
import json
import math
import os
from pathlib import Path
import stat
import sys
import threading
from types import FrameType, TracebackType
from typing import Final


DIAGNOSTIC_FLAG: Final = "ODYSSEUS_H7_MANIFEST_OBSERVATION"
CHILD_ARTIFACT_BASENAME: Final = "h7-manifest-observation.json"
CONTROLLER_JUNIT_RELATIVE: Final = Path(
    ".abc-test-tmp/h7manifest-r2-qa/r/positive-junit.xml"
)
CONTROLLER_EXPORT_BASENAME: Final = "manifest-fixed-projection-0.json"
SCHEMA: Final = "odysseus.h7_manifest_observation.v1"
MAX_PROJECTION_BYTES: Final = 768
MAX_STARTED_RECORDS: Final = 64
MAX_COMPLETED_RECORDS: Final = 64
MAX_NESTING: Final = 8
MAX_OPERATION_OCCURRENCE: Final = 16
NOMINAL_MARGIN_SECONDS: Final = 1.0
MAX_ENTRY_GAP_SECONDS: Final = 0.1

SPAN_LITERALS: Final = frozenset({
    "transport.runtime_handle",
    "manifest.begin",
    "manifest.capture",
    "manifest.cursor_verify",
    "manifest.cursor_issue",
    "manifest.fence_1",
    "manifest.encode",
    "manifest.fence_2",
    "manifest.finish",
    "snapshot.admission_scope",
    "snapshot.knowledge_reservation_scope",
    "snapshot.first.forge",
    "snapshot.first.planning",
    "snapshot.first.knowledge",
    "snapshot.final.forge",
    "snapshot.final.planning",
    "snapshot.final.knowledge",
    "fence.guards_scope",
    "fence.cookie_first",
    "fence.cookie_last",
    "fence.mcp_first",
    "fence.mcp_last",
    "fence.readback",
})

_EXACT_KEYS: Final = frozenset({
    "schema", "measurement", "outer_branch", "deadline_relation",
    "operation_path", "operation_occurrence", "request_binding",
    "lifecycle_binding", "thread_binding", "task_binding", "event_count",
    "overflow",
})
_MEASUREMENTS: Final = frozenset({
    "nominal_deadline_overlap_observed", "inconclusive", "invalid",
})
_OUTER_BRANCHES: Final = frozenset({
    "response_start_returned", "handler_response_504", "wait_for_timeout_catch",
    "downstream_timeout_observed", "timeout_catch_ambiguous", "cancelled",
    "exception", "unobserved",
})
_DEADLINE_RELATIONS: Final = frozenset({
    "not_applicable", "instrumented_nominal_overlap_ge1s", "ambiguous",
})
_BINDINGS: Final = frozenset({"exact", "mismatch", "incomplete"})
_TASK_BINDINGS: Final = frozenset({
    "distinct_bound_roles", "same_bound_role", "mismatch", "incomplete",
})
_STATUS_NORMAL_SUCCESS: Final = "normal_success"
_STATUS_HANDLER_504: Final = "handler_response_504"
_STATUS_NONNORMAL_RETURNED: Final = "nonnormal_returned"
_REQUEST_SCOPE_SENTINEL: Final = object()
_LOCK: Final = threading.RLock()
_ACTIVE_CAPSULE: ContextVar[object | None] = ContextVar(
    "h7_manifest_observation_capsule", default=None,
)
_SPAN_STACK: ContextVar[tuple[int, ...]] = ContextVar(
    "h7_manifest_observation_stack", default=(),
)
_CAPTURE_PHASE: ContextVar[str | None] = ContextVar(
    "h7_manifest_observation_capture_phase", default=None,
)

_pending_claimed = False
_capsule: dict[str, object] | None = None
_collection_finished = False


def _build_normal_traversal() -> tuple[tuple[tuple[str, ...], int | None], ...]:
    rows: list[tuple[str, ...]] = []
    root = ("transport.runtime_handle",)
    rows.append(root)

    def add_capture(parent: tuple[str, ...]) -> None:
        admission = parent + ("snapshot.admission_scope",)
        reservation = admission + ("snapshot.knowledge_reservation_scope",)
        rows.extend((admission, reservation))
        rows.extend(
            reservation + ("snapshot." + phase + "." + source,)
            for phase in ("first", "final")
            for source in ("forge", "planning", "knowledge")
        )

    rows.append(root + ("manifest.begin",))
    manifest_capture = root + ("manifest.capture",)
    rows.append(manifest_capture)
    add_capture(manifest_capture)
    for fence_literal in ("manifest.fence_1", "manifest.fence_2"):
        fence = root + (fence_literal,)
        guards = fence + ("fence.guards_scope",)
        rows.extend((fence, guards))
        for cookie_literal in ("fence.cookie_first", "fence.cookie_last"):
            cookie = guards + (cookie_literal,)
            rows.append(cookie)
        for mcp_literal in ("fence.mcp_first", "fence.mcp_last"):
            mcp = guards + (mcp_literal,)
            rows.append(mcp)
            add_capture(mcp)
        rows.append(guards + ("fence.readback",))
        if fence_literal == "manifest.fence_1":
            rows.append(root + ("manifest.encode",))
    rows.append(root + ("manifest.finish",))
    indices: dict[tuple[str, ...], int] = {}
    result: list[tuple[tuple[str, ...], int | None]] = []
    for path in rows:
        parent = indices.get(path[:-1]) if len(path) > 1 else None
        result.append((path, parent))
        indices[path] = len(result) - 1
    return tuple(result)


_NORMAL_TRAVERSAL: Final = _build_normal_traversal()


def _fixed_invalid() -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "measurement": "invalid",
        "outer_branch": "unobserved",
        "deadline_relation": "not_applicable",
        "operation_path": [],
        "operation_occurrence": 0,
        "request_binding": "incomplete",
        "lifecycle_binding": "incomplete",
        "thread_binding": "incomplete",
        "task_binding": "incomplete",
        "event_count": 0,
        "overflow": False,
    }


def fixed_invalid_projection() -> dict[str, object]:
    """Return a fresh content-free invalid projection."""
    return _fixed_invalid()


def diagnostic_enabled() -> bool:
    """The value is intentionally ignored; presence alone is the opt-in."""
    try:
        return DIAGNOSTIC_FLAG in os.environ
    except BaseException:
        return False


def _invalidate(capsule: object) -> None:
    try:
        if type(capsule) is dict:
            with _LOCK:
                capsule["invalid"] = True
    except BaseException:
        return


def _running_binding(capsule: dict[str, object]) -> tuple[object, object] | None:
    try:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task(loop=loop)
        if task is None:
            return None
        if (
            loop is not capsule.get("loop")
            or os.getpid() != capsule.get("pid")
            or threading.current_thread() is not capsule.get("thread")
            or threading.get_ident() != capsule.get("thread_ident")
        ):
            return None
        return loop, task
    except BaseException:
        return None


def wrapper_enter(scope: object, path: object, timeout: object) -> object | None:
    """Claim the one diagnostic request and capture its wrapper entry clock."""
    global _pending_claimed, _capsule
    try:
        if not diagnostic_enabled() or type(scope) is not dict:
            return None
        if tuple(sys.version_info[:3]) != (3, 14, 0):
            return None
        if type(path) is not str or path != "/api/plugins/mcp":
            return None
        if type(timeout) is not float or not math.isfinite(timeout) or timeout <= 0.0:
            return None
        loop = asyncio.get_running_loop()
        task = asyncio.current_task(loop=loop)
        if task is None:
            return None
        entry = loop.time()
        cancel_count = task.cancelling()
        if (
            type(entry) is not float or not math.isfinite(entry) or entry <= 0.0
            or type(cancel_count) is not int or cancel_count < 0
        ):
            return None
        capsule: dict[str, object] = {
            "phase": "claimed",
            "invalid": False,
            "overflow": False,
            "pid": os.getpid(),
            "thread": threading.current_thread(),
            "thread_ident": threading.get_ident(),
            "loop": loop,
            "wrapper_task": task,
            "downstream_task": None,
            "wrapper_cancel_count": cancel_count,
            "timeout": timeout,
            "deadline_lower": None,
            "deadline_upper": None,
            "request_bound": False,
            "runtime_context_open": False,
            "outer_branch": "unobserved",
            "_response_status_class": None,
            "downstream_timeout": False,
            "records": [],
            "started": 0,
            "completed": 0,
            "seal_count": 0,
            "collect_count": 0,
            "projection": None,
        }
        with _LOCK:
            if _pending_claimed or _capsule is not None or _collection_finished:
                return None
            if _REQUEST_SCOPE_SENTINEL in scope:
                return None
            _pending_claimed = True
            _capsule = capsule
            scope[_REQUEST_SCOPE_SENTINEL] = capsule
        return capsule
    except BaseException:
        return None


def wrapper_before_wait(scope: object, timeout: object) -> None:
    """Capture the lower nominal deadline immediately before wait_for."""
    try:
        loop = asyncio.get_running_loop()
        now = loop.time()
        capsule = scope.get(_REQUEST_SCOPE_SENTINEL) if type(scope) is dict else None
        if type(capsule) is not dict:
            return
        binding = _running_binding(capsule)
        if (
            binding is None or binding[0] is not loop
            or binding[1] is not capsule.get("wrapper_task")
        ):
            _invalidate(capsule)
            return
        if type(timeout) is not float or not math.isfinite(timeout) or timeout <= 0.0:
            _invalidate(capsule)
            return
        cancel_count = binding[1].cancelling()
        if (
            not math.isfinite(now) or now <= 0.0
            or timeout != capsule.get("timeout")
            or type(cancel_count) is not int or cancel_count < 0
        ):
            _invalidate(capsule)
            return
        with _LOCK:
            if capsule.get("phase") != "claimed":
                capsule["invalid"] = True
                return
            capsule["deadline_lower"] = now + timeout
            capsule["wrapper_cancel_count"] = cancel_count
            capsule["phase"] = "waiting"
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            return


def timeout_context_enter(scope: object, timeout: object) -> None:
    """Capture the upper bound as the observed coroutine's first statement."""
    try:
        loop = asyncio.get_running_loop()
        now = loop.time()
        capsule = scope.get(_REQUEST_SCOPE_SENTINEL) if type(scope) is dict else None
        if type(capsule) is not dict:
            return
        binding = _running_binding(capsule)
        if (
            binding is None or binding[0] is not loop
            or binding[1] is not capsule.get("wrapper_task")
        ):
            _invalidate(capsule)
            return
        if (
            type(timeout) is not float or not math.isfinite(timeout) or timeout <= 0.0
            or timeout != capsule.get("timeout")
        ):
            _invalidate(capsule)
            return
        lower = capsule.get("deadline_lower")
        upper = now + timeout
        if (
            type(lower) is not float or not math.isfinite(lower)
            or lower <= 0.0 or not math.isfinite(now) or now <= 0.0
            or not math.isfinite(upper) or upper <= 0.0
            or lower > upper or upper - lower >= MAX_ENTRY_GAP_SECONDS
        ):
            _invalidate(capsule)
            return
        with _LOCK:
            if capsule.get("phase") != "waiting":
                capsule["invalid"] = True
                return
            capsule["deadline_upper"] = upper
            capsule["phase"] = "timeout_entered"
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            return


def runtime_context_enter(scope: object, method: object, rpc_id: object) -> object | None:
    """Bind exact parsed manifest RPC state and activate spans around runtime_handle."""
    try:
        capsule = scope.get(_REQUEST_SCOPE_SENTINEL) if type(scope) is dict else None
        if type(capsule) is not dict:
            return None
        binding = _running_binding(capsule)
        if binding is None:
            _invalidate(capsule)
            return None
        if type(method) is not str or method != "harbour_context_manifest":
            return None
        if type(rpc_id) is not str or rpc_id != "manifest-1":
            return None
        if binding[1] is capsule.get("wrapper_task"):
            _invalidate(capsule)
            return None
        with _LOCK:
            if capsule.get("phase") != "timeout_entered":
                capsule["invalid"] = True
                return None
            if capsule.get("downstream_task") is not None:
                capsule["invalid"] = True
                return None
        active_token: object | None = None
        stack_token: object | None = None
        phase_token: object | None = None
        try:
            active_token = _ACTIVE_CAPSULE.set(capsule)
            try:
                stack_token = _SPAN_STACK.set(())
                try:
                    phase_token = _CAPTURE_PHASE.set(None)
                except BaseException:
                    try:
                        _SPAN_STACK.reset(stack_token)
                    except BaseException:
                        _invalidate(capsule)
                    raise
            except BaseException:
                try:
                    _ACTIVE_CAPSULE.reset(active_token)
                except BaseException:
                    _invalidate(capsule)
                raise
        except BaseException:
            _invalidate(capsule)
            return None
        try:
            with _LOCK:
                if (
                    capsule.get("phase") != "timeout_entered"
                    or capsule.get("downstream_task") is not None
                ):
                    raise RuntimeError("observer_state")
                capsule["downstream_task"] = binding[1]
                capsule["request_bound"] = True
                capsule["runtime_context_open"] = True
        except BaseException:
            for variable, token in (
                (_CAPTURE_PHASE, phase_token),
                (_SPAN_STACK, stack_token),
                (_ACTIVE_CAPSULE, active_token),
            ):
                try:
                    variable.reset(token)
                except BaseException:
                    _invalidate(capsule)
            _invalidate(capsule)
            return None
        return capsule, active_token, stack_token, phase_token
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            pass
        return None


def runtime_context_exit(binding: object) -> None:
    try:
        if type(binding) is not tuple or len(binding) != 4:
            return
        capsule, active_token, stack_token, phase_token = binding
        if type(capsule) is not dict:
            return
        clean = True
        try:
            running = _running_binding(capsule)
            if (
                running is None or running[1] is not capsule.get("downstream_task")
                or running[1] is capsule.get("wrapper_task")
            ):
                clean = False
                _invalidate(capsule)
        except BaseException:
            clean = False
            _invalidate(capsule)
        try:
            if (
                _ACTIVE_CAPSULE.get() is not capsule
                or _SPAN_STACK.get() != ()
                or _CAPTURE_PHASE.get() is not None
            ):
                clean = False
                _invalidate(capsule)
        except BaseException:
            clean = False
            _invalidate(capsule)
        for variable, token in (
            (_CAPTURE_PHASE, phase_token),
            (_SPAN_STACK, stack_token),
            (_ACTIVE_CAPSULE, active_token),
        ):
            try:
                variable.reset(token)
            except BaseException:
                clean = False
                _invalidate(capsule)
        with _LOCK:
            capsule["runtime_context_open"] = not clean
    except BaseException:
        return


def capture_phase_enter(phase: object) -> object | None:
    try:
        capsule = _ACTIVE_CAPSULE.get()
        if type(capsule) is not dict:
            return None
        binding = _running_binding(capsule)
        if (
            binding is None or binding[1] is not capsule.get("downstream_task")
            or binding[1] is capsule.get("wrapper_task")
            or capsule.get("runtime_context_open") is not True
        ):
            _invalidate(capsule)
            return None
        if type(phase) is not str or phase not in {"first", "final"}:
            _invalidate(capsule)
            return None
        if _CAPTURE_PHASE.get() is not None:
            _invalidate(capsule)
            return None
        return capsule, _CAPTURE_PHASE.set(phase)
    except BaseException:
        return None


def capture_phase_exit(binding: object) -> None:
    try:
        if type(binding) is not tuple or len(binding) != 2:
            return
        capsule, token = binding
        if type(capsule) is not dict:
            return
        running = _running_binding(capsule)
        if (
            running is None or running[1] is not capsule.get("downstream_task")
            or running[1] is capsule.get("wrapper_task")
            or _ACTIVE_CAPSULE.get() is not capsule
            or capsule.get("runtime_context_open") is not True
        ):
            _invalidate(capsule)
        try:
            _CAPTURE_PHASE.reset(token)
        except BaseException:
            _invalidate(capsule)
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            return


def snapshot_source_begin(source: object) -> object | None:
    try:
        capsule = _ACTIVE_CAPSULE.get()
        if type(capsule) is not dict:
            return None
        binding = _running_binding(capsule)
        if (
            binding is None or binding[1] is not capsule.get("downstream_task")
            or binding[1] is capsule.get("wrapper_task")
            or capsule.get("runtime_context_open") is not True
        ):
            _invalidate(capsule)
            return None
        phase = _CAPTURE_PHASE.get()
        if type(source) is not str or source not in {"forge", "planning", "knowledge"}:
            _invalidate(capsule)
            return None
        if phase not in {"first", "final"}:
            _invalidate(capsule)
            return None
        return span_begin("snapshot." + phase + "." + source)
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            pass
        return None


def span_begin(literal: object) -> object | None:
    try:
        capsule = _ACTIVE_CAPSULE.get()
        if type(capsule) is not dict:
            return None
        if type(literal) is not str or literal not in SPAN_LITERALS:
            _invalidate(capsule)
            return None
        binding = _running_binding(capsule)
        if (
            binding is None or binding[1] is not capsule.get("downstream_task")
            or binding[1] is capsule.get("wrapper_task")
            or capsule.get("runtime_context_open") is not True
        ):
            _invalidate(capsule)
            return None
        stack = _SPAN_STACK.get()
        if type(stack) is not tuple:
            _invalidate(capsule)
            return None
        with _LOCK:
            started = capsule.get("started")
            records = capsule.get("records")
            if type(started) is not int or type(records) is not list:
                capsule["invalid"] = True
                return None
            if started >= MAX_STARTED_RECORDS or len(stack) >= MAX_NESTING:
                capsule["overflow"] = True
                capsule["invalid"] = True
                return None
            index = len(records)
            path = tuple(records[parent]["literal"] for parent in stack) + (literal,)
            if len(path) > MAX_NESTING:
                capsule["overflow"] = True
                capsule["invalid"] = True
                return None
            records.append({
                "literal": literal,
                "start": binding[0].time(),
                "end": None,
                "depth": len(path),
                "path": path,
                "parent": stack[-1] if stack else None,
            })
            capsule["started"] = started + 1
        token = _SPAN_STACK.set(stack + (index,))
        return capsule, index, token
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            pass
        return None


def span_end(binding: object) -> None:
    try:
        if type(binding) is not tuple or len(binding) != 3:
            return
        capsule, index, token = binding
        if type(capsule) is not dict or type(index) is not int:
            return
        current = _ACTIVE_CAPSULE.get()
        stack = _SPAN_STACK.get()
        running = _running_binding(capsule)
        valid = not (
            current is not capsule or type(stack) is not tuple or not stack
            or stack[-1] != index or running is None
            or running[1] is not capsule.get("downstream_task")
            or running[1] is capsule.get("wrapper_task")
            or capsule.get("runtime_context_open") is not True
        )
        if not valid:
            _invalidate(capsule)
        else:
            with _LOCK:
                records = capsule.get("records")
                completed = capsule.get("completed")
                if (
                    type(records) is not list or not 0 <= index < len(records)
                    or type(completed) is not int or completed >= MAX_COMPLETED_RECORDS
                    or records[index].get("end") is not None
                ):
                    capsule["overflow"] = True
                    capsule["invalid"] = True
                else:
                    records[index]["end"] = running[0].time()
                    capsule["completed"] = completed + 1
        try:
            _SPAN_STACK.reset(token)
        except BaseException:
            _invalidate(capsule)
    except BaseException:
        try:
            _invalidate(capsule)
            _SPAN_STACK.reset(token)
        except BaseException:
            return


def downstream_timeout(scope: object) -> None:
    try:
        capsule = scope.get(_REQUEST_SCOPE_SENTINEL) if type(scope) is dict else None
        if type(capsule) is not dict:
            return
        running = _running_binding(capsule)
        if running is None or running[1] is not capsule.get("wrapper_task"):
            _invalidate(capsule)
            return
        wrapper = capsule.get("wrapper_task")
        lower = capsule.get("deadline_lower")
        baseline = capsule.get("wrapper_cancel_count")
        if wrapper is None or type(lower) is not float or type(baseline) is not int:
            _invalidate(capsule)
            return
        count_before = wrapper.cancelling()
        observed_at = running[0].time()
        count_after = wrapper.cancelling()
        if (
            type(count_before) is not int or type(count_after) is not int
            or not math.isfinite(observed_at)
        ):
            _invalidate(capsule)
            return
        changed = not (count_before == baseline == count_after)
        with _LOCK:
            capsule["downstream_timeout"] = True
            capsule["outer_branch"] = (
                "downstream_timeout_observed"
                if observed_at < lower and not changed
                else "timeout_catch_ambiguous"
            )
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            return


def response_start(scope: object, status_code: object) -> None:
    try:
        capsule = scope.get(_REQUEST_SCOPE_SENTINEL) if type(scope) is dict else None
        if type(capsule) is not dict:
            return
        running = _running_binding(capsule)
        if running is None or running[1] is not capsule.get("wrapper_task"):
            _invalidate(capsule)
            return
        with _LOCK:
            if type(status_code) is int and status_code == 200:
                capsule["outer_branch"] = "response_start_returned"
                capsule["_response_status_class"] = _STATUS_NORMAL_SUCCESS
            elif type(status_code) is int and status_code == 504:
                capsule["outer_branch"] = "handler_response_504"
                capsule["_response_status_class"] = _STATUS_HANDLER_504
            else:
                capsule["outer_branch"] = "response_start_returned"
                capsule["_response_status_class"] = _STATUS_NONNORMAL_RETURNED
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            return


def wrapper_timeout_catch(scope: object) -> None:
    try:
        capsule = scope.get(_REQUEST_SCOPE_SENTINEL) if type(scope) is dict else None
        if type(capsule) is not dict:
            return
        running = _running_binding(capsule)
        if running is None or running[1] is not capsule.get("wrapper_task"):
            _invalidate(capsule)
            return
        with _LOCK:
            if not capsule.get("downstream_timeout"):
                capsule["outer_branch"] = "wait_for_timeout_catch"
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            return


def wrapper_cancelled(scope: object) -> None:
    try:
        _set_outer_branch(scope, "cancelled")
    except BaseException:
        return


def wrapper_exception(scope: object) -> None:
    try:
        _set_outer_branch(scope, "exception")
    except BaseException:
        return


def _set_outer_branch(scope: object, branch: str) -> None:
    try:
        capsule = scope.get(_REQUEST_SCOPE_SENTINEL) if type(scope) is dict else None
        if type(capsule) is not dict:
            return
        running = _running_binding(capsule)
        if running is None or running[1] is not capsule.get("wrapper_task"):
            _invalidate(capsule)
            return
        with _LOCK:
            capsule["outer_branch"] = branch
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            return


def _projection(capsule: dict[str, object]) -> dict[str, object]:
    try:
        if capsule.get("invalid") or capsule.get("overflow"):
            return _fixed_invalid()
        records = capsule.get("records")
        started = capsule.get("started")
        completed = capsule.get("completed")
        lower = capsule.get("deadline_lower")
        upper = capsule.get("deadline_upper")
        outer = capsule.get("outer_branch")
        status_class = capsule.get("_response_status_class")
        if (
            type(records) is not list or type(started) is not int or type(completed) is not int
            or started != len(records) or completed != len(records)
            or not 0 <= completed <= MAX_COMPLETED_RECORDS
            or type(lower) is not float or type(upper) is not float
            or not math.isfinite(lower) or not math.isfinite(upper)
            or lower <= 0.0 or upper <= 0.0 or lower > upper
            or upper - lower >= MAX_ENTRY_GAP_SECONDS
            or outer not in _OUTER_BRANCHES or outer == "unobserved"
            or capsule.get("runtime_context_open") is not False
        ):
            return _fixed_invalid()
        if (
            status_class not in {
                None, _STATUS_NORMAL_SUCCESS, _STATUS_HANDLER_504,
                _STATUS_NONNORMAL_RETURNED,
            }
            or (
                outer == "response_start_returned"
                and status_class not in {
                    _STATUS_NORMAL_SUCCESS, _STATUS_NONNORMAL_RETURNED,
                }
            )
            or (
                outer == "handler_response_504"
                and status_class != _STATUS_HANDLER_504
            )
            or (
                outer not in {"response_start_returned", "handler_response_504"}
                and status_class is not None
            )
        ):
            return _fixed_invalid()
        if (
            len(records) > len(_NORMAL_TRAVERSAL)
            or (
                status_class == _STATUS_NORMAL_SUCCESS
                and len(records) != len(_NORMAL_TRAVERSAL)
            )
        ):
            return _fixed_invalid()
        last_child_end: dict[int | None, float] = {}
        for index, record in enumerate(records):
            if type(record) is not dict:
                return _fixed_invalid()
            start, end = record.get("start"), record.get("end")
            depth, path, parent = record.get("depth"), record.get("path"), record.get("parent")
            if (
                type(start) is not float or type(end) is not float or end < start
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0.0 or end < 0.0
                or type(depth) is not int or not 1 <= depth <= MAX_NESTING
                or type(path) is not tuple or len(path) != depth
                or any(type(item) is not str or item not in SPAN_LITERALS for item in path)
                or record.get("literal") != path[-1]
            ):
                return _fixed_invalid()
            expected_path, expected_parent = _NORMAL_TRAVERSAL[index]
            if path != expected_path or parent != expected_parent:
                return _fixed_invalid()
            if parent is None:
                if depth != 1:
                    return _fixed_invalid()
            elif (
                type(parent) is not int or not 0 <= parent < index
                or records[parent].get("start") > start
                or records[parent].get("end") < end
                or records[parent].get("path") != path[:-1]
            ):
                return _fixed_invalid()
            previous_end = last_child_end.get(parent)
            if previous_end is not None and start < previous_end:
                return _fixed_invalid()
            last_child_end[parent] = end
        request_binding = "exact" if capsule.get("request_bound") is True else "incomplete"
        thread_binding = "exact" if _same_terminal_binding(capsule) else "mismatch"
        wrapper_task = capsule.get("wrapper_task")
        downstream_task = capsule.get("downstream_task")
        if wrapper_task is None or downstream_task is None:
            task_binding = "incomplete"
        elif wrapper_task is downstream_task:
            task_binding = "same_bound_role"
        else:
            task_binding = "distinct_bound_roles"
        lifecycle_binding = "exact" if capsule.get("seal_count") == 1 else "incomplete"
        if (
            request_binding != "exact" or lifecycle_binding != "exact"
            or thread_binding != "exact" or task_binding != "distinct_bound_roles"
        ):
            return _fixed_invalid()
        candidates = [
            record for record in records
            if record["start"] < lower and record["end"] >= upper + NOMINAL_MARGIN_SECONDS
        ]
        operation_path: list[str] = []
        operation_occurrence = 0
        measurement = "inconclusive"
        relation = "ambiguous" if outer in {
            "wait_for_timeout_catch", "downstream_timeout_observed", "timeout_catch_ambiguous",
        } else "not_applicable"
        if candidates:
            deepest = max(record["depth"] for record in candidates)
            winners = [record for record in candidates if record["depth"] == deepest]
            if (
                len(winners) == 1 and outer == "wait_for_timeout_catch"
                and request_binding == "exact" and lifecycle_binding == "exact"
                and thread_binding == "exact" and task_binding == "distinct_bound_roles"
            ):
                winner = winners[0]
                winner_path = winner["path"]
                has_declared_descendants = any(
                    len(path) > len(winner_path)
                    and path[:len(winner_path)] == winner_path
                    for path, _ in _NORMAL_TRAVERSAL
                )
                has_overlapping_completed_descendant = any(
                    len(record["path"]) > len(winner_path)
                    and record["path"][:len(winner_path)] == winner_path
                    and record["start"] < upper and record["end"] > lower
                    for record in records
                )
                if not has_declared_descendants or has_overlapping_completed_descendant:
                    operation_path = list(winner_path)
                    terminal = winner["literal"]
                    winner_index = records.index(winner)
                    operation_occurrence = 1 + sum(
                        1
                        for record in records[:winner_index]
                        if record.get("literal") == terminal
                    )
                    if operation_occurrence <= MAX_OPERATION_OCCURRENCE:
                        measurement = "nominal_deadline_overlap_observed"
                        relation = "instrumented_nominal_overlap_ge1s"
                    else:
                        operation_path = []
                        operation_occurrence = 0
        result = {
            "schema": SCHEMA,
            "measurement": measurement,
            "outer_branch": outer,
            "deadline_relation": relation,
            "operation_path": operation_path,
            "operation_occurrence": operation_occurrence,
            "request_binding": request_binding,
            "lifecycle_binding": lifecycle_binding,
            "thread_binding": thread_binding,
            "task_binding": task_binding,
            "event_count": completed,
            "overflow": False,
        }
        return result if _projection_valid(result) else _fixed_invalid()
    except BaseException:
        return _fixed_invalid()


def _same_terminal_binding(capsule: dict[str, object]) -> bool:
    try:
        return bool(
            os.getpid() == capsule.get("pid")
            and threading.current_thread() is capsule.get("thread")
            and threading.get_ident() == capsule.get("thread_ident")
        )
    except BaseException:
        return False


def seal(scope: object) -> None:
    global _pending_claimed, _capsule
    try:
        capsule = scope.get(_REQUEST_SCOPE_SENTINEL) if type(scope) is dict else None
        if type(capsule) is not dict:
            return
        running = _running_binding(capsule)
        if running is None or running[1] is not capsule.get("wrapper_task"):
            _invalidate(capsule)
        with _LOCK:
            if capsule.get("request_bound") is not True:
                capsule["phase"] = "discarded"
                scope.pop(_REQUEST_SCOPE_SENTINEL, None)
                if _capsule is capsule:
                    _capsule = None
                    _pending_claimed = False
                return
            capsule["seal_count"] = int(capsule.get("seal_count", 0)) + 1
            if capsule["seal_count"] != 1 or capsule.get("phase") != "timeout_entered":
                capsule["invalid"] = True
            if (
                capsule.get("runtime_context_open") is not False
                or _SPAN_STACK.get() != () or _ACTIVE_CAPSULE.get() is not None
                or _CAPTURE_PHASE.get() is not None
            ):
                capsule["invalid"] = True
            capsule["phase"] = "sealed"
            capsule["projection"] = _projection(capsule)
    except BaseException:
        try:
            _invalidate(capsule)
        except BaseException:
            return
    finally:
        try:
            if type(scope) is dict:
                scope.pop(_REQUEST_SCOPE_SENTINEL, None)
        except BaseException:
            try:
                _invalidate(capsule)
            except BaseException:
                pass


def collect_projection() -> dict[str, object]:
    """Collect exactly once; replay or incomplete state is fixed invalid."""
    global _capsule, _collection_finished, _pending_claimed
    capsule: object = None
    result = _fixed_invalid()
    try:
        capsule = _capsule
        if type(capsule) is not dict or _collection_finished:
            return result
        with _LOCK:
            capsule["collect_count"] = int(capsule.get("collect_count", 0)) + 1
            if capsule["collect_count"] != 1 or capsule.get("phase") != "sealed":
                capsule["invalid"] = True
            else:
                value = capsule.get("projection")
                result = (
                    _copy_projection(value)
                    if type(value) is dict and _projection_valid(value)
                    else _fixed_invalid()
                )
            capsule["phase"] = "collected"
        return result
    except BaseException:
        return result
    finally:
        if type(capsule) is dict:
            for key, replacement in (
                ("pid", None), ("thread", None), ("thread_ident", None),
                ("loop", None), ("wrapper_task", None), ("downstream_task", None),
                ("timeout", None),
                ("_response_status_class", None),
                ("deadline_lower", None), ("deadline_upper", None),
                ("records", []), ("projection", None),
            ):
                try:
                    capsule[key] = replacement
                except BaseException:
                    pass
        try:
            with _LOCK:
                _capsule = None
                _pending_claimed = False
                _collection_finished = True
        except BaseException:
            _capsule = None
            _pending_claimed = False
            _collection_finished = True


def _copy_projection(value: dict[str, object]) -> dict[str, object]:
    return {
        "schema": value["schema"],
        "measurement": value["measurement"],
        "outer_branch": value["outer_branch"],
        "deadline_relation": value["deadline_relation"],
        "operation_path": list(value["operation_path"]),
        "operation_occurrence": value["operation_occurrence"],
        "request_binding": value["request_binding"],
        "lifecycle_binding": value["lifecycle_binding"],
        "thread_binding": value["thread_binding"],
        "task_binding": value["task_binding"],
        "event_count": value["event_count"],
        "overflow": value["overflow"],
    }


def _exact_int(value: object, lower: int, upper: int) -> bool:
    return type(value) is int and lower <= value <= upper


def _projection_valid(value: object) -> bool:
    try:
        if type(value) is not dict or set(value) != _EXACT_KEYS:
            return False
        path = value.get("operation_path")
        occurrence = value.get("operation_occurrence")
        event_count = value.get("event_count")
        if (
            value.get("schema") != SCHEMA
            or value.get("measurement") not in _MEASUREMENTS
            or value.get("outer_branch") not in _OUTER_BRANCHES
            or value.get("deadline_relation") not in _DEADLINE_RELATIONS
            or value.get("request_binding") not in _BINDINGS
            or value.get("lifecycle_binding") not in _BINDINGS
            or value.get("thread_binding") not in _BINDINGS
            or value.get("task_binding") not in _TASK_BINDINGS
            or type(path) is not list or len(path) > MAX_NESTING
            or any(type(item) is not str or item not in SPAN_LITERALS for item in path)
            or not _exact_int(occurrence, 0, MAX_OPERATION_OCCURRENCE)
            or not _exact_int(event_count, 0, MAX_COMPLETED_RECORDS)
            or type(value.get("overflow")) is not bool
        ):
            return False
        measurement = value["measurement"]
        if (not path) != (occurrence == 0):
            return False
        if measurement == "invalid":
            return value == _fixed_invalid()
        if (
            value["outer_branch"] == "unobserved"
            or value["request_binding"] != "exact"
            or value["lifecycle_binding"] != "exact"
            or value["thread_binding"] != "exact"
            or value["task_binding"] != "distinct_bound_roles"
            or value["overflow"] is not False
        ):
            return False
        if measurement == "nominal_deadline_overlap_observed":
            if not (
                path and value["deadline_relation"] == "instrumented_nominal_overlap_ge1s"
                and value["outer_branch"] == "wait_for_timeout_catch"
            ):
                return False
            path_tuple = tuple(path)
            matching_index = next(
                (
                    index for index, (expected_path, _) in enumerate(_NORMAL_TRAVERSAL)
                    if expected_path == path_tuple
                ),
                None,
            )
            if matching_index is None:
                return False
            expected_occurrence = 1 + sum(
                1
                for expected_path, _ in _NORMAL_TRAVERSAL[:matching_index]
                if expected_path[-1] == path[-1]
            )
            return occurrence == expected_occurrence and event_count >= matching_index + 1
        if path or occurrence != 0 or value["deadline_relation"] == "instrumented_nominal_overlap_ge1s":
            return False
        timeout_branch = value["outer_branch"] in {
            "wait_for_timeout_catch", "downstream_timeout_observed", "timeout_catch_ambiguous",
        }
        if (value["deadline_relation"] == "ambiguous") != timeout_branch:
            return False
        if value["outer_branch"] in {"response_start_returned", "handler_response_504"}:
            return event_count <= len(_NORMAL_TRAVERSAL)
        return True
    except BaseException:
        return False


def encode_projection(value: object) -> bytes:
    try:
        if not _projection_valid(value):
            value = _fixed_invalid()
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_PROJECTION_BYTES:
            encoded = json.dumps(
                _fixed_invalid(), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        return encoded
    except BaseException:
        return (
            b'{"deadline_relation":"not_applicable","event_count":0,'
            b'"lifecycle_binding":"incomplete","measurement":"invalid",'
            b'"operation_occurrence":0,"operation_path":[],"outer_branch":"unobserved",'
            b'"overflow":false,"request_binding":"incomplete",'
            b'"schema":"odysseus.h7_manifest_observation.v1",'
            b'"task_binding":"incomplete","thread_binding":"incomplete"}'
        )


def parse_projection_bytes(data: object) -> dict[str, object]:
    """Strict canonical parser with duplicate-key and JSON-constant rejection."""
    try:
        if type(data) is not bytes or not 0 < len(data) <= MAX_PROJECTION_BYTES:
            return _fixed_invalid()
        text = data.decode("utf-8", errors="strict")
        if not text or text != text.strip():
            return _fixed_invalid()
        duplicate = False

        def pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
            nonlocal duplicate
            result: dict[str, object] = {}
            for key, value in rows:
                if type(key) is not str or key in result:
                    duplicate = True
                result[key] = value
            return result

        def reject_constant(_: str) -> object:
            raise ValueError("invalid")

        value = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
        if duplicate or not _projection_valid(value):
            return _fixed_invalid()
        if encode_projection(value) != data:
            return _fixed_invalid()
        return _copy_projection(value)
    except BaseException:
        return _fixed_invalid()


def _reparse(path: Path) -> bool:
    try:
        info = os.lstat(path)
        return bool(
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    except BaseException:
        return True


def _safe_existing_directory(path: Path) -> bool:
    try:
        return bool(
            path.is_absolute() and path.is_dir() and not _reparse(path)
            and path.resolve(strict=True) == path
        )
    except BaseException:
        return False


def write_child_projection_exclusive(script_path: object) -> bool:
    """Collect once and exclusively create the fixed sibling child artifact."""
    try:
        if not diagnostic_enabled():
            return False
        value = collect_projection()
        if not (type(script_path) is str or type(script_path) is type(Path())):
            return False
        script = Path(script_path)
        if not script.is_absolute() or not script.is_file() or _reparse(script):
            return False
        parent = script.parent
        if not _safe_existing_directory(parent):
            return False
        target = parent / CHILD_ARTIFACT_BASENAME
        if target.parent != parent or target.exists() or target.is_symlink():
            return False
        payload = encode_projection(value)
        with target.open("xb") as stream:
            stream.write(payload)
        return True
    except BaseException:
        return False


def read_child_projection(child_directory: object) -> dict[str, object]:
    """Read only the fixed child artifact, bounded to 769 bytes."""
    try:
        if not (type(child_directory) is str or type(child_directory) is type(Path())):
            return _fixed_invalid()
        parent = Path(child_directory)
        if not _safe_existing_directory(parent):
            return _fixed_invalid()
        target = parent / CHILD_ARTIFACT_BASENAME
        if target.parent != parent or not target.is_file() or _reparse(target):
            return _fixed_invalid()
        with target.open("rb") as stream:
            data = stream.read(MAX_PROJECTION_BYTES + 1)
        if len(data) > MAX_PROJECTION_BYTES:
            return _fixed_invalid()
        return parse_projection_bytes(data)
    except BaseException:
        return _fixed_invalid()


def _controller_source_root() -> Path | None:
    try:
        module = Path(__file__)
        if not module.is_absolute() or not module.is_file() or _reparse(module):
            return None
        source = module.parent
        root = source.parent
        if (
            module != root / "src" / "mcp_context_causal_observation.py"
            or not _safe_existing_directory(source)
            or not _safe_existing_directory(root)
        ):
            return None
        return root
    except BaseException:
        return None


def _controller_target(root: Path, *, require_file: bool) -> Path | None:
    try:
        expected = root / CONTROLLER_JUNIT_RELATIVE
        parent = expected.parent
        current = parent
        while True:
            if not _safe_existing_directory(current) or _reparse(current):
                return None
            if current == root:
                break
            if root not in current.parents:
                return None
            current = current.parent
        target = parent / CONTROLLER_EXPORT_BASENAME
        if (
            target.parent != parent or target.is_symlink()
            or target.exists() and _reparse(target)
        ):
            return None
        if require_file:
            if not target.is_file():
                return None
        elif target.exists():
            return None
        return target
    except BaseException:
        return None


def controller_projection_path(repo_root: object, xmlpath: object) -> Path | None:
    """Validate the one controller-owned JUnit slot and return its fixed sibling."""
    try:
        if not (type(repo_root) is str or type(repo_root) is type(Path())):
            return None
        if not (type(xmlpath) is str or type(xmlpath) is type(Path())):
            return None
        root = _controller_source_root()
        if root is None or Path(repo_root) != root:
            return None
        expected = root / CONTROLLER_JUNIT_RELATIVE
        supplied = Path(os.path.abspath(xmlpath))
        if supplied != expected:
            return None
        if expected.exists() and (not expected.is_file() or _reparse(expected)):
            return None
        return _controller_target(root, require_file=False)
    except BaseException:
        return None


def write_controller_projection_exclusive(target: object, value: object) -> bool:
    """Exclusively export one already validated projection sibling."""
    try:
        if type(target) is not type(Path()) or target.name != CONTROLLER_EXPORT_BASENAME:
            return False
        module_root = _controller_source_root()
        if module_root is None or target != _controller_target(module_root, require_file=False):
            return False
        payload = encode_projection(value)
        with target.open("xb") as stream:
            stream.write(payload)
        return True
    except BaseException:
        return False


def read_controller_projection() -> dict[str, object]:
    """Read the fixed controller companion through the strict bounded parser."""
    try:
        root = _controller_source_root()
        if root is None:
            return _fixed_invalid()
        target = _controller_target(root, require_file=True)
        if target is None:
            return _fixed_invalid()
        with target.open("rb") as stream:
            data = stream.read(MAX_PROJECTION_BYTES + 1)
        if len(data) > MAX_PROJECTION_BYTES:
            return _fixed_invalid()
        return parse_projection_bytes(data)
    except BaseException:
        return _fixed_invalid()


# The content-rejection observer is deliberately independent of the timing
# observer above. It is armed explicitly by the one diagnostic child after
# the normal application import and observes only an active Page POST.
CONTENT_REJECTION_SCHEMA: Final = "odysseus.h7_content_inner_reject.v2"
CONTENT_REJECTION_BASENAME: Final = "h7-content-inner-reject-v2.json"
CONTENT_REJECTION_MAX_BYTES: Final = 4096
CONTENT_REJECTION_MAX_CHAIN: Final = 8
CONTENT_REJECTION_MAX_TRACEBACK: Final = 32
_CONTENT_KEYS: Final = frozenset({
    "schema", "observer_status", "outcome", "response_class",
    "failing_request_ordinal", "page_source_class", "route_class",
    "inner_map_status", "inner_source_class", "inner_branch", "inner_locus_id",
    "observer_rejection_reason", "event_thread_consistent",
    "observation_scope_closed", "release_scope", "global_mask_untouched",
})
# "exact" identifies the nearest mapped caller branch and source line in the
# observed traceback; it does not claim that the mapped frame is the leaf reject.
# Branch categories add no cause claim; only native line 1621 is the timeout reject.
_CONTENT_LOCUS_RULES: Final = (
    ("runtime", "runtime.content_preflight", ((3141, 3157),)),
    ("runtime", "runtime.native_lease_acquire", ((3157, 3174),)),
    ("runtime", "runtime.native_lease_shape", ((3174, 3195),)),
    ("runtime", "runtime.native_lease_live", ((3195, 3202),)),
    ("runtime", "runtime.record_live", ((3202, 3210),)),
    ("runtime", "runtime.publication_open", ((3215, 3234),)),
    ("runtime", "runtime.postpublication_validation", ((3234, 3242),)),
    ("runtime", "runtime.publication_exit", ((3242, 3256),)),
    ("runtime", "runtime.cleanup", ((3280, 3334),)),
    ("authority", "authority.native_lease_acquire", ((399, 419), (1006, 1028))),
    ("authority", "authority.lease_recheck.snapshot", ((1600, 1602),)),
    ("authority", "authority.lease_recheck.state_lookup", ((1602, 1607),)),
    ("authority", "authority.lease_recheck.cancelled_marker", ((1607, 1608),)),
    ("authority", "authority.lease_recheck.missing", ((1608, 1609),)),
    ("authority", "authority.lease_recheck.cancelled_row", ((1609, 1611),)),
    ("authority", "authority.lease_recheck.clock_read", ((1611, 1617),)),
    ("authority", "authority.lease_recheck.clock_validity", ((1617, 1619),)),
    ("authority", "authority.lease_recheck.expiry", ((1619, 1622),)),
    ("authority", "authority.lease_recheck.binding", ((1622, 1624),)),
    ("authority", "authority.lease_recheck.propagation", ((1624, 1630),)),
    ("authority", "authority.binding_recheck.snapshot", ((1490, 1492),)),
    ("authority", "authority.binding_recheck.identity", ((1492, 1500),)),
    ("authority", "authority.binding_recheck.proof_ownership", ((1500, 1508),)),
    ("authority", "authority.binding_recheck.live_precheck", ((1508, 1510),)),
    ("authority", "authority.binding_recheck.proof_shape", ((1510, 1511),)),
    ("authority", "authority.binding_recheck.authority_state", ((1511, 1512),)),
    ("authority", "authority.binding_recheck.live_postcheck", ((1512, 1513),)),
    ("authority", "authority.binding_recheck.final_ownership", ((1513, 1526),)),
    ("authority", "authority.publication_open", ((2227, 2505),)),
    ("confirmation", "confirmation.permit_issue", ((1166, 1369),)),
    ("confirmation", "confirmation.permit_reject", ((1369, 1630),)),
    ("confirmation", "confirmation.publication_fence", ((2185, 2505),)),
    ("cursor", "cursor.content_issue", ((363, 450),)),
    ("cursor", "cursor.content_verify", ((467, 567),)),
    ("coordinator", "coordinator.selection_validate", ((1021, 1042), (1215, 1236))),
    ("coordinator", "coordinator.permit_issue", ((1236, 1254),)),
    ("coordinator", "coordinator.cursor_verify", ((1254, 1274),)),
    ("coordinator", "coordinator.forge_dispatch", ((1274, 1278),)),
    ("coordinator", "coordinator.planning_dispatch", ((1278, 1283),)),
    ("coordinator", "coordinator.knowledge_dispatch", ((1283, 1288),)),
    ("coordinator", "coordinator.inner_page_contract", ((1288, 1307),)),
    ("coordinator", "coordinator.content_cursor_issue", ((1307, 1313),)),
    ("coordinator", "coordinator.response_encode", ((1098, 1105), (1313, 1325))),
    ("coordinator", "coordinator.final_freshness", ((1325, 1329),)),
    ("coordinator", "coordinator.finish", ((1049, 1073), (1329, 1342))),
    ("coordinator", "coordinator.active_guard", ((1042, 1049),)),
    ("coordinator", "coordinator.capture", ((1073, 1090),)),
    ("coordinator", "coordinator.fence", ((1090, 1098),)),
    ("native", "native.execution", ((298, 343),)),
    ("native", "native.recovery", ((343, 350),)),
    ("native", "native.consumption", ((350, 354),)),
    ("planning", "planning.execution", ((180, 228),)),
    ("planning", "planning.failure", ((228, 240),)),
    ("planning", "planning.consumption", ((240, 246),)),
)
_CONTENT_LOCK: Final = threading.RLock()
_CONTENT_SCOPE: ContextVar[object | None] = ContextVar(
    "h7_content_rejection_scope", default=None,
)
_content_state: dict[str, object] | None = None


def _content_invalid(reason: str = "unknown") -> dict[str, object]:
    return {
        "schema": CONTENT_REJECTION_SCHEMA,
        "observer_status": "invalid", "outcome": "invalid",
        "response_class": "invalid", "failing_request_ordinal": None,
        "page_source_class": "invalid", "route_class": "invalid",
        "inner_map_status": "invalid", "inner_source_class": "invalid",
        "inner_branch": "invalid", "inner_locus_id": "invalid",
        "observer_rejection_reason": reason,
        "event_thread_consistent": False,
        "observation_scope_closed": False,
        "release_scope": "observer_disarmed",
        "global_mask_untouched": True,
    }


def _content_projection_valid(value: object) -> bool:
    if type(value) is not dict or set(value) != _CONTENT_KEYS:
        return False
    if (
        value.get("schema") != CONTENT_REJECTION_SCHEMA
        or value.get("observer_status") not in {"complete", "invalid"}
        or value.get("outcome") not in {"failure", "all_success", "invalid"}
        or value.get("response_class") not in {"403", "other_non_200", "all_200", "invalid"}
        or value.get("page_source_class") not in {"forge", "knowledge", "planning", "unknown", "invalid"}
        or value.get("route_class") not in {"app_graph_absent", "runtime_exception_conversion", "unresolved", "invalid"}
        or value.get("inner_map_status") not in {"exact", "unmapped", "invalid"}
        or value.get("inner_source_class") not in {
            "runtime", "authority", "confirmation", "cursor", "coordinator",
            "native", "planning", "app_graph", "unknown", "invalid",
        }
        or type(value.get("inner_branch")) is not str
        or type(value.get("inner_locus_id")) is not str
        or value.get("observer_rejection_reason") not in {
            "none", "projection_shape", "artifact_collision", "overlap",
            "replay", "event_overflow", "event_thread_mismatch",
            "chain_truncated", "traceback_truncated", "unknown",
        }
        or value.get("release_scope") != "observer_disarmed"
        or any(type(value.get(key)) is not bool for key in (
            "event_thread_consistent", "observation_scope_closed",
            "global_mask_untouched",
        ))
    ):
        return False
    ordinal = value.get("failing_request_ordinal")
    if ordinal is not None and (type(ordinal) is not int or not 1 <= ordinal <= 4096):
        return False
    map_status = value["inner_map_status"]
    if map_status == "exact":
        if value["route_class"] == "app_graph_absent":
            locus_valid = (
                value["inner_source_class"] == "app_graph"
                and value["inner_branch"] == "app.graph_absent"
                and value["inner_locus_id"] == "app.handle_mcp_request.graph_absent"
            )
        else:
            locus_valid = False
            for source_class, branch, ranges in _CONTENT_LOCUS_RULES:
                prefix = branch + ".L"
                locus = value["inner_locus_id"]
                if (
                    value["inner_source_class"] == source_class
                    and value["inner_branch"] == branch
                    and locus.startswith(prefix) and locus[len(prefix):].isdigit()
                ):
                    line = int(locus[len(prefix):])
                    locus_valid = any(first <= line < stop for first, stop in ranges)
                    break
        if not locus_valid:
            return False
    elif map_status == "unmapped" and not (
        value["inner_source_class"] == "unknown"
        and value["inner_branch"] in {"unknown", "none"}
        and value["inner_locus_id"] in {"unknown", "none"}
    ):
        return False
    if value["observer_status"] == "complete":
        if not (
            value["event_thread_consistent"]
            and value["observation_scope_closed"]
            and value["global_mask_untouched"]
            and value["observer_rejection_reason"] == "none"
        ):
            return False
        if value["outcome"] == "all_success":
            return (
                ordinal is None and value["response_class"] == "all_200"
                and value["page_source_class"] == "unknown"
                and value["route_class"] == "unresolved"
                and value["inner_map_status"] == "unmapped"
                and value["inner_source_class"] == "unknown"
                and value["inner_branch"] == value["inner_locus_id"] == "none"
            )
        if value["route_class"] == "app_graph_absent" and map_status != "exact":
            return False
        return (
            value["outcome"] == "failure" and type(ordinal) is int
            and value["page_source_class"] in {"forge", "knowledge", "planning"}
            and value["response_class"] in {"403", "other_non_200"}
            and value["route_class"] in {
                "app_graph_absent", "runtime_exception_conversion", "unresolved",
            }
        )
    return (
        value["outcome"] == "invalid" and ordinal is None
        and value["response_class"] == "invalid"
        and value["page_source_class"] == "invalid"
        and value["route_class"] == "invalid"
        and value["inner_map_status"] == "invalid"
        and value["inner_source_class"] == "invalid"
        and value["inner_branch"] == "invalid"
        and value["inner_locus_id"] == "invalid"
        and value["observer_rejection_reason"] != "none"
        and value["event_thread_consistent"] is False
        and value["observation_scope_closed"] is False
        and value["release_scope"] == "observer_disarmed"
        and value["global_mask_untouched"] is True
    )


def encode_content_rejection_projection(value: object) -> bytes:
    if not _content_projection_valid(value):
        raise ValueError("invalid content rejection projection")
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    if len(encoded) > CONTENT_REJECTION_MAX_BYTES:
        raise ValueError("content rejection projection exceeds limit")
    return encoded


def parse_content_rejection_projection(data: object) -> dict[str, object]:
    def exact_object(pairs: object) -> dict[str, object]:
        if type(pairs) is not list:
            raise ValueError("invalid object")
        result: dict[str, object] = {}
        for key, item in pairs:
            if type(key) is not str or key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result

    try:
        if type(data) is not bytes or not 0 < len(data) <= CONTENT_REJECTION_MAX_BYTES:
            return _content_invalid("projection_shape")
        value = json.loads(
            data.decode("ascii"), object_pairs_hook=exact_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if encode_content_rejection_projection(value) != data:
            return _content_invalid("projection_shape")
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return _content_invalid("projection_shape")


def read_content_rejection_projection(path: object) -> dict[str, object]:
    try:
        if type(path) is not type(Path()) or path.name != CONTENT_REJECTION_BASENAME:
            return _content_invalid("projection_shape")
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or getattr(before, "st_file_attributes", 0) & 1024
        ):
            return _content_invalid("projection_shape")
        with path.open("rb") as stream:
            data = stream.read(CONTENT_REJECTION_MAX_BYTES + 1)
        after = path.lstat()
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or after.st_nlink != 1
        ):
            return _content_invalid("projection_shape")
        return parse_content_rejection_projection(data)
    except (FileNotFoundError, OSError):
        return _content_invalid("projection_shape")


def _content_target(value: object) -> Path | None:
    try:
        if type(value) is not str or not value.isascii():
            return None
        target = Path(value)
        if not target.is_absolute() or target.name != CONTENT_REJECTION_BASENAME:
            return None
        parent = target.parent
        if (
            not parent.is_dir() or parent.is_symlink() or _reparse(parent)
            or target.exists() or target.is_symlink()
        ):
            return None
        return target
    except (OSError, ValueError):
        return None


def arm_content_rejection_observation(result_path: object) -> bool:
    """Arm one direct, rejection-only observation after normal app import."""
    global _content_state
    try:
        target = _content_target(result_path)
        if target is None:
            return False
        with _CONTENT_LOCK:
            if _content_state is not None:
                return False
            _content_state = {
                "target": target, "active": None, "event": None,
                "event_thread": None, "requests": 0, "successes": 0,
                "sealed": False, "scope_token": None,
            }
        return True
    except BaseException:
        return False


def begin_content_rejection_observation(ordinal: object, source: object) -> bool:
    try:
        if (
            type(ordinal) is not int or not 1 <= ordinal <= 4096
            or type(source) is not str or source not in {"forge", "knowledge", "planning"}
        ):
            return False
        with _CONTENT_LOCK:
            state = _content_state
            if state is None or state["sealed"]:
                return False
            if state["active"] is not None:
                state["event"] = ("invalid", "invalid", "invalid", "invalid", "overlap")
                return False
            marker = object()
            token = _CONTENT_SCOPE.set(marker)
            state["active"] = (ordinal, source, marker)
            state["scope_token"] = token
            state["event"] = None
            state["event_thread"] = None
            state["requests"] = int(state["requests"]) + 1
        return True
    except BaseException:
        return False


def _native_exception_projection(
    exception: object, trusted_code_map: object,
) -> tuple[str, str, str, str, str]:
    """Inspect only native exception/traceback descriptors and pinned code identities."""
    current = exception
    seen: set[int] = set()
    deepest: tuple[str, str, str] | None = None
    try:
        if type(trusted_code_map) is not tuple:
            return "invalid", "invalid", "invalid", "invalid", "projection_shape"
        for _chain_index in range(CONTENT_REJECTION_MAX_CHAIN):
            if not isinstance(current, BaseException) or id(current) in seen:
                return "invalid", "invalid", "invalid", "invalid", "chain_truncated"
            seen.add(id(current))
            traceback = BaseException.__traceback__.__get__(current, type(current))
            frames: list[tuple[object, int]] = []
            while traceback is not None and len(frames) < CONTENT_REJECTION_MAX_TRACEBACK:
                if type(traceback) is not TracebackType:
                    return "invalid", "invalid", "invalid", "invalid", "projection_shape"
                frame = TracebackType.tb_frame.__get__(traceback, TracebackType)
                line = TracebackType.tb_lineno.__get__(traceback, TracebackType)
                code = FrameType.f_code.__get__(frame, FrameType)
                if type(line) is not int:
                    return "invalid", "invalid", "invalid", "invalid", "projection_shape"
                frames.append((code, line))
                traceback = TracebackType.tb_next.__get__(traceback, TracebackType)
            if traceback is not None:
                return "invalid", "invalid", "invalid", "invalid", "traceback_truncated"
            transparent = any(
                type(row) is tuple and len(row) == 5
                and row[0] == "transparent_wrapper"
                and type(current) is row[1]
                and any(code is row[2] and row[3] <= line < row[4] for code, line in frames)
                for row in trusted_code_map
            )
            if not transparent:
                for code, line in frames:
                    for row in trusted_code_map:
                        if (
                            type(row) is tuple and len(row) == 5
                            and row[0] != "transparent_wrapper" and code is row[0]
                            and row[1] <= line < row[2]
                        ):
                            deepest = (row[3], row[4], row[4] + ".L" + str(line))
            cause = BaseException.__cause__.__get__(current, type(current))
            context = BaseException.__context__.__get__(current, type(current))
            suppressed = BaseException.__suppress_context__.__get__(current, type(current))
            following = (
                cause if cause is not None
                else context if transparent
                else None if suppressed
                else context
            )
            if following is None:
                if deepest is None:
                    return "unmapped", "unknown", "unknown", "unknown", "none"
                return "exact", deepest[0], deepest[1], deepest[2], "none"
            current = following
        return "invalid", "invalid", "invalid", "invalid", "chain_truncated"
    except BaseException:
        return "invalid", "invalid", "invalid", "invalid", "projection_shape"
    finally:
        current = None
        exception = None
        trusted_code_map = None
        deepest = None
        frames = []


def record_content_rejection(
    exception: object, method: object, trusted_code_map: object,
) -> bool:
    """Record a runtime rejection before the transport converts it to 403."""
    try:
        if method != "harbour_context_page":
            return False
        with _CONTENT_LOCK:
            state = _content_state
            if state is None or state["sealed"] or state["active"] is None:
                return False
            active_marker = state["active"][2]
            if _CONTENT_SCOPE.get() is not active_marker:
                return False
        mapped = _native_exception_projection(exception, trusted_code_map)
        thread_id = threading.get_ident()
        with _CONTENT_LOCK:
            state = _content_state
            if state is None or state["sealed"] or state["active"] is None:
                return False
            marker = state["active"][2]
            if marker is not active_marker or _CONTENT_SCOPE.get() is not marker:
                return False
            if state["event"] is not None:
                state["event"] = ("invalid", "invalid", "invalid", "invalid", "event_overflow")
                return False
            prior_thread = state["event_thread"]
            if prior_thread is not None and prior_thread != thread_id:
                state["event"] = ("invalid", "invalid", "invalid", "invalid", "event_thread_mismatch")
                return False
            state["event_thread"] = thread_id
            state["event"] = mapped
        return mapped[0] != "invalid"
    except BaseException:
        return False
    finally:
        active_marker = None
        exception = None
        trusted_code_map = None


def record_app_graph_absent() -> bool:
    """Record the fixed application graph-absent rejection during an active Page POST."""
    try:
        thread_id = threading.get_ident()
        with _CONTENT_LOCK:
            state = _content_state
            if state is None or state["sealed"] or state["active"] is None:
                return False
            marker = state["active"][2]
            if _CONTENT_SCOPE.get() is not marker:
                return False
            if state["event"] is not None:
                state["event"] = ("invalid", "invalid", "invalid", "invalid", "event_overflow")
                return False
            state["event_thread"] = thread_id
            state["event"] = (
                "exact", "app_graph", "app.graph_absent",
                "app.handle_mcp_request.graph_absent", "none",
            )
        return True
    except BaseException:
        return False


def _seal_content_projection(value: dict[str, object]) -> bool:
    global _content_state
    try:
        encoded = encode_content_rejection_projection(value)
        with _CONTENT_LOCK:
            state = _content_state
            if state is None or state["sealed"]:
                return False
            state["sealed"] = True
            target = state["target"]
            _content_state = None
        if type(target) is not type(Path()):
            return False
        with target.open("xb") as stream:
            stream.write(encoded)
        return True
    except BaseException:
        with _CONTENT_LOCK:
            _content_state = None
        return False


def end_content_rejection_observation(status: object) -> bool:
    """End one Page POST and seal immediately on its first non-200 response."""
    try:
        if type(status) is not int or not 100 <= status <= 599:
            return False
        with _CONTENT_LOCK:
            state = _content_state
            if state is None or state["sealed"] or type(state["active"]) is not tuple:
                return False
            ordinal, source, marker = state["active"]
            if _CONTENT_SCOPE.get() is not marker:
                return False
            token = state["scope_token"]
            event = state["event"]
            event_thread = state["event_thread"]
            state["active"] = None
            state["event"] = None
            state["event_thread"] = None
            state["scope_token"] = None
            if type(token) is Token:
                _CONTENT_SCOPE.reset(token)
            if status == 200 and event is None:
                state["successes"] = int(state["successes"]) + 1
                return True
        if status == 200:
            return _seal_content_projection(_content_invalid("event_overflow"))
        response_class = "403" if status == 403 else "other_non_200"
        if event is None:
            map_status, source_class, branch, locus, reason = (
                "unmapped", "unknown", "none", "none", "none",
            )
            route = "unresolved"
        else:
            map_status, source_class, branch, locus, reason = event
            route = (
                "app_graph_absent" if source_class == "app_graph"
                else "runtime_exception_conversion"
            )
        if reason != "none" or map_status == "invalid":
            return _seal_content_projection(_content_invalid(reason))
        projection = {
            "schema": CONTENT_REJECTION_SCHEMA,
            "observer_status": "complete", "outcome": "failure",
            "response_class": response_class, "failing_request_ordinal": ordinal,
            "page_source_class": source, "route_class": route,
            "inner_map_status": map_status, "inner_source_class": source_class,
            "inner_branch": branch, "inner_locus_id": locus,
            "observer_rejection_reason": "none",
            "event_thread_consistent": event_thread is not None or event is None,
            "observation_scope_closed": True,
            "release_scope": "observer_disarmed",
            "global_mask_untouched": True,
        }
        return _seal_content_projection(projection)
    except BaseException:
        return False


def finalize_content_rejection_success() -> bool:
    """Seal the all-200 result after every Page POST scope has closed."""
    try:
        with _CONTENT_LOCK:
            state = _content_state
            if (
                state is None or state["sealed"] or state["active"] is not None
                or state["event"] is not None or state["requests"] == 0
                or state["requests"] != state["successes"]
            ):
                return False
        return _seal_content_projection({
            "schema": CONTENT_REJECTION_SCHEMA,
            "observer_status": "complete", "outcome": "all_success",
            "response_class": "all_200", "failing_request_ordinal": None,
            "page_source_class": "unknown", "route_class": "unresolved",
            "inner_map_status": "unmapped", "inner_source_class": "unknown",
            "inner_branch": "none", "inner_locus_id": "none",
            "observer_rejection_reason": "none",
            "event_thread_consistent": True,
            "observation_scope_closed": True,
            "release_scope": "observer_disarmed",
            "global_mask_untouched": True,
        })
    except BaseException:
        return False


__all__ = [
    "CHILD_ARTIFACT_BASENAME",
    "CONTROLLER_EXPORT_BASENAME",
    "CONTROLLER_JUNIT_RELATIVE",
    "DIAGNOSTIC_FLAG",
    "MAX_PROJECTION_BYTES",
    "SCHEMA",
    "SPAN_LITERALS",
    "capture_phase_enter",
    "capture_phase_exit",
    "collect_projection",
    "controller_projection_path",
    "diagnostic_enabled",
    "downstream_timeout",
    "encode_projection",
    "fixed_invalid_projection",
    "parse_projection_bytes",
    "read_child_projection",
    "read_controller_projection",
    "response_start",
    "runtime_context_enter",
    "runtime_context_exit",
    "seal",
    "snapshot_source_begin",
    "span_begin",
    "span_end",
    "timeout_context_enter",
    "wrapper_before_wait",
    "wrapper_cancelled",
    "wrapper_enter",
    "wrapper_exception",
    "wrapper_timeout_catch",
    "write_child_projection_exclusive",
    "write_controller_projection_exclusive",
    "CONTENT_REJECTION_BASENAME",
    "CONTENT_REJECTION_MAX_BYTES",
    "CONTENT_REJECTION_SCHEMA",
    "arm_content_rejection_observation",
    "begin_content_rejection_observation",
    "encode_content_rejection_projection",
    "end_content_rejection_observation",
    "finalize_content_rejection_success",
    "parse_content_rejection_projection",
    "read_content_rejection_projection",
    "record_app_graph_absent",
    "record_content_rejection",
]

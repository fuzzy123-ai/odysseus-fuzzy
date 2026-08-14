"""Content-free non-atomic status projection for accepted USI components."""

from __future__ import annotations

from src.unified_source_index_runtime import KnowledgeRuntime
from src.unified_source_index_job_runner import (
    COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA,
    CooperativeIndexJobPump,
)


RUNTIME_STATUS_SCHEMA = "odysseus.unified_source_index.runtime_status.v1"

_KNOWLEDGE_SNAPSHOT_SCHEMA = "odysseus.knowledge_runtime.composition_snapshot.v1"
_ERROR_CODES = frozenset({"invalid_component", "invalid_snapshot", "snapshot_failed"})
_KNOWLEDGE_FIELDS = frozenset({
    "schema",
    "state",
    "mode",
    "planner_bound",
    "error_code",
    "live_activation_authorized",
})
_PUMP_FIELDS = frozenset({
    "schema",
    "state",
    "enabled",
    "step_active",
    "close_requested",
})
_KNOWLEDGE_STATES = frozenset({"disabled", "ready", "degraded", "closed"})
_KNOWLEDGE_MODES = frozenset({
    "disabled",
    "read_only",
    "shadow",
    "canary",
    "active",
    "degraded",
    "rollback",
})
_KNOWLEDGE_ERROR_CODES = frozenset({
    "invalid_planner",
    "planner_unavailable",
    "mode_not_composed",
    "bind_failed",
    "close_failed",
})
_PUMP_STATES = frozenset({"idle", "stepping", "draining", "closed"})


class UnifiedSourceIndexRuntimeStatusError(RuntimeError):
    """Fresh fixed-code failure at the status projection boundary."""

    __slots__ = ()

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_CODES:
            code = "invalid_snapshot"
        RuntimeError.__init__(self, code)


def _has_exact_fields(snapshot: object, fields: frozenset[str]) -> bool:
    if type(snapshot) is not dict or len(snapshot) != len(fields):
        return False
    keys = tuple(snapshot)
    return all(type(key) is str for key in keys) and frozenset(keys) == fields


def _knowledge_projection(snapshot: object) -> dict[str, object]:
    if not _has_exact_fields(snapshot, _KNOWLEDGE_FIELDS):
        raise ValueError
    schema = snapshot["schema"]
    state = snapshot["state"]
    mode = snapshot["mode"]
    planner_bound = snapshot["planner_bound"]
    error_code = snapshot["error_code"]
    live_activation_authorized = snapshot["live_activation_authorized"]
    if (
        type(schema) is not str
        or schema != _KNOWLEDGE_SNAPSHOT_SCHEMA
        or type(state) is not str
        or state not in _KNOWLEDGE_STATES
        or type(mode) is not str
        or mode not in _KNOWLEDGE_MODES
        or type(planner_bound) is not bool
        or (
            error_code is not None
            and (type(error_code) is not str or error_code not in _KNOWLEDGE_ERROR_CODES)
        )
        or type(live_activation_authorized) is not bool
        or live_activation_authorized
    ):
        raise ValueError
    return {
        "state": state,
        "mode": mode,
        "planner_bound": planner_bound,
        "error_code": error_code,
    }


def _pump_projection(snapshot: object) -> dict[str, object]:
    if not _has_exact_fields(snapshot, _PUMP_FIELDS):
        raise ValueError
    schema = snapshot["schema"]
    state = snapshot["state"]
    enabled = snapshot["enabled"]
    step_active = snapshot["step_active"]
    close_requested = snapshot["close_requested"]
    if (
        type(schema) is not str
        or schema != COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA
        or type(state) is not str
        or state not in _PUMP_STATES
        or type(enabled) is not bool
        or type(step_active) is not bool
        or type(close_requested) is not bool
    ):
        raise ValueError
    return {
        "state": state,
        "enabled": enabled,
        "step_active": step_active,
        "close_requested": close_requested,
    }


def project_unified_source_index_runtime_status(
    knowledge_runtime: KnowledgeRuntime,
    index_job_pump: CooperativeIndexJobPump,
    /,
) -> dict[str, object]:
    """Return one detached, non-authorizing view of two independent snapshots."""

    if (
        type(knowledge_runtime) is not KnowledgeRuntime
        or type(index_job_pump) is not CooperativeIndexJobPump
    ):
        raise UnifiedSourceIndexRuntimeStatusError("invalid_component") from None
    try:
        knowledge_snapshot = knowledge_runtime.snapshot()
        pump_snapshot = index_job_pump.snapshot()
    except BaseException:
        raise UnifiedSourceIndexRuntimeStatusError("snapshot_failed") from None
    try:
        knowledge = _knowledge_projection(knowledge_snapshot)
        pump = _pump_projection(pump_snapshot)
    except BaseException:
        raise UnifiedSourceIndexRuntimeStatusError("invalid_snapshot") from None
    return {
        "schema": RUNTIME_STATUS_SCHEMA,
        "knowledge": knowledge,
        "index_job_pump": pump,
        "snapshot_atomic": False,
        "live_activation_authorized": False,
    }


__all__ = (
    "RUNTIME_STATUS_SCHEMA",
    "UnifiedSourceIndexRuntimeStatusError",
    "project_unified_source_index_runtime_status",
)

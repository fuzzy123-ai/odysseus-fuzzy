"""Synchronous one-step pump for the durable Unified Source Index job runtime."""

from __future__ import annotations

from threading import Lock

from src.unified_source_index_adapters import SourceAdapter
from src.unified_source_index_jobs import (
    JobStepResult,
    ProjectionSink,
    UnifiedSourceIndexJobRuntime,
)
from src.unified_source_index_runtime_config import (
    UnifiedSourceIndexRuntimeConfig,
    UnifiedSourceIndexRuntimeMode,
)
from src.unified_source_index_runtime_contract import (
    RuntimeGeneration,
    RuntimeHealth,
    RuntimeHealthState,
    RuntimeMode,
    RuntimeStateRecord,
    WorkerPolicy,
)


COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA = (
    "odysseus.unified_source_index.cooperative_job_pump.v1"
)

_ERROR_CODES = frozenset({
    "busy",
    "closed",
    "inert",
    "invalid_gate",
    "invalid_runtime",
    "step_failed",
})
_ELIGIBLE_MODES = frozenset({"shadow", "canary", "active"})


class CooperativeIndexJobPumpError(RuntimeError):
    """Fixed, content-free pump boundary error."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_CODES:
            code = "invalid_gate"
        RuntimeError.__init__(self, code)


class CooperativeIndexJobPump:
    """Run at most one explicit durable job step per synchronous wakeup."""

    def __init__(
        self,
        runtime: UnifiedSourceIndexJobRuntime | None,
        config: UnifiedSourceIndexRuntimeConfig,
        state: RuntimeStateRecord,
    ) -> None:
        if type(config) is not UnifiedSourceIndexRuntimeConfig or type(state) is not RuntimeStateRecord:
            raise CooperativeIndexJobPumpError("invalid_gate") from None
        try:
            config_mode = config.mode
            runtime_enabled = config.runtime_enabled
            selected_generation = config.selected_generation
            state_mode = state.mode
            state_generation = state.generation
            worker_policy = state.worker_policy
            if state_generation is None:
                state_generation_ref = None
            elif type(state_generation) is RuntimeGeneration:
                state_generation_ref = state_generation.generation_ref
            else:
                raise ValueError
            if (
                type(config_mode) is not UnifiedSourceIndexRuntimeMode
                or type(runtime_enabled) is not bool
                or (selected_generation is not None and type(selected_generation) is not str)
                or type(state_mode) is not RuntimeMode
                or (state_generation is not None and type(state_generation) is not RuntimeGeneration)
                or (state_generation_ref is not None and type(state_generation_ref) is not str)
                or type(worker_policy) is not WorkerPolicy
            ):
                raise ValueError
            mode = config_mode.value
            if mode != state_mode.value:
                raise ValueError
            if runtime_enabled:
                if (
                    mode == "disabled"
                    or type(selected_generation) is not str
                    or state_generation is None
                    or state_generation_ref != selected_generation
                ):
                    raise ValueError
            elif (
                mode != "disabled"
                or selected_generation is not None
                or state_generation is not None
            ):
                raise ValueError
            enabled = bool(
                runtime_enabled
                and mode in _ELIGIBLE_MODES
                and worker_policy is WorkerPolicy.RUNNING
            )
        except BaseException:
            raise CooperativeIndexJobPumpError("invalid_gate") from None

        if enabled:
            if type(runtime) is not UnifiedSourceIndexJobRuntime:
                raise CooperativeIndexJobPumpError("invalid_runtime") from None
        elif runtime is not None:
            raise CooperativeIndexJobPumpError("invalid_runtime") from None

        self._runtime = runtime
        self._enabled = enabled
        self._lock = Lock()
        self._state = "idle"
        self._step_active = False
        self._close_requested = False

    def snapshot(self, /) -> dict[str, object]:
        with self._lock:
            return {
                "schema": COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA,
                "state": self._state,
                "enabled": self._enabled,
                "step_active": self._step_active,
                "close_requested": self._close_requested,
            }

    def wakeup(
        self,
        job_id: str,
        /,
        *,
        owner_scope: str,
        worker_id: str,
        adapter: SourceAdapter,
        now: str,
        projection: ProjectionSink | None = None,
        lease_seconds: int = 60,
    ) -> JobStepResult:
        with self._lock:
            if self._state in {"draining", "closed"}:
                raise CooperativeIndexJobPumpError("closed") from None
            if self._step_active:
                raise CooperativeIndexJobPumpError("busy") from None
            if not self._enabled:
                raise CooperativeIndexJobPumpError("inert") from None
            self._state = "stepping"
            self._step_active = True

        try:
            lease = self._runtime.acquire(
                job_id,
                owner_scope=owner_scope,
                worker_id=worker_id,
                adapter=adapter,
                now=now,
                lease_seconds=lease_seconds,
            )
            result = self._runtime.run_next(
                lease,
                adapter=adapter,
                now=now,
                projection=projection,
            )
            if type(result) is not JobStepResult:
                raise CooperativeIndexJobPumpError("step_failed")
        except BaseException:
            with self._lock:
                self._step_active = False
                self._state = "closed" if self._close_requested else "idle"
            raise CooperativeIndexJobPumpError("step_failed") from None

        with self._lock:
            self._step_active = False
            self._state = "closed" if self._close_requested else "idle"
        return result

    def close(self, /) -> None:
        with self._lock:
            if self._state == "closed":
                return
            self._close_requested = True
            self._state = "draining" if self._step_active else "closed"


def build_inert_cooperative_index_job_pump(
    config: UnifiedSourceIndexRuntimeConfig,
    /,
) -> CooperativeIndexJobPump:
    """Build a stopped, non-authorizing pump for an exact inert config."""

    if type(config) is not UnifiedSourceIndexRuntimeConfig:
        raise CooperativeIndexJobPumpError("invalid_gate") from None
    try:
        mode = config.mode
        runtime_enabled = config.runtime_enabled
        selected_generation = config.selected_generation
        if (
            type(mode) is not UnifiedSourceIndexRuntimeMode
            or type(runtime_enabled) is not bool
            or (selected_generation is not None and type(selected_generation) is not str)
        ):
            raise ValueError
        if mode is UnifiedSourceIndexRuntimeMode.DISABLED:
            if runtime_enabled or selected_generation is not None:
                raise ValueError
            state = RuntimeStateRecord(
                RuntimeMode.DISABLED,
                None,
                (),
                RuntimeHealth(RuntimeHealthState.DISABLED, ()),
                WorkerPolicy.STOPPED,
                True,
                False,
                True,
                False,
            )
        elif mode is UnifiedSourceIndexRuntimeMode.READ_ONLY:
            if not runtime_enabled or type(selected_generation) is not str:
                raise ValueError
            state = RuntimeStateRecord(
                RuntimeMode.READ_ONLY,
                RuntimeGeneration(selected_generation),
                (),
                RuntimeHealth(RuntimeHealthState.READY, ()),
                WorkerPolicy.STOPPED,
                True,
                False,
                True,
                False,
            )
        else:
            raise ValueError
    except BaseException:
        raise CooperativeIndexJobPumpError("invalid_gate") from None
    return CooperativeIndexJobPump(None, config, state)


__all__ = (
    "COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA",
    "CooperativeIndexJobPump",
    "CooperativeIndexJobPumpError",
    "build_inert_cooperative_index_job_pump",
)

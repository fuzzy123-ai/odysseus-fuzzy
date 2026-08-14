from __future__ import annotations

import ast
import asyncio
import builtins
import importlib
import inspect
import multiprocessing
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading

import pytest

from src.unified_source_index_jobs import JobStepResult, UnifiedSourceIndexJobRuntime
from src.unified_source_index_runtime_config import (
    UnifiedSourceIndexRuntimeConfig,
    UnifiedSourceIndexRuntimeMode,
)
from src.unified_source_index_runtime_contract import (
    DomainScope,
    FallbackReason,
    ProviderCapability,
    ProviderHealth,
    ProviderKind,
    RuntimeGeneration,
    RuntimeHealth,
    RuntimeHealthState,
    RuntimeMode,
    RuntimeStateRecord,
    SelectedScope,
    WorkerPolicy,
)


GENERATION = "usi_generation_" + "a" * 64
PREVIOUS_GENERATION = "usi_generation_" + "b" * 64
SCOPE = "usi_scope_" + "c" * 64
NOW = "2026-08-10T16:30:00Z"


def _runner_module():
    return importlib.import_module("src.unified_source_index_job_runner")


def _config(tmp_path: Path, mode: UnifiedSourceIndexRuntimeMode):
    if mode is UnifiedSourceIndexRuntimeMode.DISABLED:
        return UnifiedSourceIndexRuntimeConfig.for_test((tmp_path / "disabled.sqlite3").resolve())
    return UnifiedSourceIndexRuntimeConfig.from_environment(
        {
            "ODYSSEUS_USI_RUNTIME_ENABLED": "true",
            "ODYSSEUS_USI_RUNTIME_MODE": mode.value,
            "ODYSSEUS_USI_SELECTED_GENERATION": GENERATION,
            "ODYSSEUS_USI_ALLOWED_OWNERS": "owner_a",
            "ODYSSEUS_USI_ALLOWED_SOURCES": "source_a",
            "ODYSSEUS_USI_ALLOWED_DOMAINS": "document",
        },
        data_root=tmp_path,
    )


def _state(mode: RuntimeMode, *, worker_policy: WorkerPolicy | None = None):
    policy = worker_policy
    if policy is None:
        policy = WorkerPolicy.RUNNING if mode in {
            RuntimeMode.SHADOW,
            RuntimeMode.CANARY,
            RuntimeMode.ACTIVE,
        } else WorkerPolicy.STOPPED
    generation = RuntimeGeneration(GENERATION)
    scopes = (SelectedScope(SCOPE, (DomainScope.DOCUMENT,), 1, True),)
    health = RuntimeHealth(
        RuntimeHealthState.READY,
        (ProviderCapability(ProviderKind.LEXICAL, ProviderHealth.READY),),
    )
    values = {
        "mode": mode,
        "generation": generation,
        "selected_scopes": scopes,
        "health": health,
        "worker_policy": policy,
        "legacy_authoritative": False,
        "prompt_injection": True,
        "fallback_enabled": True,
    }
    if mode is RuntimeMode.DISABLED:
        values.update(
            generation=None,
            selected_scopes=(),
            health=RuntimeHealth(RuntimeHealthState.DISABLED, ()),
            worker_policy=WorkerPolicy.STOPPED,
            legacy_authoritative=True,
            prompt_injection=False,
        )
    elif mode is RuntimeMode.READ_ONLY:
        values.update(
            selected_scopes=(),
            legacy_authoritative=True,
            prompt_injection=False,
        )
    elif mode is RuntimeMode.SHADOW:
        values.update(legacy_authoritative=True, prompt_injection=False)
    elif mode is RuntimeMode.DEGRADED:
        values.update(
            health=RuntimeHealth(
                RuntimeHealthState.DEGRADED,
                (),
                (FallbackReason.CORE_UNAVAILABLE,),
            ),
            worker_policy=WorkerPolicy.STOPPED,
            legacy_authoritative=True,
            prompt_injection=False,
        )
    elif mode is RuntimeMode.ROLLBACK:
        values.update(
            generation=RuntimeGeneration(GENERATION, PREVIOUS_GENERATION),
            health=RuntimeHealth(
                RuntimeHealthState.READY,
                (ProviderCapability(ProviderKind.LEXICAL, ProviderHealth.READY),),
                (FallbackReason.ROLLBACK_ACTIVE,),
            ),
            worker_policy=WorkerPolicy.STOPPED,
        )
    return RuntimeStateRecord(**values)


def _result():
    return object.__new__(JobStepResult)


def _runtime(*, acquire=None, run_next=None, cancel=None):
    runtime = object.__new__(UnifiedSourceIndexJobRuntime)
    runtime.acquire = acquire or (lambda *args, **kwargs: object())
    runtime.run_next = run_next or (lambda *args, **kwargs: _result())
    runtime.cancel = cancel or (lambda *args, **kwargs: pytest.fail("cancel must not be called"))
    return runtime


_DEFAULT_RUNTIME = object()


def _pump(tmp_path: Path, *, runtime=_DEFAULT_RUNTIME, mode=RuntimeMode.SHADOW, policy=None):
    module = _runner_module()
    config_mode = UnifiedSourceIndexRuntimeMode(mode.value)
    state = _state(mode, worker_policy=policy)
    enabled = (
        mode in (RuntimeMode.SHADOW, RuntimeMode.CANARY, RuntimeMode.ACTIVE)
        and state.worker_policy is WorkerPolicy.RUNNING
    )
    if runtime is _DEFAULT_RUNTIME:
        runtime = _runtime() if enabled else None
    return module.CooperativeIndexJobPump(
        runtime,
        _config(tmp_path, config_mode),
        state,
    )


def _wakeup(pump, **overrides):
    values = {
        "job_id": "job:one",
        "owner_scope": "user:alice",
        "worker_id": "worker.one",
        "adapter": object(),
        "now": NOW,
        "projection": None,
        "lease_seconds": 60,
    }
    values.update(overrides)
    job_id = values.pop("job_id")
    return pump.wakeup(job_id, **values)


def test_uir04a_public_contract_is_exact_and_snapshots_are_fresh_content_free(tmp_path):
    module = _runner_module()
    assert module.__all__ == (
        "COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA",
        "CooperativeIndexJobPump",
        "CooperativeIndexJobPumpError",
        "build_inert_cooperative_index_job_pump",
    )
    assert module.COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA == (
        "odysseus.unified_source_index.cooperative_job_pump.v1"
    )
    assert tuple(inspect.signature(module.CooperativeIndexJobPump).parameters) == (
        "runtime", "config", "state",
    )
    wakeup = inspect.signature(module.CooperativeIndexJobPump.wakeup)
    assert tuple(wakeup.parameters) == (
        "self", "job_id", "owner_scope", "worker_id", "adapter", "now",
        "projection", "lease_seconds",
    )
    assert wakeup.parameters["job_id"].kind is inspect.Parameter.POSITIONAL_ONLY
    assert all(
        wakeup.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("owner_scope", "worker_id", "adapter", "now", "projection", "lease_seconds")
    )
    assert wakeup.parameters["projection"].default is None
    assert wakeup.parameters["lease_seconds"].default == 60

    pump = _pump(tmp_path)
    first = pump.snapshot()
    second = pump.snapshot()
    assert first == second == {
        "schema": module.COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA,
        "state": "idle",
        "enabled": True,
        "step_active": False,
        "close_requested": False,
    }
    assert first is not second
    first["state"] = "private-marker"
    assert pump.snapshot()["state"] == "idle"
    assert set(pump.snapshot()) == {"schema", "state", "enabled", "step_active", "close_requested"}
    for code in ("busy", "closed", "inert", "invalid_gate", "invalid_runtime", "step_failed"):
        error = module.CooperativeIndexJobPumpError(code)
        assert error.args == (code,)
        assert "private-marker" not in str(error)


def test_uir04a_constructor_captures_exact_coherent_gate_without_effects(tmp_path):
    module = _runner_module()
    runtime = _runtime()
    config = _config(tmp_path, UnifiedSourceIndexRuntimeMode.SHADOW)
    state = _state(RuntimeMode.SHADOW)
    pump = module.CooperativeIndexJobPump(runtime, config, state)
    assert pump.snapshot()["enabled"] is True

    class RuntimeSubclass(UnifiedSourceIndexJobRuntime):
        pass

    with pytest.raises(module.CooperativeIndexJobPumpError) as wrong_runtime:
        module.CooperativeIndexJobPump(object.__new__(RuntimeSubclass), config, state)
    assert wrong_runtime.value.args == ("invalid_runtime",)
    for wrong_config, wrong_state in ((object(), state), (config, object())):
        with pytest.raises(module.CooperativeIndexJobPumpError) as invalid:
            module.CooperativeIndexJobPump(runtime, wrong_config, wrong_state)
        assert invalid.value.args == ("invalid_gate",)

    mismatched_mode = _state(RuntimeMode.CANARY)
    mismatched_generation = _state(RuntimeMode.SHADOW)
    object.__setattr__(mismatched_generation, "generation", RuntimeGeneration("usi_generation_" + "d" * 64))
    hostile_generation = _state(RuntimeMode.SHADOW)

    class HostileGenerationRef:
        def __eq__(self, other):
            raise AssertionError("hostile generation equality invoked")

        def __str__(self):
            raise AssertionError("hostile generation string invoked")

    object.__setattr__(hostile_generation.generation, "generation_ref", HostileGenerationRef())
    for bad_state in (mismatched_mode, mismatched_generation, hostile_generation):
        with pytest.raises(module.CooperativeIndexJobPumpError) as invalid:
            module.CooperativeIndexJobPump(runtime, config, bad_state)
        assert invalid.value.args == ("invalid_gate",)


def test_uir04a_disabled_read_only_degraded_rollback_and_nonrunning_policy_execute_zero_steps(tmp_path):
    module = _runner_module()
    for mode in (RuntimeMode.DISABLED, RuntimeMode.READ_ONLY, RuntimeMode.DEGRADED, RuntimeMode.ROLLBACK):
        pump = _pump(tmp_path / mode.value, runtime=None, mode=mode)
        with pytest.raises(module.CooperativeIndexJobPumpError) as inert:
            _wakeup(pump)
        assert inert.value.args == ("inert",)
        assert pump._runtime is None

    for policy in (WorkerPolicy.STOPPED, WorkerPolicy.READ_ONLY):
        pump = _pump(tmp_path / policy.value, runtime=None, policy=policy)
        with pytest.raises(module.CooperativeIndexJobPumpError) as inert:
            _wakeup(pump)
        assert inert.value.args == ("inert",)
        assert pump._runtime is None


def test_uir04a_one_wakeup_performs_one_acquire_and_one_run_next_without_queue_or_loop(tmp_path):
    calls = []
    lease = object()
    result = _result()

    def acquire(*args, **kwargs):
        calls.append(("acquire", args, kwargs))
        return lease

    def run_next(*args, **kwargs):
        calls.append(("run_next", args, kwargs))
        return result

    pump = _pump(tmp_path, runtime=_runtime(acquire=acquire, run_next=run_next))
    assert _wakeup(pump) is result
    assert [item[0] for item in calls] == ["acquire", "run_next"]
    assert calls[0][1] == ("job:one",)
    assert calls[0][2]["owner_scope"] == "user:alice"
    assert calls[0][2]["worker_id"] == "worker.one"
    assert calls[0][2]["lease_seconds"] == 60
    assert calls[1][1] == (lease,)
    assert "owner_scope" not in calls[1][2]
    assert pump.snapshot()["state"] == "idle"
    assert not any(hasattr(pump, name) for name in ("queue", "pending", "lease", "cursor", "job"))


def test_uir04a_duplicate_and_concurrent_wakeups_reject_busy_with_one_active_step(tmp_path):
    module = _runner_module()
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def acquire(*args, **kwargs):
        calls.append("acquire")
        entered.set()
        assert release.wait(5)
        return object()

    def run_next(*args, **kwargs):
        calls.append("run_next")
        return _result()

    pump = _pump(tmp_path, runtime=_runtime(acquire=acquire, run_next=run_next))
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(_wakeup(pump)))
    worker.start()
    assert entered.wait(5)
    with pytest.raises(module.CooperativeIndexJobPumpError) as duplicate:
        _wakeup(pump, job_id="job:duplicate")
    assert duplicate.value.args == ("busy",)
    assert pump.snapshot()["step_active"] is True
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert len(outcomes) == 1
    assert calls == ["acquire", "run_next"]


def test_uir04a_reentrant_wakeup_rejects_and_reentrant_close_marks_draining_without_deadlock(tmp_path):
    module = _runner_module()
    holder = {}
    calls = []

    def acquire(*args, **kwargs):
        calls.append("acquire")
        with pytest.raises(module.CooperativeIndexJobPumpError) as reentrant:
            _wakeup(holder["pump"], job_id="job:reentrant")
        assert reentrant.value.args == ("busy",)
        holder["pump"].close()
        assert holder["pump"].snapshot()["state"] == "draining"
        return object()

    def run_next(*args, **kwargs):
        calls.append("run_next")
        return _result()

    holder["pump"] = _pump(tmp_path, runtime=_runtime(acquire=acquire, run_next=run_next))
    assert _wakeup(holder["pump"]) is not None
    assert calls == ["acquire", "run_next"]
    assert holder["pump"].snapshot() == {
        "schema": module.COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA,
        "state": "closed",
        "enabled": True,
        "step_active": False,
        "close_requested": True,
    }


def test_uir04a_close_is_idempotent_prevents_new_work_and_never_calls_cancel(tmp_path):
    module = _runner_module()
    calls = []
    runtime = _runtime(
        acquire=lambda *a, **k: calls.append("acquire"),
        cancel=lambda *a, **k: calls.append("cancel"),
    )
    pump = _pump(tmp_path, runtime=runtime)
    pump.close()
    pump.close()
    assert pump.snapshot()["state"] == "closed"
    with pytest.raises(module.CooperativeIndexJobPumpError) as closed:
        _wakeup(pump)
    assert closed.value.args == ("closed",)
    assert calls == []

    after = _pump(tmp_path / "after", runtime=runtime)
    _wakeup(after)
    after.close()
    after.close()
    assert after.snapshot()["state"] == "closed"
    assert "cancel" not in calls


def test_uir04a_step_failures_are_fresh_fixed_content_free_and_never_retried(tmp_path):
    module = _runner_module()

    class Marker(BaseException):
        def __str__(self):
            raise AssertionError("raw marker string observed")

        def __repr__(self):
            raise AssertionError("raw marker repr observed")

    for failing_stage in ("acquire", "run_next"):
        calls = []

        def acquire(*args, **kwargs):
            calls.append("acquire")
            if failing_stage == "acquire":
                raise Marker()
            return object()

        def run_next(*args, **kwargs):
            calls.append("run_next")
            raise Marker()

        pump = _pump(tmp_path / failing_stage, runtime=_runtime(acquire=acquire, run_next=run_next))
        errors = []
        for _ in range(2):
            with pytest.raises(module.CooperativeIndexJobPumpError) as failed:
                _wakeup(pump, lease_seconds=10**100)
            errors.append(failed.value)
            assert failed.value.args == ("step_failed",)
            assert failed.value.__cause__ is None
            assert pump.snapshot()["state"] == "idle"
        assert errors[0] is not errors[1]
        expected = ["acquire", "acquire"] if failing_stage == "acquire" else [
            "acquire", "run_next", "acquire", "run_next",
        ]
        assert calls == expected


def test_uir04a_mutation_after_gate_capture_cannot_enable_or_redirect_work(tmp_path):
    module = _runner_module()
    inert_config = _config(tmp_path / "inert", UnifiedSourceIndexRuntimeMode.DISABLED)
    inert_state = _state(RuntimeMode.DISABLED)
    inert = module.CooperativeIndexJobPump(None, inert_config, inert_state)
    object.__setattr__(inert_config, "mode", UnifiedSourceIndexRuntimeMode.ACTIVE)
    object.__setattr__(inert_config, "runtime_enabled", True)
    object.__setattr__(inert_config, "selected_generation", GENERATION)
    object.__setattr__(inert_state, "mode", RuntimeMode.ACTIVE)
    object.__setattr__(inert_state, "worker_policy", WorkerPolicy.RUNNING)
    with pytest.raises(module.CooperativeIndexJobPumpError) as still_inert:
        _wakeup(inert)
    assert still_inert.value.args == ("inert",)
    assert inert._runtime is None

    calls = []
    runtime = _runtime(acquire=lambda *a, **k: calls.append("original") or object())
    config = _config(tmp_path / "eligible", UnifiedSourceIndexRuntimeMode.SHADOW)
    state = _state(RuntimeMode.SHADOW)
    eligible = module.CooperativeIndexJobPump(runtime, config, state)
    object.__setattr__(config, "mode", UnifiedSourceIndexRuntimeMode.DISABLED)
    object.__setattr__(config, "runtime_enabled", False)
    object.__setattr__(state, "worker_policy", WorkerPolicy.STOPPED)
    redirected = _runtime(acquire=lambda *a, **k: calls.append("redirected"))
    runtime = redirected
    _wakeup(eligible)
    assert calls == ["original"]


def test_uir04a_jobstore_lease_cursor_retry_and_restart_remain_sole_durable_authority(tmp_path):
    module = _runner_module()
    durable = {"outcome": "foreign_lease", "acquires": 0, "steps": 0}

    class DurableOutcome(BaseException):
        pass

    def acquire(*args, **kwargs):
        durable["acquires"] += 1
        if durable["outcome"] != "ready":
            raise DurableOutcome()
        return ("lease", durable["acquires"])

    def run_next(lease, **kwargs):
        durable["steps"] += 1
        assert lease == ("lease", durable["acquires"])
        return _result()

    runtime = _runtime(acquire=acquire, run_next=run_next)
    first = _pump(tmp_path / "first", runtime=runtime)
    with pytest.raises(module.CooperativeIndexJobPumpError) as blocked:
        _wakeup(first)
    assert blocked.value.args == ("step_failed",)
    assert durable == {"outcome": "foreign_lease", "acquires": 1, "steps": 0}

    durable["outcome"] = "ready"
    restarted = _pump(tmp_path / "restart", runtime=runtime)
    assert _wakeup(restarted) is not None
    assert durable == {"outcome": "ready", "acquires": 2, "steps": 1}
    for pump in (first, restarted):
        assert not any(
            name in vars(pump)
            for name in ("job_id", "owner_scope", "worker_id", "lease", "cursor", "attempt", "retry")
        )


def test_uir04a_import_and_construction_spawn_nothing_and_perform_no_io(tmp_path, monkeypatch):
    source = Path("src/unified_source_index_job_runner.py")
    parsed = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    forbidden_names = {"Thread", "Process", "Popen", "create_task", "run_to_completion", "cancel", "register"}
    assert not {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(parsed)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }.intersection(forbidden_names)
    code = compile(parsed, str(source), "exec")

    def bomb(*args, **kwargs):
        raise AssertionError("forbidden construction effect")

    monkeypatch.setattr(builtins, "open", bomb)
    monkeypatch.setattr(sqlite3, "connect", bomb)
    monkeypatch.setattr(socket, "socket", bomb)
    monkeypatch.setattr(subprocess, "Popen", bomb)
    monkeypatch.setattr(subprocess, "run", bomb)
    monkeypatch.setattr(threading, "Thread", bomb)
    monkeypatch.setattr(multiprocessing, "Process", bomb)
    monkeypatch.setattr(asyncio, "create_task", bomb)
    namespace = {"__name__": "isolated_uir04a_job_runner", "__package__": "src"}
    exec(code, namespace)
    pump = namespace["CooperativeIndexJobPump"](
        _runtime(),
        _config(tmp_path, UnifiedSourceIndexRuntimeMode.SHADOW),
        _state(RuntimeMode.SHADOW),
    )
    assert pump.snapshot()["state"] == "idle"


def _capture_inert_builder_state(module, config, monkeypatch):
    captured = {}
    sentinel = object()

    def capture(runtime, received_config, state):
        captured.update(runtime=runtime, config=received_config, state=state)
        return sentinel

    monkeypatch.setattr(module, "CooperativeIndexJobPump", capture)
    assert module.build_inert_cooperative_index_job_pump(config) is sentinel
    assert captured["runtime"] is None
    assert captured["config"] is config
    assert type(captured["state"]) is RuntimeStateRecord
    return captured["state"]


def test_uir04b_public_api_adds_only_exact_inert_builder_and_preserves_schema_errors(tmp_path):
    module = _runner_module()
    assert module.__all__ == (
        "COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA",
        "CooperativeIndexJobPump",
        "CooperativeIndexJobPumpError",
        "build_inert_cooperative_index_job_pump",
    )
    assert module.COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA == (
        "odysseus.unified_source_index.cooperative_job_pump.v1"
    )
    constructor = inspect.signature(module.CooperativeIndexJobPump)
    assert tuple(constructor.parameters) == ("runtime", "config", "state")
    assert constructor.parameters["runtime"].annotation == "UnifiedSourceIndexJobRuntime | None"
    builder = inspect.signature(module.build_inert_cooperative_index_job_pump)
    assert tuple(builder.parameters) == ("config",)
    assert builder.parameters["config"].kind is inspect.Parameter.POSITIONAL_ONLY
    assert builder.parameters["config"].annotation == "UnifiedSourceIndexRuntimeConfig"
    assert builder.return_annotation == "CooperativeIndexJobPump"
    for code in ("busy", "closed", "inert", "invalid_gate", "invalid_runtime", "step_failed"):
        assert module.CooperativeIndexJobPumpError(code).args == (code,)


def test_uir04b_constructor_runtime_admission_is_exact_for_executable_and_inert_gates(tmp_path):
    module = _runner_module()
    executable_config = _config(tmp_path / "executable", UnifiedSourceIndexRuntimeMode.SHADOW)
    executable_state = _state(RuntimeMode.SHADOW)
    runtime = _runtime()
    assert module.CooperativeIndexJobPump(runtime, executable_config, executable_state).snapshot()["enabled"]
    with pytest.raises(module.CooperativeIndexJobPumpError) as missing_runtime:
        module.CooperativeIndexJobPump(None, executable_config, executable_state)
    assert missing_runtime.value.args == ("invalid_runtime",)

    inert_config = _config(tmp_path / "inert", UnifiedSourceIndexRuntimeMode.DISABLED)
    inert_state = _state(RuntimeMode.DISABLED)
    assert module.CooperativeIndexJobPump(None, inert_config, inert_state).snapshot()["enabled"] is False
    with pytest.raises(module.CooperativeIndexJobPumpError) as supplied_runtime:
        module.CooperativeIndexJobPump(runtime, inert_config, inert_state)
    assert supplied_runtime.value.args == ("invalid_runtime",)
    with pytest.raises(module.CooperativeIndexJobPumpError) as malformed_precedence:
        module.CooperativeIndexJobPump(object(), object(), object())
    assert malformed_precedence.value.args == ("invalid_gate",)


def test_uir04b_inert_constructor_variants_require_none_and_execute_zero_dependencies(tmp_path):
    module = _runner_module()
    cases = (
        (RuntimeMode.DISABLED, WorkerPolicy.STOPPED),
        (RuntimeMode.READ_ONLY, WorkerPolicy.READ_ONLY),
        (RuntimeMode.DEGRADED, WorkerPolicy.STOPPED),
        (RuntimeMode.ROLLBACK, WorkerPolicy.STOPPED),
        (RuntimeMode.SHADOW, WorkerPolicy.STOPPED),
        (RuntimeMode.CANARY, WorkerPolicy.READ_ONLY),
        (RuntimeMode.ACTIVE, WorkerPolicy.STOPPED),
    )
    for mode, policy in cases:
        config = _config(tmp_path / mode.value / policy.value, UnifiedSourceIndexRuntimeMode(mode.value))
        state = _state(mode, worker_policy=policy)
        pump = module.CooperativeIndexJobPump(None, config, state)
        assert pump.snapshot()["enabled"] is False
        with pytest.raises(module.CooperativeIndexJobPumpError) as inert:
            _wakeup(pump, job_id="private-job", adapter=object(), projection=object())
        assert inert.value.args == ("inert",)
        with pytest.raises(module.CooperativeIndexJobPumpError) as runtime_rejected:
            module.CooperativeIndexJobPump(_runtime(), config, state)
        assert runtime_rejected.value.args == ("invalid_runtime",)


def test_uir04b_builder_creates_exact_disabled_stopped_nonauthorizing_state(tmp_path, monkeypatch):
    module = _runner_module()
    config = _config(tmp_path, UnifiedSourceIndexRuntimeMode.DISABLED)
    state = _capture_inert_builder_state(module, config, monkeypatch)
    assert state == RuntimeStateRecord(
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
    assert state.selected_scopes == ()
    assert state.health.providers == ()
    assert state.live_activation_authorized is False


def test_uir04b_builder_creates_exact_read_only_stopped_nonauthorizing_state(tmp_path, monkeypatch):
    module = _runner_module()
    config = _config(tmp_path, UnifiedSourceIndexRuntimeMode.READ_ONLY)
    state = _capture_inert_builder_state(module, config, monkeypatch)
    assert state == RuntimeStateRecord(
        RuntimeMode.READ_ONLY,
        RuntimeGeneration(GENERATION),
        (),
        RuntimeHealth(RuntimeHealthState.READY, ()),
        WorkerPolicy.STOPPED,
        True,
        False,
        True,
        False,
    )
    assert state.generation.generation_ref == GENERATION
    assert state.selected_scopes == ()
    assert state.health.providers == ()
    assert state.live_activation_authorized is False


def test_uir04b_builder_rejects_shadow_canary_active_degraded_and_rollback(tmp_path):
    module = _runner_module()
    for mode in (
        UnifiedSourceIndexRuntimeMode.SHADOW,
        UnifiedSourceIndexRuntimeMode.CANARY,
        UnifiedSourceIndexRuntimeMode.ACTIVE,
        UnifiedSourceIndexRuntimeMode.DEGRADED,
        UnifiedSourceIndexRuntimeMode.ROLLBACK,
    ):
        with pytest.raises(module.CooperativeIndexJobPumpError) as rejected:
            module.build_inert_cooperative_index_job_pump(_config(tmp_path / mode.value, mode))
        assert rejected.value.args == ("invalid_gate",)
        assert rejected.value.__cause__ is None

    class ConfigSubclass(UnifiedSourceIndexRuntimeConfig):
        pass

    for invalid in (object(), object.__new__(ConfigSubclass)):
        with pytest.raises(module.CooperativeIndexJobPumpError) as rejected:
            module.build_inert_cooperative_index_job_pump(invalid)
        assert rejected.value.args == ("invalid_gate",)


def test_uir04b_inert_wakeup_rejects_before_runtime_lookup_and_preserves_close_precedence(tmp_path):
    module = _runner_module()
    pump = module.build_inert_cooperative_index_job_pump(
        _config(tmp_path, UnifiedSourceIndexRuntimeMode.DISABLED)
    )

    class RuntimeBomb:
        def __getattribute__(self, name):
            raise AssertionError("inert wakeup read runtime")

    pump._runtime = RuntimeBomb()
    with pytest.raises(module.CooperativeIndexJobPumpError) as inert:
        _wakeup(pump, job_id=object(), owner_scope=object(), worker_id=object(), adapter=object(), now=object())
    assert inert.value.args == ("inert",)
    pump._step_active = True
    pump._state = "stepping"
    with pytest.raises(module.CooperativeIndexJobPumpError) as busy:
        _wakeup(pump)
    assert busy.value.args == ("busy",)
    pump.close()
    with pytest.raises(module.CooperativeIndexJobPumpError) as closed:
        _wakeup(pump)
    assert closed.value.args == ("closed",)


def test_uir04b_mutation_and_baseexception_boundaries_are_fixed_fresh_and_fail_closed(tmp_path, monkeypatch):
    module = _runner_module()
    config = _config(tmp_path / "disabled", UnifiedSourceIndexRuntimeMode.DISABLED)
    pump = module.build_inert_cooperative_index_job_pump(config)
    object.__setattr__(config, "mode", UnifiedSourceIndexRuntimeMode.ACTIVE)
    object.__setattr__(config, "runtime_enabled", True)
    object.__setattr__(config, "selected_generation", GENERATION)
    with pytest.raises(module.CooperativeIndexJobPumpError) as inert:
        _wakeup(pump)
    assert inert.value.args == ("inert",)

    malformed = object.__new__(UnifiedSourceIndexRuntimeConfig)
    errors = []
    for _ in range(2):
        with pytest.raises(module.CooperativeIndexJobPumpError) as invalid:
            module.build_inert_cooperative_index_job_pump(malformed)
        errors.append(invalid.value)
        assert invalid.value.args == ("invalid_gate",)
        assert invalid.value.__cause__ is None
    assert errors[0] is not errors[1]

    class Marker(BaseException):
        def __str__(self):
            raise AssertionError("marker string observed")

        def __repr__(self):
            raise AssertionError("marker repr observed")

    def state_bomb(*args, **kwargs):
        raise Marker()

    read_only = _config(tmp_path / "read-only", UnifiedSourceIndexRuntimeMode.READ_ONLY)
    monkeypatch.setattr(module, "RuntimeStateRecord", state_bomb)
    with pytest.raises(module.CooperativeIndexJobPumpError) as fixed:
        module.build_inert_cooperative_index_job_pump(read_only)
    assert fixed.value.args == ("invalid_gate",)
    assert fixed.value.__cause__ is None


def test_uir04b_import_builder_constructor_and_inert_wakeup_have_zero_effects(tmp_path, monkeypatch):
    source = Path("src/unified_source_index_job_runner.py")
    parsed = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    code = compile(parsed, str(source), "exec")
    config = _config(tmp_path, UnifiedSourceIndexRuntimeMode.DISABLED)
    calls = []

    def bomb(*args, **kwargs):
        calls.append("effect")
        raise AssertionError("forbidden inert composition effect")

    monkeypatch.setattr(builtins, "open", bomb)
    monkeypatch.setattr(Path, "open", bomb)
    monkeypatch.setattr(Path, "mkdir", bomb)
    monkeypatch.setattr(sqlite3, "connect", bomb)
    monkeypatch.setattr(socket, "socket", bomb)
    monkeypatch.setattr(subprocess, "Popen", bomb)
    monkeypatch.setattr(subprocess, "run", bomb)
    monkeypatch.setattr(threading, "Thread", bomb)
    monkeypatch.setattr(multiprocessing, "Process", bomb)
    monkeypatch.setattr(asyncio, "create_task", bomb)
    namespace = {"__name__": "isolated_uir04b_job_runner", "__package__": "src"}
    exec(code, namespace)
    pump = namespace["build_inert_cooperative_index_job_pump"](config)
    with pytest.raises(namespace["CooperativeIndexJobPumpError"]) as inert:
        pump.wakeup(
            object(),
            owner_scope=object(),
            worker_id=object(),
            adapter=object(),
            now=object(),
            projection=object(),
            lease_seconds=object(),
        )
    assert inert.value.args == ("inert",)
    assert calls == []

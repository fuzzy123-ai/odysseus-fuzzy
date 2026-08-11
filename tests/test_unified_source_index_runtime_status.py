import ast
import importlib
import inspect
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_SCHEMA = "odysseus.knowledge_runtime.composition_snapshot.v1"
STATUS_SCHEMA = "odysseus.unified_source_index.runtime_status.v1"


def _module():
    return importlib.import_module("src.unified_source_index_runtime_status")


def _forged_components():
    from src.unified_source_index_job_runner import CooperativeIndexJobPump
    from src.unified_source_index_runtime import KnowledgeRuntime

    return object.__new__(KnowledgeRuntime), object.__new__(CooperativeIndexJobPump)


def _knowledge_snapshot(
    *, state="disabled", mode="disabled", planner_bound=False, error_code=None
):
    return {
        "schema": KNOWLEDGE_SCHEMA,
        "state": state,
        "mode": mode,
        "planner_bound": planner_bound,
        "error_code": error_code,
        "live_activation_authorized": False,
    }


def _pump_snapshot(
    module, *, state="idle", enabled=False, step_active=False, close_requested=False
):
    return {
        "schema": module.COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA,
        "state": state,
        "enabled": enabled,
        "step_active": step_active,
        "close_requested": close_requested,
    }


def _patch_snapshots(monkeypatch, knowledge_value, pump_value):
    from src.unified_source_index_job_runner import CooperativeIndexJobPump
    from src.unified_source_index_runtime import KnowledgeRuntime

    monkeypatch.setattr(KnowledgeRuntime, "snapshot", lambda self: knowledge_value)
    monkeypatch.setattr(CooperativeIndexJobPump, "snapshot", lambda self: pump_value)


def _config(tmp_path, mode):
    from src.unified_source_index_runtime_config import UnifiedSourceIndexRuntimeConfig

    values = {}
    if mode != "disabled":
        values = {
            "ODYSSEUS_USI_RUNTIME_MODE": mode,
            "ODYSSEUS_USI_RUNTIME_ENABLED": "true",
            "ODYSSEUS_USI_SELECTED_GENERATION": "usi_generation_" + "a" * 64,
            "ODYSSEUS_USI_ALLOWED_OWNERS": "user:alice",
            "ODYSSEUS_USI_ALLOWED_SOURCES": "source:one",
            "ODYSSEUS_USI_ALLOWED_DOMAINS": "code",
        }
    return UnifiedSourceIndexRuntimeConfig.from_environment(values, data_root=tmp_path)


def _assert_error(module, code, action):
    with pytest.raises(module.UnifiedSourceIndexRuntimeStatusError) as captured:
        action()
    error = captured.value
    assert type(error) is module.UnifiedSourceIndexRuntimeStatusError
    assert error.args == (code,)
    assert error.__cause__ is None
    return error


def test_uir05a_public_api_schema_exports_signature_and_exact_field_sets(monkeypatch):
    module = _module()
    assert module.__all__ == (
        "RUNTIME_STATUS_SCHEMA",
        "UnifiedSourceIndexRuntimeStatusError",
        "project_unified_source_index_runtime_status",
    )
    assert module.RUNTIME_STATUS_SCHEMA == STATUS_SCHEMA
    signature = inspect.signature(module.project_unified_source_index_runtime_status)
    assert tuple(signature.parameters) == ("knowledge_runtime", "index_job_pump")
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_ONLY
        for parameter in signature.parameters.values()
    )
    assert signature.return_annotation == "dict[str, object]"
    runtime, pump = _forged_components()
    _patch_snapshots(
        monkeypatch,
        _knowledge_snapshot(),
        _pump_snapshot(module),
    )
    result = module.project_unified_source_index_runtime_status(runtime, pump)
    assert list(result) == [
        "schema", "knowledge", "index_job_pump", "snapshot_atomic",
        "live_activation_authorized",
    ]
    assert list(result["knowledge"]) == ["state", "mode", "planner_bound", "error_code"]
    assert list(result["index_job_pump"]) == [
        "state", "enabled", "step_active", "close_requested",
    ]
    for code in ("invalid_component", "invalid_snapshot", "snapshot_failed"):
        assert module.UnifiedSourceIndexRuntimeStatusError(code).args == (code,)


def test_uir05a_exact_types_reject_subclasses_before_callbacks_and_forgeries_fail_on_snapshot(monkeypatch):
    module = _module()
    from src.unified_source_index_job_runner import CooperativeIndexJobPump
    from src.unified_source_index_runtime import KnowledgeRuntime

    calls = []
    monkeypatch.setattr(KnowledgeRuntime, "snapshot", lambda self: calls.append("knowledge"))
    monkeypatch.setattr(CooperativeIndexJobPump, "snapshot", lambda self: calls.append("pump"))

    class RuntimeSubclass(KnowledgeRuntime):
        pass

    class PumpSubclass(CooperativeIndexJobPump):
        pass

    runtime_subclass = object.__new__(RuntimeSubclass)
    pump_subclass = object.__new__(PumpSubclass)
    exact_runtime, exact_pump = _forged_components()
    for runtime, pump in (
        (object(), exact_pump),
        (exact_runtime, object()),
        (runtime_subclass, exact_pump),
        (exact_runtime, pump_subclass),
    ):
        _assert_error(
            module,
            "invalid_component",
            lambda runtime=runtime, pump=pump: module.project_unified_source_index_runtime_status(
                runtime, pump
            ),
        )
    assert calls == []

    monkeypatch.setattr(
        KnowledgeRuntime,
        "snapshot",
        lambda self: (_ for _ in ()).throw(BaseException("private-forgery")),
    )
    _assert_error(
        module,
        "snapshot_failed",
        lambda: module.project_unified_source_index_runtime_status(exact_runtime, exact_pump),
    )
    assert calls == []


def test_uir05a_calls_each_public_snapshot_once_in_order_and_retains_no_input(monkeypatch):
    module = _module()
    from src.unified_source_index_job_runner import CooperativeIndexJobPump
    from src.unified_source_index_runtime import KnowledgeRuntime

    calls = []
    knowledge = _knowledge_snapshot()
    pump_snapshot = _pump_snapshot(module)

    def knowledge_snapshot(self):
        calls.append("knowledge")
        return knowledge

    def index_snapshot(self):
        calls.append("pump")
        return pump_snapshot

    monkeypatch.setattr(KnowledgeRuntime, "snapshot", knowledge_snapshot)
    monkeypatch.setattr(CooperativeIndexJobPump, "snapshot", index_snapshot)
    runtime, pump = _forged_components()
    result = module.project_unified_source_index_runtime_status(runtime, pump)
    assert calls == ["knowledge", "pump"]
    assert not any(value is runtime or value is pump for value in result.values())
    assert not hasattr(result, "runtime") and not hasattr(result, "pump")


def test_uir05a_disabled_and_read_only_app_components_project_content_free_status(tmp_path):
    module = _module()
    from src.unified_source_index_job_runner import build_inert_cooperative_index_job_pump
    from src.unified_source_index_runtime import build_knowledge_runtime

    for mode in ("disabled", "read_only"):
        runtime = build_knowledge_runtime((mode, mode != "disabled"), planner=None)
        pump = build_inert_cooperative_index_job_pump(_config(tmp_path / mode, mode))
        result = module.project_unified_source_index_runtime_status(runtime, pump)
        assert result["knowledge"]["mode"] == mode
        assert result["knowledge"]["state"] in {"disabled", "degraded"}
        assert result["index_job_pump"] == {
            "state": "idle",
            "enabled": False,
            "step_active": False,
            "close_requested": False,
        }
        assert result["snapshot_atomic"] is False
        assert result["live_activation_authorized"] is False
        serialized = repr(result)
        assert all(
            token not in serialized
            for token in ("user:alice", "source:one", "usi_generation_", str(tmp_path))
        )


def test_uir05a_component_state_matrix_is_preserved_without_aggregate_health_claim(monkeypatch):
    module = _module()
    runtime, pump = _forged_components()
    matrix = (
        ("disabled", "disabled", None, "idle", False, False, False),
        ("ready", "read_only", None, "stepping", True, True, False),
        ("degraded", "shadow", "mode_not_composed", "draining", True, True, True),
        ("closed", "active", "close_failed", "closed", False, False, True),
    )
    for knowledge_state, mode, code, pump_state, enabled, active, closing in matrix:
        _patch_snapshots(
            monkeypatch,
            _knowledge_snapshot(state=knowledge_state, mode=mode, error_code=code),
            _pump_snapshot(
                module,
                state=pump_state,
                enabled=enabled,
                step_active=active,
                close_requested=closing,
            ),
        )
        result = module.project_unified_source_index_runtime_status(runtime, pump)
        assert result["knowledge"]["state"] == knowledge_state
        assert result["index_job_pump"]["state"] == pump_state
        assert not set(result) & {"healthy", "ready", "degraded", "overall_state"}
        assert result["snapshot_atomic"] is False


def test_uir05a_nonatomic_shutdown_transition_is_labeled_and_never_retried_or_rejected(monkeypatch):
    module = _module()
    from src.unified_source_index_job_runner import CooperativeIndexJobPump
    from src.unified_source_index_runtime import KnowledgeRuntime

    runtime, pump = _forged_components()
    calls = []

    def knowledge_snapshot(self):
        calls.append("knowledge")
        return _knowledge_snapshot(state="ready", mode="read_only")

    def pump_snapshot(self):
        calls.append("pump")
        return _pump_snapshot(module, state="closed", close_requested=True)

    monkeypatch.setattr(KnowledgeRuntime, "snapshot", knowledge_snapshot)
    monkeypatch.setattr(CooperativeIndexJobPump, "snapshot", pump_snapshot)
    result = module.project_unified_source_index_runtime_status(runtime, pump)
    assert calls == ["knowledge", "pump"]
    assert result["knowledge"]["state"] == "ready"
    assert result["index_job_pump"]["state"] == "closed"
    assert result["snapshot_atomic"] is False
    assert result["live_activation_authorized"] is False


def test_uir05a_malformed_and_raising_snapshots_fail_fresh_fixed_content_free_from_none(monkeypatch):
    module = _module()
    runtime, pump = _forged_components()
    good_knowledge = _knowledge_snapshot()
    good_pump = _pump_snapshot(module)
    malformed = (
        ({}, good_pump),
        ({**good_knowledge, "unknown": False}, good_pump),
        ({**good_knowledge, "schema": "wrong"}, good_pump),
        ({**good_knowledge, "planner_bound": 0}, good_pump),
        ({**good_knowledge, "live_activation_authorized": True}, good_pump),
        (good_knowledge, {**good_pump, "state": "queued"}),
        (good_knowledge, {**good_pump, "enabled": 1}),
        (good_knowledge, {**good_pump, "unknown": False}),
    )
    for knowledge, pump_value in malformed:
        _patch_snapshots(monkeypatch, knowledge, pump_value)
        first = _assert_error(
            module,
            "invalid_snapshot",
            lambda: module.project_unified_source_index_runtime_status(runtime, pump),
        )
        second = _assert_error(
            module,
            "invalid_snapshot",
            lambda: module.project_unified_source_index_runtime_status(runtime, pump),
        )
        assert first is not second

    marker = "private-snapshot-marker"
    from src.unified_source_index_runtime import KnowledgeRuntime

    monkeypatch.setattr(
        KnowledgeRuntime,
        "snapshot",
        lambda self: (_ for _ in ()).throw(BaseException(marker)),
    )
    first = _assert_error(
        module,
        "snapshot_failed",
        lambda: module.project_unified_source_index_runtime_status(runtime, pump),
    )
    second = _assert_error(
        module,
        "snapshot_failed",
        lambda: module.project_unified_source_index_runtime_status(runtime, pump),
    )
    assert first is not second
    assert marker not in str(first) and marker not in repr(first)


def test_uir05a_projections_are_fresh_detached_exact_builtins_and_never_authorizing(monkeypatch):
    module = _module()
    runtime, pump = _forged_components()
    knowledge = _knowledge_snapshot(state="ready", mode="read_only", planner_bound=True)
    pump_value = _pump_snapshot(module, state="stepping", enabled=True, step_active=True)
    _patch_snapshots(monkeypatch, knowledge, pump_value)
    first = module.project_unified_source_index_runtime_status(runtime, pump)
    second = module.project_unified_source_index_runtime_status(runtime, pump)
    assert first == second and first is not second
    assert type(first) is dict
    assert type(first["knowledge"]) is dict and first["knowledge"] is not second["knowledge"]
    assert type(first["index_job_pump"]) is dict
    assert all(type(key) is str for key in first)
    assert all(
        type(value) in {str, bool, type(None)}
        for nested in (first["knowledge"], first["index_job_pump"])
        for value in nested.values()
    )
    knowledge["state"] = "closed"
    pump_value["state"] = "closed"
    first["knowledge"]["state"] = "forged"
    assert second["knowledge"]["state"] == "ready"
    assert second["index_job_pump"]["state"] == "stepping"
    assert first["snapshot_atomic"] is False
    assert first["live_activation_authorized"] is False


def test_uir05a_import_construction_and_projection_have_zero_metrics_app_io_provider_network_or_spawn_effects():
    import builtins
    import types

    source = (ROOT / "src/unified_source_index_runtime_status.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module for node in tree.body if isinstance(node, ast.ImportFrom)
        and node.module != "__future__"
    }
    assert imports == {
        "src.unified_source_index_runtime",
        "src.unified_source_index_job_runner",
    }
    forbidden = {
        "open", "Path", "connect", "getenv", "record_operation", "set_queue_depth",
        "Thread", "Process", "create_task", "Popen", "run", "wakeup", "close",
    }
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert not calls & forbidden

    effects = []

    class Runtime:
        def snapshot(self):
            return _knowledge_snapshot()

    class Pump:
        def snapshot(self):
            return {
                "schema": "pump-schema",
                "state": "idle",
                "enabled": False,
                "step_active": False,
                "close_requested": False,
            }

    runtime_module = types.SimpleNamespace(KnowledgeRuntime=Runtime)
    runner_module = types.SimpleNamespace(
        CooperativeIndexJobPump=Pump,
        COOPERATIVE_JOB_PUMP_SNAPSHOT_SCHEMA="pump-schema",
    )
    real_import = builtins.__import__

    def importing(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "__future__":
            return real_import(name, globals, locals, fromlist, level)
        modules = {
            "src.unified_source_index_runtime": runtime_module,
            "src.unified_source_index_job_runner": runner_module,
        }
        if name not in modules:
            effects.append(name)
            raise AssertionError("unexpected import")
        return modules[name]

    namespace = {
        "__name__": "isolated_status",
        "__builtins__": {
            **vars(builtins),
            "__import__": importing,
            "open": lambda *args, **kwargs: effects.append("open"),
        },
    }
    exec(compile(tree, "<uir05a-zero-effects>", "exec"), namespace)
    result = namespace["project_unified_source_index_runtime_status"](Runtime(), Pump())
    assert result["snapshot_atomic"] is False
    assert result["live_activation_authorized"] is False
    assert effects == []

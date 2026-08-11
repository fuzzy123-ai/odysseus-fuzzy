import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_app_owns_exact_runtime_lifecycle_without_background_task():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(transcription_runtime.start)" in source
    assert "await asyncio.to_thread(transcription_runtime.stop)" in source
    assert "asyncio.create_task(transcription_runtime" not in source
    assert "app.state.transcription_runtime = transcription_runtime" in source


def test_privacy_pipeline_is_default_off_local_only_and_excludes_legacy_stt():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "ODYSSEUS_TRANSCRIPTION_ENABLED:-false" in compose
    assert "ODYSSEUS_TRANSCRIPTION_LOCAL_ONLY:-true" in compose
    assert "ODYSSEUS_TRANSCRIPTION_RECORDING_AUTHORIZED:-false" in compose
    assert "ODYSSEUS_LEGACY_STT_ENABLED:-true" in compose
    assert "privacy transcription requires legacy STT to be disabled" in app
    assert "for key in (\"TELEGRAM_STT_ENABLED\", \"TELEGRAM_VOICE_STT_ENABLED\")" in app
    assert "if legacy_stt_enabled:" in app


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _function(tree: ast.AST, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    return next(
        node for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    )


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        candidate for candidate in ast.walk(node)
        if isinstance(candidate, ast.Call)
        and isinstance(candidate.func, ast.Attribute)
        and candidate.func.attr == name
        or isinstance(candidate, ast.Call)
        and isinstance(candidate.func, ast.Name)
        and candidate.func.id == name
    ]


def _string_calls(node: ast.AST, name: str) -> list[str]:
    values = []
    for call in _calls(node, name):
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
            values.append(call.args[0].value)
    return values


def test_uir03b_initializer_builds_exactly_one_runtime_from_one_config_near_return():
    tree = ast.parse(_source("src/app_initializer.py"))
    initializer = _function(tree, "initialize_managers")
    assert len(_calls(initializer, "from_environment")) == 1
    builds = _calls(initializer, "build_knowledge_runtime")
    assert len(builds) == 1
    build = builds[0]
    assert isinstance(build.args[0], ast.Tuple)
    assert [ast.unparse(item) for item in build.args[0].elts] == [
        "knowledge_runtime_config.mode.value", "knowledge_runtime_config.runtime_enabled"
    ]
    assert isinstance(build.keywords[0].value, ast.Constant)
    assert build.keywords[0].arg == "planner" and build.keywords[0].value.value is None
    returned = next(node for node in initializer.body if isinstance(node, ast.Return))
    assert isinstance(returned.value, ast.Dict)
    assert any(
        isinstance(key, ast.Constant) and key.value == "knowledge_runtime"
        and isinstance(value, ast.Name) and value.id == "knowledge_runtime"
        for key, value in zip(returned.value.keys, returned.value.values)
    )
    assert build.lineno < returned.lineno


def test_uir03b_initializer_failure_is_fixed_content_free_and_builds_no_fallback():
    source = _source("src/app_initializer.py")
    tree = ast.parse(source)
    initializer = _function(tree, "initialize_managers")
    assert "Knowledge runtime initialization failed" in _string_calls(initializer, "warning")
    raises = [node for node in ast.walk(initializer) if isinstance(node, ast.Raise)]
    assert any(
        isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "RuntimeError"
        and isinstance(node.exc.args[0], ast.Constant)
        and node.exc.args[0].value == "knowledge_runtime_initialization_failed"
        and isinstance(node.cause, ast.Constant) and node.cause.value is None
        for node in raises
    )
    assert len(_calls(initializer, "build_knowledge_runtime")) == 1
    assert "exc_info" not in source


def test_uir03b_app_stores_the_initializer_runtime_by_identity_and_rejects_missing_component():
    source = _source("app.py")
    tree = ast.parse(source)
    assert source.count('components["knowledge_runtime"]') == 1
    assert "type(knowledge_runtime) is not KnowledgeRuntime" in source
    assert "app.state.knowledge_runtime = knowledge_runtime" in source
    assert source.index('components = initialize_managers(BASE_DIR, rag_manager)') < source.index('components["knowledge_runtime"]')
    assert "Knowledge runtime component invalid" in source
    assert "knowledge_runtime_component_invalid" in source
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "build_knowledge_runtime"
        for node in ast.walk(tree)
    )


def test_uir03b_lifecycle_import_boundary_is_one_guarded_explicit_sequence():
    tree = ast.parse(_source("app.py"))
    component_index = next(
        index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "components" for target in node.targets)
    )
    guarded = tree.body[component_index - 1]
    assert isinstance(guarded, ast.Try)
    assert len(guarded.body) == 5
    imports = [node for node in guarded.body if isinstance(node, ast.ImportFrom)]
    assert [(node.module, [alias.name for alias in node.names]) for node in imports] == [
        ("src.agent_tools", ["set_mcp_manager"]),
        ("src.agent_tools.knowledge_tools", ["set_query_knowledge_planner"]),
        ("src.unified_source_index_runtime", [
            "KnowledgeRuntime", "bind_knowledge_runtime", "close_knowledge_runtime",
        ]),
        ("src.unified_source_index_job_runner", ["CooperativeIndexJobPump"]),
        ("src.unified_source_index_runtime_status", [
            "UnifiedSourceIndexRuntimeStatusError",
            "project_unified_source_index_runtime_status",
        ]),
    ]
    assert all(
        not (
            isinstance(node, ast.ImportFrom)
            and node.module in {
                "src.agent_tools",
                "src.agent_tools.knowledge_tools",
                "src.unified_source_index_runtime",
                "src.unified_source_index_job_runner",
                "src.unified_source_index_runtime_status",
            }
        )
        for node in tree.body[:component_index - 1]
    )


def test_uir03b_lifecycle_import_package_module_and_symbol_bombs_are_content_free():
    import builtins
    import types

    tree = ast.parse(_source("app.py"))
    component_index = next(
        index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "components" for target in node.targets)
    )
    guarded = tree.body[component_index - 1]
    assert isinstance(guarded, ast.Try)
    assert len(guarded.handlers) == 1
    handler = guarded.handlers[0]
    assert isinstance(handler.type, ast.Name) and handler.type.id == "Exception"
    assert len(handler.body) == 2
    warning, raise_node = handler.body
    assert isinstance(warning, ast.Expr)
    assert ast.unparse(warning) == "logger.warning('Knowledge runtime lifecycle import failed')"
    assert isinstance(raise_node, ast.Raise)
    assert ast.unparse(raise_node.exc) == "RuntimeError('knowledge_runtime_lifecycle_import_failed')"
    assert isinstance(raise_node.cause, ast.Constant) and raise_node.cause.value is None

    class Logger:
        def __init__(self):
            self.warnings = []

        def warning(self, value):
            self.warnings.append(value)

    def probe(case):
        marker = f"private-{case}-marker"
        package = types.SimpleNamespace(set_mcp_manager=object())
        knowledge_tools = types.SimpleNamespace(set_query_knowledge_planner=object())
        runtime = types.SimpleNamespace(
            KnowledgeRuntime=object(),
            bind_knowledge_runtime=object(),
            close_knowledge_runtime=object(),
        )
        runner = types.SimpleNamespace(CooperativeIndexJobPump=object())
        status = types.SimpleNamespace(
            UnifiedSourceIndexRuntimeStatusError=object(),
            project_unified_source_index_runtime_status=object(),
        )
        if case == "missing_set_mcp_manager":
            del package.set_mcp_manager
        elif case == "missing_setter":
            del knowledge_tools.set_query_knowledge_planner
        elif case == "missing_CooperativeIndexJobPump":
            del runner.CooperativeIndexJobPump
        elif case == "missing_UnifiedSourceIndexRuntimeStatusError":
            del status.UnifiedSourceIndexRuntimeStatusError
        elif case == "missing_project_unified_source_index_runtime_status":
            del status.project_unified_source_index_runtime_status
        elif case.startswith("missing_"):
            delattr(runtime, case.removeprefix("missing_"))

        def importing(name, globals=None, locals=None, fromlist=(), level=0):
            bombs = {
                "parent_package_bomb": "src.agent_tools",
                "knowledge_tools_bomb": "src.agent_tools.knowledge_tools",
                "runtime_bomb": "src.unified_source_index_runtime",
                "runner_bomb": "src.unified_source_index_job_runner",
                "status_bomb": "src.unified_source_index_runtime_status",
            }
            if bombs.get(case) == name:
                raise ImportError(marker)
            modules = {
                "src.agent_tools": package,
                "src.agent_tools.knowledge_tools": knowledge_tools,
                "src.unified_source_index_runtime": runtime,
                "src.unified_source_index_job_runner": runner,
                "src.unified_source_index_runtime_status": status,
            }
            return modules[name]

        logger = Logger()
        namespace = {
            "__builtins__": {**vars(builtins), "__import__": importing},
            "logger": logger,
        }
        isolated = ast.fix_missing_locations(ast.Module(body=[guarded], type_ignores=[]))
        try:
            exec(compile(isolated, "<uir03b-import-boundary>", "exec"), namespace)
        except RuntimeError as error:
            assert error.args == ("knowledge_runtime_lifecycle_import_failed",)
            assert error.__cause__ is None
            assert marker not in str(error)
            assert marker not in repr(error)
            assert marker not in error.args
        else:
            raise AssertionError(f"{case} did not fail closed")
        assert logger.warnings == ["Knowledge runtime lifecycle import failed"]

    for case in (
        "parent_package_bomb",
        "knowledge_tools_bomb",
        "runtime_bomb",
        "runner_bomb",
        "status_bomb",
        "missing_set_mcp_manager",
        "missing_setter",
        "missing_KnowledgeRuntime",
        "missing_bind_knowledge_runtime",
        "missing_close_knowledge_runtime",
        "missing_CooperativeIndexJobPump",
        "missing_UnifiedSourceIndexRuntimeStatusError",
        "missing_project_unified_source_index_runtime_status",
    ):
        probe(case)


def test_uir03b_startup_bind_is_final_single_shot_and_repeat_safe():
    source = _source("app.py")
    tree = ast.parse(source)
    startup = _function(tree, "_startup_event")
    bind_calls = _calls(startup, "bind_knowledge_runtime")
    assert len(bind_calls) == 1
    bind = bind_calls[0]
    assert [ast.unparse(argument) for argument in bind.args] == [
        "knowledge_runtime", "set_query_knowledge_planner"
    ]
    final_log = next(
        node for node in startup.body
        if isinstance(node, ast.Expr) and "Application startup complete" in ast.unparse(node)
    )
    assert bind.lineno < final_log.lineno
    assert not any(node.lineno > bind.lineno and node.lineno < final_log.lineno for node in startup.body)
    assert "Knowledge runtime bind failed; query_knowledge remains unavailable" in _string_calls(startup, "warning")


def test_uir03b_shutdown_close_is_first_idempotent_step_and_revokes_before_other_teardown():
    source = _source("app.py")
    tree = ast.parse(source)
    shutdown = _function(tree, "_shutdown_event")
    close_calls = _calls(shutdown, "close_knowledge_runtime")
    assert len(close_calls) == 1
    close = close_calls[0]
    assert [ast.unparse(argument) for argument in close.args] == ["knowledge_runtime"]
    stop = _calls(shutdown, "to_thread")[0]
    assert close.lineno < stop.lineno
    assert "Knowledge runtime close failed; query_knowledge remains unavailable" in _string_calls(shutdown, "warning")
    assert "close_knowledge_runtime" in _source("src/unified_source_index_runtime.py")


def test_uir03b_disabled_and_read_only_without_planner_are_inert_and_unavailable():
    from src.unified_source_index_runtime import build_knowledge_runtime

    disabled = build_knowledge_runtime(("disabled", False), planner=None)
    readonly = build_knowledge_runtime(("read_only", True), planner=None)
    assert disabled.snapshot()["state"] == "disabled"
    assert readonly.snapshot()["state"] == "degraded"
    assert readonly.snapshot()["error_code"] == "planner_unavailable"
    assert not disabled.snapshot()["live_activation_authorized"]
    assert not readonly.snapshot()["live_activation_authorized"]


def test_uir03b_retained_token_is_revoked_and_callbacks_are_never_retried():
    from src.unified_source_index_runtime import (
        bind_knowledge_runtime, build_knowledge_runtime, close_knowledge_runtime,
    )

    calls = []
    def binder(value):
        calls.append(value)

    runtime = build_knowledge_runtime(("disabled", False), planner=None)
    bind_knowledge_runtime(runtime, binder)
    close_knowledge_runtime(runtime)
    close_knowledge_runtime(runtime)
    assert calls == [None, None]
    assert runtime.snapshot()["state"] == "closed"


def test_uir03b_wiring_delta_has_no_eager_obsidian_sqlite_provider_worker_environment_or_network_access():
    initializer = _source("src/app_initializer.py")
    app = _source("app.py")
    initializer_tree = ast.parse(initializer)
    initializer_function = _function(initializer_tree, "initialize_managers")
    runtime_block = next(
        node for node in initializer_function.body
        if isinstance(node, ast.Try) and _calls(node, "from_environment")
    )
    component_block = app[app.index("# ========= COMPONENT INITIALIZATION ========="):app.index("session_manager   =")]
    lifecycle_block = app[app.index("async def _startup_event"):]
    forbidden_calls = {"getenv", "open", "connect", "Popen", "run", "create_task"}
    assert not {call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id
                for call in (node for node in ast.walk(runtime_block) if isinstance(node, ast.Call))
                if isinstance(call.func, (ast.Attribute, ast.Name))} & forbidden_calls
    assert "os." not in component_block and "Path(" not in component_block
    assert "asyncio.create_task(knowledge_runtime" not in lifecycle_block
    assert "build_knowledge_runtime" not in app


def _uir04c_pump_try(tree: ast.AST) -> ast.Try:
    initializer = _function(tree, "initialize_managers")
    return next(
        node for node in initializer.body
        if isinstance(node, ast.Try)
        and _calls(node, "build_inert_cooperative_index_job_pump")
    )


def _uir04c_config(tmp_path: Path, mode: str):
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


def test_uir04c_initializer_builds_one_inert_pump_from_the_same_exact_config():
    tree = ast.parse(_source("src/app_initializer.py"))
    initializer = _function(tree, "initialize_managers")
    config_reads = _calls(initializer, "from_environment")
    knowledge_builds = _calls(initializer, "build_knowledge_runtime")
    pump_builds = _calls(initializer, "build_inert_cooperative_index_job_pump")
    assert len(config_reads) == len(knowledge_builds) == len(pump_builds) == 1
    assert [ast.unparse(argument) for argument in pump_builds[0].args] == [
        "knowledge_runtime_config"
    ]
    assert config_reads[0].lineno < knowledge_builds[0].lineno < pump_builds[0].lineno
    returned = next(node for node in initializer.body if isinstance(node, ast.Return))
    assert any(
        isinstance(key, ast.Constant) and key.value == "index_job_pump"
        and isinstance(value, ast.Name) and value.id == "index_job_pump"
        for key, value in zip(returned.value.keys, returned.value.values)
    )
    assert pump_builds[0].lineno < returned.lineno


def test_uir04c_initializer_pump_failure_is_fixed_content_free_and_returns_no_partial_components():
    import builtins
    import types

    tree = ast.parse(_source("src/app_initializer.py"))
    pump_try = _uir04c_pump_try(tree)
    assert len(pump_try.handlers) == 1
    handler = pump_try.handlers[0]
    assert isinstance(handler.type, ast.Name) and handler.type.id == "Exception"
    assert ast.unparse(handler.body[0]) == "logger.warning('Index job pump initialization failed')"
    assert ast.unparse(handler.body[1].exc) == "RuntimeError('index_job_pump_initialization_failed')"
    assert isinstance(handler.body[1].cause, ast.Constant) and handler.body[1].cause.value is None

    marker = "private-pump-builder-marker"
    runner = types.SimpleNamespace()

    def bomb(config):
        raise ValueError(marker)

    runner.build_inert_cooperative_index_job_pump = bomb

    def importing(name, globals=None, locals=None, fromlist=(), level=0):
        assert name == "src.unified_source_index_job_runner"
        return runner

    class Logger:
        def __init__(self):
            self.warnings = []

        def warning(self, value):
            self.warnings.append(value)

    logger = Logger()
    namespace = {
        "__builtins__": {**vars(builtins), "__import__": importing},
        "knowledge_runtime_config": object(),
        "logger": logger,
    }
    isolated = ast.fix_missing_locations(ast.Module(body=[pump_try], type_ignores=[]))
    try:
        exec(compile(isolated, "<uir04c-pump-builder>", "exec"), namespace)
    except RuntimeError as error:
        assert error.args == ("index_job_pump_initialization_failed",)
        assert error.__cause__ is None
        assert marker not in str(error) and marker not in repr(error)
    else:
        raise AssertionError("pump builder bomb did not fail closed")
    assert "index_job_pump" not in namespace
    assert logger.warnings == ["Index job pump initialization failed"]


def test_uir04c_guarded_lifecycle_import_includes_exact_pump_type_and_content_free_bombs():
    tree = ast.parse(_source("app.py"))
    component_index = next(
        index for index, node in enumerate(tree.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "components" for target in node.targets)
    )
    guarded = tree.body[component_index - 1]
    imports = [node for node in guarded.body if isinstance(node, ast.ImportFrom)]
    assert [(node.module, [alias.name for alias in node.names]) for node in imports][-2] == (
        "src.unified_source_index_job_runner", ["CooperativeIndexJobPump"]
    )
    assert len(imports) == 5
    assert len(guarded.handlers) == 1
    assert ast.unparse(guarded.handlers[0].body[-1]) == (
        "raise RuntimeError('knowledge_runtime_lifecycle_import_failed') from None"
    )


def test_uir04c_app_stores_exact_initializer_pump_by_identity_and_rejects_subclasses():
    source = _source("app.py")
    assert source.count('components["knowledge_runtime"]') == 1
    assert source.count('components["index_job_pump"]') == 1
    assert "type(knowledge_runtime) is not KnowledgeRuntime" in source
    assert "type(index_job_pump) is not CooperativeIndexJobPump" in source
    assert "Index job pump component invalid" in source
    assert "index_job_pump_component_invalid" in source
    knowledge_store = source.index("app.state.knowledge_runtime = knowledge_runtime")
    pump_store = source.index("app.state.index_job_pump = index_job_pump")
    pump_read = source.index('components["index_job_pump"]')
    assert pump_read < knowledge_store < pump_store


def test_uir04c_startup_never_wakes_registers_or_constructs_a_worker_runtime():
    source = _source("app.py")
    startup = _function(ast.parse(source), "_startup_event")
    startup_source = ast.unparse(startup)
    assert "app.state.index_job_pump = index_job_pump" in source
    assert "index_job_pump" not in startup_source
    assert "build_inert_cooperative_index_job_pump" not in startup_source
    assert "UnifiedSourceIndexJobRuntime" not in startup_source
    assert ".wakeup(" not in startup_source and ".register(" not in startup_source
    bind = _calls(startup, "bind_knowledge_runtime")
    assert len(bind) == 1


def test_uir04c_shutdown_keeps_knowledge_close_first_then_pump_close_before_transcription():
    shutdown = _function(ast.parse(_source("app.py")), "_shutdown_event")
    knowledge_close = _calls(shutdown, "close_knowledge_runtime")
    pump_close = [
        call for call in ast.walk(shutdown) if isinstance(call, ast.Call)
        and ast.unparse(call.func) == "index_job_pump.close"
    ]
    transcription_stop = [
        call for call in _calls(shutdown, "to_thread")
        if call.args and ast.unparse(call.args[0]) == "transcription_runtime.stop"
    ]
    assert len(knowledge_close) == len(pump_close) == len(transcription_stop) == 1
    assert knowledge_close[0].lineno < pump_close[0].lineno < transcription_stop[0].lineno
    assert [ast.unparse(argument) for argument in knowledge_close[0].args] == ["knowledge_runtime"]
    assert not pump_close[0].args and not pump_close[0].keywords


def test_uir04c_shutdown_continues_pump_close_after_knowledge_close_failure_and_continues_teardown_after_pump_failure():
    import asyncio

    shutdown = _function(ast.parse(_source("app.py")), "_shutdown_event")
    knowledge_try = next(
        node for node in shutdown.body
        if isinstance(node, ast.Try) and _calls(node, "close_knowledge_runtime")
    )
    pump_try = next(
        node for node in shutdown.body
        if isinstance(node, ast.Try)
        and any(
            isinstance(call, ast.Call) and ast.unparse(call.func) == "index_job_pump.close"
            for call in ast.walk(node)
        )
    )
    assert shutdown.body.index(pump_try) == shutdown.body.index(knowledge_try) + 1
    assert _string_calls(knowledge_try, "warning") == [
        "Knowledge runtime close failed; query_knowledge remains unavailable"
    ]
    assert _string_calls(pump_try, "warning") == [
        "Index job pump close failed; no index job work was started"
    ]

    probe = ast.AsyncFunctionDef(
        name="_probe",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[knowledge_try, pump_try, ast.Expr(ast.Call(ast.Name("later_teardown", ast.Load()), [], []))],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[probe], type_ignores=[]))
    calls = []

    class Logger:
        def warning(self, value):
            calls.append(("warning", value))

    class Pump:
        def __init__(self, fail):
            self.fail = fail

        def close(self):
            calls.append("pump_close")
            if self.fail:
                raise ValueError("private-pump-marker")

    for knowledge_fails, pump_fails in ((True, False), (False, True)):
        calls.clear()
        def close_knowledge_runtime(runtime):
            calls.append("knowledge_close")
            if knowledge_fails:
                raise ValueError("private-knowledge-marker")
        namespace = {
            "close_knowledge_runtime": close_knowledge_runtime,
            "knowledge_runtime": object(),
            "index_job_pump": Pump(pump_fails),
            "later_teardown": lambda: calls.append("later_teardown"),
            "logger": Logger(),
        }
        exec(compile(module, "<uir04c-shutdown>", "exec"), namespace)
        asyncio.run(namespace["_probe"]())
        assert calls[0] == "knowledge_close"
        assert "pump_close" in calls and calls[-1] == "later_teardown"


def test_uir04c_disabled_and_read_only_composition_are_inert_and_storeless(tmp_path):
    from src.unified_source_index_job_runner import build_inert_cooperative_index_job_pump

    assert "build_inert_cooperative_index_job_pump(knowledge_runtime_config)" in _source(
        "src/app_initializer.py"
    )
    for mode in ("disabled", "read_only"):
        config = _uir04c_config(tmp_path / mode, mode)
        pump = build_inert_cooperative_index_job_pump(config)
        assert pump.snapshot()["enabled"] is False
        assert pump._runtime is None
        assert not any(hasattr(pump, name) for name in ("store", "provider", "adapter", "scope"))


def test_uir04c_noninert_modes_fail_closed_without_partial_component_or_effect(tmp_path):
    from src.unified_source_index_job_runner import (
        CooperativeIndexJobPumpError,
        build_inert_cooperative_index_job_pump,
    )

    assert "index_job_pump_initialization_failed" in _source("src/app_initializer.py")
    for mode in ("shadow", "canary", "active", "degraded", "rollback"):
        config = _uir04c_config(tmp_path / mode, mode)
        try:
            build_inert_cooperative_index_job_pump(config)
        except CooperativeIndexJobPumpError as error:
            assert error.args == ("invalid_gate",) and error.__cause__ is None
        else:
            raise AssertionError(f"{mode} unexpectedly built an inert App pump")


def test_uir04c_composition_adds_no_sqlite_store_provider_adapter_filesystem_network_thread_task_or_process_effect():
    initializer_tree = ast.parse(_source("src/app_initializer.py"))
    pump_try = _uir04c_pump_try(initializer_tree)
    pump_calls = {
        call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id
        for call in ast.walk(pump_try) if isinstance(call, ast.Call)
        and isinstance(call.func, (ast.Attribute, ast.Name))
    }
    assert pump_calls == {
        "build_inert_cooperative_index_job_pump", "warning", "RuntimeError"
    }
    forbidden = {
        "open", "connect", "SQLiteJobStore", "UnifiedSourceIndexJobRuntime",
        "Thread", "Process", "create_task", "Popen", "run", "wakeup", "register",
    }
    assert not pump_calls & forbidden
    app = _source("app.py")
    component_delta = app[
        app.index('components["index_job_pump"]'):
        app.index("session_manager   =")
    ]
    assert "app.state.index_job_pump = index_job_pump" in component_delta
    assert not any(token in component_delta for token in (
        "sqlite", "store", "provider", "adapter", "socket", "subprocess", "Thread(",
        "create_task", "wakeup", "register",
    ))


def _uir05b_route(tree: ast.AST) -> ast.AsyncFunctionDef:
    return next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "unified_source_index_runtime_status"
    )


def _uir05b_isolated_handler(namespace):
    import copy

    handler = copy.deepcopy(_uir05b_route(ast.parse(_source("app.py"))))
    handler.decorator_list = []
    isolated = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
    exec(compile(isolated, "<uir05b-route>", "exec"), namespace)
    return namespace[handler.name]


def test_uir05b_guarded_runtime_import_adds_exact_status_symbols_and_content_free_bombs():
    tree = ast.parse(_source("app.py"))
    component_index = next(
        index for index, node in enumerate(tree.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "components" for target in node.targets)
    )
    guarded = tree.body[component_index - 1]
    assert isinstance(guarded, ast.Try) and len(guarded.body) == 5
    imports = [node for node in guarded.body if isinstance(node, ast.ImportFrom)]
    assert [(node.module, [alias.name for alias in node.names]) for node in imports][-1] == (
        "src.unified_source_index_runtime_status",
        [
            "UnifiedSourceIndexRuntimeStatusError",
            "project_unified_source_index_runtime_status",
        ],
    )
    handler = guarded.handlers[0]
    assert ast.unparse(handler.body[0]) == (
        "logger.warning('Knowledge runtime lifecycle import failed')"
    )
    assert ast.unparse(handler.body[1]) == (
        "raise RuntimeError('knowledge_runtime_lifecycle_import_failed') from None"
    )


def test_uir05b_app_registers_one_admin_status_route_after_exact_component_storage():
    source = _source("app.py")
    tree = ast.parse(source)
    handler = _uir05b_route(tree)
    path = "/api/diagnostics/unified-source-index/runtime-status"
    assert source.count(f'@app.get("{path}")') == 1
    assert len(handler.decorator_list) == 1
    assert ast.unparse(handler.decorator_list[0]) == f"app.get('{path}')"
    assert [argument.arg for argument in handler.args.args] == ["request"]
    assert ast.unparse(handler.args.args[0].annotation) == "Request"
    assert ast.unparse(handler.returns) == "Dict[str, object]"
    pump_store = next(
        node for node in tree.body if isinstance(node, ast.Assign)
        and any(ast.unparse(target) == "app.state.index_job_pump" for target in node.targets)
    )
    assert pump_store.lineno < handler.lineno
    middleware = next(
        node for node in tree.body if isinstance(node, ast.ImportFrom)
        and node.module == "core.middleware"
    )
    assert [alias.name for alias in middleware.names] == [
        "SecurityHeadersMiddleware", "is_cors_preflight", "require_admin",
    ]


def test_uir05b_non_admin_is_rejected_before_either_component_snapshot():
    import asyncio
    from fastapi import HTTPException, Request

    calls = []

    def require_admin(request):
        calls.append("auth")
        raise HTTPException(403, "Admin only")

    def projector(*args):
        calls.append("projector")
        raise AssertionError("projector must remain untouched")

    class StatusError(RuntimeError):
        pass

    handler = _uir05b_isolated_handler({
        "Request": Request,
        "Dict": dict,
        "HTTPException": HTTPException,
        "require_admin": require_admin,
        "project_unified_source_index_runtime_status": projector,
        "UnifiedSourceIndexRuntimeStatusError": StatusError,
        "knowledge_runtime": object(),
        "index_job_pump": object(),
    })
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(handler(object()))
    assert rejected.value.status_code == 403
    assert calls == ["auth"]


def test_uir05b_admin_status_route_returns_exact_unwrapped_uir05a_projection():
    import asyncio
    from fastapi import HTTPException, Request

    calls = []
    runtime = object()
    pump = object()
    projection = {
        "schema": "odysseus.unified_source_index.runtime_status.v1",
        "knowledge": {},
        "index_job_pump": {},
        "snapshot_atomic": False,
        "live_activation_authorized": False,
    }

    def require_admin(request):
        calls.append(("auth", request))

    def projector(*args):
        calls.append(("projector", args))
        return projection

    class StatusError(RuntimeError):
        pass

    handler = _uir05b_isolated_handler({
        "Request": Request,
        "Dict": dict,
        "HTTPException": HTTPException,
        "require_admin": require_admin,
        "project_unified_source_index_runtime_status": projector,
        "UnifiedSourceIndexRuntimeStatusError": StatusError,
        "knowledge_runtime": runtime,
        "index_job_pump": pump,
    })
    request = object()
    result = asyncio.run(handler(request))
    assert result is projection
    assert calls == [("auth", request), ("projector", (runtime, pump))]


def test_uir05b_projection_failure_maps_to_fixed_content_free_503_from_none():
    import asyncio
    from fastapi import HTTPException, Request

    marker = "private-projector-marker"

    class StatusError(RuntimeError):
        pass

    def projector(*args):
        raise StatusError(marker)

    handler = _uir05b_isolated_handler({
        "Request": Request,
        "Dict": dict,
        "HTTPException": HTTPException,
        "require_admin": lambda request: None,
        "project_unified_source_index_runtime_status": projector,
        "UnifiedSourceIndexRuntimeStatusError": StatusError,
        "knowledge_runtime": object(),
        "index_job_pump": object(),
    })
    with pytest.raises(HTTPException) as unavailable:
        asyncio.run(handler(object()))
    error = unavailable.value
    assert error.status_code == 503
    assert error.detail == "unified_source_index_runtime_status_unavailable"
    assert error.__cause__ is None
    assert marker not in str(error) and marker not in repr(error)


def test_uir05b_repeated_requests_are_fresh_one_read_nonatomic_and_retain_nothing():
    import asyncio
    from fastapi import HTTPException, Request

    calls = []

    class StatusError(RuntimeError):
        pass

    def projector(*args):
        calls.append(args)
        return {
            "schema": "odysseus.unified_source_index.runtime_status.v1",
            "knowledge": {"state": "disabled"},
            "index_job_pump": {"state": "idle"},
            "snapshot_atomic": False,
            "live_activation_authorized": False,
        }

    runtime, pump = object(), object()
    handler = _uir05b_isolated_handler({
        "Request": Request,
        "Dict": dict,
        "HTTPException": HTTPException,
        "require_admin": lambda request: None,
        "project_unified_source_index_runtime_status": projector,
        "UnifiedSourceIndexRuntimeStatusError": StatusError,
        "knowledge_runtime": runtime,
        "index_job_pump": pump,
    })
    first = asyncio.run(handler(object()))
    first["knowledge"]["state"] = "forged"
    second = asyncio.run(handler(object()))
    assert second["knowledge"]["state"] == "disabled"
    assert first is not second
    assert calls == [(runtime, pump), (runtime, pump)]
    assert not hasattr(handler, "cache_info")


def test_uir05b_status_route_exposes_no_owner_health_readiness_or_activation_authority():
    handler = _uir05b_route(ast.parse(_source("app.py")))
    route_source = ast.unparse(handler)
    assert "return project_unified_source_index_runtime_status" not in route_source
    assert route_source.count("project_unified_source_index_runtime_status") == 1
    assert not any(token in route_source for token in (
        "owner", "health", "ready", "degraded", "atomic =", "authorize",
        "generation", "provider", "config",
    ))
    return_node = next(node for node in ast.walk(handler) if isinstance(node, ast.Return))
    assert isinstance(return_node.value, ast.Name) and return_node.value.id == "projection"


def test_uir05b_route_delta_adds_no_store_provider_metrics_file_network_thread_task_process_or_mutation_effect():
    app = _source("app.py")
    initializer = _source("src/app_initializer.py")
    handler = _uir05b_route(ast.parse(app))
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for statement in handler.body for node in ast.walk(statement)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert calls == {
        "require_admin", "project_unified_source_index_runtime_status", "HTTPException",
    }
    assert not calls & {
        "open", "connect", "getenv", "wakeup", "bind", "close", "register",
        "record_operation", "set_queue_depth", "create_task", "Thread", "Process",
    }
    assert "unified_source_index_runtime_status" not in initializer
    startup = ast.unparse(_function(ast.parse(app), "_startup_event"))
    shutdown = ast.unparse(_function(ast.parse(app), "_shutdown_event"))
    assert "unified_source_index_runtime_status" not in startup
    assert "unified_source_index_runtime_status" not in shutdown
    assert "app.state.unified_source_index_runtime_status" not in app

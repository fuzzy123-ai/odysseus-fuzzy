import ast
import builtins
import concurrent.futures
import inspect
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import threading

import pytest

import src.unified_source_index_runtime as runtime_module
from src.unified_source_index_runtime import (
    KnowledgeRuntime,
    KnowledgeRuntimeError,
    bind_knowledge_runtime,
    build_knowledge_runtime,
    close_knowledge_runtime,
)


SCHEMA = "odysseus.knowledge_runtime.composition_snapshot.v1"


def _assert_code(code, call):
    with pytest.raises(KnowledgeRuntimeError) as caught:
        call()
    assert caught.value.code == code
    assert caught.value.args == (code,)
    assert str(caught.value) == code
    return caught.value


class _Planner:
    def execute(self, request):
        return request


def test_uir03a_a2_public_api_signatures_all_and_fresh_errors_are_exact():
    assert runtime_module.__all__ == (
        "KnowledgeRuntime", "KnowledgeRuntimeError", "bind_knowledge_runtime",
        "build_knowledge_runtime", "close_knowledge_runtime",
    )
    assert str(inspect.signature(KnowledgeRuntime.snapshot)) == "(self, /) -> 'dict[str, object]'"
    assert str(inspect.signature(build_knowledge_runtime)) == "(config: 'tuple[str, bool]', *, planner: 'object | None' = None) -> 'KnowledgeRuntime'"
    assert str(inspect.signature(bind_knowledge_runtime)) == "(runtime: 'KnowledgeRuntime', binder: 'object') -> 'None'"
    assert str(inspect.signature(close_knowledge_runtime)) == "(runtime: 'KnowledgeRuntime') -> 'None'"
    assert KnowledgeRuntimeError.__bases__ == (ValueError,)
    _assert_code("invalid_binder", lambda: bind_knowledge_runtime(build_knowledge_runtime(("disabled", False)), [].append))
    first = _assert_code("invalid_config", lambda: build_knowledge_runtime(object()))
    second = _assert_code("invalid_config", lambda: build_knowledge_runtime(object()))
    assert first is not second


def test_uir03a_a2_primitive_config_is_exact_bounded_copied_once_and_content_free():
    assert build_knowledge_runtime(("disabled", False)).snapshot()["state"] == "disabled"
    for bad in (("disabled", True), ("read_only", False), ("nope", True), ["disabled", False], ("disabled", 0), ("disabled", False, None)):
        _assert_code("invalid_config", lambda bad=bad: build_knowledge_runtime(bad))


def test_uir03a_a2_disabled_and_uncomposed_modes_bind_inert_without_planner_access():
    class Bomb:
        @property
        def execute(self):
            raise AssertionError("must not inspect")
    for config, state, code in [(("disabled", False), "disabled", None), (("active", True), "degraded", "mode_not_composed")]:
        runtime = build_knowledge_runtime(config, planner=Bomb())
        seen = []
        def binder(value):
            seen.append(value)
        bind_knowledge_runtime(runtime, binder)
        assert seen == [None]
        assert runtime.snapshot()["state"] == state
        assert runtime.snapshot()["error_code"] == code
        close_knowledge_runtime(runtime)
        assert runtime.snapshot()["error_code"] == code
    unavailable = build_knowledge_runtime(("read_only", True))
    def unavailable_binder(value):
        assert value is None
    bind_knowledge_runtime(unavailable, unavailable_binder)
    close_knowledge_runtime(unavailable)
    assert unavailable.snapshot()["error_code"] == "planner_unavailable"


def test_uir03a_a2_read_only_token_is_inactive_inside_binder_then_active_after_commit():
    runtime = build_knowledge_runtime(("read_only", True), planner=_Planner())
    retained = []
    def binder(token):
        retained.append(token)
        _assert_code("token_inactive", lambda: token.execute("early"))
    bind_knowledge_runtime(runtime, binder)
    assert retained[0].execute("after") == "after"
    assert runtime.snapshot()["planner_bound"] is True


def test_uir03a_a2_binder_retains_token_then_raises_and_retained_token_is_revoked():
    runtime = build_knowledge_runtime(("read_only", True), planner=_Planner())
    retained = []
    def binder(token):
        retained.append(token)
        raise RuntimeError("private")
    _assert_code("bind_failed", lambda: bind_knowledge_runtime(runtime, binder))
    _assert_code("token_inactive", lambda: retained[0].execute("x"))
    assert runtime.snapshot()["error_code"] == "bind_failed"


def test_uir03a_a2_planner_capture_ignores_hostile_descriptors_metaclasses_and_inheritance():
    touched = []
    class Descriptor:
        def __get__(self, instance, owner):
            touched.append("descriptor")
            raise AssertionError
    class DescriptorPlanner:
        execute = Descriptor()
    class Parent:
        def execute(self, request): return request
    class Child(Parent): pass
    class Meta(type):
        def __getattribute__(self, name):
            touched.append(name)
            raise AssertionError
    class MetaPlanner(metaclass=Meta):
        def execute(self, request): return request
    for planner in (DescriptorPlanner(), Child(), MetaPlanner()):
        result = build_knowledge_runtime(("read_only", True), planner=planner)
        assert result.snapshot()["error_code"] == "invalid_planner"
        def binder(value):
            assert value is None
        bind_knowledge_runtime(result, binder)
        close_knowledge_runtime(result)
        assert result.snapshot()["error_code"] == "invalid_planner"
    assert touched == []


def test_uir03a_a2_planner_execute_replacement_after_build_is_inert():
    class Planner:
        def execute(self, request): return ("captured", request)
    planner = Planner()
    runtime = build_knowledge_runtime(("read_only", True), planner=planner)
    planner.execute = lambda request: ("instance", request)
    Planner.execute = lambda self, request: ("class", request)
    retained = []
    def binder(token):
        retained.append(token)
    bind_knowledge_runtime(runtime, binder)
    assert retained[0].execute("x") == ("captured", "x")


def test_uir03a_a2_runtime_direct_subclass_object_new_and_attribute_tamper_cannot_mint_registry_identity():
    _assert_code("invalid_runtime", KnowledgeRuntime)
    class SubRuntime(KnowledgeRuntime): pass
    _assert_code("invalid_runtime", SubRuntime)
    for forged in (object.__new__(KnowledgeRuntime), object.__new__(SubRuntime)):
        _assert_code("invalid_runtime", forged.snapshot)
        _assert_code("invalid_runtime", lambda forged=forged: bind_knowledge_runtime(forged, lambda _: None))
        _assert_code("invalid_runtime", lambda forged=forged: close_knowledge_runtime(forged))
    runtime = build_knowledge_runtime(("disabled", False))
    with pytest.raises(AttributeError):
        object.__setattr__(runtime, "state", "forged")
    assert runtime.snapshot()["state"] == "disabled"


def test_uir03a_a2_token_clone_object_new_and_attribute_tamper_never_mint_execution():
    runtime = build_knowledge_runtime(("read_only", True), planner=_Planner())
    tokens = []
    def binder(token):
        tokens.append(token)
    bind_knowledge_runtime(runtime, binder)
    token = tokens[0]
    clone = object.__new__(type(token))
    _assert_code("token_inactive", lambda: clone.execute("x"))
    with pytest.raises(AttributeError):
        object.__setattr__(token, "execute", lambda _: "forged")
    close_knowledge_runtime(runtime)
    _assert_code("token_inactive", lambda: token.execute("x"))


def test_uir03a_a2_snapshot_is_fresh_six_field_derived_and_never_live_authorizing():
    runtime = build_knowledge_runtime(("disabled", False))
    first, second = runtime.snapshot(), runtime.snapshot()
    assert type(first) is dict and first is not second and list(first) == ["schema", "state", "mode", "planner_bound", "error_code", "live_activation_authorized"]
    assert first == {"schema": SCHEMA, "state": "disabled", "mode": "disabled", "planner_bound": False, "error_code": None, "live_activation_authorized": False}
    first["state"] = "forged"
    assert runtime.snapshot()["state"] == "disabled"


def test_uir03a_a2_repeat_reentrant_and_concurrent_bind_install_exactly_once():
    runtime = build_knowledge_runtime(("disabled", False))
    calls = []
    def binder(value):
        calls.append(value)
        _assert_code("bind_already_attempted", lambda: bind_knowledge_runtime(runtime, binder))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = [pool.submit(bind_knowledge_runtime, runtime, binder) for _ in range(4)]
        outcomes = [result.exception() for result in results]
    assert outcomes.count(None) == 1
    assert all(item is None or item.code == "bind_already_attempted" for item in outcomes)
    assert calls == [None]


def test_uir03a_a2_reentrant_close_during_bind_projects_closed_and_unbinds_once_before_bind_returns():
    runtime = build_knowledge_runtime(("read_only", True), planner=_Planner())
    calls = []
    def binder(value):
        calls.append(value)
        if value is not None:
            close_knowledge_runtime(runtime)
            assert runtime.snapshot()["state"] == "closed"
    bind_knowledge_runtime(runtime, binder)
    assert calls[0] is not None and calls[1:] == [None]
    assert runtime.snapshot()["state"] == "closed"


def test_uir03a_a2_concurrent_close_during_bind_is_nonblocking_revocation_and_unbinds_once():
    runtime = build_knowledge_runtime(("read_only", True), planner=_Planner())
    entered, release = threading.Event(), threading.Event()
    calls = []
    def binder(value):
        calls.append(value)
        if value is not None:
            entered.set(); assert release.wait(5)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        binding = pool.submit(bind_knowledge_runtime, runtime, binder)
        assert entered.wait(5)
        assert pool.submit(close_knowledge_runtime, runtime).result(2) is None
        assert runtime.snapshot()["state"] == "closed"
        release.set(); assert binding.result(5) is None
    assert calls[1:] == [None]


def test_uir03a_a2_concurrent_close_after_bind_revokes_before_one_unbind_and_never_retries():
    runtime = build_knowledge_runtime(("read_only", True), planner=_Planner())
    retained, calls = [], []
    def binder(value): retained.append(value); calls.append(value)
    bind_knowledge_runtime(runtime, binder)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        assert all(item.result(5) is None for item in [pool.submit(close_knowledge_runtime, runtime) for _ in range(4)])
    _assert_code("token_inactive", lambda: retained[0].execute("x"))
    assert calls[1:] == [None]


def test_uir03a_a2_bind_and_unbind_failures_are_fresh_content_free_and_terminal():
    bad = build_knowledge_runtime(("disabled", False))
    _assert_code("bind_failed", lambda: bind_knowledge_runtime(bad, lambda _: (_ for _ in ()).throw(RuntimeError("secret"))))
    _assert_code("bind_already_attempted", lambda: bind_knowledge_runtime(bad, lambda _: None))
    close_knowledge_runtime(bad)
    assert bad.snapshot()["error_code"] == "bind_failed"
    runtime = build_knowledge_runtime(("disabled", False)); calls = []
    def binder(value):
        calls.append(value)
        if len(calls) == 2 and value is None: raise RuntimeError("secret")
    bind_knowledge_runtime(runtime, binder)
    _assert_code("close_failed", lambda: close_knowledge_runtime(runtime))
    close_knowledge_runtime(runtime); assert calls == [None, None]


def test_uir03a_a2_token_before_commit_after_failed_bind_and_after_close_is_inactive():
    runtime = build_knowledge_runtime(("read_only", True), planner=_Planner())
    tokens = []
    def binder(token):
        tokens.append(token); _assert_code("token_inactive", lambda: token.execute("early")); raise RuntimeError
    _assert_code("bind_failed", lambda: bind_knowledge_runtime(runtime, binder))
    _assert_code("token_inactive", lambda: tokens[0].execute("late"))


def test_uir03a_a2_admitted_execute_may_finish_but_no_execute_starts_after_close():
    entered, release = threading.Event(), threading.Event()
    class Planner:
        def execute(self, request): entered.set(); assert release.wait(5); return request
    runtime = build_knowledge_runtime(("read_only", True), planner=Planner()); tokens = []
    def binder(token):
        tokens.append(token)
    bind_knowledge_runtime(runtime, binder)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(tokens[0].execute, "ok")
        assert entered.wait(5); close_knowledge_runtime(runtime); release.set()
        assert running.result(5) == "ok"
    _assert_code("token_inactive", lambda: tokens[0].execute("no"))


def test_uir03a_a2_planner_failure_is_content_free_planner_failed():
    class Planner:
        def execute(self, request): raise RuntimeError("private detail")
    runtime = build_knowledge_runtime(("read_only", True), planner=Planner()); tokens = []
    def binder(token):
        tokens.append(token)
    bind_knowledge_runtime(runtime, binder)
    _assert_code("planner_failed", lambda: tokens[0].execute("x"))


def test_uir03a_a2_cold_source_exec_has_zero_additional_open_environment_network_or_product_import_effects(monkeypatch):
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    calls = []
    def bomb(*args, **kwargs): calls.append("effect"); raise AssertionError
    monkeypatch.setattr(os, "getenv", bomb); monkeypatch.setattr(type(os.environ), "get", bomb)
    namespace = {"__name__": "isolated_uir03a", "__package__": "src"}
    exec(compile(source, "<isolated>", "exec"), namespace)
    assert calls == []
    assert namespace["build_knowledge_runtime"](("disabled", False)).snapshot()["state"] == "disabled"


def test_uir03a_a2_import_build_snapshot_bind_execute_and_close_trip_no_effect_boundary_bombs(monkeypatch):
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {node.module if isinstance(node, ast.ImportFrom) else alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in (node.names if isinstance(node, ast.Import) else [None])}
    assert imports == {"__future__", "_thread", "_weakref"}
    calls = []
    def bomb(*args, **kwargs): calls.append("effect"); raise AssertionError
    for target, name in [(os, "getenv"), (sqlite3, "connect"), (socket, "socket"), (socket, "create_connection"), (subprocess, "run"), (subprocess, "Popen"), (Path, "open")]: monkeypatch.setattr(target, name, bomb)
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if any(word in name.lower() for word in ("app", "obsidian", "sqlite", "provider", "worker")): return bomb()
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    runtime = build_knowledge_runtime(("read_only", True), planner=_Planner()); tokens = []
    def binder(token):
        tokens.append(token)
    bind_knowledge_runtime(runtime, binder); assert tokens[0].execute("x") == "x"; close_knowledge_runtime(runtime)
    assert calls == []

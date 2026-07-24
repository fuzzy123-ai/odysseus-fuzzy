"""Tests for the drop-in plugin system: manifest discovery, setup/teardown
lifecycle, live enable/disable (routes + services), persistence, and isolation
of a broken plugin.

Uses self-contained demo plugins written to a temp dir, so nothing here depends
on Odysseus internals.
"""
import ast
import json
import os
from pathlib import Path
import textwrap

import pytest
from fastapi import FastAPI

from src.plugin_system import (
    PluginManager,
    _route_path,
    _route_paths,
    get_consolidation_jobs,
    get_context_providers,
)


def _write(pdir, pid, body):
    d = os.path.join(pdir, pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(body))


DEMO = '''
    PLUGIN = {"name": "Demo", "version": "0.2.0", "author": "t",
              "description": "demo", "category": "Test"}
    counters = {"start": 0, "stop": 0}
    def setup(ctx):
        from fastapi import APIRouter
        r = APIRouter()
        @r.get("/api/plugins/demo/ping")
        async def ping():
            return {"ok": True}
        ctx.add_router(r)
        ctx.add_service(start=lambda: counters.__setitem__("start", counters["start"] + 1),
                        stop=lambda: counters.__setitem__("stop", counters["stop"] + 1))
'''

BROKEN = '''
    PLUGIN = {"name": "Broken", "version": "1.0.0"}
    def setup(ctx):
        raise RuntimeError("boom")
'''


CONTEXT_PLUGIN = '''
    PLUGIN = {"name": "Context Demo", "version": "0.1.0"}
    def retrieve(owner, query, budget, mode):
        return {
            "owner": owner,
            "query": query,
            "budget": budget,
            "mode": mode,
            "snippets": ["demo"],
        }
    def run_consolidation(owner=None):
        return {"owner": owner, "ok": True}
    def setup(ctx):
        ctx.register_context_provider({
            "id": "demo.context",
            "label": "Demo Context",
            "priority": 25,
            "capabilities": ["chat", "agent"],
            "retrieve": retrieve,
        })
        ctx.register_consolidation_job({
            "id": "demo.consolidate",
            "label": "Demo Consolidation",
            "priority": 5,
            "capabilities": ["memory"],
            "run": run_consolidation,
        })
'''


TOOL_PLUGIN = '''
    PLUGIN = {"name": "Tool Demo", "version": "0.1.0"}
    def run(content, **kwargs):
        return {"output": content, "exit_code": 0}
    def setup(ctx):
        ctx.register_tool({
            "name": "tool_demo_one",
            "description": "Demo tool one.",
            "parameters": {"type": "object", "properties": {}},
            "handler": run,
        })
        ctx.register_tool({
            "name": "tool_demo_two",
            "description": "Demo tool two.",
            "parameters": {"type": "object", "properties": {}},
            "handler": run,
        })
'''


BROKEN_CONTEXT_PLUGIN = '''
    PLUGIN = {"name": "Broken Context", "version": "0.1.0"}
    def retrieve(owner, query, budget, mode):
        return {}
    def setup(ctx):
        ctx.register_context_provider({
            "id": "broken.context",
            "label": "Broken Context",
            "retrieve": retrieve,
        })
        raise RuntimeError("boom after provider")
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    pdir = tmp_path / "plugins"; pdir.mkdir()
    data = tmp_path / "data"; data.mkdir()
    monkeypatch.setenv("ODYSSEUS_PLUGINS_DIR", str(pdir))
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(data))
    monkeypatch.setenv("DATA_DIR", str(data))
    return str(pdir), str(data)


def _routes(app):
    return [r.path for r in app.router.routes if getattr(r, "path", "").startswith("/api/plugins/demo")]


def test_route_path_supports_starlette_path_format_only():
    class RouteLike:
        path_format = "/api/plugins/demo/ping"

    assert _route_path(RouteLike()) == "/api/plugins/demo/ping"


def test_route_paths_supports_fastapi_included_router_wrapper():
    class ChildRoute:
        path = "/api/plugins/demo/ping"

    class OriginalRouter:
        routes = [ChildRoute()]

    class IncludedRouterLike:
        original_router = OriginalRouter()

    assert _route_paths(IncludedRouterLike()) == ["/api/plugins/demo/ping"]


def test_manifest_read_without_executing(env):
    pdir, _ = env
    _write(pdir, "demo", DEMO)
    mgr = PluginManager(app=FastAPI(), directory=pdir)
    mgr.discover()                       # discovery must NOT import/run the module
    rec = mgr.list()[0]
    assert rec["id"] == "demo" and rec["name"] == "Demo" and rec["version"] == "0.2.0"
    assert rec["status"] == "discovered"


def test_load_mounts_route_and_starts_service(env):
    pdir, _ = env
    _write(pdir, "demo", DEMO)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)
    assert mgr.load_enabled(app) == 1
    assert _routes(app) == ["/api/plugins/demo/ping"]
    assert mgr.list()[0]["status"] == "loaded"


def test_plugin_tool_registration_logs_summary_not_each_tool(env, caplog):
    pdir, _ = env
    _write(pdir, "tooldemo", TOOL_PLUGIN)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)

    with caplog.at_level("INFO"):
        assert mgr.load_enabled(app) == 1

    messages = [record.getMessage() for record in caplog.records]
    assert "Plugin loaded: tooldemo (registered 2 tool(s))" in messages
    assert not any(message.startswith("Registered plugin tool:") for message in messages)
    mgr.shutdown_all()


def test_disable_then_enable_toggles_routes_and_services(env):
    pdir, _ = env
    _write(pdir, "demo", DEMO)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)
    mgr.load_enabled(app)
    counters = mgr.records["demo"].module.counters
    assert counters["start"] == 1 and _routes(app)

    mgr.disable("demo")
    assert _routes(app) == [] and counters["stop"] == 1
    assert mgr.list()[0]["enabled"] is False and mgr.list()[0]["status"] == "disabled"

    mgr.enable("demo")
    assert _routes(app) == ["/api/plugins/demo/ping"] and counters["start"] == 2


def test_disabled_state_persists(env):
    pdir, data = env
    _write(pdir, "demo", DEMO)
    mgr = PluginManager(app=FastAPI(), directory=pdir)
    mgr.load_enabled()
    mgr.disable("demo")
    with open(os.path.join(data, "plugins.json"), encoding="utf-8") as f:
        assert json.load(f)["demo"]["enabled"] is False
    # a fresh manager respects the persisted state — does not load it
    app2 = FastAPI()
    mgr2 = PluginManager(app=app2, directory=pdir)
    assert mgr2.load_enabled(app2) == 0
    assert mgr2.list()[0]["enabled"] is False


def test_broken_plugin_is_isolated(env):
    pdir, _ = env
    _write(pdir, "demo", DEMO)
    _write(pdir, "broken", BROKEN)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)
    loaded = mgr.load_enabled(app)            # must not raise
    assert loaded == 1                        # demo loads; broken fails
    by_id = {p["id"]: p for p in mgr.list()}
    assert by_id["demo"]["status"] == "loaded"
    assert by_id["broken"]["status"] == "error" and by_id["broken"]["error"]
    assert _routes(app) == ["/api/plugins/demo/ping"]   # broken left nothing behind


def test_shutdown_all_stops_services(env):
    pdir, _ = env
    _write(pdir, "demo", DEMO)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)
    mgr.load_enabled(app)
    mgr.shutdown_all()
    assert mgr.records["demo"].module.counters["stop"] == 1 and _routes(app) == []


def test_context_provider_and_consolidation_job_lifecycle(env):
    pdir, _ = env
    _write(pdir, "contextdemo", CONTEXT_PLUGIN)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)

    assert mgr.load_enabled(app) == 1

    providers = get_context_providers()
    assert [provider.id for provider in providers] == ["demo.context"]
    provider = providers[0]
    assert provider.plugin_id == "contextdemo"
    assert provider.label == "Demo Context"
    assert provider.priority == 25
    assert provider.capabilities == ("chat", "agent")
    assert provider.retrieve(owner="alice", query="hello", budget=128, mode="chat")["snippets"] == ["demo"]
    assert get_context_providers(capability="agent") == [provider]
    assert get_context_providers(capability="missing") == []

    jobs = get_consolidation_jobs()
    assert [job.id for job in jobs] == ["demo.consolidate"]
    assert jobs[0].plugin_id == "contextdemo"
    assert jobs[0].run(owner="alice") == {"owner": "alice", "ok": True}

    mgr.disable("contextdemo")

    assert get_context_providers() == []
    assert get_consolidation_jobs() == []


def test_context_provider_rolls_back_when_setup_fails(env):
    pdir, _ = env
    _write(pdir, "brokencontext", BROKEN_CONTEXT_PLUGIN)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)

    assert mgr.load_enabled(app) == 0
    assert mgr.list()[0]["status"] == "error"
    assert get_context_providers() == []


OFF_NAMESPACE = '''
    PLUGIN = {"name": "OffNs", "version": "1.0.0"}
    def setup(ctx):
        from fastapi import APIRouter
        r = APIRouter()
        @r.get("/static/evil")          # auth-exempt prefix → must be rejected
        async def evil(): return {"x": 1}
        ctx.add_router(r)
'''


def test_add_router_rejects_off_namespace_routes(env):
    pdir, _ = env
    _write(pdir, "offns", OFF_NAMESPACE)
    app = FastAPI()
    mgr = PluginManager(app=app, directory=pdir)
    assert mgr.load_enabled(app) == 0                       # plugin fails to load
    assert mgr.list()[0]["status"] == "error"
    assert not any(getattr(r, "path", "") == "/static/evil" for r in app.router.routes)


def test_ui_field_sanitized():
    """public()'s `ui.open` must be a same-origin path — blocks javascript:/`//evil`."""
    from src.plugin_system import _safe_ui
    assert _safe_ui({"ui": {"open": "/api/plugins/x/app"}}) == {"open": "/api/plugins/x/app", "label": "Open"}
    assert _safe_ui({"ui": {"open": "/api/x", "label": "Go"}})["label"] == "Go"
    assert _safe_ui({"ui": {"open": "javascript:alert(1)"}}) is None
    assert _safe_ui({"ui": {"open": "//evil.com/x"}}) is None
    assert _safe_ui({"ui": {"open": 123}}) is None
    assert _safe_ui({}) is None


def test_core_has_no_direct_obsidian_imports():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module == "plugins.obsidian" or module.startswith("plugins.obsidian."):
                    relative = path.relative_to(root).as_posix()
                    offenders.append(f"{relative}:{node.lineno}:{module}")
    assert offenders == []

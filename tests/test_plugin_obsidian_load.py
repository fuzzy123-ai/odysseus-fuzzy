import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
import src.plugin_system as plugin_system
from src.tool_registry import get_tool
from src.plugin_system import get_context_providers
from routes.model_routes import setup_model_routes
from routes.plugin_routes import setup_plugin_routes


def test_obsidian_plugin_loads_through_plugin_manager(tmp_path, monkeypatch, request):
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "plugins" / "obsidian"
    plugins_dir = tmp_path / "plugins"
    target = plugins_dir / "obsidian"
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
    )
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(plugin_system, "MANAGER", None)

    app = FastAPI()
    app.include_router(setup_model_routes(model_discovery=None))
    app.include_router(setup_plugin_routes())
    manager = plugin_system.PluginManager(app=app, directory=str(plugins_dir))
    monkeypatch.setattr(plugin_system, "MANAGER", manager)
    manager.load_enabled(app)
    request.addfinalizer(manager.shutdown_all)

    record = manager.records["obsidian"]
    paths = {
        path
        for route in app.router.routes
        for path in plugin_system._route_paths(route)
    }

    assert record.status == "loaded", record.error
    assert record.public()["version"] == "0.10.0-rc.1"
    assert record.public()["ui"] == {
        "open": "/api/plugins/obsidian/app",
        "label": "Open Vault",
        "script": "/api/plugins/obsidian/web/main.js",
    }
    assert "/api/plugins/obsidian/app" in paths
    assert "/api/plugins/obsidian/files" in paths
    assert "/api/plugins/obsidian/tags" in paths
    assert "/api/plugins/obsidian/graph" in paths
    assert "/api/plugins/obsidian/ai-status" in paths
    assert "/api/plugins/obsidian/project-plan/sessions" in paths
    assert "/api/plugins/obsidian/project-plan/sessions/{session_id}" in paths
    assert "/api/plugins/obsidian/project-plan/sessions/{session_id}/preview-stream" in paths
    assert "/api/plugins/obsidian/project-plan/sessions/{session_id}/apply" in paths
    assert "/api/plugins/obsidian/relationships" in paths
    assert "/api/plugins/obsidian/history" in paths
    assert "/api/plugins/obsidian/history/undo" in paths
    assert "/api/plugins/obsidian/memory/status" in paths
    assert "/api/plugins/obsidian/memory/baseline" in paths
    assert "/api/plugins/obsidian/memory-tree/analyze" in paths
    assert "/api/plugins/obsidian/knowledge-audit" in paths
    assert "/api/plugins/obsidian/quarantine" in paths
    assert "/api/plugins/obsidian/raptor/status" in paths
    assert "/api/plugins/obsidian/raptor/graph" in paths
    assert "/api/plugins/obsidian/raptor/rebuild" in paths
    assert "/api/plugins/obsidian/project-plan/templates" in paths
    assert "/api/plugins/obsidian/project-plan/preview" in paths
    assert "/api/plugins/obsidian/project-plan/apply" in paths
    assert "/api/plugins/obsidian/web/{filename:path}" in paths
    assert "/api/plugins/orca/app" in paths
    assert "/api/plugins/orca/files" in paths
    assert "/api/plugins/orca/memory/status" in paths
    assert "/api/plugins/orca/memory/baseline" in paths
    assert "/api/plugins/orca/memory/query" in paths
    assert "/api/plugins/orca/raptor/status" in paths
    assert "/api/plugins/orca/raptor/graph" in paths
    assert "/api/plugins/orca/raptor/rebuild" in paths
    assert "/api/plugins/orca/web/{filename:path}" in paths
    assert get_tool("obsidian_list_notes") is not None
    assert get_tool("obsidian_read_note") is not None
    assert get_tool("obsidian_list_tags") is not None
    assert get_tool("obsidian_graph") is not None
    assert get_tool("obsidian_add_relationship") is not None
    assert get_tool("obsidian_history") is not None
    assert get_tool("obsidian_undo") is not None
    assert get_tool("obsidian_memory_status") is not None
    assert get_tool("obsidian_memory_tree_status") is not None
    assert get_tool("obsidian_knowledge_audit") is not None
    assert get_tool("obsidian_quarantine_list") is not None
    assert get_tool("obsidian_raptor_status") is not None
    assert get_tool("obsidian_raptor_graph_view") is not None
    assert get_tool("obsidian_raptor_rebuild") is not None
    assert get_tool("obsidian_project_plan_preview") is not None
    assert get_tool("obsidian_project_plan_apply") is not None
    assert get_tool("orca_list_notes") is not None
    assert get_tool("orca_read_note") is not None
    assert get_tool("orca_graph") is not None
    assert get_tool("orca_memory_status") is not None
    assert get_tool("orca_raptor_status") is not None
    obsidian_providers = [provider for provider in get_context_providers() if provider.id == "obsidian.vault_context"]
    assert obsidian_providers
    orca_providers = [provider for provider in get_context_providers() if provider.id == "orca.vault_context"]
    assert orca_providers
    assert orca_providers[0].retrieve is not obsidian_providers[0].retrieve
    assert {
        "chat",
        "agent",
        "memory",
        "readiness",
        "freshness_gate",
        "raptor",
        "hybrid_retrieval",
    } <= set(obsidian_providers[0].capabilities)
    client = TestClient(app)
    app_response = client.get("/api/plugins/obsidian/app")
    assert app_response.status_code == 200
    assert "/api/plugins/obsidian/web/app.js" in app_response.text
    assert "/api/plugins/orca/web/app.js" not in app_response.text
    orca_app_response = client.get("/api/plugins/orca/app")
    assert orca_app_response.status_code == 200
    assert "/api/plugins/orca/web/app.js" in orca_app_response.text
    assert "/api/plugins/obsidian/web/app.js" not in orca_app_response.text
    assert "ODYSSEUS_OBSIDIAN_STANDALONE" not in app_response.text
    app_asset_response = client.get("/api/plugins/obsidian/web/app.js")
    assert app_asset_response.status_code == 200
    assert "ODYSSEUS_OBSIDIAN_STANDALONE" in app_asset_response.text
    assert "await import(`${pluginApiPrefix}/web/main.js`)" in app_asset_response.text
    orca_app_asset_response = client.get("/api/plugins/orca/web/app.js")
    assert orca_app_asset_response.status_code == 200
    assert "ODYSSEUS_OBSIDIAN_STANDALONE" in orca_app_asset_response.text
    asset_response = client.get("/api/plugins/obsidian/web/main.js")
    assert asset_response.status_code == 200
    assert "obsidian-panel" in asset_response.text
    assert "openPanel" in asset_response.text

    tools_response = client.get(
        "/api/tools",
        headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN},
    )
    assert tools_response.status_code == 200
    visible_tools = {tool["id"]: tool for tool in tools_response.json()["tools"]}
    assert "obsidian_list_notes" in visible_tools
    assert visible_tools["obsidian_list_notes"]["cat"] == "Plugins"
    assert visible_tools["obsidian_list_notes"]["enabled"] is True
    assert "obsidian_graph" in visible_tools
    assert visible_tools["obsidian_graph"]["desc"]
    assert "obsidian_add_relationship" in visible_tools
    assert "obsidian_undo" in visible_tools
    assert "obsidian_memory_status" in visible_tools
    assert "readiness_gate" in visible_tools["obsidian_memory_status"]["desc"]
    assert "retrieval_policy" in visible_tools["obsidian_memory_status"]["desc"]
    assert "freshness_isolation_flags" in visible_tools["obsidian_memory_status"]["desc"]
    assert "raptor_lineage_flags" in visible_tools["obsidian_memory_status"]["desc"]
    assert "raptor_write_gate" in visible_tools["obsidian_memory_status"]["desc"]
    assert "warnings" in visible_tools["obsidian_memory_status"]["desc"]
    assert "obsidian_memory_tree_status" in visible_tools
    assert "obsidian_knowledge_audit" in visible_tools
    assert "obsidian_quarantine_list" in visible_tools
    assert "obsidian_raptor_status" in visible_tools
    assert "obsidian_raptor_graph_view" in visible_tools
    assert "obsidian_raptor_rebuild" in visible_tools
    assert "obsidian_project_plan_preview" in visible_tools
    assert "obsidian_project_plan_apply" in visible_tools
    assert "orca_memory_status" in visible_tools
    assert "orca_raptor_status" in visible_tools
    assert "ORCA Local Markdown Vault" in visible_tools["orca_graph"]["desc"]
    ui_loader_response = TestClient(app).get("/api/plugins/ui-loader.js")
    assert ui_loader_response.status_code == 200
    assert "/api/plugins/obsidian/web/main.js" in ui_loader_response.text
    assert "obsidian" in ui_loader_response.text
    from src.agent_loop import _loaded_plugins_prompt

    assert "obsidian v0.10.0-rc.1" in _loaded_plugins_prompt()

    manager.disable("obsidian")
    assert get_tool("obsidian_list_notes") is None
    assert get_tool("obsidian_read_note") is None
    assert get_tool("obsidian_list_tags") is None
    assert get_tool("obsidian_graph") is None
    assert get_tool("obsidian_add_relationship") is None
    assert get_tool("obsidian_history") is None
    assert get_tool("obsidian_undo") is None
    assert get_tool("obsidian_memory_status") is None
    assert get_tool("obsidian_memory_tree_status") is None
    assert get_tool("obsidian_knowledge_audit") is None
    assert get_tool("obsidian_quarantine_list") is None
    assert get_tool("obsidian_raptor_status") is None
    assert get_tool("obsidian_raptor_graph_view") is None
    assert get_tool("obsidian_raptor_rebuild") is None
    assert get_tool("obsidian_project_plan_preview") is None
    assert get_tool("obsidian_project_plan_apply") is None
    assert get_tool("orca_list_notes") is None
    assert get_tool("orca_read_note") is None
    assert get_tool("orca_graph") is None
    assert get_tool("orca_memory_status") is None
    assert get_tool("orca_raptor_status") is None
    assert all(provider.id != "obsidian.vault_context" for provider in get_context_providers())
    assert all(provider.id != "orca.vault_context" for provider in get_context_providers())


def test_obsidian_plugin_routes_require_authentication_middleware(tmp_path, monkeypatch, request):
    import shutil
    from starlette.middleware.base import BaseHTTPMiddleware
    from fastapi.responses import JSONResponse

    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "plugins" / "obsidian"
    plugins_dir = tmp_path / "plugins"
    target = plugins_dir / "obsidian"
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
    )
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(plugin_system, "MANAGER", None)

    app = FastAPI()
    app.include_router(setup_plugin_routes())
    manager = plugin_system.PluginManager(app=app, directory=str(plugins_dir))
    monkeypatch.setattr(plugin_system, "MANAGER", manager)
    manager.load_enabled(app)
    request.addfinalizer(manager.shutdown_all)

    # Add a mock AuthMiddleware simulating app.py behavior
    class MockAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            # /api/plugins/ui-loader.js is exempt, but others starting with /api/ are not
            if path == "/api/plugins/ui-loader.js":
                return await call_next(request)
            
            # Simple session check
            cookie = request.cookies.get("session_token")
            if not cookie or cookie != "valid_session":
                if path.startswith("/api/"):
                    return JSONResponse(status_code=401, content={"error": "Not authenticated"})
            return await call_next(request)

    app.add_middleware(MockAuthMiddleware)
    client = TestClient(app)

    # 1. UI Loader is exempt, should succeed
    ui_loader_response = client.get("/api/plugins/ui-loader.js")
    assert ui_loader_response.status_code == 200

    # 2. Unauthenticated requests to obsidian routes should fail with 401
    assert client.get("/api/plugins/obsidian/app").status_code == 401
    assert client.get("/api/plugins/obsidian/web/main.js").status_code == 401
    assert client.get("/api/plugins/obsidian/files").status_code == 401

    # 3. Authenticated requests with cookie should succeed
    client.cookies.set("session_token", "valid_session")
    assert client.get("/api/plugins/obsidian/app").status_code == 200
    assert client.get("/api/plugins/obsidian/web/main.js").status_code == 200

    manager.disable("obsidian")

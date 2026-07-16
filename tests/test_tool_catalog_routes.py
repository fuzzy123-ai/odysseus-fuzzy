from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.model_routes import setup_model_routes
from src.builtin_tool_catalog import DEFAULT_DEFERRED_TOOLS
from src.tool_registry import ToolSpec, get_tool, register_tool, unregister_tool


class _McpInventory:
    def get_all_tools(self):
        return [
            {
                "server_id": "review",
                "server_name": "Review",
                "name": "lookup",
                "qualified_name": "mcp__review__lookup",
                "description": "Authorization: Bearer private-value-must-not-leak",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                "is_disabled": False,
            }
        ]


def _install_reviewed_plugin():
    async def _execute(content, **_kwargs):
        return {"output": content, "exit_code": 0}

    previous = get_tool("reviewed_plugin_tool")
    register_tool(
        ToolSpec(
            name="reviewed_plugin_tool",
            description="Inspect a reviewed local plugin artifact.",
            parameters={"type": "object", "properties": {"artifact_ref": {"type": "string"}}},
            execute=_execute,
            permission="user",
        )
    )
    return previous


def _restore_reviewed_plugin(previous):
    unregister_tool("reviewed_plugin_tool")
    if previous is not None:
        register_tool(previous)


def test_tools_get_returns_complete_deterministic_redacted_descriptors(monkeypatch):
    previous = _install_reviewed_plugin()
    monkeypatch.setenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", "true")
    monkeypatch.setattr("src.tool_utils.get_mcp_manager", lambda: _McpInventory())
    monkeypatch.setattr(
        "routes.model_routes._load_settings",
        lambda: {"disabled_tools": ["bash"]},
    )
    try:
        app = FastAPI()
        app.include_router(setup_model_routes(model_discovery=None))
        response = TestClient(app).get("/api/tools")
    finally:
        _restore_reviewed_plugin(previous)

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "odysseus.tool_catalog_projection.v1"
    assert payload["descriptor_schema"] == "odysseus.tool_descriptor.v2"
    assert payload["raw_schema_visible"] is False
    assert payload["tool_arguments_visible"] is False
    assert payload["tool_results_visible"] is False
    assert payload["secret_values_visible"] is False

    ids = [item["id"] for item in payload["descriptors"]]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids)) == payload["tool_count"]
    tools = {item["id"]: item for item in payload["descriptors"]}
    mutable_ids = [item["id"] for item in payload["tools"]]
    assert mutable_ids == sorted(mutable_ids)
    assert len(mutable_ids) == payload["mutable_tool_count"]

    bash = tools["bash"]
    assert bash["source"] == "builtin"
    assert bash["family"] == "code_filesystem"
    assert bash["permission"] == "admin"
    assert bash["runtime_availability"] == "disabled_by_settings"
    assert bash["description"]
    assert bash["raw_content_visible"] is False

    tail = tools["tail_serve_output"]
    assert tail["registration_disposition"] == "security_blocked"
    assert tail["enabled"] is False
    assert tail["permission"] == "admin"
    assert tail["settings_mutable"] is False
    assert "tail_serve_output" not in mutable_ids

    plugin = tools["reviewed_plugin_tool"]
    assert plugin["source"] == "plugin"
    assert plugin["permission"] == "owner"
    assert plugin["effect_class"] == "control"
    assert plugin["requires_confirmation"] is True

    mcp = tools["mcp__review__lookup"]
    assert mcp["source"] == "mcp"
    assert mcp["permission"] == "admin"
    assert mcp["settings_mutable"] is False
    assert mcp["requires_confirmation"] is True
    assert "mcp__review__lookup" not in mutable_ids
    assert "private-value-must-not-leak" not in repr(payload)


def test_tools_get_defaults_to_exact_legacy_projection(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", raising=False)
    monkeypatch.setattr("src.tool_utils.get_mcp_manager", lambda: _McpInventory())
    monkeypatch.setattr(
        "routes.model_routes._load_settings",
        lambda: {"disabled_tools": sorted(DEFAULT_DEFERRED_TOOLS | {"bash"})},
    )

    app = FastAPI()
    app.include_router(setup_model_routes(model_discovery=None))
    response = TestClient(app).get("/api/tools")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"tools"}
    assert "schema" not in payload
    tools = {item["id"]: item for item in payload["tools"]}
    assert tools["bash"] == {"id": "bash", "enabled": False}
    assert tools["send_email"]["enabled"] is False


def test_tools_mutation_is_admin_gated_validated_and_preserves_legacy_ids(monkeypatch):
    previous = _install_reviewed_plugin()
    settings = {"disabled_tools": ["bash", "legacy_unmapped_tool"]}
    saved = []
    admin_calls = []
    monkeypatch.setattr("src.tool_utils.get_mcp_manager", lambda: _McpInventory())
    monkeypatch.setattr("routes.model_routes._load_settings", lambda: settings)
    monkeypatch.setattr(
        "routes.model_routes._save_settings",
        lambda value: saved.append(dict(value)),
    )
    monkeypatch.setattr(
        "routes.model_routes.require_admin",
        lambda request: admin_calls.append(request.url.path),
    )
    try:
        app = FastAPI()
        app.include_router(setup_model_routes(model_discovery=None))
        client = TestClient(app)

        owner_read = client.get("/api/tools")
        assert owner_read.status_code == 200
        assert admin_calls == []

        runtime_read = client.get("/api/system/runtime-tools")
        assert runtime_read.status_code == 200
        assert admin_calls == ["/api/system/runtime-tools"]

        update = client.post(
            "/api/tools",
            json={"disabled": ["read_file", "reviewed_plugin_tool"]},
        )
        assert update.status_code == 200
        assert update.json() == {
            "ok": True,
            "disabled": ["read_file", "reviewed_plugin_tool"],
            "preserved_legacy_count": 1,
        }
        assert saved[-1]["disabled_tools"] == [
            "legacy_unmapped_tool",
            "read_file",
            "reviewed_plugin_tool",
        ]

        rejected = client.post(
            "/api/tools",
            json={"disabled": ["mcp__review__lookup", "not_a_registered_tool"]},
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"] == {
            "error": "unknown_or_immutable_tool_ids",
            "tool_ids": ["mcp__review__lookup", "not_a_registered_tool"],
            "settings_written": False,
        }
        assert len(saved) == 1
        assert admin_calls == [
            "/api/system/runtime-tools",
            "/api/tools",
            "/api/tools",
        ]
    finally:
        _restore_reviewed_plugin(previous)

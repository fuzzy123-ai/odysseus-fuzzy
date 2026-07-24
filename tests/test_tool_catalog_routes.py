import importlib

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.tool_registry import ToolSpec, get_tool, register_tool, unregister_tool


class _FakeMcpManager:
    def get_all_tools(self):
        return [
            {
                "server_id": "demo_mcp",
                "server_name": "Demo MCP",
                "name": "lookup",
                "qualified_name": "mcp__demo_mcp__lookup",
                "description": "Authorization: Bearer private-mcp-token",
                "input_schema": {
                    "type": "object",
                    "properties": {"private_query": {"type": "string"}},
                },
                "is_disabled": False,
            }
        ]


def _client(monkeypatch, *, settings, saved=None, require_admin=None):
    model_routes = importlib.import_module("routes.model_routes")
    monkeypatch.setattr(
        model_routes,
        "require_admin",
        require_admin or (lambda _request: None),
    )
    monkeypatch.setattr(model_routes, "_load_settings", lambda: dict(settings))
    monkeypatch.setattr(
        model_routes,
        "_save_settings",
        (lambda value: saved.append(dict(value))) if saved is not None else (lambda _value: None),
    )
    tool_utils = importlib.import_module("src.tool_utils")
    monkeypatch.setattr(tool_utils, "_mcp_manager", _FakeMcpManager())
    app = FastAPI()
    app.include_router(model_routes.setup_model_routes(model_discovery=None))
    return TestClient(app)


def test_get_tools_returns_complete_deterministic_redacted_descriptor_projection(monkeypatch):
    async def _execute(content, **_kwargs):
        return {"output": content, "exit_code": 0}

    previous = get_tool("tax6_public_plugin")
    register_tool(
        ToolSpec(
            name="tax6_public_plugin",
            description="Authorization: Bearer private-plugin-token",
            parameters={
                "type": "object",
                "properties": {"private_argument": {"type": "string"}},
            },
            execute=_execute,
            permission="public",
        )
    )
    try:
        monkeypatch.setenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", "true")
        client = _client(monkeypatch, settings={})
        response = client.get("/api/tools")
    finally:
        unregister_tool("tax6_public_plugin")
        if previous is not None:
            register_tool(previous)

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "odysseus.tool_catalog_projection.v2"
    assert payload["feature_flag"] == {
        "name": "tool-catalog-v2",
        "environment": "ODYSSEUS_TOOL_CATALOG_V2_ENABLED",
        "enabled": True,
        "selected_projection": "catalog_v2",
    }
    assert payload["operator_priority_defaults_applied"] is True
    assert payload["sources"] == ["builtin", "mcp", "plugin"]
    assert payload["tool_count"] >= 86
    assert payload["raw_schema_visible"] is False
    assert payload["secret_values_visible"] is False
    assert payload["raw_content_visible"] is False

    tools = payload["tools"]
    assert all(item["id"] == item["runtime_tool_id"] for item in tools)
    assert [(item["runtime_tool_id"], item["source"]) for item in tools] == sorted(
        (item["runtime_tool_id"], item["source"]) for item in tools
    )
    by_runtime = {(item["runtime_tool_id"], item["source"]): item for item in tools}

    bash = by_runtime[("bash", "builtin")]
    assert bash["contract"] == "odysseus.tool_descriptor.v2"
    assert bash["permission"] == "admin"
    assert bash["settings_toggle_allowed"] is True
    assert bash["schema_ref"] == "function:bash"

    tail = by_runtime[("tail_serve_output", "builtin")]
    assert tail["availability"] == "disabled"
    assert tail["enabled"] is False
    assert tail["settings_toggle_allowed"] is False
    assert tail["settings_mutable"] is False
    assert tail["policy_status"] == "catalog_unavailable"

    plugin = by_runtime[("tax6_public_plugin", "plugin")]
    assert plugin["permission"] == "admin"
    assert plugin["runtime_permission"] == "public"
    assert plugin["risk_level"] == "dangerous"
    assert plugin["requires_confirmation"] is True
    assert plugin["enabled"] is True
    assert plugin["settings_mutable"] is True
    assert plugin["projection_drift"] == [
        "runtime_permission_narrower_than_conservative_descriptor"
    ]

    mcp = by_runtime[("mcp__demo_mcp__lookup", "mcp")]
    assert mcp["source_id"] == "demo_mcp"
    assert mcp["permission"] == "admin"
    assert mcp["handler_ref"] == "mcp:demo_mcp"
    assert mcp["enabled"] is True
    assert mcp["settings_mutable"] is True

    encoded = response.text
    assert "private-plugin-token" not in encoded
    assert "private-mcp-token" not in encoded
    assert "private_argument" not in encoded
    assert "private_query" not in encoded
    assert '"parameters"' not in encoded


def test_get_tools_defaults_to_legacy_and_rolls_back_exactly_after_v2_read(monkeypatch):
    client = _client(monkeypatch, settings={})

    monkeypatch.delenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", raising=False)
    off_before = client.get("/api/tools")
    monkeypatch.setenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", "true")
    on = client.get("/api/tools")
    monkeypatch.setenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", "false")
    off_after = client.get("/api/tools")

    assert off_before.status_code == on.status_code == off_after.status_code == 200
    assert off_before.json() == off_after.json()
    legacy = off_before.json()
    assert legacy["schema"] == "odysseus.tool_catalog_projection.legacy.v1"
    assert legacy["feature_flag"]["enabled"] is False
    assert legacy["feature_flag"]["selected_projection"] == "legacy"
    assert all(set(row) <= {"id", "enabled", "settings_mutable"} for row in legacy["tools"])

    v2 = on.json()
    assert v2["schema"] == "odysseus.tool_catalog_projection.v2"
    assert v2["feature_flag"]["enabled"] is True
    assert v2["feature_flag"]["selected_projection"] == "catalog_v2"
    assert sum(row["source"] == "builtin" for row in v2["tools"]) == 86
    assert any(row["source"] == "mcp" for row in v2["tools"])
    assert all(row["id"] == row["runtime_tool_id"] for row in v2["tools"])


def test_legacy_get_does_not_enumerate_mcp_but_v2_still_does(monkeypatch):
    calls = []

    class _RecordingMcpManager:
        def get_all_tools(self):
            calls.append("get_all_tools")
            return _FakeMcpManager().get_all_tools()

    client = _client(monkeypatch, settings={})
    tool_utils = importlib.import_module("src.tool_utils")
    monkeypatch.setattr(tool_utils, "_mcp_manager", _RecordingMcpManager())

    monkeypatch.delenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", raising=False)
    legacy = client.get("/api/tools")
    assert legacy.status_code == 200
    assert calls == []

    monkeypatch.setenv("ODYSSEUS_TOOL_CATALOG_V2_ENABLED", "true")
    catalog_v2 = client.get("/api/tools")
    assert catalog_v2.status_code == 200
    assert calls == ["get_all_tools"]
    assert any(row["source"] == "mcp" for row in catalog_v2.json()["tools"])


def test_get_tools_is_admin_scoped(monkeypatch):
    def _deny(_request):
        raise HTTPException(status_code=403, detail="admin required")

    response = _client(monkeypatch, settings={}, require_admin=_deny).get("/api/tools")

    assert response.status_code == 403
    assert response.json()["detail"] == "admin required"


def test_post_tools_rejects_unknown_or_non_toggleable_ids_without_write(monkeypatch):
    saved = []
    client = _client(monkeypatch, settings={"disabled_tools": ["bash"]}, saved=saved)

    unknown = client.post("/api/tools", json={"disabled": ["unknown_private_tool"]})
    unavailable = client.post("/api/tools", json={"disabled": ["tail_serve_output"]})

    assert unknown.status_code == 400
    assert unknown.json()["detail"]["code"] == "unknown_or_non_toggleable_tool_ids"
    assert unavailable.status_code == 400
    assert unavailable.json()["detail"]["tool_ids"] == ["tail_serve_output"]
    assert saved == []


def test_post_tools_rejects_non_list_disabled_payload(monkeypatch):
    saved = []
    response = _client(
        monkeypatch,
        settings={"disabled_tools": []},
        saved=saved,
    ).post("/api/tools", json={"disabled": None})

    assert response.status_code == 422
    assert saved == []


def test_post_tools_sorts_dedupes_and_preserves_legacy_disabled_values(monkeypatch):
    saved = []
    client = _client(
        monkeypatch,
        settings={
            "disabled_tools": ["bash", "manage_assistant", "legacy_removed_tool"]
        },
        saved=saved,
    )

    response = client.post(
        "/api/tools",
        json={"disabled": ["web_search", "bash", "web_search"]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "disabled": [
            "bash",
            "legacy_removed_tool",
            "manage_assistant",
            "web_search",
        ],
        "requested_disabled": ["bash", "web_search"],
        "preserved_legacy_count": 2,
    }
    assert saved == [
        {
            "disabled_tools": [
                "bash",
                "legacy_removed_tool",
                "manage_assistant",
                "web_search",
            ]
        }
    ]


def test_post_tools_is_admin_scoped(monkeypatch):
    saved = []

    def _deny(_request):
        raise HTTPException(status_code=403, detail="admin required")

    response = _client(
        monkeypatch,
        settings={"disabled_tools": []},
        saved=saved,
        require_admin=_deny,
    ).post("/api/tools", json={"disabled": ["bash"]})

    assert response.status_code == 403
    assert saved == []

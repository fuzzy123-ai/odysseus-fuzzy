from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.model_routes import setup_model_routes
from src.tool_registry import ToolSpec, get_tool, register_tool, unregister_tool


def test_runtime_tool_status_route_returns_redacted_live_inventory(monkeypatch):
    async def _execute(content, **_kwargs):
        return {"output": content, "exit_code": 0}

    previous = get_tool("telegram_document_reply")
    register_tool(
        ToolSpec(
            name="telegram_document_reply",
            description="Send reviewed artifact.",
            parameters={"type": "object", "properties": {"artifact_ref": {"type": "string"}}},
            execute=_execute,
        )
    )
    try:
        monkeypatch.setattr("routes.model_routes.require_admin", lambda _request: None)
        monkeypatch.setattr("routes.model_routes._load_settings", lambda: {"disabled_tools": ["bash"]})
        app = FastAPI()
        app.include_router(setup_model_routes(model_discovery=None))
        response = TestClient(app).get("/api/system/runtime-tools")
    finally:
        unregister_tool("telegram_document_reply")
        if previous is not None:
            register_tool(previous)

    assert response.status_code == 200
    payload = response.json()
    tools = {item["tool_id"]: item for item in payload["tools"]}
    assert payload["schema"] == "odysseus.runtime_tool_status.v1"
    assert payload["raw_schema_visible"] is False
    assert payload["secret_values_visible"] is False
    assert tools["bash"]["availability"] == "disabled"
    assert tools["bash"]["gate_status"] == "disabled_by_settings"
    assert tools["telegram_document_reply"]["side_effect_class"] == "telegram_outbound"
    assert tools["telegram_document_reply"]["gate_status"] == "operator_or_live_gate_required"
    assert "artifact_ref" in tools["telegram_document_reply"]["parameter_names"]

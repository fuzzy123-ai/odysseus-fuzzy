import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.mcp_server.plugin import setup
from src.tool_registry import ToolSpec, unregister_tool, register_tool


@dataclass
class _PluginContext:
    app: FastAPI
    data_dir: Path
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("test.mcp_server"))
    require_admin: Callable[[Any], None] = lambda _request: None

    def add_router(self, router):
        self.app.include_router(router)


def _client(tmp_path):
    app = FastAPI()
    setup(_PluginContext(app=app, data_dir=tmp_path))
    return TestClient(app)


def _rpc(method, params=None, message_id=1):
    payload = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def test_mcp_server_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)

    response = client.post("/api/plugins/mcp", json=_rpc("initialize"))

    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["message"] == "MCP server is disabled"
    assert "token" not in response.text.lower()


def test_mcp_server_config_gate_enables_initialize(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)

    config_response = client.post("/api/plugins/mcp/config", json={"enabled": True})
    response = client.post("/api/plugins/mcp", json=_rpc("initialize", {
        "protocolVersion": "2025-06-18",
    }))

    assert config_response.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["serverInfo"]["name"] == "odysseus"
    assert payload["result"]["capabilities"]["tools"]["listChanged"] is False
    assert payload["result"]["capabilities"]["resources"]["subscribe"] is False
    assert payload["result"]["capabilities"]["prompts"]["listChanged"] is False


def test_mcp_tools_list_is_policy_filtered_and_includes_notification(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    unregister_tool("odysseus_notify_user")
    register_tool(ToolSpec(
        name="odysseus_notify_user",
        description="Request user notification.",
        parameters={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        execute=lambda content, **_kwargs: {"output": content, "exit_code": 0},
    ))
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    response = client.post("/api/plugins/mcp", json=_rpc("tools/list"))
    unregister_tool("odysseus_notify_user")

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert "odysseus_notify_user" in names
    assert "bash" not in names
    assert "python" not in names
    assert "write_file" not in names
    assert "app_api" not in names
    assert "odysseus_call" not in names


def test_mcp_tools_call_denies_hidden_high_risk_tool(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    response = client.post("/api/plugins/mcp", json=_rpc("tools/call", {
        "name": "bash",
        "arguments": {"cmd": "echo should-not-run"},
    }))

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert "high_risk_tool_hidden" in result["content"][0]["text"]


def test_mcp_resources_and_prompts_are_available(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    resources = client.post("/api/plugins/mcp", json=_rpc("resources/list")).json()["result"]["resources"]
    readiness = client.post("/api/plugins/mcp", json=_rpc("resources/read", {
        "uri": "odysseus://mcp/readiness",
    })).json()["result"]["contents"][0]
    prompts = client.post("/api/plugins/mcp", json=_rpc("prompts/list")).json()["result"]["prompts"]
    prompt = client.post("/api/plugins/mcp", json=_rpc("prompts/get", {
        "name": "odysseus_mcp_safe_notification",
        "arguments": {"message": "MCP smoke complete."},
    })).json()["result"]

    assert {resource["uri"] for resource in resources} >= {
        "odysseus://mcp/readiness",
        "odysseus://mcp/operator-runbook",
    }
    assert json.loads(readiness["text"])["enabled"] is True
    assert {item["name"] for item in prompts} >= {
        "odysseus_mcp_safe_notification",
        "odysseus_mcp_readiness_review",
    }
    assert "odysseus_notify_user" in prompt["messages"][0]["content"]["text"]


def test_mcp_notifications_return_accepted_without_body(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    response = client.post("/api/plugins/mcp", json={
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    })

    assert response.status_code == 202

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.mcp_server.plugin import setup
from src.tool_registry import ToolSpec, unregister_tool, register_tool
from src.user_notification_contract import build_user_notification_decision


RUNBOOK_HIGH_RISK_TOOLS = (
    "bash",
    "python",
    "write_file",
    "edit_file",
    "app_api",
    "api_call",
    "send_email",
    "manage_tokens",
    "manage_settings",
    "manage_mcp",
    "odysseus_call",
)


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


def test_mcp_tools_list_excludes_runbook_high_risk_tools_when_registered(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    for name in RUNBOOK_HIGH_RISK_TOOLS:
        unregister_tool(name)
        register_tool(ToolSpec(
            name=name,
            description=f"High-risk placeholder for {name}.",
            parameters={"type": "object", "properties": {}},
            execute=lambda content, **_kwargs: {"output": content, "exit_code": 0},
        ))
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    try:
        response = client.post("/api/plugins/mcp", json=_rpc("tools/list"))
    finally:
        for name in RUNBOOK_HIGH_RISK_TOOLS:
            unregister_tool(name)

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names.isdisjoint(RUNBOOK_HIGH_RISK_TOOLS)


def test_mcp_config_ignores_expose_all_and_keeps_generic_api_default_hidden(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)

    config_response = client.post("/api/plugins/mcp/config", json={
        "enabled": True,
        "expose_all": True,
        "allow_generic_api": False,
    })
    config = config_response.json()
    readiness = client.post("/api/plugins/mcp", json=_rpc("resources/read", {
        "uri": "odysseus://mcp/readiness",
    })).json()["result"]["contents"][0]
    readiness_payload = json.loads(readiness["text"])

    assert config_response.status_code == 200
    assert "expose_all" not in config
    assert readiness_payload["expose_all_supported"] is False
    assert readiness_payload["generic_api_enabled"] is False
    assert "token" not in readiness["text"].lower()
    assert "secret" not in readiness["text"].lower()


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


def test_mcp_notify_user_tool_call_requires_trusted_execution_owner(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    unregister_tool("odysseus_notify_user")

    def _notify_tool(content, **kwargs):
        payload = json.loads(content) if isinstance(content, str) else dict(content or {})
        payload.update(kwargs)
        return {
            "output": json.dumps(build_user_notification_decision(payload).as_public_dict()),
            "exit_code": 0,
        }

    register_tool(ToolSpec(
        name="odysseus_notify_user",
        description="Request user notification.",
        parameters={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        execute=_notify_tool,
    ))
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    response = client.post("/api/plugins/mcp", json=_rpc("tools/call", {
        "name": "odysseus_notify_user",
        "arguments": {
            "event": "mcp_smoke",
            "message": "MCP notification smoke completed.",
            "dry_run": True,
        },
    }))
    unregister_tool("odysseus_notify_user")

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "requires an admin user" in text
    assert "chat_id" not in text
    assert "token" not in text.lower().replace("token_value_visible", "")


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


def test_mcp_audit_trail_is_redacted_for_smoke_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    client.post("/api/plugins/mcp", json=_rpc("initialize"))
    client.post("/api/plugins/mcp", json=_rpc("tools/list"))
    client.post("/api/plugins/mcp", json=_rpc("resources/read", {
        "uri": "odysseus://mcp/readiness",
    }))

    audit_path = tmp_path / "mcp_audit.jsonl"
    assert audit_path.exists()
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert {entry["method"] for entry in entries} >= {"initialize", "tools/list", "resources/read"}
    for entry in entries:
        assert entry["token_value_visible"] is False
        assert entry["secret_value_visible"] is False
        assert "authorization" not in json.dumps(entry).lower()


def test_mcp_notifications_return_accepted_without_body(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    response = client.post("/api/plugins/mcp", json={
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    })

    assert response.status_code == 202

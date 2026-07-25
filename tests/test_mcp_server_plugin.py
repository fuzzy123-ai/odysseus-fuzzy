import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.mcp_server.plugin import McpServerState, setup
from src.tool_registry import ToolSpec, get_tool, unregister_tool, register_tool
from src.user_notification_contract import build_user_notification_decision
from src.mcp_server_tool_policy import PLANNING_READONLY_TOOLS


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


def _write_planning_roadmap(root: Path) -> None:
    path = root / "docs" / "plans" / "external-roadmap.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "harbor.planning.roadmap",
        "project_id": "external-preview",
        "roadmap_id": "external-map",
        "revision": 1,
        "created_at": "2026-07-10T06:00:00Z",
        "updated_at": "2026-07-10T06:00:00Z",
        "title": "External Planning Preview",
        "goal": "Expose only explicitly enabled read-only Planning tools.",
        "status": "planned",
        "source_refs": [],
        "slices": [{
            "id": "preview-one",
            "title": "Preview policy",
            "objective": "Keep Planning hidden until explicitly enabled.",
            "class": "repo_only",
            "status": "planned",
        }],
        "gates": [],
        "gate_refs": [],
        "dependency_refs": [],
        "verification": ["focused plugin tests"],
        "stop_rules": ["Stop before external live smoke."],
    }, indent=2), encoding="utf-8")


def _write_approved_definition_snapshot(root: Path) -> None:
    path = root / "data" / "planning" / "definitions" / "external-preview.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "harbor.planning.roadmap",
        "project_id": "external-preview",
        "roadmap_id": "external-map",
        "revision": 3,
        "created_at": "2026-07-15T06:00:00Z",
        "updated_at": "2026-07-15T06:00:00Z",
        "title": "External Planning Preview",
        "goal": "Expose only immutable Planning definitions.",
        "status": "approved",
        "slice_queue": [{
            "id": "definition-preview",
            "title": "Definition preview",
            "objective": "Keep Agent execution outside Planning.",
            "status": "running",
            "gate_ids": ["definition-go"],
        }],
        "gate_queue": [{
            "id": "definition-go",
            "class": "repo",
            "state": "blocked",
            "decision": "synthetic-runtime-decision",
            "decision_needed": "Confirm the definition-only external preview.",
            "safe_default": "Keep the preview read-only.",
            "blocks": ["definition-preview"],
        }],
        "source_refs": ["plugins/mcp_server/plugin.py"],
    }, indent=2), encoding="utf-8")


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
    assert "github_issue_find_duplicates" in names
    assert "manage_github_issues" not in names
    assert "github_issue_create_triaged" not in names


def test_mcp_tools_list_excludes_runbook_high_risk_tools_when_registered(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    previous = get_tool("odysseus_call")
    unregister_tool("odysseus_call")
    register_tool(ToolSpec(
        name="odysseus_call",
        description="High-risk generic API placeholder.",
        parameters={"type": "object", "properties": {}},
        execute=lambda content, **_kwargs: {"output": content, "exit_code": 0},
    ))
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    try:
        response = client.post("/api/plugins/mcp", json=_rpc("tools/list"))
    finally:
        unregister_tool("odysseus_call")
        if previous is not None:
            register_tool(previous)

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


def test_mcp_github_issue_duplicate_lookup_routes_to_readonly_action(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    calls = []

    async def _fake_manage_github_issues(content, owner=None):
        payload = json.loads(content)
        calls.append((payload, owner))
        return {
            "github_issue_duplicates": {"candidates": [], "blocks_auto_create": False},
            "exit_code": 0,
        }

    monkeypatch.setattr("src.tool_domains.github_issues.do_manage_github_issues", _fake_manage_github_issues)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    response = client.post("/api/plugins/mcp", json=_rpc("tools/call", {
        "name": "github_issue_find_duplicates",
        "arguments": {
            "repository": "fuzzy123-ai/odysseus-fuzzy",
            "title": "Duplicate candidate",
        },
    }))

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert calls == [(
        {
            "repository": "fuzzy123-ai/odysseus-fuzzy",
            "title": "Duplicate candidate",
            "action": "duplicate_search",
        },
        None,
    )]
    assert "github_issue_duplicates" in result["content"][0]["text"]


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


def test_external_planning_tools_are_hidden_until_explicit_read_capability(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)
    config = client.post("/api/plugins/mcp/config", json={"enabled": True}).json()
    tools = client.post("/api/plugins/mcp", json=_rpc("tools/list")).json()["result"]["tools"]
    names = {tool["name"] for tool in tools}

    assert config["allow_planning_reads"] is False
    assert names.isdisjoint(PLANNING_READONLY_TOOLS)

    denied = client.post("/api/plugins/mcp", json=_rpc("tools/call", {
        "name": "planning_list_roadmaps",
        "arguments": {"limit": 5},
    })).json()["result"]
    assert denied["isError"] is True
    assert "planning_read_hidden_by_default" in denied["content"][0]["text"]
    entries = [json.loads(line) for line in (tmp_path / "mcp_audit.jsonl").read_text(encoding="utf-8").splitlines()]
    blocked = next(entry for entry in entries if entry.get("tool_name") == "planning_list_roadmaps")
    assert blocked["status"] == "blocked"
    assert blocked["category"] == "planning_readonly"
    assert blocked["reason"] == "planning_read_hidden_by_default"
    assert blocked["required_gate"] == "PLANNING-MCP-READONLY-GO"
    assert blocked["client_id"] == "external-mcp"


def test_external_planning_read_capability_exposes_exact_definition_tool_inventory_and_readiness(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)
    config = client.post("/api/plugins/mcp/config", json={
        "enabled": True,
        "allow_planning_reads": True,
    }).json()
    tools = client.post("/api/plugins/mcp", json=_rpc("tools/list")).json()["result"]["tools"]
    names = {tool["name"] for tool in tools}
    readiness = client.post("/api/plugins/mcp", json=_rpc("resources/read", {
        "uri": "odysseus://mcp/readiness",
    })).json()["result"]["contents"][0]

    assert config["allow_planning_reads"] is True
    assert {name for name in names if name.startswith("planning_")} == set(PLANNING_READONLY_TOOLS)
    assert all(
        next(tool for tool in tools if tool["name"] == name)["inputSchema"]["additionalProperties"] is False
        for name in PLANNING_READONLY_TOOLS
    )
    assert json.loads(readiness["text"])["planning_reads_enabled"] is True
    assert names.isdisjoint({
        "planning_create_roadmap_draft",
        "planning_validate_roadmap",
        "planning_propose_patch",
        "planning_apply_patch",
        "planning_delete_roadmap",
        "planning_gate_status",
        "planning_mark_status",
    })


def test_external_deprecated_planning_tools_are_hidden_even_when_reads_are_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={
        "enabled": True,
        "allow_planning_reads": True,
    })

    for name in ("planning_gate_status", "planning_mark_status"):
        result = client.post("/api/plugins/mcp", json=_rpc("tools/call", {
            "name": name,
            "arguments": {"runtime_status": "running", "decision": "go"},
        })).json()["result"]
        assert result["isError"] is True
        assert "planning_deprecated_tool_hidden" in result["content"][0]["text"]

    entries = [
        json.loads(line)
        for line in (tmp_path / "mcp_audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    deprecated = [entry for entry in entries if entry.get("tool_name") in {
        "planning_gate_status",
        "planning_mark_status",
    }]
    assert len(deprecated) == 2
    assert all(entry["status"] == "blocked" for entry in deprecated)
    assert all(entry["category"] == "planning_deprecated" for entry in deprecated)
    assert all(entry["metadata"]["argument_values_stored"] == "" for entry in deprecated)
    assert all(entry["raw_arguments_visible"] is False for entry in deprecated)


def test_external_planning_dispatch_uses_pure_bounded_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    monkeypatch.setenv("ODYSSEUS_ROOT", str(tmp_path))
    _write_planning_roadmap(tmp_path)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={
        "enabled": True,
        "allow_planning_reads": True,
    })

    response = client.post("/api/plugins/mcp", json=_rpc("tools/call", {
        "name": "planning_list_roadmaps",
        "arguments": {"query": "External Planning", "limit": 5},
    }))
    result = response.json()["result"]
    payload = json.loads(result["content"][0]["text"])

    assert result["isError"] is False
    assert payload["schema"] == "odysseus.planning.roadmap_list.v1"
    assert payload["roadmaps"][0]["roadmap_id"] == "external-map"
    assert payload["writes_supported"] is False
    assert str(tmp_path) not in result["content"][0]["text"]
    entries = [json.loads(line) for line in (tmp_path / "mcp_audit.jsonl").read_text(encoding="utf-8").splitlines()]
    audit = next(entry for entry in entries if entry.get("tool_name") == "planning_list_roadmaps")
    assert audit["status"] == "ok"
    assert audit["category"] == "planning_readonly"
    assert audit["reason"] == "planning_read_explicitly_allowed"
    assert audit["required_gate"] == ""
    assert audit["client_id"] == "external-mcp"
    assert audit["argument_fields"] == ["limit", "query"]
    assert audit["argument_count"] == 2
    assert audit["argument_hash"].startswith("sha256:")


def test_external_definition_gate_and_handoff_dispatch_never_emit_runtime_state_or_launch(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    monkeypatch.setenv("ODYSSEUS_ROOT", str(tmp_path))
    monkeypatch.setenv("ODYSSEUS_SINGLE_USER_OWNER", "local-user")
    _write_planning_roadmap(tmp_path)
    _write_approved_definition_snapshot(tmp_path)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={
        "enabled": True,
        "allow_planning_reads": True,
    })

    gate_result = client.post("/api/plugins/mcp", json=_rpc("tools/call", {
        "name": "planning_read_gate_definitions",
        "arguments": {
            "project_id": "external-preview",
            "roadmap_id": "external-map",
            "revision_or_latest_approved": 3,
            "node_id": "definition-preview",
        },
    })).json()["result"]
    handoff_result = client.post("/api/plugins/mcp", json=_rpc("tools/call", {
        "name": "planning_create_agent_handoff",
        "arguments": {
            "project_id": "external-preview",
            "roadmap_id": "external-map",
            "revision_or_latest_approved": "latest_approved",
        },
    })).json()["result"]
    gate = json.loads(gate_result["content"][0]["text"])
    handoff = json.loads(handoff_result["content"][0]["text"])

    assert gate_result["isError"] is False
    assert gate["schema_id"] == "odysseus.planning.gate_definitions.v2"
    assert gate["revision"] == 3
    assert gate["gate_definitions"][0]["gate_id"] == "definition-go"
    gate_text = json.dumps(gate, sort_keys=True)
    assert "synthetic-runtime-decision" not in gate_text
    assert '"state"' not in gate_text
    assert '"decision"' not in gate_text
    assert '"status"' not in gate_text

    assert handoff_result["isError"] is False
    assert handoff["schema_id"] == "odysseus.agent.plan_handoff.v1"
    assert handoff["revision"] == 3
    assert handoff["composer_text"] == (
        f"/abc run roadmap:external-map@3 hash:{handoff['content_hash']}"
    )
    assert handoff["launch_authorized"] is False
    assert handoff["read_only"] is True
    assert set(handoff).isdisjoint(
        {"skill", "model", "run_id", "workflow_id", "command", "auto_submit"}
    )


def test_external_planning_errors_and_audit_never_record_raw_arguments(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    monkeypatch.setenv("ODYSSEUS_ROOT", str(tmp_path))
    _write_planning_roadmap(tmp_path)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={
        "enabled": True,
        "allow_planning_reads": True,
    })
    rejected = "../../private-roadmap.json"

    response = client.post("/api/plugins/mcp", json=_rpc("tools/call", {
        "name": "planning_read_roadmap",
        "arguments": {"source_id_or_path": rejected},
    }))
    result = response.json()["result"]
    payload = json.loads(result["content"][0]["text"])
    entries = [
        json.loads(line)
        for line in (tmp_path / "mcp_audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert result["isError"] is True
    assert payload["status"] == "error"
    assert payload["rejected_value_visible"] is False
    assert rejected not in result["content"][0]["text"]
    assert all(rejected not in json.dumps(entry) for entry in entries)
    assert all("arguments" not in entry for entry in entries)
    assert all(entry["token_value_visible"] is False for entry in entries)
    detailed = next(entry for entry in entries if entry.get("tool_name") == "planning_read_roadmap")
    assert detailed["status"] == "error"
    assert detailed["reason"] == "planning_read_explicitly_allowed"
    assert detailed["argument_fields"] == ["source_id_or_path"]
    assert detailed["argument_count"] == 1
    assert detailed["argument_hash"].startswith("sha256:")


def test_mcp_audit_failure_is_bounded_and_does_not_raise_or_change_policy(tmp_path, caplog):
    not_a_directory = tmp_path / "audit-parent-file"
    not_a_directory.write_text("occupied", encoding="utf-8")
    state = McpServerState(not_a_directory, logging.getLogger("test.mcp.audit.failure"))

    state.audit(
        method="tools/call",
        status="blocked",
        tool="planning_read_roadmap",
        client_id="profile:codex-planning",
        reason="planning_read_hidden_by_default",
        arguments={"source_id_or_path": "C:\\private\\roadmap.json"},
    )

    assert "MCP audit write failed" in caplog.text
    assert "C:\\private" not in caplog.text
    assert "roadmap.json" not in caplog.text

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from core.auth import AuthManager
import plugins.mcp_server.plugin as mcp_plugin
from plugins.mcp_server.plugin import setup
from src.mcp_authenticated_dispatch import (
    AppOwnedPlanningRuntime,
    MCP_BATCH_MAX,
    MCP_BODY_MAX_BYTES,
    MCP_CONTEXT_SCOPE,
    McpAuthenticatedIdentity,
)
from src.planning_revision_store import PlanningRevisionStore
from mcp_servers.planning_server import PLANNING_TOOL_NAMES


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
    app.state.planning_mcp_runtime = AppOwnedPlanningRuntime(
        repo_root=str(Path(__file__).resolve().parents[1]),
        legacy_store=PlanningRevisionStore([]),
        durable_store=None,
        issuer=None,
    )
    setup(_PluginContext(app=app, data_dir=tmp_path))
    return TestClient(app)


def _authorize(monkeypatch):
    monkeypatch.setattr(
        mcp_plugin,
        "authenticate_mcp_request",
        lambda _request: McpAuthenticatedIdentity("token-id", "admin", "subject-id", 1, 1),
    )


def _real_admin_client(tmp_path):
    """Build the plugin with a real SID-bound AuthManager for admin routes."""
    manager = AuthManager(str(tmp_path / "auth.json"))
    assert manager.create_user("admin", "password", is_admin=True)
    cookie = manager.create_session("admin", "password")
    assert cookie is not None

    app = FastAPI()
    app.state.auth_manager = manager
    app.state.planning_mcp_runtime = AppOwnedPlanningRuntime(
        repo_root=str(Path(__file__).resolve().parents[1]),
        legacy_store=PlanningRevisionStore([]),
        durable_store=None,
        issuer=None,
    )

    @app.middleware("http")
    async def bind_current_user(request, call_next):
        request.state.current_user = manager.get_username_for_token(
            request.cookies.get("odysseus_session")
        )
        return await call_next(request)

    setup(_PluginContext(app=app, data_dir=tmp_path, require_admin=None))
    return TestClient(app), manager, cookie


def _rpc(method, params=None, message_id=1):
    payload = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def test_mcp_http_requires_authenticated_exact_scope(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    response = _client(tmp_path).post("/api/plugins/mcp", json=_rpc("initialize"))
    assert response.status_code == 401
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32001, "message": "Unauthorized"},
    }
    assert "token" not in response.text.lower()


def test_admin_http_uses_real_sid_owner_fence_and_rejects_revocation(tmp_path):
    client, manager, cookie = _real_admin_client(tmp_path)

    accepted = client.get("/api/plugins/mcp/info", cookies={"odysseus_session": cookie})
    assert accepted.status_code == 200
    assert accepted.json()["plugin"] == "mcp_server"
    assert manager.strict_session_identity(cookie) is not None

    assert manager.revoke_token(cookie) is None
    revoked = client.get("/api/plugins/mcp/info", cookies={"odysseus_session": cookie})
    assert revoked.status_code == 403
    assert revoked.json() == {"detail": "Admin only"}

    fresh = manager.create_session("admin", "password")
    assert fresh is not None
    assert manager.strict_session_identity(fresh) is not None
    assert manager.revoke_user_sessions("admin") == 1
    stale = client.get("/api/plugins/mcp/info", cookies={"odysseus_session": fresh})
    assert stale.status_code == 403
    assert stale.json() == {"detail": "Admin only"}


def test_mcp_server_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    _authorize(monkeypatch)
    client = _client(tmp_path)
    response = client.post("/api/plugins/mcp", json=_rpc("initialize"))
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "MCP server is disabled"


def test_initialize_is_available_when_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    _authorize(monkeypatch)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})
    response = client.post("/api/plugins/mcp", json=_rpc("initialize", {
        "protocolVersion": "2025-06-18",
    }))
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["serverInfo"]["name"] == "odysseus"
    assert result["capabilities"] == {"tools": {"listChanged": False}}


def test_planning_tool_list_is_exact_and_requires_both_gates(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    _authorize(monkeypatch)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})

    hidden = client.post("/api/plugins/mcp", json=_rpc("tools/list"))
    assert hidden.status_code == 403
    assert hidden.json()["error"]["message"] == "Planning read is unavailable"

    client.post("/api/plugins/mcp/config", json={"enabled": True, "allow_planning_reads": True})
    visible = client.post("/api/plugins/mcp", json=_rpc("tools/list"))
    assert visible.status_code == 200
    names = [tool["name"] for tool in visible.json()["result"]["tools"]]
    assert tuple(names) == PLANNING_TOOL_NAMES
    assert not {"odysseus_call", "bash", "python", "app_api", "github_issue_find_duplicates"}.intersection(names)


def test_http_rechecks_planning_gate_between_batch_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    _authorize(monkeypatch)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True, "allow_planning_reads": True})
    gate = {"allowed": True, "calls": 0}
    def dynamic_config(_state):
        return {"enabled": True, "allow_planning_reads": gate["allowed"]}
    monkeypatch.setattr(mcp_plugin.McpServerState, "load_config", dynamic_config)
    class Runtime:
        def dispatch(self, _name, _arguments, _identity):
            gate["calls"] += 1
            gate["allowed"] = False
            return {
                "schema": "odysseus.mcp.tool_result.v1",
                "tool": "planning_list_roadmaps",
                "origin": {"kind": "repository_projection", "source_of_truth": False},
                "payload": {
                    "schema": "odysseus.planning.roadmap_list.v1", "read_only": True,
                    "writes_supported": False, "query": "", "filters": {"kind": "", "status": ""},
                    "summary": {"total_roadmaps": 0, "total_matches": 0, "returned": 0, "clipped": False},
                    "roadmaps": [],
                },
            }
    client.app.state.planning_mcp_runtime = Runtime()
    batch = [
        _rpc("tools/call", {"name": "planning_list_roadmaps", "arguments": {}}, 1),
        _rpc("tools/call", {"name": "planning_list_roadmaps", "arguments": {}}, 2),
    ]
    response = client.post("/api/plugins/mcp", json=batch)
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Planning read is unavailable"
    assert gate["calls"] == 1


def test_generic_provider_and_legacy_rpc_methods_are_not_dispatched(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    _authorize(monkeypatch)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True, "allow_planning_reads": True})
    for method in ("resources/read", "prompts/list", "tools/call", "api_call"):
        params = {"name": "bash", "arguments": {}} if method == "tools/call" else {}
        response = client.post("/api/plugins/mcp", json=_rpc(method, params))
        assert response.status_code == 200
        expected = "Invalid params" if method == "tools/call" else "Method not found"
        assert response.json()["error"]["message"] == expected


def test_notifications_have_no_response_body(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    _authorize(monkeypatch)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True, "allow_planning_reads": True})
    response = client.post("/api/plugins/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert response.status_code == 202
    assert response.content == b""


def test_http_parse_body_and_batch_budget_errors_are_bounded(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    _authorize(monkeypatch)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})
    marker = "secret-provider-path=C:/private/provider-token"

    malformed = client.post(
        "/api/plugins/mcp",
        content=(b'{"jsonrpc":"2.0","secret":"' + marker.encode("utf-8")),
    )
    assert malformed.status_code == 400
    assert malformed.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Parse error"},
    }
    assert marker not in malformed.text

    oversized = client.post(
        "/api/plugins/mcp",
        content=b"x" * (MCP_BODY_MAX_BYTES + 1),
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"] == {
        "code": -32012,
        "message": "Request budget exceeded",
    }
    assert marker not in oversized.text

    over_batch = client.post(
        "/api/plugins/mcp",
        json=[_rpc("ping", {"marker": marker}, index) for index in range(MCP_BATCH_MAX + 1)],
    )
    assert over_batch.status_code == 413
    assert over_batch.json()["error"] == {
        "code": -32012,
        "message": "Request budget exceeded",
    }
    assert marker not in over_batch.text


def test_audit_records_fixed_method_and_no_arguments(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    _authorize(monkeypatch)
    client = _client(tmp_path)
    client.post("/api/plugins/mcp/config", json={"enabled": True})
    client.post("/api/plugins/mcp", json=_rpc("ping"))
    lines = (tmp_path / "mcp_audit.jsonl").read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[-1])
    assert event["method"] == "authenticated_planning_rpc"
    assert event["metadata"] == {"argument_values_stored": "false"}
    assert event["tool_name"] == ""
    assert event["argument_fields"] == []
    assert event["argument_count"] == 0
    assert event["raw_arguments_visible"] is False
    assert "arguments" not in event
    assert "argument_values" not in event


def test_legacy_compatibility_errors_and_prompt_are_fixed_and_bounded(tmp_path, monkeypatch):
    state = mcp_plugin.McpServerState(tmp_path, logging.getLogger("test.mcp_server"))
    config = dict(mcp_plugin.DEFAULT_CONFIG, enabled=True)

    async def fail_call_tool(*_args, **_kwargs):
        raise RuntimeError("secret-provider-path=C:/private/provider-token")

    monkeypatch.setattr(state, "call_tool", fail_call_tool)
    error = asyncio.run(mcp_plugin._handle_jsonrpc(
        state,
        _rpc("tools/call", {"name": "safe", "arguments": {}}),
        config,
    ))
    assert error["error"]["message"] == "Internal error"
    assert "secret-provider-path" not in json.dumps(error)

    unknown = asyncio.run(mcp_plugin._handle_jsonrpc(
        state,
        _rpc("resources/read", {"uri": "odysseus://secret-provider-path"}),
        config,
    ))
    assert unknown["error"]["message"] == "Unknown resource"
    assert "secret-provider-path" not in json.dumps(unknown)

    prompt = mcp_plugin._prompt_payload(
        "odysseus_mcp_safe_notification",
        {"message": "secret-provider-path=C:/private/provider-token"},
    )
    assert prompt is not None
    assert "secret-provider-path" not in json.dumps(prompt)
    assert "configured redacted message" in json.dumps(prompt)


def test_audit_arguments_store_only_shape_and_hash(tmp_path):
    state = mcp_plugin.McpServerState(tmp_path, logging.getLogger("test.mcp_server"))
    state.audit(
        method="tools/call",
        status="ok",
        tool="planning_list_roadmaps",
        arguments={"password": "secret-value", "path": "C:/private/roadmap.json"},
    )
    event = json.loads((tmp_path / "mcp_audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    serialized = json.dumps(event)
    assert event["raw_arguments_visible"] is False
    assert event["secret_value_visible"] is False
    assert event["argument_fields"] == ["password", "path"]
    assert event["argument_count"] == 2
    assert event["argument_hash"].startswith("sha256:")
    assert "secret-value" not in serialized
    assert "roadmap.json" not in serialized


def test_context_config_snapshot_requires_exact_persisted_gates_and_pinned_owner(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("ODYSSEUS_MCP_SERVER_ENABLED", raising=False)
    app = FastAPI()
    state = mcp_plugin.McpServerState(tmp_path, logging.getLogger("test.context-config"))
    state.bind_context_app(app)
    assert state.context_snapshot()[:2] == (False, False)
    saved = state.save_config({"enabled": 1, "allow_context_reads": True})
    assert saved["enabled"] is False
    assert saved["allow_context_reads"] is True
    assert state.context_snapshot()[:2] == (False, True)
    monkeypatch.setenv("ODYSSEUS_MCP_SERVER_ENABLED", "true")
    assert state.load_config()["enabled"] is True
    assert state.context_snapshot()[:2] == (False, True)
    assert state.save_config({"allow_context_reads": True})["enabled"] is False
    assert state.context_snapshot()[:2] == (False, True)
    state.config_path.write_text(json.dumps({
        "enabled": True,
        "allow_context_reads": True,
    }), encoding="utf-8")
    enabled = state.context_snapshot()
    assert enabled[:2] == (True, True)
    state.config_path.write_text(json.dumps({
        "enabled": True,
        "allow_context_reads": "true",
    }), encoding="utf-8")
    malformed = state.context_snapshot()
    assert malformed[:2] == (True, False)
    assert malformed[2] != enabled[2]
    with monkeypatch.context() as changed:
        changed.setattr(state, "data_dir", tmp_path / "substituted")
        with pytest.raises(ValueError, match="configuration owner"):
            state.context_snapshot()
    with monkeypatch.context() as changed:
        changed.setattr(state, "_config_path_pin", Path(str(state.config_path)))
        with pytest.raises(ValueError, match="configuration owner"):
            state.context_snapshot()
    with monkeypatch.context() as changed:
        changed.setattr(
            mcp_plugin.McpServerState,
            "_load_persisted_context_unlocked",
            lambda _self: (True, True),
        )
        with pytest.raises(ValueError, match="configuration owner"):
            state.context_snapshot()
    with monkeypatch.context() as changed:
        changed.setattr(
            mcp_plugin.McpServerState,
            "config_path",
            property(lambda _self: tmp_path / "substituted.json"),
        )
        with pytest.raises(ValueError, match="configuration owner"):
            state.context_snapshot()


def test_context_scope_branches_before_generic_body_parse_and_returns_original_response(tmp_path):
    class Composition:
        def __init__(self):
            self.bound = None
            self.body_was_cached = None
            self.response = Response(
                content=b"",
                status_code=418,
                headers={"Cache-Control": "no-store"},
            )

        def bind_plugin_context(self, app, owner):
            self.bound = (app, owner)

        async def handle_mcp_request(self, request):
            self.body_was_cached = hasattr(request, "_body")
            return self.response

    app = FastAPI()
    composition = Composition()
    app.state.mcp_context_app = composition

    @app.middleware("http")
    async def context_identity(request, call_next):
        request.state.api_token_scopes = (MCP_CONTEXT_SCOPE,)
        return await call_next(request)

    setup(_PluginContext(app=app, data_dir=tmp_path))
    async def substituted_handler(_request):
        raise AssertionError("post-setup handler substitution ran")
    composition.handle_mcp_request = substituted_handler
    with TestClient(app) as client:
        response = client.post(
            "/api/plugins/mcp",
            content=b"not-json-and-must-not-be-read",
            headers={"Authorization": "Bearer malformed-is-owned-by-context"},
        )
    assert composition.bound is not None
    assert composition.body_was_cached is False
    assert response.status_code == 418
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"

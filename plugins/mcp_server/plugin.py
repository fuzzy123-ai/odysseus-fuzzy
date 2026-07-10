"""Odysseus MCP Server plugin.

This plugin exposes a deliberately small Model Context Protocol surface for
trusted external clients. It is disabled by default and relies on Odysseus auth;
tool exposure is filtered through ``src.mcp_server_tool_policy``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from src.mcp_server_tool_policy import (
    McpToolPolicyOptions,
    PLANNING_READONLY_TOOLS,
    classify_mcp_tool,
    filter_mcp_tools,
)

try:
    from core.middleware import require_admin as _core_require_admin
except Exception:  # pragma: no cover - file-loader tests may not have app context
    _core_require_admin = None


PLUGIN = {
    "name": "MCP Server",
    "version": "0.1.0",
    "author": "Odysseus",
    "description": "Policy-gated MCP server for trusted external clients.",
    "category": "Integrations",
    "manifest_version": "1.0",
    "permission": "admin",
    "kind": "ui",
    "capabilities": ["admin_route", "local_api"],
    "compatibility": {"min_odysseus": "1.0.0"},
    "lifecycle": "loadable",
    "ui": {"open": "/api/plugins/mcp/app", "label": "Setup"},
}

SERVER_NAME = "odysseus"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_CONFIG = {
    "enabled": False,
    "allow_owner_scoped_writes": False,
    "allow_private_reads": False,
    "allow_filesystem_reads": False,
    "allow_generic_api": False,
    "allow_planning_reads": False,
}


def _bool_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _jsonrpc_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _schema_name(schema: Mapping[str, Any]) -> str:
    function = schema.get("function")
    if isinstance(function, Mapping):
        return str(function.get("name") or "")
    return str(schema.get("name") or "")


def _mcp_tool_from_openai_schema(schema: Mapping[str, Any]) -> dict[str, Any] | None:
    function = schema.get("function")
    if not isinstance(function, Mapping):
        return None
    name = str(function.get("name") or "")
    if not name:
        return None
    return {
        "name": name,
        "description": str(function.get("description") or ""),
        "inputSchema": function.get("parameters") or {"type": "object", "properties": {}},
    }


def _github_issue_find_duplicates_tool() -> dict[str, Any]:
    return {
        "name": "github_issue_find_duplicates",
        "description": (
            "Read-only duplicate preview over already-synced GitHubIssueRecord rows. "
            "Does not sync GitHub, create issues, set fields, or accept tokens."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "description": "Repository in owner/repo form."},
                "title": {"type": "string", "description": "Draft issue title."},
                "body": {"type": "string", "description": "Optional draft issue body."},
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional draft labels for local duplicate matching.",
                },
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                "include_closed": {"type": "boolean", "default": True},
            },
            "required": ["repository", "title"],
        },
    }


class McpServerState:
    def __init__(self, data_dir: str | Path, logger: Any) -> None:
        self.data_dir = Path(data_dir)
        self.logger = logger

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "mcp_audit.jsonl"

    def load_config(self) -> dict[str, Any]:
        config = dict(DEFAULT_CONFIG)
        try:
            loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config.update({key: loaded.get(key, value) for key, value in DEFAULT_CONFIG.items()})
        except Exception:
            pass
        if _bool_env("ODYSSEUS_MCP_SERVER_ENABLED"):
            config["enabled"] = True
        return config

    def save_config(self, incoming: Mapping[str, Any]) -> dict[str, Any]:
        config = self.load_config()
        for key in DEFAULT_CONFIG:
            if key in incoming:
                config[key] = bool(incoming[key])
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return config

    def policy_options(self, config: Mapping[str, Any]) -> McpToolPolicyOptions:
        return McpToolPolicyOptions(
            allow_owner_scoped_writes=bool(config.get("allow_owner_scoped_writes")),
            allow_private_reads=bool(config.get("allow_private_reads")),
            allow_filesystem_reads=bool(config.get("allow_filesystem_reads")),
            allow_generic_api=bool(config.get("allow_generic_api")),
            allow_planning_reads=bool(config.get("allow_planning_reads")),
            expose_all=False,
        )

    def audit(
        self,
        *,
        method: str,
        status: str,
        tool: str = "",
        client_id: str = "",
        reason: str = "",
        duration_ms: int = 0,
        arguments: Mapping[str, Any] | None = None,
        options: McpToolPolicyOptions | None = None,
    ) -> None:
        try:
            from src.mcp_audit_events import McpAuditEvent

            entry = McpAuditEvent.create(
                method=method,
                status=status,
                tool_name=tool,
                client_id=client_id,
                reason=reason,
                duration_ms=duration_ms,
                arguments=arguments or {},
                options=options,
                metadata={"argument_values_stored": False},
            ).to_dict()
            # Preserve the legacy key while standardizing on tool_name.
            entry["tool"] = entry["tool_name"]
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.logger.warning("MCP audit write failed: %s", type(exc).__name__)

    def tool_schemas(self, options: McpToolPolicyOptions) -> list[dict[str, Any]]:
        schemas: list[Mapping[str, Any]] = []
        try:
            import src.agent_tools  # noqa: F401 - initialize tool schema facade first
            from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
            schemas.extend(FUNCTION_TOOL_SCHEMAS)
        except Exception as exc:
            self.logger.warning("MCP tool schemas unavailable: %s", exc)
        try:
            from src.tool_registry import get_function_schemas
            schemas.extend(get_function_schemas())
        except Exception as exc:
            self.logger.warning("MCP dynamic tool schemas unavailable: %s", exc)
        filtered = filter_mcp_tools(schemas, options)
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        for schema in filtered:
            tool = _mcp_tool_from_openai_schema(schema)
            if not tool or tool["name"] in seen:
                continue
            seen.add(tool["name"])
            tools.append(tool)
        github_issue_tool = _github_issue_find_duplicates_tool()
        if (
            classify_mcp_tool(github_issue_tool["name"], options).exposed
            and github_issue_tool["name"] not in seen
        ):
            tools.append(github_issue_tool)
            seen.add(github_issue_tool["name"])
        if options.allow_planning_reads:
            try:
                from mcp_servers.planning_server import build_planning_tool_contracts

                for contract in build_planning_tool_contracts():
                    name = str(contract.get("name") or "")
                    if not name or name in seen or not classify_mcp_tool(name, options).exposed:
                        continue
                    tools.append({
                        "name": name,
                        "description": str(contract.get("description") or ""),
                        "inputSchema": contract.get("inputSchema") or {"type": "object", "properties": {}},
                    })
                    seen.add(name)
            except Exception as exc:
                self.logger.warning("Planning MCP tool contracts unavailable: %s", type(exc).__name__)
        return tools

    async def call_tool(self, name: str, arguments: Mapping[str, Any], options: McpToolPolicyOptions) -> tuple[str, bool]:
        decision = classify_mcp_tool(name, options)
        if not decision.exposed:
            return (f"Tool {name!r} is not exposed: {decision.reason}", True)
        if name == "github_issue_find_duplicates":
            return await self._call_github_issue_find_duplicates(arguments)
        if name in PLANNING_READONLY_TOOLS:
            return self._call_planning_tool(name, arguments)
        try:
            import src.agent_tools  # noqa: F401 - initialize tool subsystem first
            from src.tool_execution import execute_tool_block
            from src.tool_schemas import function_call_to_tool_block

            block = function_call_to_tool_block(name, json.dumps(dict(arguments or {})))
            if block is None:
                return (f"Unknown tool: {name!r}", True)
            _desc, result = await execute_tool_block(block)
        except Exception as exc:
            return (f"Tool execution failed: {exc}", True)
        if not isinstance(result, dict):
            return (str(result), False)
        text = result.get("output")
        is_error = bool(result.get("error")) or result.get("exit_code") not in (0, None)
        if text is None:
            text = result.get("error")
        if text is None:
            text = json.dumps(result, ensure_ascii=False, default=str)
        return (str(text)[:60000], is_error)

    def _call_planning_tool(self, name: str, arguments: Mapping[str, Any]) -> tuple[str, bool]:
        try:
            from mcp_servers.planning_server import call_planning_tool_contract

            payload = call_planning_tool_contract(name, arguments)
        except Exception as exc:
            payload = {
                "schema": "odysseus.planning.mcp_error.v1",
                "tool": name,
                "status": "error",
                "code": "planning_dispatch_failed",
                "message": f"Planning dispatch failed: {type(exc).__name__}",
                "read_only": True,
                "writes_performed": False,
                "rejected_value_visible": False,
                "absolute_paths_visible": False,
            }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        if len(text.encode("utf-8")) > 60000:
            payload = {
                "schema": "odysseus.planning.mcp_error.v1",
                "tool": name,
                "status": "error",
                "code": "planning_response_budget_exceeded",
                "message": "Planning response exceeded the external MCP budget",
                "read_only": True,
                "writes_performed": False,
                "rejected_value_visible": False,
                "absolute_paths_visible": False,
            }
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return text, payload.get("status") == "error"

    async def _call_github_issue_find_duplicates(self, arguments: Mapping[str, Any]) -> tuple[str, bool]:
        try:
            from src.tool_domains.github_issues import do_manage_github_issues

            payload = dict(arguments or {})
            payload["action"] = "duplicate_search"
            result = await do_manage_github_issues(json.dumps(payload))
        except Exception as exc:
            return (f"GitHub issue duplicate lookup failed: {type(exc).__name__}", True)
        is_error = bool(result.get("error")) or result.get("exit_code") not in (0, None)
        text = result.get("error")
        if text is None:
            text = json.dumps(result, ensure_ascii=False, default=str)
        return (str(text)[:60000], is_error)


def _static_resources() -> list[dict[str, Any]]:
    return [
        {
            "uri": "odysseus://mcp/readiness",
            "name": "Odysseus MCP readiness",
            "description": "Current MCP server readiness and safety posture.",
            "mimeType": "application/json",
        },
        {
            "uri": "odysseus://mcp/operator-runbook",
            "name": "Odysseus MCP operator runbook",
            "description": "Operator guidance for safe MCP server activation.",
            "mimeType": "text/markdown",
        },
    ]


def _static_prompts() -> list[dict[str, Any]]:
    return [
        {
            "name": "odysseus_mcp_safe_notification",
            "description": "Ask Odysseus to notify the user through the server-side notification bridge.",
            "arguments": [{"name": "message", "description": "Redacted user-facing completion message.", "required": True}],
        },
        {
            "name": "odysseus_mcp_readiness_review",
            "description": "Review MCP readiness before enabling remote clients.",
            "arguments": [],
        },
    ]


def _prompt_payload(name: str, args: Mapping[str, Any]) -> dict[str, Any] | None:
    if name == "odysseus_mcp_safe_notification":
        message = str(args.get("message") or "Task complete.")
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            "Call `odysseus_notify_user` with dry_run=true first. "
                            f"Use this redacted message: {message}"
                        ),
                    },
                }
            ]
        }
    if name == "odysseus_mcp_readiness_review":
        return {
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": "Read `odysseus://mcp/readiness` and summarize remaining production gates.",
                    },
                }
            ]
        }
    return None


async def _handle_jsonrpc(
    state: McpServerState,
    msg: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    client_id: str = "external-mcp",
) -> dict[str, Any] | None:
    started = time.monotonic()
    method = str(msg.get("method") or "")
    message_id = msg.get("id")
    params = msg.get("params") or {}
    is_notification = "id" not in msg
    options = state.policy_options(config)

    if not isinstance(msg, Mapping) or msg.get("jsonrpc") != "2.0":
        return _jsonrpc_error(None, -32600, "Invalid Request")

    try:
        if method == "initialize":
            requested = str((params or {}).get("protocolVersion") or "")
            protocol = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            result = {
                "protocolVersion": protocol,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": SERVER_NAME, "version": PLUGIN["version"]},
                "instructions": "Odysseus MCP is policy-gated. Dangerous tools and generic API calls are hidden by default.",
            }
        elif method.startswith("notifications/"):
            return None
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": state.tool_schemas(options)}
        elif method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not name:
                raise ValueError("tools/call requires name")
            decision = classify_mcp_tool(name, options)
            text, is_error = await state.call_tool(name, arguments, options)
            result = {"content": [{"type": "text", "text": text}], "isError": is_error}
            state.audit(
                method=method,
                tool=name,
                client_id=client_id,
                status="blocked" if not decision.exposed else ("error" if is_error else "ok"),
                reason=decision.reason,
                duration_ms=int((time.monotonic() - started) * 1000),
                arguments=arguments if isinstance(arguments, Mapping) else {},
                options=options,
            )
        elif method == "resources/list":
            result = {"resources": _static_resources()}
        elif method == "resources/read":
            uri = str(params.get("uri") or "")
            if uri == "odysseus://mcp/readiness":
                readiness = {
                    "enabled": bool(config.get("enabled")),
                    "generic_api_enabled": bool(config.get("allow_generic_api")),
                    "owner_scoped_writes_enabled": bool(config.get("allow_owner_scoped_writes")),
                    "private_reads_enabled": bool(config.get("allow_private_reads")),
                    "filesystem_reads_enabled": bool(config.get("allow_filesystem_reads")),
                    "planning_reads_enabled": bool(config.get("allow_planning_reads")),
                    "expose_all_supported": False,
                }
                text = json.dumps(readiness, indent=2)
            elif uri == "odysseus://mcp/operator-runbook":
                text = (
                    "# Odysseus MCP Operator Notes\n\n"
                    "- Keep the server disabled until a named MCP client is ready.\n"
                    "- Use a dedicated Odysseus API token per client.\n"
                    "- Do not enable generic API access for MVP.\n"
                    "- Review the redacted audit log after live smoke tests.\n"
                )
            else:
                return _jsonrpc_error(message_id, -32602, f"Unknown resource: {uri}")
            result = {"contents": [{"uri": uri, "mimeType": "application/json" if uri.endswith("readiness") else "text/markdown", "text": text}]}
        elif method == "prompts/list":
            result = {"prompts": _static_prompts()}
        elif method == "prompts/get":
            payload = _prompt_payload(str(params.get("name") or ""), params.get("arguments") or {})
            if payload is None:
                return _jsonrpc_error(message_id, -32602, "Unknown prompt")
            result = payload
        else:
            if is_notification:
                return None
            return _jsonrpc_error(message_id, -32601, f"Method not found: {method}")
    except Exception as exc:
        state.audit(method=method, status="error", client_id=client_id, reason=type(exc).__name__, options=options)
        if is_notification:
            return None
        return _jsonrpc_error(message_id, -32603, f"Internal error: {exc}")

    state.audit(
        method=method,
        status="ok",
        client_id=client_id,
        duration_ms=int((time.monotonic() - started) * 1000),
        options=options,
    )
    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _app_html(config: Mapping[str, Any], nonce: str) -> str:
    enabled = "enabled" if config.get("enabled") else "disabled"
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Odysseus MCP Server</title>
<link rel="stylesheet" href="/static/plugin-theme.css">
<script src="/static/js/plugin-theme.js"></script>
</head><body>
<header class="od-header"><a class="brand" href="/">Odysseus</a><span class="od-title">MCP Server</span></header>
<main class="od-wrap">
  <h1>MCP Server</h1>
  <div class="od-card">
    <p>Status: <strong>{enabled}</strong></p>
    <p class="muted">Endpoint: <code>POST /api/plugins/mcp</code></p>
    <p class="muted">Use a dedicated Odysseus API token. Dangerous tools and generic API access are hidden by default.</p>
  </div>
  <script nonce="{nonce}">console.log("Odysseus MCP Server setup page loaded");</script>
</main>
</body></html>"""


def setup(ctx):
    router = APIRouter(prefix="/api/plugins/mcp", tags=["plugin:mcp_server"])
    state = McpServerState(ctx.data_dir, ctx.logger)

    def _require_admin(request: Request) -> None:
        gate = getattr(ctx, "require_admin", None) or _core_require_admin
        if callable(gate):
            gate(request)

    def _client_ref(request: Request) -> str:
        current_user = getattr(request.state, "current_user", None)
        if isinstance(current_user, str) and current_user.strip():
            return f"profile:{current_user.strip()}"
        return "external-mcp"

    @router.post("")
    @router.post("/")
    async def mcp_endpoint(request: Request):
        _require_admin(request)
        config = state.load_config()
        if not config.get("enabled"):
            return JSONResponse(_jsonrpc_error(None, -32000, "MCP server is disabled"), status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(_jsonrpc_error(None, -32700, "Parse error"), status_code=400)
        batch = isinstance(payload, list)
        messages = payload if batch else [payload]
        client_id = _client_ref(request)
        responses = [
            response
            for msg in messages
            if (response := await _handle_jsonrpc(state, msg, config, client_id=client_id)) is not None
        ]
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses if batch else responses[0])

    @router.get("")
    async def mcp_get(request: Request):
        _require_admin(request)
        return JSONResponse({"error": "MCP endpoint is POST-only in this MVP."}, status_code=405)

    @router.get("/info")
    async def info(request: Request):
        _require_admin(request)
        config = state.load_config()
        options = state.policy_options(config)
        return {
            "plugin": "mcp_server",
            "enabled": bool(config.get("enabled")),
            "endpoint": "/api/plugins/mcp",
            "transport": "streamable_http_post",
            "tools_exposed": len(state.tool_schemas(options)) if config.get("enabled") else 0,
            "expose_all_supported": False,
            "token_value_visible": False,
        }

    @router.get("/config")
    async def get_config(request: Request):
        _require_admin(request)
        return state.load_config()

    @router.post("/config")
    async def set_config(request: Request):
        _require_admin(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        return state.save_config(body if isinstance(body, dict) else {})

    @router.get("/app")
    async def app_page(request: Request):
        _require_admin(request)
        return HTMLResponse(_app_html(state.load_config(), getattr(request.state, "csp_nonce", "")))

    ctx.add_router(router)
    ctx.logger.info("MCP Server plugin ready at /api/plugins/mcp (runtime gate disabled by default)")

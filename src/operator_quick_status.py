"""Compact redacted operator status summaries for chat surfaces."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping


OPERATOR_QUICK_STATUS_SCHEMA = "odysseus.operator_quick_status.v1"

DIAGNOSTIC_ENDPOINTS = (
    {"id": "services", "method": "GET", "path": "/api/diagnostics/services"},
    {"id": "ai_activity", "method": "GET", "path": "/api/diagnostics/ai-activity"},
    {"id": "memory_provenance", "method": "GET", "path": "/api/diagnostics/memory-provenance"},
    {"id": "tool_capabilities", "method": "GET", "path": "/api/diagnostics/tool-capabilities"},
    {"id": "mcp_servers", "method": "GET", "path": "/api/mcp/servers"},
    {"id": "mcp_tools", "method": "GET", "path": "/api/mcp/tools"},
    {"id": "system_health", "method": "GET", "path": "/api/plugins/system_health_checker/health"},
    {"id": "version", "method": "GET", "path": "/api/version"},
    {"id": "health", "method": "GET", "path": "/api/health"},
    {"id": "ready", "method": "GET", "path": "/api/ready"},
)


def build_operator_quick_status(
    *,
    mcp_manager: Any = None,
    mcp_servers: Iterable[Any] = (),
    system_health: Mapping[str, Any] | None = None,
    app_version: str = "",
) -> dict[str, Any]:
    mcp = _mcp_summary(mcp_manager, mcp_servers)
    health = _system_health_summary(system_health or {})
    diagnostics = _diagnostics_summary()
    app = {
        "status": "ok",
        "version": _safe_token(app_version),
        "health_endpoint": "/api/health",
        "ready_endpoint": "/api/ready",
        "version_endpoint": "/api/version",
        "remote_lookup_performed": False,
    }
    overall = _overall_status(app, mcp, health)
    return {
        "schema": OPERATOR_QUICK_STATUS_SCHEMA,
        "status": overall,
        "app": app,
        "mcp": mcp,
        "system_health": health,
        "diagnostics": diagnostics,
        "raw_content_visible": False,
        "path_values_visible": False,
        "command_values_visible": False,
        "env_values_visible": False,
        "url_values_visible": False,
        "token_value_visible": False,
        "chat_id_value_visible": False,
        "live_probe_performed": False,
        "live_mutation_performed": False,
    }


def _mcp_summary(mcp_manager: Any, servers: Iterable[Any]) -> dict[str, Any]:
    server_list = list(servers or ())
    statuses = _safe_call(lambda: mcp_manager.get_all_statuses()) if mcp_manager is not None else {}
    tools = _safe_call(lambda: mcp_manager.get_all_tools()) if mcp_manager is not None else []
    statuses = statuses if isinstance(statuses, Mapping) else {}
    tools = tools if isinstance(tools, list) else []

    configured = len(server_list)
    enabled = sum(1 for srv in server_list if bool(getattr(srv, "is_enabled", False)))
    disabled_tools = sum(_disabled_tool_count(getattr(srv, "disabled_tools", None)) for srv in server_list)
    connected = sum(1 for status in statuses.values() if _status_token(status) in {"connected", "ready", "running"})
    needs_auth = sum(1 for status in statuses.values() if _status_token(status) in {"needs_auth", "needs_oauth"})
    errors = sum(1 for status in statuses.values() if _status_token(status) in {"error", "failed", "disconnected"})

    if enabled and connected == enabled and errors == 0 and needs_auth == 0:
        state = "ok"
    elif enabled == 0 and configured == 0:
        state = "not_configured"
    else:
        state = "warn"

    return {
        "status": state,
        "configured_server_count": configured,
        "enabled_server_count": enabled,
        "connected_server_count": connected,
        "needs_auth_count": needs_auth,
        "error_server_count": errors,
        "tool_count": len(tools),
        "disabled_tool_count": disabled_tools + sum(1 for tool in tools if bool(tool.get("is_disabled"))),
        "server_names_visible": False,
        "tool_names_visible": False,
        "tool_descriptions_visible": False,
        "command_values_visible": False,
        "env_values_visible": False,
        "url_values_visible": False,
    }


def _system_health_summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    collectors = snapshot.get("collectors") if isinstance(snapshot.get("collectors"), list) else []
    alerts = snapshot.get("alerts") if isinstance(snapshot.get("alerts"), Mapping) else {}
    state = _safe_token(snapshot.get("state") or "unknown")
    return {
        "status": state or "unknown",
        "collector_count": len(collectors),
        "alert_count": _safe_count(alerts.get("active_count") if isinstance(alerts, Mapping) else 0),
        "host_agent_connected": state == "ok",
        "snapshot_available": bool(snapshot),
        "details_visible": False,
        "host_paths_visible": False,
    }


def _diagnostics_summary() -> dict[str, Any]:
    return {
        "status": "available",
        "endpoint_count": len(DIAGNOSTIC_ENDPOINTS),
        "endpoints": [dict(item) for item in DIAGNOSTIC_ENDPOINTS],
        "logs_included": False,
        "raw_prompts_visible": False,
        "private_content_visible": False,
    }


def _overall_status(app: Mapping[str, Any], mcp: Mapping[str, Any], health: Mapping[str, Any]) -> str:
    if app.get("status") not in {"ok", "healthy"}:
        return "warn"
    if mcp.get("status") == "warn":
        return "warn"
    if health.get("status") in {"warn", "critical", "unknown"}:
        return "warn"
    return "ok"


def _disabled_tool_count(raw: Any) -> int:
    if not raw:
        return 0
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def _status_token(status: Any) -> str:
    if isinstance(status, Mapping):
        status = status.get("status")
    return _safe_token(status)


def _safe_call(fn):
    try:
        return fn()
    except Exception:
        return None


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch in "._:-")[:80]

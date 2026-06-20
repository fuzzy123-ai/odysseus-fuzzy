"""Policy model for exposing Odysseus tools through an MCP server.

This module is intentionally pure and side-effect free. It does not start an MCP
endpoint; it only decides which existing Odysseus tools are safe to advertise to
external MCP clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


ALWAYS_DENIED_TOOLS = frozenset({
    "api_call",
    "app_api",
    "archive_email",
    "ask_teacher",
    "bash",
    "bulk_email",
    "adopt_served_model",
    "cancel_download",
    "chat_with_model",
    "create_session",
    "delete_email",
    "delegate",
    "download_model",
    "edit_file",
    "manage_contact",
    "manage_endpoints",
    "manage_memory",
    "manage_mcp",
    "manage_settings",
    "manage_skills",
    "manage_subagents",
    "manage_tokens",
    "manage_webhooks",
    "mark_email_read",
    "pipeline",
    "python",
    "reply_to_email",
    "send_email",
    "send_to_session",
    "serve_model",
    "serve_preset",
    "spawn_subagent",
    "stop_served_model",
    "trigger_research",
    "ui_control",
    "write_file",
})

GENERIC_API_TOOLS = frozenset({
    "odysseus_call",
    "odysseus_list_endpoints",
})

DEFAULT_ALLOWED_TOOLS = frozenset({
    "list_cached_models",
    "list_cookbook_servers",
    "list_downloads",
    "list_models",
    "list_serve_presets",
    "list_served_models",
    "list_sessions",
    "odysseus_notify_user",
    "search_hf_models",
    "web_fetch",
    "web_search",
})

OWNER_SCOPED_WRITE_TOOLS = frozenset({
    "create_document",
    "edit_document",
    "manage_calendar",
    "manage_documents",
    "manage_session",
    "manage_notes",
    "manage_tasks",
    "suggest_document",
    "update_document",
})

READONLY_PRIVATE_TOOLS = frozenset({
    "list_email_accounts",
    "list_emails",
    "read_email",
    "resolve_contact",
    "search_chats",
})

FILESYSTEM_READ_TOOLS = frozenset({
    "get_workspace",
    "glob",
    "grep",
    "ls",
    "read_file",
})


@dataclass(frozen=True)
class McpToolPolicyOptions:
    allow_owner_scoped_writes: bool = False
    allow_private_reads: bool = False
    allow_filesystem_reads: bool = False
    allow_generic_api: bool = False
    expose_all: bool = False


@dataclass(frozen=True)
class McpToolDecision:
    tool_name: str
    exposed: bool
    category: str
    reason: str


def _tool_name(tool: str | Mapping[str, Any]) -> str:
    if isinstance(tool, str):
        return tool
    if not isinstance(tool, Mapping):
        return ""
    function = tool.get("function")
    if isinstance(function, Mapping):
        return str(function.get("name") or "")
    return str(tool.get("name") or "")


def classify_mcp_tool(
    tool: str | Mapping[str, Any],
    options: McpToolPolicyOptions | None = None,
) -> McpToolDecision:
    options = options or McpToolPolicyOptions()
    name = _tool_name(tool)
    if not name:
        return McpToolDecision("", False, "invalid", "missing_tool_name")

    if name in ALWAYS_DENIED_TOOLS:
        return McpToolDecision(name, False, "high_risk", "high_risk_tool_hidden")
    if name in GENERIC_API_TOOLS:
        return McpToolDecision(
            name,
            bool(options.allow_generic_api),
            "generic_api",
            "generic_api_explicitly_allowed" if options.allow_generic_api else "generic_api_hidden_by_default",
        )
    if name in DEFAULT_ALLOWED_TOOLS:
        return McpToolDecision(name, True, "default_allowed", "mvp_allowed_tool")
    if name in OWNER_SCOPED_WRITE_TOOLS:
        return McpToolDecision(
            name,
            bool(options.allow_owner_scoped_writes),
            "owner_scoped_write",
            "owner_scoped_write_explicitly_allowed"
            if options.allow_owner_scoped_writes
            else "owner_scoped_write_hidden_by_default",
        )
    if name in READONLY_PRIVATE_TOOLS:
        return McpToolDecision(
            name,
            bool(options.allow_private_reads),
            "private_read",
            "private_read_explicitly_allowed" if options.allow_private_reads else "private_read_hidden_by_default",
        )
    if name in FILESYSTEM_READ_TOOLS:
        return McpToolDecision(
            name,
            bool(options.allow_filesystem_reads),
            "filesystem_read",
            "filesystem_read_explicitly_allowed"
            if options.allow_filesystem_reads
            else "filesystem_read_hidden_by_default",
        )
    if options.expose_all:
        return McpToolDecision(name, False, "unclassified", "expose_all_not_supported_in_mvp")
    return McpToolDecision(name, False, "unclassified", "unclassified_tool_hidden_by_default")


def exposed_mcp_tool_names(
    tools: Iterable[str | Mapping[str, Any]],
    options: McpToolPolicyOptions | None = None,
) -> tuple[str, ...]:
    decisions = (classify_mcp_tool(tool, options) for tool in tools)
    return tuple(sorted(decision.tool_name for decision in decisions if decision.exposed))


def filter_mcp_tools(
    tools: Iterable[Mapping[str, Any]],
    options: McpToolPolicyOptions | None = None,
) -> list[Mapping[str, Any]]:
    return [tool for tool in tools if classify_mcp_tool(tool, options).exposed]

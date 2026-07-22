"""Server-side tool safety policy."""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from typing import Any, Optional, Set

from src.builtin_tool_catalog import builtin_spec
from src.tool_catalog import (
    ToolEffectClass,
    ToolPermission,
    ToolRiskLevel,
)

logger = logging.getLogger(__name__)

PUBLIC_MCP_SERVER_ALLOWLIST = {"vault"}

PUBLIC_VAULT_MCP_READONLY_TOOLS = {
    "obsidian_tree",
    "obsidian_read_note",
    "obsidian_search_notes",
    "obsidian_search_semantic",
    "obsidian_list_tags",
    "obsidian_graph",
    "obsidian_suggest_links",
    "obsidian_recent_notes",
    "obsidian_history",
    "obsidian_vault_stats",
    "obsidian_spark_analyze",
    "obsidian_spark_plan",
    "obsidian_memory_tree_status",
    "obsidian_memory_status",
    "obsidian_memory_tree_analyze",
    "obsidian_knowledge_audit",
    "obsidian_quarantine_list",
    "obsidian_raptor_status",
    "obsidian_raptor_graph_view",
}


RUNTIME_ADMIN_TOOLS = frozenset(
    {
        "app_api",
        "bash",
        "download_model",
        "manage_embeddings",
        "manage_endpoints",
        "manage_github_issues",
        "manage_mcp",
        "manage_personal_docs",
        "manage_plugins",
        "manage_presets",
        "manage_repos",
        "manage_settings",
        "manage_tokens",
        "manage_webhooks",
        "python",
        "recent_changes",
        "serve_model",
        "serve_preset",
        "stop_served_model",
        "cancel_download",
        "tail_serve_output",
    }
)

_READ_ACTIONS = {
    "manage_plugins": frozenset(
        {"list", "registry", "registries", "list_registries", "status"}
    ),
    "manage_tokens": frozenset({"list"}),
    "manage_settings": frozenset(
        {
            "list",
            "get",
            "features",
            "secret_handoffs",
            "explain",
            "list_tools",
        }
    ),
    "manage_repos": frozenset(
        {
            "list",
            "get",
            "status",
            "log",
            "diff_stat",
            "changed_paths",
            "remotes",
            "commit_plan",
            "push_plan",
            "forge_plan",
            "changes",
            "change_history",
        }
    ),
}

_ACTION_EFFECT_OVERRIDES = {
    ("manage_settings", "delete"): ToolEffectClass.DESTRUCTIVE,
    ("manage_settings", "reset"): ToolEffectClass.DESTRUCTIVE,
    ("manage_tokens", "delete"): ToolEffectClass.DESTRUCTIVE,
    ("manage_plugins", "uninstall"): ToolEffectClass.DESTRUCTIVE,
    ("manage_plugins", "remove_registry"): ToolEffectClass.DESTRUCTIVE,
    ("manage_plugins", "delete_registry"): ToolEffectClass.DESTRUCTIVE,
    ("manage_repos", "forget"): ToolEffectClass.DESTRUCTIVE,
    ("manage_repos", "commit"): ToolEffectClass.LOCAL_WRITE,
    ("manage_repos", "register"): ToolEffectClass.LOCAL_WRITE,
    ("manage_repos", "update_policy"): ToolEffectClass.LOCAL_WRITE,
    ("manage_repos", "push"): ToolEffectClass.EXTERNAL_WRITE,
    ("manage_repos", "forge_metadata"): ToolEffectClass.EXTERNAL_WRITE,
}

_PERMISSION_RANK = {
    ToolPermission.OWNER: 0,
    ToolPermission.ADMIN: 1,
}


@dataclass(frozen=True, slots=True)
class RuntimeToolSecurityProfile:
    tool_id: str
    permission: ToolPermission
    risk_level: ToolRiskLevel
    effect_class: ToolEffectClass
    requires_confirmation: bool
    action: str
    source: str


def _json_action(content: object) -> str:
    try:
        payload = json.loads(str(content or "{}"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("action") or "").strip().lower()


def runtime_tool_security_profile(
    tool_id: object,
    content: object = "",
    *,
    dynamic_permission: Optional[str] = None,
) -> RuntimeToolSecurityProfile:
    """Return the conservative runtime projection for a built-in or dynamic tool."""

    normalized = str(tool_id or "").strip()
    spec = builtin_spec(normalized)
    action = _json_action(content)
    if spec is None:
        explicit_owner_policy = str(dynamic_permission or "").lower() in {
            "owner",
            "user",
        }
        return RuntimeToolSecurityProfile(
            tool_id=normalized,
            permission=(
                ToolPermission.OWNER
                if explicit_owner_policy
                else ToolPermission.ADMIN
            ),
            risk_level=ToolRiskLevel.ELEVATED,
            effect_class=ToolEffectClass.CONTROL,
            requires_confirmation=True,
            action=action,
            source="dynamic_conservative",
        )

    permission = (
        ToolPermission.ADMIN
        if normalized in RUNTIME_ADMIN_TOOLS
        else spec.permission
    )
    effect = spec.effect_class
    if action and action in _READ_ACTIONS.get(normalized, frozenset()):
        effect = ToolEffectClass.READ
    elif action:
        effect = _ACTION_EFFECT_OVERRIDES.get((normalized, action), effect)
        if effect == ToolEffectClass.READ:
            effect = ToolEffectClass.CONTROL
    risk = (
        ToolRiskLevel.SAFE
        if effect == ToolEffectClass.READ
        else ToolRiskLevel.ELEVATED
        if effect == ToolEffectClass.CONTROL
        else ToolRiskLevel.DANGEROUS
    )
    return RuntimeToolSecurityProfile(
        tool_id=normalized,
        permission=permission,
        risk_level=risk,
        effect_class=effect,
        requires_confirmation=effect != ToolEffectClass.READ,
        action=action,
        source="builtin_catalog_runtime_overlay",
    )


def runtime_tool_requires_admin(tool_id: object) -> bool:
    return runtime_tool_security_profile(tool_id).permission == ToolPermission.ADMIN


def validate_catalog_runtime_security_projection() -> tuple[str, ...]:
    """Return content-free drift errors when runtime is weaker than the catalog."""

    errors = []
    from src.builtin_tool_catalog import BUILTIN_TOOL_SPECS

    for spec in BUILTIN_TOOL_SPECS:
        profile = runtime_tool_security_profile(spec.tool_id)
        if _PERMISSION_RANK[profile.permission] < _PERMISSION_RANK[spec.permission]:
            errors.append(f"{spec.tool_id}:runtime_permission_weaker")
        if spec.effect_class != ToolEffectClass.READ and not profile.requires_confirmation:
            errors.append(f"{spec.tool_id}:runtime_confirmation_weaker")
    return tuple(sorted(errors))


# Tools regular/public users must not execute directly. These either expose
# server/runtime access, sensitive user data, external messaging, persistent
# state changes, or generic loopback/integration surfaces.
NON_ADMIN_BLOCKED_TOOLS = {
    "bash",
    "python",
    "manage_bg_jobs",
    "spawn_subagent",
    "manage_subagents",
    "read_file",
    "write_file",
    "edit_file",
    "publish_artifact",
    "verify_pygame_headless",
    "grep",
    "glob",
    "ls",
    "get_workspace",
    "search_chats",
    "manage_memory",
    "manage_skills",
    "manage_tasks",
    "manage_endpoints",
    "manage_mcp",
    "manage_webhooks",
    "manage_tokens",
    "manage_documents",
    "manage_repos",
    "manage_github_issues",
    "manage_nextcloud_transfer",
    "manage_settings",
    "api_call",
    "app_api",
    "send_email",
    "reply_to_email",
    "list_emails",
    "read_email",
    "resolve_contact",
    "manage_contact",
    "manage_calendar",
    "vault_search",
    "vault_get",
    "vault_unlock",
    "download_model",
    "serve_model",
    "serve_preset",
    "stop_served_model",
    "tail_serve_output",
    "cancel_download",
    "adopt_served_model",
}


# Plan mode: the agent may investigate but must not mutate anything. Only these
# read-only/inspection tools stay enabled; everything else (writes, sends,
# manage_*, model serving, MCP, etc.) is blocked. Allowlist rather than blocklist
# so any newly added tool defaults to BLOCKED in plan mode — fail safe.
#
# bash/python are deliberately NOT here: the shell can mutate (write files, hit
# the network) and can't be constrained to read-only at the tool layer, so plan
# mode blocks it outright rather than relying on a prompt to keep it well-behaved.
# Code/file discovery is covered by the dedicated read-only tools below
# (read_file, grep, glob, ls) instead of freestyle shell.
PLAN_MODE_READONLY_TOOLS = {
    "read_file",
    "grep",
    "glob",
    "ls",
    "get_workspace",
    "web_search",
    "web_fetch",
    "search_chats",
    "list_models",
    "list_sessions",
    "list_emails",
    "read_email",
    "list_served_models",
    "list_downloads",
    "list_cached_models",
    "search_hf_models",
    "list_serve_presets",
    "list_cookbook_servers",
    "resolve_contact",
    "chat_with_model",
    "ask_teacher",
}

ORCHESTRATOR_MODE_ALLOWED_TOOLS = {
    "delegate",
    "spawn_subagent",
    "manage_subagents",
    "obsidian_read_note",
    "obsidian_write_note",
    "obsidian_search_notes",
    "obsidian_graph",
    "read_file",
    "grep",
    "glob",
    "ls",
    "web_search",
    "web_fetch",
    "search_chats",
    "list_models",
    "sensitive_local_analysis",
}


# The agent's tool gate is a DENYLIST: execute_tool_block blocks any tool whose
# name is in `disabled_tools`. Plan mode's policy is the opposite — an allowlist
# (PLAN_MODE_READONLY_TOOLS). To apply an allowlist through a denylist, plan mode
# returns the inverse: every known tool name minus the allowlist.
#
# Known tool names come from FUNCTION_TOOL_SCHEMAS, but that source is imperfect:
# some tools are only XML-invocable (e.g. manage_notes, generate_image) and never
# appear there, and the import can fail outright. Either gap would drop a mutating
# tool from the subtraction and silently leave it enabled. This set is the static
# backstop for both: union it in so known mutators are always subtracted, and so a
# failed import still blocks them (fail closed, never open). Only mutators belong
# here — read-only tools are covered by the allowlist. Keep in sync when adding
# new mutating tools.
_PLAN_MODE_KNOWN_MUTATORS = {
    "write_file", "create_document", "edit_document", "update_document",
    "publish_artifact", "verify_pygame_headless",
    "suggest_document", "manage_documents", "create_session", "manage_session",
    "send_to_session", "pipeline", "manage_memory", "manage_skills",
    "manage_tasks", "manage_notes", "manage_todos", "manage_endpoints", "manage_mcp",
    "spawn_subagent", "manage_subagents",
    "manage_webhooks", "manage_tokens", "manage_settings", "manage_contact",
    "manage_github_issues", "manage_nextcloud_transfer",
    "manage_calendar", "api_call", "app_api", "ui_control",
    "send_email", "reply_to_email", "bulk_email", "delete_email",
    "archive_email", "mark_email_read", "download_model", "serve_model",
    "stop_served_model", "cancel_download", "adopt_served_model", "serve_preset",
    "generate_image", "edit_image", "trigger_research", "manage_research",
    # Shell is never read-only-safe; block it explicitly so it stays out of plan
    # mode even if the schema list fails to load.
    "bash", "python",
    # Controls shell processes (kill); plan mode can't run bash anyway.
    "manage_bg_jobs",
}


def plan_mode_disabled_tools() -> Set[str]:
    """Tool names to add to the denylist in plan mode.

    Plan mode allows only PLAN_MODE_READONLY_TOOLS. The gate is a denylist, so
    return the inverse: every known tool name minus the allowlist. Known names
    come from the function-tool schemas, backstopped by _PLAN_MODE_KNOWN_MUTATORS
    (see above) so XML-only tools and a failed schema import can't leave a mutator
    enabled. MCP tools are handled separately — the loop drops the MCP manager
    entirely in plan mode."""
    try:
        # agent_tools / tool_parsing / tool_schemas form a mutually-circular
        # cluster that only resolves cleanly when entered via agent_tools.
        # Import it first so the lazy schema import works even from a cold
        # import (e.g. tests) — not just after the app has wired everything up.
        import src.agent_tools  # noqa: F401
        from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

        all_names = {
            (t.get("function") or {}).get("name")
            for t in FUNCTION_TOOL_SCHEMAS
        }
        all_names.discard(None)
        try:
            from src.tool_registry import tool_names

            all_names.update(tool_names())
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Unable to load tool schemas for plan-mode gating: %s", exc)
        all_names = set()
    # Subtract the allowlist from all known tool names (schema-derived plus the
    # static mutator backstop). Fail closed: if the schema import failed above,
    # the backstop alone still blocks known mutators.
    return (all_names | _PLAN_MODE_KNOWN_MUTATORS) - PLAN_MODE_READONLY_TOOLS


def orchestrator_mode_disabled_tools() -> Set[str]:
    """Tool names to add to the denylist in orchestrator mode.

    Orchestrator mode is an allowlist: the top-level agent may inspect context,
    update the Obsidian state doc, and delegate work, but it may not directly
    mutate host files, run shell commands, call broad app APIs, or use arbitrary
    plugin/MCP tools. Unknown future tools default to blocked once known by the
    schema/registry discovery path.
    """
    try:
        import src.agent_tools  # noqa: F401
        from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

        all_names = {
            (t.get("function") or {}).get("name")
            for t in FUNCTION_TOOL_SCHEMAS
        }
        all_names.discard(None)
        try:
            from src.tool_registry import tool_names

            all_names.update(tool_names())
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Unable to load tool schemas for orchestrator-mode gating: %s", exc)
        all_names = set()
    return (all_names | _PLAN_MODE_KNOWN_MUTATORS) - ORCHESTRATOR_MODE_ALLOWED_TOOLS


def is_public_blocked_tool(tool_name: Optional[str]) -> bool:
    """Return True when a non-admin/public user must not execute this tool.

    This is a security gate, so it fails CLOSED: a malformed non-string tool
    name can't be matched against the blocklist or the ``mcp__`` namespace, so
    it is treated as blocked rather than silently allowed through. ``None`` /
    empty string means there is no tool to gate.
    """
    if tool_name is None or tool_name == "":
        return False
    if not isinstance(tool_name, str):
        return True
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__", 2)
        server_id = parts[1] if len(parts) == 3 else ""
        mcp_tool = parts[2] if len(parts) == 3 else ""
        if server_id == "vault":
            return mcp_tool not in PUBLIC_VAULT_MCP_READONLY_TOOLS
        return server_id not in PUBLIC_MCP_SERVER_ALLOWLIST
    return tool_name in NON_ADMIN_BLOCKED_TOOLS


def owner_is_admin_or_single_user(owner: Optional[str]) -> bool:
    """Return True for admins, or in intentional single-user mode.

    Single-user mode means the operator explicitly disabled auth
    (``AUTH_ENABLED=false``) — the local/self-host default where the owner has
    full access to their own box.

    The pre-setup window (auth ENABLED but no admin created yet) is treated as
    NON-admin: returning True there would hand server-execution tools
    (``bash``/``python``) to any caller before setup completes. The auth
    middleware already 401s ``/api/`` requests pre-setup, so this is
    defense-in-depth for callers that bypass it (e.g. trusted loopback).
    """
    try:
        from src.auth_helpers import _auth_disabled

        if _auth_disabled():
            return True

        from core.auth import AuthManager

        auth = AuthManager()
        if not auth.is_configured:
            return False
        return bool(owner and auth.is_admin(owner))
    except Exception as exc:
        logger.warning("Unable to evaluate owner admin status: %s", exc)
        return False


def blocked_tools_for_owner(owner: Optional[str]) -> Set[str]:
    """Tools to hide/disable for this owner under public-user policy."""
    if owner_is_admin_or_single_user(owner):
        return set()
    blocked = set(NON_ADMIN_BLOCKED_TOOLS)
    try:
        from src.tool_registry import list_tools

        blocked.update(tool.name for tool in list_tools() if tool.permission == "admin")
    except Exception:
        pass
    return blocked

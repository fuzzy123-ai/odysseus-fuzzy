"""Per-turn tool policy composition for agent execution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Set, Tuple

from src.builtin_tool_catalog import resolve_operator_priority_disabled


GUIDE_ONLY_DIRECTIVE = (
    "## GUIDE-ONLY MODE - TOOL POLICY\n"
    "The latest user turn explicitly forbids tool use. Do not call tools, do not "
    "run shell commands, and do not inspect local files or the environment. "
    "Respond in normal text by guiding the user or asking them to paste the "
    "output they will produce locally."
)

CLARIFICATION_OPEN_DIRECTIVE = (
    "## CLARIFICATION OPEN - TOOL POLICY\n"
    "A required clarification is still open. Do not create or update a plan, do "
    "not run mutating tools, and do not proceed with implementation. You may "
    "inspect bounded context with read-only tools and may call `ask_user` to "
    "collect or refine material missing information. When enough information is "
    "available, summarize what is understood and wait for the clarification "
    "state to be completed server-side."
)


_COMMON_TOOL_NAMES = {
    "api_call",
    "app_api",
    "archive_email",
    "ask_teacher",
    "ask_user",
    "bash",
    "bulk_email",
    "builtin_browser",
    "cancel_download",
    "chat_with_model",
    "create_document",
    "create_session",
    "delegate",
    "delete_email",
    "download_model",
    "edit_document",
    "edit_file",
    "edit_image",
    "generate_image",
    "glob",
    "grep",
    "list_cached_models",
    "list_cookbook_servers",
    "list_downloads",
    "list_emails",
    "list_models",
    "list_serve_presets",
    "list_served_models",
    "list_sessions",
    "ls",
    "manage_calendar",
    "manage_contact",
    "manage_documents",
    "manage_endpoints",
    "manage_mcp",
    "manage_memory",
    "manage_notes",
    "manage_todos",
    "manage_research",
    "manage_session",
    "manage_settings",
    "manage_skills",
    "manage_subagents",
    "manage_tasks",
    "manage_tokens",
    "manage_webhooks",
    "mark_email_read",
    "pipeline",
    "python",
    "read_email",
    "read_file",
    "reply_to_email",
    "resolve_contact",
    "search_chats",
    "search_hf_models",
    "send_email",
    "send_to_session",
    "serve_model",
    "serve_preset",
    "stop_served_model",
    "suggest_document",
    "spawn_subagent",
    "trigger_research",
    "ui_control",
    "update_document",
    "update_plan",
    "vault_get",
    "vault_search",
    "vault_unlock",
    "web_fetch",
    "web_search",
    "write_file",
    "publish_artifact",
    "verify_pygame_headless",
    "commit_project",
    "obsidian_read_note",
    "obsidian_write_note",
    "obsidian_search_notes",
    "obsidian_graph",
}

CLARIFICATION_OPEN_ALLOWED_TOOLS = frozenset(
    {
        "ask_user",
        "read_file",
        "grep",
        "glob",
        "ls",
        "get_workspace",
        "web_search",
        "web_fetch",
        "search_chats",
        "list_sessions",
        "list_models",
        "list_cached_models",
        "list_served_models",
        "list_downloads",
        "list_serve_presets",
        "list_cookbook_servers",
        "resolve_contact",
        "sensitive_local_analysis",
        "obsidian_read_note",
        "obsidian_search_notes",
        "obsidian_graph",
    }
)

_DSGVO_TOOL_SAFETY_CLASSES: Mapping[str, str] = MappingProxyType({
    "api_call": "unsafe",
    "app_api": "unsafe",
    "ask_teacher": "external",
    "bash": "unsafe",
    "bulk_email": "external",
    "chat_with_model": "external",
    "delegate": "unsafe",
    "delete_email": "external",
    "download_model": "external",
    "edit_file": "unsafe",
    "edit_image": "external",
    "generate_image": "external",
    "manage_endpoints": "unsafe",
    "manage_mcp": "unsafe",
    "manage_settings": "unsafe",
    "manage_tokens": "unsafe",
    "manage_webhooks": "external",
    "python": "unsafe",
    "publish_artifact": "unsafe",
    "verify_pygame_headless": "unsafe",
    "commit_project": "unsafe",
    "read_email": "external",
    "reply_to_email": "external",
    "search_hf_models": "external",
    "send_email": "external",
    "send_to_session": "external",
    "serve_model": "unsafe",
    "serve_preset": "unsafe",
    "spawn_subagent": "unsafe",
    "stop_served_model": "unsafe",
    "trigger_research": "external",
    "web_fetch": "external",
    "web_search": "external",
    "write_file": "unsafe",
})


_GUIDE_ONLY_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), reason)
    for pattern, reason in (
        (r"\bguide[-\s]?only mode\b", "guide-only mode requested"),
        (r"\bno[-\s]?tools? mode\b", "no-tools mode requested"),
        (r"\bdo not use (?:any )?tools?\b", "user forbade tool use"),
        (r"\bdon'?t use (?:any )?tools?\b", "user forbade tool use"),
        (r"\bnot allowed to use (?:any )?tools?\b", "user forbade tool use"),
        (r"\bnot allowed to:?.{0,120}\buse (?:any )?tools?\b", "user forbade tool use"),
        (r"\bask (?:me )?(?:for confirmation )?before using tools?\b", "user requested confirmation before tools"),
    )
)


@dataclass(frozen=True)
class ToolPolicy:
    """Effective tool behavior for one agent turn."""

    disabled_tools: frozenset[str] = frozenset()
    hidden_tools: frozenset[str] = frozenset()
    reasons: Mapping[str, str] = field(default_factory=dict)
    mode: str = "normal"
    block_all_tool_calls: bool = False
    disable_mcp: bool = False

    def all_disabled_names(self) -> Set[str]:
        return set(self.disabled_tools) | set(self.hidden_tools)

    def blocks(self, tool_name: Optional[str]) -> bool:
        if not tool_name:
            return False
        return self.block_all_tool_calls or tool_name in self.disabled_tools or tool_name in self.hidden_tools

    def reason_for(self, tool_name: Optional[str]) -> str:
        if tool_name and tool_name in self.reasons:
            return self.reasons[tool_name]
        if self.block_all_tool_calls and self.mode == "guide_only":
            return "Tool use is disabled for this guide-only turn."
        return "Tool use is disabled for this turn."


def detect_guide_only_turn(message: object) -> Optional[str]:
    """Return a reason when the latest user turn strongly requests no tools."""

    if not isinstance(message, str) or not message.strip():
        return None
    text = re.sub(r"\s+", " ", message.strip())
    for pattern, reason in _GUIDE_ONLY_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def known_tool_names() -> Set[str]:
    """Best-effort set of native tool names for prompt hiding and denylisting."""

    names = set(_COMMON_TOOL_NAMES)
    try:
        from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

        for schema in FUNCTION_TOOL_SCHEMAS:
            name = (schema.get("function") or {}).get("name") or schema.get("name")
            if name:
                names.add(name)
    except Exception:
        pass
    try:
        from src.agent_loop import TOOL_SECTIONS

        names.update(TOOL_SECTIONS.keys())
    except Exception:
        pass
    try:
        from src.tool_security import PLAN_MODE_READONLY_TOOLS, _PLAN_MODE_KNOWN_MUTATORS

        names.update(PLAN_MODE_READONLY_TOOLS)
        names.update(_PLAN_MODE_KNOWN_MUTATORS)
    except Exception:
        pass
    try:
        from src.tool_registry import tool_names

        names.update(tool_names())
    except Exception:
        pass
    return names


def _settings_snapshot(
    settings: Optional[Mapping[str, object]],
) -> Mapping[str, object]:
    if settings is not None:
        return settings
    try:
        from src.settings import load_settings

        loaded = load_settings()
        return loaded if isinstance(loaded, Mapping) else {}
    except Exception:
        return {}


def operator_priority_disabled_tools(
    settings: Optional[Mapping[str, object]] = None,
) -> frozenset[str]:
    """Return explicit Admin disables, or safe defaults when the setting is absent.

    Presence of ``disabled_tools`` is treated as an existing operator choice,
    including an explicit empty list. TAX9 owns any later migration of that
    choice; this defaulting path never writes settings.
    """

    snapshot = _settings_snapshot(settings)
    if "disabled_tools" not in snapshot:
        return DEFAULT_DEFERRED_RUNTIME_TOOLS

    configured = snapshot.get("disabled_tools")
    if not isinstance(configured, (list, tuple, set, frozenset)):
        return DEFAULT_DEFERRED_RUNTIME_TOOLS
    return expand_runtime_disabled_tool_names(configured)


def build_effective_tool_policy(
    *,
    disabled_tools: Optional[Iterable[str]] = None,
    last_user_message: object = "",
    orchestrator_mode: bool = False,
    clarification_open: bool = False,
    clarification_reason: str = "",
    settings: Optional[Mapping[str, object]] = None,
) -> ToolPolicy:
    """Compose the effective policy for one agent turn.

    Existing callers still provide the already-composed disabled-tool denylist.
    This function adds higher-level turn policy on top so enforcement is not
    delegated to prompt compliance.
    """

    effective_settings = _settings_snapshot(settings)
    disabled = set(expand_runtime_disabled_tool_names(disabled_tools or ()))
    hidden: Set[str] = set()
    reasons = {tool: "Tool is disabled for this request." for tool in disabled}
    default_or_configured_disabled = operator_priority_disabled_tools(effective_settings)
    explicit_admin_configuration = "disabled_tools" in effective_settings
    disabled.update(default_or_configured_disabled)
    hidden.update(default_or_configured_disabled)
    for tool in default_or_configured_disabled:
        reasons.setdefault(
            tool,
            "Tool is disabled by explicit Admin configuration."
            if explicit_admin_configuration
            else "Tool is deferred by operator priority until explicit Admin activation.",
        )

    setting_present = bool(settings is not None and "disabled_tools" in settings)
    disabled_with_defaults, defaults_applied = resolve_operator_priority_disabled(
        disabled,
        setting_present=setting_present,
    )
    if defaults_applied:
        priority_defaults = set(disabled_with_defaults) - disabled
        disabled.update(priority_defaults)
        hidden.update(priority_defaults)
        reasons.update(
            {
                tool: "Tool is deferred by operator priority until explicit reviewed activation."
                for tool in priority_defaults
            }
        )

    guide_reason = detect_guide_only_turn(last_user_message)
    if guide_reason:
        all_tools = known_tool_names()
        disabled.update(all_tools)
        hidden.update(all_tools)
        reasons.update({tool: f"{guide_reason}." for tool in all_tools})
        return ToolPolicy(
            disabled_tools=frozenset(disabled),
            hidden_tools=frozenset(hidden),
            reasons=MappingProxyType(dict(reasons)),
            mode="guide_only",
            block_all_tool_calls=True,
            disable_mcp=True,
        )

    if clarification_open:
        all_tools = known_tool_names()
        clarification_disabled = all_tools - set(CLARIFICATION_OPEN_ALLOWED_TOOLS)
        disabled.update(clarification_disabled)
        reason = clarification_reason or "A required clarification is still open; planning and mutation are blocked."
        reasons.update({tool: reason for tool in clarification_disabled})
        return ToolPolicy(
            disabled_tools=frozenset(disabled),
            hidden_tools=frozenset(clarification_disabled),
            reasons=MappingProxyType(dict(reasons)),
            mode="clarification_open",
            disable_mcp=True,
        )

    if orchestrator_mode:
        try:
            from src.tool_security import orchestrator_mode_disabled_tools

            orch_disabled = orchestrator_mode_disabled_tools()
        except Exception:
            orch_disabled = known_tool_names() - {
                "delegate",
                "spawn_subagent",
                "manage_subagents",
                "obsidian_read_note",
                "obsidian_write_note",
                "obsidian_search_notes",
                "obsidian_graph",
            }
        disabled.update(orch_disabled)
        reasons.update({tool: "Tool is disabled in orchestrator mode." for tool in orch_disabled})
        return ToolPolicy(
            disabled_tools=frozenset(disabled),
            hidden_tools=frozenset(hidden),
            reasons=MappingProxyType(dict(reasons)),
            mode="orchestrator",
            disable_mcp=True,
        )

    try:
        from src.privacy_runtime import create_runtime_security_state
        from src.secure_policy_gate import decide_tool_gate

        state = create_runtime_security_state(settings=effective_settings)
        decisions = {}
        for tool, safety_class in _DSGVO_TOOL_SAFETY_CLASSES.items():
            if safety_class not in decisions:
                decisions[safety_class] = decide_tool_gate(state=state, tool_safety_class=safety_class)
            decision = decisions[safety_class]
            if decision.allowed:
                continue
            disabled.add(tool)
            hidden.add(tool)
            reasons[tool] = (
                "DSGVO mode requires local-only processing; "
                f"tool '{tool}' is classified as {safety_class}."
            )
    except Exception:
        pass

    return ToolPolicy(
        disabled_tools=frozenset(disabled),
        hidden_tools=frozenset(hidden),
        reasons=MappingProxyType(dict(reasons)),
    )

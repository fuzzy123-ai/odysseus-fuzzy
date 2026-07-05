"""
tool_schemas.py

OpenAI-compatible function tool schemas and the converter that turns
native function calls back into ToolBlocks for the execution pipeline.

Extracted from agent_tools.py to keep schema definitions separate from
tool parsing / execution logic.
"""

import json
import logging
from typing import Optional

from src.agent_tools import ToolBlock, TOOL_TAGS
from src.tool_parsing import _TOOL_NAME_MAP
from src.tool_schema_definitions import FUNCTION_TOOL_SCHEMAS

logger = logging.getLogger(__name__)


def invalid_tool_call_block(name: str, args: Optional[dict] = None) -> Optional[ToolBlock]:
    """Return a feedback tool block for known-near-miss tool names.

    Truly unknown tool names still return None elsewhere so random XML prose is
    ignored. This path is only for recognizable legacy/foreign tool namespaces
    where the model can recover if we feed back the canonical Odysseus name.
    """
    raw_name = str(name or "").strip()
    lowered = raw_name.lower().replace("-", "_")
    suggestions: list[str] = []
    reason = "Unknown tool name."

    aliases = {
        "obsidian_mcp__obsidian_file_create": "mcp__vault__obsidian_write_note",
        "obsidian_mcp__obsidian_note_create": "mcp__vault__obsidian_write_note",
        "obsidian_mcp__obsidian_write_note": "mcp__vault__obsidian_write_note",
        "obsidian_mcp__obsidian_file_read": "mcp__vault__obsidian_read_note",
        "obsidian_mcp__obsidian_read_note": "mcp__vault__obsidian_read_note",
        "obsidian_mcp__obsidian_search_notes": "mcp__vault__obsidian_search_notes",
        "obsidian_mcp__obsidian_file_search": "mcp__vault__obsidian_search_notes",
        "obsidian_mcp__obsidian_tree": "mcp__vault__obsidian_tree",
    }
    if lowered in aliases:
        suggestions.append(aliases[lowered])
        reason = "Legacy Obsidian MCP namespace is not registered in Odysseus."
    elif lowered.startswith("obsidian_mcp__"):
        suffix = lowered.split("__", 1)[1]
        if "create" in suffix or "write" in suffix:
            suggestions.append("mcp__vault__obsidian_write_note")
        elif "read" in suffix:
            suggestions.append("mcp__vault__obsidian_read_note")
        elif "search" in suffix:
            suggestions.append("mcp__vault__obsidian_search_notes")
        elif "tree" in suffix or "list" in suffix:
            suggestions.append("mcp__vault__obsidian_tree")
        reason = "Legacy Obsidian MCP namespace is not registered in Odysseus."

    if not suggestions:
        return None

    payload = {
        "tool": raw_name,
        "reason": reason,
        "suggestions": suggestions,
        "arguments": args or {},
    }
    return ToolBlock("invalid_tool_call", json.dumps(payload, ensure_ascii=False))


def get_function_tool_schemas() -> list:
    """Return built-in plus dynamically registered plugin function schemas."""
    schemas = list(FUNCTION_TOOL_SCHEMAS)
    names = {
        (schema.get("function") or {}).get("name") or schema.get("name")
        for schema in schemas
    }
    try:
        from src.tool_registry import get_function_schemas

        for schema in get_function_schemas():
            name = (schema.get("function") or {}).get("name") or schema.get("name")
            if name and name not in names:
                schemas.append(schema)
                names.add(name)
    except Exception:
        pass
    return schemas

def function_call_to_tool_block(name: str, arguments: str) -> Optional[ToolBlock]:
    """Convert a native function call into a ToolBlock for the existing execution pipeline."""
    try:
        if not arguments or (isinstance(arguments, str) and not arguments.strip()):
            args = {}
        else:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (json.JSONDecodeError, TypeError):
        logger.error(f"Failed to parse function call arguments for {name}: {arguments}")
        return None

    tool_type = _TOOL_NAME_MAP.get(name, name)
    _BUILTIN_EMAIL_TOOLS = {"list_email_accounts", "send_email", "list_emails", "read_email", "reply_to_email",
                            "archive_email", "delete_email", "mark_email_read", "bulk_email", "download_attachment"}

    # Some models emit valid JSON that isn't an object (e.g. a bare array
    # ["ls -la"], string, or number) as function arguments. Most local tools keep
    # the legacy empty-object coercion for stream robustness, but email MCP tools
    # must fail closed so a malformed call cannot read the default mailbox.
    if not isinstance(args, dict):
        if tool_type.startswith("mcp__email__") or name in _BUILTIN_EMAIL_TOOLS:
            logger.warning(f"Non-object email function call arguments for {name}: {args!r}; rejecting")
            return None
        logger.warning(f"Non-object function call arguments for {name}: {args!r}; treating as empty")
        args = {}

    # Allow MCP tools through (namespaced as mcp__serverid__toolname)
    if tool_type.startswith("mcp__"):
        content = json.dumps(args) if args else "{}"
        return ToolBlock(tool_type, content)
    try:
        from src.tool_registry import get_tool

        if get_tool(tool_type):
            return ToolBlock(tool_type, json.dumps(args) if args else "{}")
    except Exception:
        pass
    # Email tools are implemented as MCP — route them to email
    if name in _BUILTIN_EMAIL_TOOLS:
        return ToolBlock(f"mcp__email__{name}", json.dumps(args) if args else "{}")
    if tool_type not in TOOL_TAGS:
        feedback = invalid_tool_call_block(name, args)
        if feedback:
            return feedback
        logger.warning(f"Unknown function call: {name}")
        return None

    # Convert structured args back to the text format each tool expects
    if tool_type == "bash":
        content = args.get("command", "")
    elif tool_type == "python":
        content = args.get("code", "")
    elif tool_type == "web_search":
        queries = args.get("queries")
        if isinstance(queries, list) and queries:
            content = str(queries[0])
        elif queries:
            content = str(queries)
        else:
            content = args.get("query", "")
        # Preserve the model-requested freshness filter — the web_search schema
        # advertises time_filter and the executor parses {"query","time_filter"},
        # but a bare query string dropped it. Mirrors the read_file JSON idiom.
        tf = args.get("time_filter")
        if content and isinstance(tf, str) and tf in ("day", "week", "month", "year"):
            content = json.dumps({"query": content, "time_filter": tf})
    elif tool_type == "read_file":
        # Plain path (back-compat) unless a line range is requested → JSON.
        if args.get("offset") or args.get("limit"):
            content = json.dumps(args)
        else:
            content = args.get("path", "")
    elif tool_type in ("grep", "glob", "ls"):
        content = json.dumps(args) if args else "{}"
    elif tool_type == "get_workspace":
        content = ""
    elif tool_type == "write_file":
        content = args.get("path", "") + "\n" + args.get("content", "")
    elif tool_type == "edit_file":
        content = json.dumps(args)
    elif tool_type == "create_document":
        parts = [args.get("title", "Untitled")]
        if args.get("language"):
            parts.append(args["language"])
        parts.append(args.get("content", ""))
        content = "\n".join(parts)
    elif tool_type == "edit_document":
        blocks = []
        edits = args.get("edits", [])
        if not isinstance(edits, list):
            edits = []
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            blocks.append(
                f'<<<FIND>>>\n{edit.get("find", "")}\n<<<REPLACE>>>\n{edit.get("replace", "")}\n<<<END>>>'
            )
        content = "\n".join(blocks)
    elif tool_type == "suggest_document":
        blocks = []
        suggestions = args.get("suggestions", [])
        if not isinstance(suggestions, list):
            suggestions = []
        for s in suggestions:
            if not isinstance(s, dict):
                continue
            blocks.append(
                f'<<<FIND>>>\n{s.get("find", "")}\n<<<SUGGEST>>>\n{s.get("replace", "")}\n<<<REASON>>>\n{s.get("reason", "")}\n<<<END>>>'
            )
        content = "\n".join(blocks)
    elif tool_type == "update_document":
        content = args.get("content", "")
    elif tool_type == "search_chats":
        content = args.get("query", "")
    elif tool_type == "chat_with_model":
        content = args.get("model", "") + "\n" + args.get("message", "")
    elif tool_type == "create_session":
        content = args.get("name", "Untitled") + "\n" + args.get("model", "")
    elif tool_type == "list_sessions":
        content = args.get("filter", "")
    elif tool_type == "send_to_session":
        content = args.get("session_id", "") + "\n" + args.get("message", "")
    elif tool_type == "pipeline":
        # Pass as JSON for the pipeline parser
        content = json.dumps({"steps": args.get("steps", [])})
    elif tool_type == "manage_session":
        action = args.get("action", "")
        value = args.get("value", "")
        if "confirmed" in args or "confirm" in args:
            content = json.dumps(args)
            return ToolBlock(tool_type, content)
        # `list` is the only action that takes an OPTIONAL keyword
        # filter — never a session_id. Don't leak the "current" default
        # into the filter slot (was producing "No sessions found
        # matching 'current'" when the agent omitted session_id).
        if action == "list":
            keyword = args.get("session_id", "") or args.get("keyword", "") or value
            content = "list" + (("\n" + keyword) if keyword and keyword.lower() != "current" else "")
        else:
            sid = args.get("session_id", "current")
            content = action + "\n" + sid
            if value:
                content += "\n" + value
    elif tool_type == "manage_memory":
        action = args.get("action", "")
        if action == "add":
            content = "add\n" + args.get("text", "")
            if args.get("category"):
                content += "\n" + args["category"]
        elif action == "edit":
            content = "edit\n" + args.get("memory_id", "") + "\n" + args.get("text", "")
        elif action == "delete":
            if "confirmed" in args or "confirm" in args:
                content = json.dumps(args)
            else:
                content = "delete\n" + args.get("memory_id", "")
        elif action == "search":
            content = "search\n" + args.get("text", "")
        elif action == "list":
            content = "list"
            if args.get("category"):
                content += "\n" + args["category"]
        else:
            content = action
    elif tool_type == "list_models":
        content = args.get("filter", "")
    elif tool_type == "ui_control":
        action = args.get("action", "")
        name = args.get("name", "")
        value = args.get("value", "")
        if action == "toggle":
            content = f"toggle {name} {value}"
        elif action == "open_panel":
            content = f"open_panel {name or value}"
        elif action == "open_email_reply":
            uid = args.get("uid") or name
            folder = args.get("folder") or value or "INBOX"
            mode = args.get("mode") or "reply"
            content = f"open_email_reply {uid} {folder} {mode}"
        elif action == "set_mode":
            content = f"set_mode {value or name}"
        elif action == "switch_model":
            content = f"switch_model {value or name}"
        elif action == "set_theme":
            content = f"set_theme {value or name}"
        elif action == "create_theme":
            colors = args.get("colors", {})
            theme_name = name or value or "custom"
            bg = colors.get("bg", "#282c34")
            fg = colors.get("fg", "#9cdef2")
            panel = colors.get("panel", "#111111")
            border = colors.get("border", "#355a66")
            accent = colors.get("accent", "#e06c75")
            content = f"create_theme {theme_name} {bg} {fg} {panel} {border} {accent}"
            # Append advanced overrides as key=value
            adv_keys = [
                "userBubbleBg", "aiBubbleBg", "bubbleBorder", "sidebarBg",
                "sectionAccent", "brandColor", "inputBg", "inputBorder",
                "sendBtnBg", "sendBtnHover", "codeBg", "codeFg",
                "toggleBg", "toggleActive", "accentPrimary", "accentError",
            ]
            for ak in adv_keys:
                if colors.get(ak):
                    content += f" {ak}={colors[ak]}"
        else:
            content = action
    elif tool_type in ("manage_tasks", "manage_skills", "api_call", "recent_changes", "manage_repos", "manage_github_issues",
                        "manage_nextcloud_transfer",
                        "manage_endpoints", "manage_mcp", "manage_webhooks",
                        "manage_tokens", "manage_presets", "manage_personal_docs", "manage_embeddings", "manage_assistant", "manage_plugins", "manage_documents", "manage_settings",
                        "manage_research"):
        content = json.dumps(args)
    elif tool_type == "ask_teacher":
        content = args.get("model", "auto") + "\n" + args.get("problem", "")
    elif tool_type == "ask_user":
        content = json.dumps(args, ensure_ascii=False)
    elif tool_type in ("delegate", "spawn_subagent", "manage_subagents"):
        content = json.dumps(args)
    else:
        content = json.dumps(args)

    return ToolBlock(tool_type, content)

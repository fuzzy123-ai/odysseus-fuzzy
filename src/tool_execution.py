"""
tool_execution.py

Tool dispatcher and result formatter for the agent loop.
Routes tool blocks to MCP servers or native implementations.

Extracted from agent_tools.py.
"""

import asyncio
import collections
import json
import logging
import os
import re
import sys
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from src.tool_security import (
    RUNTIME_ADMIN_TOOLS,
    is_public_blocked_tool,
    owner_is_admin_or_single_user,
)
from src.tool_policy import ToolPolicy
from src.constants import MAX_OUTPUT_CHARS, MAX_READ_CHARS, MAX_DIFF_LINES
from src.tool_utils import _truncate

from src.tool_path_confinement import (
    _AGENT_WORKDIR,
    _active_workspace,
    _is_sensitive_path,
    _resolve_search_root,
    _resolve_tool_path,
    _resolve_tool_path_in_workspace,
    _sensitive_path_globs,
    _tool_path_roots,
    agent_cwd,
    get_active_workspace,
    vet_workspace,
)
from src.tool_control_markers import handle_ask_user_marker, handle_update_plan_marker
from src.tool_result_formatting import format_tool_result


def get_mcp_manager():
    from src import agent_tools
    return agent_tools.get_mcp_manager()

logger = logging.getLogger(__name__)

_VAULT_MCP_RATE_LIMIT_BUCKETS: Dict[str, list[float]] = {}
_VAULT_MCP_RATE_LIMIT_MAX = 10
_VAULT_MCP_RATE_LIMIT_WINDOW = 60


_ADMIN_TOOLS = set(RUNTIME_ADMIN_TOOLS)


def _owner_is_admin(owner: Optional[str]) -> bool:
    """Mirror route-level admin behavior for agent tool execution."""
    return owner_is_admin_or_single_user(owner)


def _parse_mcp_tool_name(qualified_name: str) -> tuple[Optional[str], Optional[str]]:
    parts = str(qualified_name or "").split("__", 2)
    if len(parts) != 3 or parts[0] != "mcp":
        return None, None
    return parts[1], parts[2]


def _check_vault_mcp_rate_limit(owner: Optional[str], tool_name: str) -> Optional[str]:
    now = time.time()
    bucket_key = f"{owner or 'default'}:{tool_name}"
    bucket = _VAULT_MCP_RATE_LIMIT_BUCKETS.setdefault(bucket_key, [])
    bucket[:] = [t for t in bucket if now - t < _VAULT_MCP_RATE_LIMIT_WINDOW]
    if len(bucket) >= _VAULT_MCP_RATE_LIMIT_MAX:
        retry_after = int(_VAULT_MCP_RATE_LIMIT_WINDOW - (now - bucket[0]))
        return (
            f"Rate limit exceeded: {_VAULT_MCP_RATE_LIMIT_MAX} destructive vault ops "
            f"per {_VAULT_MCP_RATE_LIMIT_WINDOW}s. Retry in {retry_after}s."
        )
    bucket.append(now)
    return None


def _call_internal_vault_mcp(tool_name: str, args: Dict[str, Any], owner: Optional[str]) -> Dict[str, Any]:
    """Run the built-in vault MCP surface in-process with trusted owner scope."""
    try:
        from src.plugin_system import import_plugin_module

        vault_service = import_plugin_module("obsidian", "backend.vault_service")
        tool_specs = import_plugin_module("obsidian", "backend.tool_specs")

        effective_owner = owner or "default"
        vault_dir = vault_service.unlocked_vault_path_for_owner(effective_owner)
        if tool_name in tool_specs.DESTRUCTIVE_TOOL_NAMES:
            rate_error = _check_vault_mcp_rate_limit(effective_owner, tool_name)
            if rate_error:
                return {"stderr": f"Error: {rate_error}", "stdout": "", "exit_code": 1}
        result = tool_specs.execute_vault_tool(
            tool_name,
            vault_dir,
            args or {},
            effective_owner,
            {"source": "mcp", "token_id": "", "token_prefix": ""},
        )
        return {"stdout": tool_specs.format_tool_result(result), "stderr": "", "exit_code": 0}
    except KeyError as exc:
        return {"stderr": str(exc), "stdout": "", "exit_code": 1}
    except FileNotFoundError as exc:
        return {"stderr": f"Not found: {exc}", "stdout": "", "exit_code": 1}
    except OSError as exc:
        return {"stderr": f"IO error: {exc}", "stdout": "", "exit_code": 1}
    except Exception as exc:
        return {"stderr": f"Error: {type(exc).__name__}: {exc}", "stdout": "", "exit_code": 1}

# ---------------------------------------------------------------------------
# MCP-backed tool helpers
# ---------------------------------------------------------------------------

# Map legacy tool names -> (MCP server_id, MCP tool_name)
_MCP_TOOL_MAP = {
    "bash":           ("bash",       "bash"),
    "python":         ("python",     "python"),
    "read_file":      ("filesystem", "read_file"),
    "write_file":     ("filesystem", "write_file"),
    "web_search":     ("web_search", "web_search"),
    "web_fetch":      ("web_fetch",  "web_fetch"),
    "generate_image": ("image_gen",  "generate_image"),
}
_EMAIL_MCP_OWNER_ARG = "_odysseus_owner"


def _parse_qualified_mcp_args(tool: str, content: str) -> tuple[Dict, Optional[str]]:
    raw = (content or "").strip()
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        if tool.startswith("mcp__email__"):
            return {}, "Email MCP tool arguments must be a JSON object."
        return {}, None
    if not isinstance(parsed, dict):
        if tool.startswith("mcp__email__"):
            return {}, "Email MCP tool arguments must be a JSON object."
        return {}, None
    return parsed, None


def _parse_generate_image(content: str) -> Dict:
    lines = content.strip().split("\n")
    args = {"prompt": lines[0].strip() if lines else ""}
    for i, key in enumerate(["model", "size", "quality"], 1):
        if len(lines) > i and lines[i].strip():
            args[key] = lines[i].strip()
    return args


def _parse_manage_memory(content: str) -> Dict:
    lines = content.strip().split("\n")
    action = lines[0].strip().lower() if lines else ""
    args = {"action": action}
    if action == "add":
        args["text"] = lines[1].strip() if len(lines) > 1 else ""
        if len(lines) > 2 and lines[2].strip():
            args["category"] = lines[2].strip().lower()
    elif action == "edit":
        args["memory_id"] = lines[1].strip() if len(lines) > 1 else ""
        args["text"] = lines[2].strip() if len(lines) > 2 else ""
    elif action == "delete":
        args["memory_id"] = lines[1].strip() if len(lines) > 1 else ""
        if len(lines) > 2:
            args["confirmed"] = any(
                line.strip().lower() in {"confirmed=true", "confirm=true", "true", "yes"}
                for line in lines[2:]
            )
    elif action == "search":
        args["text"] = lines[1].strip() if len(lines) > 1 else ""
    elif action == "list":
        if len(lines) > 1 and lines[1].strip():
            args["category"] = lines[1].strip().lower()
    return args


def _parse_write_file(content: str) -> Dict:
    lines = content.split("\n", 1)
    return {"path": lines[0].strip(), "content": lines[1] if len(lines) > 1 else ""}


_MCP_ARG_PARSERS: Dict[str, Callable[[str], Dict[str, str]]] = {
    "bash":           lambda c: {"command": c},
    "python":         lambda c: {"code": c},
    "web_search":     lambda c: {"query": c.split("\n")[0].strip()},
    "web_fetch":      lambda c: {"url": c.split("\n")[0].strip()},
    "read_file":      lambda c: {"path": c.split("\n")[0].strip()},
    "write_file":     _parse_write_file,
    "generate_image": _parse_generate_image,
    "manage_memory":  _parse_manage_memory,
}


def _build_mcp_args(tool: str, content: str) -> Dict:
    """Convert fenced-block text content to structured MCP arguments."""
    parser = _MCP_ARG_PARSERS.get(tool)
    return parser(content) if parser else {}


async def _call_mcp_tool(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    owner: Optional[str] = None,
) -> Dict:
    """Route a legacy tool call through the MCP manager, with direct fallbacks."""
    mcp = get_mcp_manager()
    if not mcp:
        return await _direct_fallback(tool, content, progress_cb=progress_cb, owner=owner) or {"error": f"MCP manager not available for tool '{tool}'", "exit_code": 1}

    server_id, tool_name = _MCP_TOOL_MAP[tool]
    qualified = f"mcp__{server_id}__{tool_name}"
    args = _build_mcp_args(tool, content)
    result = await mcp.call_tool(qualified, args)

    # If MCP server not connected, try direct fallback
    if isinstance(result, dict) and result.get("exit_code") == 1 and "not connected" in result.get("error", ""):
        fallback = await _direct_fallback(tool, content, progress_cb=progress_cb, owner=owner)
        if fallback:
            return fallback

    # generate_image runs as a text-only MCP tool, so the saved image URL never
    # reaches the agent loop's structured forwarding (which renders the image via
    # buildImageBubble on result["image_url"]). Lift it out of the tool's stdout so
    # the image renders deterministically — no dependence on the model echoing the
    # URL into its prose (which it mangles/hallucinates).
    if tool == "generate_image":
        _promote_image_fields(result)

    return result


def _promote_image_fields(result: Dict) -> None:
    """Lift the image URL (+ prompt/model/size) from a successful generate_image MCP
    text result into structured fields the agent loop already forwards to
    buildImageBubble. Only acts on a dict result with exit_code 0; matches the
    generated-image URL by pattern (absolute or relative) so it's robust to the
    result's wording."""
    if not isinstance(result, dict) or result.get("exit_code") != 0:
        return
    out = result.get("stdout") or ""
    m = re.search(r'(?:https?://[^\s)\]]+)?/api/generated-image/[A-Za-z0-9._-]+', out)
    if not m:
        return
    result["image_url"] = m.group(0).strip()
    for field, pat in (
        ("image_prompt", r'^Generated image for:\s*(.+)$'),
        ("image_model", r'^model:\s*(.+)$'),
        ("image_size", r'^size:\s*(.+)$'),
    ):
        fm = re.search(pat, out, re.M)
        if fm:
            result[field] = fm.group(1).strip()


_BG_MARKERS = {"#!bg", "#bg", "# bg", "#background", "# background", "@background", "# @background"}


def _split_bg_marker(content: str):
    """If the bash content's first non-empty line is a background marker
    (e.g. `#!bg`), return (True, command_without_marker); else (False, content)."""
    lines = content.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip().lower() in _BG_MARKERS:
        del lines[i]
        return True, "\n".join(lines).strip()
    return False, content


async def _direct_fallback(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
) -> Optional[Dict]:
    _subproc_env = {
        **os.environ,
        "TERM": "xterm-256color",
        "COLUMNS": "120",
        "LINES": "40",
        "HOME": _AGENT_WORKDIR,
    }

    try:
        ctx = {
            "progress_cb": progress_cb,
            "subproc_env": _subproc_env,
            "session_id": session_id,
            "owner": owner,
        }

        from src.agent_tools import TOOL_HANDLERS
        if tool in TOOL_HANDLERS:
            return await TOOL_HANDLERS[tool](content, ctx)

    except Exception as e:
        return {"error": f"{tool}: {e}", "exit_code": 1}

    return None


async def _document_tool_dispatch(
    tool: str,
    content: str,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
) -> Optional[Dict]:
    """Route a document tool through TOOL_HANDLERS with the right ctx shape."""
    from src.agent_tools import TOOL_HANDLERS
    ctx = {"session_id": session_id, "owner": owner}
    if tool in TOOL_HANDLERS:
        return await TOOL_HANDLERS[tool](content, ctx)
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def execute_tool_block(
    block: Any,
    session_id: Optional[str] = None,
    disabled_tools: Optional[set] = None,
    owner: Optional[str] = None,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    workspace: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    model: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    context_length: int = 0,
    tool_policy: Optional[Any] = None,
    ai_lens_emitter: Optional[Any] = None,
    tool_usage_instrumentation: Optional[Any] = None,
) -> Tuple[str, Dict]:
    """Execute a single tool block. Returns (description, result_dict).

    Thin wrapper: bind the per-turn workspace (so the path resolvers + subprocess
    cwd confine to it) for the duration of this call, then delegate. Reset on the
    way out so the binding never leaks to the next tool call.
    """
    token = _active_workspace.set(workspace or None)
    started_at = time.perf_counter()
    usage_metadata = None
    usage_span = None
    if ai_lens_emitter is not None or tool_usage_instrumentation is not None:
        usage_metadata = _build_tool_usage_metadata(block)
    usage_span = _begin_tool_usage(
        tool_usage_instrumentation,
        usage_metadata,
        owner=owner,
        session_id=session_id,
    )
    _emit_ai_lens_tool_event(
        ai_lens_emitter,
        block,
        event_type="tool_call_started",
        status="started",
        usage_metadata=usage_metadata,
    )
    try:
        outcome = await _execute_tool_block_impl(
            block,
            session_id=session_id,
            disabled_tools=disabled_tools,
            owner=owner,
            progress_cb=progress_cb,
            endpoint_url=endpoint_url,
            model=model,
            headers=headers,
            context_length=context_length,
            tool_policy=tool_policy,
        )
        _emit_ai_lens_tool_event(
            ai_lens_emitter,
            block,
            event_type="tool_call_result",
            status="failed" if _tool_result_failed(outcome[1]) else "succeeded",
            result=outcome[1],
            latency_ms=max(1, int((time.perf_counter() - started_at) * 1000)),
            usage_metadata=usage_metadata,
        )
        _finish_tool_usage(usage_span, outcome=outcome, result=outcome[1])
        return outcome
    except asyncio.CancelledError as exc:
        _emit_ai_lens_tool_event(
            ai_lens_emitter,
            block,
            event_type="tool_call_result",
            status="failed",
            latency_ms=max(1, int((time.perf_counter() - started_at) * 1000)),
            usage_metadata=usage_metadata,
        )
        _finish_tool_usage(usage_span, exception=exc)
        raise
    except Exception as exc:
        _emit_ai_lens_tool_event(
            ai_lens_emitter,
            block,
            event_type="tool_call_result",
            status="failed",
            latency_ms=max(1, int((time.perf_counter() - started_at) * 1000)),
            usage_metadata=usage_metadata,
        )
        _finish_tool_usage(usage_span, exception=exc)
        raise
    finally:
        _active_workspace.reset(token)


def _tool_result_failed(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    return bool(result.get("error")) or result.get("exit_code") not in (None, 0, "0")


def _build_tool_usage_metadata(block: Any) -> Any:
    try:
        from src.tool_usage_instrumentation import build_tool_usage_call_metadata

        return build_tool_usage_call_metadata(block)
    except Exception:
        return None


def _begin_tool_usage(
    instrumentation: Any,
    metadata: Any,
    *,
    owner: Optional[str],
    session_id: Optional[str],
) -> Any:
    if instrumentation is None or metadata is None:
        return None
    try:
        return instrumentation.begin(metadata, owner=owner, session_id=session_id)
    except Exception:
        return None


def _finish_tool_usage(
    span: Any,
    *,
    outcome: Optional[Tuple[str, Dict]] = None,
    result: Any = None,
    exception: Optional[BaseException] = None,
) -> None:
    if span is None:
        return
    try:
        from src.tool_usage_instrumentation import (
            classify_tool_usage_outcome,
            exception_outcome,
        )

        terminal = (
            exception_outcome(exception)
            if exception is not None
            else classify_tool_usage_outcome(outcome[0], outcome[1])
        )
        span.finish(terminal, result=result)
    except Exception:
        pass


def _emit_ai_lens_tool_event(
    emitter: Any,
    block: Any,
    *,
    event_type: str,
    status: str,
    result: Any = None,
    latency_ms: int = 0,
    usage_metadata: Any = None,
) -> None:
    if emitter is None:
        return
    try:
        from src.ai_lens_events import AiLensRedactionLevel, AiLensSourceKind, AiLensSourceRef
        from src.ai_lens_service import opaque_ai_lens_ref

        tool_name = (
            usage_metadata.tool_analytics_id
            if usage_metadata is not None
            else str(getattr(block, "tool_type", "") or "unknown")
        )
        content = str(getattr(block, "content", "") or "")
        source_ref = AiLensSourceRef.create(
            source_id=opaque_ai_lens_ref("tool", tool_name),
            kind=AiLensSourceKind.TOOL,
            redaction_level=AiLensRedactionLevel.HASHED,
        )
        payload: Dict[str, Any] = {
            "tool_ref": source_ref.source_id,
            "argument_present": (
                usage_metadata.argument_present
                if usage_metadata is not None
                else bool(content)
            ),
            "argument_bytes": (
                usage_metadata.argument_bytes
                if usage_metadata is not None
                else min(len(content.encode("utf-8", errors="replace")), 10_000_000)
            ),
            "arguments_included": False,
        }
        if usage_metadata is not None:
            payload.update(
                tool_analytics_id=usage_metadata.tool_analytics_id,
                tool_family=usage_metadata.tool_family.value,
                tool_source=usage_metadata.tool_source.value,
                argument_size_bucket=usage_metadata.argument_size_bucket.value,
            )
        if event_type == "tool_call_result":
            payload.update({
                "success": status == "succeeded",
                "result_field_count": min(len(result), 1000) if isinstance(result, dict) else 0,
                "result_included": False,
                "retry_count": 0,
            })
        emitter.emit(
            event_type=event_type,
            source_refs=(source_ref,),
            payload=payload,
            summary="Tool call started." if event_type == "tool_call_started" else "Tool call completed.",
            status=status,
            latency_ms=latency_ms,
        )
    except Exception:
        try:
            emitter.record_rejection("tool_capture_failed")
        except Exception:
            pass


async def _execute_tool_block_impl(
    block: Any,
    session_id: Optional[str] = None,
    disabled_tools: Optional[set] = None,
    owner: Optional[str] = None,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    endpoint_url: Optional[str] = None,
    model: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    context_length: int = 0,
    tool_policy: Optional[Any] = None,
) -> Tuple[str, Dict]:
    """Execute a single tool block. Returns (description, result_dict).

    `progress_cb` is forwarded to long-running subprocess tools
    (bash, python) so the agent loop can emit `tool_progress` SSE
    events while the command is in flight. Ignored by other tools.
    """
    from src.tool_implementations import (
        do_search_chats, do_manage_tasks,
        do_manage_skills, do_recent_changes, do_api_call, do_manage_endpoints,
        do_manage_mcp, do_manage_webhooks, do_manage_tokens,
        do_manage_presets, do_manage_personal_docs, do_manage_embeddings, do_manage_assistant, do_manage_plugins, do_manage_repos, do_manage_settings, do_manage_notes, do_manage_todos,
        do_manage_github_issues,
        do_manage_nextcloud_transfer,
        do_manage_calendar,
        do_download_model, do_serve_model, do_list_served_models, do_stop_served_model,
        do_tail_serve_output,
        do_list_downloads, do_cancel_download, do_search_hf_models, do_list_cached_models,
        do_list_serve_presets, do_serve_preset, do_adopt_served_model,
        do_list_cookbook_servers,
        do_edit_image, do_trigger_research, do_manage_research, do_resolve_contact,
        do_manage_contact,
        do_vault_search, do_vault_get, do_vault_unlock,
        do_app_api,
    )

    tool = block.tool_type
    content = block.content
    interactive_runtime_decision = None

    # Misformatted tool call detection: model put JSON inside ```python``` (or
    # similar) without naming the tool. Common with MiniMax-style outputs.
    # Return a helpful error so the model retries with the correct format.
    if tool in ("python", "json", "xml") and content.strip().startswith("{") and content.strip().endswith("}"):
        try:
            parsed = json.loads(content.strip())
            if isinstance(parsed, dict):
                desc = f"{tool}: misformatted tool call"
                result = {
                    "error": (
                        f"You wrote a JSON object inside a ```{tool}``` block, but that's not a tool call.\n"
                        "To call a tool, use the tool name as the fence tag, e.g.\n"
                        "```resolve_contact\n"
                        "{\"name\": \"...\"}\n"
                        "```\n"
                        "or\n"
                        "```send_email\n"
                        "{\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\"}\n"
                        "```"
                    ),
                    "exit_code": 1,
                }
                return desc, result
        except (ValueError, TypeError):
            pass

    # Reject tools that the user has disabled for this request
    if disabled_tools and tool in disabled_tools:
        desc = f"{tool}: BLOCKED"
        result = {"error": f"Tool '{tool}' is disabled by user.", "exit_code": 1}
        logger.info(f"Tool blocked by user: {tool}")
        return desc, result

    if tool_policy and tool_policy.blocks(tool):
        desc = f"{tool}: BLOCKED"
        result = {
            "error": f"Execution of tool '{tool}' is forbade by the active guide-only policy.",
            "exit_code": 1,
        }
        logger.warning("Tool policy blocked tool=%s", tool)
        return desc, result

    if tool in _ADMIN_TOOLS and not _owner_is_admin(owner):
        desc = f"{tool}: BLOCKED"
        result = {"error": f"Tool '{tool}' requires an admin user.", "exit_code": 1}
        logger.warning("Admin tool blocked for non-admin owner=%r tool=%s", owner, tool)
        return desc, result

    if is_public_blocked_tool(tool) and not _owner_is_admin(owner):
        desc = f"{tool}: BLOCKED"
        result = {
            "error": (
                f"Tool '{tool}' is restricted to admin users on this deployment. "
                "Ask an admin to perform this action or grant the needed permission."
            ),
            "exit_code": 1,
        }
        logger.warning("Public tool policy blocked owner=%r tool=%s", owner, tool)
        return desc, result

    if tool == "invalid_tool_call":
        try:
            payload = json.loads(content) if content else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        bad_tool = str(payload.get("tool") or "unknown")
        suggestions = payload.get("suggestions") if isinstance(payload.get("suggestions"), list) else []
        suggestion_text = ", ".join(f"`{item}`" for item in suggestions if item)
        desc = f"invalid_tool_call: {bad_tool}"
        result = {
            "error": (
                f"Ungültiger Tool-Befehl `{bad_tool}`. "
                + (f"Meintest du vielleicht {suggestion_text}? " if suggestion_text else "")
                + "Bitte rufe den vorgeschlagenen Odysseus-Toolnamen mit denselben Argumenten erneut auf."
            ),
            "exit_code": 1,
        }
        logger.info("Invalid tool call feedback generated for tool=%s suggestions=%s", bad_tool, suggestions)
        return desc, result

    # ask_user: the agent poses a multiple-choice question to the user to get a
    # decision/clarification. This is a pure UI-control marker — no subprocess,
    # no filesystem. It returns an `ask_user` payload that the agent loop turns
    # into an `ask_user` SSE event and then ENDS the turn, so the chat waits for
    # the user's selection (their choice arrives as the next message).
    if tool == "ask_user":
        desc, result = handle_ask_user_marker(content)
        payload = result.get("ask_user") or {}
        logger.info(
            "Tool executed: %s (%d options, multi=%s)",
            desc,
            len(payload.get("options") or []),
            bool(payload.get("multi")),
        )
        return desc, result

    # Native desktop loops are not visible in the web chat.  Enforce that at
    # the execution boundary instead of relying on the model to remember a
    # prompt warning.  Dummy-SDL runs remain explicitly headless evidence;
    # their audit record can never imply an interactive preview.
    if tool in ("bash", "python"):
        try:
            from src.interactive_runtime_policy import (
                InteractiveRuntimeKind,
                classify_interactive_runtime_command,
            )

            policy_input = content if tool == "bash" else "python -c " + content
            interactive_runtime_decision = classify_interactive_runtime_command(policy_input)
            decision_kind = interactive_runtime_decision.kind
            interactive_install = bool(
                re.search(
                    r"\b(?:pygame(?:-ce)?|SDL(?:2)?|tkinter|pyqt\d*|pyside\d*|wxpython|kivy|pyglet|arcade)\b",
                    content,
                    re.IGNORECASE,
                )
            )
            must_block = not interactive_runtime_decision.permitted and (
                decision_kind != InteractiveRuntimeKind.RISKY_INSTALL or interactive_install
            )
            if must_block:
                messages = {
                    InteractiveRuntimeKind.INTERACTIVE_NATIVE_GUI_LAUNCH: (
                        "Native GUI execution is not interactive in the Odysseus server sandbox. "
                        "Use bounded dummy-SDL verification and publish the native file, or build a self-contained HTML preview."
                    ),
                    InteractiveRuntimeKind.RISKY_INSTALL: (
                        "Interactive dependency installation needs a separate install gate. "
                        "Probe the installed dependency first; do not use an OS-package fallback."
                    ),
                    InteractiveRuntimeKind.PIPELINE_MASKING: (
                        "The command can hide the producer's failing exit status. "
                        "Run the producer directly or preserve and check its exit code."
                    ),
                }
                return (
                    f"{tool}: BLOCKED by interactive runtime policy",
                    {
                        "error": messages.get(decision_kind, "Interactive runtime command blocked."),
                        "exit_code": 1,
                        "interactive_runtime": interactive_runtime_decision.audit_summary(),
                    },
                )
        except (ValueError, TypeError) as exc:
            return (
                f"{tool}: BLOCKED by interactive runtime policy",
                {"error": f"Interactive runtime policy could not safely classify the command: {exc}", "exit_code": 1},
            )

    # update_plan: the agent writes back to the active plan — tick an item done
    # or revise steps (e.g. when the user asks to change something). Pure UI
    # marker: returns a `plan_update` payload the agent loop turns into a
    # `plan_update` SSE event; the frontend replaces the stored plan and refreshes
    # the docked plan window. Does NOT end the turn.
    if tool == "update_plan":
        desc, result = handle_update_plan_marker(content)
        logger.info("Tool executed: %s", desc)
        return desc, result

    # Background execution: a `bash` block whose first line is the `#!bg`
    # marker runs DETACHED — returns a job id immediately so the chat stream
    # isn't held open for a multi-minute install/ffmpeg/download. The always-on
    # monitor re-invokes the agent with the full output when the job finishes.
    if tool == "bash" and session_id:
        _is_bg, _bg_cmd = _split_bg_marker(content)
        if _is_bg and _bg_cmd:
            from src import bg_jobs
            rec = bg_jobs.launch(_bg_cmd, session_id=session_id, cwd=agent_cwd())
            short = _bg_cmd.strip().split(chr(10))[0][:80]
            desc = f"bash (background): {short}"
            result = {
                "output": (
                    f"Started background job `{rec['id']}`. It is running detached; "
                    f"do NOT wait for it or poll it. You will be automatically re-invoked "
                    f"with its full output when it finishes. Continue with other work, or "
                    f"end your turn now and resume when the result arrives. If the user "
                    f"later asks to check progress or stop it, call the manage_bg_jobs "
                    f"tool yourself (output or kill); do not tell them to run a tool "
                    f"command, and do not surface raw tool syntax in your reply."
                ),
                "exit_code": 0,
                "bg_job_id": rec["id"],
            }
            logger.info(f"Tool executed: {desc} -> bg job {rec['id']}")
            return desc, result

    # Route MCP-extracted tools through the MCP manager. Forward
    # the progress callback so long-running subprocess tools
    # (bash, python) can stream `tool_progress` events to the UI.
    if tool in _MCP_TOOL_MAP:
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await _call_mcp_tool(tool, content, progress_cb=progress_cb, owner=owner)
    elif tool in ("grep", "glob", "ls", "get_workspace"):
        # Code-navigation tools — no MCP server; run the direct implementation.
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await _direct_fallback(tool, content, progress_cb=progress_cb, owner=owner) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool in ("publish_artifact", "verify_pygame_headless"):
        # Generated-deliverable tools live in the direct agent registry.  Keep
        # them on the same owner/workspace-bound execution path as file tools;
        # schema registration alone does not make a tool dispatchable here.
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await _direct_fallback(
            tool,
            content,
            progress_cb=progress_cb,
            owner=owner,
        ) or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool == "delegate":
        from src.delegate_tool import do_delegate

        desc = "delegate"
        result = await do_delegate(
            content,
            endpoint_url=endpoint_url,
            model=model,
            headers=headers,
            owner=owner,
            session_id=session_id,
            context_length=context_length,
        )
    elif tool in ("spawn_subagent", "manage_subagents"):
        from src.subagent_runtime import manage_subagents_from_tool, spawn_subagent_from_tool

        desc = tool
        try:
            result = (
                spawn_subagent_from_tool(content)
                if tool == "spawn_subagent"
                else manage_subagents_from_tool(content)
            )
        except Exception as exc:
            result = {"error": f"{tool}: {exc}", "exit_code": 1}
    elif tool == "manage_bg_jobs":
        # Inspect/kill detached `bash` jobs; needs session_id to scope to chat.
        desc = f"manage_bg_jobs: {content.split(chr(10))[0][:80]}"
        result = await _direct_fallback(tool, content, session_id=session_id, owner=owner) \
            or {"error": "manage_bg_jobs: execution failed", "exit_code": 1}
    elif tool in ("create_document", "update_document", "edit_document",
                  "suggest_document", "manage_documents"):
        desc = f"{tool}: {content.split(chr(10))[0][:80]}"
        result = await _document_tool_dispatch(tool, content, session_id, owner) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
        if tool in ("edit_document", "suggest_document") and "title" in (result or {}):
            desc = f"{tool}: {result.get('title', '')}"
    elif tool == "search_chats":
        query = content.split("\n")[0].strip()
        desc = f"search_chats: {query[:80]}"
        result = await do_search_chats(query, owner=owner)
    elif tool in ("chat_with_model", "ask_teacher", "list_models"):
        # Migrated to the agent_tools registry (#3629): dispatched through
        # TOOL_HANDLERS with the owner/session ctx these tools need, instead
        # of the legacy dispatch_ai_tool elif. The impls live in
        # src/agent_tools/model_interaction_tools.py.
        first_line = content.split(chr(10))[0].strip()[:60]
        desc = f"{tool}: {first_line}" if first_line else tool
        result = await _document_tool_dispatch(tool, content, session_id, owner) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool in ("create_session", "list_sessions", "send_to_session", "manage_session"):
        # Migrated to the agent_tools registry (#3629): dispatched through
        # TOOL_HANDLERS with the owner/session ctx these tools need. The impls
        # live in src/agent_tools/session_tools.py.
        first_line = content.split(chr(10))[0].strip()[:60]
        desc = f"{tool}: {first_line}" if first_line else tool
        result = await _document_tool_dispatch(tool, content, session_id, owner) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool in ("pipeline", "manage_memory", "ui_control"):
        from src.ai_interaction import dispatch_ai_tool
        desc, result = await dispatch_ai_tool(tool, content, session_id, owner=owner)
    elif tool == "manage_tasks":
        desc = "manage_tasks"
        result = await do_manage_tasks(content, owner=owner)
    elif tool == "manage_skills":
        desc = "manage_skills"
        result = await do_manage_skills(content, owner=owner)
    elif tool == "recent_changes":
        desc = "recent_changes"
        result = await do_recent_changes(content, owner=owner)
    elif tool == "api_call":
        first_line = content.split("\n")[0].strip()[:60]
        desc = f"api_call: {first_line}"
        result = await do_api_call(content)
    elif tool == "manage_endpoints":
        desc = "manage_endpoints"
        result = await do_manage_endpoints(content, owner=owner)
    elif tool == "manage_mcp":
        desc = "manage_mcp"
        result = await do_manage_mcp(content, owner=owner)
    elif tool == "manage_webhooks":
        desc = "manage_webhooks"
        result = await do_manage_webhooks(content, owner=owner)
    elif tool == "manage_tokens":
        desc = "manage_tokens"
        result = await do_manage_tokens(content, owner=owner)
    elif tool == "manage_presets":
        desc = "manage_presets"
        result = await do_manage_presets(content, owner=owner)
    elif tool == "manage_personal_docs":
        desc = "manage_personal_docs"
        result = await do_manage_personal_docs(content, owner=owner)
    elif tool == "manage_embeddings":
        desc = "manage_embeddings"
        result = await do_manage_embeddings(content, owner=owner)
    elif tool == "manage_assistant":
        desc = "manage_assistant"
        result = await do_manage_assistant(content, owner=owner)
    elif tool == "manage_plugins":
        desc = "manage_plugins"
        result = await do_manage_plugins(content, owner=owner)
    elif tool == "manage_repos":
        desc = "manage_repos"
        result = await do_manage_repos(content, owner=owner)
    elif tool == "manage_github_issues":
        desc = "manage_github_issues"
        result = await do_manage_github_issues(content, owner=owner)
    elif tool == "manage_nextcloud_transfer":
        desc = "manage_nextcloud_transfer"
        result = await do_manage_nextcloud_transfer(content, owner=owner)
    elif tool == "manage_settings":
        desc = "manage_settings"
        result = await do_manage_settings(content, owner=owner)
    elif tool == "manage_notes":
        desc = "manage_notes"
        result = await do_manage_notes(content, owner=owner)
    elif tool == "manage_todos":
        desc = "manage_todos"
        result = await do_manage_todos(content, owner=owner)
    elif tool == "manage_calendar":
        desc = "manage_calendar"
        result = await do_manage_calendar(content, owner=owner)
    elif tool == "download_model":
        desc = "download_model"
        result = await do_download_model(content, owner=owner)
    elif tool == "serve_model":
        desc = "serve_model"
        result = await do_serve_model(
            content, owner=owner, caller_session_id=session_id
        )
    elif tool == "list_served_models":
        desc = "list_served_models"
        result = await do_list_served_models(content, owner=owner)
    elif tool == "stop_served_model":
        desc = "stop_served_model"
        result = await do_stop_served_model(content, owner=owner)
    elif tool == "tail_serve_output":
        desc = "tail_serve_output"
        result = await do_tail_serve_output(
            content, owner=owner, caller_session_id=session_id
        )
    elif tool == "list_downloads":
        desc = "list_downloads"
        result = await do_list_downloads(content, owner=owner)
    elif tool == "cancel_download":
        desc = "cancel_download"
        result = await do_cancel_download(content, owner=owner)
    elif tool == "search_hf_models":
        desc = "search_hf_models"
        result = await do_search_hf_models(content, owner=owner)
    elif tool == "list_cached_models":
        desc = "list_cached_models"
        result = await do_list_cached_models(content, owner=owner)
    elif tool == "app_api":
        desc = "app_api"
        result = await do_app_api(content, owner=owner)
    elif tool == "list_serve_presets":
        desc = "list_serve_presets"
        result = await do_list_serve_presets(content, owner=owner)
    elif tool == "serve_preset":
        desc = "serve_preset"
        result = await do_serve_preset(
            content, owner=owner, caller_session_id=session_id
        )
    elif tool == "adopt_served_model":
        desc = "adopt_served_model"
        result = await do_adopt_served_model(
            content, owner=owner, caller_session_id=session_id
        )
    elif tool == "list_cookbook_servers":
        desc = "list_cookbook_servers"
        result = await do_list_cookbook_servers(content, owner=owner)
    elif tool == "edit_image":
        desc = "edit_image"
        result = await do_edit_image(content, owner=owner)
    elif tool == "edit_file":
        result = await _direct_fallback(tool, content, owner=owner) or {"error": "edit failed", "exit_code": 1}
        desc = result.get("output") or result.get("error") or "edit_file"
    elif tool == "trigger_research":
        desc = "trigger_research"
        result = await do_trigger_research(content, owner=owner)
    elif tool == "manage_research":
        desc = "manage_research"
        result = await do_manage_research(content, owner=owner)
    elif tool == "resolve_contact":
        desc = "resolve_contact"
        result = await do_resolve_contact(content, owner=owner)
    elif tool == "manage_contact":
        desc = "manage_contact"
        result = await do_manage_contact(content, owner=owner)
    elif tool == "vault_search":
        desc = "vault_search"
        result = await do_vault_search(content, owner=owner)
    elif tool == "vault_get":
        desc = "vault_get"
        result = await do_vault_get(content, owner=owner)
    elif tool == "vault_unlock":
        desc = "vault_unlock"
        result = await do_vault_unlock(content, owner=owner)
    elif tool.startswith("mcp__"):
        # MCP tool dispatch
        desc = f"mcp: {tool}"
        server_id, mcp_tool_name = _parse_mcp_tool_name(tool)
        args, parse_error = _parse_qualified_mcp_args(tool, content)
        if parse_error:
            result = {"error": parse_error, "exit_code": 1}
        elif server_id == "vault" and mcp_tool_name:
            result = _call_internal_vault_mcp(mcp_tool_name, args, owner)
        else:
            mcp = get_mcp_manager()
            if mcp:
                if tool.startswith("mcp__email__") and owner:
                    args = dict(args)
                    args[_EMAIL_MCP_OWNER_ARG] = owner
                result = await mcp.call_tool(tool, args)
            else:
                result = {"error": "MCP manager not available", "exit_code": 1}
    else:
        try:
            from src.tool_registry import execute_tool as execute_plugin_tool
            from src.tool_registry import get_tool as get_plugin_tool

            plugin_tool = get_plugin_tool(tool)
        except Exception:
            plugin_tool = None

        if plugin_tool is not None:
            if plugin_tool.permission == "admin" and not _owner_is_admin(owner):
                desc = f"{tool}: BLOCKED"
                result = {"error": f"Tool '{tool}' requires an admin user.", "exit_code": 1}
                logger.warning("Admin plugin tool blocked for non-admin owner=%r tool=%s", owner, tool)
            else:
                desc = f"{tool}: plugin"
                result = await execute_plugin_tool(
                    tool,
                    content,
                    owner=owner,
                    session_id=session_id,
                    workspace=get_active_workspace(),
                    progress_cb=progress_cb,
                )
        else:
            desc = f"unknown: {tool}"
            result = {"error": f"Unknown tool type: {tool}", "exit_code": 1}

    if interactive_runtime_decision is not None and isinstance(result, dict):
        result.setdefault("interactive_runtime", interactive_runtime_decision.audit_summary())
    logger.info(f"Tool executed: {desc} -> exit_code={result.get('exit_code', 'n/a')}")
    return desc, result

# Keys handled by the dedicated branches below — never echo them as raw JSON.

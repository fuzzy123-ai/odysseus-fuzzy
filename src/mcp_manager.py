"""
mcp_manager.py

Manages connections to MCP (Model Context Protocol) tool servers.
Each server exposes tools that are made available to the agent loop.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from src.runtime_paths import get_app_root
from src.tool_catalog import ToolAvailability, ToolFamily, ToolLifecycle

logger = logging.getLogger(__name__)

# Most built-in Python MCP servers have native/legacy wrappers and should not
# be advertised as dynamic MCP tools. These built-ins intentionally keep their
# qualified MCP surface visible to the chat agent.
PROMPT_VISIBLE_BUILTIN_SERVERS = {"vault", "builtin_browser"}

def _format_mcp_connection_error(name: str, command: str = "", args: Optional[List[str]] = None, error: Exception = None) -> str:
    """Return a user-actionable MCP connection error message."""
    args = args or []
    raw_error = str(error) if error else "Unknown error"
    command_line = " ".join([command or "", *args]).strip()
    lower_command = command_line.lower()

    if "@playwright/mcp" in lower_command:
        return (
            f"{raw_error}\n\n"
            "Browser MCP could not start. On fresh installs, cache the Playwright MCP package once before connecting:\n\n"
            "npx -y @playwright/mcp@latest --version\n\n"
            "Then restart Odysseus and reconnect the Browser MCP server."
        )

    return raw_error


# Caps for rendering untrusted MCP tool schemas into the agent prompt (issue #2660).
# MCP servers are third-party/user-added, so field names and parameter counts are
# untrusted input — bound them so an odd or hostile schema cannot distort the prompt.
_MCP_PARAM_MAX = 12   # max params rendered per tool
_MCP_TOKEN_MAX = 40   # max chars per rendered name / type token
_MCP_HINT_MAX = 300   # total-length backstop for the whole hint


def _sanitize_schema_token(value: Any, limit: int = _MCP_TOKEN_MAX) -> str:
    """Make an untrusted JSON-Schema token safe to splice into the prompt.

    Replaces control chars / newlines with a space, collapses whitespace, and
    length-caps the result, so a weird field name or type cannot inject newlines
    or run on. Normal short identifiers pass through unchanged.
    """
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _format_mcp_params(input_schema: Any) -> str:
    """Render an MCP tool's JSON-Schema inputs as a compact prompt hint.

    Without this the agent only sees a tool's name + description and has to
    guess its arguments (issue #2509). Produces e.g.
    ` Args (JSON): {"path": string (required), "limit": integer}` — names,
    coarse types, and required-ness, kept short so it stays prompt-friendly.
    Returns "" when there are no parameters.

    MCP servers are third-party, so names/types are sanitized and the parameter
    count + total length are capped (issue #2660); normal schemas are unaffected.
    """
    if not isinstance(input_schema, dict):
        return ""
    props = input_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return ""
    required = set(input_schema.get("required") or [])
    parts = []
    for pname, pinfo in list(props.items())[:_MCP_PARAM_MAX]:
        pinfo = pinfo if isinstance(pinfo, dict) else {}
        ptype = pinfo.get("type") or "any"
        if isinstance(ptype, list):
            ptype = "|".join(str(x) for x in ptype)
        tag = f'"{_sanitize_schema_token(pname)}": {_sanitize_schema_token(ptype)}'
        if pname in required:
            tag += " (required)"
        parts.append(tag)
    extra = len(props) - len(parts)
    if extra > 0:
        parts.append(f"…+{extra} more")
    hint = " Args (JSON): {" + ", ".join(parts) + "}"
    if len(hint) > _MCP_HINT_MAX:
        hint = hint[:_MCP_HINT_MAX - 1].rstrip() + "…"
    return hint


# Tool-name prefixes that denote a read-only/inspection operation. Used to
# classify MCP tools for plan mode when the server provides no readOnlyHint.
# These are PREFIXES, not whole words (matched via str.startswith below), so a
# stem like "summar" intentionally covers "summarise"/"summarize"/"summary".
_MCP_READONLY_VERBS = (
    "list", "get", "read", "search", "fetch", "query", "find", "describe",
    "show", "view", "lookup", "count", "status", "info", "inspect", "summar",
)

_OBSIDIAN_READONLY_MCP_TOOLS = {
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
}

_MCP_CATALOG_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def parse_qualified_mcp_tool_name(value: object) -> tuple[str, str] | None:
    """Parse a bounded qualified MCP name without retaining malformed input."""

    if not isinstance(value, str):
        return None
    parts = value.split("__", 2)
    if len(parts) != 3 or parts[0] != "mcp":
        return None
    server_id, tool_name = parts[1], parts[2]
    if not _MCP_CATALOG_COMPONENT_RE.fullmatch(
        server_id
    ) or not _MCP_CATALOG_COMPONENT_RE.fullmatch(tool_name):
        return None
    return server_id, tool_name


def _mcp_source_id(server_id: object) -> str:
    component = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(server_id or "")).strip("-")[:80]
    return f"mcp:{component or 'unknown'}"


def _mcp_catalog_metadata(
    server_id: str,
    tool: Dict,
    *,
    disabled: bool,
    collision: bool = False,
) -> Dict[str, Any]:
    raw_family = tool.get("family")
    try:
        family = ToolFamily(str(raw_family or ToolFamily.PLUGINS_MCP.value).lower())
    except ValueError:
        family = ToolFamily.UNCLASSIFIED_DYNAMIC
    try:
        lifecycle = ToolLifecycle(
            str(tool.get("lifecycle") or ToolLifecycle.CONTEXTUAL.value).lower()
        )
    except ValueError:
        lifecycle = ToolLifecycle.BLOCKED
    try:
        availability = ToolAvailability(
            str(tool.get("availability") or ToolAvailability.AVAILABLE.value).lower()
        )
    except ValueError:
        availability = ToolAvailability.BLOCKED

    tool_name = str(tool.get("name") or "")
    safe_identity = bool(
        _MCP_CATALOG_COMPONENT_RE.fullmatch(str(server_id or ""))
        and _MCP_CATALOG_COMPONENT_RE.fullmatch(tool_name)
    )
    blocked = (
        collision
        or not safe_identity
        or family == ToolFamily.UNCLASSIFIED_DYNAMIC
        or lifecycle == ToolLifecycle.BLOCKED
        or availability in {
            ToolAvailability.BLOCKED,
            ToolAvailability.UNAVAILABLE,
            ToolAvailability.UNKNOWN,
        }
    )
    if blocked:
        family = ToolFamily.UNCLASSIFIED_DYNAMIC
        lifecycle = ToolLifecycle.BLOCKED
        availability = ToolAvailability.BLOCKED
    elif disabled:
        availability = ToolAvailability.DISABLED
    catalog_enabled = bool(
        not blocked
        and lifecycle in {ToolLifecycle.ACTIVE, ToolLifecycle.CONTEXTUAL}
        and availability == ToolAvailability.AVAILABLE
    )
    return {
        "family": family.value,
        "source": "mcp",
        "source_id": _mcp_source_id(server_id),
        "permission": "admin",
        "availability": availability.value,
        "lifecycle": lifecycle.value,
        "catalog_blocked": blocked,
        "catalog_enabled": catalog_enabled,
        "policy_authority": "mcp_runtime_policy",
    }


def mcp_tool_is_readonly(tool: Dict) -> bool:
    """Classify an MCP tool as safe (non-mutating) for plan mode.

    Prefer the server's own annotations (readOnlyHint / destructiveHint). When
    absent, fall back to a tool-name verb heuristic, and FAIL CLOSED (treat as
    write) for anything that doesn't clearly read — plan mode must not run a
    write tool just because its intent is ambiguous.
    """
    ann = tool.get("annotations")
    # annotations may be a dict or a pydantic model
    read_hint = None
    destructive = None
    if ann is not None:
        if isinstance(ann, dict):
            read_hint = ann.get("readOnlyHint")
            destructive = ann.get("destructiveHint")
        else:
            read_hint = getattr(ann, "readOnlyHint", None)
            destructive = getattr(ann, "destructiveHint", None)
    if read_hint is True:
        return True
    if read_hint is False or destructive is True:
        return False
    # No usable hint — heuristic on the tool name's leading verb.
    name = (tool.get("name") or "").lower()
    if name in _OBSIDIAN_READONLY_MCP_TOOLS:
        return True
    return name.startswith(_MCP_READONLY_VERBS)


class McpManager:
    """Manages MCP server connections and tool routing."""

    def __init__(self):
        # server_id -> connection state
        self._connections: Dict[str, Dict[str, Any]] = {}
        # server_id -> list of tool schemas
        self._tools: Dict[str, List[Dict]] = {}
        # server_id -> MCP ClientSession
        self._sessions: Dict[str, Any] = {}
        # server_id -> exit stack (for cleanup)
        self._stacks: Dict[str, Any] = {}
        # server_id -> background connect task (HTTP transport / OAuth)
        self._connect_tasks: Dict[str, Any] = {}
        # Tracking updates to tools/connections for RAG indexing / prompt cache
        self._generation = 0
        # Descriptor projections are derived state and must track discovery generation.
        self._descriptor_cache_key = None
        self._descriptor_cache: Tuple[Dict[str, Any], ...] = ()

    def generation(self) -> int:
        """Return the generation used to invalidate MCP catalog consumers."""
        return self._generation

    async def connect_server(
        self,
        server_id: str,
        name: str,
        transport: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        url: Optional[str] = None,
    ) -> bool:
        """Connect to an MCP server via stdio, SSE, or Streamable HTTP transport."""
        before_generation = self._generation
        try:
            if transport == "stdio":
                res = await self._connect_stdio(server_id, name, command, args or [], env or {})
            elif transport == "sse":
                res = await self._connect_sse(server_id, name, url)
            elif transport == "http":
                res = await self._start_http_connect(server_id, name, url)
            else:
                logger.error(f"Unknown MCP transport: {transport}")
                res = False
            if res and self._generation == before_generation:
                self._generation += 1
            return res
        except Exception as e:
            logger.error(f"Failed to connect MCP server {name} ({server_id}): {e}")
            error_message = _format_mcp_connection_error(name, command or "", args or [], e)
            self._connections[server_id] = {"status": "error", "error": error_message, "name": name}
            self._generation += 1
            return False

    async def _connect_stdio(self, server_id: str, name: str, command: str, args: List[str], env: Dict[str, str]) -> bool:
        """Connect to an MCP server via stdio transport."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from contextlib import AsyncExitStack

            server_params = StdioServerParameters(
                command=command,
                args=args,
                env={**os.environ, **env} if env else None,
            )

            stack = AsyncExitStack()
            try:
                transport = await stack.enter_async_context(stdio_client(server_params))
                read_stream, write_stream = transport
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))

                await session.initialize()

                # Discover tools
                tools_result = await session.list_tools()
            except Exception:
                await stack.aclose()
                raise
            tools = []
            for tool in tools_result.tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                    # MCP tool annotations (readOnlyHint / destructiveHint) drive
                    # plan-mode read-only gating. Absent on many servers, so we
                    # fall back to a name heuristic in mcp_tool_is_readonly().
                    "annotations": getattr(tool, 'annotations', None),
                })

            self._sessions[server_id] = session
            self._stacks[server_id] = stack
            self._tools[server_id] = tools
            # Extract identity hints from env vars (e.g. email address, API name)
            # so tool descriptions can distinguish between multiple instances of
            # the same MCP server (e.g. two email accounts).
            identity_hints = []
            for k, v in (env or {}).items():
                k_lower = k.lower()
                if any(x in k_lower for x in ['email_address', 'account', 'user', 'username']):
                    identity_hints.append(v)
            identity = ", ".join(identity_hints) if identity_hints else ""

            self._connections[server_id] = {
                "status": "connected",
                "name": name,
                "transport": "stdio",
                "tool_count": len(tools),
                "identity": identity,
            }

            logger.info(f"MCP server connected: {name} ({server_id}) - {len(tools)} tools via stdio")
            return True

        except ImportError:
            logger.warning("MCP package not installed. Install with: pip install mcp")
            self._connections[server_id] = {"status": "error", "error": "mcp package not installed", "name": name}
            return False

    async def _connect_sse(self, server_id: str, name: str, url: str) -> bool:
        """Connect to an MCP server via SSE transport."""
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
            from contextlib import AsyncExitStack

            stack = AsyncExitStack()
            try:
                transport = await stack.enter_async_context(sse_client(url))
                read_stream, write_stream = transport
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))

                await session.initialize()

                # Discover tools
                tools_result = await session.list_tools()
            except Exception:
                await stack.aclose()
                raise
            tools = []
            for tool in tools_result.tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema if hasattr(tool, 'inputSchema') else {},
                    # MCP tool annotations (readOnlyHint / destructiveHint) drive
                    # plan-mode read-only gating. Absent on many servers, so we
                    # fall back to a name heuristic in mcp_tool_is_readonly().
                    "annotations": getattr(tool, 'annotations', None),
                })

            self._sessions[server_id] = session
            self._stacks[server_id] = stack
            self._tools[server_id] = tools
            self._connections[server_id] = {
                "status": "connected",
                "name": name,
                "transport": "sse",
                "tool_count": len(tools),
            }

            logger.info(f"MCP server connected: {name} ({server_id}) - {len(tools)} tools via SSE")
            return True

        except ImportError:
            logger.warning("MCP package not installed. Install with: pip install mcp")
            self._connections[server_id] = {"status": "error", "error": "mcp package not installed", "name": name}
            return False

    async def _start_http_connect(self, server_id: str, name: str, url: str, wait: float = 8.0) -> bool:
        """Begin a Streamable HTTP connect in the background. Returns within
        `wait` seconds: True if it connected (cached-token path), otherwise the
        flow is awaiting browser authorization and status becomes 'needs_auth'."""
        import asyncio
        self._connections[server_id] = {"status": "connecting", "name": name, "transport": "http"}
        task = asyncio.create_task(self._connect_http(server_id, name, url))
        self._connect_tasks[server_id] = task
        done, _ = await asyncio.wait({task}, timeout=wait)
        if task in done:
            try:
                return task.result()
            except Exception as e:
                self._connections[server_id] = {"status": "error", "error": str(e), "name": name}
                return False
        # Still running → either awaiting authorization, or discovery/DCR is
        # still in flight. If _on_redirect already published needs_auth+auth_url,
        # leave it; otherwise mark needs_auth (auth_url filled in once it fires).
        from src.mcp_oauth import pop_auth_url
        cur = self._connections.get(server_id, {})
        if cur.get("status") != "needs_auth":
            self._connections[server_id] = {
                "status": "needs_auth", "name": name, "transport": "http",
                "auth_url": pop_auth_url(server_id),
            }
        return False

    async def _connect_http(self, server_id: str, name: str, url: str) -> bool:
        """Connect to a Streamable HTTP MCP server (with automatic OAuth)."""
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            from contextlib import AsyncExitStack
            from src.mcp_oauth import build_provider, clear_auth_url

            def _on_redirect(auth_url):
                # Publish needs_auth the moment the URL is known, independent of
                # how long discovery/DCR took (may exceed the bounded start wait).
                self._connections[server_id] = {
                    "status": "needs_auth", "name": name, "transport": "http",
                    "auth_url": auth_url,
                }

            provider = build_provider(server_id, url, on_redirect=_on_redirect)
            stack = AsyncExitStack()
            transport = await stack.enter_async_context(streamablehttp_client(url, auth=provider))
            read_stream, write_stream, _get_session_id = transport
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            tools_result = await session.list_tools()
            tools = []
            for tool in tools_result.tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                })

            self._sessions[server_id] = session
            self._stacks[server_id] = stack
            self._tools[server_id] = tools
            self._connections[server_id] = {
                "status": "connected", "name": name, "transport": "http",
                "tool_count": len(tools),
            }
            clear_auth_url(server_id)
            # Tools changed (this can complete after connect_server already
            # returned, via the background OAuth flow), so bump the generation
            # to invalidate the tool-prompt cache.
            self._generation += 1
            logger.info(f"MCP server connected: {name} ({server_id}) - {len(tools)} tools via http")
            return True
        except ImportError:
            logger.warning("MCP package not installed. Install with: pip install mcp")
            self._connections[server_id] = {"status": "error", "error": "mcp package not installed", "name": name}
            return False
        except Exception as e:
            logger.error(f"Failed to connect HTTP MCP server {name} ({server_id}): {e}")
            self._connections[server_id] = {"status": "error", "error": str(e), "name": name}
            return False

    async def disconnect_server(self, server_id: str):
        """Disconnect from an MCP server."""
        existed = any(
            server_id in collection
            for collection in (
                self._connections,
                self._tools,
                self._sessions,
                self._stacks,
                self._connect_tasks,
            )
        )
        # Cancel any in-flight HTTP/OAuth background connect so it stops
        # publishing status for a server that may be getting deleted.
        task = self._connect_tasks.pop(server_id, None)
        if task is not None and not task.done():
            task.cancel()
        try:
            from src.mcp_oauth import clear_auth_url
            clear_auth_url(server_id)
        except Exception:
            pass

        stack = self._stacks.pop(server_id, None)
        if stack:
            try:
                await stack.aclose()
            except Exception as e:
                logger.warning(f"Error closing MCP server {server_id}: {e}")

        self._sessions.pop(server_id, None)
        self._tools.pop(server_id, None)
        self._connections.pop(server_id, None)
        if existed:
            self._generation += 1
        logger.info(f"MCP server disconnected: {server_id}")

    async def disconnect_all(self):
        """Disconnect from all MCP servers."""
        ids = list(self._sessions.keys())
        for sid in ids:
            await self.disconnect_server(sid)

    async def connect_all_enabled(self):
        """Connect to all enabled MCP servers from the database."""
        from src.database import McpServer, SessionLocal

        db = SessionLocal()
        try:
            servers = db.query(McpServer).filter(McpServer.is_enabled == True).all()
            for srv in servers:
                args = json.loads(srv.args) if srv.args else []
                env = json.loads(srv.env) if srv.env else {}
                await self.connect_server(
                    server_id=srv.id,
                    name=srv.name,
                    transport=srv.transport,
                    command=srv.command,
                    args=args,
                    env=env,
                    url=srv.url,
                )
        finally:
            db.close()

    async def call_tool(self, qualified_name: str, arguments: Dict) -> Dict:
        """Call an MCP tool by its qualified name (mcp__{server_id}__{tool_name}).

        Returns a result dict compatible with agent_tools format.
        """
        instrumentation = None
        span = None
        try:
            from src.tool_usage_instrumentation import (
                build_tool_usage_call_metadata_for_name,
                current_bypass_tool_usage_instrumentation,
            )

            instrumentation = current_bypass_tool_usage_instrumentation()
            if instrumentation is not None:
                argument_bytes = len(
                    json.dumps(
                        arguments if isinstance(arguments, dict) else {},
                        sort_keys=True,
                        separators=(",", ":"),
                        default=lambda _value: None,
                    ).encode("utf-8", errors="replace")
                )
                metadata = build_tool_usage_call_metadata_for_name(
                    qualified_name,
                    argument_bytes=argument_bytes,
                )
                span = instrumentation.begin(metadata)
        except Exception:
            span = None
        try:
            result = await self._call_tool_impl(qualified_name, arguments)
        except BaseException as exc:
            if span is not None:
                try:
                    from src.tool_usage_instrumentation import exception_outcome

                    span.finish(exception_outcome(exc))
                except Exception:
                    pass
            raise
        if span is not None:
            try:
                from src.tool_usage_instrumentation import classify_tool_usage_outcome

                error_text = str(result.get("error") or "").casefold()
                description = "mcp: direct"
                if error_text.startswith("invalid mcp tool") or error_text.startswith(
                    "unknown mcp tool"
                ):
                    description = "unknown: mcp"
                elif "blocked" in error_text:
                    description = "mcp: BLOCKED"
                span.finish(
                    classify_tool_usage_outcome(description, result),
                    result=result,
                )
            except Exception:
                pass
        return result

    async def _call_tool_impl(self, qualified_name: str, arguments: Dict) -> Dict:
        parsed_name = parse_qualified_mcp_tool_name(qualified_name)
        if parsed_name is None:
            return {"error": "Invalid MCP tool name", "exit_code": 1}

        server_id, tool_name = parsed_name

        registered_tools = [
            tool for tool in self._tools.get(server_id, []) if tool.get("name") == tool_name
        ]
        if not registered_tools:
            return {"error": f"Unknown MCP tool: {qualified_name}", "exit_code": 1}
        registered_tool = registered_tools[0]
        metadata = _mcp_catalog_metadata(
            server_id,
            registered_tool,
            disabled=False,
            collision=len(registered_tools) != 1,
        )
        if not metadata["catalog_enabled"]:
            return {
                "error": f"MCP tool blocked by catalog normalization: {qualified_name}",
                "exit_code": 1,
            }

        session = self._sessions.get(server_id)
        if not session:
            return {"error": f"MCP server not connected: {server_id}", "exit_code": 1}

        try:
            result = await self._do_call(session, tool_name, arguments)
        except Exception as e:
            # Auto-reconnect for builtin servers whose subprocess may have died
            if self.is_builtin(server_id):
                logger.warning(f"MCP call failed for {qualified_name}, attempting reconnect: {e}")
                reconnected = await self._reconnect_builtin(server_id)
                if reconnected:
                    session = self._sessions.get(server_id)
                    if session:
                        try:
                            result = await self._do_call(session, tool_name, arguments)
                        except Exception as e2:
                            logger.error(f"MCP tool call failed after reconnect: {qualified_name}: {e2}")
                            return {"error": str(e2), "exit_code": 1}
                    else:
                        return {"error": f"Reconnected but no session for {server_id}", "exit_code": 1}
                else:
                    logger.error(f"MCP reconnect failed for {server_id}")
                    return {"error": f"MCP server crashed and reconnect failed: {server_id}", "exit_code": 1}
            else:
                logger.error(f"MCP tool call failed: {qualified_name}: {e}")
                return {"error": str(e), "exit_code": 1}

        return result

    async def _do_call(self, session, tool_name: str, arguments: Dict) -> Dict:
        """Execute a single MCP tool call and return result dict."""
        result = await session.call_tool(tool_name, arguments)
        output_parts = []
        images = []
        for content in result.content:
            if hasattr(content, 'text'):
                output_parts.append(content.text)
            elif getattr(content, 'type', '') == 'image' and hasattr(content, 'data'):
                # Image content (e.g. Playwright screenshots)
                mime = getattr(content, 'mimeType', 'image/png')
                images.append({"data": content.data, "mimeType": mime})
                output_parts.append(f"[Screenshot captured ({mime})]")
            elif hasattr(content, 'data'):
                output_parts.append(str(content.data))

        output = "\n".join(output_parts)
        is_error = getattr(result, 'isError', False)

        result_dict = {
            "stdout": output if not is_error else "",
            "stderr": output if is_error else "",
            "exit_code": 1 if is_error else 0,
        }
        if images:
            result_dict["images"] = images
        return result_dict

    async def _reconnect_builtin(self, server_id: str) -> bool:
        """Tear down and reconnect a crashed builtin MCP server."""
        import sys
        from src.builtin_mcp import _BUILTIN_SERVERS

        if server_id not in _BUILTIN_SERVERS:
            return False

        script_rel, name = _BUILTIN_SERVERS[server_id]
        base_dir = get_app_root()
        script_path = os.path.join(base_dir, script_rel)

        # Clean up old connection
        await self.disconnect_server(server_id)

        try:
            ok = await self.connect_server(
                server_id=server_id,
                name=name,
                transport="stdio",
                command=sys.executable,
                args=[script_path],
                env={"PYTHONPATH": base_dir},
            )
            if ok:
                logger.info(f"Reconnected builtin MCP server: {name}")
            return ok
        except Exception as e:
            logger.error(f"Failed to reconnect builtin MCP server {name}: {e}")
            return False

    def get_all_openai_schemas(self, disabled_map: Optional[Dict[str, set]] = None) -> List[Dict]:
        """Return all MCP tools in OpenAI function-calling format.

        Tool names are namespaced as mcp__{server_id}__{tool_name}.
        disabled_map: optional {server_id: set_of_disabled_tool_names} to filter out.
        """
        schemas = []
        seen: Set[str] = set()
        for server_id, tools in sorted(self._tools.items()):
            # Skip most builtin Python servers; only explicit builtins keep
            # their qualified MCP function-calling surface visible.
            if self.is_builtin(server_id) and server_id not in PROMPT_VISIBLE_BUILTIN_SERVERS:
                continue
            conn = self._connections.get(server_id, {})
            server_name = conn.get("name", server_id)
            disabled = (disabled_map or {}).get(server_id, set())

            identity = conn.get("identity", "")
            label = f"{server_name} ({identity})" if identity else server_name

            for tool in sorted(tools, key=lambda item: str(item.get("name") or "")):
                if tool["name"] in disabled:
                    continue
                metadata = _mcp_catalog_metadata(
                    server_id,
                    tool,
                    disabled=False,
                    collision=name_counts.get(str(tool.get("name") or ""), 0) != 1,
                )
                if not metadata["catalog_enabled"]:
                    continue
                qualified = f"mcp__{server_id}__{tool['name']}"
                if qualified in seen:
                    continue
                seen.add(qualified)
                schema = {
                    "type": "function",
                    "function": {
                        "name": qualified,
                        "description": f"[MCP:{label}] {tool['description']}",
                        "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
                schemas.append(schema)

        return schemas

    def get_all_tools(self, disabled_map: Optional[Dict[str, set]] = None) -> List[Dict]:
        """Return a flat list of all discovered tools with server info."""
        result = []
        seen: Set[str] = set()
        for server_id, tools in sorted(self._tools.items()):
            conn = self._connections.get(server_id, {})
            disabled = (disabled_map or {}).get(server_id, set())
            for tool in sorted(tools, key=lambda item: str(item.get("name") or "")):
                qualified = f"mcp__{server_id}__{tool['name']}"
                if qualified in seen:
                    continue
                seen.add(qualified)
                result.append({
                    "server_id": server_id,
                    "server_name": conn.get("name", server_id),
                    "name": tool["name"],
                    "qualified_name": qualified,
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("input_schema") or {},
                    "is_disabled": is_disabled,
                    "catalog_generation": self._generation,
                }
                row.update(
                    _mcp_catalog_metadata(
                        server_id,
                        tool,
                        disabled=is_disabled,
                        collision=name_counts.get(str(tool.get("name") or ""), 0) != 1,
                    )
                )
                result.append(row)
        return result

    def get_descriptor_projections(
        self,
        disabled_map: Optional[Dict[str, set]] = None,
    ) -> List[Dict[str, Any]]:
        """Return deterministic fail-closed Descriptor-v2 projections for MCP tools."""

        from src.runtime_tool_status import (
            build_dynamic_tool_descriptor,
            canonical_dynamic_tool_id,
        )

        disabled_key = frozenset(
            (str(server_id), frozenset(str(name) for name in names))
            for server_id, names in (disabled_map or {}).items()
        )
        cache_key = (self._generation, disabled_key)
        if self._descriptor_cache_key == cache_key:
            return [dict(row) for row in self._descriptor_cache]

        rows: List[Dict[str, Any]] = []
        for tool in self.get_all_tools(disabled_map):
            runtime_id = str(tool["qualified_name"])
            source_id = str(tool["server_id"] or "mcp-server")
            descriptor = build_dynamic_tool_descriptor(
                runtime_id,
                source="mcp",
                source_id=source_id,
                description=str(tool.get("description") or ""),
            )
            row = descriptor.audit_dict()
            row.update(
                runtime_tool_id=runtime_id,
                server_name=str(tool.get("server_name") or source_id),
                runtime_permission="admin",
                enabled=not bool(tool.get("is_disabled")),
                policy_status=(
                    "disabled_by_mcp_server"
                    if tool.get("is_disabled")
                    else "dynamic_review_required"
                ),
                handler_ref=f"mcp:{canonical_dynamic_tool_id(source_id)}",
                projection_drift=(),
            )
            rows.append(row)
        rows.sort(key=lambda item: str(item["runtime_tool_id"]))
        self._descriptor_cache_key = cache_key
        self._descriptor_cache = tuple(dict(row) for row in rows)
        return [dict(row) for row in rows]

    def plan_mode_blocked_mcp(self) -> Tuple[Dict[str, Set[str]], Set[str]]:
        """Plan mode: block every MCP tool that isn't clearly read-only.

        Returns (disabled_map, qualified_names):
          - disabled_map: {server_id: {tool_name, ...}} to hide write tools from
            the prompt/schemas (merged into the existing mcp_disabled_map).
          - qualified_names: {"mcp__<server>__<tool>", ...} for runtime rejection
            in execute_tool_block (which matches the qualified name).
        """
        disabled_map: Dict[str, Set[str]] = {}
        qualified: Set[str] = set()
        for server_id, tools in self._tools.items():
            name_counts: Dict[str, int] = {}
            for tool in tools:
                name = str(tool.get("name") or "")
                name_counts[name] = name_counts.get(name, 0) + 1
            for tool in tools:
                metadata = _mcp_catalog_metadata(
                    server_id,
                    tool,
                    disabled=False,
                    collision=name_counts.get(str(tool.get("name") or ""), 0) != 1,
                )
                if not metadata["catalog_enabled"] or not mcp_tool_is_readonly(tool):
                    disabled_map.setdefault(server_id, set()).add(tool["name"])
                    qualified.add(f"mcp__{server_id}__{tool['name']}")
        return disabled_map, qualified

    def is_builtin(self, server_id: str) -> bool:
        """Check if a server is a built-in (auto-registered) server."""
        return server_id.startswith("builtin_") or server_id in {
            "image_gen",
            "memory",
            "rag",
            "email",
            "vault",
        }

    def get_server_status(self, server_id: str) -> Dict:
        """Get connection status for a server."""
        return self._connections.get(server_id, {"status": "disconnected"})

    def get_all_statuses(self) -> Dict[str, Dict]:
        """Get connection statuses for all servers."""
        return dict(self._connections)

    _cached_prompt_desc = None
    _cached_prompt_desc_key = None

    def get_tool_descriptions_for_prompt(self, disabled_map: Optional[Dict[str, set]] = None) -> str:
        """Generate text describing MCP tools for the agent system prompt. Cached."""
        cache_key = (
            frozenset((k, frozenset(v)) for k, v in (disabled_map or {}).items()),
            len(self._tools),
            self._generation,
        )
        if self._cached_prompt_desc is not None and self._cached_prompt_desc_key == cache_key:
            return self._cached_prompt_desc
        tools = self.get_all_tools(disabled_map)
        if not tools:
            return ""

        lines = ["\n\nYou also have access to external MCP tool servers. These tools are called via native function calling:"]
        by_server = {}
        for t in tools:
            # Skip most builtin Python servers; only explicit builtins keep
            # their qualified MCP prompt surface visible.
            if self.is_builtin(t["server_id"]) and t["server_id"] not in PROMPT_VISIBLE_BUILTIN_SERVERS:
                continue
            if t.get("is_disabled") or not t.get("catalog_enabled"):
                continue
            sn = t["server_name"]
            if sn not in by_server:
                by_server[sn] = []
            by_server[sn].append(t)

        if not by_server:
            return ""

        for server_name, server_tools in by_server.items():
            # Include identity (e.g. email address) if available
            sid = server_tools[0]["server_id"] if server_tools else ""
            identity = self._connections.get(sid, {}).get("identity", "")
            label = f"{server_name} ({identity})" if identity else server_name
            lines.append(f"\n**{label}:**")
            for t in server_tools:
                # Truncate long descriptions
                desc = t['description'][:120] + '...' if len(t['description']) > 120 else t['description']
                # Include the tool's declared inputs so the model calls it with
                # real argument names instead of guessing from the description
                # alone (issue #2509).
                args_hint = _format_mcp_params(t.get("input_schema"))
                lines.append(f"  - {t['qualified_name']}: {desc}{args_hint}")

        result = "\n".join(lines)
        self._cached_prompt_desc = result
        self._cached_prompt_desc_key = cache_key
        return result

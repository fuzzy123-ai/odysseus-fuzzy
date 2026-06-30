"""MCP admin agent tool implementation and command validator."""

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from src.tool_domains.common import _parse_tool_args
from src.tool_domains.admin_common import _INTERNAL_BASE, _internal_headers

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP server management tool
# ---------------------------------------------------------------------------

# Parallel to routes/cookbook_helpers._validate_serve_cmd but deliberately the
# opposite policy: that gate guards an admin-only serve command and allows
# interpreters (python3/etc) because model-serving needs them, whereas this is
# the model/prompt-injection-reachable manage_mcp path, so interpreters and
# runners are denied here.
#
# Commands that can execute arbitrary code regardless of their arguments. These
# are NEVER accepted on the manage_mcp agent path, even if an operator lists one
# in ODYSSEUS_MCP_ALLOWED_COMMANDS -- a stdio server that genuinely needs an
# interpreter or package runner must be registered via the trusted admin route.
_MCP_DENIED_COMMANDS = frozenset({
    "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "ash", "busybox",
    "cmd", "command.com", "powershell", "pwsh",
    "python", "pypy", "node", "nodejs", "deno", "bun", "ruby", "jruby",
    "perl", "raku", "php", "lua", "luajit", "tclsh", "wish", "expect", "rscript",
    "groovy", "scala", "elixir", "erl", "iex", "java", "javac", "jshell", "jbang",
    "kotlin", "kotlinc", "dotnet", "mono", "swift", "osascript", "tsx", "ts-node",
    "npx", "bunx", "uvx", "pipx", "npm", "pnpm", "yarn", "pip", "uv",
    "gem", "cargo", "go", "bundle", "poetry", "conda", "mamba", "brew",
    "apt", "apt-get", "yum", "dnf", "pacman", "apk",
    "env", "xargs", "nohup", "setsid", "nice", "ionice", "time", "timeout",
    "watch", "stdbuf", "unbuffer", "script", "ssh", "scp", "sshpass", "sudo",
    "doas", "su", "make", "cmake", "docker", "podman", "kubectl", "find",
    "awk", "gawk", "sed", "vi", "vim", "nvim", "emacs", "ed", "tee", "eval",
})

# Argv flags that make even an allowlisted binary execute inline code. Matched
# by prefix so glued forms (-cimport os, --eval=...) are caught, not just the
# exact-token form.
_MCP_CODE_EXEC_SHORT_FLAGS = ("-c", "-e", "-m")
_MCP_CODE_EXEC_LONG_FLAGS = ("--eval", "--exec", "--print", "--module", "--command", "--require")

_MCP_URL_SCHEMES = ("http://", "https://", "ftp://", "ftps://", "file://", "data:", "jar:", "blob:")

# Shell metacharacters refused in command/args. Args are passed as an argv list
# (no shell), but refusing these keeps the surface narrow and obvious.
_MCP_SHELL_METACHARS = set(";|&$`><\n\r")

# Env vars that let a child process load attacker-supplied code before main().
_MCP_DANGEROUS_ENV = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "PYTHONPATH", "PYTHONSTARTUP",
    "PYTHONHOME", "PYTHONEXECUTABLE", "NODE_OPTIONS", "NODE_PATH", "BASH_ENV",
    "ENV", "SHELLOPTS", "PERL5LIB", "PERL5OPT", "RUBYOPT", "RUBYLIB", "GEM_PATH",
    "R_PROFILE", "R_HOME", "PATH", "IFS", "PROMPT_COMMAND",
})


def _mcp_allowed_commands() -> set:
    """Operator-configured allowlist of safe MCP launcher basenames for the agent
    path. Empty by default; set ODYSSEUS_MCP_ALLOWED_COMMANDS (comma-separated)
    to opt specific trusted binaries in. Denied commands are rejected even if
    listed here."""
    raw = os.environ.get("ODYSSEUS_MCP_ALLOWED_COMMANDS", "")
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


def _validate_mcp_command(command, args, env) -> Optional[str]:
    """Validate a model-supplied stdio MCP registration. Returns an error string
    if it must be rejected, else None.

    Closes the RCE where manage_mcp 'add' passed prompt-injection-controlled
    command/args/env straight to a subprocess spawn (issue #438): a payload
    smuggled into a skill description, memory entry, fetched page, or email body
    could register a stdio server running arbitrary code as the app UID.
    """
    if not isinstance(command, str) or not command.strip():
        return "command must be a non-empty string"
    command = command.strip()
    if "/" in command or "\\" in command:
        return "command must be a bare executable name, not a path"
    if any(ch in _MCP_SHELL_METACHARS for ch in command):
        return "command contains shell metacharacters"
    base = command.lower()
    if base.endswith(".exe") or base.endswith(".cmd") or base.endswith(".bat"):
        base = base.rsplit(".", 1)[0]
    # Canonicalize a trailing version suffix so versioned aliases collapse to the
    # family name (python3.11 -> python, node18 -> node, pip3 -> pip); both the
    # raw basename and the canonical form are denied, so an operator cannot
    # accidentally allowlist a runtime alias back into the path.
    canon = re.sub(r"[-_.]?\d+(?:\.\d+)*$", "", base)
    if base in _MCP_DENIED_COMMANDS or canon in _MCP_DENIED_COMMANDS:
        return (
            f"command '{command}' is not allowed on the agent MCP path: "
            "interpreters, runtimes, package runners, and shells can execute "
            "arbitrary code. Register such a server via the admin route instead."
        )
    if base not in _mcp_allowed_commands():
        return (
            f"command '{command}' is not in the MCP allowlist. Add it to "
            "ODYSSEUS_MCP_ALLOWED_COMMANDS if you trust it, or register the "
            "server via the admin route."
        )

    if args is not None:
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                return "args must be a JSON list"
        if not isinstance(args, list):
            return "args must be a list"
        for a in args:
            if not isinstance(a, str):
                return "args must all be strings"
            s = a.strip()
            low = s.lower()
            if any(s == f or s.startswith(f) for f in _MCP_CODE_EXEC_SHORT_FLAGS):
                return f"arg '{a}' is a code-execution flag and is not allowed"
            if any(low == f or low.startswith(f + "=") for f in _MCP_CODE_EXEC_LONG_FLAGS):
                return f"arg '{a}' is a code-execution flag and is not allowed"
            if any(low.startswith(u) for u in _MCP_URL_SCHEMES):
                return f"arg '{a}' is a remote URL and is not allowed"
            if any(ch in _MCP_SHELL_METACHARS for ch in a):
                return f"arg '{a}' contains shell metacharacters"

    if env:
        if isinstance(env, str):
            try:
                env = json.loads(env)
            except Exception:
                return "env must be a JSON object"
        if not isinstance(env, dict):
            return "env must be an object"
        for k in env:
            if str(k).strip().upper() in _MCP_DANGEROUS_ENV:
                return f"env var '{k}' can inject code into the child process and is not allowed"

    return None


async def do_manage_mcp(content: str, owner: Optional[str] = None) -> Dict:
    """Manage MCP servers: list, add, delete, enable, disable, reconnect."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"MCP server {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _json_text(value: Any, fallback: str) -> str:
        if value in (None, ""):
            return fallback
        if isinstance(value, str):
            return value
        return json.dumps(value)

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"MCP route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _safe_server_item(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "transport": item.get("transport"),
            "is_enabled": item.get("is_enabled"),
            "status": item.get("status"),
            "tool_count": item.get("tool_count", 0),
            "disabled_tool_count": item.get("disabled_tool_count", 0),
            "enabled_tool_count": item.get("enabled_tool_count"),
            "needs_oauth": bool(item.get("needs_oauth")),
            "needs_auth": bool(item.get("needs_auth")),
            "has_oauth": bool(item.get("has_oauth")),
            "error": item.get("error"),
        }

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/mcp/servers", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            servers = [_safe_server_item(item) for item in (resp.json() or [])]
            return {"response": f"{len(servers)} MCP servers", "servers": servers, "exit_code": 0}

        if action == "add":
            name = str(args.get("name") or "").strip()
            transport = str(args.get("transport") or "stdio").strip().lower()
            command = str(args.get("command") or "").strip()
            cmd_args = args.get("args", [])
            env = args.get("env", {})
            url = str(args.get("url") or "").strip()
            if not name:
                return {"error": "name is required", "exit_code": 1}
            if transport == "stdio":
                if not command:
                    return {"error": "command is required for stdio transport", "exit_code": 1}
                # Validate BEFORE route call: the trusted admin route may allow
                # host executables, but the agent path must keep its allowlist.
                _mcp_err = _validate_mcp_command(command, cmd_args, env)
                if _mcp_err:
                    return {"error": f"manage_mcp: refused unsafe server registration: {_mcp_err}", "exit_code": 1}
            elif transport in {"sse", "http"}:
                if not url:
                    return {"error": "url is required for sse/http transport", "exit_code": 1}
            else:
                return {"error": "transport must be stdio, sse, or http", "exit_code": 1}
            if not _confirmed():
                return _confirmation_required("add")
            data = {
                "name": name,
                "transport": transport,
                "command": command,
                "args": _json_text(cmd_args, "[]"),
                "env": _json_text(env, "{}"),
                "url": url,
            }
            if "oauth_config" in args:
                data["oauth_config"] = _json_text(args.get("oauth_config"), "")
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/mcp/servers", data=data, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            server = resp.json() or {}
            return {
                "response": f"Added MCP server '{server.get('name') or name}' ({server.get('tool_count', 0)} tools)",
                "server": server,
                "exit_code": 0,
            }

        if action == "delete":
            if not _confirmed():
                return _confirmation_required("delete")
            sid = args.get("server_id", "")
            if not sid:
                return {"error": "server_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/mcp/servers/{sid}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Deleted MCP server {sid}", "result": resp.json() or {}, "exit_code": 0}

        if action == "reconnect":
            if not _confirmed():
                return _confirmation_required("reconnect")
            sid = args.get("server_id", "")
            if not sid:
                return {"error": "server_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/mcp/servers/{sid}/reconnect", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {
                "response": f"Reconnected MCP server {sid} ({result.get('tool_count', 0)} tools)",
                "result": result,
                "exit_code": 0,
            }

        if action in ("enable", "disable"):
            if not _confirmed():
                return _confirmation_required(action)
            sid = args.get("server_id", "")
            if not sid:
                return {"error": "server_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(
                    f"{_INTERNAL_BASE}/api/mcp/servers/{sid}",
                    data={"is_enabled": "true" if action == "enable" else "false"},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"MCP server {sid} {action}d", "server": resp.json() or {}, "exit_code": 0}

        if action == "list_tools":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/mcp/tools", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            tools = resp.json() or []
            items = [
                {
                    "name": t.get("name"),
                    "server": t.get("server_name") or t.get("server"),
                    "description": str(t.get("description") or "")[:100],
                }
                for t in tools
            ]
            return {"response": f"{len(items)} MCP tools available", "tools": items, "exit_code": 0}

        return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_mcp error: {e}")
        return {"error": str(e), "exit_code": 1}



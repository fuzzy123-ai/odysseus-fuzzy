"""Admin and configuration tool implementations."""

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from core.constants import internal_api_base
from src.tool_domains.common import _parse_tool_args

logger = logging.getLogger(__name__)
_INTERNAL_BASE = internal_api_base()


def _internal_headers(owner: Optional[str] = None) -> Dict[str, str]:
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN

    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    if owner:
        headers["X-Odysseus-Owner"] = owner
    return headers


# ---------------------------------------------------------------------------
# Task management tool
# ---------------------------------------------------------------------------

async def do_manage_tasks(content: str, owner: Optional[str] = None) -> Dict:
    """Handle manage_tasks tool calls: CRUD on scheduled tasks."""
    import uuid as _uuid
    from core.database import SessionLocal, ScheduledTask
    from src.task_scheduler import compute_next_run

    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Task {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    db = SessionLocal()
    try:
        if action == "list":
            q = db.query(ScheduledTask)
            if owner:
                q = q.filter(ScheduledTask.owner == owner)
            tasks = q.order_by(ScheduledTask.created_at.desc()).all()
            task_list = []
            for t in tasks:
                task_list.append({
                    "id": t.id, "name": t.name, "status": t.status,
                    "task_type": t.task_type or "llm",
                    "action": t.action,
                    "trigger_type": t.trigger_type or "schedule",
                    "schedule": t.schedule,
                    "trigger_event": t.trigger_event,
                    "trigger_count": t.trigger_count,
                    "next_run": t.next_run.isoformat() + "Z" if t.next_run else None,
                    "last_run": t.last_run.isoformat() + "Z" if t.last_run else None,
                    "run_count": t.run_count or 0,
                })
            return {"response": f"Found {len(task_list)} tasks", "tasks": task_list, "exit_code": 0}

        elif action == "create":
            task_type = args.get("task_type", "llm")
            trigger_type = args.get("trigger_type", "schedule")

            if task_type in ("llm", "research") and not args.get("prompt"):
                return {"error": "Prompt is required for llm/research tasks", "exit_code": 1}
            if task_type == "action" and not args.get("action_name"):
                return {"error": "action_name is required for action tasks", "exit_code": 1}

            # Compute next_run for schedule triggers
            next_run = None
            if trigger_type == "schedule":
                schedule = args.get("schedule", "daily")
                next_run = compute_next_run(
                    schedule, args.get("scheduled_time", "09:00"),
                    args.get("scheduled_day"),
                )

            task_id = str(_uuid.uuid4())
            # Guard each fallback with `or`: args.get("prompt", default) returns
            # None when the key is present but null, and None[:50] raises.
            name = args.get("name") or (args.get("prompt") or args.get("action_name") or "Task")[:50]

            task = ScheduledTask(
                id=task_id,
                owner=owner,
                name=name,
                prompt=args.get("prompt"),
                task_type=task_type,
                action=args.get("action_name"),
                schedule=args.get("schedule") if trigger_type == "schedule" else None,
                scheduled_time=args.get("scheduled_time", "09:00") if trigger_type == "schedule" else None,
                scheduled_day=args.get("scheduled_day"),
                trigger_type=trigger_type,
                trigger_event=args.get("trigger_event"),
                trigger_count=args.get("trigger_count"),
                trigger_counter=0,
                next_run=next_run,
                status="active",
                output_target=args.get("output_target", "session"),
            )
            db.add(task)
            db.commit()
            return {"response": f"Created task '{name}' (id: {task_id})", "task_id": task_id, "exit_code": 0}

        elif action == "edit":
            task_id = args.get("task_id")
            if not task_id:
                return {"error": "task_id is required for edit", "exit_code": 1}
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}

            changed = []
            for field in ("name", "prompt", "output_target"):
                if args.get(field) is not None:
                    setattr(task, field, args[field])
                    changed.append(field)
            if args.get("task_type") is not None:
                task.task_type = args["task_type"]
                changed.append("task_type")
            if args.get("action_name") is not None:
                task.action = args["action_name"]
                changed.append("action")
            if args.get("trigger_type") is not None:
                task.trigger_type = args["trigger_type"]
                changed.append("trigger_type")
            if args.get("trigger_event") is not None:
                task.trigger_event = args["trigger_event"]
                changed.append("trigger_event")
            if args.get("trigger_count") is not None:
                task.trigger_count = args["trigger_count"]
                changed.append("trigger_count")

            schedule_changed = False
            for field in ("schedule", "scheduled_time", "scheduled_day"):
                if args.get(field) is not None:
                    setattr(task, field, args[field])
                    changed.append(field)
                    schedule_changed = True

            if schedule_changed and (task.trigger_type or "schedule") == "schedule":
                task.next_run = compute_next_run(
                    task.schedule, task.scheduled_time, task.scheduled_day,
                )

            db.commit()
            return {"response": f"Updated task '{task.name}': {', '.join(changed)}", "exit_code": 0}

        elif action == "delete":
            task_id = args.get("task_id")
            if not task_id:
                return {"error": "task_id is required for delete", "exit_code": 1}
            if not _confirmed():
                return _confirmation_required("delete")
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}
            name = task.name
            db.delete(task)
            db.commit()
            return {"response": f"Deleted task '{name}'", "exit_code": 0}

        elif action in ("pause", "resume"):
            task_id = args.get("task_id")
            if not task_id:
                return {"error": f"task_id is required for {action}", "exit_code": 1}
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}

            if action == "pause":
                task.status = "paused"
            else:
                task.status = "active"
                if (task.trigger_type or "schedule") == "schedule":
                    task.next_run = compute_next_run(
                        task.schedule, task.scheduled_time, task.scheduled_day,
                    )
            db.commit()
            return {"response": f"Task '{task.name}' {action}d", "exit_code": 0}

        elif action == "run":
            task_id = args.get("task_id")
            if not task_id:
                return {"error": "task_id is required for run", "exit_code": 1}
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}

            from src.event_bus import get_task_scheduler
            scheduler = get_task_scheduler()
            if scheduler:
                started = await scheduler.run_task_now(task_id)
                if started:
                    return {"response": f"Task '{task.name}' triggered", "exit_code": 0}
                else:
                    return {"error": "Task is already running", "exit_code": 1}
            return {"error": "Task scheduler not available", "exit_code": 1}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}

    except Exception as e:
        logger.error(f"manage_tasks error: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoint management tool
# ---------------------------------------------------------------------------

async def do_manage_endpoints(content: str, owner: Optional[str] = None) -> Dict:
    """Manage model endpoints through the same admin routes as the UI."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Confirmation required before endpoint {target}. Repeat with confirmed=true after explicit user confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "action": action,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Endpoint route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/model-endpoints", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            items = resp.json() or []
            return {"response": f"{len(items)} endpoints", "endpoints": items, "exit_code": 0}

        elif action == "add":
            name = args.get("name", "")
            base_url = args.get("base_url", "")
            if not base_url:
                return {"error": "base_url is required", "exit_code": 1}
            if args.get("api_key"):
                return {
                    "response": "Endpoint API keys must be entered through secure UI handoff, not chat text.",
                    "status": "secret_handoff_required",
                    "secret_handoff_required": True,
                    "exit_code": 0,
                }
            if not _confirmed():
                return _confirmation_required("add")
            pinned_models = args.get("pinned_models", "")
            if isinstance(pinned_models, (list, dict)):
                pinned_models = json.dumps(pinned_models)
            data = {
                "name": name,
                "base_url": base_url,
                "skip_probe": str(args.get("skip_probe", "false")).lower(),
                "require_models": str(args.get("require_models", "false")).lower(),
                "model_type": args.get("model_type", "llm"),
                "endpoint_kind": args.get("endpoint_kind", "auto"),
                "model_refresh_mode": args.get("model_refresh_mode", ""),
                "model_refresh_interval": str(args.get("model_refresh_interval", "")),
                "model_refresh_timeout": str(args.get("model_refresh_timeout", "")),
                "supports_tools": "" if args.get("supports_tools") is None else str(args.get("supports_tools")).lower(),
                "pinned_models": pinned_models,
                "container_local": str(args.get("container_local", "false")).lower(),
                "shared": str(args.get("shared", "true")).lower(),
            }
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/model-endpoints", data=data, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            endpoint = resp.json() or {}
            return {
                "response": f"Added endpoint '{endpoint.get('name') or name or base_url}' (id: {endpoint.get('id')}).",
                "endpoint": endpoint,
                "exit_code": 0,
            }

        elif action == "delete":
            eid = args.get("endpoint_id", "")
            if not eid:
                return {"error": "endpoint_id is required", "exit_code": 1}
            if not _confirmed():
                return _confirmation_required("delete")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/model-endpoints/{eid}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Deleted endpoint {eid}", "result": result, "exit_code": 0}

        elif action in ("enable", "disable", "update"):
            eid = args.get("endpoint_id", "")
            if not eid:
                return {"error": "endpoint_id is required", "exit_code": 1}
            body: Dict[str, Any] = {}
            if action in ("enable", "disable"):
                body["is_enabled"] = action == "enable"
            else:
                for field in (
                    "name",
                    "base_url",
                    "model_type",
                    "pinned_models",
                    "endpoint_kind",
                    "model_refresh_mode",
                    "model_refresh_interval",
                    "model_refresh_timeout",
                    "supports_tools",
                ):
                    if field in args:
                        body[field] = args[field]
                if args.get("api_key"):
                    return {
                        "response": "Endpoint API keys must be rotated through secure UI handoff, not chat text.",
                        "status": "secret_handoff_required",
                        "secret_handoff_required": True,
                        "exit_code": 0,
                    }
                if not body:
                    return {"error": "No update fields supplied", "exit_code": 1}
            if not _confirmed():
                return _confirmation_required(action)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(f"{_INTERNAL_BASE}/api/model-endpoints/{eid}", json=body, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            endpoint = resp.json() or {}
            return {
                "response": f"Endpoint '{endpoint.get('name') or eid}' updated.",
                "endpoint": endpoint,
                "exit_code": 0,
            }

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_endpoints error: {e}")
        return {"error": str(e), "exit_code": 1}


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


# ---------------------------------------------------------------------------
# Webhook management tool
# ---------------------------------------------------------------------------

async def do_manage_webhooks(content: str, owner: Optional[str] = None) -> Dict:
    """Manage webhooks through admin routes with confirmation for mutations."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Webhook {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Webhook route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _mask_webhook(item: Dict[str, Any]) -> Dict[str, Any]:
        safe = dict(item)
        url = str(safe.get("url") or "")
        if url:
            try:
                from urllib.parse import urlparse

                parsed = urlparse(url)
                safe["url"] = f"{parsed.scheme}://{parsed.netloc}/..." if parsed.scheme and parsed.netloc else "(configured)"
            except Exception:
                safe["url"] = "(configured)"
        safe["has_url"] = bool(url)
        return safe

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/webhooks", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            hooks = [_mask_webhook(item) for item in (resp.json() or [])]
            return {"response": f"{len(hooks)} webhooks", "webhooks": hooks, "exit_code": 0}

        elif action == "add":
            if not _confirmed():
                return _confirmation_required("add")
            name = args.get("name", "")
            url = args.get("url", "")
            events = args.get("events", "chat.completed")
            if not url:
                return {"error": "url is required", "exit_code": 1}
            data = {"name": name or "Webhook", "url": url, "events": events}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/webhooks", data=data, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            webhook = resp.json() or {}
            return {
                "response": f"Added webhook '{webhook.get('name') or name or 'Webhook'}'.",
                "webhook": webhook,
                "exit_code": 0,
            }

        elif action == "delete":
            if not _confirmed():
                return _confirmation_required("delete")
            wid = args.get("webhook_id", "")
            if not wid:
                return {"error": "webhook_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/webhooks/{wid}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Deleted webhook {wid}", "result": resp.json() or {}, "exit_code": 0}

        elif action == "test":
            if not _confirmed():
                return _confirmation_required("test")
            wid = args.get("webhook_id", "")
            if not wid:
                return {"error": "webhook_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/webhooks/{wid}/test", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {
                "response": f"Sent test event for webhook {wid}.",
                "result": resp.json() or {},
                "exit_code": 0,
            }

        elif action in ("enable", "disable"):
            if not _confirmed():
                return _confirmation_required(action)
            wid = args.get("webhook_id", "")
            if not wid:
                return {"error": "webhook_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                listed = await client.get(f"{_INTERNAL_BASE}/api/webhooks", headers=headers)
                if listed.status_code >= 400:
                    return _error_from_response(listed)
                hooks = listed.json() or []
                current = next((item for item in hooks if item.get("id") == wid), None)
                if not current:
                    return {"error": f"Webhook {wid} not found", "exit_code": 1}
                desired = action == "enable"
                if bool(current.get("is_active")) != desired:
                    resp = await client.patch(f"{_INTERNAL_BASE}/api/webhooks/{wid}", headers=headers)
                    if resp.status_code >= 400:
                        return _error_from_response(resp)
                    current = {**current, **(resp.json() or {})}
            return {
                "response": f"Webhook '{current.get('name') or wid}' {'enabled' if desired else 'disabled'}.",
                "webhook": _mask_webhook(current),
                "exit_code": 0,
            }

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_webhooks error: {e}")
        return {"error": str(e), "exit_code": 1}


# ---------------------------------------------------------------------------
# Preset management tool
# ---------------------------------------------------------------------------

async def do_manage_presets(content: str, owner: Optional[str] = None) -> Dict:
    """Manage chat/persona presets through the same routes as the UI."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Preset {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Preset route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/presets", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            presets = resp.json() or {}
            return {"response": f"{len(presets)} presets", "presets": presets, "exit_code": 0}

        elif action in ("templates", "list_templates"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/presets/templates", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            templates = resp.json() or []
            return {"response": f"{len(templates)} preset templates", "templates": templates, "exit_code": 0}

        elif action in ("groups", "list_groups"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/presets/groups", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            groups = resp.json() or {}
            count = len(groups.get("groups") or []) if isinstance(groups, dict) else 0
            return {"response": f"{count} preset groups", "groups": groups, "exit_code": 0}

        elif action in ("update_custom", "custom"):
            if not _confirmed():
                return _confirmation_required("update_custom")
            body = {
                "name": args.get("name", ""),
                "enabled": bool(args.get("enabled", True)),
                "temperature": args.get("temperature", 1.0),
                "max_tokens": args.get("max_tokens", 0),
                "system_prompt": args.get("system_prompt", ""),
                "inject_prefix": args.get("inject_prefix", ""),
                "inject_suffix": args.get("inject_suffix", ""),
            }
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/presets/custom", json=body, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": "Updated custom preset.", "result": resp.json() or {}, "exit_code": 0}

        elif action in ("save_template", "template"):
            if not _confirmed():
                return _confirmation_required("save_template")
            name = str(args.get("name") or "").strip()
            if not name:
                return {"error": "name is required", "exit_code": 1}
            body = {
                "id": args.get("template_id") or args.get("id") or "",
                "name": name,
                "system_prompt": args.get("system_prompt", ""),
                "temperature": args.get("temperature", 1.0),
                "max_tokens": args.get("max_tokens", 0),
            }
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/presets/templates", json=body, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Saved preset template '{name}'.", "result": resp.json() or {}, "exit_code": 0}

        elif action == "delete_template":
            if not _confirmed():
                return _confirmation_required("delete_template")
            template_id = str(args.get("template_id") or args.get("id") or "").strip()
            if not template_id:
                return {"error": "template_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/presets/templates/{template_id}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Deleted preset template {template_id}.", "result": resp.json() or {}, "exit_code": 0}

        elif action == "save_groups":
            if not _confirmed():
                return _confirmation_required("save_groups")
            groups = args.get("groups")
            if not isinstance(groups, list):
                return {"error": "groups must be a list", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/presets/groups", json={"groups": groups}, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Saved {len(groups)} preset group(s).", "result": resp.json() or {}, "exit_code": 0}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_presets error: {e}")
        return {"error": str(e), "exit_code": 1}

# ---------------------------------------------------------------------------
# Personal document/RAG source management tool
# ---------------------------------------------------------------------------

async def do_manage_personal_docs(content: str, owner: Optional[str] = None) -> Dict:
    """Manage personal document/RAG sources through the same routes as the UI."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Personal document {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Personal docs route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _directory_arg() -> str:
        return str(args.get("directory") or args.get("path") or "").strip()

    def _filepath_arg() -> str:
        return str(args.get("filepath") or args.get("file_path") or args.get("path") or "").strip()

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/personal", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            files = data.get("files") or []
            directories = data.get("directories") or []
            return {
                "response": f"{len(files)} personal document(s), {len(directories)} indexed source dir(s)",
                "personal_docs": data,
                "exit_code": 0,
            }

        elif action == "reload":
            if not _confirmed():
                return _confirmation_required("reload")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/personal/reload", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {
                "response": f"Reloaded personal documents ({result.get('count', 0)} indexed item(s)).",
                "result": result,
                "exit_code": 0,
            }

        elif action in ("add_directory", "add"):
            if not _confirmed():
                return _confirmation_required("add_directory")
            directory = _directory_arg()
            if not directory:
                return {"error": "directory is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{_INTERNAL_BASE}/api/personal/add_directory",
                    json={"directory": directory},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {
                "response": result.get("message") or f"Added personal directory {directory}.",
                "result": result,
                "exit_code": 0,
            }

        elif action in ("remove_directory", "remove"):
            if not _confirmed():
                return _confirmation_required("remove_directory")
            directory = _directory_arg()
            if not directory:
                return {"error": "directory is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.delete(
                    f"{_INTERNAL_BASE}/api/personal/remove_directory",
                    params={"directory": directory},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {
                "response": result.get("message") or f"Removed personal directory {directory}.",
                "result": result,
                "exit_code": 0,
            }

        elif action in ("delete_file", "delete"):
            if not _confirmed():
                return _confirmation_required("delete_file")
            filepath = _filepath_arg()
            if not filepath:
                return {"error": "filepath is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.delete(
                    f"{_INTERNAL_BASE}/api/personal/file",
                    params={"filepath": filepath},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {
                "response": f"Deleted/excluded personal document {filepath}.",
                "result": result,
                "exit_code": 0,
            }

        elif action == "upload":
            return {
                "error": "Uploading files stays UI-only for now; use the Personal Docs UI so multipart bytes and owner scope stay bounded.",
                "exit_code": 1,
            }

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_personal_docs error: {e}")
        return {"error": str(e), "exit_code": 1}

# ---------------------------------------------------------------------------
# Embedding model/endpoint management tool
# ---------------------------------------------------------------------------

async def do_manage_embeddings(content: str, owner: Optional[str] = None) -> Dict:
    """Manage embedding models through admin routes with confirmation for mutations."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Embedding {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Embedding route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _model_name() -> str:
        return str(args.get("model_name") or args.get("model") or "").strip()

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/embeddings/models", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            models = resp.json() or []
            return {"response": f"{len(models)} embedding model(s)", "models": models, "exit_code": 0}

        elif action == "status":
            model_name = _model_name()
            if not model_name:
                return {"error": "model_name is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{_INTERNAL_BASE}/api/embeddings/models/{model_name}/status",
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            status = resp.json() or {}
            return {"response": f"Embedding model {model_name}: {status}", "status": status, "exit_code": 0}

        elif action in ("endpoint", "get_endpoint"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/embeddings/endpoint", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            endpoint = resp.json() or {}
            return {"response": f"Embedding endpoint active={bool(endpoint.get('active'))}", "endpoint": endpoint, "exit_code": 0}

        elif action in ("download", "download_model"):
            if not _confirmed():
                return _confirmation_required("download")
            model_name = _model_name()
            if not model_name:
                return {"error": "model_name is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(
                    f"{_INTERNAL_BASE}/api/embeddings/models/{model_name}/download",
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Embedding model download requested for {model_name}.", "result": result, "exit_code": 0}

        elif action in ("delete", "delete_model"):
            if not _confirmed():
                return _confirmation_required("delete")
            model_name = _model_name()
            if not model_name:
                return {"error": "model_name is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.delete(
                    f"{_INTERNAL_BASE}/api/embeddings/models/{model_name}",
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Deleted embedding model cache for {model_name}.", "result": result, "exit_code": 0}

        elif action in ("clear_endpoint", "delete_endpoint"):
            if not _confirmed():
                return _confirmation_required("clear_endpoint")
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/embeddings/endpoint", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": "Cleared embedding endpoint; local FastEmbed will be used.", "result": result, "exit_code": 0}

        elif action in ("set_endpoint", "update_endpoint"):
            return {
                "error": "Setting embedding endpoints stays UI/secure-handoff-only for now because it performs a live health check and may require an API key.",
                "exit_code": 1,
            }

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_embeddings error: {e}")
        return {"error": str(e), "exit_code": 1}


# ---------------------------------------------------------------------------
# Personal assistant management tool
# ---------------------------------------------------------------------------

async def do_manage_assistant(content: str, owner: Optional[str] = None) -> Dict:
    """Manage the per-user assistant through the same routes as the UI."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "settings") or "settings").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Assistant {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Assistant route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _task_id() -> str:
        return str(args.get("task_id") or args.get("id") or "").strip()

    try:
        import httpx

        headers = _internal_headers(owner=owner)

        if action == "session":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/assistant/session", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            session = resp.json() or {}
            return {
                "response": f"Assistant session {session.get('session_id') or '(unknown)'}",
                "session": session,
                "exit_code": 0,
            }

        elif action in ("settings", "get", "list"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/assistant/settings", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            settings = resp.json() or {}
            check_ins = settings.get("check_ins") or []
            crew = settings.get("crew") or {}
            return {
                "response": f"Assistant settings for {crew.get('name') or 'assistant'} with {len(check_ins)} check-in(s)",
                "assistant": settings,
                "exit_code": 0,
            }

        elif action == "timezones":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/assistant/available-timezones", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            zones = resp.json() or {}
            return {
                "response": f"{len(zones.get('timezones') or [])} available timezones",
                "timezones": zones.get("timezones") or [],
                "exit_code": 0,
            }

        elif action in ("run_status", "status"):
            task_id = _task_id()
            if not task_id:
                return {"error": "task_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/assistant/run-status/{task_id}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            status = resp.json() or {}
            return {"response": f"Assistant task {task_id}: {status.get('status')}", "status": status, "exit_code": 0}

        elif action == "update":
            if not _confirmed():
                return _confirmation_required("update")
            if "endpoint_url" in args:
                return {
                    "error": (
                        "endpoint_url stays UI/manage_endpoints-only for now; "
                        "assistant endpoint changes must use the UI or endpoint management flow."
                    ),
                    "exit_code": 1,
                }
            allowed = (
                "name", "avatar", "personality", "model", "enabled_tools",
                "allow_autonomous_email", "timezone", "check_ins",
            )
            body = {key: args[key] for key in allowed if key in args}
            if "check_ins" in body and not isinstance(body["check_ins"], list):
                return {"error": "check_ins must be a list", "exit_code": 1}
            if "enabled_tools" in body and not isinstance(body["enabled_tools"], list):
                return {"error": "enabled_tools must be a list", "exit_code": 1}
            if not body:
                return {"error": "No assistant settings fields supplied", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(
                    f"{_INTERNAL_BASE}/api/assistant/settings",
                    json=body,
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            settings = resp.json() or {}
            return {"response": "Updated assistant settings.", "assistant": settings, "exit_code": 0}

        elif action == "run":
            if not _confirmed():
                return _confirmation_required("run")
            task_id = _task_id()
            if not task_id:
                return {"error": "task_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/assistant/run/{task_id}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Assistant check-in {task_id} started={bool(result.get('started'))}.", "result": result, "exit_code": 0}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_assistant error: {e}")
        return {"error": str(e), "exit_code": 1}


# ---------------------------------------------------------------------------
# Plugin management tool
# ---------------------------------------------------------------------------

async def do_manage_plugins(content: str, owner: Optional[str] = None) -> Dict:
    """Manage plugins through admin plugin routes with confirmation for mutations."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Plugin {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Plugin route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    def _plugin_id() -> str:
        return str(args.get("plugin_id") or args.get("id") or "").strip()

    def _valid_plugin_id(plugin_id: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}", plugin_id or ""))

    def _registry_url() -> str:
        return str(args.get("url") or args.get("registry_url") or "").strip()

    try:
        import httpx

        headers = _internal_headers(owner=owner)

        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/plugins", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            plugins = data.get("plugins") or []
            return {"response": f"{len(plugins)} plugin(s)", "plugins": plugins, "exit_code": 0}

        elif action == "registry":
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/plugins/registry", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            plugins = data.get("plugins") or []
            return {"response": f"{len(plugins)} registry plugin(s)", "registry": data, "exit_code": 0}

        elif action in ("registries", "list_registries"):
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/plugins/registries", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            registries = data.get("registries") or []
            return {"response": f"{len(registries)} plugin registries", "registries": data, "exit_code": 0}

        elif action == "status":
            plugin_id = _plugin_id()
            if not _valid_plugin_id(plugin_id):
                return {"error": "valid plugin_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/plugins/{plugin_id}/status", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            status = resp.json() or {}
            return {"response": f"Plugin {plugin_id} status", "status": status, "exit_code": 0}

        elif action in ("enable", "disable", "reload"):
            if not _confirmed():
                return _confirmation_required(action)
            plugin_id = _plugin_id()
            if not _valid_plugin_id(plugin_id):
                return {"error": "valid plugin_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/plugins/{plugin_id}/{action}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Plugin {plugin_id} {action} complete.", "plugin": result, "exit_code": 0}

        elif action == "rescan":
            if not _confirmed():
                return _confirmation_required("rescan")
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/plugins/rescan", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            plugins = data.get("plugins") or []
            return {"response": f"Rescanned plugins ({len(plugins)} discovered).", "plugins": plugins, "exit_code": 0}

        elif action == "install":
            if not _confirmed():
                return _confirmation_required("install")
            if args.get("url"):
                return {
                    "error": (
                        "Direct plugin ZIP URL installs stay Plugins UI-only for now. "
                        "Use manage_plugins install with a registry plugin id."
                    ),
                    "exit_code": 1,
                }
            plugin_id = _plugin_id()
            if not _valid_plugin_id(plugin_id):
                return {"error": "valid plugin_id or id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{_INTERNAL_BASE}/api/plugins/install",
                    json={"id": plugin_id},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Installed plugin {plugin_id}.", "plugin": result, "exit_code": 0}

        elif action == "uninstall":
            if not _confirmed():
                return _confirmation_required("uninstall")
            plugin_id = _plugin_id()
            if not _valid_plugin_id(plugin_id):
                return {"error": "valid plugin_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/plugins/{plugin_id}/uninstall", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            result = resp.json() or {}
            return {"response": f"Uninstalled plugin {plugin_id}.", "result": result, "exit_code": 0}

        elif action in ("add_registry", "add_registries"):
            if not _confirmed():
                return _confirmation_required("add_registry")
            url = _registry_url()
            if not url:
                return {"error": "url is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{_INTERNAL_BASE}/api/plugins/registries",
                    json={"url": url},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            return {"response": "Added plugin registry.", "registries": data.get("registries") or data, "exit_code": 0}

        elif action in ("remove_registry", "delete_registry"):
            if not _confirmed():
                return _confirmation_required("remove_registry")
            url = _registry_url()
            if not url:
                return {"error": "url is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(
                    "DELETE",
                    f"{_INTERNAL_BASE}/api/plugins/registries",
                    json={"url": url},
                    headers=headers,
                )
            if resp.status_code >= 400:
                return _error_from_response(resp)
            data = resp.json() or {}
            return {"response": "Removed plugin registry.", "registries": data.get("registries") or data, "exit_code": 0}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_plugins error: {e}")
        return {"error": str(e), "exit_code": 1}

# ---------------------------------------------------------------------------
# API token management tool
# ---------------------------------------------------------------------------

async def do_manage_tokens(content: str, owner: Optional[str] = None) -> Dict:
    """Manage API tokens through the same admin routes as the UI/API."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"API token {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _error_from_response(resp) -> Dict:
        try:
            data = resp.json()
        except Exception:
            data = {}
        detail = data.get("detail") if isinstance(data, dict) else None
        return {
            "error": detail or getattr(resp, "text", "") or f"Token route returned HTTP {resp.status_code}",
            "status_code": resp.status_code,
            "exit_code": 1,
        }

    try:
        import httpx

        headers = _internal_headers(owner=owner)
        if action == "list":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/tokens", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            items = resp.json() or []
            return {"response": f"{len(items)} API tokens", "tokens": items, "exit_code": 0}

        elif action == "create":
            if not _confirmed():
                return _confirmation_required("create")
            name = args.get("name", "API Token")
            data = {"name": name}
            if args.get("scopes") is not None:
                scopes = args.get("scopes")
                data["scopes"] = ",".join(scopes) if isinstance(scopes, list) else str(scopes)
            if args.get("profile") is not None:
                data["profile"] = str(args.get("profile"))
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{_INTERNAL_BASE}/api/tokens", data=data, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            token = resp.json() or {}
            return {
                "response": f"Created token '{token.get('name') or name}'. Store the token now; it will not be shown again.",
                "token": token.get("token"),
                "token_meta": {k: v for k, v in token.items() if k != "token"},
                "exit_code": 0,
            }

        elif action in ("update", "rename"):
            if not _confirmed():
                return _confirmation_required(action)
            tid = args.get("token_id", "")
            if not tid:
                return {"error": "token_id is required", "exit_code": 1}
            body: Dict[str, Any] = {}
            if "name" in args:
                body["name"] = args.get("name")
            if args.get("scopes") is not None:
                body["scopes"] = args.get("scopes")
            if not body:
                return {"error": "name or scopes is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(f"{_INTERNAL_BASE}/api/tokens/{tid}", json=body, headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            token = resp.json() or {}
            return {
                "response": f"Updated token '{token.get('name') or tid}'.",
                "token_meta": token,
                "exit_code": 0,
            }

        elif action == "delete":
            if not _confirmed():
                return _confirmation_required("delete")
            tid = args.get("token_id", "")
            if not tid:
                return {"error": "token_id is required", "exit_code": 1}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(f"{_INTERNAL_BASE}/api/tokens/{tid}", headers=headers)
            if resp.status_code >= 400:
                return _error_from_response(resp)
            return {"response": f"Deleted token {tid}", "result": resp.json() or {}, "exit_code": 0}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_tokens error: {e}")
        return {"error": str(e), "exit_code": 1}

# ---------------------------------------------------------------------------
# Settings/preferences management tool
# ---------------------------------------------------------------------------


def _manage_settings_v2(args: Dict[str, Any], owner: Optional[str] = None) -> Dict:
    """Service-backed manage_settings implementation.

    The public tool response stays legacy-friendly (`response`, `value`,
    `exit_code`) while the new `setting` payload carries machine-readable
    policy and scope details for agent self-control flows.
    """

    action = str(args.get("action", "list") or "list").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _scope() -> str:
        return str(args.get("scope") or "auto")

    def _store() -> str:
        raw = str(args.get("store") or args.get("source") or "").strip().lower()
        return "feature" if raw in {"feature", "features", "flag", "flags"} else "setting"

    def _display_value(value: Any, visible: bool = True) -> Any:
        return value if visible else "***** (secure handoff required)"

    def _policy_response(result: Dict[str, Any]) -> Dict:
        status = str(result.get("status") or "blocked")
        return {
            "response": str(result.get("reason") or status),
            "status": status,
            "requires_confirmation": bool(result.get("requires_confirmation")),
            "secret_handoff_required": status == "secret_handoff_required",
            "setting": result,
            "exit_code": 0,
        }

    def _feature_items() -> Dict:
        from src.settings_service import list_settings

        snapshot = list_settings(scope="global", store="feature", include_secrets=False)
        features = {item["key"]: item.get("value") for item in snapshot["settings"]}
        return {
            "response": f"{len(features)} feature flags",
            "features": features,
            "settings": features,
            "exit_code": 0,
        }

    def _model_slug(value: str) -> str:
        import re as _re

        return _re.sub(r"[^a-z0-9]+", "", (value or "").lower())

    def _endpoint_model_from_cache(model_query: str) -> Dict[str, Any] | None:
        import json as _json
        import re as _re

        try:
            from core.database import ModelEndpoint, SessionLocal
        except Exception:
            return None

        wanted = (model_query or "").strip()
        wanted_slug = _model_slug(wanted)
        wanted_tokens = [_model_slug(t) for t in _re.findall(r"[A-Za-z0-9]+", wanted)]
        wanted_tokens = [t for t in wanted_tokens if t]
        if not wanted_slug:
            return None
        try:
            db = SessionLocal()
        except Exception:
            return None
        try:
            best = None
            for ep in db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all():
                try:
                    raw_models = _json.loads(ep.cached_models or "[]") or []
                except Exception:
                    raw_models = []
                for mid in raw_models:
                    mid = str(mid)
                    mid_slug = _model_slug(mid)
                    if not mid_slug:
                        continue
                    exact = mid.lower() == wanted.lower()
                    compact_match = wanted_slug in mid_slug or mid_slug in wanted_slug
                    token_match = bool(wanted_tokens) and all(tok in mid_slug for tok in wanted_tokens)
                    if exact or compact_match or token_match:
                        score = 3 if exact else (2 if compact_match else 1)
                        if not best or score > best[0]:
                            best = (score, ep.id, mid)
            if best:
                return {"endpoint_id": best[1], "model": best[2]}
            return None
        except Exception:
            return None
        finally:
            db.close()

    try:
        from src.settings import get_setting as load_setting_value, load_settings, save_settings
        from src.settings_service import (
            SettingsServiceError,
            explain_setting,
            get_setting as service_get_setting,
            list_settings,
            patch_setting,
            reset_setting,
            set_setting,
        )
        from src.settings_registry import resolve_setting_alias

        if action == "list":
            store = _store()
            if store == "feature":
                return _feature_items()
            snapshot = list_settings(owner=owner, scope=_scope(), store="setting", include_secrets=False)
            shown = {
                item["key"]: _display_value(item.get("value"), bool(item.get("value_visible")))
                for item in snapshot["settings"]
                if item.get("value_type") != "object"
            }
            return {
                "response": f"{len(shown)} settings (use get/set/patch/explain with a key)",
                "settings": shown,
                "exit_code": 0,
            }

        if action == "request_secret":
            key = str(args.get("key") or "").strip()
            if not key:
                return {"error": "key is required", "exit_code": 1}
            try:
                from src.secret_handoff import create_secret_handoff

                handoff = create_secret_handoff(
                    key,
                    owner=owner,
                    scope=_scope(),
                    requested_by="agent",
                    ttl_seconds=int(args.get("ttl_seconds") or 3600),
                )
            except SettingsServiceError as exc:
                return {"error": str(exc), "status": exc.code, "exit_code": 1}
            return {
                "response": (
                    f"Secure input requested for {handoff['key']}. "
                    "Open the secure settings handoff UI to enter the value."
                ),
                "secret_handoff": handoff,
                "ui_event": {
                    "type": "odysseus:secret-handoff-requested",
                    "request_id": handoff["id"],
                    "key": handoff["key"],
                },
                "exit_code": 0,
            }

        if action == "secret_handoffs":
            from src.secret_handoff import list_secret_handoffs

            status = str(args.get("status") or "pending").strip().lower()
            handoffs = list_secret_handoffs(status=status or None)
            return {
                "response": f"{handoffs['count']} secret handoff request(s)",
                "secret_handoffs": handoffs["requests"],
                "exit_code": 0,
            }

        if action == "features":
            key = str(args.get("key") or "").strip()
            if not key:
                return _feature_items()
            if "value" not in args:
                result = service_get_setting(key, owner=owner, scope="global", store="feature")
                return {
                    "response": f"{result['key']} = {result.get('value')}",
                    "value": result.get("value"),
                    "setting": result,
                    "exit_code": 0,
                }
            result = set_setting(
                key,
                args.get("value"),
                owner=owner,
                scope="global",
                store="feature",
                actor="agent",
                confirmed=_confirmed(),
            )
            if not result.get("ok"):
                return _policy_response(result)
            return {
                "response": f"Set feature {result['key']} = {result.get('value')}.",
                "value": result.get("value"),
                "setting": result,
                "exit_code": 0,
            }

        if action == "get":
            key = str(args.get("key") or "").strip()
            if not key:
                return {"error": "key is required", "exit_code": 1}
            result = service_get_setting(key, owner=owner, scope=_scope(), store=_store(), include_secret=False)
            value = _display_value(result.get("value"), bool(result.get("value_visible")))
            return {
                "response": f"{result['key']} = {value}",
                "value": value,
                "setting": result,
                "exit_code": 0,
            }

        if action == "set":
            raw = str(args.get("key") or "").strip()
            if not raw:
                return {"error": "key is required", "exit_code": 1}
            if "value" not in args:
                return {"error": "value is required", "exit_code": 1}
            store = _store()
            key = resolve_setting_alias(raw) if store == "setting" else raw
            value = args.get("value")
            endpoint_result = None
            if store == "setting" and key in {
                "default_model",
                "research_model",
                "utility_model",
                "task_model",
                "vision_model",
                "image_model",
            }:
                resolved = _endpoint_model_from_cache(str(value))
                if resolved:
                    prefix = key[:-6]
                    endpoint_result = set_setting(
                        f"{prefix}_endpoint_id",
                        resolved["endpoint_id"],
                        owner=owner,
                        scope=_scope(),
                        store="setting",
                        actor="agent",
                        confirmed=_confirmed(),
                    )
                    value = resolved["model"]
            result = set_setting(
                key,
                value,
                owner=owner,
                scope=_scope(),
                store=store,
                actor="agent",
                confirmed=_confirmed(),
            )
            if not result.get("ok"):
                return _policy_response(result)
            display_value = _display_value(result.get("value"), bool(result.get("value_visible")))
            response = f"Set {result['key']} = {display_value}."
            if endpoint_result and endpoint_result.get("ok"):
                response = f"Set {result['key']} = {display_value} (endpoint {endpoint_result.get('value')})."
            return {
                "response": response,
                "value": display_value,
                "setting": result,
                "endpoint_setting": endpoint_result,
                "exit_code": 0,
            }

        if action in ("delete", "reset"):
            key = str(args.get("key") or "").strip()
            if not key:
                return {"error": "key is required", "exit_code": 1}
            result = reset_setting(
                key,
                owner=owner,
                scope=_scope(),
                store=_store(),
                actor="agent",
                confirmed=_confirmed(),
            )
            if not result.get("ok"):
                return _policy_response(result)
            return {
                "response": f"Reset {result['key']} to default ({result.get('value')}).",
                "value": result.get("value"),
                "setting": result,
                "exit_code": 0,
            }

        if action == "patch":
            key = str(args.get("key") or "").strip()
            if not key:
                return {"error": "key is required", "exit_code": 1}
            patch = args.get("patch")
            if not isinstance(patch, dict):
                patch = {
                    "op": args.get("op"),
                    "path": args.get("path") or args.get("patch_key"),
                    "key": args.get("patch_key"),
                    "value": args.get("value"),
                }
            result = patch_setting(
                key,
                patch,
                owner=owner,
                scope=_scope(),
                actor="agent",
                confirmed=_confirmed(),
            )
            if not result.get("ok"):
                return _policy_response(result)
            return {
                "response": f"Patched {result['key']}.",
                "value": result.get("value"),
                "setting": result,
                "exit_code": 0,
            }

        if action == "explain":
            key = str(args.get("key") or "").strip()
            if not key:
                return {"error": "key is required", "exit_code": 1}
            result = explain_setting(key, owner=owner, scope=_scope(), store=_store())
            bits = [result["key"], f"scope={result['entry']['scope']}", f"agent_access={result['agent_access']}"]
            if result.get("requires_confirmation"):
                bits.append("requires_confirmation")
            if result.get("secret_handoff_required"):
                bits.append("secret_handoff_required")
            return {"response": "; ".join(bits), "setting": result, "exit_code": 0}

        if action in ("disable_tool", "enable_tool", "list_tools"):
            _ALIASES = {
                "shell": ["bash"],
                "terminal": ["bash"],
                "search": ["web_search"],
                "web": ["web_search"],
                "browser": ["builtin_browser"],
                "documents": ["create_document", "edit_document", "update_document", "suggest_document"],
                "doc": ["create_document", "edit_document", "update_document", "suggest_document"],
                "memory": ["manage_memory"],
                "skills": ["manage_skills"],
                "images": ["generate_image"],
                "image": ["generate_image"],
                "tasks": ["manage_tasks"],
                "notes": ["manage_notes"],
                "calendar": ["manage_calendar"],
                "email": ["mcp__email__list_emails", "mcp__email__read_email", "mcp__email__send_email"],
                "research": ["web_search"],
            }
            if action == "list_tools":
                current = load_setting_value("disabled_tools", []) or []
                return {
                    "response": (
                        f"Currently disabled: {', '.join(current) if current else '(none)'}.\n"
                        "Common toggles: shell (bash), search (web_search), browser, documents, "
                        "memory, skills, images, tasks, notes, calendar, email."
                    ),
                    "disabled": list(current),
                    "exit_code": 0,
                }
            tool_name = (args.get("tool") or args.get("name") or "").strip().lower()
            if not tool_name:
                return {"error": "tool name required (e.g. 'shell', 'search', 'bash')", "exit_code": 1}
            targets = _ALIASES.get(tool_name, [tool_name])
            settings = load_settings()
            current = list(settings.get("disabled_tools") or [])
            before = set(current)
            if action == "disable_tool":
                for target in targets:
                    if target not in current:
                        current.append(target)
            else:
                current = [target for target in current if target not in targets]
            after = set(current)
            settings["disabled_tools"] = current
            save_settings(settings)
            verb = "Disabled" if action == "disable_tool" else "Enabled"
            changed = sorted(after.symmetric_difference(before))
            return {
                "response": (
                    f"{verb} {tool_name} ({', '.join(targets)}). "
                    f"Now disabled: {', '.join(current) if current else '(none)'}."
                ),
                "changed": changed,
                "disabled": list(current),
                "exit_code": 0,
            }

        return {"error": f"Unknown action: {action}", "exit_code": 1}
    except SettingsServiceError as exc:
        return {"error": str(exc), "status": exc.code, "exit_code": 1}
    except Exception as exc:
        logger.error("manage_settings v2 error: %s", exc)
        return {"error": str(exc), "exit_code": 1}


async def do_manage_settings(content: str, owner: Optional[str] = None) -> Dict:
    """Manage user settings and preferences."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    return _manage_settings_v2(args, owner=owner)

    action = args.get("action", "list")

    from core.database import SessionLocal
    db = SessionLocal()
    try:
        # set/get/list/delete operate on the REAL app settings (the same store
        # the Settings panel writes), so changing a model / voice / search
        # engine / reminder channel from chat actually takes effect.
        from src.settings import load_settings, save_settings, DEFAULT_SETTINGS

        # Secrets/credentials the agent must NOT write — kept read-only (masked)
        # so API keys never flow through chat. User sets these in the panel.
        _SECRET_KEYS = {
            "brave_api_key", "google_pse_key", "google_pse_cx",
            "tavily_api_key", "serper_api_key", "app_public_url",
        }
        def _is_secret(k):
            # `token` must be a suffix, not a substring: otherwise the int
            # setting `agent_input_token_budget` (which even has a "token budget"
            # alias to set it from chat) is wrongly classified as a credential.
            return (
                k in _SECRET_KEYS
                or k.endswith("token")
                or any(t in k for t in ("api_key", "_key", "secret", "password"))
            )

        # Friendly aliases → real keys, so natural phrasing resolves.
        _ALIASES_SET = {
            "voice": "tts_voice", "tts voice": "tts_voice", "tts": "tts_enabled",
            "text to speech": "tts_enabled", "tts provider": "tts_provider",
            "speech speed": "tts_speed", "voice speed": "tts_speed",
            "stt": "stt_enabled", "speech to text": "stt_enabled", "transcription": "stt_enabled",
            "search engine": "search_provider", "search provider": "search_provider",
            "search results": "search_result_count", "result count": "search_result_count",
            "default model": "default_model", "chat model": "default_model",
            "default endpoint": "default_endpoint_id",
            "task model": "task_model", "background model": "task_model",
            "teacher model": "teacher_model", "teacher": "teacher_enabled",
            "utility model": "utility_model", "research model": "research_model",
            "research max tokens": "research_max_tokens",
            "vision model": "vision_model", "vision": "vision_enabled",
            "image model": "image_model", "image quality": "image_quality",
            "image gen": "image_gen_enabled", "image generation": "image_gen_enabled",
            "reminder channel": "reminder_channel", "reminders": "reminder_channel",
            "ntfy topic": "reminder_ntfy_topic",
            "webhook integration": "reminder_webhook_integration_id",
            "webhook template": "reminder_webhook_payload_template", "webhook payload": "reminder_webhook_payload_template",
            "agent tool calls": "agent_max_tool_calls", "max tool calls": "agent_max_tool_calls",
            "agent timeout": "agent_stream_timeout_seconds", "stream timeout": "agent_stream_timeout_seconds",
            "token budget": "agent_input_token_budget", "input budget": "agent_input_token_budget",
            "hard max": "agent_input_token_hard_max",
            "token budget cap": "agent_input_token_hard_max",
            "input budget cap": "agent_input_token_hard_max",
        }
        def _resolve(k):
            k2 = (k or "").strip().lower()
            if k2 in DEFAULT_SETTINGS:
                return k2
            return _ALIASES_SET.get(k2, (k or "").strip())

        _ENUMS = {
            "image_quality": ["low", "medium", "high"],
            "reminder_channel": ["browser", "email", "ntfy", "webhook"],
        }
        def _coerce(value, default):
            if isinstance(default, bool):
                return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "on", "yes", "1", "enable", "enabled")
            if isinstance(default, int):
                return int(value)
            return value

        def _model_slug(value: str) -> str:
            import re as _re
            return _re.sub(r"[^a-z0-9]+", "", (value or "").lower())

        def _endpoint_model_from_cache(model_query: str):
            """Resolve friendly model text to an enabled endpoint + real model id.

            The Settings UI stores both `<prefix>_endpoint_id` and
            `<prefix>_model`; writing only the model leaves the runtime on the
            old endpoint. Prefer cached model lists so this stays fast/offline.
            """
            import json as _json
            import re as _re
            from core.database import ModelEndpoint

            wanted = (model_query or "").strip()
            wanted_slug = _model_slug(wanted)
            wanted_tokens = [_model_slug(t) for t in _re.findall(r"[A-Za-z0-9]+", wanted)]
            wanted_tokens = [t for t in wanted_tokens if t]
            if not wanted_slug:
                return None
            best = None
            for ep in db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all():
                raw_models = []
                try:
                    raw_models = _json.loads(ep.cached_models or "[]") or []
                except Exception:
                    raw_models = []
                # If cache is empty, still allow matching against endpoint name
                # for callers using model@endpoint elsewhere later.
                for mid in raw_models:
                    mid = str(mid)
                    mid_slug = _model_slug(mid)
                    if not mid_slug:
                        continue
                    exact = mid.lower() == wanted.lower()
                    compact_match = wanted_slug in mid_slug or mid_slug in wanted_slug
                    token_match = bool(wanted_tokens) and all(tok in mid_slug for tok in wanted_tokens)
                    if exact or compact_match or token_match:
                        score = 3 if exact else (2 if compact_match else 1)
                        if not best or score > best[0]:
                            best = (score, ep.id, mid)
            if best:
                return {"endpoint_id": best[1], "model": best[2]}
            return None

        def _mask(k, v):
            return "••••• (set in panel)" if _is_secret(k) and v else v

        if action == "list":
            s = load_settings()
            shown = {k: _mask(k, v) for k, v in s.items() if k in DEFAULT_SETTINGS and not isinstance(v, dict)}
            return {"response": f"{len(shown)} settings (use get/set with a key)", "settings": shown, "exit_code": 0}

        elif action == "get":
            key = _resolve(args.get("key", ""))
            if not key:
                return {"error": "key is required", "exit_code": 1}
            if key not in DEFAULT_SETTINGS:
                return {"error": f"Unknown setting '{args.get('key')}'. Use action='list' to see them.", "exit_code": 1}
            val = load_settings().get(key, DEFAULT_SETTINGS.get(key))
            return {"response": f"{key} = {_mask(key, val)}", "value": _mask(key, val), "exit_code": 0}

        elif action == "set":
            raw = args.get("key", "")
            value = args.get("value")
            if not raw:
                return {"error": "key is required", "exit_code": 1}
            key = _resolve(raw)
            if key not in DEFAULT_SETTINGS:
                return {"error": f"Unknown setting '{raw}'. Use action='list' to see available settings.", "exit_code": 1}
            if _is_secret(key):
                return {"response": f"'{key}' is a credential/secret — for security I can't set it from chat. Open Settings and set it there.", "exit_code": 0}
            # Structured settings (dicts/lists like keybinds, default_model_fallbacks)
            # have no safe scalar coercion — _coerce would pass a bare string
            # straight through and clobber the structure. Refuse them here; they're
            # edited in their dedicated panels. (reset/delete still restore the
            # default structure, which is safe.)
            if isinstance(DEFAULT_SETTINGS[key], (dict, list)):
                return {"response": f"'{key}' is a structured setting — edit it in its panel, not from chat. (You can reset it to default here.)", "exit_code": 0}
            try:
                value = _coerce(value, DEFAULT_SETTINGS[key])
            except (ValueError, TypeError):
                return {"error": f"'{value}' isn't a valid value for {key} (expected {type(DEFAULT_SETTINGS[key]).__name__}).", "exit_code": 1}
            if key in _ENUMS and str(value).lower() not in _ENUMS[key]:
                return {"error": f"{key} must be one of: {', '.join(_ENUMS[key])}.", "exit_code": 1}
            s = load_settings()
            s[key] = value
            if key in {"default_model", "research_model", "utility_model", "task_model", "vision_model", "image_model"}:
                resolved = _endpoint_model_from_cache(str(value))
                if resolved:
                    prefix = key[:-6]
                    s[f"{prefix}_endpoint_id"] = resolved["endpoint_id"]
                    s[key] = resolved["model"]
                    value = resolved["model"]
            save_settings(s)
            if key.endswith("_model") and s.get(f"{key[:-6]}_endpoint_id"):
                return {"response": f"Set {key} = {value} (endpoint {s.get(f'{key[:-6]}_endpoint_id')}).", "exit_code": 0}
            return {"response": f"Set {key} = {value}.", "exit_code": 0}

        elif action == "delete" or action == "reset":
            key = _resolve(args.get("key", ""))
            if key not in DEFAULT_SETTINGS:
                return {"error": f"Unknown setting '{args.get('key')}'.", "exit_code": 1}
            if _is_secret(key):
                return {"response": f"'{key}' is a credential — reset it in the panel.", "exit_code": 0}
            s = load_settings()
            s[key] = DEFAULT_SETTINGS[key]
            save_settings(s)
            return {"response": f"Reset {key} to default ({DEFAULT_SETTINGS[key]}).", "exit_code": 0}

        elif action in ("disable_tool", "enable_tool", "list_tools"):
            # Tool-toggle actions. These edit settings.json:disabled_tools
            # (the global list read on every chat request) rather than
            # prefs.json. Friendly aliases accepted: "shell" -> "bash",
            # "search" -> "web_search", "browser" -> "builtin_browser",
            # "documents" -> the document tool set, "memory" ->
            # manage_memory, etc.
            from src.settings import get_setting, save_settings, load_settings
            _ALIASES = {
                "shell": ["bash"],
                "terminal": ["bash"],
                "search": ["web_search"],
                "web": ["web_search"],
                "browser": ["builtin_browser"],
                "documents": ["create_document", "edit_document", "update_document", "suggest_document"],
                "doc": ["create_document", "edit_document", "update_document", "suggest_document"],
                "memory": ["manage_memory"],
                "skills": ["manage_skills"],
                "images": ["generate_image"],
                "image": ["generate_image"],
                "tasks": ["manage_tasks"],
                "notes": ["manage_notes"],
                "calendar": ["manage_calendar"],
                "email": ["mcp__email__list_emails", "mcp__email__read_email", "mcp__email__send_email"],
                "research": ["web_search"],  # research is a per-request flag, not a tool — closest analog
            }

            if action == "list_tools":
                current = get_setting("disabled_tools", []) or []
                return {
                    "response": (
                        f"Currently disabled: {', '.join(current) if current else '(none)'}.\n"
                        "Common toggles: shell (bash), search (web_search), browser, documents, "
                        "memory, skills, images, tasks, notes, calendar, email."
                    ),
                    "disabled": list(current),
                    "exit_code": 0,
                }

            tool_name = (args.get("tool") or args.get("name") or "").strip().lower()
            if not tool_name:
                return {"error": "tool name required (e.g. 'shell', 'search', 'bash')", "exit_code": 1}
            targets = _ALIASES.get(tool_name, [tool_name])

            settings = load_settings()
            current = list(settings.get("disabled_tools") or [])
            before = set(current)
            if action == "disable_tool":
                for t in targets:
                    if t not in current:
                        current.append(t)
            else:  # enable_tool
                current = [t for t in current if t not in targets]
            after = set(current)
            settings["disabled_tools"] = current
            save_settings(settings)

            verb = "Disabled" if action == "disable_tool" else "Enabled"
            changed = sorted(after.symmetric_difference(before))
            return {
                "response": (
                    f"{verb} {tool_name} ({', '.join(targets)}). "
                    f"Now disabled: {', '.join(current) if current else '(none)'}."
                ),
                "changed": changed,
                "disabled": list(current),
                "exit_code": 0,
            }

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}
    except Exception as e:
        logger.error(f"manage_settings error: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()



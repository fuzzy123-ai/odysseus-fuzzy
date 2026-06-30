"""
tool_implementations.py

Extracted tool implementation functions (do_* and helpers) from agent_tools.py.
These handle the actual execution logic for each tool type.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from src.constants import MAX_READ_CHARS, DEEP_RESEARCH_DIR, VAULT_FILE
from src.tool_utils import get_mcp_manager
from core.constants import internal_api_base
from routes._validators import validate_remote_host, validate_ssh_port
from src.tool_domains.common import _parse_tool_args
from src.tool_domains.repo_skills import (
    do_manage_repos,
    do_manage_skills,
    do_recent_changes,
    do_search_chats,
)
from src.tool_domains.personal_workspace import (
    do_manage_calendar,
    do_manage_notes,
)
from src.tool_domains.admin_config import (
    _validate_mcp_command,
    do_manage_assistant,
    do_manage_embeddings,
    do_manage_endpoints,
    do_manage_mcp,
    do_manage_personal_docs,
    do_manage_plugins,
    do_manage_presets,
    do_manage_settings,
    do_manage_tasks,
    do_manage_tokens,
    do_manage_webhooks,
)

logger = logging.getLogger(__name__)


def _string_arg(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _validate_cookbook_ssh_target(remote_host: Any, ssh_port: Any = "") -> tuple[str, str]:
    remote = validate_remote_host(_string_arg(remote_host) or None) or ""
    sport = validate_ssh_port(_string_arg(ssh_port) or None) or ""
    return remote, sport

# ---------------------------------------------------------------------------
# Active email state
# ---------------------------------------------------------------------------

# When the user has an email reader window open, the frontend tells the
# backend about it on each chat submit. Email tools can resolve "this email"
# without guessing a UID. Cleared between requests by chat_routes.
_active_email_ref: Optional[Dict[str, str]] = None


def set_active_email(uid: Optional[str], folder: Optional[str] = None, account: Optional[str] = None,
                     subject: Optional[str] = None, sender: Optional[str] = None) -> None:
    """Stash the email currently open in the UI. None clears it."""
    global _active_email_ref
    if not uid:
        _active_email_ref = None
        return
    _active_email_ref = {
        "uid": str(uid),
        "folder": str(folder or "INBOX"),
        "account": str(account or ""),
        "subject": str(subject or ""),
        "from": str(sender or ""),
    }


def get_active_email() -> Optional[Dict[str, str]]:
    return _active_email_ref


def clear_active_email() -> None:
    global _active_email_ref
    _active_email_ref = None

# ---------------------------------------------------------------------------
# Tool domains imported above; remaining domains stay local until their R7 slice.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Admin/config tool domain imported above; API/App/Cookbook tools stay local until R7E.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API call tool
# ---------------------------------------------------------------------------

async def do_api_call(content: str) -> Dict:
    """Execute an API call to a registered integration."""
    from src.integrations import execute_api_call, load_integrations
    try:
        args = json.loads(content)
    except json.JSONDecodeError:
        # Try line-based format: integration\nmethod path\nbody
        lines = content.strip().split("\n")
        args = {"integration": lines[0].strip() if lines else ""}
        if len(lines) > 1:
            parts = lines[1].strip().split(" ", 1)
            args["method"] = parts[0] if parts else "GET"
            args["path"] = parts[1] if len(parts) > 1 else "/"
        if len(lines) > 2:
            try:
                args["body"] = json.loads("\n".join(lines[2:]))
            except json.JSONDecodeError:
                pass

    integration_name = args.get("integration", "")
    integrations = load_integrations()
    intg = next((i for i in integrations if i["id"] == integration_name
                 or i["name"].lower() == integration_name.lower()), None)
    if not intg:
        available = ", ".join(i["name"] for i in integrations if i.get("enabled", True))
        return {"error": f"No integration matching '{integration_name}'. Available: {available or 'none configured'}", "exit_code": 1}

    return await execute_api_call(
        intg["id"],
        args.get("method", "GET"),
        args.get("path", "/"),
        params=args.get("params"),
        body=args.get("body"),
        extra_headers=args.get("headers"),
    )


# ---------------------------------------------------------------------------
# Personal workspace tool domain imported above; Cookbook tools stay local until R7E.
# ---------------------------------------------------------------------------


# ── Cookbook tools ──

# In-process loopback base for agent tools that call Odysseus's own API
# (cookbook state, model serve, gallery, email, calendar). We ride the
# per-process internal token so require_admin lets us through. See
# core/middleware.py. Resolution (override / APP_PORT / 7000) lives in
# core.constants.internal_api_base().
_INTERNAL_BASE = internal_api_base()


def _internal_headers(owner: Optional[str] = None) -> Dict[str, str]:
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    if owner:
        headers["X-Odysseus-Owner"] = owner
    return headers


async def _cookbook_servers() -> Dict[str, Any]:
    """Return the cookbook's configured servers + the currently-selected
    default host. Shape: {default_host, hosts: [{host, platform, env, envPath}]}.
    The agent uses this to route downloads/serves to the right machine
    instead of silently defaulting to localhost."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=_internal_headers())
            state = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception:
        return {"default_host": "", "hosts": []}
    env = (state or {}).get("env") or {}
    if not isinstance(env, dict):
        return {"default_host": "", "hosts": []}
    hosts = []
    for s in (env.get("servers") or []):
        if isinstance(s, dict):
            hosts.append({
                "name": s.get("name") or "",
                "host": s.get("host") or "",   # "" = Local
                "platform": s.get("platform") or "",
                "env": s.get("env") or "",
                "envPath": s.get("envPath") or "",
                "port": s.get("port") or "",
            })
    return {"default_host": env.get("remoteHost") or "", "hosts": hosts}


async def _resolve_cookbook_host(name_or_host: str) -> str:
    """Map a friendly server NAME ('gpu-box', 'workstation') to its ssh host
    string ('user@192.0.2.10'). If the input already looks like an
    ssh host (contains '@' or matches a known host), or matches nothing,
    it's returned unchanged. 'local'/'localhost' → '' (this machine)."""
    if not name_or_host:
        return ""
    val = name_or_host.strip()
    low = val.lower()
    if low in ("local", "localhost", "this machine", "here"):
        return ""
    servers = await _cookbook_servers()
    # Exact host match → already an ssh host
    for h in servers.get("hosts") or []:
        if h.get("host") and h["host"] == val:
            return val
    # Name match (case-insensitive)
    for h in servers.get("hosts") or []:
        if (h.get("name") or "").lower() == low:
            return h.get("host") or ""   # "" for the Local entry
    # Substring name match as a fallback
    for h in servers.get("hosts") or []:
        if low and low in (h.get("name") or "").lower():
            return h.get("host") or ""
    # No match — assume the caller passed a raw host/alias; return as-is
    # (ssh can resolve aliases from ~/.ssh/config).
    return val


async def _cookbook_env_for_host(host: str) -> Dict[str, Any]:
    """Resolve env_prefix / gpus / platform / hf_token / ssh_port for a
    given host by looking it up in cookbook_state.env. The user
    configures these per-host in the Cookbook UI; without them, raw
    `vllm serve …` fails with 'command not found' because vLLM lives
    inside a venv that has to be sourced first.

    Returns a dict with keys ready to drop into the /api/model/serve
    payload: env_prefix, gpus, platform, hf_token, ssh_port.
    Falls back to the top-level env settings if no per-host entry exists.
    """
    import httpx
    headers = _internal_headers()
    state: Dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
            state = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        logger.debug(f"cookbook env lookup failed for host={host!r}: {e}")
        return {}
    if not isinstance(state, dict):
        return {}
    env_root = state.get("env") or {}
    if not isinstance(env_root, dict):
        return {}

    # Per-host entry takes precedence over top-level.
    per_host: Dict[str, Any] = {}
    for s in (env_root.get("servers") or []):
        if isinstance(s, dict) and (s.get("host") or "") == (host or ""):
            per_host = s
            break

    env_kind = per_host.get("env") or env_root.get("env") or "none"
    env_path = per_host.get("envPath") or env_root.get("envPath") or ""
    platform = per_host.get("platform") or env_root.get("platform") or "linux"
    ssh_port = per_host.get("sshPort") or env_root.get("sshPort") or ""

    env_prefix = ""
    if env_kind == "venv" and env_path:
        if platform == "windows":
            activate = env_path if env_path.endswith("\\Scripts\\Activate.ps1") else env_path.rstrip("\\") + "\\Scripts\\Activate.ps1"
            env_prefix = f"& {activate}"
        else:
            activate = env_path if env_path.endswith("/bin/activate") else env_path.rstrip("/") + "/bin/activate"
            env_prefix = f"source {activate}"
    elif env_kind == "conda" and env_path:
        if platform == "windows":
            env_prefix = f"conda activate {env_path}"
        else:
            env_prefix = f'eval "$(conda shell.bash hook)" && conda activate {env_path}'

    from routes.cookbook_helpers import load_stored_hf_token
    return {
        "env_prefix": env_prefix,
        "env_type": env_kind,
        "env_path": env_path,
        "gpus": env_root.get("gpus") or "",
        "platform": platform,
        "hf_token": load_stored_hf_token(),
        "ssh_port": ssh_port,
    }


def _infer_serve_port(cmd: str) -> int:
    """Infer likely listen port from a serve command."""
    if not cmd:
        return 8080
    m = re.search(r"--port\\s+(\\d+)", cmd)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    m = re.search(r"OLLAMA_HOST=[^\\s]*?:(\\d+)", cmd)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    if "ollama" in cmd:
        return 11434
    return 8080


def _infer_serve_host(host: str | None) -> tuple[str, bool]:
    """Return (host, container_local) for registering a served endpoint."""
    if not (host or "").strip():
        return "localhost", True
    base_host = host.split("@", 1)[-1] if "@" in host else host
    return base_host, False


async def _ensure_served_endpoint(
    *,
    model: str,
    cmd: str,
    host: str | None,
) -> Dict[str, Any]:
    """Register/fetch a model endpoint for a running serve session."""
    import httpx
    endpoint_host, container_local = _infer_serve_host(host)
    port = _infer_serve_port(cmd)
    base_url = f"http://{endpoint_host}:{port}/v1"
    short_name = model.split("/")[-1] if "/" in model else model
    is_image = "diffusion_server.py" in (cmd or "")
    payload = {
        "name": short_name if not is_image else f"{short_name} (image)",
        "base_url": base_url,
        "skip_probe": "true",
        "model_type": "image" if is_image else "llm",
        "container_local": "true" if container_local else "false",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{_INTERNAL_BASE}/api/model-endpoints",
                data=payload,
                headers=_internal_headers(),
            )
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code >= 400:
            logger.debug(
                f"ensure endpoint failed for {model!r}: status={resp.status_code} data={data}"
            )
            return {"added": False, "endpoint_id": "", "base_url": base_url, "error": data}
        ep_id = data.get("id") if isinstance(data, dict) else None
        return {
            "added": bool(ep_id),
            "endpoint_id": ep_id or "",
            "base_url": base_url,
            "data": data,
        }
    except Exception as e:
        logger.debug(f"ensure endpoint exception for {model!r}: {e}")
        return {"added": False, "endpoint_id": "", "base_url": base_url, "error": str(e)}


async def _cookbook_register_task(
    session_id: str,
    model: str,
    host: str,
    cmd: str,
    task_type: str = "serve",
    *,
    endpoint_added: bool = False,
    endpoint_id: str = "",
) -> bool:
    """Append a task entry to cookbook_state.json after the agent
    launches via /api/model/serve or /api/model/download. The route
    spawns tmux but leaves state-writing to the UI; the agent needs to
    do that here so the task shows up in the Cookbook tab.
    Returns True on success, False if the write failed (best-effort)."""
    import httpx
    import time as _time
    headers = _internal_headers()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
            state = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        logger.debug(f"cookbook state read failed: {e}")
        return False
    if not isinstance(state, dict):
        state = {}
    tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
    # Skip duplicate (same session_id) entries
    if any(isinstance(t, dict) and t.get("sessionId") == session_id for t in tasks):
        return True
    display_name = model.split("/")[-1] if "/" in model else model
    # Placeholder output — the cookbook UI's CSS hides empty <pre>
    # via `.cookbook-output-pre:empty { display: none }`, so an
    # empty-string output makes the expansion appear broken until the
    # frontend's reconnect-polling loop captures tmux output. A short
    # placeholder gives the user something to see immediately; it gets
    # replaced by real tmux output within a few seconds.
    target = f"{host}:" if host else "local:"
    placeholder = (
        f"Launched via agent — waiting for tmux output…\n"
        f"  session: {session_id}\n"
        f"  target:  {target}{(cmd.split() or [''])[0] if cmd else ''}\n"
        f"  cmd:     {cmd[:200]}{'…' if len(cmd) > 200 else ''}"
    )
    tasks.append({
        "id": session_id,
        "sessionId": session_id,
        "name": display_name,
        "modelId": model,
        "type": task_type,
        "status": "running",
        "output": placeholder,
        "ts": int(_time.time() * 1000),
        "payload": {"repo_id": model, "remote_host": host or "", "_cmd": cmd},
        "remoteHost": host or "",
        "sshPort": "",
        "platform": "linux",
        "_serveReady": False,
        "_endpointAdded": bool(endpoint_added),
        "_endpointId": endpoint_id or "",
    })
    state["tasks"] = tasks
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{_INTERNAL_BASE}/api/cookbook/state",
                                  json=state, headers=headers)
        return r.status_code < 400
    except Exception as e:
        logger.debug(f"cookbook state write failed: {e}")
        return False


# Paths the generic `app_api` tool will refuse to call. Auth/token/user
# administration and host shell execution are too risky to route through an
# agent surface even when the agent is admin-context; accidental account or
# command mistakes have permanent blast radius.
_APP_API_BLOCKLIST_PREFIXES = (
    "/api/auth",           # login/logout/password
    "/api/users",          # user CRUD (bare /api/users list+create+delete must also block)
    "/api/tokens",         # api token mgmt (bare /api/tokens list+create must also block)
    "/api/admin",          # admin one-shots (wipe etc.)
    "/api/shell",          # host shell execution must stay behind named command tooling
    "/api/mounts",         # mount management controls host filesystem exposure
    "/api/backup/restore", # destructive restore
)

# (method, prefix) pairs to refuse specifically. Used for endpoints
# where GET is fine but writes are destructive or host-control shaped.
# Saw the agent wipe cookbook_state.json (presets + tasks) by POSTing
# {"tasks": []} to /api/cookbook/state, which overwrote the whole file.
# Use dedicated tools or UI flows instead.
_APP_API_BLOCKLIST_METHOD_PATH = (
    ("GET",    "/api/email/accounts"),  # owner-filtered in tool context; use list_email_accounts MCP tool
    # Email writes/sends/config changes must use the named email tools or UI
    # flows so account selection, confirmations, staged-send, and owner scope
    # are handled consistently.
    ("POST",   "/api/email"),
    ("PUT",    "/api/email"),
    ("PATCH",  "/api/email"),
    ("DELETE", "/api/email"),
    # Skill writes and audits should use manage_skills or the Skills UI so
    # SKILL.md parsing, owner scope, confirmation, and dedupe semantics stay
    # centralized.
    ("POST",   "/api/skills"),
    ("PUT",    "/api/skills"),
    ("PATCH",  "/api/skills"),
    ("DELETE", "/api/skills"),
    # Personal-assistant settings mutate CrewMember + ScheduledTask rows.
    # Keep app_api read-only until a dedicated confirmed assistant tool exists.
    ("POST",   "/api/assistant"),
    ("PUT",    "/api/assistant"),
    ("PATCH",  "/api/assistant"),
    ("DELETE", "/api/assistant"),
    # Do not let the agent recursively start/stop chat runs, rewrite messages,
    # or inject context through the generic loopback bridge.
    ("POST",   "/api/chat"),
    ("POST",   "/api/inject_context"),
    ("POST",   "/api/rewrite"),
    # Codex plugin action routes mirror named tools (email, memory, calendar,
    # documents, cookbook). Keep reads available but force mutations through
    # the policy-aware native tools.
    ("POST",   "/api/codex"),
    ("PUT",    "/api/codex"),
    ("PATCH",  "/api/codex"),
    ("DELETE", "/api/codex"),
    # Upload routes expose raw attachment bytes and can trigger vision
    # processing/cache writes. Keep them behind the normal attachment UI.
    ("GET",    "/api/upload"),
    ("POST",   "/api/upload"),
    ("PUT",    "/api/upload"),
    ("PATCH",  "/api/upload"),
    ("DELETE", "/api/upload"),
    # Saved visual signatures contain user-owned image data and should only be
    # surfaced inside the signing/document UI.
    ("GET",    "/api/signatures"),
    ("POST",   "/api/signatures"),
    ("PUT",    "/api/signatures"),
    ("PATCH",  "/api/signatures"),
    ("DELETE", "/api/signatures"),
    # Preset writes change model/persona behavior globally. Keep reads via
    # app_api, but require manage_presets or the Presets UI to save.
    ("POST",   "/api/presets/custom"),
    ("POST",   "/api/presets/templates"),
    ("DELETE", "/api/presets/templates"),
    ("POST",   "/api/presets/groups"),
    # Gallery editor drafts can contain full layer payloads and thumbnail data
    # URLs. Keep them inside the editor UI.
    ("GET",    "/api/editor-drafts"),
    ("POST",   "/api/editor-drafts"),
    ("PUT",    "/api/editor-drafts"),
    ("PATCH",  "/api/editor-drafts"),
    ("DELETE", "/api/editor-drafts"),
    # Cleanup preview is safe, but executing cleanup archives/deletes sessions.
    ("POST",   "/api/cleanup"),
    # Compare reads/history are safe; starting/voting/record/delete creates
    # sessions, spends model calls, or mutates comparison history.
    ("POST",   "/api/compare"),
    ("PUT",    "/api/compare"),
    ("PATCH",  "/api/compare"),
    ("DELETE", "/api/compare"),
    # Web search execution must go through web_search/web_fetch so tool
    # toggles, DSGVO/external-IO policy, freshness handling, and attribution
    # stay centralized. Config/provider reads can remain discoverable.
    ("POST",   "/api/search"),
    # Embedding downloads/deletes and endpoint writes touch network, local
    # model cache, secrets, and RAG singleton state. Keep app_api read-only.
    ("POST",   "/api/embeddings"),
    ("PUT",    "/api/embeddings"),
    ("PATCH",  "/api/embeddings"),
    ("DELETE", "/api/embeddings"),
    ("POST",   "/api/cookbook/state"),   # whole-file overwrite — agent must use serve_preset/serve_model instead
    ("DELETE", "/api/cookbook/state"),
    # Host-control routes: package install, engine rebuild, and process
    # signalling should not be reachable through the generic API bridge.
    ("POST",   "/api/cookbook/packages/install"),
    ("POST",   "/api/cookbook/rebuild-engine"),
    ("POST",   "/api/cookbook/kill-pid"),
    # Use the named tools (download_model / serve_model) — they handle
    # host-name resolution, per-host env_prefix, AND register the task
    # in cookbook state so it shows in the UI + list_downloads. Hitting
    # the raw endpoint via app_api skips all of that → orphan task.
    ("POST",   "/api/model/download"),
    ("POST",   "/api/model/serve"),
    # Use trigger_research — it returns a UI hint so the Deep Research
    # sidebar surfaces the session. Raw start works but the agent
    # fumbles the payload + the session doesn't reliably show up.
    ("POST",   "/api/research/start"),
    # Use named admin tools so confirmation, secret handoff, masking, owner
    # headers, and route-parity validation cannot be bypassed via app_api.
    ("POST",   "/api/model-endpoints"),
    ("PATCH",  "/api/model-endpoints"),
    ("DELETE", "/api/model-endpoints"),
    ("POST",   "/api/webhooks"),
    ("PATCH",  "/api/webhooks"),
    ("DELETE", "/api/webhooks"),
    ("POST",   "/api/mcp"),
    ("PUT",    "/api/mcp"),
    ("PATCH",  "/api/mcp"),
    ("DELETE", "/api/mcp"),
    # Plugin-manager and plugin-provider mutations can enable code, install
    # bundles, reload runtime modules, or hit live provider actions. Keep
    # app_api read-only for plugin surfaces until confirmed plugin tools exist.
    ("POST",   "/api/plugins"),
    ("PUT",    "/api/plugins"),
    ("PATCH",  "/api/plugins"),
    ("DELETE", "/api/plugins"),
    # Personal document/RAG source mutations can scan local folders, upload
    # private files, and remove indexed sources. Keep the generic bridge
    # read-only until a confirmed personal-docs tool handles scope.
    ("POST",   "/api/personal"),
    ("PUT",    "/api/personal"),
    ("PATCH",  "/api/personal"),
    ("DELETE", "/api/personal"),
    # Repo mutations and plan requests have a dedicated manage_repos surface
    # so confirmation, remote policy, branch gates, and path boundaries stay
    # centralized. GET routes may remain visible to UI/app_api as read/status.
    ("POST",   "/api/repos"),
    ("PUT",    "/api/repos"),
    ("PATCH",  "/api/repos"),
    ("DELETE", "/api/repos"),
    # Preference writes bypass manage_settings validation and ui_control's
    # event contract. Keep app_api read-only for prefs.
    ("POST",   "/api/prefs"),
    ("PUT",    "/api/prefs"),
    ("PATCH",  "/api/prefs"),
    ("DELETE", "/api/prefs"),
    # Data mutation routes with dedicated tools or without a confirmed agent
    # flow must not be reachable through generic app_api.
    ("POST",   "/api/memory"),
    ("PUT",    "/api/memory"),
    ("PATCH",  "/api/memory"),
    ("DELETE", "/api/memory"),
    ("POST",   "/api/contacts"),
    ("PUT",    "/api/contacts"),
    ("PATCH",  "/api/contacts"),
    ("DELETE", "/api/contacts"),
    ("POST",   "/api/gallery"),
    ("PUT",    "/api/gallery"),
    ("PATCH",  "/api/gallery"),
    ("DELETE", "/api/gallery"),
    ("POST",   "/api/document"),
    ("POST",   "/api/document/"),
    ("PUT",    "/api/document/"),
    ("PATCH",  "/api/document/"),
    ("DELETE", "/api/document"),
    ("POST",   "/api/documents"),
    ("POST",   "/api/documents/tidy"),
    ("POST",   "/api/documents/ai-tidy"),
    ("DELETE", "/api/research"),
    ("POST",   "/api/tasks"),
    ("PUT",    "/api/tasks"),
    ("PATCH",  "/api/tasks"),
    ("DELETE", "/api/tasks"),
    ("POST",   "/api/session"),
    ("PUT",    "/api/session"),
    ("PATCH",  "/api/session"),
    ("DELETE", "/api/session"),
    # Use the named tools for notes/events — they handle owner attribution,
    # natural-language due_date parsing, timezone, dedup, and tag/category
    # normalization. Calendar configuration/account mutations stay UI-only
    # until a confirmed calendar admin agent flow exists.
    ("POST",   "/api/notes"),
    ("PUT",    "/api/notes"),
    ("DELETE", "/api/notes"),
    ("POST",   "/api/calendar"),
    ("PUT",    "/api/calendar"),
    ("PATCH",  "/api/calendar"),
    ("DELETE", "/api/calendar"),
)


async def do_app_api(content: str, owner: Optional[str] = None) -> Dict:
    """Generic loopback to allowed internal Odysseus API endpoints. Lets the
    agent reach the full UI-button surface (cookbook, email, notes,
    calendar, skills, sessions, gallery, research, etc.) without us
    landing a named tool wrapper for every one.

    Args (JSON):
      action: "call" (default) | "endpoints"
      path:   "/api/cookbook/gpus"     # required for call
      method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" (default GET)
      body:   <object>                 # JSON body for POST/PUT/PATCH
      query:  <object>                 # querystring params

    The `endpoints` action returns the OpenAPI surface (method + path +
    summary) so the agent can discover what's reachable. A blocklist
    refuses sensitive auth/user/admin/shell paths and method-specific
    host-control routes to keep blast radius bounded.
    """
    import httpx
    try:
        args = _parse_tool_args(content) if content.strip() else {}
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = (args.get("action") or "call").lower()
    base = _INTERNAL_BASE

    if action == "endpoints":
        # Fetch FastAPI's OpenAPI schema so the agent can discover any
        # endpoint without us pre-listing them. Filter by an optional
        # `filter` keyword (substring match on path or summary).
        kw = (args.get("filter") or "").lower()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{base}/openapi.json",
                                        headers=_internal_headers())
                data = resp.json()
        except Exception as e:
            return {"error": f"OpenAPI fetch failed: {e}", "exit_code": 1}
        rows: List[Dict[str, Any]] = []
        for path, methods in (data.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            if any(path.startswith(p) for p in _APP_API_BLOCKLIST_PREFIXES):
                continue
            for method, op in methods.items():
                if method.lower() not in ("get", "post", "put", "patch", "delete"):
                    continue
                if any(method.upper() == m and path.startswith(p) for m, p in _APP_API_BLOCKLIST_METHOD_PATH):
                    continue
                summary = (op or {}).get("summary") or (op or {}).get("description") or ""
                if isinstance(summary, str):
                    summary = summary.strip().split("\n")[0][:140]
                if kw and kw not in path.lower() and kw not in (summary or "").lower():
                    continue
                rows.append({"method": method.upper(), "path": path, "summary": summary})
        rows.sort(key=lambda r: (r["path"], r["method"]))
        if not rows:
            return {"output": f"No endpoints match filter {kw!r}." if kw else "No endpoints found.", "exit_code": 0}
        lines = [f"{len(rows)} endpoint(s)" + (f" matching {kw!r}" if kw else "") + ":"]
        for r in rows[:200]:
            line = f"  {r['method']:6s} {r['path']}"
            if r["summary"]:
                line += f"  — {r['summary']}"
            lines.append(line)
        if len(rows) > 200:
            lines.append(f"  ...({len(rows) - 200} more — filter to narrow)")
        return {"output": "\n".join(lines), "endpoints": rows, "exit_code": 0}

    # action == "call"
    path = args.get("path") or ""
    if not path:
        return {"error": "path is required (e.g. '/api/cookbook/gpus')", "exit_code": 1}
    if not path.startswith("/"):
        path = "/" + path
    if any(path.startswith(p) for p in _APP_API_BLOCKLIST_PREFIXES):
        return {"error": f"Path blocked for safety: {path}. Sensitive endpoints are off-limits via app_api.", "exit_code": 1}

    method = (args.get("method") or "GET").upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return {"error": f"Unsupported method: {method}", "exit_code": 1}
    if any(method == m and path.startswith(p) for m, p in _APP_API_BLOCKLIST_METHOD_PATH):
        if "/api/email/accounts" in path and method == "GET":
            return {"error": "Don't use /api/email/accounts via app_api — it is owner-filtered in tool context and may return empty. Use the `list_email_accounts` email tool, then pass `account` to list_emails/read_email.", "exit_code": 1}
        if "/api/email/accounts" in path:
            return {"error": "Don't mutate email accounts via app_api - use the Email Settings UI or dedicated secure account setup flow so credentials and owner scope are protected.", "exit_code": 1}
        if "/api/email" in path:
            return {"error": "Don't mutate email via app_api - use the named email tools (`send_email`, `reply_to_email`, `bulk_email`, `archive_email`, `delete_email`, `mark_email_read`) or `ui_control` for draft windows so confirmation, account selection, and staged-send rules are enforced.", "exit_code": 1}
        if "/api/skills" in path:
            return {"error": "Don't mutate skills via app_api - use `manage_skills` for list/view/add/edit/patch/publish/delete/search so SKILL.md validation, owner scope, dedupe, and confirmation are enforced; use the Skills UI for test/audit/import flows.", "exit_code": 1}
        if "/api/assistant" in path:
            return {"error": "Don't mutate personal assistant settings or run check-ins via app_api - use `manage_assistant` so confirmation and owner scope are enforced; use `manage_tasks` for ordinary scheduled tasks.", "exit_code": 1}
        if "/api/chat" in path or "/api/inject_context" in path or "/api/rewrite" in path:
            return {"error": "Don't start, stop, rewrite, or inject chat context via app_api - use the normal chat UI or `manage_session` so run state, owner scope, and confirmation are preserved.", "exit_code": 1}
        if "/api/codex" in path:
            return {"error": "Don't mutate Codex plugin action routes via app_api - use the native named tools so confirmation, owner scope, and host-control guards are enforced.", "exit_code": 1}
        if "/api/upload" in path:
            return {"error": "Don't read or mutate upload attachment routes via app_api - use the normal attachment UI so owner scope, binary handling, and vision-processing boundaries are preserved.", "exit_code": 1}
        if "/api/signatures" in path:
            return {"error": "Don't read or mutate saved visual signatures via app_api - use the Signature/Documents UI so personal image data and signing confirmation stay scoped.", "exit_code": 1}
        if "/api/presets" in path:
            return {"error": "Don't mutate presets or persona templates via app_api - use `manage_presets` so confirmation and route parity are enforced.", "exit_code": 1}
        if "/api/editor-drafts" in path:
            return {"error": "Don't read or mutate gallery editor drafts via app_api - use the Gallery Editor UI so layered image payloads and owner scope stay contained.", "exit_code": 1}
        if "/api/cleanup" in path:
            return {"error": "Don't run cleanup via app_api - use `manage_session` or the Cleanup UI so archive/delete effects are explicit and confirmed.", "exit_code": 1}
        if "/api/compare" in path:
            return {"error": "Don't start, vote, record, or delete model comparisons via app_api - use the Compare UI or `chat_with_model` so model-call cost, owner scope, and vote/delete intent stay explicit.", "exit_code": 1}
        if "/api/search" in path:
            return {"error": "Don't run web search via app_api - use `web_search`, `web_fetch`, or `trigger_research` so external-IO policy, tool toggles, freshness, and attribution are enforced.", "exit_code": 1}
        if "/api/cookbook/packages/install" in path:
            return {"error": "Don't POST /api/cookbook/packages/install via app_api — package installation is host code execution. Use the dedicated Cookbook dependency UI/flow instead.", "exit_code": 1}
        if "/api/cookbook/rebuild-engine" in path:
            return {"error": "Don't POST /api/cookbook/rebuild-engine via app_api — engine rebuild mutates local or remote host state. Use the dedicated Cookbook UI/flow instead.", "exit_code": 1}
        if "/api/cookbook/kill-pid" in path:
            return {"error": "Don't POST /api/cookbook/kill-pid via app_api — process signalling is host control. Use the dedicated Cookbook stop/diagnostic flow instead.", "exit_code": 1}
        if "/api/model/download" in path:
            return {"error": "Don't POST /api/model/download directly — use the `download_model` tool (it resolves the server name, sets the venv env_prefix, and registers the task so it shows in the UI).", "exit_code": 1}
        if "/api/model/serve" in path:
            return {"error": "Don't POST /api/model/serve directly — use the `serve_model` or `serve_preset` tool (handles host resolution, env_prefix, and cookbook tracking).", "exit_code": 1}
        if "/api/embeddings" in path:
            if method == "POST" and path.startswith("/api/embeddings/endpoint"):
                return {"error": "Don't set embedding endpoints via app_api - use the Embedding Settings UI or a secure handoff flow because endpoint setup performs a live health check and may require an API key.", "exit_code": 1}
            return {"error": "Don't mutate embedding models or embedding endpoint config via app_api - use `manage_embeddings` so confirmation and route parity are enforced.", "exit_code": 1}
        if "/api/research/start" in path:
            return {"error": "Don't POST /api/research/start directly — use the `trigger_research` tool (it surfaces the session in the Deep Research sidebar).", "exit_code": 1}
        if "/api/model-endpoints" in path:
            return {"error": "Don't mutate /api/model-endpoints via app_api - use the `manage_endpoints` tool so confirmation, secure credential handoff, and route-parity validation are enforced.", "exit_code": 1}
        if "/api/webhooks" in path:
            return {"error": "Don't mutate /api/webhooks via app_api - use the `manage_webhooks` tool so confirmation and URL masking are enforced.", "exit_code": 1}
        if "/api/mcp" in path:
            return {"error": "Don't mutate /api/mcp via app_api - use the `manage_mcp` tool so confirmation and MCP command safety checks are enforced.", "exit_code": 1}
        if "/api/plugins" in path:
            return {"error": "Don't mutate plugin manager or plugin-provider routes via app_api - use `manage_plugins` for confirmed plugin manager actions; plugin-specific provider actions stay Plugins UI/provider-specific.", "exit_code": 1}
        if "/api/personal" in path:
            if path.startswith("/api/personal/upload"):
                return {"error": "Don't upload personal documents via app_api - use the Personal Docs UI so multipart bytes and owner scope stay bounded.", "exit_code": 1}
            return {"error": "Don't mutate personal document or RAG source routes via app_api - use `manage_personal_docs` so confirmation and route parity are enforced.", "exit_code": 1}
        if "/api/repos" in path:
            return {"error": "Don't mutate repo registry, repo policy, commit-plan, or push-plan routes via app_api - use `manage_repos` or the Repo/Project UI so confirmation, remote policy, branch gates, and path boundaries are enforced.", "exit_code": 1}
        if "/api/prefs" in path:
            return {"error": "Don't mutate preferences via app_api - use `manage_settings` for registered settings or `ui_control` for themes and UI state.", "exit_code": 1}
        if "/api/memory" in path:
            return {"error": "Don't write or search memory via app_api - use `manage_memory` for list/add/edit/delete/search so owner scope, confirmation, and vector sync are enforced; use the Memory UI for import/audit/pin flows.", "exit_code": 1}
        if "/api/contacts" in path:
            return {"error": "Don't mutate contacts via app_api - use `manage_contact` for add/update/delete with confirmation and validation; use the Contacts UI for import/config/clear flows.", "exit_code": 1}
        if "/api/gallery" in path:
            return {"error": "Don't mutate gallery items via app_api - use the Gallery UI until a confirmed gallery agent tool exists.", "exit_code": 1}
        if "/api/document" in path or "/api/documents" in path:
            return {"error": "Don't create, import, export, mutate, delete, or tidy documents via app_api - use document tools or `manage_documents` so owner scope, binary handling, and confirmation are enforced.", "exit_code": 1}
        if "/api/research" in path:
            return {"error": "Don't delete research reports via app_api - use the `manage_research` tool so confirmation and owner scope are enforced.", "exit_code": 1}
        if "/api/tasks" in path:
            return {"error": "Don't mutate scheduled tasks via app_api - use the `manage_tasks` tool so scheduling semantics and confirmation are enforced.", "exit_code": 1}
        if "/api/session" in path:
            return {"error": "Don't mutate chats/sessions via app_api - use `create_session`, `list_sessions`, or `manage_session` so exact ids, owner scope, and confirmation are enforced.", "exit_code": 1}
        if "/api/notes" in path:
            return {"error": "Don't hit /api/notes via app_api — use the `manage_notes` tool. It accepts natural-language due_date ('11pm today', 'tomorrow at 9am'), fires reminders from the due_date itself (no separate calendar event), and uses the caller's timezone. The raw endpoint requires ISO-UTC + a separate calendar event, both of which the agent tends to get wrong.", "exit_code": 1}
        if "/api/calendar/events" in path:
            return {"error": "Don't hit /api/calendar/events via app_api — use the `manage_calendar` tool. It handles tz-aware natural-language datetimes and reminder_minutes correctly. If the user wants a note + reminder, prefer `manage_notes` with due_date — it bundles both.", "exit_code": 1}
        if "/api/calendar" in path:
            return {"error": "Don't mutate calendar configuration via app_api - use the Calendar UI until a confirmed calendar admin agent flow exists.", "exit_code": 1}
        return {"error": f"{method} {path} is blocked — it overwrites the whole cookbook state file. Use list_serve_presets / serve_preset / serve_model instead.", "exit_code": 1}

    body = args.get("body")
    query = args.get("query") or None
    # Pass owner so the backend impersonates the user — without this,
    # POSTs (notes, calendar, todos, ...) get owner="internal-tool"
    # and the user that asked for them can't see the result.
    headers = {**_internal_headers(owner=owner), "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                method, f"{base}{path}",
                json=body if body is not None and method in ("POST", "PUT", "PATCH") else None,
                params=query,
                headers=headers,
            )
        # Try to parse JSON; fall back to raw text.
        try:
            payload = resp.json()
            preview = json.dumps(payload, indent=2, default=str)
            if len(preview) > 4000:
                preview = preview[:4000] + "\n... (truncated)"
        except Exception:
            payload = None
            preview = (resp.text or "")[:4000]
        if resp.status_code >= 400:
            return {
                "error": f"{method} {path} -> HTTP {resp.status_code}",
                "status_code": resp.status_code,
                "body": preview,
                "exit_code": 1,
            }
        return {
            "output": f"{method} {path} -> {resp.status_code}\n{preview}",
            "status_code": resp.status_code,
            "json": payload,
            "exit_code": 0,
        }
    except Exception as e:
        return {"error": f"{method} {path} failed: {e}", "exit_code": 1}


# Patterns for detecting running LLM/diffusion model servers outside
# the cookbook's task tracker. Each entry: (label, substring-list).
# Match is case-insensitive against the FULL cmdline. First-match wins.
_MODEL_PROCESS_PATTERNS = [
    ("vLLM",            ["vllm.entrypoints", "vllm serve", "/vllm/", "vllm-openai"]),
    ("SGLang",          ["sglang.launch_server", "sglang/launch_server"]),
    ("llama.cpp",       ["llama-server", "llama_cpp_server", "llamacppserver"]),
    ("Ollama",          ["ollama serve", "ollama runner", "/ollama "]),
    ("ComfyUI",         ["comfyui/main.py", "/ComfyUI/main.py", "ComfyUI"]),
    ("A1111 WebUI",     ["stable-diffusion-webui/webui", "stable-diffusion-webui/launch", "webui.sh"]),
    ("Fooocus",         ["Fooocus/entry_with_update", "Fooocus/launch"]),
    ("InvokeAI",        ["invokeai-web", "invokeai.app", "invokeai/api_app"]),
    ("Forge WebUI",     ["stable-diffusion-webui-forge", "forge/webui"]),
    ("SD.Next",         ["automatic/webui", "sd.next"]),
    ("TGI",             ["text-generation-launcher", "text_generation_launcher"]),
    ("Aphrodite",       ["aphrodite.endpoints", "aphrodite-engine"]),
    ("Triton",          ["tritonserver", "triton/main"]),
    ("Diffusers",       ["diffusers.pipelines", "StableDiffusionInpaintPipeline", "DiffusionPipeline"]),
]


def _cookbook_apply_retry_suggestion(cmd: str, suggestion: Dict[str, Any]) -> str:
    """Apply a structured Cookbook diagnosis suggestion to a serve command."""
    if not cmd or not suggestion:
        return cmd
    op = suggestion.get("op")
    if op == "append":
        arg = (suggestion.get("arg") or "").strip()
        if not arg or arg in cmd:
            return cmd
        return f"{cmd.rstrip()} {arg}"
    if op == "remove":
        flag = (suggestion.get("flag") or "").strip()
        if not flag:
            return cmd
        return re.sub(rf"\s*{re.escape(flag)}(?:\s+\S+)?", "", cmd).strip()
    if op == "replace":
        flag = (suggestion.get("flag") or "").strip()
        value = str(suggestion.get("value") or "").strip()
        if not flag or not value:
            return cmd
        repl = f"{flag} {value}"
        if re.search(rf"(^|\s){re.escape(flag)}(\s+\S+)?", cmd):
            return re.sub(rf"(^|\s){re.escape(flag)}(?:\s+\S+)?", lambda m: (m.group(1) or " ") + repl, cmd).strip()
        return f"{cmd.rstrip()} {repl}"
    return cmd


def _scan_running_model_processes() -> List[Dict[str, Any]]:
    """Scan /proc for running model server processes. Linux-only; returns
    [] on other platforms or if /proc isn't accessible. Each match returns
    a dict shaped like a cookbook task so the caller can merge cleanly.
    """
    import os
    if not os.path.isdir("/proc"):
        return []
    out: List[Dict[str, Any]] = []
    seen_keys = set()
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                    raw = f.read()
            except (OSError, PermissionError):
                continue
            if not raw:
                continue
            # cmdline is NUL-separated; join with spaces for matching/display
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
            if not cmdline:
                continue
            lower = cmdline.lower()
            for label, needles in _MODEL_PROCESS_PATTERNS:
                if any(n.lower() in lower for n in needles):
                    # Dedupe by (label, first-arg) — multi-worker servers
                    # spawn N processes; only show one row per server.
                    key = (label, cmdline.split(" ")[0])
                    if key in seen_keys:
                        break
                    seen_keys.add(key)
                    # Try to pluck a model name out of the cmdline.
                    model = ""
                    for tok in cmdline.split():
                        if "/" in tok and any(s in tok.lower() for s in (
                            "model", "checkpoint", ".safetensors", ".gguf", ".bin", "huggingface"
                        )):
                            model = tok
                            break
                    out.append({
                        "session_id": f"pid-{pid_dir}",
                        "model": model or label,
                        "phase": "running (external)",
                        "type": "serve",
                        "remote": "local",
                        "pid": int(pid_dir),
                        "label": label,
                        "cmdline_preview": cmdline[:140] + ("…" if len(cmdline) > 140 else ""),
                        "external": True,
                    })
                    break
    except Exception as e:
        logger.debug(f"_scan_running_model_processes failed: {e}")
    return out


async def do_download_model(content: str, owner: Optional[str] = None) -> Dict:
    """Download a HuggingFace model via the cookbook API."""
    import httpx
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    repo_id = args.get("repo_id", "")
    if not repo_id:
        return {"error": "repo_id is required", "exit_code": 1}
    host = (args.get("host") or "").strip()
    # Resolve a friendly server NAME ("gpu-box") to its ssh host string.
    if host:
        host = await _resolve_cookbook_host(host)
    # No host specified → default to the cookbook's currently-selected
    # server rather than silently downloading to localhost (which is
    # usually NOT where the GPUs / model cache live).
    _host_defaulted = False
    if not host and not args.get("local"):
        _servers = await _cookbook_servers()
        if _servers.get("default_host"):
            host = _servers["default_host"]
            _host_defaulted = True
    backend = (args.get("backend") or "").strip().lower()
    if not backend and "/" not in repo_id and ":" in repo_id:
        backend = "ollama"
    payload = {"repo_id": repo_id}
    if backend:
        payload["backend"] = backend
    if host:
        payload["remote_host"] = host
    if args.get("include"):
        payload["include"] = args["include"]
    # Per-host env_prefix + hf_token from cookbook_state (same as serve).
    env_cfg = await _cookbook_env_for_host(host)
    if env_cfg.get("env_prefix"): payload["env_prefix"] = env_cfg["env_prefix"]
    if env_cfg.get("hf_token"):   payload["hf_token"]   = env_cfg["hf_token"]
    if env_cfg.get("platform"):   payload["platform"]   = env_cfg["platform"]
    if env_cfg.get("ssh_port"):   payload["ssh_port"]   = env_cfg["ssh_port"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_INTERNAL_BASE}/api/model/download",
                                     json=payload, headers=_internal_headers())
            data = resp.json()
        if data.get("ok"):
            sid = data.get("session_id", "?")
            registered = await _cookbook_register_task(
                session_id=sid, model=repo_id, host=host,
                cmd=(f"ollama pull {repo_id}" if backend == "ollama" else f"hf download {repo_id}"),
                task_type="download",
            )
            note = "" if registered else " (state-write failed — download may not show in UI)"
            where = host or "local"
            default_note = " (defaulted to the cookbook's selected server — pass host= or local=true to override)" if _host_defaulted else ""
            return {
                "output": f"Download started: {repo_id} on {where} (session: {sid}){note}{default_note}",
                "session_id": sid,
                "host": host,
                "task_type": "download",
                "phase": "running",
                "exit_code": 0,
            }
        return {"error": data.get("error", "Download failed"), "exit_code": 1}
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


async def do_serve_model(content: str, owner: Optional[str] = None) -> Dict:
    """Start serving a model via the cookbook API."""
    import httpx
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    repo_id = args.get("repo_id", "")
    cmd = args.get("cmd", "")
    if not repo_id or not cmd:
        return {"error": "repo_id and cmd are required", "exit_code": 1}
    host = (args.get("host") or "").strip()
    if host:
        host = await _resolve_cookbook_host(host)
    if not host and not args.get("local"):
        _servers = await _cookbook_servers()
        if _servers.get("default_host"):
            host = _servers["default_host"]
    payload = {"repo_id": repo_id, "cmd": cmd}
    if host:
        payload["remote_host"] = host
    # Resolve per-host env settings (venv/conda activate, gpus,
    # hf_token, platform, ssh_port) from cookbook_state — same path
    # the UI uses. Without env_prefix, `vllm serve …` lands in a shell
    # without the user's venv and fails 'command not found'.
    env_cfg = await _cookbook_env_for_host(host)
    # Rewrite bare `vllm` / `python3` leading tokens to the venv's absolute
    # binary path when the target host has a venv configured. SSH non-
    # interactive shells often leave ~/.local/bin ahead of the venv bin on
    # PATH even with the venv activated, so `vllm serve` finds the wrong
    # binary and crashes early (e.g. compute_89 torch ABI errors on an old
    # user-site torch). This mirrors what static/js/cookbook.js does in
    # _buildServeCmd for the UI launch path.
    env_path = (env_cfg.get("env_path") or "").rstrip("/")
    env_type = (env_cfg.get("env_type") or env_cfg.get("env") or "").lower()
    if env_type == "venv" and env_path:
        venv_bin = f"{env_path}/bin"
        # Match the FIRST shell-token: skip leading KEY=VAL env-var prefixes
        # (CUDA_VISIBLE_DEVICES=… VLLM_USE_FLASHINFER_SAMPLER=…) before the binary.
        import re as _re3
        tokens = cmd.split()
        idx = 0
        env_re = _re3.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
        while idx < len(tokens) and env_re.match(tokens[idx]):
            idx += 1
        if idx < len(tokens):
            head = tokens[idx]
            if head in ("vllm", "python3", "python"):
                tokens[idx] = f"{venv_bin}/{head}"
                cmd = " ".join(tokens)
                payload["cmd"] = cmd
    if env_cfg.get("env_prefix"): payload["env_prefix"] = env_cfg["env_prefix"]
    if env_cfg.get("gpus"):       payload["gpus"]       = env_cfg["gpus"]
    if env_cfg.get("hf_token"):   payload["hf_token"]   = env_cfg["hf_token"]
    if env_cfg.get("platform"):   payload["platform"]   = env_cfg["platform"]
    if env_cfg.get("ssh_port"):   payload["ssh_port"]   = env_cfg["ssh_port"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_INTERNAL_BASE}/api/model/serve",
                                     json=payload, headers=_internal_headers())
            data = resp.json()
        if data.get("ok"):
            sid = data.get("session_id", "?")
            endpoint_id = data.get("endpoint_id") or ""
            if endpoint_id:
                endpoint_added = True
            else:
                endpoint_meta = await _ensure_served_endpoint(model=repo_id, cmd=cmd, host=host)
                endpoint_added = bool(endpoint_meta.get("added"))
                endpoint_id = endpoint_meta.get("endpoint_id", "") or endpoint_id
            registered = await _cookbook_register_task(
                session_id=sid, model=repo_id,
                host=host, cmd=cmd, task_type="serve",
                endpoint_added=endpoint_added, endpoint_id=endpoint_id or "",
            )
            note = "" if registered else " (state-write failed — task may not show in UI)"
            return {
                "output": f"Serving {repo_id} (session: {sid}){note}",
                "session_id": sid,
                "task_type": "serve",
                "phase": "running",
                "host": host,
                "endpoint_id": endpoint_id,
                "exit_code": 0,
            }
        # FastAPI HTTPException puts the message under `detail`, not `error`.
        # Surface BOTH so the agent sees "Invalid characters in cmd" (from
        # _validate_serve_cmd rejecting `&&`/`source`/`cd`) instead of
        # the generic "Serve failed", which leaves it with nothing to act on.
        err_msg = data.get("error") or data.get("detail") or "Serve failed"
        hint = ""
        if isinstance(err_msg, str) and "cmd" in err_msg.lower():
            hint = (" — the cmd must START with an allowlisted binary "
                    "(vllm, python3, llama-server, ollama, sglang, lmdeploy, node, npx). "
                    "Do NOT prefix with `cd …`, `source …`, or chain with `&&`. "
                    "env_prefix (e.g. `source ~/qwen35-env/bin/activate`) is added "
                    "automatically from the host's saved venv settings.")
        return {"error": f"{err_msg}{hint}", "exit_code": 1}
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


async def do_list_served_models(content: str, owner: Optional[str] = None) -> Dict:
    """List running model servers — merges cookbook-tracked tasks with
    a /proc scan for externally-launched LLM/diffusion processes
    (vLLM, sglang, llama.cpp, Ollama, ComfyUI, A1111, Fooocus, etc.)."""
    import asyncio
    import httpx

    # Cookbook-tracked tasks (best-effort; don't fail the whole call if
    # this is unreachable).
    cookbook_tasks: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/tasks/status",
                                    headers=_internal_headers())
            cookbook_tasks = (resp.json() or {}).get("tasks") or []
    except Exception as e:
        logger.debug(f"cookbook tasks/status fetch failed: {e}")

    # Local process scan — runs in a worker thread so it doesn't block.
    external = await asyncio.to_thread(_scan_running_model_processes)

    merged: List[Dict[str, Any]] = []
    merged.extend(cookbook_tasks)
    # Dedupe: if a process's PID is already mentioned by a cookbook task
    # (cookbook may track the PID via session_id), skip it.
    cookbook_pids = set()
    for t in cookbook_tasks:
        if isinstance(t, dict) and t.get("pid"):
            cookbook_pids.add(t["pid"])
    for p in external:
        if p.get("pid") not in cookbook_pids:
            merged.append(p)

    if not merged:
        return {
            "output": "No model servers currently running (cookbook task tracker empty; /proc scan found no vLLM / sglang / llama.cpp / Ollama / ComfyUI / A1111 / Fooocus / InvokeAI / TGI / Aphrodite / Triton / Diffusers processes).",
            "exit_code": 0,
        }

    # Sort so the agent sees what's actually LIVE first. Stopped/error/
    # completed tasks are mostly historical noise — they shouldn't lead
    # the list when something is genuinely serving.
    _ORDER = {
        "ready": 0, "running": 1, "loading": 1, "warming": 1,
        "queued": 2, "starting": 2,
        "error": 5, "crashed": 5, "failed": 5,
        "stopped": 6, "killed": 6, "cancelled": 6, "canceled": 6,
        "done": 7, "completed": 7, "finished": 7,
    }
    def _rank(t: Dict[str, Any]) -> int:
        phase = (t.get("phase") or t.get("status") or "unknown").lower()
        return _ORDER.get(phase, 3)
    merged.sort(key=_rank)

    cb_n = len(cookbook_tasks)
    ext_n = len(external)
    live_n = sum(1 for t in merged if _rank(t) <= 2)
    header = []
    if cb_n:
        header.append(f"{cb_n} cookbook-tracked")
    if ext_n:
        header.append(f"{ext_n} external")
    if live_n:
        header.insert(0, f"{live_n} LIVE")
    lines = [f"Running: {', '.join(header)}."]
    for t in merged:
        phase = t.get("phase") or t.get("status", "unknown")
        model = t.get("model", "?")
        remote = t.get("remote", "local")
        sid = t.get("session_id", "?")
        tag = " [external]" if t.get("external") else ""
        lines.append(f"- {model}: {phase} ({remote}, session: {sid}){tag}")
        diag = t.get("diagnosis") if isinstance(t.get("diagnosis"), dict) else None
        if diag:
            lines.append(f"    diagnosis: {diag.get('message')}")
            cmd = t.get("cmd") or ""
            suggestions = diag.get("suggestions") or []
            actionable = []
            for s in suggestions[:3]:
                label = s.get("label") or "retry"
                retry_cmd = _cookbook_apply_retry_suggestion(cmd, s)
                if retry_cmd and retry_cmd != cmd and s.get("op") in {"append", "replace", "remove"}:
                    actionable.append(f"{label}: `{retry_cmd}`")
                else:
                    actionable.append(label)
            if actionable:
                lines.append("    suggestions: " + " | ".join(actionable))
        if t.get("status") == "error" and t.get("output_tail"):
            tail = str(t.get("output_tail") or "").strip()
            if tail:
                # Prefer a window around a Python traceback if one exists,
                # falling back to the last 30 lines. The previous 6-line
                # tail showed only the post-crash bash prompt / neofetch
                # banner ("Locale: C / Ubuntu_Odysseus ❯") — useless for
                # diagnosis. The traceback we want is usually 50-200 lines
                # earlier in the buffer.
                _tail_lines = tail.splitlines()
                _shown = _tail_lines[-30:]
                for _i, _ln in enumerate(_tail_lines):
                    if "Traceback (most recent call last)" in _ln or "ERROR" in _ln or "Error:" in _ln:
                        _shown = _tail_lines[_i:_i + 40]
                        break
                lines.append("    recent log:")
                for line in _shown:
                    lines.append(f"      {line[:220]}")
        if t.get("external") and t.get("cmdline_preview"):
            lines.append(f"    cmd: {t['cmdline_preview']}")
    return {"output": "\n".join(lines), "tasks": merged, "exit_code": 0}


async def _cookbook_kill_session(session_id: str, *, remote_host: str = "",
                                 ssh_port: str = "", verb: str = "Stopped") -> Dict:
    """Kill a cookbook tmux session — remote-aware — AND mark the task
    stopped in cookbook_state.json. Shared by stop_served_model and
    cancel_download so both behave identically.

    Resolves the task's remote host from state when not passed in. A
    local-only `tmux kill-session` silently no-ops for remote tasks —
    that's the bug where "stop the download" appeared to work but the
    download kept running on the remote host.
    """
    import httpx
    import shlex
    headers = _internal_headers()
    remote = remote_host or ""
    sport = ssh_port or ""

    # Look up the task's host + confirm it exists in state.
    state: Dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
            state = resp.json() or {}
    except Exception as e:
        logger.debug(f"cookbook state lookup failed for {session_id}: {e}")
    if not isinstance(state, dict):
        state = {}
    matched = None
    for t in (state.get("tasks") or []):
        if isinstance(t, dict) and (t.get("sessionId") == session_id or t.get("id") == session_id):
            matched = t
            if not remote:
                remote = t.get("remoteHost") or ""
            if not sport:
                sport = t.get("sshPort") or ""
            break

    if remote:
        try:
            remote, sport = _validate_cookbook_ssh_target(remote, sport)
        except HTTPException as e:
            return {"error": str(getattr(e, "detail", e)), "exit_code": 1}
        _pf = f"-p {shlex.quote(str(sport))} " if sport and str(sport) != "22" else ""
        cmd = (
            f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "
            f"{_pf}{shlex.quote(remote)} 'tmux kill-session -t {shlex.quote(session_id)}'"
        )
        target_label = f"{session_id} on {remote}"
    else:
        cmd = f"tmux kill-session -t {shlex.quote(session_id)}"
        target_label = session_id

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{_INTERNAL_BASE}/api/shell/exec",
                                     json={"command": cmd}, headers=headers)
        if resp.status_code >= 400:
            return {"error": f"shell/exec returned HTTP {resp.status_code}: {resp.text[:200]}", "exit_code": 1}
        try:
            data = resp.json()
        except Exception:
            data = {}
        kill_failed = isinstance(data, dict) and data.get("exit_code") not in (None, 0)
        kill_err = ((data.get("stderr") or data.get("error") or "").strip() if isinstance(data, dict) else "")
        # "no server running" / "can't find session" means it was already
        # gone — treat as success (the goal is "not running").
        already_gone = any(s in kill_err.lower() for s in ("no server running", "can't find session", "session not found"))
        if kill_failed and not already_gone:
            return {"error": f"Failed to {verb.lower()} {target_label}: {kill_err or 'kill-session returned non-zero'}", "exit_code": 1}

        # Update state: mark stopped (so the UI + list reflect reality).
        if matched is not None:
            try:
                matched["status"] = "stopped"
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(f"{_INTERNAL_BASE}/api/cookbook/state",
                                      json=state, headers=headers)
            except Exception as e:
                logger.debug(f"failed to mark {session_id} stopped in state: {e}")

        suffix = " (was already gone)" if already_gone else ""
        return {"output": f"{verb} {target_label}{suffix}", "exit_code": 0}
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


async def do_stop_served_model(content: str, owner: Optional[str] = None) -> Dict:
    """Stop a running model server by killing its tmux session (remote-aware)."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    session_id = args.get("session_id", "")
    if not session_id:
        return {"error": "session_id is required", "exit_code": 1}
    return await _cookbook_kill_session(
        session_id,
        remote_host=args.get("remote_host") or args.get("host") or "",
        ssh_port=args.get("ssh_port") or "",
        verb="Stopped server",
    )


async def do_tail_serve_output(content: str, owner: Optional[str] = None) -> Dict:
    """Capture the last N lines of a cookbook task's tmux pane — remote-aware.

    Used by the agent to debug a failed/stuck serve: list_served_models tells
    you the task is `crashed`, this tool returns the actual stderr/traceback
    so the agent can match it against a known fix (compute_89 nvcc mismatch,
    flashinfer version mismatch, OOM, missing kernels, etc.) and decide
    whether to relaunch via serve_model with new flags.
    """
    import httpx
    import shlex
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    session_id = (args.get("session_id") or "").strip()
    if not session_id:
        return {"error": "session_id is required (from list_served_models)", "exit_code": 1}
    import re as _re
    if not _re.fullmatch(r"[a-zA-Z0-9_-]+", session_id):
        return {"error": "Invalid session_id format", "exit_code": 1}
    try:
        tail = int(args.get("tail") or 400)
    except (TypeError, ValueError):
        tail = 400
    tail = max(20, min(tail, 4000))
    headers = _internal_headers()
    remote = _string_arg(args.get("remote_host") or args.get("host"))
    sport = _string_arg(args.get("ssh_port"))
    # Resolve host from cookbook state if caller didn't pass one — same
    # lookup _cookbook_kill_session uses.
    if not remote:
        state: Dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
                state = resp.json() or {}
        except Exception as e:
            logger.debug(f"cookbook state lookup failed for {session_id}: {e}")
        if isinstance(state, dict):
            for t in (state.get("tasks") or []):
                if isinstance(t, dict) and (t.get("sessionId") == session_id or t.get("id") == session_id):
                    remote = t.get("remoteHost") or ""
                    if not sport:
                        sport = t.get("sshPort") or ""
                    break
    if remote:
        try:
            remote, sport = _validate_cookbook_ssh_target(remote, sport)
        except HTTPException as e:
            return {"error": str(getattr(e, "detail", e)), "exit_code": 1}

    # Prefer the persisted /tmp/odysseus-tmux/SESSION.log file over the
    # live tmux pane. The pane is what the user would see scrolling on
    # their screen — including the post-crash neofetch banner and the
    # idle bash prompt that overwrites the actual traceback the moment
    # vllm exits. The log file is the raw stdout/stderr of the wrapped
    # process and survives the crash unchanged. We only fall back to
    # the pane when the log file doesn't exist (older sessions launched
    # before the tmux+tee wrapper was added).
    log_path = f"/tmp/odysseus-tmux/{session_id}.log"
    pane_inner = f"tmux capture-pane -t {shlex.quote(session_id)} -p -S -{tail} 2>/dev/null"
    file_inner = f"tail -n {tail} {shlex.quote(log_path)} 2>/dev/null"
    inner = (
        f"if [ -s {shlex.quote(log_path)} ]; then {file_inner}; "
        f"else {pane_inner}; fi"
    )
    if remote:
        _pf = f"-p {shlex.quote(str(sport))} " if sport and str(sport) != "22" else ""
        cmd = (
            f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "
            f"{_pf}{shlex.quote(remote)} {shlex.quote(inner)}"
        )
        host_label = remote
    else:
        cmd = inner
        host_label = "local"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{_INTERNAL_BASE}/api/shell/exec",
                                     json={"command": cmd}, headers=headers)
        if resp.status_code >= 400:
            return {"error": f"shell/exec returned HTTP {resp.status_code}: {resp.text[:200]}", "exit_code": 1}
        data = resp.json() if resp.content else {}
        output_text = (data.get("stdout") or "").strip()
        stderr_text = (data.get("stderr") or "").strip()
        rc = data.get("exit_code")
        if rc not in (None, 0) and not output_text:
            already_gone = any(s in (stderr_text or "").lower() for s in ("no server running", "can't find session", "session not found"))
            if already_gone:
                return {"output": f"Tmux session {session_id} on {host_label} is gone (task already exited).", "exit_code": 0, "session_id": session_id, "host": host_label}
            return {"error": f"capture-pane failed on {host_label}: {stderr_text or f'exit {rc}'}", "exit_code": 1}
        # Dedupe download-progress noise. A 100-shard HF download produces
        # tens of thousands of `model-NN-of-MM.safetensors: 91%|...` lines
        # that all look the same to the agent and drown the actual error.
        # Keep only one sample per (file, decile-percent) bucket.
        import re as _re2
        lines = output_text.splitlines()
        dedup_lines = []
        seen_progress = set()
        progress_re = _re2.compile(r"^([\w./\-]+):\s+(\d+)%")
        for ln in lines:
            m = progress_re.match(ln.strip())
            if m:
                key = (m.group(1), int(m.group(2)) // 10)  # bucket by 10%
                if key in seen_progress:
                    continue
                seen_progress.add(key)
            dedup_lines.append(ln)
        output_text = "\n".join(dedup_lines)
        # Hard cap so the agent doesn't blow its token budget.
        MAX_CHARS = 8000
        if len(output_text) > MAX_CHARS:
            output_text = "…(earlier output truncated)…\n" + output_text[-MAX_CHARS:]
        return {
            "output": output_text or "(empty pane)",
            "session_id": session_id,
            "host": host_label,
            "tail_lines": tail,
            "exit_code": 0,
        }
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


async def do_list_downloads(content: str, owner: Optional[str] = None) -> Dict:
    """List in-flight model downloads (filters /api/cookbook/tasks/status to type=download)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/tasks/status",
                                    headers=_internal_headers())
            data = resp.json()
        tasks = [t for t in data.get("tasks", []) if (t.get("type") or "").lower() == "download"]
        if not tasks:
            return {"output": "No downloads in progress.", "exit_code": 0}
        lines = [f"{len(tasks)} download(s) in progress:"]
        for t in tasks:
            phase = t.get("phase") or t.get("status", "unknown")
            model = t.get("model", "?")
            pct = t.get("progress_percent") or t.get("percent")
            pct_str = f" {pct}%" if pct is not None else ""
            lines.append(f"- {model}: {phase}{pct_str} ({t.get('remote', 'local')}, session: {t.get('session_id', '?')})")
        return {"output": "\n".join(lines), "downloads": tasks, "exit_code": 0}
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


async def do_cancel_download(content: str, owner: Optional[str] = None) -> Dict:
    """Cancel a model download by killing its tmux session (remote-aware)."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    session_id = args.get("session_id", "")
    if not session_id:
        return {"error": "session_id is required (from list_downloads)", "exit_code": 1}
    return await _cookbook_kill_session(
        session_id,
        remote_host=args.get("remote_host") or args.get("host") or "",
        ssh_port=args.get("ssh_port") or "",
        verb="Cancelled download",
    )


async def do_search_hf_models(content: str, owner: Optional[str] = None) -> Dict:
    """Search HuggingFace via the cookbook /api/cookbook/hf-latest endpoint."""
    import httpx
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    query = args.get("query", "") or args.get("search", "")
    limit = args.get("limit", 10)
    params: Dict[str, str] = {}
    if query:
        params["search"] = query
    if limit:
        params["limit"] = str(limit)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/hf-latest",
                                    params=params, headers=_internal_headers())
            data = resp.json()
        models = data.get("models") if isinstance(data, dict) else data
        if not models:
            return {"output": f"No models found for query: {query!r}", "exit_code": 0}
        lines = [f"Found {len(models)} model(s) for {query!r}:" if query else f"{len(models)} model(s):"]
        for m in models[:limit if isinstance(limit, int) else 10]:
            if isinstance(m, dict):
                name = m.get("repo_id") or m.get("modelId") or m.get("id") or "?"
                dl = m.get("downloads")
                size = m.get("size_gb") or m.get("needed_vram_gb")
                bits = []
                if size:
                    bits.append(f"~{size}GB")
                if dl:
                    bits.append(f"{dl} downloads")
                tail = f" ({', '.join(bits)})" if bits else ""
                lines.append(f"- {name}{tail}")
            else:
                lines.append(f"- {m}")
        return {"output": "\n".join(lines), "models": models, "exit_code": 0}
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


async def do_adopt_served_model(content: str, owner: Optional[str] = None) -> Dict:
    """Register an externally-launched model server (bash + tmux + ssh, or
    anything else) into the Cookbook so it appears in list_served_models,
    can be stopped via stop_served_model, and is added to the user's
    endpoint list for chat. Use this when a model was started outside
    the cookbook's serve flow but you want first-class tracking.

    Args (JSON):
      host:          "user@192.0.2.10" (or omit for localhost)
      tmux_session:  "minimax-m27"  (existing tmux session name)
      model:         "cyankiwi/MiniMax-M2.7-AWQ-4bit" (HF repo or display name)
      port:          8000
      name:          optional display name (defaults to model basename)
      add_endpoint:  bool (default true) — also register as a chat endpoint
    """
    import httpx
    import shlex
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    host = _string_arg(args.get("host") or args.get("remote_host"))
    sess = (args.get("tmux_session") or args.get("session_id") or "").strip()
    model = (args.get("model") or args.get("repo_id") or "").strip()
    port = args.get("port") or 8000
    display_name = (args.get("name") or "").strip() or (model.split("/")[-1] if "/" in model else model)
    add_endpoint = args.get("add_endpoint", True)

    if not sess or not model:
        return {"error": "tmux_session and model are required", "exit_code": 1}

    # Verify tmux session exists on the target host
    if host:
        try:
            host, _ = _validate_cookbook_ssh_target(host)
        except HTTPException as e:
            return {"error": str(getattr(e, "detail", e)), "exit_code": 1}

    headers = _internal_headers()
    if host:
        check = f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {shlex.quote(host)} 'tmux has-session -t {shlex.quote(sess)} 2>&1'"
    else:
        check = f"tmux has-session -t {shlex.quote(sess)} 2>&1"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{_INTERNAL_BASE}/api/shell/exec",
                                  json={"command": check}, headers=headers)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400 or (data.get("exit_code") not in (None, 0)):
            err = (data.get("stderr") or data.get("error") or r.text[:200]).strip()
            return {"error": f"tmux session {sess!r} not found on {host or 'local'}: {err}", "exit_code": 1}
    except Exception as e:
        return {"error": f"verify failed: {e}", "exit_code": 1}

    # Best-effort health check — does port respond to /v1/models?
    if host:
        health_cmd = f"ssh -o ConnectTimeout=5 {shlex.quote(host)} 'curl -s -m 3 http://localhost:{int(port)}/v1/models'"
    else:
        health_cmd = f"curl -s -m 3 http://localhost:{int(port)}/v1/models"
    server_up = False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{_INTERNAL_BASE}/api/shell/exec",
                                  json={"command": health_cmd}, headers=headers)
            body = (r.json() or {}).get("stdout", "") if r.headers.get("content-type", "").startswith("application/json") else ""
            server_up = '"data"' in body or '"object"' in body
    except Exception:
        pass

    # Read+modify+write cookbook state. APPEND a task entry; do NOT
    # overwrite the whole file (that'd nuke presets).
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
            state = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        return {"error": f"could not read cookbook state: {e}", "exit_code": 1}
    if not isinstance(state, dict):
        state = {}
    tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
    # Skip duplicate adopt of the same session
    if any(isinstance(t, dict) and t.get("sessionId") == sess for t in tasks):
        adopted_already = True
    else:
        adopted_already = False
        import time as _time
        new_task = {
            "id": sess,
            "sessionId": sess,
            "name": display_name,
            "type": "serve",
            "status": "running",
            "output": (
                f"Adopted externally-launched session {sess!r} on {host or 'local'}.\n"
                "Reconnect polling will start streaming tmux output shortly."
            ),
            "ts": int(_time.time() * 1000),
            "payload": {"repo_id": model, "remote_host": host or "", "_cmd": "(adopted — launched outside cookbook)"},
            "remoteHost": host or "",
            "sshPort": "",
            "platform": "linux",
            "_serveReady": bool(server_up),
            "_endpointAdded": False,
            "_adoptedExternally": True,
        }
        tasks.append(new_task)
        state["tasks"] = tasks
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{_INTERNAL_BASE}/api/cookbook/state",
                                  json=state, headers=headers)
        except Exception as e:
            return {"error": f"could not save cookbook state: {e}", "exit_code": 1}

    # Optionally register as a chat endpoint
    endpoint_msg = ""
    if add_endpoint:
        # Resolve host to a URL. SSH form `user@host` → just take host.
        host_only = host.split("@", 1)[-1] if host else "localhost"
        endpoint_url = f"http://{host_only}:{int(port)}/v1"
        try:
            from src.tool_implementations import do_manage_endpoints  # avoid forward ref issues
        except Exception:
            do_manage_endpoints = None
        if do_manage_endpoints is not None:
            try:
                ep_result = await do_manage_endpoints(json.dumps({
                    "action": "add",
                    "name": display_name,
                    "endpoint_url": endpoint_url,
                    "is_local": False,
                }), owner=owner)
                if isinstance(ep_result, dict) and not ep_result.get("error"):
                    endpoint_msg = f" Endpoint {endpoint_url} added as {display_name!r}."
                else:
                    endpoint_msg = f" Endpoint registration skipped: {(ep_result or {}).get('error', 'unknown')}"
            except Exception as e:
                endpoint_msg = f" Endpoint registration failed: {e}"

    return {
        "output": (
            f"Adopted session {sess!r} ({model}) on {host or 'local'}:{port}. "
            + ("Already tracked — skipped state write. " if adopted_already else "Added to cookbook state. ")
            + ("Server responding. " if server_up else "Server not responding yet (still loading?). ")
            + endpoint_msg
        ).strip(),
        "session_id": sess,
        "host": host,
        "port": int(port),
        "server_up": server_up,
        "exit_code": 0,
    }


async def do_list_cookbook_servers(content: str, owner: Optional[str] = None) -> Dict:
    """List the cookbook's configured servers and which one is the
    current default. Use this to decide where to download/serve a
    model, or to show the user options when the target host is
    ambiguous."""
    servers = await _cookbook_servers()
    hosts = servers.get("hosts") or []
    default = servers.get("default_host") or ""
    if not hosts:
        return {"output": "No cookbook servers configured. Downloads/serves default to localhost.", "servers": [], "default_host": "", "exit_code": 0}
    # Resolve which server is the default by its friendly name too.
    default_name = next((h.get("name") for h in hosts if h.get("host") == default and h.get("name")), default or "local")
    lines = [f"{len(hosts)} configured server(s) (default: {default_name}):"]
    for h in hosts:
        name = h.get("name") or "(unnamed)"
        host = h.get("host") or "local"
        mark = " ← default" if h.get("host") == default else ""
        env_bit = f" [{h.get('env')}: {h.get('envPath')}]" if h.get("env") and h.get("env") != "none" else ""
        plat = f" ({h.get('platform')})" if h.get("platform") else ""
        lines.append(f"- {name} → {host}{plat}{env_bit}{mark}")
    lines.append("\nRefer to servers by their name (e.g. download_model with host=\"gpu-box\").")
    return {"output": "\n".join(lines), "servers": hosts, "default_host": default, "exit_code": 0}


async def do_list_serve_presets(content: str, owner: Optional[str] = None) -> Dict:
    """List saved serve presets from cookbook_state.json. Each preset
    is a launch template: name, model, host, port, cmd. Use this to
    discover what the user has previously configured so you can
    launch by preset instead of fabricating tmux commands."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state",
                                    headers=_internal_headers())
            state = resp.json() or {}
    except Exception as e:
        return {"error": f"Failed to fetch cookbook state: {e}", "exit_code": 1}

    presets = state.get("presets") or []
    if not presets:
        return {
            "output": "No serve presets saved. Tell the user to save one from the Cookbook UI first, or use serve_model with explicit repo_id + cmd + host.",
            "presets": [],
            "exit_code": 0,
        }
    lines = [f"{len(presets)} saved serve preset(s):"]
    for p in presets:
        if not isinstance(p, dict):
            continue
        name = p.get("name", "?")
        model = p.get("model") or p.get("modelId") or "?"
        host = p.get("host") or p.get("remoteHost") or "local"
        port = p.get("port", "")
        cmd = (p.get("cmd") or "").strip()
        bits = [f"- {name}: {model}", f"host={host}"]
        if port:
            bits.append(f"port={port}")
        lines.append("  ".join(bits))
        if cmd:
            cmd_preview = cmd if len(cmd) < 140 else cmd[:140] + "…"
            lines.append(f"    cmd: {cmd_preview}")
    return {"output": "\n".join(lines), "presets": presets, "exit_code": 0}


async def do_serve_preset(content: str, owner: Optional[str] = None) -> Dict:
    """Launch a saved serve preset by name. Resolves the preset's
    cmd + host + model from cookbook_state.json, then calls the
    standard model/serve endpoint. Saves the agent from having to
    reinvent tmux launch commands the user already saved."""
    import httpx
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    name = (args.get("name") or args.get("preset") or "").strip()
    if not name:
        return {"error": "name (preset name) is required. Call list_serve_presets to see what's available.", "exit_code": 1}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state",
                                    headers=_internal_headers())
            state = resp.json() or {}
    except Exception as e:
        return {"error": f"Failed to fetch cookbook state: {e}", "exit_code": 1}

    presets = state.get("presets") or []
    # Match by exact name first, then case-insensitive substring.
    chosen = None
    lname = name.lower()
    for p in presets:
        if isinstance(p, dict) and (p.get("name") or "").lower() == lname:
            chosen = p
            break
    if chosen is None:
        for p in presets:
            if isinstance(p, dict) and lname in (p.get("name") or "").lower():
                chosen = p
                break
    if chosen is None:
        sample = ", ".join((p.get("name") or "?") for p in presets[:8] if isinstance(p, dict))
        return {"error": f"No preset matching {name!r}. Available: {sample or '(none)'}", "exit_code": 1}

    repo_id = chosen.get("model") or chosen.get("modelId") or ""
    cmd = (chosen.get("cmd") or "").strip()
    host = chosen.get("host") or chosen.get("remoteHost") or ""
    if not repo_id or not cmd:
        return {"error": f"Preset {chosen.get('name')!r} is missing model or cmd — can't launch.", "exit_code": 1}

    payload: Dict[str, Any] = {"repo_id": repo_id, "cmd": cmd}
    if host:
        payload["remote_host"] = host
    # Resolve per-host env settings the same way the UI does — pulls
    # env_prefix (source ~/vllm-env/bin/activate), gpus, hf_token,
    # etc. from cookbook_state.env so launches actually find vllm.
    env_cfg = await _cookbook_env_for_host(host)
    if env_cfg.get("env_prefix"): payload["env_prefix"] = env_cfg["env_prefix"]
    if env_cfg.get("gpus"):       payload["gpus"]       = env_cfg["gpus"]
    if env_cfg.get("hf_token"):   payload["hf_token"]   = env_cfg["hf_token"]
    if env_cfg.get("platform"):   payload["platform"]   = env_cfg["platform"]
    if env_cfg.get("ssh_port"):
        payload["ssh_port"] = env_cfg["ssh_port"]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_INTERNAL_BASE}/api/model/serve",
                                     json=payload, headers=_internal_headers())
            data = resp.json()
        if data.get("ok"):
            sid = data.get("session_id", "?")
            endpoint_id = data.get("endpoint_id") or ""
            if endpoint_id:
                endpoint_added = True
            else:
                endpoint_meta = await _ensure_served_endpoint(model=repo_id, cmd=cmd, host=host)
                endpoint_added = bool(endpoint_meta.get("added"))
                endpoint_id = endpoint_meta.get("endpoint_id", "") or endpoint_id
            registered = await _cookbook_register_task(
                session_id=sid, model=repo_id, host=host,
                cmd=cmd, task_type="serve",
                endpoint_added=endpoint_added, endpoint_id=endpoint_id or "",
            )
            note = "" if registered else " (state-write failed — task may not show in UI)"
            return {"output": f"Launched preset {chosen.get('name')!r}: {repo_id} on {host or 'local'} (session: {sid}){note}", "session_id": sid, "host": host, "endpoint_id": endpoint_id, "exit_code": 0}
        return {"error": data.get("error", "Serve failed"), "exit_code": 1}
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


async def do_list_cached_models(content: str, owner: Optional[str] = None) -> Dict:
    """List models already cached locally and/or on remote hosts.

    With no `host` arg, scans EVERY configured Cookbook server (and local)
    and aggregates — so the agent sees the full inventory in one call
    instead of having to query each server individually.
    """
    import httpx
    try:
        args = _parse_tool_args(content) if content.strip() else {}
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    raw_host = (args.get("host") or "").strip()
    headers = _internal_headers()

    async def _scan_one(host_label: str, host_val: str, ssh_port: str = "",
                        platform: str = "", model_dir: str = "") -> list:
        """Hit /api/model/cached for one host; tag each returned model with its source."""
        p: Dict[str, str] = {}
        if host_val:
            p["host"] = host_val
        # Caller-provided override beats per-server config beats nothing.
        if args.get("model_dir"):
            p["model_dir"] = args["model_dir"]
        elif model_dir:
            p["model_dir"] = model_dir
        if ssh_port:
            p["ssh_port"] = ssh_port
        elif args.get("ssh_port"):
            p["ssh_port"] = str(args["ssh_port"])
        if platform:
            p["platform"] = platform
        elif args.get("platform"):
            p["platform"] = args["platform"]
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(f"{_INTERNAL_BASE}/api/model/cached",
                                        params=p, headers=headers)
                data = resp.json()
            ms = data.get("models", []) if isinstance(data, dict) else (data or [])
            for m in ms:
                m["host"] = host_label or "local"
            return ms or []
        except Exception as e:
            logger.debug(f"list_cached_models scan({host_label}) failed: {e}")
            return []

    # When the caller specifies a host explicitly, scan only that one (old behaviour).
    # Otherwise iterate every configured server + local so the agent doesn't
    # have to repeat the call per server.
    try:
        # Pull configured servers from cookbook state (used for resolving
        # modelDirs both when caller specifies a host and when we scan all).
        servers: list = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                st = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
                st_data = st.json() if st.headers.get("content-type", "").startswith("application/json") else {}
            servers = (st_data.get("env", {}) or {}).get("servers") or []
        except Exception as e:
            logger.debug(f"server list fetch failed: {e}")
            st_data = {}

        def _dirs_for(server_record: Dict[str, Any]) -> str:
            """Comma-joined modelDirs from a saved server record (Settings).

            Filters out the HF cache (~/.cache/huggingface/hub) — the backend
            scan script always scans it by default, so re-passing it as an
            extra model_dir is redundant AND confuses some path-handling
            edge cases where the extra dir suppresses the deeper scan.
            We only need to forward the NON-default dirs (e.g. /mnt/HADES/models).
            """
            mds = server_record.get("modelDirs") if isinstance(server_record, dict) else None
            HF_DEFAULTS = {"~/.cache/huggingface/hub", "~/.cache/huggingface"}
            if isinstance(mds, list):
                extras = [d for d in mds if isinstance(d, str) and d.strip() and d.strip() not in HF_DEFAULTS]
                return ",".join(extras)
            if isinstance(mds, str) and mds.strip() not in HF_DEFAULTS:
                return mds
            return ""

        if raw_host:
            host = await _resolve_cookbook_host(raw_host)
            # Find this host's saved record so its modelDirs apply too.
            srv = next(
                (s for s in servers if isinstance(s, dict)
                 and (s.get("name") == raw_host or s.get("host") == host or s.get("host") == raw_host)),
                {},
            )
            models = await _scan_one(raw_host, host, model_dir=_dirs_for(srv))
        else:
            # Always include local. Local's saved record is the one with no host.
            local_srv = next((s for s in servers if isinstance(s, dict) and not (s.get("host") or "").strip()), {})
            scans: list = [_scan_one("local", "", model_dir=_dirs_for(local_srv))]
            for s in servers:
                if not isinstance(s, dict):
                    continue
                name = s.get("name") or s.get("host")
                host_val = s.get("host") or ""
                if not host_val:
                    continue
                scans.append(_scan_one(
                    name,
                    host_val,
                    ssh_port=str(s.get("port") or ""),
                    platform=s.get("platform") or "",
                    model_dir=_dirs_for(s),
                ))
            results = await asyncio.gather(*scans, return_exceptions=False)
            # Dedupe by (host, repo_id) — same model could appear in both HF cache + Ollama list.
            seen = set()
            models: list = []
            for batch in results:
                for m in batch:
                    key = (m.get("host", ""), m.get("repo_id", ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    models.append(m)
        if not models:
            # Cache scans can miss models downloaded into the HF default cache
            # when the server has no explicit model_dir configured. Surface
            # completed Cookbook download tasks so the agent doesn't conclude
            # a model is absent and re-download it.
            downloaded = []
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    st = await client.get(f"{_INTERNAL_BASE}/api/cookbook/state", headers=headers)
                    state = st.json() if st.headers.get("content-type", "").startswith("application/json") else {}
                for t in (state.get("tasks") or []):
                    if not isinstance(t, dict) or t.get("type") != "download":
                        continue
                    if (t.get("status") or "").lower() not in {"done", "completed"}:
                        continue
                    task_host = t.get("remoteHost") or (t.get("payload") or {}).get("remote_host") or ""
                    if raw_host and task_host != raw_host:
                        continue
                    repo = t.get("modelId") or t.get("repoId") or (t.get("payload") or {}).get("repo_id") or t.get("name")
                    if repo and repo not in downloaded:
                        downloaded.append(repo)
            except Exception:
                downloaded = []
            host_str = f" on {raw_host}" if raw_host else ""
            if downloaded:
                lines = [f"No cache paths were detected{host_str}, but Cookbook has completed download task(s):"]
                lines.extend(f"- {repo} — downloaded via Cookbook task" for repo in downloaded)
                return {"output": "\n".join(lines), "models": [{"repo_id": repo, "source": "cookbook_task"} for repo in downloaded], "exit_code": 0}
            return {"output": f"No cached models found{host_str}.", "exit_code": 0}
        # Multi-host scan: group by host so the agent sees inventory per server.
        # Single-host scan: flat list (matches old output shape).
        if raw_host:
            lines = [f"{len(models)} cached model(s) on {raw_host}:"]
            for m in models:
                name = m.get("repo_id", "?")
                sz = m.get("size") or (f"{m.get('size_bytes', 0) / (1024**3):.1f}GB" if m.get("size_bytes") else "")
                inc = " (incomplete)" if m.get("has_incomplete") else ""
                kind = " [diffusion]" if m.get("is_diffusion") else ""
                lines.append(f"- {name}{kind} — {sz}{inc}")
        else:
            from collections import defaultdict as _dd
            by_host = _dd(list)
            for m in models:
                by_host[m.get("host", "local")].append(m)
            lines = [f"{len(models)} cached model(s) across {len(by_host)} server(s):"]
            for host_name in sorted(by_host.keys()):
                lines.append(f"\n[{host_name}]")
                for m in by_host[host_name]:
                    name = m.get("repo_id", "?")
                    sz = m.get("size") or (f"{m.get('size_bytes', 0) / (1024**3):.1f}GB" if m.get("size_bytes") else "")
                    inc = " (incomplete)" if m.get("has_incomplete") else ""
                    kind = " [diffusion]" if m.get("is_diffusion") else ""
                    backend = f" ({m.get('backend')})" if m.get("backend") else ""
                    lines.append(f"- {name}{kind}{backend} — {sz}{inc}")
        return {"output": "\n".join(lines), "models": models, "exit_code": 0}
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


# ── Gallery tools ──

async def do_edit_image(content: str, owner: Optional[str] = None) -> Dict:
    """Edit a gallery image (upscale, rembg, inpaint, harmonize)."""
    import httpx
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    image_id = args.get("image_id", "")
    action = args.get("action", "")
    if not image_id or not action:
        return {"error": "image_id and action are required", "exit_code": 1}
    payload = {"image_id": image_id}
    if args.get("prompt"):
        payload["prompt"] = args["prompt"]
    if args.get("scale"):
        payload["scale"] = args["scale"]
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{_INTERNAL_BASE}/api/gallery/{action}", json=payload)
            data = resp.json()
        if data.get("success") or data.get("id"):
            return {"output": f"Image edited ({action}). New image ID: {data.get('id', '?')}", "exit_code": 0}
        return {"error": data.get("error", f"{action} failed"), "exit_code": 1}
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


# ── Research tools ──

async def do_manage_research(content: str, owner: Optional[str] = None) -> Dict:
    """List, read/open, or delete saved deep-research results from the Library.
    Args (JSON): {"action": "list|read|delete", "id": "<id>", "search": "...", "confirmed": true}.
    Research is stored as data/deep_research/<id>.json (query, summary, sources)."""
    import json as _json
    from pathlib import Path as _Path
    try:
        args = _parse_tool_args(content) if content.strip().startswith("{") else {}
    except ValueError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    action = (args.get("action") or "list").lower()
    rid = (args.get("id") or args.get("session_id") or args.get("research_id") or "").strip()
    data_dir = _Path(DEEP_RESEARCH_DIR)

    # SECURITY: the research id is interpolated straight into a filesystem
    # path (data/deep_research/<rid>.json) for read AND delete. Without this
    # gate an agent-supplied id like "../settings" or "../../etc/passwd"
    # escapes the research dir — reading exfiltrates arbitrary *.json into
    # chat, deleting unlinks arbitrary *.json on disk. Allow only a bare
    # token (research session ids are hex/uuid/slug — no separators).
    if rid and not re.fullmatch(r"[A-Za-z0-9_-]+", rid):
        return {"error": "Invalid research id."}

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Research {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    def _load(p):
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _visible_to_owner(d) -> bool:
        if owner is None:
            return True
        return isinstance(d, dict) and d.get("owner") == owner

    if action in ("read", "open", "view", "get"):
        if not rid:
            return {"error": "Provide the research id (from action='list')."}
        p = data_dir / f"{rid}.json"
        if not p.exists():
            return {"error": f"Research '{rid}' not found."}
        d = _load(p) or {}
        if not _visible_to_owner(d):
            return {"error": f"Research '{rid}' not found."}
        summary = d.get("result") or d.get("raw_report") or d.get("summary") or d.get("report") or "(no report body)"
        srcs = d.get("sources", []) or []
        out = f"# {d.get('query', '(untitled)')}\n\n{summary}"
        if srcs:
            out += "\n\nSources:\n" + "\n".join(
                f"- {s.get('title') or s.get('url', '')}: {s.get('url', '')}" for s in srcs[:30]
            )
        return {"output": out[:16000], "exit_code": 0}

    if action == "delete":
        if not rid:
            return {"error": "Provide the research id to delete (from action='list')."}
        if not _confirmed():
            return _confirmation_required("delete")
        p = data_dir / f"{rid}.json"
        if p.exists():
            d = _load(p)
            if not _visible_to_owner(d):
                return {"error": f"Research '{rid}' not found."}
            try:
                p.unlink()
            except Exception as e:
                return {"error": f"Failed to delete: {e}"}
            return {"output": f"Deleted research '{rid}'.", "exit_code": 0}
        return {"error": f"Research '{rid}' not found."}

    # default: list — clickable [query](#research-<id>) rows, most-recent first
    search = (args.get("search") or "").lower()
    items = []
    if data_dir.exists():
        for p in data_dir.glob("*.json"):
            d = _load(p)
            if not d:
                continue
            if not _visible_to_owner(d):
                continue
            q = d.get("query", "")
            if search and search not in q.lower():
                continue
            items.append((d.get("completed_at", 0) or 0, p.stem, q, len(d.get("sources", []) or [])))
    items.sort(reverse=True)
    if not items:
        return {"output": "No research found in the library." + (f" (search: {search})" if search else ""), "exit_code": 0}
    rows = "\n".join(f"- [{q or '(untitled)'}](#research-{sid}) — {n} sources" for _, sid, q, n in items[:50])
    return {"output": f"Research library ({len(items)} item{'s' if len(items) != 1 else ''}):\n{rows}", "exit_code": 0}


async def do_trigger_research(content: str, owner: Optional[str] = None) -> Dict:
    """Start a live deep-research job that appears in the Deep Research
    sidebar. Hits /api/research/start (the same path the sidebar's
    'Research' button uses) so the session is discoverable + streamable
    there, rather than creating a scheduled task that never surfaces."""
    import httpx
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    topic = args.get("topic", "") or args.get("query", "")
    if not topic:
        return {"error": "topic (or query) is required", "exit_code": 1}
    payload: Dict[str, Any] = {"query": topic}
    # Optional knobs the research panel supports.
    if args.get("max_rounds") is not None:
        try: payload["max_rounds"] = int(args["max_rounds"])
        except (ValueError, TypeError): pass
    if args.get("max_time") is not None:
        try: payload["max_time"] = int(args["max_time"])
        except (ValueError, TypeError): pass
    if args.get("category"):
        payload["category"] = args["category"]
    if args.get("search_provider"):
        payload["search_provider"] = args["search_provider"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_INTERNAL_BASE}/api/research/start",
                                     json=payload, headers=_internal_headers(owner))
        if resp.status_code >= 400:
            return {"error": f"research/start returned HTTP {resp.status_code}: {resp.text[:200]}", "exit_code": 1}
        data = resp.json()
        sid = data.get("session_id", "?")
        return {
            "output": (
                f"Deep research started: [{topic}](#research-{sid}). "
                "Click to open the Deep Research sidebar and watch progress / read the report."
            ),
            "session_id": sid,
            "anchor": f"[{topic}](#research-{sid})",
            # UI hint so the frontend can open/refresh the research panel.
            "ui_event": "research_started",
            "research_session_id": sid,
            "exit_code": 0,
        }
    except Exception as e:
        return {"error": str(e), "exit_code": 1}


# ── Contact tools ──

async def do_resolve_contact(content: str, owner: Optional[str] = None) -> Dict:
    """Look up a contact by name. Searches: CardDAV -> email history -> memory."""
    import httpx
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    name = args.get("name", "")
    if not name:
        return {"error": "name is required", "exit_code": 1}

    contacts = {}  # email_or_phone -> {name, source, phone?}

    # 1. CardDAV (Radicale) — structured contacts. Call in-process: a
    # server-side httpx GET to /api/contacts/search carries no session
    # cookie and would 401 under require_user.
    try:
        import asyncio
        from routes import contacts_routes as cc
        all_contacts = await asyncio.to_thread(cc._fetch_contacts)
        q = name.lower()
        for c in (all_contacts or []):
            hay_name = (c.get("name") or "").lower()
            match = q in hay_name or any(q in (e or "").lower() for e in c.get("emails", []))
            if not match:
                continue
            has_email = False
            for email in (c.get("emails") or []):
                email = (email or "").strip().lower()
                if email and "@" in email:
                    contacts[email] = {"name": c.get("name") or email, "source": "contacts"}
                    has_email = True
            # Fall back to phone numbers when the contact has no email address
            if not has_email:
                for phone in (c.get("phones") or []):
                    phone = (phone or "").strip()
                    if phone:
                        contacts[phone] = {"name": c.get("name") or phone, "source": "contacts", "phone": phone}
    except Exception:
        pass

    async with httpx.AsyncClient(timeout=30) as client:
        # 2. Email history (sent/received)
        try:
            resp = await client.get(f"{_INTERNAL_BASE}/api/email/resolve-contact", params={"name": name})
            if resp.status_code == 200:
                for c in (resp.json().get("contacts") or []):
                    email = (c.get("email") or "").strip().lower()
                    if email and email not in contacts:
                        contacts[email] = {"name": c.get("name") or email, "source": "email history"}
        except Exception:
            pass

    if not contacts:
        return {"output": f"No contacts found matching '{name}'.", "exit_code": 0}

    lines = [f"Contacts matching '{name}':"]
    for key, info in contacts.items():
        if info.get("phone"):
            lines.append(f"- {info['name']} — phone: {info['phone']} ({info['source']})")
        else:
            lines.append(f"- {info['name']} <{key}> ({info['source']})")
    return {"output": "\n".join(lines), "exit_code": 0}


async def do_manage_contact(content: str, owner: Optional[str] = None) -> Dict:
    """Add / update / delete / list CardDAV contacts. Calls the contacts
    helpers IN-PROCESS rather than over HTTP — a server-side httpx call to
    /api/contacts/* carries no session cookie and would be rejected by
    require_user (401), so the tool would see zero contacts even though
    the browser-side UI works fine."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    action = (args.get("action") or "").strip().lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Contact {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    try:
        from routes import contacts_routes as cc
    except Exception as e:
        return {"error": f"Contacts module unavailable: {e}", "exit_code": 1}
    # The contacts helpers are sync (httpx blocking calls to CardDAV) — run
    # them in a thread so we don't block the event loop.
    import asyncio
    try:
        if action == "list":
            rows = await asyncio.to_thread(cc._fetch_contacts, True)
            if not rows:
                return {"output": "No contacts.", "exit_code": 0}
            lines = [f"{len(rows)} contacts:"]
            for c in rows:
                em = ", ".join(c.get("emails") or [])
                lines.append(f"- {c.get('name') or '(no name)'} <{em}>  [uid={c.get('uid','')}]")
            return {"output": "\n".join(lines), "exit_code": 0}

        if action == "add":
            email = (args.get("email") or "").strip()
            if not email:
                return {"error": "email is required for add", "exit_code": 1}
            name = (args.get("name") or "").strip() or email.split("@")[0]
            # Dedupe by email (same as the /add route).
            existing = await asyncio.to_thread(cc._fetch_contacts)
            for c in existing:
                if email.lower() in [e.lower() for e in c.get("emails", [])]:
                    return {"output": f"{email} is already a contact ({c.get('name','')}).", "exit_code": 0}
            ok = await asyncio.to_thread(cc._create_contact, name, email)
            return {"output": f"{'Added' if ok else 'Failed to add'} {name} <{email}>.", "exit_code": 0 if ok else 1}

        if action in ("update", "edit"):
            uid = (args.get("uid") or "").strip()
            if not uid:
                return {"error": "uid is required for update (use action=list to find it)", "exit_code": 1}
            name = (args.get("name") or "").strip()
            emails = args.get("emails")
            if emails is None and args.get("email"):
                emails = [args["email"]]
            emails = [e.strip() for e in (emails or []) if e and e.strip()]
            phones = [p.strip() for p in (args.get("phones") or []) if p and p.strip()]
            if not name and not emails:
                return {"error": "Provide a name or emails to update", "exit_code": 1}
            if not name and emails:
                name = emails[0].split("@")[0]
            ok = await asyncio.to_thread(cc._update_contact, uid, name, emails, phones)
            return {"output": "Contact updated." if ok else "Update failed.", "exit_code": 0 if ok else 1}

        if action == "delete":
            uid = (args.get("uid") or "").strip()
            if not uid:
                return {"error": "uid is required for delete (use action=list to find it)", "exit_code": 1}
            if not _confirmed():
                return _confirmation_required("delete")
            ok = await asyncio.to_thread(cc._delete_contact, uid)
            return {"output": "Contact deleted." if ok else "Delete failed.", "exit_code": 0 if ok else 1}

        return {"error": f"Unknown action '{action}'. Use list, add, update, or delete.", "exit_code": 1}
    except Exception as e:
        return {"error": f"Contact operation failed: {e}", "exit_code": 1}


# ── Vaultwarden / Bitwarden CLI tools ──

def _load_vault_config() -> Dict:
    """Load Vaultwarden config from data/vault.json."""
    from pathlib import Path
    p = Path(VAULT_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


async def _run_bw(args: list, session: Optional[str] = None, input_text: Optional[str] = None) -> tuple:
    """Run a bw CLI command with optional session + stdin. Returns (stdout, stderr, returncode)."""
    import asyncio
    env = {}
    import os as _os
    env.update(_os.environ)
    if session:
        env["BW_SESSION"] = session

    proc = await asyncio.create_subprocess_exec(
        "bw", *args,
        stdin=asyncio.subprocess.PIPE if input_text else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate(input=input_text.encode() if input_text else None)
    return stdout.decode(errors="replace").strip(), stderr.decode(errors="replace").strip(), proc.returncode


async def do_vault_search(content: str, owner: Optional[str] = None) -> Dict:
    """Search the vault by keyword. Returns matching item names + URLs, NO passwords."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    query = args.get("query", "").strip()
    if not query:
        return {"error": "query is required", "exit_code": 1}

    cfg = _load_vault_config()
    session = cfg.get("session")
    if not session:
        return {"error": "Vault is locked. Run vault_unlock or provide session key in settings.", "exit_code": 1}

    stdout, stderr, rc = await _run_bw(["list", "items", "--search", query], session=session)
    if rc != 0:
        return {"error": f"bw failed: {stderr[:300]}", "exit_code": 1}

    try:
        items = json.loads(stdout)
    except json.JSONDecodeError:
        return {"error": "Failed to parse bw output", "exit_code": 1}

    if not items:
        return {"output": f"No vault items match '{query}'.", "exit_code": 0}

    lines = [f"Found {len(items)} item(s) matching '{query}':"]
    for it in items[:20]:
        item_id = it.get("id", "?")
        name = it.get("name", "?")
        login = it.get("login") or {}
        username = login.get("username", "")
        uris = login.get("uris") or []
        url = uris[0].get("uri", "") if uris else ""
        parts = [f"[{item_id[:8]}] {name}"]
        if username:
            parts.append(f"user: {username}")
        if url:
            parts.append(f"url: {url}")
        lines.append("- " + " · ".join(parts))
    lines.append("\nUse vault_get(item_id, reason) to retrieve the password.")
    return {"output": "\n".join(lines), "exit_code": 0}


async def do_vault_get(content: str, owner: Optional[str] = None) -> Dict:
    """Retrieve a full vault entry (including password) by item ID. Logs access to assistant chat."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    item_id = args.get("item_id", "").strip()
    reason = args.get("reason", "").strip()
    if not item_id:
        return {"error": "item_id is required", "exit_code": 1}
    if not reason:
        return {"error": "reason is required — explain WHY you need this password", "exit_code": 1}

    cfg = _load_vault_config()
    session = cfg.get("session")
    if not session:
        return {"error": "Vault is locked. Unlock first.", "exit_code": 1}

    stdout, stderr, rc = await _run_bw(["get", "item", item_id], session=session)
    if rc != 0:
        return {"error": f"bw failed: {stderr[:300]}", "exit_code": 1}

    try:
        item = json.loads(stdout)
    except json.JSONDecodeError:
        return {"error": "Failed to parse bw output", "exit_code": 1}

    login = item.get("login") or {}
    name = item.get("name", "?")

    # Audit log to assistant chat
    try:
        from src.assistant_log import log_to_assistant
        if owner:
            log_to_assistant(
                owner,
                f"Retrieved password for **{name}** — reason: {reason}",
                category="Vault",
            )
    except Exception:
        pass

    output = [
        f"Vault item: {name}",
        f"Username: {login.get('username', '(none)')}",
        f"Password: {login.get('password', '(none)')}",
    ]
    if login.get("totp"):
        output.append(f"TOTP secret: {login['totp']}")
    uris = login.get("uris") or []
    if uris:
        output.append("URLs: " + ", ".join(u.get("uri", "") for u in uris))
    if item.get("notes"):
        output.append(f"Notes: {item['notes']}")

    return {"output": "\n".join(output), "exit_code": 0}


async def do_vault_unlock(content: str, owner: Optional[str] = None) -> Dict:
    """Unlock the vault using a master password. Stores the resulting session key."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    master_password = args.get("master_password", "")
    if not master_password:
        return {"error": "master_password is required", "exit_code": 1}

    # Do not pass the master password as an argv element. Local process lists
    # can expose argv to other users; stdin keeps the secret out of `ps`.
    stdout, stderr, rc = await _run_bw(["unlock", "--raw"], input_text=master_password + "\n")
    if rc != 0:
        return {"error": f"Unlock failed: {stderr[:300]}", "exit_code": 1}

    session = stdout.strip()
    if not session:
        return {"error": "bw returned empty session", "exit_code": 1}

    # Save session to vault.json
    from pathlib import Path
    p = Path(VAULT_FILE)
    cfg = {}
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    cfg["session"] = session
    from datetime import datetime as _dt
    cfg["unlocked_at"] = _dt.utcnow().isoformat()
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        import os as _os
        _os.chmod(str(p), 0o600)
    except Exception:
        pass

    return {"output": "Vault unlocked. Session saved.", "exit_code": 0}

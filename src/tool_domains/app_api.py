"""App API and shared Cookbook loopback helpers for agent tools."""

import json
import logging
import re
from typing import Any, Dict, List, Optional

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



"""System Health Checker plugin scaffold.

The plugin exposes a stable health snapshot surface without executing host
commands from the Odysseus container. A Debian host-agent can be wired in later.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

try:
    from .health_model import build_agent_offline_snapshot
except ImportError:  # pragma: no cover - supports direct plugin.py loading
    from health_model import build_agent_offline_snapshot


PLUGIN = {
    "name": "System Health Checker",
    "version": "0.1.0",
    "author": "Odysseus",
    "description": "Homeserver health snapshots via a host-agent boundary. No host commands run in Odysseus.",
    "category": "Operations",
    "permission": "admin",
    "kind": "ui",
    "capabilities": ["local_api"],
    "requires": ["odysseus-health-agent (planned external Debian service)"],
    "ui": {"open": "/api/plugins/system_health_checker/app", "label": "Open Health"},
}


_CHEVRON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>'
)


def _app_html(nonce: str) -> str:
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>System Health Checker</title>
<link rel="stylesheet" href="/static/plugin-theme.css">
<script src="/static/js/plugin-theme.js"></script>
</head><body>
<header class="od-header">
  <a class="brand" href="/" title="Back to Odysseus">{_CHEVRON}<span>Odysseus</span></a>
  <span class="od-title">System Health Checker</span>
</header>
<main class="od-wrap">
  <h1>Homeserver health</h1>
  <section class="od-card">
    <p class="muted">Host-agent integration is not configured yet.</p>
    <p>This plugin currently exposes the stable health snapshot contract and an
    offline state. It does not run host commands from the Odysseus container.</p>
    <div id="health-status" class="badge warn">Loading health snapshot...</div>
    <pre id="health-details" class="muted" style="white-space:pre-wrap;margin-top:12px"></pre>
  </section>
</main>
<script nonce="{nonce}">
(async () => {{
  const status = document.getElementById("health-status");
  const details = document.getElementById("health-details");
  try {{
    const response = await fetch("/api/plugins/system_health_checker/health", {{ credentials: "same-origin" }});
    const snapshot = await response.json();
    status.textContent = `Health: ${{snapshot.state}}`;
    status.className = snapshot.state === "ok" ? "badge ok" : "badge warn";
    details.textContent = (snapshot.collectors || []).map((collector) =>
      `${{collector.kind}}: ${{collector.state}} - ${{collector.summary}}`
    ).join("\\n");
  }} catch (error) {{
    status.textContent = "Health snapshot unavailable";
    status.className = "badge warn";
    details.textContent = String(error && error.message ? error.message : error);
  }}
}})();
</script>
</body></html>"""


def setup(ctx):
    router = APIRouter(prefix="/api/plugins/system_health_checker", tags=["plugin:system_health_checker"])

    @router.get("/health")
    async def health(request: Request):
        snapshot = build_agent_offline_snapshot(observed_at="unknown")
        return snapshot.to_dict()

    @router.get("/app")
    async def app_page(request: Request):
        return HTMLResponse(_app_html(getattr(request.state, "csp_nonce", "")))

    ctx.add_router(router)
    ctx.logger.info("system health checker plugin ready")

"""Telegram status plugin.

This plugin intentionally does not call Telegram, store tokens, or send
messages. It only exposes a redacted readiness surface for manual testing.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


PLUGIN = {
    "name": "Telegram Status",
    "version": "0.1.0",
    "author": "Odysseus",
    "description": "Redacted Telegram readiness status. No token display, network call, or message send.",
    "category": "Communications",
    "permission": "admin",
    "kind": "ui",
    "capabilities": ["local_api"],
    "ui": {"open": "/api/plugins/telegram_status/app", "label": "Open Telegram"},
}


_CHEVRON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>'
)


def build_telegram_status() -> dict[str, object]:
    token_present = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_present = bool(os.getenv("TELEGRAM_CHAT_ID"))
    if token_present and chat_present:
        state = "manual_send_smoke_ready"
        summary = "Telegram env markers are present; sending still requires explicit operator approval."
    elif token_present:
        state = "needs_chat_id"
        summary = "Telegram token env marker is present; chat id env marker is still missing."
    else:
        state = "needs_token"
        summary = "Telegram token env marker is missing."

    return {
        "plugin": "telegram_status",
        "state": state,
        "summary": summary,
        "token_env_present": token_present,
        "chat_id_env_present": chat_present,
        "token_value_visible": False,
        "chat_id_value_visible": False,
        "network_enabled": False,
        "send_enabled": False,
        "next_allowed_action": "Set local env markers and request an explicit manual send-smoke go.",
    }


def _app_html(nonce: str) -> str:
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telegram Status</title>
<link rel="stylesheet" href="/static/plugin-theme.css">
<script src="/static/js/plugin-theme.js"></script>
</head><body>
<header class="od-header">
  <a class="brand" href="/" title="Back to Odysseus">{_CHEVRON}<span>Odysseus</span></a>
  <span class="od-title">Telegram Status</span>
</header>
<main class="od-wrap">
  <h1>Telegram readiness</h1>
  <section class="od-card">
    <p class="muted">This plugin never displays tokens, calls Telegram, or sends messages.</p>
    <div id="telegram-status" class="badge warn">Loading Telegram readiness...</div>
    <pre id="telegram-details" class="muted" style="white-space:pre-wrap;margin-top:12px"></pre>
  </section>
</main>
<script nonce="{nonce}">
(async () => {{
  const status = document.getElementById("telegram-status");
  const details = document.getElementById("telegram-details");
  try {{
    const response = await fetch("/api/plugins/telegram_status/status", {{ credentials: "same-origin" }});
    const snapshot = await response.json();
    status.textContent = `Telegram: ${{snapshot.state}}`;
    status.className = snapshot.state === "manual_send_smoke_ready" ? "badge ok" : "badge warn";
    details.textContent = [
      snapshot.summary,
      `Token env present: ${{snapshot.token_env_present ? "yes" : "no"}}`,
      `Chat env present: ${{snapshot.chat_id_env_present ? "yes" : "no"}}`,
      "Network enabled: no",
      "Send enabled: no"
    ].join("\\n");
  }} catch (error) {{
    status.textContent = "Telegram readiness unavailable";
    status.className = "badge warn";
    details.textContent = String(error && error.message ? error.message : error);
  }}
}})();
</script>
</body></html>"""


def setup(ctx):
    router = APIRouter(prefix="/api/plugins/telegram_status", tags=["plugin:telegram_status"])

    @router.get("/status")
    async def status(request: Request):
        return build_telegram_status()

    @router.get("/app")
    async def app_page(request: Request):
        return HTMLResponse(_app_html(getattr(request.state, "csp_nonce", "")))

    ctx.add_router(router)
    ctx.logger.info("telegram status plugin ready")

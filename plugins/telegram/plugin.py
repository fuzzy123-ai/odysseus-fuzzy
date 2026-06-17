"""Standalone Telegram plugin.

The plugin is intentionally safe-by-default: it does not call Telegram, store
tokens, display secrets, or send messages. It exposes a redacted readiness
surface so an operator can decide when to run a later manual smoke.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse


PLUGIN = {
    "name": "Telegram",
    "version": "0.1.0",
    "author": "Odysseus",
    "description": "Standalone Telegram readiness plugin. No token display, network call, or message send by default.",
    "category": "Communications",
    "permission": "admin",
    "kind": "ui",
    "capabilities": ["local_api"],
    "ui": {"open": "/api/plugins/telegram/app", "label": "Open Telegram"},
}


_CHEVRON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>'
)


def build_telegram_readiness() -> dict[str, object]:
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
        "plugin": "telegram",
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
<title>Telegram</title>
<link rel="stylesheet" href="/static/plugin-theme.css">
<script src="/static/js/plugin-theme.js"></script>
</head><body>
<header class="od-header">
  <a class="brand" href="/" title="Back to Odysseus">{_CHEVRON}<span>Odysseus</span></a>
  <span class="od-title">Telegram</span>
</header>
<main class="od-wrap">
  <h1>Telegram readiness</h1>
  <section class="od-card">
    <p class="muted">Standalone plugin. It never displays tokens, calls Telegram, or sends messages by default.</p>
    <div id="telegram-status" class="badge warn">Loading Telegram readiness...</div>
    <pre id="telegram-details" class="muted" style="white-space:pre-wrap;margin-top:12px"></pre>
  </section>
</main>
<script nonce="{nonce}">
(async () => {{
  const status = document.getElementById("telegram-status");
  const details = document.getElementById("telegram-details");
  try {{
    const response = await fetch("/api/plugins/telegram/status", {{ credentials: "same-origin" }});
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
    router = APIRouter(prefix="/api/plugins/telegram", tags=["plugin:telegram"])

    @router.get("/status")
    async def status(request: Request):
        return build_telegram_readiness()

    @router.get("/app")
    async def app_page(request: Request):
        return HTMLResponse(_app_html(getattr(request.state, "csp_nonce", "")))

    ctx.add_router(router)
    ctx.logger.info("telegram plugin ready")

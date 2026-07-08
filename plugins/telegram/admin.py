"""Telegram admin/readiness helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.privacy_runtime import is_dsgvo_mode_enabled, runtime_requires_local_only

from plugins.telegram.status import build_telegram_gate_statuses, telegram_gate_statuses_to_dict
from plugins.telegram.stores import TelegramInboxStore, TelegramPrivacyPinStore


_CHEVRON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>'
)


def _bool_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _voice_stt_enabled() -> bool:
    return _bool_env("TELEGRAM_VOICE_STT_ENABLED") or _bool_env("TELEGRAM_STT_ENABLED")


def _draft_interval_ms() -> int:
    raw = os.getenv("TELEGRAM_DRAFT_INTERVAL_MS") or "750"
    try:
        value = int(raw)
    except ValueError:
        value = 750
    return max(250, min(value, 10000))


def _dsgvo_mode_active(settings_loader: Any | None = None) -> bool:
    try:
        if settings_loader is None:
            from src.settings import load_settings

            settings_loader = load_settings
        settings: dict[str, Any] | None = dict(settings_loader() or {})
    except Exception:
        settings = None
    return is_dsgvo_mode_enabled(settings=settings)


def _privacy_pin_enabled() -> bool:
    return not _bool_env("TELEGRAM_PRIVACY_PIN_DISABLED")


def build_telegram_readiness(
    data_dir: str | Path | None = None,
    *,
    dsgvo_settings_loader: Any | None = None,
) -> dict[str, Any]:
    token_present = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_present = bool(os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))
    agent_chat_enabled = _bool_env("TELEGRAM_AGENT_CHAT_ENABLED")
    reply_enabled = _bool_env("TELEGRAM_AGENT_REPLY_ENABLED")
    polling_enabled = _bool_env("TELEGRAM_POLLING_ENABLED")
    rich_messages_enabled = _bool_env("TELEGRAM_RICH_MESSAGES_ENABLED")
    rich_drafts_enabled = _bool_env("TELEGRAM_RICH_DRAFTS_ENABLED")
    dsgvo_mode = _dsgvo_mode_active(dsgvo_settings_loader)
    readiness_gates = telegram_gate_statuses_to_dict(build_telegram_gate_statuses())

    if token_present and chat_present and agent_chat_enabled and reply_enabled:
        state = "agent_reply_ready"
        summary = "Telegram agent chat and replies are locally enabled."
    elif token_present and agent_chat_enabled:
        state = "agent_receive_ready"
        summary = "Telegram agent intake is enabled; replies still require allowed chat ids and reply gate."
    elif token_present:
        state = "token_ready"
        summary = "Telegram token env marker is present; agent chat is not enabled yet."
    else:
        state = "needs_token"
        summary = "Telegram token env marker is missing."

    if data_dir is not None:
        inbox_store = TelegramInboxStore(data_dir)
        pin_store = TelegramPrivacyPinStore(data_dir)
        counts = inbox_store.counts()
        delivery = inbox_store.last_delivery_summary()
        active_privacy_pins = pin_store.active_count()
    else:
        counts = {"total": 0, "inbound": 0, "outbound": 0, "voice": 0, "image": 0, "pending_stt": 0, "pending_image_action": 0}
        delivery = {
            "last_delivery_mode": "",
            "last_delivery_status": "",
            "formatting_mode": "html",
            "raw_rich_payload_visible": False,
        }
        active_privacy_pins = 0
    return {
        "plugin": "telegram",
        "state": state,
        "summary": summary,
        "token_env_present": token_present,
        "chat_id_env_present": chat_present,
        "agent_chat_enabled": agent_chat_enabled,
        "reply_gate_enabled": reply_enabled,
        "polling_enabled": polling_enabled,
        "rich_messages_enabled": rich_messages_enabled,
        "rich_drafts_enabled": rich_drafts_enabled,
        "draft_interval_ms": _draft_interval_ms(),
        "formatting_mode": delivery["formatting_mode"],
        "last_delivery_mode": delivery["last_delivery_mode"],
        "last_delivery_status": delivery["last_delivery_status"],
        "token_value_visible": False,
        "chat_id_value_visible": False,
        "raw_rich_payload_visible": delivery["raw_rich_payload_visible"],
        "readiness_gates": readiness_gates,
        "network_enabled": bool(token_present and reply_enabled),
        "send_enabled": bool(token_present and chat_present and reply_enabled),
        "history_counts": counts,
        "voice_boundary": {
            "mode": "fakeable_pipeline" if _bool_env("TELEGRAM_VOICE_DOWNLOAD_ENABLED") or _voice_stt_enabled() else "metadata_only",
            "pending_stt_count": int(counts.get("pending_stt") or 0),
            "download_enabled": _bool_env("TELEGRAM_VOICE_DOWNLOAD_ENABLED"),
            "stt_enabled": _voice_stt_enabled(),
            "stt_gate_names": ["TELEGRAM_VOICE_STT_ENABLED", "TELEGRAM_STT_ENABLED"],
            "raw_voice_ids_visible": False,
        },
        "image_boundary": {
            "mode": "worker_client_ready" if _bool_env("TELEGRAM_IMAGE_ACTIONS_ENABLED") else "metadata_only",
            "pending_image_action_count": int(counts.get("pending_image_action") or 0),
            "image_actions_enabled": _bool_env("TELEGRAM_IMAGE_ACTIONS_ENABLED"),
            "raw_image_ids_visible": False,
        },
        "privacy_boundary": {
            "dsgvo_mode": dsgvo_mode,
            "local_only_required": runtime_requires_local_only(settings={"dsgvo_mode": dsgvo_mode}),
            "telegram_control_enabled": True,
            "telegram_commands": ["/dsgvo", "/privacy", "/gdpr", "/inbox"],
            "settings_values_visible": False,
            "pinned_status_enabled": _privacy_pin_enabled(),
            "active_pinned_status_count": active_privacy_pins,
            "pin_message_id_value_visible": False,
            "chat_feedback_modes": ["reply_message", "typing_indicator", "pinned_status_message", "status_endpoint"],
        },
        "next_allowed_action": "Enable TELEGRAM_AGENT_CHAT_ENABLED for intake and TELEGRAM_AGENT_REPLY_ENABLED for bot replies.",
    }


def app_html(nonce: str) -> str:
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
  <h1>Telegram agent chat</h1>
  <section class="od-card">
    <p class="muted">Standalone plugin for Telegram intake, local history, agent bridge payloads, and gated replies.</p>
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
    status.className = snapshot.send_enabled ? "badge ok" : "badge warn";
    details.textContent = [
      snapshot.summary,
      `Token env present: ${{snapshot.token_env_present ? "yes" : "no"}}`,
      `Allowed chat marker present: ${{snapshot.chat_id_env_present ? "yes" : "no"}}`,
      `Agent intake enabled: ${{snapshot.agent_chat_enabled ? "yes" : "no"}}`,
      `Reply gate enabled: ${{snapshot.reply_gate_enabled ? "yes" : "no"}}`,
      `History total: ${{snapshot.history_counts.total}}`,
      `Voice messages pending/seen: ${{snapshot.history_counts.voice}}`
    ].join("\\n");
  }} catch (error) {{
    status.textContent = "Telegram readiness unavailable";
    status.className = "badge warn";
    details.textContent = String(error && error.message ? error.message : error);
  }}
}})();
</script>
</body></html>"""

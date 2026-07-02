"""Shared backend delivery for user notifications.

The public contract accepts only a logical channel and message. This module is
the server-side bridge that may resolve configured Telegram targets without
letting tasks, agents, reminders, or documents pass chat IDs or tokens around.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from src.user_notification_contract import build_user_notification_decision


def _bool_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _configured_telegram_target() -> str:
    explicit = (os.getenv("TELEGRAM_NOTIFICATION_CHAT_ID") or "").strip()
    if explicit:
        return explicit
    allowed = (os.getenv("TELEGRAM_ALLOWED_CHAT_IDS") or "").strip()
    if allowed:
        return allowed.split(",", 1)[0].strip()
    return (os.getenv("TELEGRAM_CHAT_ID") or "").strip()


def _telegram_target_configured() -> bool:
    return bool(_configured_telegram_target())


async def deliver_user_notification(payload: Mapping[str, Any] | str) -> dict[str, Any]:
    decision = build_user_notification_decision(
        payload,
        configured_channels=("telegram",),
        live_dispatch_enabled=_bool_env("TELEGRAM_AGENT_REPLY_ENABLED"),
        target_configured=_telegram_target_configured(),
    )
    public = decision.as_public_dict()
    if not decision.dispatch_allowed:
        public["delivery_status"] = decision.status
        return public

    if decision.resolved_channel != "telegram":
        public["delivery_status"] = "blocked"
        public["reason"] = "unsupported_channel"
        return public

    target = _configured_telegram_target()
    try:
        from plugins.telegram.plugin import _chat_allowed, send_telegram_text

        if not _chat_allowed(target):
            public["delivery_status"] = "blocked"
            public["reason"] = "telegram_target_not_allowed"
            return public
        sent = send_telegram_text(target, decision.rendered_text)
        ok = bool(sent.get("ok")) if isinstance(sent, dict) else bool(sent)
        public["delivery_status"] = "dispatched" if ok else "failed"
        public["dispatch_allowed"] = ok
        if not ok:
            public["reason"] = "telegram_send_failed"
        return public
    except Exception as exc:
        public["delivery_status"] = "failed"
        public["dispatch_allowed"] = False
        public["reason"] = type(exc).__name__
        return public

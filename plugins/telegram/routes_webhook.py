"""Webhook route registration for the Telegram plugin."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Request


WebhookHandler = Callable[[Request], Awaitable[dict[str, Any]]]


def register_telegram_webhook_routes(
    router: APIRouter,
    *,
    require_admin: Callable[[Request], None],
    handle_webhook: WebhookHandler,
) -> None:
    """Register Telegram webhook route on an existing router."""

    @router.post("/webhook")
    async def webhook(request: Request):
        require_admin(request)
        return await handle_webhook(request)

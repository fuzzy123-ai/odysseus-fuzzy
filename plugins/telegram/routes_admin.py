"""Admin/status routes for the Telegram plugin."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from plugins.telegram.admin import app_html
from plugins.telegram.stores import TelegramInboxStore


def register_telegram_admin_routes(
    router: APIRouter,
    *,
    data_dir: str | Path,
    inbox_store: TelegramInboxStore,
    require_admin: Callable[[Request], None],
    build_readiness: Callable[[str | Path | None], dict[str, Any]],
) -> None:
    """Register read-only Telegram admin routes on an existing router."""

    @router.get("/status")
    async def status(request: Request):
        require_admin(request)
        return build_readiness(data_dir)

    @router.get("/history")
    async def history(request: Request, chat_id: str | None = None, limit: int = 50):
        require_admin(request)
        return {"messages": inbox_store.audit_history(chat_id=chat_id, limit=limit)}

    @router.get("/app")
    async def app_page(request: Request):
        require_admin(request)
        return HTMLResponse(app_html(getattr(request.state, "csp_nonce", "")))

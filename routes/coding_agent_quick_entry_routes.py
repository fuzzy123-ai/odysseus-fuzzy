"""Read-only coding-agent quick entry routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.coding_agent_quick_entry import build_coding_agent_quick_entry


def setup_coding_agent_quick_entry_routes() -> APIRouter:
    router = APIRouter(tags=["coding-agent"])

    @router.get("/api/coding-agent/quick-entry")
    def coding_agent_quick_entry(request: Request):
        require_admin(request)
        return build_coding_agent_quick_entry()

    return router

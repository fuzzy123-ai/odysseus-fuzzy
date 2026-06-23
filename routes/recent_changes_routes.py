"""Routes for local recent-change patch-note snapshots."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from src.recent_changes import collect_recent_changes, list_change_history, read_change_snapshot


def setup_recent_changes_routes() -> APIRouter:
    router = APIRouter(tags=["recent-changes"])

    @router.get("/api/system/recent-changes")
    async def get_recent_changes(
        request: Request,
        hours: int = 12,
        persist: bool = True,
        force: bool = False,
    ) -> dict[str, Any]:
        require_admin(request)
        return collect_recent_changes(hours=hours, persist=persist, force=force)

    @router.get("/api/system/recent-changes/history")
    async def get_recent_changes_history(request: Request, limit: int = 20) -> dict[str, Any]:
        require_admin(request)
        return {"items": list_change_history(limit=limit)}

    @router.get("/api/system/recent-changes/{snapshot_id}")
    async def get_recent_changes_snapshot(request: Request, snapshot_id: str) -> dict[str, Any]:
        require_admin(request)
        snapshot = read_change_snapshot(snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Recent changes snapshot not found")
        return snapshot

    return router

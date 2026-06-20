"""Admin routes for read-only update and backup status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.system_update_status import collect_system_update_status, start_system_update_action


def setup_system_update_routes() -> APIRouter:
    router = APIRouter(tags=["system-update"])

    @router.get("/api/admin/system/update-status")
    async def get_update_status(request: Request) -> dict[str, Any]:
        require_admin(request)
        return collect_system_update_status()

    @router.post("/api/admin/system/update-check")
    async def check_update_status(request: Request) -> dict[str, Any]:
        require_admin(request)
        return collect_system_update_status(force_version_refresh=True)

    @router.post("/api/admin/system/backup-now")
    async def backup_now(request: Request) -> dict[str, Any]:
        require_admin(request)
        return start_system_update_action("backup_now")

    @router.post("/api/admin/system/update-now")
    async def update_now(request: Request) -> dict[str, Any]:
        require_admin(request)
        return start_system_update_action("update_now")

    return router

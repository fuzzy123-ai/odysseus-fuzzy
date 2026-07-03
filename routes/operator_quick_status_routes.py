"""Read-only operator quick status routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

from core.database import McpServer, SessionLocal
from core.middleware import require_admin
from plugins.system_health_checker.health_model import build_agent_offline_snapshot
from src.constants import APP_VERSION
from src.operator_quick_status import build_operator_quick_status


def setup_operator_quick_status_routes(mcp_manager: Any = None) -> APIRouter:
    router = APIRouter(tags=["diagnostics"])

    @router.get("/api/diagnostics/quick-status")
    async def diagnostics_quick_status(request: Request):
        require_admin(request)
        db = SessionLocal()
        try:
            servers = db.query(McpServer).all()
        finally:
            db.close()

        observed_at = datetime.now(timezone.utc).isoformat()
        system_health = build_agent_offline_snapshot(observed_at=observed_at).to_dict()
        return build_operator_quick_status(
            mcp_manager=mcp_manager,
            mcp_servers=servers,
            system_health=system_health,
            app_version=APP_VERSION,
        )

    return router

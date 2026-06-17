"""Read-only agent team-card routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.agent_team_card_api import build_default_agent_team_card_payload


def setup_agent_team_routes() -> APIRouter:
    router = APIRouter(prefix="/api/agents", tags=["agents"])

    @router.get("/team-card")
    def api_agent_team_card(request: Request):
        """Return the safe default team-card payload for Main Agent and UI."""
        require_admin(request)
        return build_default_agent_team_card_payload().to_dict()

    return router

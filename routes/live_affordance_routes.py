"""Read-only readiness routes for live affordances."""

from __future__ import annotations

from fastapi import APIRouter, Request

from core.middleware import require_admin
from src.live_affordance_readiness import build_live_affordance_readiness


def setup_live_affordance_routes() -> APIRouter:
    router = APIRouter(tags=["live-affordances"])

    @router.get("/api/live-affordances/readiness")
    def live_affordance_readiness(request: Request):
        require_admin(request)
        return build_live_affordance_readiness()

    return router

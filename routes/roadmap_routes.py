"""Read-only roadmap graph routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from src.plan_runtime import PlanRuntimeError, PlanRuntimeState
from src.roadmap_lens import build_roadmap_lens_page


def setup_roadmap_routes() -> APIRouter:
    router = APIRouter(prefix="/api/roadmap", tags=["roadmap"])

    @router.get("/graph")
    def api_roadmap_graph(request: Request):
        require_admin(request)
        try:
            runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
            return build_roadmap_lens_page(runtime).to_dict()
        except (OSError, PlanRuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"roadmap graph unavailable: {exc}") from exc

    return router

"""Read-only roadmap graph routes."""

from __future__ import annotations

from datetime import datetime, UTC
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from src.orchestration_dashboard import build_orchestration_dashboard_snapshot
from src.plan_runtime import PlanRuntimeError, PlanRuntimeState
from src.planning_source_inventory import build_planning_source_inventory
from src.roadmap_lens import build_roadmap_lens_page
from src.visual_agent_programming_lens import (
    apply_visual_plan_mutation_patch,
    build_visual_agent_programming_snapshot,
    build_visual_plan_mutation_patch,
    build_visual_plan_proposal_queue,
    validate_visual_plan_acceptance,
    validate_visual_plan_edit,
)


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

    @router.get("/dashboard")
    def api_roadmap_dashboard(request: Request):
        require_admin(request)
        try:
            runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
            snapshot = build_orchestration_dashboard_snapshot(
                runtime,
                last_updated_at=datetime.now(UTC).isoformat(),
            )
            return snapshot.to_dict()
        except (OSError, PlanRuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"roadmap dashboard unavailable: {exc}") from exc

    @router.get("/planning-sources/inventory")
    def api_planning_sources_inventory(request: Request, preview_chars: int = 240):
        require_admin(request)
        try:
            repo_root = os.getenv("ODYSSEUS_ROOT") or str(Path(__file__).resolve().parents[1])
            return build_planning_source_inventory(repo_root, preview_chars=max(0, min(int(preview_chars), 1000)))
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"planning source inventory unavailable: {exc}") from exc

    @router.get("/visual-agent-programming")
    def api_visual_agent_programming(request: Request):
        require_admin(request)
        try:
            runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
            snapshot = build_visual_agent_programming_snapshot(
                runtime,
                last_updated_at=datetime.now(UTC).isoformat(),
            )
            return snapshot.to_dict()
        except (OSError, PlanRuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"visual agent programming lens unavailable: {exc}") from exc

    @router.post("/visual-agent-programming/validate-edit")
    def api_validate_visual_agent_programming_edit(payload: dict[str, Any], request: Request):
        require_admin(request)
        try:
            runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
            return validate_visual_plan_edit(runtime, payload).to_dict()
        except (OSError, PlanRuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"visual agent programming edit validation unavailable: {exc}") from exc

    @router.post("/visual-agent-programming/proposals/review")
    def api_review_visual_agent_programming_proposals(payload: dict[str, Any], request: Request):
        require_admin(request)
        try:
            runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
            return build_visual_plan_proposal_queue(runtime, payload).to_dict()
        except (OSError, PlanRuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"visual agent programming proposal review unavailable: {exc}") from exc

    @router.post("/visual-agent-programming/proposals/acceptance/validate")
    def api_validate_visual_agent_programming_acceptance(payload: dict[str, Any], request: Request):
        require_admin(request)
        try:
            runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
            return validate_visual_plan_acceptance(runtime, payload).to_dict()
        except (OSError, PlanRuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"visual agent programming acceptance unavailable: {exc}") from exc

    @router.post("/visual-agent-programming/mutations/patch")
    def api_build_visual_agent_programming_mutation_patch(payload: dict[str, Any], request: Request):
        require_admin(request)
        try:
            runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
            return build_visual_plan_mutation_patch(
                runtime,
                payload,
                last_updated_at=datetime.now(UTC).isoformat(),
            ).to_dict()
        except (OSError, PlanRuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"visual agent programming mutation patch unavailable: {exc}") from exc

    @router.post("/visual-agent-programming/mutations/apply")
    def api_apply_visual_agent_programming_mutation(payload: dict[str, Any], request: Request):
        require_admin(request)
        try:
            runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
            return apply_visual_plan_mutation_patch(
                runtime,
                payload,
                last_updated_at=datetime.now(UTC).isoformat(),
            ).to_dict()
        except (OSError, PlanRuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"visual agent programming mutation apply unavailable: {exc}") from exc

    return router

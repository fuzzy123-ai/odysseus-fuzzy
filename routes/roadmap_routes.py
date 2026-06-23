"""Read-only roadmap graph routes."""

from __future__ import annotations

from datetime import datetime, UTC
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from src.constants import DATA_DIR
from src.memory import MemoryManager
from src.orchestration_dashboard import build_orchestration_dashboard_snapshot
from src.plan_runtime import PlanRuntimeError, PlanRuntimeState
from src.planning_source_inventory import build_planning_source_inventory
from src.planning_source_memory import (
    build_planning_memory_capsules,
    ingest_planning_sources_to_memory,
)
from src.roadmap_lens import build_roadmap_lens_page
from src.visual_agent_programming_lens import (
    apply_visual_plan_mutation_patch,
    build_visual_agent_programming_snapshot,
    build_visual_plan_mutation_patch,
    build_visual_plan_proposal_queue,
    validate_visual_plan_acceptance,
    validate_visual_plan_edit,
)
from src.planruntime_post_apply_dispatch import build_post_apply_agent_dispatch_request


def _repo_root() -> str:
    return os.getenv("ODYSSEUS_ROOT") or str(Path(__file__).resolve().parents[1])


def _memory_manager() -> MemoryManager:
    data_dir = os.getenv("ODYSSEUS_DATA_DIR") or DATA_DIR
    os.makedirs(data_dir, exist_ok=True)
    return MemoryManager(data_dir)


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
            return build_planning_source_inventory(_repo_root(), preview_chars=max(0, min(int(preview_chars), 1000)))
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"planning source inventory unavailable: {exc}") from exc

    @router.get("/planning-sources/memory/status")
    def api_planning_sources_memory_status(request: Request, preview_chars: int = 240):
        require_admin(request)
        try:
            manager = _memory_manager()
            dry_run = ingest_planning_sources_to_memory(
                manager,
                _repo_root(),
                preview_chars=max(0, min(int(preview_chars), 1000)),
                dry_run=True,
            )
            return {
                "schema": "odysseus.planning_source_memory_status.v1",
                "capsules": build_planning_memory_capsules(
                    _repo_root(),
                    preview_chars=max(0, min(int(preview_chars), 1000)),
                )["summary"],
                "ingest": dry_run["summary"],
            }
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"planning source memory status unavailable: {exc}") from exc

    @router.post("/planning-sources/memory/ingest")
    def api_planning_sources_memory_ingest(request: Request, preview_chars: int = 240, dry_run: bool = True):
        require_admin(request)
        try:
            return ingest_planning_sources_to_memory(
                _memory_manager(),
                _repo_root(),
                preview_chars=max(0, min(int(preview_chars), 1000)),
                dry_run=bool(dry_run),
            )
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"planning source memory ingest unavailable: {exc}") from exc

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

    @router.post("/visual-agent-programming/dispatch/request")
    def api_request_visual_agent_programming_dispatch(payload: dict[str, Any], request: Request):
        require_admin(request)
        try:
            return build_post_apply_agent_dispatch_request(
                payload,
                created_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            ).to_dict()
        except (PlanRuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"visual agent programming dispatch request unavailable: {exc}") from exc

    return router

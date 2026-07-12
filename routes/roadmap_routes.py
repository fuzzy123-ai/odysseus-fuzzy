"""Read-only roadmap graph routes."""

from __future__ import annotations

from datetime import datetime, UTC
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.middleware import require_admin
from src.constants import DATA_DIR
from src.memory import MemoryManager
from src.mcp_audit_events import build_planning_section_audit_descriptor
from src.orchestration_dashboard import build_orchestration_dashboard_snapshot
from src.plan_runtime import PlanRuntimeError, PlanRuntimeState
from src.planning_source_inventory import build_planning_source_inventory
from src.planning_source_memory import (
    build_planning_memory_capsules,
    ingest_planning_sources_to_memory,
)
from src.planning_mcp_service import PlanningMcpService, PlanningServiceError
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

    @router.get("/documents/{project_id}/{roadmap_id}")
    def api_roadmap_document(
        project_id: str,
        roadmap_id: str,
        request: Request,
        max_items: int = 24,
        canonical_json_chars: int = 8192,
        include_memory: bool = False,
    ):
        require_admin(request)
        try:
            return PlanningMcpService(_repo_root()).read_document(
                project_id,
                roadmap_id,
                max_items=max_items,
                canonical_json_chars=canonical_json_chars,
                include_memory=include_memory,
            )
        except PlanningServiceError as exc:
            status_code = 404 if exc.code == "roadmap_document_not_found" else 400
            raise HTTPException(status_code=status_code, detail=exc.to_dict()) from exc

    @router.post("/documents/{project_id}/{roadmap_id}/proposals")
    def api_roadmap_document_edit_proposal(
        project_id: str,
        roadmap_id: str,
        payload: dict[str, Any],
        request: Request,
    ):
        require_admin(request)
        try:
            proposal = PlanningMcpService(_repo_root()).propose_document_edit(project_id, roadmap_id, payload)
        except PlanningServiceError as exc:
            status_code = 404 if exc.code in {"roadmap_document_not_found", "document_task_not_found"} else 400
            raise HTTPException(status_code=status_code, detail=exc.to_dict()) from exc
        if proposal.get("status") == "conflict":
            return JSONResponse(proposal, status_code=409)
        if proposal.get("status") != "ready":
            return JSONResponse(
                {
                    "schema": proposal.get("schema"),
                    "draft_id": proposal.get("draft_id"),
                    "patch_id": proposal.get("patch_id"),
                    "status": "invalid",
                    "ready_for_apply": False,
                    "writes_performed": False,
                    "required_apply_gate": "PLANNING-APPLY-GO",
                    "validation": proposal.get("validation") or {},
                    "conflicts": proposal.get("conflicts") or [],
                    "warnings": proposal.get("warnings") or [],
                    "rejected_value_visible": False,
                },
                status_code=400,
            )
        return proposal

    def _section_context_response(
        project_id: str,
        roadmap_id: str,
        *,
        section_id: str,
        item_id: str = "",
        task_id: str = "",
        gate_id: str = "",
        max_items: int = 12,
        include_memory: bool = True,
        client_id: str = "",
    ) -> dict[str, Any]:
        arguments = {
            "project_id": project_id,
            "roadmap_id": roadmap_id,
            "section_id": section_id,
            "max_items": max_items,
            "include_memory": include_memory,
        }
        if item_id:
            arguments["item_id"] = item_id
        if task_id:
            arguments["task_id"] = task_id
        if gate_id:
            arguments["gate_id"] = gate_id
        pack = PlanningMcpService(_repo_root()).get_section_context_pack(
            project_id,
            roadmap_id,
            section_id,
            item_id=item_id,
            task_id=task_id,
            gate_id=gate_id,
            max_items=max_items,
            include_memory=include_memory,
        )
        pack["audit_descriptor"] = build_planning_section_audit_descriptor(
            client_id=client_id,
            arguments=arguments,
        )
        return pack

    @router.get("/documents/{project_id}/{roadmap_id}/context-pack")
    def api_roadmap_section_context_pack(
        project_id: str,
        roadmap_id: str,
        request: Request,
        section_id: str,
        item_id: str = "",
        task_id: str = "",
        gate_id: str = "",
        max_items: int = 12,
        include_memory: bool = True,
        client_id: str = "",
    ):
        require_admin(request)
        try:
            return _section_context_response(
                project_id,
                roadmap_id,
                section_id=section_id,
                item_id=item_id,
                task_id=task_id,
                gate_id=gate_id,
                max_items=max_items,
                include_memory=include_memory,
                client_id=client_id,
            )
        except PlanningServiceError as exc:
            status_code = 404 if exc.code in {
                "roadmap_document_not_found", "section_task_not_found", "section_gate_not_found",
            } else 400
            raise HTTPException(status_code=status_code, detail=exc.to_dict()) from exc

    @router.post("/documents/{project_id}/{roadmap_id}/context-pack")
    def api_roadmap_section_context_pack_post(
        project_id: str,
        roadmap_id: str,
        payload: dict[str, Any],
        request: Request,
    ):
        require_admin(request)
        allowed = {"section_id", "item_id", "task_id", "gate_id", "max_items", "include_memory", "client_id"}
        if set(payload) - allowed:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_section_context_request", "message": "Section context request has unsupported fields"},
            )
        try:
            return _section_context_response(
                project_id,
                roadmap_id,
                section_id=str(payload.get("section_id") or ""),
                item_id=str(payload.get("item_id") or ""),
                task_id=str(payload.get("task_id") or ""),
                gate_id=str(payload.get("gate_id") or ""),
                max_items=payload.get("max_items", 12),
                include_memory=payload.get("include_memory", True),
                client_id=str(payload.get("client_id") or ""),
            )
        except PlanningServiceError as exc:
            status_code = 404 if exc.code in {
                "roadmap_document_not_found", "section_task_not_found", "section_gate_not_found",
            } else 400
            raise HTTPException(status_code=status_code, detail=exc.to_dict()) from exc

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

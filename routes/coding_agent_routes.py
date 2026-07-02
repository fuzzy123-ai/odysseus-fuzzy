"""API routes for the backend-only coding-agent lane."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src.coding_agent_backend import (
    CodingAgentBackendError,
    CodingCheckCommand,
    CodingPatchOperation,
    build_coding_task_plan,
    build_coding_handoff_plan,
    build_coding_publish_plan,
    build_coding_subagent_plan,
    create_coding_worktree,
    apply_coding_patch_set,
    evaluate_coding_done_gate,
    evaluate_coding_quality_gate,
    evaluate_coding_worktree_gate,
    repo_git_snapshot_for_coding_task,
)
from src.agent_sandbox_worker import SandboxWorker
from src.coding_agent_sandbox_bridge import CodingAgentSandboxBridgeError, dispatch_coding_checks_to_sandbox
from src.constants import BASE_DIR, DATA_DIR
from src.repo_registry import REPO_REGISTRY_FILE, RepoRegistry
from src.sandbox_job_ledger import SandboxJobLedger


class CodingCheckRequest(BaseModel):
    argv: list[str] = Field(default_factory=list)
    timeout_seconds: int = 300


class CodingTaskPlanRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=500)
    allowed_paths: list[str] = Field(default_factory=list)
    blocked_paths: list[str] = Field(default_factory=list)
    checks: list[CodingCheckRequest] = Field(default_factory=list)
    base_ref: str = ""
    task_id: str = ""
    operator_decision: str = "missing"
    live_enabled: bool | None = None


class CodingQualityGateRequest(BaseModel):
    changed_paths: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    blocked_paths: list[str] = Field(default_factory=list)
    check_results: list[dict[str, Any]] = Field(default_factory=list)


class CodingPatchRequest(BaseModel):
    path: str
    find: str
    replace: str
    expected_replacements: int = 1


class CodingPatchSetRequest(CodingTaskPlanRequest):
    patches: list[CodingPatchRequest] = Field(default_factory=list)
    patch_operator_decision: str = "missing"
    patch_live_enabled: bool | None = None


class CodingDoneGateRequest(CodingQualityGateRequest):
    review_decision: str = "missing"
    reviewed_by: str = ""
    content_reviewed: bool = False


class CodingHandoffPlanRequest(CodingDoneGateRequest):
    objective: str = Field(min_length=1, max_length=500)
    task_id: str
    base_ref: str = ""
    checks: list[CodingCheckRequest] = Field(default_factory=list)
    target_mode: str = "local"


class CodingPublishPlanRequest(CodingDoneGateRequest):
    objective: str = Field(min_length=1, max_length=500)
    task_id: str
    base_ref: str = ""
    checks: list[CodingCheckRequest] = Field(default_factory=list)
    commit_message: str
    remote_name: str = "fuzzy"
    branch_name: str = ""
    commit_sha: str = ""
    commit_confirmed: bool = False
    push_confirmed: bool = False
    operator_go: bool = False


class CodingSubagentPlanRequest(CodingTaskPlanRequest):
    worker_agent_id: str = "bob"
    reviewer_agent_id: str = "charlie"


class CodingSandboxChecksRequest(CodingTaskPlanRequest):
    sandbox_live_enabled: bool = False
    sandbox_operator_go: bool = False


def setup_coding_agent_routes(
    *,
    registry_path: str | Path = REPO_REGISTRY_FILE,
    workspace_base: str | Path = BASE_DIR,
    worktree_base: str | Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/coding-agent", tags=["coding-agent"])
    registry_file = Path(registry_path)
    configured_workspace_base = Path(workspace_base)
    configured_worktree_base = Path(worktree_base) if worktree_base is not None else Path(DATA_DIR) / "coding-worktrees"
    sandbox_worker = SandboxWorker(ledger=SandboxJobLedger(Path(DATA_DIR) / "sandbox_job_ledger"))

    @router.get("/repos/{repo_id}/snapshot")
    def get_repo_snapshot(request: Request, repo_id: str) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            snapshot = repo_git_snapshot_for_coding_task(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
            )
        except CodingAgentBackendError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": True, "coding_snapshot": snapshot}

    @router.post("/repos/{repo_id}/task-plan")
    def create_task_plan(request: Request, repo_id: str, body: CodingTaskPlanRequest) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            plan = build_coding_task_plan(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                objective=body.objective,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                checks=_checks_from_request(body.checks),
                base_ref=body.base_ref,
                task_id=body.task_id,
                worktree_base=configured_worktree_base,
                live_enabled=body.live_enabled,
                operator_decision=body.operator_decision,
            )
        except CodingAgentBackendError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": plan.decision == "plan_ready", "coding_task": plan.to_dict()}

    @router.post("/repos/{repo_id}/worktree")
    def create_worktree(request: Request, repo_id: str, body: CodingTaskPlanRequest) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            report = create_coding_worktree(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                objective=body.objective,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                checks=_checks_from_request(body.checks),
                base_ref=body.base_ref,
                task_id=body.task_id,
                worktree_base=configured_worktree_base,
                live_enabled=body.live_enabled,
                operator_decision=body.operator_decision,
            )
        except CodingAgentBackendError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": report.status == "created", "coding_worktree": report.to_dict()}

    @router.post("/repos/{repo_id}/patch-set")
    def apply_patch_set(request: Request, repo_id: str, body: CodingPatchSetRequest) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            plan = build_coding_task_plan(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                objective=body.objective,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                checks=_checks_from_request(body.checks),
                base_ref=body.base_ref,
                task_id=body.task_id,
                worktree_base=configured_worktree_base,
                live_enabled=body.live_enabled,
                operator_decision=body.operator_decision,
            )
            results = apply_coding_patch_set(
                plan=plan,
                patches=tuple(_patch_from_request(item) for item in body.patches),
                worktree_base=configured_worktree_base,
                live_enabled=body.patch_live_enabled,
                operator_decision=body.patch_operator_decision,
            )
        except CodingAgentBackendError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {
            "success": all(result.ok for result in results),
            "patch_results": [result.to_dict() for result in results],
        }

    @router.post("/quality-gate")
    def evaluate_quality_gate(request: Request, body: CodingQualityGateRequest) -> dict[str, Any]:
        require_admin(request)
        try:
            report = evaluate_coding_quality_gate(
                changed_paths=body.changed_paths,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                check_results=[
                    _command_result_from_payload(item)
                    for item in body.check_results
                ],
            )
        except CodingAgentBackendError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": report.verified, "quality_gate": report.to_dict()}

    @router.post("/repos/{repo_id}/worktree-quality-gate")
    def evaluate_worktree_quality_gate(request: Request, repo_id: str, body: CodingTaskPlanRequest) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            plan = build_coding_task_plan(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                objective=body.objective,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                checks=_checks_from_request(body.checks),
                base_ref=body.base_ref,
                task_id=body.task_id,
                worktree_base=configured_worktree_base,
                allow_existing_worktree=True,
                live_enabled=True,
                operator_decision="go",
            )
            report = evaluate_coding_worktree_gate(
                plan=plan,
                worktree_base=configured_worktree_base,
            )
        except CodingAgentBackendError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": report.verified, "quality_gate": report.to_dict()}

    @router.post("/repos/{repo_id}/sandbox-checks")
    def dispatch_sandbox_checks(request: Request, repo_id: str, body: CodingSandboxChecksRequest) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            plan = build_coding_task_plan(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                objective=body.objective,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                checks=_checks_from_request(body.checks),
                base_ref=body.base_ref,
                task_id=body.task_id,
                worktree_base=configured_worktree_base,
                allow_existing_worktree=True,
                live_enabled=body.live_enabled,
                operator_decision=body.operator_decision,
            )
            dispatch = dispatch_coding_checks_to_sandbox(
                plan=plan,
                worker=sandbox_worker,
                live_enabled=body.sandbox_live_enabled,
                operator_go=body.sandbox_operator_go,
            )
        except (CodingAgentBackendError, CodingAgentSandboxBridgeError) as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": True, "sandbox_dispatch": dispatch.to_dict()}

    @router.post("/done-gate")
    def evaluate_done_gate(request: Request, body: CodingDoneGateRequest) -> dict[str, Any]:
        require_admin(request)
        try:
            quality = evaluate_coding_quality_gate(
                changed_paths=body.changed_paths,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                check_results=[
                    _command_result_from_payload(item)
                    for item in body.check_results
                ],
            )
            report = evaluate_coding_done_gate(
                quality_gate=quality,
                review_decision=body.review_decision,
                reviewed_by=body.reviewed_by,
                content_reviewed=body.content_reviewed,
            )
        except CodingAgentBackendError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": report.done, "done_gate": report.to_dict()}

    @router.post("/repos/{repo_id}/handoff-plan")
    def create_handoff_plan(request: Request, repo_id: str, body: CodingHandoffPlanRequest) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            plan = build_coding_task_plan(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                objective=body.objective,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                checks=_checks_from_request(body.checks),
                base_ref=body.base_ref,
                task_id=body.task_id,
                worktree_base=configured_worktree_base,
                allow_existing_worktree=True,
                live_enabled=True,
                operator_decision="go",
            )
            quality = evaluate_coding_quality_gate(
                changed_paths=body.changed_paths,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                check_results=[
                    _command_result_from_payload(item)
                    for item in body.check_results
                ],
            )
            done = evaluate_coding_done_gate(
                quality_gate=quality,
                review_decision=body.review_decision,
                reviewed_by=body.reviewed_by,
                content_reviewed=body.content_reviewed,
            )
            report = build_coding_handoff_plan(
                plan=plan,
                done_gate=done,
                changed_paths=body.changed_paths,
                target_mode=body.target_mode,
            )
        except CodingAgentBackendError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": report.decision == "plan_ready", "handoff_plan": report.to_dict()}

    @router.post("/repos/{repo_id}/publish-plan")
    def create_publish_plan(request: Request, repo_id: str, body: CodingPublishPlanRequest) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            plan = build_coding_task_plan(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                objective=body.objective,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                checks=_checks_from_request(body.checks),
                base_ref=body.base_ref,
                task_id=body.task_id,
                worktree_base=configured_worktree_base,
                allow_existing_worktree=True,
                live_enabled=True,
                operator_decision="go",
            )
            quality = evaluate_coding_quality_gate(
                changed_paths=body.changed_paths,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                check_results=[
                    _command_result_from_payload(item)
                    for item in body.check_results
                ],
            )
            done = evaluate_coding_done_gate(
                quality_gate=quality,
                review_decision=body.review_decision,
                reviewed_by=body.reviewed_by,
                content_reviewed=body.content_reviewed,
            )
            report = build_coding_publish_plan(
                plan=plan,
                done_gate=done,
                changed_paths=body.changed_paths,
                commit_message=body.commit_message,
                remote_name=body.remote_name,
                branch_name=body.branch_name,
                commit_sha=body.commit_sha,
                commit_confirmed=body.commit_confirmed,
                push_confirmed=body.push_confirmed,
                operator_go=body.operator_go,
            )
        except CodingAgentBackendError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": report.ready, "publish_plan": report.to_dict()}

    @router.post("/repos/{repo_id}/subagents-plan")
    def create_subagents_plan(request: Request, repo_id: str, body: CodingSubagentPlanRequest) -> dict[str, Any]:
        require_admin(request)
        registry = _load_registry(registry_file)
        try:
            plan = build_coding_task_plan(
                registry=registry,
                repo_id=repo_id,
                workspace_base=configured_workspace_base,
                objective=body.objective,
                allowed_paths=body.allowed_paths,
                blocked_paths=body.blocked_paths,
                checks=_checks_from_request(body.checks),
                base_ref=body.base_ref,
                task_id=body.task_id,
                worktree_base=configured_worktree_base,
                allow_existing_worktree=True,
                live_enabled=body.live_enabled,
                operator_decision=body.operator_decision,
            )
            report = build_coding_subagent_plan(
                plan=plan,
                worker_agent_id=body.worker_agent_id,
                reviewer_agent_id=body.reviewer_agent_id,
            )
        except CodingAgentBackendError as exc:
            status = 404 if "unknown repo" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return {"success": report.ready, "subagents_plan": report.to_dict()}

    return router


def _load_registry(path: Path) -> RepoRegistry:
    try:
        return RepoRegistry.load_or_empty(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="repo registry is unreadable") from exc


def _checks_from_request(items: list[CodingCheckRequest]) -> tuple[CodingCheckCommand, ...]:
    try:
        return tuple(
            CodingCheckCommand.create(argv=item.argv, timeout_seconds=item.timeout_seconds)
            for item in items
        )
    except CodingAgentBackendError:
        raise
    except Exception as exc:
        raise CodingAgentBackendError(str(exc)) from exc


def _patch_from_request(item: CodingPatchRequest) -> CodingPatchOperation:
    return CodingPatchOperation.create(
        path=item.path,
        find=item.find,
        replace=item.replace,
        expected_replacements=item.expected_replacements,
    )


def _command_result_from_payload(payload: dict[str, Any]):
    from src.coding_agent_backend import CodingCommandResult

    if not isinstance(payload, dict):
        raise CodingAgentBackendError("check_results must contain objects")
    return CodingCommandResult(
        exit_code=int(payload.get("exit_code", 1)),
        stdout=str(payload.get("stdout", "")),
        stderr=str(payload.get("stderr", "")),
        timed_out=bool(payload.get("timed_out", False)),
        duration_seconds=float(payload.get("duration_seconds", 0.0) or 0.0),
    )

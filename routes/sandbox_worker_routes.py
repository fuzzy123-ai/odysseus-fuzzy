"""Admin routes for the sandbox worker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src.agent_sandbox_contract import SandboxContractError, SandboxJobRequest, SandboxMount, SandboxResourceLimits
from src.agent_sandbox_worker import SandboxWorker, SandboxWorkerError
from src.constants import DATA_DIR
from src.sandbox_job_ledger import SandboxJobLedger, SandboxJobLedgerError


class SandboxWorkerSubmitRequest(BaseModel):
    job: dict[str, Any]
    live_enabled: bool = False
    operator_go: bool = False
    allow_network: bool = False
    allow_rw_mounts: bool = False


class SandboxWorkerCancelRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


def setup_sandbox_worker_routes(*, ledger_root: str | Path | None = None, worker: SandboxWorker | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/sandbox-worker", tags=["sandbox-worker"])
    ledger = SandboxJobLedger(ledger_root or Path(DATA_DIR) / "sandbox_job_ledger")
    sandbox_worker = worker or SandboxWorker(ledger=ledger)

    @router.post("/submit")
    def submit_job(request: Request, body: SandboxWorkerSubmitRequest) -> dict[str, Any]:
        require_admin(request)
        try:
            job = _job_from_payload(body.job)
            result = sandbox_worker.submit(
                job,
                live_enabled=body.live_enabled,
                operator_go=body.operator_go,
                allow_network=body.allow_network,
                allow_rw_mounts=body.allow_rw_mounts,
            )
        except (SandboxContractError, SandboxWorkerError, SandboxJobLedgerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": result.status.status not in {"blocked", "failed"}, "sandbox_worker": result.to_dict()}

    @router.get("/status/{job_id}")
    def get_status(request: Request, job_id: str) -> dict[str, Any]:
        require_admin(request)
        try:
            status = sandbox_worker.status(job_id)
        except (SandboxWorkerError, SandboxJobLedgerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": status.status != "missing", "status": status.to_dict()}

    @router.post("/cancel/{job_id}")
    def cancel_job(request: Request, job_id: str, body: SandboxWorkerCancelRequest | None = None) -> dict[str, Any]:
        require_admin(request)
        try:
            status = sandbox_worker.cancel(job_id)
        except (SandboxWorkerError, SandboxJobLedgerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": True, "status": status.to_dict()}

    @router.get("/artifacts/{job_id}")
    def get_artifacts(request: Request, job_id: str) -> dict[str, Any]:
        require_admin(request)
        try:
            artifacts = sandbox_worker.artifacts(job_id)
        except (SandboxWorkerError, SandboxJobLedgerError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "success": True,
            "job_id": job_id[:80],
            "artifacts": artifacts,
            "artifact_count": len(artifacts),
            "raw_content_visible": False,
        }

    return router


def _job_from_payload(payload: dict[str, Any]) -> SandboxJobRequest:
    if not isinstance(payload, dict):
        raise SandboxWorkerError("job must be an object")
    mounts = tuple(SandboxMount.create(**dict(item)) for item in payload.get("mounts") or ())
    limits_payload = payload.get("limits") if isinstance(payload.get("limits"), dict) else {}
    return SandboxJobRequest.create(
        job_id=payload.get("job_id"),
        argv=tuple(payload.get("argv") or ()),
        image=payload.get("image"),
        mounts=mounts,
        limits=SandboxResourceLimits.create(**limits_payload),
        network_mode=payload.get("network_mode") or "none",
        network_allowlist=tuple(payload.get("network_allowlist") or ()),
        secrets_attached=bool(payload.get("secrets_attached", False)),
    )

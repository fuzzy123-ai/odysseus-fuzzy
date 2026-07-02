"""Bridge coding-agent check plans into sandbox worker jobs."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from src.agent_sandbox_contract import SandboxJobRequest, SandboxMount, SandboxResourceLimits
from src.agent_sandbox_worker import SandboxWorker
from src.agent_sandbox_worker_api import SandboxWorkerStatus
from src.coding_agent_backend import CodingCommandResult, CodingTaskPlan, evaluate_coding_quality_gate


class CodingAgentSandboxBridgeError(ValueError):
    """Raised when a coding-agent task cannot be safely bridged to sandbox jobs."""


@dataclass(frozen=True, slots=True)
class CodingSandboxDispatch:
    task_id: str
    jobs: tuple[SandboxJobRequest, ...]
    statuses: tuple[SandboxWorkerStatus, ...]
    quality_gate: dict[str, Any]
    raw_content_visible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.coding_agent.sandbox_dispatch.v1",
            "task_id": self.task_id,
            "jobs": tuple(job.to_dict() for job in self.jobs),
            "statuses": tuple(status.to_dict() for status in self.statuses),
            "quality_gate": self.quality_gate,
            "raw_content_visible": False,
        }


def build_sandbox_jobs_for_coding_plan(
    plan: CodingTaskPlan,
    *,
    image: str = "localhost/odysseus_odysseus:latest",
) -> tuple[SandboxJobRequest, ...]:
    if not isinstance(plan, CodingTaskPlan):
        raise CodingAgentSandboxBridgeError("plan must be a CodingTaskPlan")
    if plan.decision not in {"plan_ready", "created", "verified"}:
        raise CodingAgentSandboxBridgeError("coding plan is not ready for sandbox dispatch")
    mounts = _mounts_for_allowed_paths(plan.allowed_paths)
    jobs: list[SandboxJobRequest] = []
    for index, check in enumerate(plan.checks, start=1):
        jobs.append(
            SandboxJobRequest.create(
                job_id=f"{plan.task_id}-check-{index}",
                argv=check.argv,
                image=image,
                mounts=mounts,
                limits=SandboxResourceLimits.create(timeout_seconds=check.timeout_seconds, memory_mb=1024, cpu_count=1.0),
                network_mode="none",
                secrets_attached=False,
            )
        )
    return tuple(jobs)


def dispatch_coding_checks_to_sandbox(
    *,
    plan: CodingTaskPlan,
    worker: SandboxWorker,
    changed_paths: Iterable[Any] = (),
    live_enabled: bool = False,
    operator_go: bool = False,
) -> CodingSandboxDispatch:
    jobs = build_sandbox_jobs_for_coding_plan(plan)
    statuses: list[SandboxWorkerStatus] = []
    for job in jobs:
        result = worker.submit(job, live_enabled=live_enabled, operator_go=operator_go)
        statuses.append(result.status)
    check_results = tuple(sandbox_status_to_coding_result(status) for status in statuses)
    quality = evaluate_coding_quality_gate(
        changed_paths=changed_paths,
        allowed_paths=plan.allowed_paths,
        blocked_paths=plan.blocked_paths,
        check_results=check_results,
    )
    return CodingSandboxDispatch(
        task_id=plan.task_id,
        jobs=jobs,
        statuses=tuple(statuses),
        quality_gate=quality.to_dict(),
    )


def sandbox_status_to_coding_result(status: SandboxWorkerStatus | dict[str, Any]) -> CodingCommandResult:
    payload = status.to_dict() if isinstance(status, SandboxWorkerStatus) else dict(status)
    status_text = str(payload.get("status") or "unknown").lower()
    exit_code = payload.get("exit_code")
    if exit_code is None:
        exit_code = 0 if status_text in {"succeeded", "dry_run"} else 1
    return CodingCommandResult(
        exit_code=int(exit_code),
        stdout=str(payload.get("stdout_preview") or ""),
        stderr=str(payload.get("stderr_preview") or ""),
        timed_out=status_text == "timed_out",
        duration_seconds=0.0,
    )


def _mounts_for_allowed_paths(allowed_paths: Iterable[str]) -> tuple[SandboxMount, ...]:
    cleaned: list[str] = []
    for path in allowed_paths:
        safe = _safe_mount_source(path)
        if safe and safe not in cleaned:
            cleaned.append(safe)
    if not cleaned:
        cleaned = ["src", "tests"]
    return tuple(
        SandboxMount.create(source=source, target=f"/workspace/repo/{source}", mode="ro")
        for source in cleaned[:20]
    )


def _safe_mount_source(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/").strip("/")
    if not text or text in {".", ".."} or ".." in text.split("/"):
        raise CodingAgentSandboxBridgeError("allowed path is unsafe for sandbox mount")
    if re.match(r"^[A-Za-z]:", text) or text.startswith("/"):
        raise CodingAgentSandboxBridgeError("allowed path must be repo-relative")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,180}", text):
        raise CodingAgentSandboxBridgeError("allowed path is unsafe for sandbox mount")
    return text

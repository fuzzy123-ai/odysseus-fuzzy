"""Sandbox worker core for dry-run and gated Podman execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping

from src.agent_result_observer import build_sandbox_result_evidence
from src.agent_sandbox_contract import SandboxJobRequest
from src.agent_sandbox_podman_plan import PodmanSandboxPlan, render_podman_sandbox_plan
from src.agent_sandbox_worker_api import SandboxWorkerStatus
from src.sandbox_job_ledger import SandboxJobLedger
from src.sandbox_resource_guard import evaluate_sandbox_resource_guard


SandboxCommandRunner = Callable[[tuple[str, ...], int], "SandboxCommandResult"]


class SandboxWorkerError(ValueError):
    """Raised when a sandbox worker operation is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class SandboxCommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True, slots=True)
class SandboxWorkerSubmitResult:
    job_id: str
    status: SandboxWorkerStatus
    plan: PodmanSandboxPlan
    executed_live: bool
    evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.agent.sandbox_worker.submit_result.v1",
            "job_id": self.job_id,
            "status": self.status.to_dict(),
            "plan": self.plan.to_dict(),
            "executed_live": self.executed_live,
            "evidence": self.evidence,
            "raw_content_visible": False,
        }


class SandboxWorker:
    def __init__(
        self,
        *,
        ledger: SandboxJobLedger | None = None,
        command_runner: SandboxCommandRunner | None = None,
    ):
        self.ledger = ledger or SandboxJobLedger()
        self.command_runner = command_runner or run_podman_command

    def submit(
        self,
        job: SandboxJobRequest,
        *,
        live_enabled: bool = False,
        operator_go: bool = False,
        allow_network: bool = False,
        allow_rw_mounts: bool = False,
    ) -> SandboxWorkerSubmitResult:
        if not isinstance(job, SandboxJobRequest):
            raise SandboxWorkerError("job must be a SandboxJobRequest")
        guard = evaluate_sandbox_resource_guard(
            job,
            allow_network=allow_network,
            allow_rw_mounts=allow_rw_mounts,
        )
        plan = render_podman_sandbox_plan(job)
        self.ledger.record(
            job_id=job.job_id,
            status="submitted",
            event_type="submit",
            correlation_id=job.job_id,
            payload={"job": job.to_dict(), "guard": guard, "plan": plan.to_dict()},
            preview="Sandbox job submitted.",
        )
        if not guard["allowed"] or not plan.decision.get("allowed"):
            reason = ",".join(tuple(guard.get("reasons") or (plan.decision.get("reason") or "blocked",)))
            status = SandboxWorkerStatus.create(
                job_id=job.job_id,
                status="blocked",
                stderr_preview=reason,
            )
            self.ledger.record(
                job_id=job.job_id,
                status="blocked",
                event_type="blocked",
                correlation_id=job.job_id,
                payload={"guard": guard, "decision": plan.decision},
                preview=reason,
            )
            return SandboxWorkerSubmitResult(job.job_id, status, plan, False)
        if not live_enabled or not operator_go:
            status = SandboxWorkerStatus.create(
                job_id=job.job_id,
                status="dry_run",
                stdout_preview="Podman plan rendered; live execution not enabled.",
                artifact_count=0,
            )
            self.ledger.record(
                job_id=job.job_id,
                status="dry_run",
                event_type="plan_rendered",
                correlation_id=job.job_id,
                payload={"plan": plan.to_dict()},
                preview="Podman plan rendered; live execution not enabled.",
            )
            return SandboxWorkerSubmitResult(job.job_id, status, plan, False)
        return self._execute_live(job, plan)

    def status(self, job_id: str) -> SandboxWorkerStatus:
        latest = self.ledger.latest(job_id)
        if not latest:
            return SandboxWorkerStatus.create(job_id=job_id, status="missing")
        return SandboxWorkerStatus.create(
            job_id=job_id,
            status=latest.get("status") or "unknown",
            exit_code=latest.get("exit_code"),
            stdout_preview=latest.get("preview") or "",
            artifact_count=len(latest.get("artifact_refs") or ()),
        )

    def cancel(self, job_id: str) -> SandboxWorkerStatus:
        self.ledger.record(
            job_id=job_id,
            status="cancel_requested",
            event_type="cancel",
            correlation_id=job_id,
            payload={"job_id": job_id},
            preview="Cancel requested; running process cancellation is handled by the live runner.",
        )
        return SandboxWorkerStatus.create(job_id=job_id, status="cancel_requested")

    def artifacts(self, job_id: str) -> tuple[str, ...]:
        return self.ledger.artifacts(job_id)

    def _execute_live(self, job: SandboxJobRequest, plan: PodmanSandboxPlan) -> SandboxWorkerSubmitResult:
        self.ledger.record(
            job_id=job.job_id,
            status="running",
            event_type="live_start",
            correlation_id=job.job_id,
            payload={"plan": plan.to_dict()},
            preview="Live sandbox execution started.",
        )
        cleanup_result: SandboxCommandResult | None = None
        try:
            pod = self.command_runner(tuple(plan.pod_create_argv), job.limits.timeout_seconds)
            if not pod.ok:
                return self._live_failed(job, plan, pod, "pod_create_failed")
            run = self.command_runner(tuple(plan.run_argv), job.limits.timeout_seconds)
            artifact_ref = f"data/reports/autonomous_coding_agent/{job.job_id}.log"
            evidence = build_sandbox_result_evidence(
                job_id=job.job_id,
                exit_code=run.exit_code,
                stdout_artifact=artifact_ref,
                summary="Sandbox command completed." if run.ok else "Sandbox command failed.",
            )
            status_text = "succeeded" if run.ok else "failed"
            self.ledger.record(
                job_id=job.job_id,
                status=status_text,
                event_type="live_done",
                correlation_id=job.job_id,
                payload={"exit_code": run.exit_code, "timed_out": run.timed_out, "evidence": evidence},
                artifact_refs=(artifact_ref,),
                preview="Sandbox command completed." if run.ok else "Sandbox command failed.",
                exit_code=run.exit_code,
            )
            status = SandboxWorkerStatus.create(
                job_id=job.job_id,
                status=status_text,
                exit_code=run.exit_code,
                stdout_preview="Sandbox command completed." if run.ok else "Sandbox command failed.",
                artifact_count=1,
            )
            return SandboxWorkerSubmitResult(job.job_id, status, plan, True, evidence)
        finally:
            if plan.cleanup_argv:
                cleanup_result = self.command_runner(tuple(plan.cleanup_argv), 60)
                self.ledger.record(
                    job_id=job.job_id,
                    status="cleanup_done" if cleanup_result.ok else "cleanup_failed",
                    event_type="cleanup",
                    correlation_id=job.job_id,
                    payload={"exit_code": cleanup_result.exit_code, "timed_out": cleanup_result.timed_out},
                    preview="Sandbox cleanup completed." if cleanup_result.ok else "Sandbox cleanup failed.",
                    exit_code=cleanup_result.exit_code,
                )

    def _live_failed(
        self,
        job: SandboxJobRequest,
        plan: PodmanSandboxPlan,
        result: SandboxCommandResult,
        reason: str,
    ) -> SandboxWorkerSubmitResult:
        self.ledger.record(
            job_id=job.job_id,
            status="failed",
            event_type=reason,
            correlation_id=job.job_id,
            payload={"exit_code": result.exit_code, "timed_out": result.timed_out},
            preview=reason,
            exit_code=result.exit_code,
        )
        status = SandboxWorkerStatus.create(job_id=job.job_id, status="failed", exit_code=result.exit_code, stderr_preview=reason)
        return SandboxWorkerSubmitResult(job.job_id, status, plan, True)


def run_podman_command(argv: tuple[str, ...], timeout_seconds: int) -> SandboxCommandResult:
    if not argv or argv[0] != "podman":
        raise SandboxWorkerError("live sandbox runner only accepts podman argv")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
        return SandboxCommandResult(
            exit_code=max(0, min(int(completed.returncode), 255)),
            stdout=completed.stdout[:2000],
            stderr=completed.stderr[:2000],
            duration_seconds=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxCommandResult(
            exit_code=124,
            stdout=str(exc.stdout or "")[:2000],
            stderr=str(exc.stderr or "")[:2000],
            timed_out=True,
            duration_seconds=time.monotonic() - started,
        )

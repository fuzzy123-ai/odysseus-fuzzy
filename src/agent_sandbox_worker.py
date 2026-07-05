"""Sandbox worker core for dry-run and gated Podman execution."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
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
        self.command_runner = command_runner or build_sandbox_command_runner_from_env()

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
        latest = self._latest_status_event(job_id)
        if not latest:
            return SandboxWorkerStatus.create(job_id=job_id, status="missing")
        return SandboxWorkerStatus.create(
            job_id=job_id,
            status=latest.get("status") or "unknown",
            exit_code=latest.get("exit_code"),
            stdout_preview=latest.get("preview") or "",
            artifact_count=len(latest.get("artifact_refs") or ()),
        )

    def _latest_status_event(self, job_id: str) -> dict[str, Any] | None:
        events = self.ledger.events(job_id=job_id, limit=1000)
        for event in reversed(events):
            if event.get("event_type") != "cleanup":
                return event
        return events[-1] if events else None

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
            self._ensure_rw_mount_sources(job)
            try:
                pod = self.command_runner(tuple(plan.pod_create_argv), job.limits.timeout_seconds)
            except Exception as exc:
                return self._live_exception(job, plan, exc, "pod_create_error")
            if not pod.ok:
                return self._live_failed(job, plan, pod, "pod_create_failed")
            try:
                run = self.command_runner(tuple(plan.run_argv), job.limits.timeout_seconds)
            except Exception as exc:
                return self._live_exception(job, plan, exc, "run_error")
            artifact_ref = f"data/reports/autonomous_coding_agent/{job.job_id}.log"
            self._write_command_artifact(artifact_ref, run)
            evidence = build_sandbox_result_evidence(
                job_id=job.job_id,
                exit_code=run.exit_code,
                stdout_artifact=artifact_ref,
                summary="Sandbox command completed." if run.ok else "Sandbox command failed.",
                repo_root=Path.cwd(),
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
                try:
                    cleanup_result = self.command_runner(tuple(plan.cleanup_argv), 60)
                except Exception as exc:
                    cleanup_result = SandboxCommandResult(exit_code=127, stderr=type(exc).__name__[:120])
                self.ledger.record(
                    job_id=job.job_id,
                    status="cleanup_done" if cleanup_result.ok else "cleanup_failed",
                    event_type="cleanup",
                    correlation_id=job.job_id,
                    payload={"exit_code": cleanup_result.exit_code, "timed_out": cleanup_result.timed_out},
                    preview="Sandbox cleanup completed." if cleanup_result.ok else "Sandbox cleanup failed.",
                    exit_code=cleanup_result.exit_code,
                )

    def _ensure_rw_mount_sources(self, job: SandboxJobRequest) -> None:
        root = Path.cwd().resolve()
        for mount in job.mounts:
            if mount.mode != "rw":
                continue
            target = Path.cwd() / mount.source
            resolved = target.resolve()
            if root != resolved and root not in resolved.parents:
                raise SandboxWorkerError("rw mount source escapes workspace")
            target.mkdir(parents=True, exist_ok=True)

    def _write_command_artifact(self, artifact_ref: str, result: SandboxCommandResult) -> None:
        root = Path.cwd().resolve()
        target = Path.cwd() / artifact_ref
        resolved = target.resolve()
        if root != resolved and root not in resolved.parents:
            raise SandboxWorkerError("artifact path escapes workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            (
                f"exit_code={result.exit_code}\n"
                f"timed_out={str(result.timed_out).lower()}\n"
                f"duration_seconds={result.duration_seconds:.3f}\n\n"
                "[stdout]\n"
                f"{result.stdout}\n\n"
                "[stderr]\n"
                f"{result.stderr}\n"
            ),
            encoding="utf-8",
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

    def _live_exception(
        self,
        job: SandboxJobRequest,
        plan: PodmanSandboxPlan,
        exc: Exception,
        reason: str,
    ) -> SandboxWorkerSubmitResult:
        error_class = type(exc).__name__[:120]
        self.ledger.record(
            job_id=job.job_id,
            status="failed",
            event_type=reason,
            correlation_id=job.job_id,
            payload={"error_class": error_class},
            preview=reason,
            exit_code=127,
        )
        status = SandboxWorkerStatus.create(
            job_id=job.job_id,
            status="failed",
            exit_code=127,
            stderr_preview=f"{reason}:{error_class}",
        )
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


_TRUE_VALUES = {"1", "true", "yes", "on", "y"}
_SSH_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,120}$")
_SAFE_REMOTE_COMMAND_RE = re.compile(r"^/[A-Za-z0-9._/-]{1,220}$")


class SandboxSshHostRunner:
    """Run approved Podman sandbox argv through the homeserver host runner.

    This intentionally does not expose SSH as an agent tool. The only payload
    crossing the boundary is the already validated Podman argv plus a timeout.
    """

    def __init__(
        self,
        *,
        target: str,
        remote_command: str,
        ssh_config: str = "",
        process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ):
        self.target = _safe_ssh_target(target)
        self.remote_command = _safe_remote_command(remote_command)
        self.ssh_config = _safe_optional_container_path(ssh_config)
        self.process_runner = process_runner or subprocess.run

    def __call__(self, argv: tuple[str, ...], timeout_seconds: int) -> SandboxCommandResult:
        if not argv or argv[0] != "podman":
            raise SandboxWorkerError("host sandbox runner only accepts podman argv")
        payload = {
            "schema": "odysseus.agent.sandbox_host_runner_command.v1",
            "argv": list(argv),
            "timeout_seconds": max(1, min(int(timeout_seconds), 7200)),
        }
        ssh_argv = self._ssh_argv()
        started = time.monotonic()
        try:
            completed = self.process_runner(
                ssh_argv,
                input=json.dumps(payload, separators=(",", ":")),
                text=True,
                capture_output=True,
                timeout=payload["timeout_seconds"] + 20,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxCommandResult(
                exit_code=124,
                stdout=str(exc.stdout or "")[:2000],
                stderr=str(exc.stderr or "host runner timed out")[:2000],
                timed_out=True,
                duration_seconds=time.monotonic() - started,
            )
        if completed.returncode != 0:
            return SandboxCommandResult(
                exit_code=max(1, min(int(completed.returncode), 255)),
                stdout="",
                stderr=_redact_runner_text(completed.stderr or "host runner failed"),
                duration_seconds=time.monotonic() - started,
            )
        try:
            data = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return SandboxCommandResult(
                exit_code=126,
                stdout="",
                stderr="host runner returned invalid json",
                duration_seconds=time.monotonic() - started,
            )
        return SandboxCommandResult(
            exit_code=max(0, min(int(data.get("exit_code", 126)), 255)),
            stdout=_redact_runner_text(data.get("stdout", "")),
            stderr=_redact_runner_text(data.get("stderr", "")),
            timed_out=bool(data.get("timed_out", False)),
            duration_seconds=float(data.get("duration_seconds") or (time.monotonic() - started)),
        )

    def _ssh_argv(self) -> list[str]:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if self.ssh_config:
            argv.extend(["-F", self.ssh_config])
        argv.extend([self.target, self.remote_command])
        return argv


class SandboxUnavailableRunner:
    def __init__(self, reason: str):
        self.reason = reason[:200]

    def __call__(self, argv: tuple[str, ...], timeout_seconds: int) -> SandboxCommandResult:
        raise SandboxWorkerError(self.reason)


def build_sandbox_command_runner_from_env(
    *,
    env: Mapping[str, str] | None = None,
    tool_lookup: Callable[[str], str | None] | None = None,
) -> SandboxCommandRunner:
    source_env = env if env is not None else os.environ
    backend = str(source_env.get("ODYSSEUS_SANDBOX_RUNNER_BACKEND") or "local_podman").strip().lower()
    if backend == "host_ssh":
        try:
            return SandboxSshHostRunner(
                target=str(source_env.get("ODYSSEUS_SANDBOX_HOST_RUNNER_SSH_TARGET") or ""),
                ssh_config=str(source_env.get("ODYSSEUS_SANDBOX_HOST_RUNNER_SSH_CONFIG") or ""),
                remote_command=str(
                    source_env.get("ODYSSEUS_SANDBOX_HOST_RUNNER_REMOTE_COMMAND")
                    or "/opt/odysseus/ops/homeserver/run-sandbox-job.py"
                ),
            )
        except SandboxWorkerError as exc:
            return SandboxUnavailableRunner(str(exc))
    lookup = tool_lookup or shutil.which
    if backend not in {"", "local_podman"}:
        return SandboxUnavailableRunner("unsupported sandbox runner backend")
    if not lookup("podman"):
        return run_podman_command
    return run_podman_command


def sandbox_runner_readiness(
    *,
    env: Mapping[str, str] | None = None,
    tool_lookup: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    source_env = env if env is not None else os.environ
    lookup = tool_lookup or shutil.which
    backend = str(source_env.get("ODYSSEUS_SANDBOX_RUNNER_BACKEND") or "local_podman").strip().lower()
    if backend == "host_ssh":
        return {
            "backend": "host_ssh",
            "runner_available": bool(lookup("ssh"))
            and bool(str(source_env.get("ODYSSEUS_SANDBOX_HOST_RUNNER_SSH_TARGET") or "").strip())
            and bool(str(source_env.get("ODYSSEUS_SANDBOX_HOST_RUNNER_REMOTE_COMMAND") or "/opt/odysseus/ops/homeserver/run-sandbox-job.py").strip()),
            "required_gates": (
                "ssh_available",
                "sandbox_host_runner_target_configured",
                "sandbox_host_runner_remote_command_configured",
            ),
            "values_visible": False,
        }
    return {
        "backend": "local_podman",
        "runner_available": bool(lookup("podman")),
        "required_gates": ("podman_available",),
        "values_visible": False,
    }


def _safe_ssh_target(value: str) -> str:
    text = str(value or "").strip()
    if not text or text.startswith("-") or not _SSH_TARGET_RE.fullmatch(text):
        raise SandboxWorkerError("sandbox host runner ssh target is unsafe or missing")
    return text


def _safe_remote_command(value: str) -> str:
    text = str(value or "").strip()
    if not text or not _SAFE_REMOTE_COMMAND_RE.fullmatch(text) or ".." in text.split("/"):
        raise SandboxWorkerError("sandbox host runner remote command is unsafe or missing")
    return text


def _safe_optional_container_path(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith("/app/") or ".." in text.split("/") or not _SAFE_REMOTE_COMMAND_RE.fullmatch(text):
        raise SandboxWorkerError("sandbox host runner ssh config path is unsafe")
    return text


def _redact_runner_text(value: Any) -> str:
    text = str(value or "")
    lowered = text.lower()
    if any(marker in lowered for marker in ("token", "secret", "password", "api_key", "bearer ")):
        return "[redacted]"
    return text[:2000]

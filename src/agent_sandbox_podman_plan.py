"""Render reviewable Podman command plans for sandbox jobs.

The renderer never executes Podman. It returns argv arrays so the live runner
can display and audit the exact commands before operator-approved execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.agent_sandbox_contract import SandboxJobRequest, evaluate_sandbox_job


@dataclass(frozen=True, slots=True)
class PodmanSandboxPlan:
    job_id: str
    pod_create_argv: tuple[str, ...]
    run_argv: tuple[str, ...]
    cleanup_argv: tuple[str, ...]
    decision: dict[str, Any]
    executes_live: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.agent.podman_sandbox_plan.v1",
            "job_id": self.job_id,
            "pod_create_argv": self.pod_create_argv,
            "run_argv": self.run_argv,
            "cleanup_argv": self.cleanup_argv,
            "decision": self.decision,
            "executes_live": False,
        }


def render_podman_sandbox_plan(job: SandboxJobRequest, *, pod_name_prefix: str = "odysseus-agent") -> PodmanSandboxPlan:
    decision = evaluate_sandbox_job(job)
    pod_name = f"{pod_name_prefix}-{job.job_id}"
    network_arg = "none" if job.network_mode == "none" else f"slirp4netns:allow_host_loopback=false"
    pod_create = (
        "podman",
        "pod",
        "create",
        "--name",
        pod_name,
        "--network",
        network_arg,
    )
    run_args: list[str] = [
        "podman",
        "run",
        "--rm",
        "--pod",
        pod_name,
        "--memory",
        f"{job.limits.memory_mb}m",
        "--cpus",
        str(job.limits.cpu_count),
        "--pids-limit",
        "256",
        "--security-opt",
        "no-new-privileges",
    ]
    for mount in job.mounts:
        run_args.extend(["--mount", f"type=bind,src={mount.source},dst={mount.target},{mount.mode}"])
    run_args.extend([job.image, *job.argv])
    cleanup = ("podman", "pod", "rm", "-f", pod_name)
    if not decision.allowed:
        run_args = []
    return PodmanSandboxPlan(
        job_id=job.job_id,
        pod_create_argv=pod_create if decision.allowed else (),
        run_argv=tuple(run_args),
        cleanup_argv=cleanup if decision.allowed else (),
        decision=decision.to_dict(),
    )

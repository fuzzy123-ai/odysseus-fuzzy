"""Pre-dispatch resource guard for sandbox jobs."""

from __future__ import annotations

from typing import Any

from src.agent_sandbox_contract import SandboxJobRequest, evaluate_sandbox_job
from src.sandbox_network_policy import network_policy_from_job


def evaluate_sandbox_resource_guard(
    job: SandboxJobRequest,
    *,
    allow_network: bool = False,
    allow_rw_mounts: bool = False,
    max_memory_mb: int = 2048,
    max_timeout_seconds: int = 600,
) -> dict[str, Any]:
    decision = evaluate_sandbox_job(job)
    reasons: list[str] = []
    if not decision.allowed:
        reasons.append(decision.reason)
    network_policy = network_policy_from_job(job)
    if not network_policy.allowed:
        reasons.extend(network_policy.reasons)
    if job.network_mode != "none" and not allow_network:
        reasons.append("network_not_allowed")
    if any(mount.mode == "rw" for mount in job.mounts) and not allow_rw_mounts:
        reasons.append("rw_mount_not_allowed")
    if job.limits.memory_mb > max_memory_mb:
        reasons.append("memory_limit_exceeded")
    if job.limits.timeout_seconds > max_timeout_seconds:
        reasons.append("timeout_limit_exceeded")
    return {
        "schema": "odysseus.sandbox_resource_guard.v1",
        "allowed": not reasons,
        "reasons": tuple(dict.fromkeys(reasons)),
        "warnings": decision.warnings,
        "network_mode": job.network_mode,
        "network_allowlist": job.network_allowlist,
        "network_policy": network_policy.to_dict(),
        "memory_mb": job.limits.memory_mb,
        "timeout_seconds": job.limits.timeout_seconds,
        "raw_content_visible": False,
    }

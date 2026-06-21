"""Offline multiagent end-to-end benchmarks for the orchestration core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.claim_lease_store import ClaimLeaseStore
from src.context_capsule import CapsuleMemoryItem, CapsuleMemoryKind, ContextCapsule
from src.orchestration_dashboard import build_orchestration_dashboard_snapshot
from src.plan_runtime import PlanRuntimeState
from src.subagent_plan_binding import build_subagent_spec_from_plan_runtime
from src.subagent_runtime import run_subagent_fake_e2e_smoke
from src.workspace_policy import (
    WorkerWorkspaceAssignment,
    WorkspaceIsolationMode,
    evaluate_workspace_integration_gate,
)


class MultiagentE2EBenchmarkError(ValueError):
    """Raised when an offline benchmark cannot prove the core path."""


@dataclass(frozen=True, slots=True)
class MultiagentE2EBenchmarkResult:
    benchmark_id: str
    plan_id: str
    node_id: str
    decision: str
    checks: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    blocked_actions: tuple[str, ...]
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "decision": self.decision,
            "checks": list(self.checks),
            "evidence_refs": list(self.evidence_refs),
            "blocked_actions": list(self.blocked_actions),
            "next_action": self.next_action,
        }

    def audit_summary(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "decision": self.decision,
            "check_count": len(self.checks),
            "checks": self.checks,
            "evidence_refs": self.evidence_refs,
            "blocked_action_count": len(self.blocked_actions),
            "next_action": self.next_action,
        }


def run_core_multiagent_e2e_benchmark(
    runtime: PlanRuntimeState,
    *,
    node_id: str = "",
    checked_at: str = "2026-06-21T10:00:00Z",
) -> MultiagentE2EBenchmarkResult:
    """Prove the current multiagent contracts without live providers or threads."""

    if not isinstance(runtime, PlanRuntimeState):
        raise MultiagentE2EBenchmarkError("runtime must be a PlanRuntimeState")
    target_id = node_id or runtime.next_claimable_node_id()
    if not target_id:
        raise MultiagentE2EBenchmarkError("runtime has no claimable node")
    claimable = {node.node_id: node for node in runtime.claimable_nodes()}
    if target_id not in claimable:
        raise MultiagentE2EBenchmarkError(f"node is not claimable: {target_id}")

    target = claimable[target_id]
    lease_store = ClaimLeaseStore.create(known_agent_ids=["charlie", "bob"])
    claim = lease_store.claim_node(
        runtime,
        node_id=target.node_id,
        owner_agent_id="charlie",
        claimed_at=checked_at,
        lease_expires_at=_lease_expires_at(checked_at),
        reason="offline multiagent e2e benchmark claim",
    )
    spec = build_subagent_spec_from_plan_runtime(
        runtime,
        node_id=target.node_id,
        agent_id="bob",
        created_at=checked_at,
    )
    memory_capsule = _memory_capsule(spec.to_context_capsule(), runtime=runtime, checked_at=checked_at)
    workspace_decision = evaluate_workspace_integration_gate(
        WorkerWorkspaceAssignment.create(
            agent_identity=memory_capsule.agent_identity,
            plan_id=runtime.plan_id,
            node_id=target.node_id,
            isolation_mode=WorkspaceIsolationMode.WORKTREE,
            integration_base_branch="dev",
            worker_branch=f"worker/{target.node_id}-bob",
            worker_workspace_root=f".worktrees/{target.node_id}-bob",
            owned_files=memory_capsule.allowed_files,
            blocked_files=memory_capsule.blocked_files,
            created_at=checked_at,
        ),
        target_branch="dev",
        changed_files=memory_capsule.allowed_files,
        dirty_files=(),
        tests_passed=True,
        gates_verified=True,
    )
    fake_smoke = run_subagent_fake_e2e_smoke()
    dashboard = build_orchestration_dashboard_snapshot(runtime, last_updated_at=checked_at)

    smoke_counts = fake_smoke.audit_summary()["counts_by_state"]
    checks = (
        "planruntime_claimable",
        "claim_lease_created",
        "subagent_spec_bound_to_capsule",
        "accepted_memory_item_attached",
        "workspace_integration_allowed",
        "fake_subagent_verified_and_gate_blocked",
        "dashboard_snapshot_ready",
    )
    _require(workspace_decision.allowed, "workspace integration gate blocked benchmark")
    _require(smoke_counts.get("verified_done") == 1, "fake smoke did not include verified_done")
    _require(smoke_counts.get("gate_blocked") == 1, "fake smoke did not include gate_blocked")
    _require(dashboard.next_claimable_node_id == target.node_id, "dashboard lost benchmark target")

    return MultiagentE2EBenchmarkResult(
        benchmark_id=f"{runtime.plan_id}-{target.node_id}-core-e2e",
        plan_id=runtime.plan_id,
        node_id=target.node_id,
        decision="go",
        checks=checks,
        evidence_refs=(
            f"claim:{claim.claim_id}",
            f"lease_files:{len(claim.files)}",
            f"spec:{spec.agent_run_id}",
            f"capsule:{memory_capsule.capsule_id}",
            f"memory_items:{len(memory_capsule.memory_items)}",
            f"workspace:{workspace_decision.reason}",
            f"subagent_smoke:{fake_smoke.snapshot_id}",
            f"dashboard:{dashboard.dashboard_id}",
        ),
        blocked_actions=(
            "no_live_provider_without_operator_go",
            "no_raw_thread_or_chat_content",
            "no_direct_roadmap_mutation_from_worker_reports",
        ),
        next_action="mark benchmark evidence and continue to the next claimable roadmap node",
    )


def _memory_capsule(capsule: ContextCapsule, *, runtime: PlanRuntimeState, checked_at: str) -> ContextCapsule:
    memory_item = CapsuleMemoryItem.create(
        item_id=f"{capsule.capsule_id}-accepted-runtime-context",
        kind=CapsuleMemoryKind.DECISION,
        source_ref=runtime.roadmap_path,
        summary="Future agents use accepted roadmap evidence, claims, gates, and capsule summaries instead of raw chat history.",
        confidence=0.94,
        evidence_refs=[f"plan:{runtime.plan_id}", f"checked_at:{checked_at}"],
    )
    return ContextCapsule.create(
        capsule_id=capsule.capsule_id,
        objective=capsule.objective,
        agent_identity=capsule.agent_identity,
        allowed_files=capsule.allowed_files,
        blocked_files=capsule.blocked_files,
        inputs=capsule.inputs,
        expected_outputs=capsule.expected_outputs,
        tests=capsule.tests,
        handoff_format=capsule.handoff_format,
        stop_conditions=capsule.stop_conditions,
        evidence_required=capsule.evidence_required,
        memory_items=[memory_item],
    )


def _lease_expires_at(checked_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(checked_at.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise MultiagentE2EBenchmarkError("checked_at must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        parsed = parsed.astimezone(timezone.utc)
    return (parsed + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MultiagentE2EBenchmarkError(message)

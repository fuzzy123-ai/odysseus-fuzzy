"""Compact orchestration dashboard snapshots for roadmap control surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.plan_runtime import PlanRuntimeNode, PlanRuntimeState


class OrchestrationDashboardError(ValueError):
    """Raised when an orchestration dashboard snapshot cannot be built safely."""


@dataclass(frozen=True, slots=True)
class OrchestrationDashboardSnapshot:
    dashboard_id: str
    plan_id: str
    plan_status: str
    progress_percent: int
    active_node_id: str
    next_claimable_node_id: str
    counts: dict[str, int]
    agent_paths: tuple[dict[str, Any], ...]
    heartbeat_status: dict[str, Any]
    quality_gates: dict[str, Any]
    blocking_items: tuple[dict[str, Any], ...]
    next_actions: tuple[dict[str, Any], ...]
    evidence_refs: tuple[dict[str, Any], ...]
    controls: dict[str, Any]
    last_updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dashboard_id": self.dashboard_id,
            "plan_id": self.plan_id,
            "plan_status": self.plan_status,
            "progress_percent": self.progress_percent,
            "active_node_id": self.active_node_id,
            "next_claimable_node_id": self.next_claimable_node_id,
            "counts": self.counts,
            "agent_paths": list(self.agent_paths),
            "heartbeat_status": self.heartbeat_status,
            "quality_gates": self.quality_gates,
            "blocking_items": list(self.blocking_items),
            "next_actions": list(self.next_actions),
            "evidence_refs": list(self.evidence_refs),
            "controls": self.controls,
            "last_updated_at": self.last_updated_at,
        }


def build_orchestration_dashboard_snapshot(
    runtime: PlanRuntimeState,
    *,
    last_updated_at: str,
) -> OrchestrationDashboardSnapshot:
    if not isinstance(runtime, PlanRuntimeState):
        raise OrchestrationDashboardError("runtime must be a PlanRuntimeState")
    if not str(last_updated_at or "").strip():
        raise OrchestrationDashboardError("last_updated_at must not be empty")

    nodes = runtime.nodes
    active = runtime.node_map()[runtime.recommended_active_node]
    next_claimable = runtime.next_claimable_node_id()
    claimable_ids = {node.node_id for node in runtime.claimable_nodes()}
    counts = _counts(nodes, claimable_ids=claimable_ids)
    blocking_items = _blocking_items(nodes)
    plan_status = _plan_status(nodes=nodes, next_claimable=next_claimable, blocking_items=blocking_items)

    return OrchestrationDashboardSnapshot(
        dashboard_id=f"{runtime.plan_id}-dashboard",
        plan_id=runtime.plan_id,
        plan_status=plan_status,
        progress_percent=_progress_percent(nodes),
        active_node_id=active.node_id,
        next_claimable_node_id=next_claimable,
        counts=counts,
        agent_paths=_agent_paths(active=active, runtime=runtime),
        heartbeat_status=_heartbeat_status(runtime=runtime, active=active, next_claimable=next_claimable),
        quality_gates=_quality_gate_lens(nodes),
        blocking_items=blocking_items,
        next_actions=_next_actions(runtime=runtime, claimable_ids=claimable_ids),
        evidence_refs=_evidence_refs(nodes),
        controls=_controls(),
        last_updated_at=str(last_updated_at).strip(),
    )


def _counts(nodes: tuple[PlanRuntimeNode, ...], *, claimable_ids: set[str]) -> dict[str, int]:
    return {
        "total_nodes": len(nodes),
        "active_nodes": sum(1 for node in nodes if node.status in {"active", "planned"} and node.node_id in claimable_ids),
        "blocked_nodes": sum(1 for node in nodes if node.status == "blocked"),
        "completed_nodes": sum(1 for node in nodes if node.is_live_done),
        "claimable_nodes": len(claimable_ids),
        "research_nodes": sum(1 for node in nodes if node.status == "research"),
    }


def _progress_percent(nodes: tuple[PlanRuntimeNode, ...]) -> int:
    executable = [node for node in nodes if node.status != "research"]
    if not executable:
        return 100
    return round(100 * sum(1 for node in executable if node.is_live_done) / len(executable))


def _plan_status(
    *,
    nodes: tuple[PlanRuntimeNode, ...],
    next_claimable: str,
    blocking_items: tuple[dict[str, Any], ...],
) -> str:
    executable = [node for node in nodes if node.status != "research"]
    if blocking_items:
        return "blocked"
    if executable and all(node.is_live_done for node in executable):
        return "completed"
    if next_claimable:
        return "healthy"
    return "waiting"


def _agent_paths(*, active: PlanRuntimeNode, runtime: PlanRuntimeState) -> tuple[dict[str, Any], ...]:
    return (
        {
            "agent_id": "charlie",
            "role": "orchestrator",
            "status": "active",
            "active_node_id": active.node_id,
            "active_title": active.title,
            "allowed_files": list(active.source_refs or (runtime.roadmap_path,)),
        },
    )


def _heartbeat_status(
    *,
    runtime: PlanRuntimeState,
    active: PlanRuntimeNode,
    next_claimable: str,
) -> dict[str, Any]:
    return {
        "heartbeat_id": "abc-roadmap-continuation-runtime",
        "state": "ready" if next_claimable else "waiting",
        "active_node_id": active.node_id,
        "next_claimable_node_id": next_claimable,
        "automation": "active",
        "stop_reason": "" if next_claimable else "no_claimable_node",
        "roadmap_path": runtime.roadmap_path,
    }


def _quality_gate_lens(nodes: tuple[PlanRuntimeNode, ...]) -> dict[str, Any]:
    gate_count = sum(len(node.gates) for node in nodes)
    active_gate_count = sum(len(node.gates) for node in nodes if not node.is_live_done and node.status != "research")
    return {
        "gate_count": gate_count,
        "open_gate_count": active_gate_count,
        "blocking_gate_count": 0,
        "warning_gate_count": 0,
        "claimed_done_without_verified_done_count": sum(
            1 for node in nodes if node.status == "done" and not node.is_live_done
        ),
    }


def _blocking_items(nodes: tuple[PlanRuntimeNode, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "node_id": node.node_id,
            "reason": node.completion_status or "roadmap_blocked",
            "severity": "high",
        }
        for node in nodes
        if node.status == "blocked"
    )


def _next_actions(*, runtime: PlanRuntimeState, claimable_ids: set[str]) -> tuple[dict[str, Any], ...]:
    node_map = runtime.node_map()
    actions: list[dict[str, Any]] = []
    ordered_ids = runtime.next_action_node_ids or tuple(node.node_id for node in runtime.claimable_nodes())
    for rank, node_id in enumerate(ordered_ids, start=1):
        node = node_map[node_id]
        actions.append(
            {
                "rank": rank,
                "node_id": node.node_id,
                "title": node.title,
                "status": "claimable" if node.node_id in claimable_ids else "waiting",
                "target_version": node.target_version,
            }
        )
    return tuple(actions)


def _evidence_refs(nodes: tuple[PlanRuntimeNode, ...]) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    for node in nodes:
        if node.completion_commit:
            refs.append(
                {
                    "node_id": node.node_id,
                    "type": "commit",
                    "ref": node.completion_commit,
                    "status": node.completion_status,
                }
            )
    return tuple(refs[-12:])


def _controls() -> dict[str, Any]:
    return {
        "pause": {"state": "policy_gated", "reason": "dashboard v1 is read-only"},
        "resume": {"state": "policy_gated", "reason": "dashboard v1 is read-only"},
        "cancel": {"state": "policy_gated", "reason": "dashboard v1 is read-only"},
        "retry": {"state": "policy_gated", "reason": "dashboard v1 is read-only"},
    }

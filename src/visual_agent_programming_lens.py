"""Read-only visual-agent-programming roadmap projection.

The visual editor branch is intentionally observable before it is editable.
This module exposes the branch shape, version layers, controls, and blocked
actions without mutating the roadmap or starting agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.plan_runtime import PlanRuntimeNode, PlanRuntimeState


_BRANCH_PREFIX = "visual-agent-programming"


class VisualAgentProgrammingLensError(ValueError):
    """Raised when the visual-agent-programming snapshot cannot be built."""


@dataclass(frozen=True, slots=True)
class VisualAgentProgrammingSnapshot:
    snapshot_id: str
    plan_id: str
    mode: str
    active_node_id: str
    next_claimable_node_id: str
    progress: dict[str, int]
    version_layers: tuple[dict[str, Any], ...]
    nodes: tuple[dict[str, Any], ...]
    branch_edges: tuple[dict[str, str], ...]
    controls: dict[str, dict[str, str]]
    blocked_actions: tuple[dict[str, str], ...]
    next_steps: tuple[dict[str, str], ...]
    context_policy: dict[str, str]
    last_updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "plan_id": self.plan_id,
            "mode": self.mode,
            "active_node_id": self.active_node_id,
            "next_claimable_node_id": self.next_claimable_node_id,
            "progress": self.progress,
            "version_layers": list(self.version_layers),
            "nodes": list(self.nodes),
            "branch_edges": list(self.branch_edges),
            "controls": self.controls,
            "blocked_actions": list(self.blocked_actions),
            "next_steps": list(self.next_steps),
            "context_policy": self.context_policy,
            "last_updated_at": self.last_updated_at,
        }


def build_visual_agent_programming_snapshot(
    runtime: PlanRuntimeState,
    *,
    last_updated_at: str,
) -> VisualAgentProgrammingSnapshot:
    if not isinstance(runtime, PlanRuntimeState):
        raise VisualAgentProgrammingLensError("runtime must be a PlanRuntimeState")
    if not str(last_updated_at or "").strip():
        raise VisualAgentProgrammingLensError("last_updated_at must not be empty")

    node_map = runtime.node_map()
    branch_nodes = tuple(node for node in runtime.nodes if node.node_id.startswith(_BRANCH_PREFIX))
    if not branch_nodes:
        raise VisualAgentProgrammingLensError("visual-agent-programming branch is missing")

    claimable_ids = {node.node_id for node in runtime.claimable_nodes()}
    next_claimable = runtime.next_claimable_node_id()
    return VisualAgentProgrammingSnapshot(
        snapshot_id=f"{runtime.plan_id}-visual-agent-programming",
        plan_id=runtime.plan_id,
        mode="read_only",
        active_node_id=runtime.recommended_active_node,
        next_claimable_node_id=next_claimable,
        progress=_progress(runtime.nodes, branch_nodes),
        version_layers=_version_layers(branch_nodes),
        nodes=_nodes(branch_nodes, claimable_ids=claimable_ids),
        branch_edges=_branch_edges(branch_nodes, node_map=node_map),
        controls=_controls(),
        blocked_actions=_blocked_actions(),
        next_steps=_next_steps(runtime=runtime, claimable_ids=claimable_ids),
        context_policy=_context_policy(runtime),
        last_updated_at=str(last_updated_at).strip(),
    )


def _progress(all_nodes: tuple[PlanRuntimeNode, ...], branch_nodes: tuple[PlanRuntimeNode, ...]) -> dict[str, int]:
    executable = [node for node in all_nodes if node.status != "research"]
    branch_executable = [node for node in branch_nodes if node.status != "research"]
    return {
        "total_nodes": len(all_nodes),
        "live_done_nodes": sum(1 for node in all_nodes if node.is_live_done),
        "branch_nodes": len(branch_nodes),
        "branch_live_done_nodes": sum(1 for node in branch_nodes if node.is_live_done),
        "branch_executable_nodes": len(branch_executable),
        "overall_percent": round(100 * sum(1 for node in executable if node.is_live_done) / len(executable))
        if executable
        else 100,
    }


def _version_layers(branch_nodes: tuple[PlanRuntimeNode, ...]) -> tuple[dict[str, Any], ...]:
    layers: dict[tuple[str, str], list[str]] = {}
    for node in branch_nodes:
        layers.setdefault((node.horizon, node.target_version), []).append(node.node_id)
    return tuple(
        {
            "horizon": horizon,
            "target_version": target_version,
            "node_ids": tuple(sorted(node_ids)),
        }
        for (horizon, target_version), node_ids in sorted(layers.items())
    )


def _nodes(branch_nodes: tuple[PlanRuntimeNode, ...], *, claimable_ids: set[str]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "node_id": node.node_id,
            "title": node.title,
            "kind": node.kind,
            "status": node.status,
            "horizon": node.horizon,
            "target_version": node.target_version,
            "claimable": node.node_id in claimable_ids,
            "live_done": node.is_live_done,
            "depends_on": node.depends_on,
            "unlocks": node.unlocks,
            "gates": node.gates,
            "deliverables": node.deliverables,
            "completion_status": node.completion_status,
            "completion_commit": node.completion_commit,
        }
        for node in branch_nodes
    )


def _branch_edges(
    branch_nodes: tuple[PlanRuntimeNode, ...],
    *,
    node_map: dict[str, PlanRuntimeNode],
) -> tuple[dict[str, str], ...]:
    branch_ids = {node.node_id for node in branch_nodes}
    edges: list[dict[str, str]] = []
    for node in branch_nodes:
        for dep in node.depends_on:
            if dep in branch_ids or dep in node_map:
                edges.append({"from": dep, "to": node.node_id, "kind": "depends_on"})
        for unlocked in node.unlocks:
            if unlocked in branch_ids:
                edges.append({"from": node.node_id, "to": unlocked, "kind": "unlocks"})
    return tuple(edges)


def _controls() -> dict[str, dict[str, str]]:
    reason = "visual-agent-programming read-only slice; mutations require the dry-run validator and operator go"
    return {
        "create_slice": {"state": "policy_gated", "reason": reason},
        "connect_dependency": {"state": "policy_gated", "reason": reason},
        "assign_agent": {"state": "policy_gated", "reason": reason},
        "start_run": {"state": "policy_gated", "reason": "agent start requires explicit operator action"},
    }


def _blocked_actions() -> tuple[dict[str, str], ...]:
    return (
        {
            "action": "mutate_graph",
            "state": "blocked",
            "reason": "no_graph_mutation gate is active for this slice",
        },
        {
            "action": "start_agent",
            "state": "blocked",
            "reason": "no_agent_start gate is active for this slice",
        },
        {
            "action": "accept_report_directly",
            "state": "blocked",
            "reason": "agent reports must be reduced into accepted plan events before future context uses them",
        },
    )


def _next_steps(*, runtime: PlanRuntimeState, claimable_ids: set[str]) -> tuple[dict[str, str], ...]:
    node_map = runtime.node_map()
    steps: list[dict[str, str]] = []
    for node_id in runtime.next_action_node_ids:
        node = node_map[node_id]
        if node.status == "research":
            state = "research"
        elif node.node_id in claimable_ids:
            state = "claimable"
        else:
            state = "waiting"
        steps.append(
            {
                "node_id": node.node_id,
                "title": node.title,
                "state": state,
                "target_version": node.target_version,
            }
        )
    return tuple(steps)


def _context_policy(runtime: PlanRuntimeState) -> dict[str, str]:
    return {
        "source_of_truth": runtime.roadmap_path,
        "agent_write_mode": "reports_only_until_reducer_accepts_events",
        "live_boundary": "one_feature_must_be_live_installed_before_next_feature",
        "mutation_mode": "read_only",
    }

"""Bind PlanRuntime nodes to offline SubagentRunSpec objects."""

from __future__ import annotations

from typing import Any

from src.plan_runtime import PlanRuntimeNode, PlanRuntimeState
from src.subagent_runtime import SubagentRunSpec


class SubagentPlanBindingError(ValueError):
    """Raised when a PlanRuntime node cannot be bound to a subagent spec."""


def build_subagent_spec_from_plan_runtime(
    runtime: PlanRuntimeState,
    *,
    node_id: str = "",
    agent_id: str = "bob",
    role_id: str = "",
    model: str = "fake-model",
    thinking: str = "medium",
    created_at: str,
) -> SubagentRunSpec:
    """Create an offline SubagentRunSpec for a claimable roadmap node.

    This does not spawn, read, or send any live thread. It only translates the
    canonical roadmap node into the already fake/offline SubagentRuntime
    contract.
    """

    if not isinstance(runtime, PlanRuntimeState):
        raise SubagentPlanBindingError("runtime must be a PlanRuntimeState")
    claimable = {node.node_id: node for node in runtime.claimable_nodes()}
    selected_id = node_id or runtime.next_claimable_node_id()
    if not selected_id:
        raise SubagentPlanBindingError("no claimable PlanRuntime node is available")
    if selected_id not in claimable:
        raise SubagentPlanBindingError(f"node is not claimable: {selected_id}")
    node = claimable[selected_id]
    resolved_role = role_id or _role_for_node(node)
    return SubagentRunSpec.create(
        agent_run_id=f"{node.node_id}-{agent_id}-run",
        plan_id=runtime.plan_id,
        node_id=node.node_id,
        slice_id=node.node_id,
        agent_id=agent_id,
        role_id=resolved_role,
        objective=_objective(node),
        allowed_files=node.source_refs or (runtime.roadmap_path,),
        blocked_files=[],
        inputs=_inputs(runtime, node),
        expected_outputs=node.deliverables or (f"{node.title} handoff",),
        tests=_tests_for_node(node),
        handoff_format=[
            f"Agent: {agent_id}",
            f"Slice: {node.node_id}",
            "Status: done | blocked | handoff | failed",
            "Tests: <commands and results>",
            "Evidence: <focused evidence only>",
        ],
        stop_conditions=[
            "Do not spawn or message live threads from this binding.",
            "Stop if the node is no longer claimable.",
            "Stop on secrets, provider output, raw chat logs, or scope expansion.",
            "Stop if live execution is required before the execution-backend-adapter node is done.",
        ],
        evidence_required=node.gates,
        model=model,
        thinking=thinking,
        created_at=created_at,
        target_kind="job",
    )


def _role_for_node(node: PlanRuntimeNode) -> str:
    if node.kind in {"foundation", "lens"}:
        return "planning"
    if node.kind in {"runtime", "coordination", "workspace"}:
        return "backend"
    if node.kind == "verification":
        return "verification"
    return node.kind


def _objective(node: PlanRuntimeNode) -> str:
    deliverables = "; ".join(node.deliverables[:3])
    suffix = f" Deliverables: {deliverables}." if deliverables else ""
    return f"{node.title}. Keep work scoped to the PlanRuntime node and return a structured handoff.{suffix}"


def _inputs(runtime: PlanRuntimeState, node: PlanRuntimeNode) -> dict[str, Any]:
    return {
        "roadmap_plan_id": runtime.plan_id,
        "roadmap_path": runtime.roadmap_path,
        "node_id": node.node_id,
        "horizon": node.horizon,
        "target_version": node.target_version,
        "depends_on": list(node.depends_on),
    }


def _tests_for_node(node: PlanRuntimeNode) -> tuple[str, ...]:
    if node.kind in {"runtime", "coordination", "verification", "workspace"}:
        return (f"focused tests for {node.node_id}",)
    return ()

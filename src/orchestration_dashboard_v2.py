"""Dashboard v2 snapshot builder for automated orchestration.

AUTO6 keeps the UI boundary clean: this module builds a compact
OrchestrationStatusSnapshot from existing runtime models, but it does not
serve HTTP, touch frontend files, or read live threads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.agent_run_store import AgentRunStatus
from src.handoff_mailbox import DispatchMailbox
from src.heartbeat_coordinator import HeartbeatCoordinatorState, HeartbeatStatus
from src.orchestration_registry import OrchestrationRegistry
from src.orchestration_status import (
    AgentPathSummary,
    DashboardItem,
    LensSummary,
    NextAction,
    OrchestrationHealth,
    OrchestrationStatusSnapshot,
)
from src.plan_graph_store import AgentPathStatus, PlanGraph, PlanNodeStatus
from src.quality_gates import QualityGateResult


class OrchestrationDashboardV2Error(ValueError):
    """Raised when dashboard inputs are incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class DashboardBuildInput:
    registry: OrchestrationRegistry
    plan_id: str
    heartbeat_state: HeartbeatCoordinatorState
    quality_gate_result: QualityGateResult
    mailbox: DispatchMailbox
    last_updated_at: str

    @classmethod
    def create(
        cls,
        *,
        registry: OrchestrationRegistry,
        plan_id: str,
        heartbeat_state: HeartbeatCoordinatorState,
        quality_gate_result: QualityGateResult,
        mailbox: DispatchMailbox,
        last_updated_at: str,
    ) -> "DashboardBuildInput":
        if not isinstance(registry, OrchestrationRegistry):
            raise OrchestrationDashboardV2Error("registry must be an OrchestrationRegistry")
        if not isinstance(heartbeat_state, HeartbeatCoordinatorState):
            raise OrchestrationDashboardV2Error("heartbeat_state must be a HeartbeatCoordinatorState")
        if not isinstance(quality_gate_result, QualityGateResult):
            raise OrchestrationDashboardV2Error("quality_gate_result must be a QualityGateResult")
        if not isinstance(mailbox, DispatchMailbox):
            raise OrchestrationDashboardV2Error("mailbox must be a DispatchMailbox")
        if plan_id not in registry.plans:
            raise OrchestrationDashboardV2Error(f"unknown plan: {plan_id}")
        return cls(
            registry=registry,
            plan_id=plan_id,
            heartbeat_state=heartbeat_state,
            quality_gate_result=quality_gate_result,
            mailbox=mailbox,
            last_updated_at=last_updated_at,
        )


def build_orchestration_dashboard_v2(payload: DashboardBuildInput) -> OrchestrationStatusSnapshot:
    if not isinstance(payload, DashboardBuildInput):
        raise OrchestrationDashboardV2Error("payload must be a DashboardBuildInput")

    plan = payload.registry.plans[payload.plan_id]
    runs = payload.registry.runs_for_plan(payload.plan_id)
    blocking_items = _blocking_items(payload)
    plan_health = _plan_health(plan, payload.heartbeat_state, payload.quality_gate_result, blocking_items)
    progress = _plan_progress(plan)
    heartbeat_health = _heartbeat_health(payload.heartbeat_state.status)
    next_actions = _next_actions(payload)

    evidence_refs = [
        f"plan:{plan.plan_id}",
        f"heartbeat:{payload.heartbeat_state.heartbeat_id}",
    ]
    if payload.quality_gate_result.verified_done:
        evidence_refs.append("quality-gates:verified")
    if payload.mailbox.messages:
        evidence_refs.append(f"mailbox:{len(payload.mailbox.messages)}")

    return OrchestrationStatusSnapshot.create(
        dashboard_id=f"{plan.plan_id}-dashboard-v2",
        plan_id=plan.plan_id,
        plan_status=plan_health,
        overall_progress_percent=progress,
        agent_paths=_agent_path_summaries(plan),
        agent_runs=[
            LensSummary.create(
                lens_id="agent-runs",
                status=_run_lens_health(runs),
                count=len(runs),
                summary=_run_lens_summary(runs),
            ),
            LensSummary.create(
                lens_id="mailbox",
                status="waiting" if payload.mailbox.messages else "healthy",
                count=len(payload.mailbox.messages),
                summary=_mailbox_summary(payload.mailbox),
            ),
        ],
        heartbeat_status=heartbeat_health,
        quality_gates=[
            LensSummary.create(
                lens_id="quality-gates",
                status="healthy" if payload.quality_gate_result.verified_done else "blocked",
                count=len(payload.quality_gate_result.gates),
                summary=_quality_gate_summary(payload.quality_gate_result),
            )
        ],
        blocking_items=blocking_items,
        next_actions=next_actions,
        last_updated_at=payload.last_updated_at,
        evidence_refs=evidence_refs,
        warnings=_warnings(payload),
    )


def _plan_health(
    plan: PlanGraph,
    heartbeat_state: HeartbeatCoordinatorState,
    gate_result: QualityGateResult,
    blocking_items: tuple[DashboardItem, ...],
) -> OrchestrationHealth:
    if any(node.status == PlanNodeStatus.FAILED for node in plan.nodes):
        return OrchestrationHealth.FAILED
    if blocking_items or not gate_result.verified_done or heartbeat_state.status == HeartbeatStatus.BLOCKED:
        return OrchestrationHealth.BLOCKED
    if all(node.status in {PlanNodeStatus.DONE, PlanNodeStatus.SKIPPED} for node in plan.nodes):
        return OrchestrationHealth.COMPLETED
    if heartbeat_state.status in {HeartbeatStatus.WAITING, HeartbeatStatus.DISPATCHING}:
        return OrchestrationHealth.WAITING
    if heartbeat_state.status == HeartbeatStatus.STALE:
        return OrchestrationHealth.STALE
    return OrchestrationHealth.HEALTHY


def _heartbeat_health(status: HeartbeatStatus) -> OrchestrationHealth:
    mapping = {
        HeartbeatStatus.WATCHING: OrchestrationHealth.HEALTHY,
        HeartbeatStatus.DISPATCHING: OrchestrationHealth.WAITING,
        HeartbeatStatus.WAITING: OrchestrationHealth.WAITING,
        HeartbeatStatus.BLOCKED: OrchestrationHealth.BLOCKED,
        HeartbeatStatus.STALE: OrchestrationHealth.STALE,
        HeartbeatStatus.COMPLETED: OrchestrationHealth.COMPLETED,
        HeartbeatStatus.FAILED: OrchestrationHealth.FAILED,
        HeartbeatStatus.PAUSED: OrchestrationHealth.WAITING,
    }
    return mapping[status]


def _plan_progress(plan: PlanGraph) -> int:
    done = sum(1 for node in plan.nodes if node.status in {PlanNodeStatus.DONE, PlanNodeStatus.SKIPPED})
    return round((done / len(plan.nodes)) * 100)


def _agent_path_summaries(plan: PlanGraph) -> tuple[AgentPathSummary, ...]:
    node_map = {node.node_id: node for node in plan.nodes}
    summaries: list[AgentPathSummary] = []
    for path in plan.agent_paths:
        done = sum(
            1
            for node_id in path.node_ids
            if node_map[node_id].status in {PlanNodeStatus.DONE, PlanNodeStatus.SKIPPED}
        )
        active = next(
            (
                node_map[node_id].slice_id
                for node_id in path.node_ids
                if node_map[node_id].status in {PlanNodeStatus.RUNNING, PlanNodeStatus.PENDING, PlanNodeStatus.HANDOFF}
            ),
            node_map[path.node_ids[-1]].slice_id,
        )
        summaries.append(
            AgentPathSummary.create(
                agent_id=path.agent_id,
                role_id=path.agent_id,
                status=_agent_path_health(path.status),
                progress_percent=round((done / len(path.node_ids)) * 100),
                active_slice_id=active,
            )
        )
    return tuple(summaries)


def _agent_path_health(status: AgentPathStatus) -> OrchestrationHealth:
    mapping = {
        AgentPathStatus.PENDING: OrchestrationHealth.WAITING,
        AgentPathStatus.RUNNING: OrchestrationHealth.HEALTHY,
        AgentPathStatus.DONE: OrchestrationHealth.COMPLETED,
        AgentPathStatus.BLOCKED: OrchestrationHealth.BLOCKED,
        AgentPathStatus.FAILED: OrchestrationHealth.FAILED,
        AgentPathStatus.HANDOFF: OrchestrationHealth.WAITING,
        AgentPathStatus.SKIPPED: OrchestrationHealth.COMPLETED,
    }
    return mapping[status]


def _run_lens_health(runs: tuple[Any, ...]) -> str:
    statuses = {run.status for run in runs}
    if any(status == AgentRunStatus.FAILED for status in statuses):
        return "failed"
    if any(status == AgentRunStatus.BLOCKED for status in statuses):
        return "blocked"
    if any(status in {AgentRunStatus.RUNNING, AgentRunStatus.HANDOFF} for status in statuses):
        return "waiting"
    return "healthy"


def _run_lens_summary(runs: tuple[Any, ...]) -> str:
    counts = {status.value: sum(1 for run in runs if run.status == status) for status in AgentRunStatus}
    visible = ", ".join(f"{key}:{value}" for key, value in counts.items() if value)
    return visible or "no agent runs yet"


def _mailbox_summary(mailbox: DispatchMailbox) -> str:
    queued = mailbox.audit_summary()["queued_count"]
    return f"{queued} queued dispatches" if queued else "mailbox empty"


def _quality_gate_summary(result: QualityGateResult) -> str:
    summary = result.audit_summary()["status_counts"]
    return ", ".join(f"{key}:{value}" for key, value in summary.items() if value)


def _blocking_items(payload: DashboardBuildInput) -> tuple[DashboardItem, ...]:
    items: list[DashboardItem] = []
    for gate_id in payload.quality_gate_result.blocking_gate_ids:
        items.append(
            DashboardItem.create(
                item_id=f"gate-{gate_id}",
                title="Quality gate blocked",
                status="blocked",
                summary=f"Required gate is blocking: {gate_id}",
            )
        )
    if payload.heartbeat_state.status == HeartbeatStatus.BLOCKED:
        items.append(
            DashboardItem.create(
                item_id="heartbeat-blocked",
                title="Heartbeat blocked",
                status="blocked",
                summary=payload.heartbeat_state.stop_reason or "Heartbeat requires inspection.",
            )
        )
    return tuple(items)


def _next_actions(payload: DashboardBuildInput) -> tuple[NextAction, ...]:
    if payload.quality_gate_result.blocking_gate_ids:
        return (
            NextAction.create(
                owner="charlie",
                action="resolve quality gates",
                summary="Inspect blocking gate IDs before dispatching another slice.",
            ),
        )
    if payload.mailbox.messages:
        return (
            NextAction.create(
                owner="charlie",
                action="dispatch queued message",
                summary="Review mailbox queue and send only through the approved thread bridge.",
            ),
        )
    return (
        NextAction.create(
            owner="charlie",
            action="watch next heartbeat",
            summary="No blocking gate or queued dispatch is visible.",
        ),
    )


def _warnings(payload: DashboardBuildInput) -> tuple[str, ...]:
    warnings: list[str] = []
    warnings.extend(payload.heartbeat_state.warnings)
    warnings.extend(
        f"gate-warning:{gate_id}" for gate_id in payload.quality_gate_result.warning_gate_ids
    )
    return tuple(warnings)

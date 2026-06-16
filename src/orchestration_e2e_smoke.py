"""Deterministic two-agent orchestration smoke for AUTO7.

This smoke proves the model path from approved plan to verified dashboard
without reading real threads, sending messages, running git, or invoking tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.agent_run_store import AgentRun
from src.handoff_mailbox import DispatchMailbox, ParsedHandoff, parse_handoff_text
from src.heartbeat_coordinator import HeartbeatCoordinatorState
from src.orchestration_dashboard_v2 import DashboardBuildInput, build_orchestration_dashboard_v2
from src.orchestration_registry import OrchestrationRegistry
from src.orchestration_runtime_loop import OrchestrationRuntimeLoop, RuntimeDispatchIntent
from src.orchestration_status import OrchestrationStatusSnapshot
from src.plan_graph_store import AgentPath, PlanEdge, PlanGraph, PlanNode
from src.quality_gates import QualityGateResult
from src.runtime_quality_gates import (
    GitStatusSnapshot,
    RuntimeQualityGateInput,
    TestExecutionSnapshot,
    evaluate_runtime_quality_gates,
)
from src.thread_lifecycle_bridge import ThreadDispatchRequest, ThreadLifecycleSnapshot, ThreadRef
from src.thread_registry import ThreadRegistry


class OrchestrationE2ESmokeError(ValueError):
    """Raised when the deterministic smoke cannot produce a verified path."""


@dataclass(frozen=True, slots=True)
class TwoAgentSmokeResult:
    registry: OrchestrationRegistry
    threads: ThreadRegistry
    mailbox: DispatchMailbox
    gates: QualityGateResult
    dashboard: OrchestrationStatusSnapshot

    def audit_summary(self) -> dict[str, Any]:
        return {
            "plan_count": self.registry.audit_summary()["plan_count"],
            "run_count": self.registry.audit_summary()["run_count"],
            "thread_count": self.threads.audit_summary()["thread_count"],
            "mailbox_messages": self.mailbox.audit_summary()["message_count"],
            "verified_done": self.gates.verified_done,
            "dashboard_status": self.dashboard.plan_status.value,
            "dashboard_progress": self.dashboard.overall_progress_percent,
        }


def run_two_agent_smoke() -> TwoAgentSmokeResult:
    registry = OrchestrationRegistry()
    plan = _plan()
    registry.add_plan(plan)
    registry.add_run(_alice_run())

    thread_registry = ThreadRegistry()
    alice_ref = _ref(thread_id="019-alice", agent_id="alice", agent_run_id="run-alice", node_id="auto7a")
    bob_ref = _ref(thread_id="019-bob", agent_id="bob", agent_run_id="run-bob", node_id="auto7b")
    thread_registry.register(alice_ref)
    thread_registry.register(bob_ref)

    handoff = parse_handoff_text(
        """
        Agent: Alice
        Slice: AUTO7A-e2e-two-agent-smoke-runbook
        Status: done
        Commit: abcdef1
        Geänderte Dateien: docs/plans/automated-agent-handoff-e2e-smoke-runbook.md
        Tests: pytest tests/test_orchestration_e2e_smoke.py
        Evidence: runbook prepared
        Nächster Slice: AUTO7B-e2e-two-agent-smoke-execution
        """
    )

    runtime_loop = OrchestrationRuntimeLoop()
    dispatch_request = ThreadDispatchRequest.create(
        thread_ref=bob_ref,
        expected_agent_id="bob",
        expected_agent_run_id="run-bob",
        expected_node_id="auto7b",
        prompt_summary="Execute AUTO7B deterministic smoke with fake thread refs",
        allowed_action="send",
    )
    tick_plan = runtime_loop.plan_tick(
        tick_id="auto7-tick",
        state=_heartbeat(),
        snapshots=[
            ThreadLifecycleSnapshot.create(
                thread_ref=bob_ref,
                thread_status="idle",
                last_seen_turn=7,
                handoff_status="none",
            )
        ],
        dispatch_intents=[
            RuntimeDispatchIntent.create(
                request=dispatch_request,
                source_handoff=handoff,
            )
        ],
    )
    if tick_plan.tick.decision.value != "dispatch":
        raise OrchestrationE2ESmokeError("expected smoke tick to queue a dispatch")

    registry.add_run(_bob_run())
    bob_handoff = ParsedHandoff.create(
        agent="bob",
        slice_id="AUTO7B-e2e-two-agent-smoke-execution",
        status="done",
        commit="bcdef12",
        changed_files=["src/orchestration_e2e_smoke.py", "tests/test_orchestration_e2e_smoke.py"],
        tests=["pytest tests/test_orchestration_e2e_smoke.py"],
        evidence=["two-agent smoke produced verified dashboard"],
    )
    gates = evaluate_runtime_quality_gates(
        RuntimeQualityGateInput.create(
            agent_run_id="run-bob",
            plan_node_id="auto7b",
            subject_ref="auto7b",
            verified_at="2026-06-16T21:55:00Z",
            verified_by="charlie",
            handoff=bob_handoff,
            git_status=GitStatusSnapshot.create(branch="dev", clean=True, commit="bcdef12"),
            test_results=[
                TestExecutionSnapshot.create(
                    command="pytest tests/test_orchestration_e2e_smoke.py",
                    exit_code=0,
                    summary="deterministic smoke green",
                )
            ],
            changed_files=["src/orchestration_e2e_smoke.py", "tests/test_orchestration_e2e_smoke.py"],
            allowed_files=["src/orchestration_e2e_smoke.py", "tests/test_orchestration_e2e_smoke.py"],
            hot_files=[],
        )
    )
    dashboard = build_orchestration_dashboard_v2(
        DashboardBuildInput.create(
            registry=registry,
            plan_id="auto7-plan",
            heartbeat_state=_heartbeat(status="completed", evidence=["AUTO7 deterministic smoke complete"]),
            quality_gate_result=gates,
            mailbox=runtime_loop.mailbox,
            last_updated_at="2026-06-16T21:56:00Z",
        )
    )
    return TwoAgentSmokeResult(
        registry=registry,
        threads=thread_registry,
        mailbox=runtime_loop.mailbox,
        gates=gates,
        dashboard=dashboard,
    )


def _plan() -> PlanGraph:
    return PlanGraph.create(
        plan_id="auto7-plan",
        title="AUTO7 deterministic two-agent smoke",
        nodes=[
            PlanNode.create(
                node_id="auto7a",
                slice_id="auto7a",
                title="Alice runbook",
                owner="alice",
                status="done",
                allowed_files=["docs/plans/automated-agent-handoff-e2e-smoke-runbook.md"],
                blocked_files=[],
                evidence_required=["runbook"],
            ),
            PlanNode.create(
                node_id="auto7b",
                slice_id="auto7b",
                title="Bob smoke execution",
                owner="bob",
                status="done",
                allowed_files=["src/orchestration_e2e_smoke.py", "tests/test_orchestration_e2e_smoke.py"],
                blocked_files=[],
                evidence_required=["green smoke"],
            ),
        ],
        edges=[PlanEdge.create(from_node="auto7a", to_node="auto7b", kind="handoff_to")],
        agent_paths=[
            AgentPath.create(agent_id="alice", node_ids=["auto7a"], status="done"),
            AgentPath.create(agent_id="bob", node_ids=["auto7b"], status="done"),
        ],
    )


def _alice_run() -> AgentRun:
    return AgentRun.create(
        agent_run_id="run-alice",
        plan_id="auto7-plan",
        node_id="auto7a",
        slice_id="auto7a",
        agent_id="alice",
        role_id="docs",
        model="gpt-5.4",
        thinking="medium",
        status="done",
        started_at="2026-06-16T21:50:00Z",
        completed_at="2026-06-16T21:52:00Z",
        changed_files=["docs/plans/automated-agent-handoff-e2e-smoke-runbook.md"],
        tests=["pytest tests/test_orchestration_e2e_smoke.py"],
        commit="abcdef1",
        warnings=[],
        errors=[],
        blocker="",
        next_action="dispatch bob",
        evidence=["runbook prepared"],
    )


def _bob_run() -> AgentRun:
    return AgentRun.create(
        agent_run_id="run-bob",
        plan_id="auto7-plan",
        node_id="auto7b",
        slice_id="auto7b",
        agent_id="bob",
        role_id="backend",
        model="gpt-5.4",
        thinking="medium",
        status="done",
        started_at="2026-06-16T21:53:00Z",
        completed_at="2026-06-16T21:55:00Z",
        changed_files=["src/orchestration_e2e_smoke.py", "tests/test_orchestration_e2e_smoke.py"],
        tests=["pytest tests/test_orchestration_e2e_smoke.py"],
        commit="bcdef12",
        warnings=[],
        errors=[],
        blocker="",
        next_action="verified done",
        evidence=["dashboard completed"],
    )


def _ref(*, thread_id: str, agent_id: str, agent_run_id: str, node_id: str) -> ThreadRef:
    return ThreadRef.create(
        thread_id=thread_id,
        agent_id=agent_id,
        agent_run_id=agent_run_id,
        plan_id="auto7-plan",
        node_id=node_id,
    )


def _heartbeat(status: str = "watching", evidence: tuple[str, ...] = ()) -> HeartbeatCoordinatorState:
    return HeartbeatCoordinatorState.create(
        heartbeat_id="auto7-heartbeat",
        plan_id="auto7-plan",
        coordinator_run_id="charlie-run",
        agent_run_ids=["run-alice", "run-bob"],
        thread_refs=["019-alice", "019-bob"],
        interval_seconds=60,
        status=status,
        last_tick_at="",
        next_tick_at="",
        stop_reason="AUTO7 smoke complete" if status == "completed" else "",
        evidence=evidence,
    )

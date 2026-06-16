from src.agent_run_store import AgentRun
from src.handoff_mailbox import DispatchMailbox, MailboxMessage, ParsedHandoff
from src.heartbeat_coordinator import HeartbeatCoordinatorState
from src.orchestration_dashboard_v2 import DashboardBuildInput, build_orchestration_dashboard_v2
from src.orchestration_registry import OrchestrationRegistry
from src.plan_graph_store import AgentPath, PlanEdge, PlanGraph, PlanNode
from src.quality_gates import QualityGate, QualityGateResult
from src.thread_lifecycle_bridge import ThreadRef


def _node(node_id: str, *, owner: str = "bob", status: str = "pending") -> PlanNode:
    return PlanNode.create(
        node_id=node_id,
        slice_id=node_id,
        title=f"{node_id} slice",
        owner=owner,
        status=status,
        allowed_files=[f"src/{node_id}.py"],
        blocked_files=[],
        evidence_required=["green tests"],
    )


def _registry(*, second_status: str = "running", run_status: str = "running") -> OrchestrationRegistry:
    plan = PlanGraph.create(
        plan_id="auto6-plan",
        title="AUTO6 Dashboard Plan",
        nodes=[_node("auto5", status="done"), _node("auto6", status=second_status)],
        edges=[PlanEdge.create(from_node="auto5", to_node="auto6", kind="handoff_to")],
        agent_paths=[AgentPath.create(agent_id="bob", node_ids=["auto5", "auto6"], status=run_status)],
    )
    run = AgentRun.create(
        agent_run_id="run-auto6",
        plan_id="auto6-plan",
        node_id="auto6",
        slice_id="auto6",
        agent_id="bob",
        role_id="backend",
        model="gpt-5.4",
        thinking="medium",
        status=run_status,
        started_at="2026-06-16T21:40:00Z",
        completed_at="",
        changed_files=["src/orchestration_dashboard_v2.py"],
        tests=["pytest tests/test_orchestration_dashboard_v2.py"],
        commit="",
        warnings=[],
        errors=[],
        blocker="waiting for gate" if run_status == "blocked" else "",
        next_action="",
        evidence=[],
    )
    registry = OrchestrationRegistry()
    registry.add_plan(plan)
    registry.add_run(run)
    return registry


def _heartbeat(status: str = "watching") -> HeartbeatCoordinatorState:
    return HeartbeatCoordinatorState.create(
        heartbeat_id="auto6-heartbeat",
        plan_id="auto6-plan",
        coordinator_run_id="charlie-run",
        agent_run_ids=["run-auto6"],
        thread_refs=["019-thread"],
        interval_seconds=60,
        status=status,
        last_tick_at="",
        next_tick_at="",
        stop_reason="blocked by gate" if status == "blocked" else "",
        evidence=["blocked evidence"] if status == "blocked" else [],
    )


def _gate(gate_id: str = "tests-pass", *, status: str = "pass", required: bool = True) -> QualityGate:
    return QualityGate.create(
        gate_id=gate_id,
        gate_type="tests",
        subject_ref="auto6",
        agent_run_id="run-auto6",
        plan_node_id="auto6",
        status=status,
        severity="high",
        required=required,
        evidence=["green test"] if status == "pass" else [],
        verified_at="2026-06-16T21:45:00Z" if status == "pass" else "",
        verified_by="Charlie" if status == "pass" else "",
        block_reason="gate blocked" if status == "block" else "",
        next_action="resolve gate" if status == "block" else "",
    )


def _message() -> MailboxMessage:
    return MailboxMessage.create(
        thread_ref=ThreadRef.create(
            thread_id="019-thread",
            agent_id="bob",
            agent_run_id="run-auto6",
            plan_id="auto6-plan",
            node_id="auto6",
        ),
        prompt_summary="Dispatch AUTO7 smoke",
        allowed_action="send",
        source_handoff=ParsedHandoff.create(
            agent="bob",
            slice_id="auto6",
            status="done",
            commit="abcdef1",
        ),
    )


def _input(**overrides) -> DashboardBuildInput:
    payload = {
        "registry": _registry(),
        "plan_id": "auto6-plan",
        "heartbeat_state": _heartbeat(),
        "quality_gate_result": QualityGateResult.create(gates=[_gate()]),
        "mailbox": DispatchMailbox(),
        "last_updated_at": "2026-06-16T21:45:00Z",
    }
    payload.update(overrides)
    return DashboardBuildInput.create(**payload)


def test_builds_healthy_dashboard_snapshot_from_runtime_models():
    snapshot = build_orchestration_dashboard_v2(_input())

    assert snapshot.plan_status.value == "healthy"
    assert snapshot.overall_progress_percent == 50
    assert snapshot.agent_paths[0].progress_percent == 50
    assert snapshot.agent_runs[0].lens_id == "agent-runs"
    assert snapshot.quality_gates[0].status.value == "healthy"
    assert snapshot.next_actions[0].action == "watch next heartbeat"


def test_blocking_quality_gate_creates_blocking_item_and_next_action():
    gate_result = QualityGateResult.create(gates=[_gate("scope-block", status="block")])

    snapshot = build_orchestration_dashboard_v2(_input(quality_gate_result=gate_result))

    assert snapshot.plan_status.value == "blocked"
    assert snapshot.blocking_items[0].item_id == "gate-scope-block"
    assert snapshot.next_actions[0].action == "resolve quality gates"


def test_queued_mailbox_dispatch_is_visible_without_frontend_hotfiles():
    mailbox = DispatchMailbox()
    mailbox.queue(_message())

    snapshot = build_orchestration_dashboard_v2(_input(mailbox=mailbox))

    mailbox_lens = next(lens for lens in snapshot.agent_runs if lens.lens_id == "mailbox")
    assert mailbox_lens.status.value == "waiting"
    assert mailbox_lens.count == 1
    assert snapshot.next_actions[0].action == "dispatch queued message"


def test_blocked_heartbeat_adds_blocking_item():
    snapshot = build_orchestration_dashboard_v2(_input(heartbeat_state=_heartbeat("blocked")))

    assert snapshot.plan_status.value == "blocked"
    assert any(item.item_id == "heartbeat-blocked" for item in snapshot.blocking_items)


def test_completed_plan_reaches_100_percent():
    registry = _registry(second_status="done", run_status="done")
    snapshot = build_orchestration_dashboard_v2(_input(registry=registry))

    assert snapshot.plan_status.value == "completed"
    assert snapshot.overall_progress_percent == 100

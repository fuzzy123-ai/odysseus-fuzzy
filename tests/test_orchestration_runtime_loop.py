import pytest

from src.claim_lease_store import ClaimLeaseStore
from src.handoff_mailbox import ParsedHandoff
from src.heartbeat_coordinator import HeartbeatCoordinatorState, HeartbeatDecision
from src.orchestration_runtime_loop import (
    OrchestrationRuntimeLoop,
    OrchestrationRuntimeLoopError,
    RuntimeDispatchIntent,
    RuntimeHeartbeatInput,
)
from src.plan_runtime import PlanRuntimeState
from src.quality_gates import QualityGate, QualityGateResult
from src.thread_lifecycle_bridge import ThreadDispatchRequest, ThreadLifecycleSnapshot, ThreadRef


def _ref(
    *,
    thread_id: str = "019-thread",
    agent_id: str = "bob",
    agent_run_id: str = "run-b",
    node_id: str = "auto4",
) -> ThreadRef:
    return ThreadRef.create(
        thread_id=thread_id,
        agent_id=agent_id,
        agent_run_id=agent_run_id,
        plan_id="auto4-plan",
        node_id=node_id,
    )


def _state(*, mode: str = "assist") -> HeartbeatCoordinatorState:
    return HeartbeatCoordinatorState.create(
        heartbeat_id="auto4-heartbeat",
        plan_id="auto4-plan",
        coordinator_run_id="charlie-run",
        agent_run_ids=["run-b"],
        thread_refs=["019-thread"],
        interval_seconds=60,
        mode=mode,
        status="watching",
        last_tick_at="",
        next_tick_at="",
        stop_reason="",
    )


def _snapshot(status: str = "idle", *, ref: ThreadRef | None = None) -> ThreadLifecycleSnapshot:
    return ThreadLifecycleSnapshot.create(
        thread_ref=ref or _ref(),
        thread_status=status,
        last_seen_turn=5,
        handoff_status="none",
        dispatch_intent="read_only",
    )


def _intent(*, ref: ThreadRef | None = None, allowed_action: str = "send") -> RuntimeDispatchIntent:
    thread_ref = ref or _ref()
    return RuntimeDispatchIntent.create(
        request=ThreadDispatchRequest.create(
            thread_ref=thread_ref,
            expected_agent_id=thread_ref.agent_id,
            expected_agent_run_id=thread_ref.agent_run_id,
            expected_node_id=thread_ref.node_id,
            prompt_summary="Continue with the next safe AUTO slice",
            allowed_action=allowed_action,
        ),
        source_handoff=ParsedHandoff.create(
            agent="bob",
            slice_id="AUTO3-handoff-parser-and-mailbox",
            status="done",
            commit="abcdef1",
        ),
    )


def _runtime_payload(*, active_status: str = "planned") -> dict:
    return {
        "schema_version": 1,
        "plan_id": "auto4-plan",
        "title": "AUTO4 Test Plan",
        "format_decision": {"source_of_truth": "json"},
        "recommended_active_node": "heartbeat-runtime-live-loop",
        "version_horizons": [
            {"id": "now", "label": "Now", "version": "pre-0.9", "visual_weight": "primary", "description": "test"},
            {"id": "v0-9", "label": "0.9", "version": "0.9", "visual_weight": "primary", "description": "test"},
        ],
        "graph_nodes": [
            {
                "id": "thread-bridge-live",
                "kind": "runtime",
                "priority_rank": 1,
                "title": "Thread bridge live",
                "horizon": "now",
                "target_version": "pre-0.9",
                "status": "done",
                "source_refs": ["src/thread_lifecycle_bridge.py"],
                "depends_on": [],
                "unlocks": ["heartbeat-runtime-live-loop"],
                "gates": ["tests"],
                "deliverables": ["bridge"],
                "completion_state": {"status": "live_installed", "commit": "abc1234"},
            },
            {
                "id": "heartbeat-runtime-live-loop",
                "kind": "runtime",
                "priority_rank": 2,
                "title": "Heartbeat runtime live loop",
                "horizon": "v0-9",
                "target_version": "0.9",
                "status": active_status,
                "source_refs": ["src/orchestration_runtime_loop.py", "tests/test_orchestration_runtime_loop.py"],
                "depends_on": ["thread-bridge-live"],
                "unlocks": [],
                "gates": ["tests"],
                "deliverables": ["runtime tick"],
            },
        ],
        "next_actions": [{"rank": 1, "node_id": "heartbeat-runtime-live-loop", "action": "continue"}],
    }


def _runtime(*, active_status: str = "planned") -> PlanRuntimeState:
    return PlanRuntimeState.from_dict(_runtime_payload(active_status=active_status))


def _runtime_input(
    *,
    runtime: PlanRuntimeState | None = None,
    claim_store: ClaimLeaseStore | None = None,
    gate_results=(),
    snapshots=None,
    intents=None,
) -> RuntimeHeartbeatInput:
    return RuntimeHeartbeatInput.create(
        runtime=runtime or _runtime(),
        claim_store=claim_store or ClaimLeaseStore.create(known_agent_ids=["charlie"]),
        coordinator_state=_state(),
        snapshots=[_snapshot("idle")] if snapshots is None else snapshots,
        dispatch_intents=[_intent()] if intents is None else intents,
        gate_results=gate_results,
        checked_at="2026-06-21T10:00:00Z",
    )


def _blocking_gate_result() -> QualityGateResult:
    return QualityGateResult.create(
        gates=[
            QualityGate.create(
                gate_id="tests-block",
                gate_type="tests",
                subject_ref="heartbeat-runtime-live-loop",
                agent_run_id="charlie-run",
                plan_node_id="heartbeat-runtime-live-loop",
                status="block",
                severity="high",
                required=True,
                evidence=[],
                verified_at="2026-06-21T10:00:00Z",
                verified_by="charlie",
                block_reason="focused tests have not passed",
                next_action="run focused tests",
            )
        ]
    )


def test_idle_thread_queues_mailbox_dispatch_and_tick_dispatch():
    loop = OrchestrationRuntimeLoop()

    plan = loop.plan_tick(
        tick_id="tick-1",
        state=_state(),
        snapshots=[_snapshot("idle")],
        dispatch_intents=[_intent()],
    )

    assert plan.tick.decision == HeartbeatDecision.DISPATCH
    assert plan.tick.audit_summary()["dispatch_count"] == 1
    assert plan.audit_summary()["queued_message_count"] == 1
    assert loop.mailbox.audit_summary()["queued_count"] == 1


def test_running_thread_waits_instead_of_queueing_dispatch():
    loop = OrchestrationRuntimeLoop()

    plan = loop.plan_tick(
        tick_id="tick-1",
        state=_state(),
        snapshots=[_snapshot("running")],
        dispatch_intents=[_intent()],
    )

    assert plan.tick.decision == HeartbeatDecision.WAIT
    assert plan.queued_messages == ()
    assert "thread_already_running" in plan.tick.warnings


def test_ambiguous_thread_stops_runtime_tick():
    loop = OrchestrationRuntimeLoop()

    plan = loop.plan_tick(
        tick_id="tick-1",
        state=_state(),
        snapshots=[_snapshot("ambiguous")],
        dispatch_intents=[_intent()],
    )

    assert plan.tick.decision == HeartbeatDecision.STOP
    assert plan.can_continue is False
    assert "ambiguous_thread" in plan.tick.errors
    assert plan.stop_reason == "blocked_runtime_tick"


def test_missing_snapshot_stops_runtime_tick():
    loop = OrchestrationRuntimeLoop()

    plan = loop.plan_tick(
        tick_id="tick-1",
        state=_state(),
        snapshots=[],
        dispatch_intents=[_intent()],
    )

    assert plan.tick.decision == HeartbeatDecision.STOP
    assert "missing_thread_snapshot" in plan.tick.errors


def test_manual_stop_pending_always_stops_without_dispatch():
    loop = OrchestrationRuntimeLoop()

    plan = loop.plan_tick(
        tick_id="tick-1",
        state=_state(mode="manual_stop_pending"),
        snapshots=[_snapshot("idle")],
        dispatch_intents=[_intent()],
    )

    assert plan.tick.decision == HeartbeatDecision.STOP
    assert plan.stop_reason == "manual_stop_pending"
    assert plan.queued_messages == ()


def test_no_ready_intents_returns_read_tick():
    loop = OrchestrationRuntimeLoop()

    plan = loop.plan_tick(
        tick_id="tick-1",
        state=_state(),
        snapshots=[],
        dispatch_intents=[],
    )

    assert plan.tick.decision == HeartbeatDecision.READ
    assert "no dispatch intents ready" in plan.tick.evidence


def test_duplicate_snapshot_is_rejected_before_tick_creation():
    ref = _ref()

    with pytest.raises(OrchestrationRuntimeLoopError, match="duplicate snapshot"):
        OrchestrationRuntimeLoop().plan_tick(
            tick_id="tick-1",
            state=_state(),
            snapshots=[_snapshot(ref=ref), _snapshot(ref=ref)],
            dispatch_intents=[_intent(ref=ref)],
        )


def test_runtime_heartbeat_input_dispatches_when_claimable_and_unblocked():
    loop = OrchestrationRuntimeLoop()

    plan = loop.plan_runtime_tick(
        tick_id="tick-runtime-1",
        runtime_input=_runtime_input(),
    )

    assert plan.tick.decision == HeartbeatDecision.DISPATCH
    assert "claimable_node:heartbeat-runtime-live-loop" in plan.tick.evidence
    assert plan.audit_summary()["queued_message_count"] == 1


def test_runtime_heartbeat_input_stops_on_blocking_quality_gate():
    loop = OrchestrationRuntimeLoop()

    plan = loop.plan_runtime_tick(
        tick_id="tick-runtime-1",
        runtime_input=_runtime_input(gate_results=[_blocking_gate_result()]),
    )

    assert plan.tick.decision == HeartbeatDecision.STOP
    assert plan.stop_reason == "blocked_runtime_heartbeat"
    assert "quality_gates_blocked:tests-block" in plan.tick.errors
    assert plan.queued_messages == ()


def test_runtime_heartbeat_input_stops_without_claimable_roadmap_node():
    loop = OrchestrationRuntimeLoop()

    plan = loop.plan_runtime_tick(
        tick_id="tick-runtime-1",
        runtime_input=_runtime_input(runtime=_runtime(active_status="done")),
    )

    assert plan.tick.decision == HeartbeatDecision.STOP
    assert plan.stop_reason == "blocked_runtime_heartbeat"
    assert "no_claimable_roadmap_node" in plan.tick.errors


def test_runtime_heartbeat_input_records_same_node_active_claim_as_evidence():
    runtime = _runtime()
    claim_store = ClaimLeaseStore.create(known_agent_ids=["charlie"])
    claim_store.claim_node(
        runtime,
        node_id="heartbeat-runtime-live-loop",
        owner_agent_id="charlie",
        claimed_at="2026-06-21T09:00:00Z",
        lease_expires_at="2026-06-21T11:00:00Z",
        reason="working current heartbeat slice",
    )

    plan = OrchestrationRuntimeLoop().plan_runtime_tick(
        tick_id="tick-runtime-1",
        runtime_input=_runtime_input(runtime=runtime, claim_store=claim_store),
    )

    assert plan.tick.decision == HeartbeatDecision.DISPATCH
    assert "active_claim:charlie:heartbeat-runtime-live-loop" in plan.tick.evidence

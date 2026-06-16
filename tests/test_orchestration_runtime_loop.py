import pytest

from src.handoff_mailbox import ParsedHandoff
from src.heartbeat_coordinator import HeartbeatCoordinatorState, HeartbeatDecision
from src.orchestration_runtime_loop import (
    OrchestrationRuntimeLoop,
    OrchestrationRuntimeLoopError,
    RuntimeDispatchIntent,
)
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

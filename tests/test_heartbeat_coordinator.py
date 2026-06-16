import pytest

from src.heartbeat_coordinator import (
    HeartbeatCoordinatorError,
    HeartbeatCoordinatorState,
    HeartbeatDecision,
    HeartbeatDispatch,
    HeartbeatStatus,
    HeartbeatTick,
)


def test_valid_coordinator_state_normalizes_stably():
    state = HeartbeatCoordinatorState.create(
        heartbeat_id=" OR4B Heartbeat ",
        plan_id=" OR4 Plan ",
        coordinator_run_id=" Coordinator Run 1 ",
        agent_run_ids=["run-b", "run-a", "run-a"],
        thread_refs=["thread-2", "thread-1", "thread-1"],
        interval_seconds="30",
        status="watching",
        last_tick_at="2026-06-16T10:00:00Z",
        next_tick_at="2026-06-16T10:00:30Z",
        stop_reason="",
    )

    assert state.heartbeat_id == "or4b-heartbeat"
    assert state.plan_id == "or4-plan"
    assert state.coordinator_run_id == "coordinator-run-1"
    assert state.agent_run_ids == ("run-a", "run-b")
    assert state.thread_refs == ("thread-1", "thread-2")
    assert state.interval_seconds == 30


def test_invalid_interval_is_rejected():
    with pytest.raises(HeartbeatCoordinatorError):
        HeartbeatCoordinatorState.create(
            heartbeat_id="hb",
            plan_id="plan",
            coordinator_run_id="coord",
            agent_run_ids=["run-1"],
            thread_refs=["thread-1"],
            interval_seconds=0,
            status="watching",
            last_tick_at="",
            next_tick_at="",
            stop_reason="",
        )


def test_dispatching_without_dispatch_is_rejected():
    with pytest.raises(HeartbeatCoordinatorError):
        HeartbeatTick.create(
            tick_id="tick-1",
            heartbeat_id="hb",
            decision="dispatch",
            dispatches=[],
            evidence=[],
            warnings=[],
            errors=[],
        )


def test_failed_without_error_is_rejected():
    with pytest.raises(HeartbeatCoordinatorError):
        HeartbeatCoordinatorState.create(
            heartbeat_id="hb",
            plan_id="plan",
            coordinator_run_id="coord",
            agent_run_ids=["run-1"],
            thread_refs=["thread-1"],
            interval_seconds=15,
            status="failed",
            last_tick_at="",
            next_tick_at="",
            stop_reason="",
            errors=[],
        )


def test_stale_without_warning_or_evidence_is_rejected():
    with pytest.raises(HeartbeatCoordinatorError):
        HeartbeatCoordinatorState.create(
            heartbeat_id="hb",
            plan_id="plan",
            coordinator_run_id="coord",
            agent_run_ids=["run-1"],
            thread_refs=["thread-1"],
            interval_seconds=15,
            status="stale",
            last_tick_at="",
            next_tick_at="",
            stop_reason="",
            warnings=[],
            evidence=[],
        )


def test_empty_tick_without_dispatch_evidence_warning_or_error_is_rejected():
    with pytest.raises(HeartbeatCoordinatorError):
        HeartbeatTick.create(
            tick_id="tick-1",
            heartbeat_id="hb",
            decision="read",
            dispatches=[],
            evidence=[],
            warnings=[],
            errors=[],
        )


def test_audit_summary_keeps_ids_status_decision_counts_without_long_dumps():
    long_text = "dispatch summary with lots of details " * 20
    dispatch = HeartbeatDispatch.create(
        target_thread_id="019eccdd-b25b-7ae2-89e5-e4b568943fa6",
        agent_run_id="run-42",
        action="send",
        summary=long_text,
    )
    state = HeartbeatCoordinatorState.create(
        heartbeat_id="hb",
        plan_id="plan",
        coordinator_run_id="coord",
        agent_run_ids=["run-1"],
        thread_refs=["thread-1"],
        interval_seconds=15,
        status=HeartbeatStatus.COMPLETED,
        last_tick_at="2026-06-16T10:00:00Z",
        next_tick_at="2026-06-16T10:00:15Z",
        stop_reason="completed successfully",
        evidence=["green tick"],
    )
    tick = HeartbeatTick.create(
        tick_id="tick-1",
        heartbeat_id="hb",
        decision=HeartbeatDecision.DISPATCH,
        dispatches=[dispatch],
        evidence=["dispatch recorded"],
        warnings=[],
        errors=[],
    )

    state_summary = state.audit_summary()
    tick_summary = tick.audit_summary()

    assert state_summary["heartbeat_id"] == "hb"
    assert state_summary["status"] == "completed"
    assert state_summary["agent_run_count"] == 1
    assert tick_summary["decision"] == "dispatch"
    assert tick_summary["dispatch_count"] == 1
    assert tick_summary["evidence_count"] == 1
    assert long_text not in repr(state_summary)
    assert long_text not in repr(tick_summary)

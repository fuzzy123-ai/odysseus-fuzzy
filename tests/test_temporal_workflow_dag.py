from __future__ import annotations

from copy import deepcopy

import pytest

from src.temporal_runtime.workflows import (
    DeterministicDagState,
    WorkflowCarryState,
    WorkflowContractError,
    WorkflowStart,
)


def _node(node_id: str, *depends_on: str, gate_ids: tuple[str, ...] = ()) -> dict:
    return {
        "node_id": node_id,
        "kind": "repo_slice",
        "depends_on": list(depends_on),
        "gate_ids": list(gate_ids),
        "verification_rule_ids": [f"verify-{node_id}"],
    }


def _manifest(
    nodes: list[dict] | None = None,
    *,
    gates: list[dict] | None = None,
    parallelism: int = 2,
    deadline_at: str = "2099-07-15T12:00:00Z",
) -> dict:
    return {
        "schema_id": "odysseus.abc.execution_manifest.v1",
        "agent_run_id": "arun-" + "a" * 32,
        "manifest_hash": "sha256:" + "b" * 64,
        "deadline_at": deadline_at,
        "max_parallel_activities": parallelism,
        "normalized_dag": {
            "nodes": nodes or [_node("only")],
            "edges": [],
            "gates": gates or [],
        },
    }


def _state(**manifest_overrides) -> DeterministicDagState:
    manifest = _manifest(**manifest_overrides)
    state = DeterministicDagState(WorkflowStart(manifest=manifest))
    state.transition("workflow_started")
    state.transition("manifest_accepted")
    return state


def _verified(node_id: str, cursor: str | None = None) -> dict:
    return {
        "node_id": node_id,
        "status": "succeeded",
        "evidence_verified": True,
        "writeback_receipt": cursor or f"receipt:{node_id}",
    }


def test_dependency_frontier_is_sorted_bounded_and_requires_success():
    state = _state(
        nodes=[_node("root-b"), _node("root-a"), _node("join", "root-a", "root-b")],
        parallelism=2,
    )

    assert state.frontier() == ("root-a", "root-b")
    for node_id in state.frontier():
        state.schedule(node_id)
        state.mark_running(node_id)
    assert state.frontier() == ()
    state.apply_activity_result("root-a", _verified("root-a"))
    assert state.frontier() == ()
    state.apply_activity_result("root-b", _verified("root-b"))
    assert state.frontier() == ("join",)


def test_runtime_gate_blocks_frontier_and_completion_until_approved():
    gate = {"gate_id": "live-go", "kind": "live_go", "blocks": ["deploy"]}
    state = _state(nodes=[_node("deploy", gate_ids=("live-go",))], gates=[gate])

    assert state.frontier() == ()
    assert state.unsatisfied_ready_gates() == ("live-go",)
    assert not state.is_complete()

    state.gate_states["live-go"] = "approved"
    assert state.frontier() == ("deploy",)
    state.schedule("deploy")
    state.mark_running("deploy")
    state.apply_activity_result("deploy", _verified("deploy"))
    assert state.is_complete()


@pytest.mark.parametrize(
    ("nodes", "gates", "code"),
    [
        ([_node("a", "missing")], [], "missing_dependency"),
        ([_node("a", "b"), _node("b", "a")], [], "dependency_cycle"),
        ([_node("a", gate_ids=("missing-gate",))], [], "missing_gate"),
    ],
)
def test_invalid_dag_is_rejected_before_initial_state_transition(nodes, gates, code):
    with pytest.raises(WorkflowContractError) as raised:
        DeterministicDagState(WorkflowStart(manifest=_manifest(nodes, gates=gates)))

    assert raised.value.code == code


@pytest.mark.parametrize(
    ("current", "event", "target"),
    [
        ("queued", "workflow_started", "starting"),
        ("starting", "manifest_accepted", "running"),
        ("starting", "manifest_rejected", "failed"),
        ("running", "unsatisfied_runtime_gate", "waiting_gate"),
        ("running", "explicit_external_input_required", "waiting_signal"),
        ("running", "pause_update_applied", "paused"),
        ("waiting_gate", "gate_decision_approved", "running"),
        ("waiting_gate", "gate_decision_rejected_when_required", "failed"),
        ("waiting_signal", "steering_signal_applied", "running"),
        ("paused", "resume_update_applied", "running"),
        ("running", "cancel_update_applied", "cancelling"),
        ("waiting_gate", "cancel_update_applied", "cancelling"),
        ("waiting_signal", "cancel_update_applied", "cancelling"),
        ("paused", "cancel_update_applied", "cancelling"),
        ("cancelling", "activities_cancelled", "cancelled"),
        ("running", "verified_completion", "completed"),
        ("running", "deadline_reached", "timed_out"),
        ("waiting_gate", "deadline_reached", "timed_out"),
        ("waiting_signal", "deadline_reached", "timed_out"),
        ("paused", "deadline_reached", "timed_out"),
        ("starting", "unrecoverable_error", "failed"),
        ("running", "unrecoverable_error", "failed"),
        ("waiting_gate", "unrecoverable_error", "failed"),
        ("waiting_signal", "unrecoverable_error", "failed"),
        ("paused", "unrecoverable_error", "failed"),
        ("cancelling", "unrecoverable_error", "failed"),
        ("queued", "admin_terminated", "terminated"),
        ("starting", "admin_terminated", "terminated"),
        ("running", "admin_terminated", "terminated"),
        ("waiting_gate", "admin_terminated", "terminated"),
        ("waiting_signal", "admin_terminated", "terminated"),
        ("paused", "admin_terminated", "terminated"),
        ("cancelling", "admin_terminated", "terminated"),
    ],
)
def test_every_declared_run_transition_is_explicit(current, event, target):
    state = DeterministicDagState(WorkflowStart(manifest=_manifest()))
    state.run_state = current
    if event == "verified_completion":
        state.slice_states["only"] = "succeeded"
        state.receipt_store_cursor = "receipt:only"
    version = state.run_version

    assert state.transition(event) == target
    assert state.run_version == version + 1


def test_invalid_run_transition_does_not_mutate_state_or_version():
    state = _state()
    before = (state.run_state, state.run_version, state.event_cursor)

    with pytest.raises(WorkflowContractError) as raised:
        state.transition("resume_update_applied")

    assert raised.value.code == "invalid_transition"
    assert (state.run_state, state.run_version, state.event_cursor) == before


@pytest.mark.parametrize(
    ("current", "event", "target"),
    [
        ("pending", "claim_granted", "claiming"),
        ("pending", "dependency_failed", "skipped"),
        ("claiming", "activity_scheduled", "activity_scheduled"),
        ("claiming", "claim_gate_required", "waiting_gate"),
        ("activity_scheduled", "activity_started", "activity_running"),
        ("activity_scheduled", "activity_cancelled", "cancelled"),
        ("activity_running", "activity_cancelled", "cancelled"),
        ("activity_running", "verified_result", "succeeded"),
        ("activity_running", "retryable_failure", "retry_wait"),
        ("activity_running", "terminal_failure", "failed"),
        ("retry_wait", "retry_due", "claiming"),
        ("waiting_gate", "gate_approved", "pending"),
        ("waiting_gate", "gate_rejected", "failed"),
    ],
)
def test_every_slice_transition_is_explicit(current, event, target):
    state = _state()
    state.slice_states["only"] = current

    assert state.transition_slice("only", event) == target


def test_success_requires_verified_evidence_and_writeback_receipt():
    state = _state()
    state.schedule("only")
    state.mark_running("only")
    before = deepcopy(state.slice_states)

    with pytest.raises(WorkflowContractError) as raised:
        state.apply_activity_result(
            "only",
            {"node_id": "only", "status": "succeeded", "evidence_verified": False},
        )

    assert raised.value.code == "unverified_activity_result"
    assert state.slice_states == before


def test_continue_as_new_preserves_logical_run_and_increments_only_segment():
    state = _state(nodes=[_node("first"), _node("second", "first")], parallelism=1)
    state.schedule("first")
    state.mark_running("first")
    state.apply_activity_result("first", _verified("first", "receipt:41"))
    original = state.projection()

    continued = DeterministicDagState(state.to_continue_start())
    carried = continued.projection()

    assert carried["agent_run_id"] == original["agent_run_id"]
    assert carried["manifest_hash"] == original["manifest_hash"]
    assert carried["run_version"] == original["run_version"]
    assert carried["slice_states"] == original["slice_states"]
    assert carried["deadline_at"] == original["deadline_at"]
    assert carried["event_cursor"] == original["event_cursor"]
    assert carried["receipt_store_cursor"] == "receipt:41"
    assert carried["history_segment"] == original["history_segment"] + 1


def test_tampered_continue_as_new_identity_is_rejected():
    state = _state()
    carry = state.to_continue_start().carry
    assert carry is not None
    tampered = WorkflowCarryState(**{**carry.__dict__, "manifest_hash": "sha256:" + "c" * 64})

    with pytest.raises(WorkflowContractError) as raised:
        DeterministicDagState(WorkflowStart(manifest=_manifest(), carry=tampered))

    assert raised.value.code == "continuation_identity_mismatch"

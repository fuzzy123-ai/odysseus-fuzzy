from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.temporal_runtime.commands import (
    CommandContractError,
    CommandLedger,
    CommandRequest,
    ExternalConditionSignal,
    OperatorNoteSignal,
)
from src.temporal_runtime.workflows import DeterministicDagState, RUN_STATES, WorkflowStart


COMMAND_STATE_MATRIX = {
    "pause": {"running", "waiting_gate", "waiting_signal"},
    "resume": {"paused"},
    "cancel": {"running", "waiting_gate", "waiting_signal", "paused"},
    "retry_activity": {"running"},
    "decide_gate": {"waiting_gate"},
    "steer_run": {"running", "waiting_signal", "paused"},
}


def _manifest(*, gate: bool = False) -> dict:
    gates = [{"gate_id": "gate-one"}] if gate else []
    return {
        "schema_id": "odysseus.abc.execution_manifest.v1",
        "agent_run_id": "arun-" + "d" * 32,
        "manifest_hash": "sha256:" + "d" * 64,
        "deadline_at": (datetime.now(timezone.utc) + timedelta(minutes=5))
        .isoformat()
        .replace("+00:00", "Z"),
        "max_parallel_activities": 1,
        "normalized_dag": {
            "nodes": [
                {
                    "node_id": "node-one",
                    "kind": "repo_slice",
                    "depends_on": [],
                    "gate_ids": ["gate-one"] if gate else [],
                    "verification_rule_ids": ["verify-node-one"],
                }
            ],
            "edges": [],
            "gates": gates,
        },
    }


def _state(*, gate: bool = False) -> DeterministicDagState:
    state = DeterministicDagState(WorkflowStart(manifest=_manifest(gate=gate)))
    state.transition("workflow_started")
    state.transition("manifest_accepted")
    return state


def _command(
    state: DeterministicDagState,
    command: str,
    *,
    suffix: str,
    payload=None,
    expected_run_version: int | None = None,
) -> CommandRequest:
    return CommandRequest.create(
        command_id=f"command-{suffix}",
        command=command,
        expected_run_version=(
            state.run_version if expected_run_version is None else expected_run_version
        ),
        idempotency_key=f"idempotency-{suffix}",
        payload=payload or {},
    )


@pytest.mark.parametrize("source", ["running", "waiting_gate", "waiting_signal"])
def test_pause_legal_states_apply_one_versioned_transition(source):
    state = _state(gate=source == "waiting_gate")
    if source == "waiting_gate":
        state.transition("unsatisfied_runtime_gate")
    elif source == "waiting_signal":
        state.transition("explicit_external_input_required")
    before = state.run_version

    receipt = state.apply_command(_command(state, "pause", suffix=source))

    assert state.run_state == "paused"
    assert state.run_version == before + 1
    assert receipt.result_run_version == state.run_version
    assert receipt.result_code == "applied"


def test_resume_cancel_retry_gate_and_run_scoped_steer_legal_transitions():
    resumed = _state()
    resumed.apply_command(_command(resumed, "pause", suffix="pause"))
    resumed.apply_command(_command(resumed, "resume", suffix="resume"))
    assert resumed.run_state == "running"

    cancelled = _state()
    cancelled.apply_command(_command(cancelled, "cancel", suffix="cancel"))
    assert cancelled.run_state == "cancelling"

    retried = _state()
    retried.slice_states["node-one"] = "retry_wait"
    receipt = retried.apply_command(
        _command(
            retried,
            "retry_activity",
            suffix="retry",
            payload={"node_id": "node-one"},
        )
    )
    assert retried.slice_states["node-one"] == "pending"
    assert receipt.result_run_version == retried.run_version

    decided = _state(gate=True)
    decided.transition("unsatisfied_runtime_gate")
    decided.apply_command(
        _command(
            decided,
            "decide_gate",
            suffix="gate",
            payload={"gate_id": "gate-one", "decision": "approved"},
        )
    )
    assert decided.run_state == "running"
    assert decided.gate_states == {"gate-one": "approved"}

    steered = _state()
    before = steered.run_version
    steered.apply_command(
        _command(
            steered,
            "steer_run",
            suffix="steer",
            payload={"steering_ref": "instruction-local-1"},
        )
    )
    assert steered.run_state == "running"
    assert steered.run_version == before + 1


@pytest.mark.parametrize(
    ("command", "state_name", "payload"),
    [
        ("pause", "paused", {}),
        ("resume", "running", {}),
        ("cancel", "completed", {}),
        ("retry_activity", "paused", {"node_id": "node-one"}),
        ("decide_gate", "running", {"gate_id": "gate-one", "decision": "approved"}),
        ("steer_run", "waiting_gate", {"steering_ref": "instruction-local-1"}),
    ],
)
def test_every_update_rejects_an_illegal_state_without_mutation(command, state_name, payload):
    state = _state(gate=command == "decide_gate")
    state.run_state = state_name
    before = state.projection()
    request = _command(state, command, suffix=command, payload=payload)

    with pytest.raises(CommandContractError, match="illegal_command_transition"):
        state.apply_command(request)

    assert state.projection() == before
    assert state.command_ledger.count == 0


@pytest.mark.parametrize(
    ("command", "run_state"),
    [
        (command, run_state)
        for command in COMMAND_STATE_MATRIX
        for run_state in RUN_STATES
    ],
)
def test_complete_update_legal_transition_matrix(command, run_state):
    state = _state(gate=command == "decide_gate")
    state.run_state = run_state
    payload: dict[str, object] = {}
    if command == "retry_activity":
        state.slice_states["node-one"] = "retry_wait"
        payload = {"node_id": "node-one"}
    elif command == "decide_gate":
        payload = {"gate_id": "gate-one", "decision": "approved"}
    elif command == "steer_run":
        payload = {"steering_ref": "instruction-matrix"}
    request = _command(
        state,
        command,
        suffix=f"matrix-{command}-{run_state}",
        payload=payload,
    )
    before = state.projection()

    if run_state in COMMAND_STATE_MATRIX[command]:
        receipt = state.apply_command(request)
        assert receipt.command == command
        assert state.command_ledger.count == 1
    else:
        with pytest.raises(CommandContractError, match="illegal_command_transition"):
            state.apply_command(request)
        assert state.projection() == before
        assert state.command_ledger.count == 0


def test_duplicate_from_two_clients_returns_original_receipt_and_applies_once():
    state = _state()
    request = _command(state, "pause", suffix="shared")
    first = state.apply_command(request)
    version_after_first = state.run_version
    second = state.apply_command(request)

    assert second == first
    assert state.run_version == version_after_first
    assert state.command_ledger.count == 1


def test_id_or_key_rebinding_is_rejected_without_state_mutation():
    state = _state()
    original = _command(state, "pause", suffix="original")
    state.apply_command(original)
    before = state.projection()
    conflict = CommandRequest.create(
        command_id=original.command_id,
        command="resume",
        expected_run_version=state.run_version,
        idempotency_key="idempotency-conflict",
        payload={},
    )

    with pytest.raises(CommandContractError, match="command_conflict"):
        state.apply_command(conflict)

    assert state.projection() == before
    assert state.command_ledger.count == 1


def test_stale_expected_version_rejected_without_receipt_or_projection_mutation():
    state = _state()
    before = state.projection()

    with pytest.raises(CommandContractError, match="stale_run_version"):
        state.apply_command(
            _command(
                state,
                "pause",
                suffix="stale",
                expected_run_version=state.run_version - 1,
            )
        )

    assert state.projection() == before
    assert state.command_ledger.count == 0


def test_structural_steer_requires_plan_revision_and_keeps_manifest_byte_stable():
    state = _state()
    manifest_before = state.manifest.copy()
    hash_before = state.manifest_hash
    version_before = state.run_version
    receipt = state.apply_command(
        _command(
            state,
            "steer_run",
            suffix="structural",
            payload={"allowed_paths": ["src/new_scope.py"]},
        )
    )

    assert receipt.result_code == "requires_plan_revision"
    assert receipt.result_run_version == version_before
    assert state.run_version == version_before
    assert state.manifest == manifest_before
    assert state.manifest_hash == hash_before
    assert state.command_ledger.count == 1


def test_receipt_ledger_survives_continue_as_new_and_deduplicates():
    state = _state()
    request = _command(
        state,
        "steer_run",
        suffix="continue",
        payload={"steering_ref": "instruction-continue"},
    )
    original = state.apply_command(request)

    continued = DeterministicDagState(state.to_continue_start())
    duplicate = continued.apply_command(request)

    assert duplicate == original
    assert continued.command_ledger.count == 1
    assert continued.run_version == state.run_version


def test_signal_records_are_bounded_refs_and_survive_continue_as_new():
    state = _state()
    manifest_before = state.manifest.copy()
    state.record_operator_note(
        OperatorNoteSignal.create(note_id="note-one", note_ref="note-ref-one")
    )
    state.record_external_condition(
        ExternalConditionSignal.create(condition_ref="condition-one")
    )

    continued = DeterministicDagState(state.to_continue_start())

    assert continued.operator_notes == {"note-one": "note-ref-one"}
    assert continued.external_condition_revisions == {"condition-one": 1}
    assert continued.manifest == manifest_before


def test_ledger_rejects_conflicting_persisted_receipts():
    state = _state()
    first = state.apply_command(_command(state, "pause", suffix="one"))
    payload = first.to_payload()
    payload["idempotency_key"] = "idempotency-other"

    with pytest.raises(CommandContractError, match="duplicated"):
        CommandLedger((first.to_payload(), payload))


def test_structural_payload_rejects_secrets_and_absolute_paths():
    state = _state()
    for payload in (
        {"scope": {"api_key": "hidden"}},
        {"allowed_paths": ["C:\\outside\\file.py"]},
    ):
        with pytest.raises(CommandContractError, match="invalid_command_payload"):
            _command(state, "steer_run", suffix="unsafe", payload=payload)

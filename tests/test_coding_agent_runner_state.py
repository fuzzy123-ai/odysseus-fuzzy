from types import SimpleNamespace

import pytest

from src.agent_sandbox_worker_api import SandboxWorkerStatus
from src.coding_agent_runner_state import (
    CodingRunnerState,
    CodingRunnerStateError,
    CodingRunnerStateStore,
    transition_from_clarification_run,
    record_advisory_memory_checkpoint,
    transition_from_sandbox_dispatch,
    transition_from_task_control_event,
)
from src.runtime_event_envelope import stable_payload_hash


_REVISION_BINDING = "sha256:" + "e" * 64


def _planning_binding() -> dict[str, object]:
    return {
        "status": "validated",
        "planning_item_id": "ACPR-11",
        "canonical_plan_revision": "plan-rev-1",
        "acceptance_contract": "acceptance-contract-1",
        "allowed_paths": ["src", "tests"],
        "gate_requirements": ["machine_auto", "agent_auto"],
    }


def _memory_receipt(state: CodingRunnerState) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema": "odysseus.coding_agent.memory_checkpoint_receipt.v1",
        "checkpoint": "pre_edit",
        "planning": {
            "planning_item_id": state.planning_item_id,
            "canonical_plan_revision": state.canonical_plan_revision,
            "binding_digest": state.planning_binding_digest,
            "acceptance_contract": state.planning_acceptance_contract,
            "allowed_paths_digest": stable_payload_hash(state.planning_allowed_paths),
            "gate_requirements": state.planning_gate_requirements,
        },
        "scope_digest": stable_payload_hash(
            {"normalized_claim_scope": state.planning_allowed_paths}
        ),
        "revision_binding": _REVISION_BINDING,
        "advisory_only": True,
        "authority_effect": "none",
        "gate_effect": "none",
        "execution_allowed": False,
        "write_allowed": False,
        "dispatch_allowed": False,
        "live_effect_allowed": False,
        "raw_content_visible": False,
    }
    receipt["receipt_id"] = stable_payload_hash(receipt)
    return receipt


class _InMemoryRunnerStore:
    def __init__(self, state: CodingRunnerState) -> None:
        self.state = state

    def read(self, task_id: object) -> CodingRunnerState | None:
        return self.state if str(task_id) == self.state.task_id else None

    def write(self, state: CodingRunnerState) -> CodingRunnerState:
        self.state = state
        return state


def _record(store, task_id: str, receipt: dict[str, object]):
    return record_advisory_memory_checkpoint(
        store=store,
        task_id=task_id,
        receipt=receipt,
        expected_revision_binding=_REVISION_BINDING,
    )


def test_runner_state_persists_and_survives_store_recreation(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    state = CodingRunnerState.create(
        task_id="task-alpha",
        repo_id="demo",
        phase="planned",
        next_human_decision="Review scope.",
    )

    store.write(state)
    loaded = CodingRunnerStateStore(tmp_path / "runner-state").read("task-alpha")

    assert loaded is not None
    assert loaded.task_id == "task-alpha"
    assert loaded.phase == "planned"
    assert loaded.to_dict()["raw_content_visible"] is False


def test_runner_state_blocks_invalid_transition():
    state = CodingRunnerState.create(task_id="task-alpha", repo_id="demo", phase="done")

    with pytest.raises(CodingRunnerStateError, match="invalid runner transition"):
        state.transition(phase="planned")


def test_runner_state_rejects_secrets_and_host_paths():
    with pytest.raises(CodingRunnerStateError, match="secret"):
        CodingRunnerState.create(task_id="task-alpha", repo_id="demo", blockers=["token=abc123"])

    with pytest.raises(CodingRunnerStateError, match="host paths"):
        CodingRunnerState.create(task_id=r"C:\Users\example\project", repo_id="demo")


def test_store_blocks_plan_ready_task_without_validated_planning_binding(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    plan = SimpleNamespace(
        task_id="task-alpha",
        repo_id="demo",
        decision="plan_ready",
        blockers=(),
        next_human_decision="Approve scoped coding task.",
        objective="Private implementation objective that must not persist",
    )

    state = store.upsert_from_task_plan(plan)
    payload = state.to_dict()

    assert payload["phase"] == "blocked"
    assert payload["gates_waiting"] == ["planning_authority"]
    assert "validated Planning item" in payload["blockers"][0]
    assert "objective" not in payload
    assert "Private implementation" not in str(payload)


def test_runner_state_reflects_clarification_lifecycle(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")

    clarifying = transition_from_clarification_run(
        store=store,
        task_id="task-alpha",
        repo_id="demo",
        clarification_run={
            "clarification_id": "clar-12345678",
            "status": "clarifying",
            "unresolved_required_count": 2,
            "ready_for_plan": False,
        },
    )
    review = transition_from_clarification_run(
        store=store,
        task_id="task-alpha",
        repo_id="demo",
        clarification_run={
            "clarification_id": "clar-12345678",
            "status": "understanding_review",
            "unresolved_required_count": 0,
            "ready_for_plan": False,
        },
    )
    ready = transition_from_clarification_run(
        store=store,
        task_id="task-alpha",
        repo_id="demo",
        clarification_run={
            "clarification_id": "clar-12345678",
            "status": "ready_for_plan",
            "unresolved_required_count": 0,
            "ready_for_plan": True,
        },
    )

    assert clarifying.phase == "clarifying"
    assert clarifying.gates_waiting == ("clarification_required",)
    assert review.phase == "understanding_review"
    assert review.gates_waiting == ("confirm_understanding",)
    assert ready.phase == "ready_for_plan"
    assert ready.gates_waiting == ("create_plan",)


def test_runner_state_consumes_successful_sandbox_dispatch_as_review_ready(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    plan = SimpleNamespace(
        task_id="task-alpha",
        repo_id="demo",
        decision="plan_ready",
        blockers=(),
        next_human_decision="Run checks.",
        planning_binding=_planning_binding(),
    )
    dispatch = SimpleNamespace(
        quality_gate={"verified": True, "blockers": []},
        statuses=(SandboxWorkerStatus.create(job_id="task-alpha-check-1", status="dry_run"),),
    )

    state = transition_from_sandbox_dispatch(store=store, plan=plan, dispatch=dispatch)

    assert state.phase == "review_ready"
    assert state.progress_percent == 65
    assert state.gates_waiting == ("operator_review",)
    assert state.blockers == ()
    assert state.event_count >= 3
    assert store.read("task-alpha").phase == "review_ready"


def test_runner_state_consumes_failed_sandbox_dispatch_as_blocked(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    plan = SimpleNamespace(
        task_id="task-beta",
        repo_id="demo",
        decision="plan_ready",
        blockers=(),
        next_human_decision="Run checks.",
        planning_binding=_planning_binding(),
    )
    dispatch = SimpleNamespace(
        quality_gate={"verified": False, "blockers": ["changed path outside allowed scope"]},
        statuses=(SandboxWorkerStatus.create(job_id="task-beta-check-1", status="failed", exit_code=1),),
    )

    state = transition_from_sandbox_dispatch(store=store, plan=plan, dispatch=dispatch)

    assert state.phase == "blocked"
    assert state.gates_waiting == ("sandbox_check_failure",)
    assert "changed path outside allowed scope" in state.blockers
    assert "sandbox job task-beta-check-1 status failed" in state.blockers
    assert "fix the failing check" in state.next_human_decision


def test_runner_persists_revision_bound_advisory_memory_receipt_without_changing_gates(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    plan = SimpleNamespace(
        task_id="task-memory",
        repo_id="demo",
        decision="plan_ready",
        blockers=(),
        next_human_decision="Run checks.",
        planning_binding=_planning_binding(),
    )
    initial = store.upsert_from_task_plan(plan)
    receipt = _memory_receipt(initial)

    recorded = _record(store, "task-memory", receipt)

    assert recorded.phase == "scoped"
    assert recorded.gates_waiting == ()
    assert recorded.memory_checkpoint_receipt_ids == (receipt["receipt_id"],)
    assert recorded.to_dict()["planning"]["canonical_plan_revision"] == "plan-rev-1"


def test_runner_rejects_memory_receipt_that_conflicts_with_planning_or_gates(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    state = store.upsert_from_task_plan(
        SimpleNamespace(task_id="task-memory", repo_id="demo", decision="plan_ready", blockers=(), planning_binding=_planning_binding())
    )
    receipt = _memory_receipt(state)
    receipt["gate_effect"] = "closed"
    receipt["receipt_id"] = stable_payload_hash(
        {key: value for key, value in receipt.items() if key != "receipt_id"}
    )

    with pytest.raises(CodingRunnerStateError, match="authority or gates"):
        _record(store, "task-memory", receipt)


@pytest.mark.parametrize(
    ("field_name", "forged_value", "expected"),
    (
        ("advisory_only", False, "advisory only"),
        ("execution_allowed", True, "execution_allowed must be false"),
        ("write_allowed", True, "write_allowed must be false"),
        ("dispatch_allowed", True, "dispatch_allowed must be false"),
        ("live_effect_allowed", True, "live_effect_allowed must be false"),
    ),
)
def test_runner_rejects_each_forged_memory_effect_flag(
    tmp_path, field_name: str, forged_value: object, expected: str
):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    state = store.upsert_from_task_plan(
        SimpleNamespace(
            task_id="task-memory-effects",
            repo_id="demo",
            decision="plan_ready",
            blockers=(),
            planning_binding=_planning_binding(),
        )
    )
    receipt = _memory_receipt(state)
    receipt[field_name] = forged_value
    receipt["receipt_id"] = stable_payload_hash(
        {key: value for key, value in receipt.items() if key != "receipt_id"}
    )

    with pytest.raises(CodingRunnerStateError, match=expected):
        _record(store, "task-memory-effects", receipt)


def test_runner_rejects_memory_payload_or_digest_tampering(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    state = store.upsert_from_task_plan(
        SimpleNamespace(
            task_id="task-memory-digest",
            repo_id="demo",
            decision="plan_ready",
            blockers=(),
            planning_binding=_planning_binding(),
        )
    )
    payload_tampered = _memory_receipt(state)
    payload_tampered["checkpoint"] = "failure_retrieval"
    digest_tampered = _memory_receipt(state)
    digest_tampered["receipt_id"] = "sha256:" + "f" * 64
    noncanonical = _memory_receipt(state)
    noncanonical["receipt_id"] = "receipt-not-a-digest"

    for receipt in (payload_tampered, digest_tampered):
        with pytest.raises(CodingRunnerStateError, match="does not match canonical payload"):
            _record(store, "task-memory-digest", receipt)
    with pytest.raises(CodingRunnerStateError, match="canonical SHA-256"):
        _record(store, "task-memory-digest", noncanonical)


@pytest.mark.parametrize(
    ("field_name", "forged_value", "expected"),
    (
        ("planning.acceptance_contract", "acceptance-contract-foreign", "acceptance contract"),
        ("planning.allowed_paths_digest", "sha256:" + "f" * 64, "allowed paths"),
        ("planning.gate_requirements", ("agent_auto",), "gate requirements"),
        ("scope_digest", "sha256:" + "f" * 64, "scope"),
        ("revision_binding", "sha256:" + "f" * 64, "revision binding is stale"),
    ),
)
def test_runner_in_memory_rejects_each_planning_receipt_mismatch(
    field_name: str, forged_value: object, expected: str
):
    state = CodingRunnerState.create(
        task_id="task-memory-semantic",
        repo_id="demo",
        phase="scoped",
        planning_binding=_planning_binding(),
    )
    store = _InMemoryRunnerStore(state)
    receipt = _memory_receipt(state)
    if field_name.startswith("planning."):
        receipt["planning"][field_name.split(".", 1)[1]] = forged_value
    else:
        receipt[field_name] = forged_value
    receipt["receipt_id"] = stable_payload_hash(
        {key: value for key, value in receipt.items() if key != "receipt_id"}
    )

    with pytest.raises(CodingRunnerStateError, match=expected):
        _record(store, "task-memory-semantic", receipt)


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("planning.allowed_paths_digest", "SHA256:" + "f" * 64),
        ("scope_digest", "sha256:short"),
        ("revision_binding", "sha256:" + "F" * 64),
    ),
)
def test_runner_in_memory_rejects_noncanonical_semantic_digests(
    field_name: str, forged_value: object
):
    state = CodingRunnerState.create(
        task_id="task-memory-canonical",
        repo_id="demo",
        phase="scoped",
        planning_binding=_planning_binding(),
    )
    store = _InMemoryRunnerStore(state)
    receipt = _memory_receipt(state)
    if field_name.startswith("planning."):
        receipt["planning"][field_name.split(".", 1)[1]] = forged_value
    else:
        receipt[field_name] = forged_value
    receipt["receipt_id"] = stable_payload_hash(
        {key: value for key, value in receipt.items() if key != "receipt_id"}
    )

    with pytest.raises(CodingRunnerStateError, match="canonical lowercase SHA-256"):
        _record(store, "task-memory-canonical", receipt)


def test_runner_in_memory_requires_canonical_expected_revision_binding():
    state = CodingRunnerState.create(
        task_id="task-memory-expected-revision",
        repo_id="demo",
        phase="scoped",
        planning_binding=_planning_binding(),
    )
    store = _InMemoryRunnerStore(state)

    with pytest.raises(CodingRunnerStateError, match="expected_revision_binding"):
        record_advisory_memory_checkpoint(
            store=store,
            task_id=state.task_id,
            receipt=_memory_receipt(state),
            expected_revision_binding="revision-not-a-digest",
        )


def test_runner_in_memory_accepts_planning_intake_allowed_scope_binding():
    state = CodingRunnerState.create(
        task_id="task-memory-intake",
        repo_id="demo",
        phase="planned",
        planning_binding=_planning_binding(),
    )
    store = _InMemoryRunnerStore(state)
    receipt = _memory_receipt(state)
    receipt["checkpoint"] = "planning_intake"
    receipt["scope_digest"] = stable_payload_hash(
        {"normalized_allowed_scope": state.planning_allowed_paths}
    )
    receipt["receipt_id"] = stable_payload_hash(
        {key: value for key, value in receipt.items() if key != "receipt_id"}
    )

    recorded = _record(store, state.task_id, receipt)

    assert recorded.memory_checkpoint_receipt_ids == (receipt["receipt_id"],)
    assert recorded.phase == "planned"


def test_runner_in_memory_accepts_pre_edit_claim_scope_and_revision_binding():
    state = CodingRunnerState.create(
        task_id="task-memory-pre-edit",
        repo_id="demo",
        phase="scoped",
        planning_binding=_planning_binding(),
    )
    store = _InMemoryRunnerStore(state)
    receipt = _memory_receipt(state)

    recorded = _record(store, state.task_id, receipt)

    assert recorded.memory_checkpoint_receipt_ids == (receipt["receipt_id"],)
    assert recorded.planning_binding_digest == state.planning_binding_digest
    assert recorded.gates_waiting == state.gates_waiting


def test_runner_state_refuses_sandbox_dispatch_after_done(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    store.write(CodingRunnerState.create(task_id="task-done", repo_id="demo", phase="done", progress_percent=100))
    plan = SimpleNamespace(task_id="task-done", repo_id="demo", decision="plan_ready")
    dispatch = SimpleNamespace(
        quality_gate={"verified": True, "blockers": []},
        statuses=(SandboxWorkerStatus.create(job_id="task-done-check-1", status="dry_run"),),
    )

    with pytest.raises(CodingRunnerStateError, match="completed runner state"):
        transition_from_sandbox_dispatch(store=store, plan=plan, dispatch=dispatch)


def test_runner_state_consumes_pause_and_resume_control_events(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    store.write(CodingRunnerState.create(task_id="task-remote", repo_id="demo", phase="review_ready", progress_percent=65))

    paused = transition_from_task_control_event(
        store=store,
        event={"task_id": "task-remote", "task_type": "coding_agent_task", "status": "pause_requested"},
    )
    resumed = transition_from_task_control_event(
        store=store,
        event={"task_id": "task-remote", "task_type": "coding_agent_task", "status": "resume_requested"},
    )

    assert paused.phase == "blocked"
    assert paused.gates_waiting == ("telegram_pause_requested",)
    assert "telegram pause requested" in paused.blockers
    assert resumed.phase == "review_ready"
    assert resumed.gates_waiting == ()
    assert resumed.blockers == ()


def test_runner_state_consumes_cancel_control_event_as_blocked(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    store.write(CodingRunnerState.create(task_id="task-cancel", repo_id="demo", phase="scoped", progress_percent=20))

    state = transition_from_task_control_event(
        store=store,
        event={"task_id": "task-cancel", "task_type": "coding_agent_task", "status": "cancel_requested"},
    )

    assert state.phase == "blocked"
    assert state.gates_waiting == ("telegram_cancel_requested",)
    assert "confirm discard" in state.next_human_decision


def test_runner_state_rejects_control_events_for_other_task_types(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    store.write(CodingRunnerState.create(task_id="task-other", repo_id="demo", phase="scoped", progress_percent=20))

    with pytest.raises(CodingRunnerStateError, match="not for a coding_agent_task"):
        transition_from_task_control_event(
            store=store,
            event={"task_id": "task-other", "task_type": "website_research", "status": "pause_requested"},
        )

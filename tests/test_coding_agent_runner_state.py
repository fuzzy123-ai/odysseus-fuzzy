from types import SimpleNamespace

import pytest

from src.coding_agent_runner_state import CodingRunnerState, CodingRunnerStateError, CodingRunnerStateStore


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
        CodingRunnerState.create(task_id=r"C:\Users\nkatz\project", repo_id="demo")


def test_store_upserts_from_task_plan_without_raw_objective(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    plan = SimpleNamespace(
        task_id="task-alpha",
        repo_id="demo",
        decision="hold",
        blockers=("operator decision is not go",),
        next_human_decision="Approve scoped coding task.",
        objective="Private implementation objective that must not persist",
    )

    state = store.upsert_from_task_plan(plan)
    payload = state.to_dict()

    assert payload["phase"] == "blocked"
    assert payload["gates_waiting"] == ["operator_go"]
    assert "objective" not in payload
    assert "Private implementation" not in str(payload)

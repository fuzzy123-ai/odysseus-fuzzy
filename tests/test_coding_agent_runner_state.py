from types import SimpleNamespace

import pytest

from src.agent_sandbox_worker_api import SandboxWorkerStatus
from src.coding_agent_runner_state import (
    CodingRunnerState,
    CodingRunnerStateError,
    CodingRunnerStateStore,
    transition_from_sandbox_dispatch,
    transition_from_task_control_event,
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


def test_runner_state_consumes_successful_sandbox_dispatch_as_review_ready(tmp_path):
    store = CodingRunnerStateStore(tmp_path / "runner-state")
    plan = SimpleNamespace(
        task_id="task-alpha",
        repo_id="demo",
        decision="plan_ready",
        blockers=(),
        next_human_decision="Run checks.",
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

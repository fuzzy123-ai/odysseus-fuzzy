import pytest

from src.agent_run_store import AgentRunStatus
from src.handoff_mailbox import ParsedHandoff
from src.runtime_quality_gates import GitStatusSnapshot, TestExecutionSnapshot
from src.subagent_runtime import (
    FakeSubagentExecutionBackend,
    InMemorySubagentRuntimeStores,
    SubagentRunSpec,
    SubagentRunState,
    SubagentRuntimeError,
    create_subagent_run,
    apply_subagent_handoff_and_gates,
)


def _spec(**overrides) -> SubagentRunSpec:
    payload = {
        "agent_run_id": "SUB2 Bob Run",
        "plan_id": "Subagent Runtime V1",
        "node_id": "SUB2",
        "slice_id": "SUB2-spawn-api",
        "agent_id": "bob",
        "role_id": "backend",
        "objective": "Persist fake scoped subagent run records.",
        "allowed_files": ["src/subagent_runtime.py", "tests/test_subagent_runtime.py"],
        "blocked_files": [],
        "inputs": {"brief": "fake backend only"},
        "expected_outputs": ["run record", "fake backend snapshot"],
        "tests": ["python -m pytest tests/test_subagent_runtime.py"],
        "handoff_format": ["Agent: bob", "Slice: SUB2-spawn-api", "Status: done"],
        "stop_conditions": ["no live thread sends"],
        "evidence_required": ["tests green"],
        "model": "fake-model",
        "thinking": "medium",
        "created_at": "2026-06-20T10:00:00Z",
        "target_kind": "job",
    }
    payload.update(overrides)
    return SubagentRunSpec.create(**payload)


def _git(**overrides) -> GitStatusSnapshot:
    payload = {
        "branch": "dev",
        "clean": True,
        "commit": "abcdef1",
        "staged_files": [],
        "unstaged_files": [],
        "untracked_files": [],
    }
    payload.update(overrides)
    return GitStatusSnapshot.create(**payload)


def _test(**overrides) -> TestExecutionSnapshot:
    payload = {
        "command": "python -m pytest tests/test_subagent_runtime.py",
        "exit_code": 0,
        "summary": "focused runtime tests passed",
    }
    payload.update(overrides)
    return TestExecutionSnapshot.create(**payload)


def _handoff(**overrides) -> ParsedHandoff:
    payload = {
        "agent": "bob",
        "slice_id": "sub2-spawn-api",
        "status": "done",
        "commit": "abcdef1",
        "changed_files": ["src/subagent_runtime.py", "tests/test_subagent_runtime.py"],
        "tests": ["python -m pytest tests/test_subagent_runtime.py"],
        "evidence": ["fake backend path verified"],
    }
    payload.update(overrides)
    return ParsedHandoff.create(**payload)


def test_create_subagent_run_persists_capsule_agent_run_and_job_ref():
    stores = InMemorySubagentRuntimeStores()
    backend = FakeSubagentExecutionBackend()

    run = create_subagent_run(_spec(), stores=stores, backend=backend)

    assert run.state == SubagentRunState.SPAWNED
    assert run.agent_run.status == AgentRunStatus.PENDING
    assert run.job_ref is not None
    assert run.thread_ref is None
    assert stores.resolve("sub2-bob-run") == run
    assert stores.capsules["sub2-bob-run-capsule"] == run.capsule
    assert stores.agent_runs["sub2-bob-run"] == run.agent_run
    assert stores.job_refs[run.job_ref.job_id] == run.job_ref
    assert backend.status("sub2-bob-run").state == SubagentRunState.SPAWNED


def test_create_subagent_run_registers_fake_thread_ref_when_requested():
    stores = InMemorySubagentRuntimeStores()
    run = create_subagent_run(
        _spec(target_kind="thread", thread_id="fake-thread-sub2"),
        stores=stores,
        backend=FakeSubagentExecutionBackend(),
    )

    assert run.thread_ref is not None
    assert run.job_ref is None
    assert stores.thread_registry.resolve_run("sub2-bob-run").thread_id == "fake-thread-sub2"


def test_duplicate_agent_run_id_is_rejected():
    stores = InMemorySubagentRuntimeStores()
    backend = FakeSubagentExecutionBackend()

    create_subagent_run(_spec(), stores=stores, backend=backend)
    with pytest.raises(SubagentRuntimeError, match="already exists"):
        create_subagent_run(_spec(), stores=stores, backend=FakeSubagentExecutionBackend())


def test_fake_backend_pause_resume_cancel_retry_status_and_read_handoff():
    stores = InMemorySubagentRuntimeStores()
    backend = FakeSubagentExecutionBackend()
    run = create_subagent_run(_spec(), stores=stores, backend=backend)

    paused = backend.pause(run.agent_run_id)
    assert paused.state == SubagentRunState.PAUSED

    resumed = backend.resume(run.agent_run_id)
    assert resumed.state == SubagentRunState.SPAWNED

    cancelled = backend.cancel(run.agent_run_id)
    assert cancelled.state == SubagentRunState.CANCELLED

    retried = backend.retry(run.agent_run_id)
    assert retried.state == SubagentRunState.SPAWNED
    assert retried.attempts == 2

    backend.set_handoff(run.agent_run_id, _handoff())
    assert backend.read(run.agent_run_id).status.value == "done"
    assert backend.status(run.agent_run_id).state == SubagentRunState.DONE


def test_apply_handoff_and_gates_marks_verified_done_only_after_all_gates_pass():
    stores = InMemorySubagentRuntimeStores()
    run = create_subagent_run(_spec(), stores=stores, backend=FakeSubagentExecutionBackend())

    verified = apply_subagent_handoff_and_gates(
        run,
        handoff=_handoff(),
        git_status=_git(),
        test_results=[_test()],
        verified_at="2026-06-20T10:05:00Z",
        verified_by="charlie",
        stores=stores,
    )

    assert verified.state == SubagentRunState.DONE
    assert verified.verified_done is True
    assert verified.agent_run.status == AgentRunStatus.DONE
    assert stores.resolve("sub2-bob-run").verified_done is True


def test_done_handoff_without_gate_evidence_is_blocked_not_verified():
    run = create_subagent_run(_spec(), backend=FakeSubagentExecutionBackend())

    blocked = apply_subagent_handoff_and_gates(
        run,
        handoff=_handoff(),
        git_status=_git(clean=False, unstaged_files=["src/subagent_runtime.py"]),
        test_results=[_test()],
        verified_at="2026-06-20T10:05:00Z",
        verified_by="charlie",
    )

    assert blocked.state == SubagentRunState.BLOCKED
    assert blocked.verified_done is False
    assert blocked.agent_run.status == AgentRunStatus.BLOCKED
    assert "git-block" in blocked.gate_result.blocking_gate_ids


def test_handoff_scope_mismatch_is_rejected():
    run = create_subagent_run(_spec(), backend=FakeSubagentExecutionBackend())

    with pytest.raises(SubagentRuntimeError, match="does not match"):
        apply_subagent_handoff_and_gates(
            run,
            handoff=_handoff(agent="alice"),
            git_status=_git(),
            test_results=[_test()],
            verified_at="2026-06-20T10:05:00Z",
            verified_by="charlie",
        )


def test_audit_summary_does_not_dump_raw_inputs_or_external_ids():
    run = create_subagent_run(
        _spec(inputs={"private": "raw provider output should not be exposed"}),
        backend=FakeSubagentExecutionBackend(),
    )

    summary = run.audit_summary()

    assert summary["capsule"]["input_keys"] == ("private",)
    rendered = repr(summary)
    assert "raw provider output" not in rendered
    assert "fake-job-sub2-bob-run" not in rendered

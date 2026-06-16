import pytest

from src.agent_run_store import AgentRun, AgentRunStatus, AgentRunStoreError


def test_valid_run_normalizes_stably():
    run = AgentRun.create(
        agent_run_id=" OR2B Run ",
        plan_id=" OR2 Plan ",
        node_id=" Node 1 ",
        slice_id=" OR2B-agent-run-store-model-spike ",
        agent_id=" Bob Worker ",
        role_id=" Backend Owner ",
        model="deepseek-chat",
        thinking="medium",
        status="done",
        started_at="2026-06-16T10:00:00Z",
        completed_at="2026-06-16T10:10:00Z",
        changed_files=["src/agent_run_store.py", "tests/test_agent_run_store.py"],
        tests=["python -m pytest tests/test_agent_run_store.py"],
        commit="e65de563",
        warnings=["minor warning"],
        errors=[],
        blocker="",
        next_action="handoff to charlie",
        evidence=["green pytest"],
    )

    assert run.agent_run_id == "or2b-run"
    assert run.plan_id == "or2-plan"
    assert run.node_id == "node-1"
    assert run.slice_id == "or2b-agent-run-store-model-spike"
    assert run.agent_id == "bob-worker"
    assert run.role_id == "backend-owner"
    assert run.status == AgentRunStatus.DONE
    assert run.evidence.commit == "e65de563"


def test_done_without_evidence_is_rejected():
    with pytest.raises(AgentRunStoreError):
        AgentRun.create(
            agent_run_id="run",
            plan_id="plan",
            node_id="node",
            slice_id="slice",
            agent_id="bob",
            role_id="backend",
            model="deepseek-chat",
            thinking="medium",
            status="done",
            started_at="2026-06-16T10:00:00Z",
            completed_at="2026-06-16T10:10:00Z",
            changed_files=[],
            tests=[],
            commit="",
            warnings=[],
            errors=[],
            blocker="",
            next_action="",
            evidence=[],
        )


def test_failed_without_error_is_rejected():
    with pytest.raises(AgentRunStoreError):
        AgentRun.create(
            agent_run_id="run",
            plan_id="plan",
            node_id="node",
            slice_id="slice",
            agent_id="bob",
            role_id="backend",
            model="deepseek-chat",
            thinking="medium",
            status="failed",
            started_at="2026-06-16T10:00:00Z",
            completed_at="2026-06-16T10:10:00Z",
            changed_files=[],
            tests=[],
            commit="",
            warnings=[],
            errors=[],
            blocker="",
            next_action="",
            evidence=[],
        )


def test_blocked_without_blocker_is_rejected():
    with pytest.raises(AgentRunStoreError):
        AgentRun.create(
            agent_run_id="run",
            plan_id="plan",
            node_id="node",
            slice_id="slice",
            agent_id="bob",
            role_id="backend",
            model="deepseek-chat",
            thinking="medium",
            status="blocked",
            started_at="2026-06-16T10:00:00Z",
            completed_at="",
            changed_files=[],
            tests=[],
            commit="",
            warnings=[],
            errors=[],
            blocker="",
            next_action="",
            evidence=[],
        )


@pytest.mark.parametrize(
    "bad_path",
    [
        "../src/agent_run_store.py",
        "/tmp/agent_run_store.py",
        r"C:\repo\src\agent_run_store.py",
        r"src\agent_run_store.py",
    ],
)
def test_unsafe_changed_files_are_rejected(bad_path):
    with pytest.raises(AgentRunStoreError):
        AgentRun.create(
            agent_run_id="run",
            plan_id="plan",
            node_id="node",
            slice_id="slice",
            agent_id="bob",
            role_id="backend",
            model="deepseek-chat",
            thinking="medium",
            status="running",
            started_at="2026-06-16T10:00:00Z",
            completed_at="",
            changed_files=[bad_path],
            tests=[],
            commit="",
            warnings=[],
            errors=[],
            blocker="",
            next_action="continue",
            evidence=[],
        )


def test_commit_sha_is_validated():
    with pytest.raises(AgentRunStoreError):
        AgentRun.create(
            agent_run_id="run",
            plan_id="plan",
            node_id="node",
            slice_id="slice",
            agent_id="bob",
            role_id="backend",
            model="deepseek-chat",
            thinking="medium",
            status="running",
            started_at="2026-06-16T10:00:00Z",
            completed_at="",
            changed_files=[],
            tests=[],
            commit="not-a-sha",
            warnings=[],
            errors=[],
            blocker="",
            next_action="continue",
            evidence=[],
        )


def test_audit_summary_keeps_ids_status_counts_commit_and_tests_without_long_dumps():
    long_text = "tool output with many details " * 30
    run = AgentRun.create(
        agent_run_id="run",
        plan_id="plan",
        node_id="node",
        slice_id="slice",
        agent_id="bob",
        role_id="backend",
        model="deepseek-chat",
        thinking="medium",
        status="handoff",
        started_at="2026-06-16T10:00:00Z",
        completed_at="2026-06-16T10:10:00Z",
        changed_files=["src/agent_run_store.py"],
        tests=["python -m pytest tests/test_agent_run_store.py"],
        commit="fe9744b7",
        warnings=[long_text],
        errors=[],
        blocker="",
        next_action="handoff to charlie",
        evidence=[long_text],
    )

    summary = run.audit_summary()

    assert summary["agent_run_id"] == "run"
    assert summary["status"] == "handoff"
    assert summary["commit"] == "fe9744b7"
    assert summary["changed_file_count"] == 1
    assert summary["test_count"] == 1
    assert summary["evidence_count"] == 1
    assert summary["tests"] == ("python -m pytest tests/test_agent_run_store.py",)
    assert long_text not in repr(summary)

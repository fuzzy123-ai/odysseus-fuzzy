import pytest

from src.handoff_mailbox import ParsedHandoff
from src.runtime_quality_gates import GitStatusSnapshot, TestExecutionSnapshot
from src.subagent_runtime import (
    FakeSubagentExecutionBackend,
    SubagentDisplayStatus,
    SubagentRuntimeError,
    apply_subagent_handoff_and_gates,
    build_subagent_status_snapshot,
    create_subagent_run,
    run_subagent_fake_e2e_smoke,
)
from tests.test_subagent_runtime import _git, _handoff, _spec, _test


def test_subagent_status_snapshot_shows_fake_backend_run_without_live_refs():
    run = create_subagent_run(_spec(), backend=FakeSubagentExecutionBackend())

    snapshot = build_subagent_status_snapshot(
        [run],
        plan_id="subagent-runtime-v1",
        last_updated_at="2026-06-20T12:00:00Z",
    )

    item = snapshot.items[0]
    assert item.agent_id == "bob"
    assert item.slice_id == "sub2-spawn-api"
    assert item.state == SubagentDisplayStatus.PLANNED
    assert item.backend == "fake"
    assert item.allowed_actions == ("pause", "cancel", "retry")
    assert snapshot.audit_summary()["counts_by_state"] == {"planned": 1}
    assert "fake-job" not in repr(snapshot.audit_summary())


def test_status_snapshot_distinguishes_verified_done_and_gate_blocked():
    verified = apply_subagent_handoff_and_gates(
        create_subagent_run(_spec(agent_run_id="verified-run"), backend=FakeSubagentExecutionBackend()),
        handoff=_handoff(),
        git_status=_git(),
        test_results=[_test()],
        verified_at="2026-06-20T12:05:00Z",
        verified_by="charlie",
    )
    blocked = apply_subagent_handoff_and_gates(
        create_subagent_run(_spec(agent_run_id="blocked-run"), backend=FakeSubagentExecutionBackend()),
        handoff=_handoff(),
        git_status=_git(clean=False, unstaged_files=["src/subagent_runtime.py"]),
        test_results=[_test()],
        verified_at="2026-06-20T12:06:00Z",
        verified_by="charlie",
    )

    snapshot = build_subagent_status_snapshot(
        [verified, blocked],
        plan_id="subagent-runtime-v1",
        last_updated_at="2026-06-20T12:07:00Z",
    )

    states = {item.agent_run_id: item.state for item in snapshot.items}
    assert states["verified-run"] == SubagentDisplayStatus.VERIFIED_DONE
    assert states["blocked-run"] == SubagentDisplayStatus.GATE_BLOCKED
    blocked_item = next(item for item in snapshot.items if item.agent_run_id == "blocked-run")
    assert blocked_item.allowed_actions == ("retry",)
    assert "runtime gates blocked" in blocked_item.blocker


def test_status_snapshot_can_show_claimed_done_before_gates():
    run = create_subagent_run(_spec(), backend=FakeSubagentExecutionBackend())
    claimed = run.__class__(
        spec=run.spec,
        state=run.state,
        capsule=run.capsule,
        agent_run=run.agent_run,
        backend=run.backend,
        thread_ref=run.thread_ref,
        job_ref=run.job_ref,
        handoff=ParsedHandoff.create(
            agent="bob",
            slice_id="sub2-spawn-api",
            status="done",
            evidence=["claimed only"],
        ),
    )

    snapshot = build_subagent_status_snapshot(
        [claimed],
        plan_id="subagent-runtime-v1",
        last_updated_at="2026-06-20T12:08:00Z",
    )

    assert snapshot.items[0].state == SubagentDisplayStatus.CLAIMED_DONE


def test_status_snapshot_rejects_mixed_plans():
    run = create_subagent_run(_spec(plan_id="subagent-runtime-v1"), backend=FakeSubagentExecutionBackend())

    with pytest.raises(SubagentRuntimeError, match="requested plan_id"):
        build_subagent_status_snapshot(
            [run],
            plan_id="other-plan",
            last_updated_at="2026-06-20T12:09:00Z",
        )


def test_subagent_fake_e2e_smoke_proves_verified_and_gate_blocked_paths():
    snapshot = run_subagent_fake_e2e_smoke()
    summary = snapshot.audit_summary()

    assert summary["plan_id"] == "subagent-runtime-v1"
    assert summary["run_count"] == 2
    assert summary["counts_by_state"] == {"gate_blocked": 1, "verified_done": 1}
    assert {item["agent_id"] for item in summary["items"]} == {"alice", "bob"}
    assert all("restore" not in item["allowed_actions"] for item in summary["items"])
    assert all("delete" not in item["allowed_actions"] for item in summary["items"])


def test_subagent_status_snapshot_exposes_resume_for_paused_runs():
    backend = FakeSubagentExecutionBackend()
    run = create_subagent_run(_spec(), backend=backend)
    backend.pause(run.agent_run_id)
    paused = run.__class__(
        spec=run.spec,
        state=backend.status(run.agent_run_id).state,
        capsule=run.capsule,
        agent_run=run.agent_run,
        backend=run.backend,
        thread_ref=run.thread_ref,
        job_ref=run.job_ref,
    )

    snapshot = build_subagent_status_snapshot(
        [paused],
        plan_id="subagent-runtime-v1",
        last_updated_at="2026-06-20T12:10:00Z",
    )

    assert snapshot.items[0].state == SubagentDisplayStatus.PAUSED
    assert snapshot.items[0].allowed_actions == ("resume", "cancel")

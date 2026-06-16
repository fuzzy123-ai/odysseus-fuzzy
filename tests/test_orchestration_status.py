from src.orchestration_status import (
    AgentPathSummary,
    DashboardItem,
    LensSummary,
    NextAction,
    OrchestrationHealth,
    OrchestrationStatusError,
    OrchestrationStatusSnapshot,
)


def _make_agent_path(**overrides) -> AgentPathSummary:
    payload = {
        "agent_id": "bob",
        "role_id": "backend",
        "status": "healthy",
        "progress_percent": 65,
        "active_slice_id": "or6b",
    }
    payload.update(overrides)
    return AgentPathSummary.create(**payload)


def _make_blocking_item(**overrides) -> DashboardItem:
    payload = {
        "item_id": "gate-block",
        "title": "Quality gate blocked",
        "status": "blocked",
        "summary": "Required scope gate is still red.",
    }
    payload.update(overrides)
    return DashboardItem.create(**payload)


def _make_lens(**overrides) -> LensSummary:
    payload = {
        "lens_id": "agent-runs",
        "status": "healthy",
        "count": 2,
        "summary": "Alice and Bob have visible run summaries.",
    }
    payload.update(overrides)
    return LensSummary.create(**payload)


def _make_next_action(**overrides) -> NextAction:
    payload = {
        "owner": "charlie",
        "action": "review handoff",
        "summary": "Check the OR6B snapshot before route integration.",
    }
    payload.update(overrides)
    return NextAction.create(**payload)


def _make_snapshot(**overrides) -> OrchestrationStatusSnapshot:
    payload = {
        "dashboard_id": "dashboard-main",
        "plan_id": "plan-12",
        "plan_status": "healthy",
        "overall_progress_percent": 72,
        "agent_paths": [_make_agent_path()],
        "heartbeat_status": "waiting",
        "agent_runs": [_make_lens(lens_id="agent-runs", count=2)],
        "quality_gates": [_make_lens(lens_id="quality-gates", count=3, summary="1 pass, 1 warn, 0 block")],
        "blocking_items": (),
        "next_actions": [_make_next_action()],
        "last_updated_at": "2026-06-16T12:30:00Z",
        "evidence_refs": ["commit 6e06a4c5", "pytest tests/test_orchestration_status.py"],
        "warnings": (),
    }
    payload.update(overrides)
    return OrchestrationStatusSnapshot.create(**payload)


def test_valid_snapshot_normalizes_stably() -> None:
    snapshot = _make_snapshot(
        dashboard_id=" Dashboard Main ",
        agent_paths=[
            _make_agent_path(agent_id="charlie", progress_percent=80),
            _make_agent_path(agent_id="alice", progress_percent=40, active_slice_id="or6a"),
        ],
    )

    assert snapshot.dashboard_id == "dashboard-main"
    assert snapshot.plan_id == "plan-12"
    assert snapshot.plan_status is OrchestrationHealth.HEALTHY
    assert snapshot.heartbeat_status is OrchestrationHealth.WAITING
    assert snapshot.agent_paths[0].agent_id == "alice"
    assert snapshot.agent_paths[1].agent_id == "charlie"


def test_progress_outside_range_is_rejected() -> None:
    try:
        _make_snapshot(overall_progress_percent=101)
    except OrchestrationStatusError as exc:
        assert "between 0 and 100" in str(exc)
    else:
        raise AssertionError("expected progress validation to fail")


def test_blocked_without_blocking_item_or_evidence_is_rejected() -> None:
    try:
        _make_snapshot(
            plan_status="blocked",
            blocking_items=(),
            evidence_refs=(),
        )
    except OrchestrationStatusError as exc:
        assert "blocking_items or evidence_refs" in str(exc)
    else:
        raise AssertionError("expected blocked snapshot to require blockers or evidence")


def test_completed_without_100_percent_or_evidence_is_rejected() -> None:
    try:
        _make_snapshot(
            plan_status="completed",
            overall_progress_percent=88,
            evidence_refs=(),
        )
    except OrchestrationStatusError as exc:
        assert "100 percent progress or completion evidence" in str(exc)
    else:
        raise AssertionError("expected completed snapshot to require full progress or evidence")


def test_stale_without_warning_or_evidence_is_rejected() -> None:
    try:
        _make_snapshot(
            plan_status="stale",
            evidence_refs=(),
            warnings=(),
        )
    except OrchestrationStatusError as exc:
        assert "warnings or evidence_refs" in str(exc)
    else:
        raise AssertionError("expected stale snapshot to require warnings or evidence")


def test_next_action_without_owner_or_action_is_rejected() -> None:
    try:
        _make_next_action(owner=" ", action=" ")
    except OrchestrationStatusError as exc:
        assert "must not be empty" in str(exc)
    else:
        raise AssertionError("expected next action validation to fail")


def test_healthy_snapshot_requires_agent_run_and_quality_gate_lenses() -> None:
    try:
        _make_snapshot(agent_runs=(), quality_gates=())
    except OrchestrationStatusError as exc:
        assert "agent_runs and quality_gates" in str(exc)
    else:
        raise AssertionError("expected healthy snapshot to require compact lenses")


def test_audit_summary_contains_ids_progress_counts_status_without_long_dumps() -> None:
    long_evidence = "thread history " + ("x" * 500)
    snapshot = _make_snapshot(
        evidence_refs=[long_evidence],
        blocking_items=[_make_blocking_item(item_id="handoff-wait", status="waiting")],
    )

    summary = snapshot.audit_summary()

    assert summary["dashboard_id"] == "dashboard-main"
    assert summary["plan_status"] == "healthy"
    assert summary["overall_progress_percent"] == 72
    assert summary["agent_path_count"] == 1
    assert summary["agent_run_lens_count"] == 1
    assert summary["quality_gate_lens_count"] == 1
    assert summary["blocking_item_count"] == 1
    assert summary["next_action_count"] == 1
    assert summary["evidence_ref_count"] == 1
    assert "thread history" not in str(summary)
    assert "x" * 200 not in str(summary)

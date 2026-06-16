from src.orchestration_e2e_smoke import run_two_agent_smoke


def test_two_agent_smoke_reaches_verified_completed_dashboard():
    result = run_two_agent_smoke()
    summary = result.audit_summary()

    assert summary["plan_count"] == 1
    assert summary["run_count"] == 2
    assert summary["thread_count"] == 2
    assert summary["mailbox_messages"] == 1
    assert summary["verified_done"] is True
    assert summary["dashboard_status"] == "completed"
    assert summary["dashboard_progress"] == 100


def test_two_agent_smoke_preserves_mailbox_and_next_action_visibility():
    result = run_two_agent_smoke()

    assert result.mailbox.audit_summary()["queued_count"] == 1
    assert result.dashboard.next_actions[0].action == "dispatch queued message"
    assert any(ref.startswith("mailbox:") for ref in result.dashboard.evidence_refs)


def test_two_agent_smoke_keeps_known_fake_boundaries_visible():
    result = run_two_agent_smoke()

    assert result.dashboard.plan_id == "auto7-plan"
    assert result.dashboard.agent_paths[0].agent_id == "alice"
    assert result.dashboard.agent_paths[1].agent_id == "bob"
    assert result.dashboard.quality_gates[0].summary

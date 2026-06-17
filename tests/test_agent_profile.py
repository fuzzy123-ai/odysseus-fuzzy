import pytest

from src.agent_profile import (
    AgentProfile,
    AgentProfileError,
    AgentProfileOverride,
    AgentProfileVisibility,
)


def test_alice_bob_charlie_profiles_normalize_stably():
    alice = AgentProfile.create(
        agent_id="Alice",
        display_name="Alice",
        role_preset_id="docs_lead",
        strengths=["Runbooks", "Operator copy"],
        best_for=["docs contracts", "operator messaging"],
        avoid_for=["runtime hooks"],
        default_tools=["markdown_renderer", "issue_tracker"],
        allowed_actions=["draft_docs", "summarize"],
        safety_rules=["No secrets", "No runtime activation"],
        visibility=AgentProfileVisibility.PRIMARY,
    )
    bob = AgentProfile.create(
        agent_id="Bob",
        display_name="Bob",
        role_preset_id="implementation",
        strengths=["Backend slices", "Focused tests"],
        best_for=["small safe changes"],
        avoid_for=["broad refactors"],
        default_tools=["pytest", "apply_patch"],
        allowed_actions=["edit_code", "run_focused_tests"],
        safety_rules=["Stay in scope", "No destructive git"],
        reports_to="charlie",
        visibility="subagent_visible",
    )
    charlie = AgentProfile.create(
        agent_id="Charlie",
        display_name="Charlie",
        role_preset_id="orchestrator",
        strengths=["Scope control", "Verification"],
        best_for=["handoffs", "integration validation"],
        avoid_for=["parallel hotfile collisions"],
        default_tools=["git", "review"],
        allowed_actions=["coordinate", "verify"],
        safety_rules=["Require operator go for live steps", "No secret logging"],
        visibility="primary",
    )

    assert alice.agent_id == "alice"
    assert bob.role_preset_id == "implementation"
    assert bob.reports_to == "charlie"
    assert bob.visibility is AgentProfileVisibility.SUBAGENT_VISIBLE
    assert charlie.team_card_summary()["display_name"] == "Charlie"


@pytest.mark.parametrize(
    "field_name, kwargs",
    [
        ("agent_id", {"agent_id": "../alice"}),
        ("role_preset_id", {"role_preset_id": "roles/main"}),
        ("reports_to", {"reports_to": "charlie:root"}),
    ],
)
def test_invalid_path_like_values_are_rejected(field_name, kwargs):
    base = dict(
        agent_id="alice",
        display_name="Alice",
        role_preset_id="docs",
        strengths=["Docs"],
        best_for=["Contracts"],
        avoid_for=["Runtime"],
        default_tools=["markdown"],
        allowed_actions=["draft_docs"],
        safety_rules=["No secrets"],
    )
    base.update(kwargs)

    with pytest.raises(AgentProfileError, match="path-like|must not contain path-like"):
        AgentProfile.create(**base)


def test_overrides_can_extend_strengths_and_tools_but_cannot_remove_safety_rules():
    overrides = AgentProfileOverride.create(
        strengths_add=["Escalation handling"],
        default_tools_add=["git_status"],
        safety_rules_add=["Keep operator review visible"],
    )
    profile = AgentProfile.create(
        agent_id="charlie",
        display_name="Charlie",
        role_preset_id="orchestrator",
        strengths=["Scope control"],
        best_for=["handoffs"],
        avoid_for=["hotfile collisions"],
        default_tools=["review"],
        allowed_actions=["verify"],
        safety_rules=["No secrets"],
        overrides=overrides,
    )

    assert profile.strengths == ("Scope control", "Escalation handling")
    assert profile.default_tools == ("review", "git-status")
    assert profile.safety_rules == ("No secrets", "Keep operator review visible")

    with pytest.raises(AgentProfileError, match="cannot be removed"):
        AgentProfileOverride.create(remove_safety_rules=["No secrets"])


def test_hidden_worker_visibility_is_distinct_from_visible_subagent():
    hidden = AgentProfile.create(
        agent_id="worker-one",
        display_name="Worker One",
        role_preset_id="background_worker",
        strengths=["Silent processing"],
        best_for=["bounded preprocessing"],
        avoid_for=["operator-facing chat"],
        default_tools=["audit"],
        allowed_actions=["prepare"],
        safety_rules=["No visible runtime side effects"],
        hidden_worker_policy="must not appear in visible team cards",
        visibility="hidden_worker",
    )
    visible = AgentProfile.create(
        agent_id="worker-two",
        display_name="Worker Two",
        role_preset_id="subagent",
        strengths=["Visible support"],
        best_for=["narrow implementation slices"],
        avoid_for=["root orchestration"],
        default_tools=["pytest"],
        allowed_actions=["implement"],
        safety_rules=["Stay in scope"],
        visibility="subagent_visible",
    )

    assert hidden.visibility is AgentProfileVisibility.HIDDEN_WORKER
    assert visible.visibility is AgentProfileVisibility.SUBAGENT_VISIBLE
    assert hidden.visibility != visible.visibility


def test_summary_is_compact_and_leak_free():
    profile = AgentProfile.create(
        agent_id="bob",
        display_name="Bob",
        role_preset_id="implementation",
        persona_preset_id="backend_focus",
        strengths=["Backend slices"],
        best_for=["safe model changes"],
        avoid_for=["secrets"],
        default_tools=["pytest", "apply_patch"],
        allowed_actions=["edit_code", "run_focused_tests"],
        safety_rules=["No token=abc123", "No raw chat ids"],
        timer_policy="short focused runs",
        visibility="subagent_visible",
    )

    summary = profile.audit_summary()
    card = profile.team_card_summary()

    assert summary["agent_id"] == "bob"
    assert summary["default_tools"] == ("pytest", "apply-patch")
    assert "abc123" not in str(summary)
    assert "token=[redacted]" in summary["safety_rules"][0].lower()
    assert "persona_preset_id" not in card
    assert len(str(card)) < 500

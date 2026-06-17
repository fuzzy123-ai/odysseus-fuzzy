import pytest

from src.agent_profile import AgentProfile
from src.agent_team_card import AgentTeamCard, AgentTeamCardError, build_default_team_rules


def _profile(
    agent_id,
    *,
    display_name=None,
    visibility="subagent_visible",
    reports_to=None,
    strengths=("Tests",),
):
    return AgentProfile.create(
        agent_id=agent_id,
        display_name=display_name or agent_id.title(),
        role_preset_id=f"{agent_id}-preset",
        strengths=strengths,
        best_for=[f"{agent_id} work"],
        avoid_for=["unsafe live actions"],
        default_tools=["review"],
        allowed_actions=["summarize"],
        safety_rules=["No secrets"],
        visibility=visibility,
        reports_to=reports_to,
    )


def test_team_card_renders_visible_agents_for_main_agent_prompt():
    card = AgentTeamCard.create(
        profiles=[
            _profile("charlie", display_name="Charlie", visibility="primary"),
            _profile("alice", display_name="Alice", reports_to="charlie", strengths=("Docs", "Runbooks")),
            _profile("bob", display_name="Bob", reports_to="charlie", strengths=("Code", "Tests")),
        ],
        rules=build_default_team_rules(),
    )

    prompt = card.to_prompt_text()

    assert "Team:" in prompt
    assert "Alice: alice-preset; strong at Docs, Runbooks" in prompt
    assert "Bob: bob-preset; strong at Code, Tests" in prompt
    assert "Main agent orchestrates by default." in prompt
    assert "reports to charlie" in prompt


def test_hidden_workers_are_not_rendered_as_visible_agents():
    card = AgentTeamCard.create(
        profiles=[
            _profile("charlie", display_name="Charlie", visibility="primary"),
            _profile("scratch", display_name="Scratch Worker", visibility="hidden_worker"),
        ],
        rules=build_default_team_rules(),
    )

    prompt = card.to_prompt_text()
    summary = card.audit_summary()

    assert "- Scratch Worker:" not in prompt
    assert "Hidden workers: Scratch Worker appear only as bounded work steps." in prompt
    assert summary["visible_agent_ids"] == ("charlie",)
    assert summary["hidden_worker_ids"] == ("scratch",)


def test_team_card_requires_at_least_one_visible_agent():
    with pytest.raises(AgentTeamCardError, match="visible agent"):
        AgentTeamCard.create(
            profiles=[_profile("scratch", visibility="hidden_worker")],
            rules=build_default_team_rules(),
        )


def test_team_card_rejects_non_profile_inputs():
    with pytest.raises(AgentTeamCardError, match="AgentProfile"):
        AgentTeamCard.create(profiles=[object()], rules=())


def test_rules_are_deduplicated_and_bounded():
    card = AgentTeamCard.create(
        profiles=[_profile("charlie", visibility="primary")],
        rules=[" Main agent orchestrates by default. ", "main agent orchestrates by default."],
    )

    assert card.rules == ("Main agent orchestrates by default.",)

    with pytest.raises(AgentTeamCardError, match="rule count"):
        AgentTeamCard.create(
            profiles=[_profile("charlie", visibility="primary")],
            rules=[f"rule {idx}" for idx in range(8)],
        )

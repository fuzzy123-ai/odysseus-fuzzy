import json
from datetime import datetime, timedelta, timezone

from src.agent_automation_spec import AgentAutomationSpec
from src.agent_profile import AgentProfile
from src.agent_team_card import AgentTeamCard, build_default_team_rules
from src.agent_team_card_api import (
    build_agent_team_card_payload,
    build_default_agent_team_card_payload,
)


def _profile(
    agent_id,
    *,
    display_name=None,
    role_preset_id=None,
    visibility="subagent_visible",
    reports_to=None,
    strengths=("Tests",),
    hidden_worker_policy=None,
    safety_rules=("No secrets",),
):
    return AgentProfile.create(
        agent_id=agent_id,
        display_name=display_name or agent_id.title(),
        role_preset_id=role_preset_id or f"{agent_id}-preset",
        strengths=strengths,
        best_for=[f"{agent_id} work"],
        avoid_for=["unsafe live actions"],
        default_tools=["review"],
        allowed_actions=["summarize"],
        safety_rules=safety_rules,
        visibility=visibility,
        reports_to=reports_to,
        hidden_worker_policy=hidden_worker_policy,
    )


def test_default_payload_contains_alice_bob_charlie():
    payload = build_default_agent_team_card_payload().to_dict()

    ids = [agent["agent_id"] for agent in payload["team"]]

    assert ids == ["charlie", "alice", "bob"]
    assert payload["hidden_workers"] == []
    assert "Team:" in payload["prompt_text"]


def test_parent_and_visibility_info_are_preserved():
    payload = build_default_agent_team_card_payload().to_dict()
    by_id = {agent["agent_id"]: agent for agent in payload["team"]}

    assert by_id["charlie"]["visibility"] == "primary"
    assert by_id["alice"]["reports_to"] == "charlie"
    assert by_id["bob"]["reports_to"] == "charlie"


def test_hidden_workers_are_reduced_and_not_mixed_with_visible_agents():
    team_card = AgentTeamCard.create(
        profiles=[
            _profile("charlie", visibility="primary", role_preset_id="coordinator-release"),
            _profile(
                "worker",
                display_name="Worker",
                visibility="hidden_worker",
                role_preset_id="background-worker",
                hidden_worker_policy="token=abc123 must remain hidden",
                safety_rules=("No secrets", "No chat_id=55 persistence"),
            ),
        ],
        rules=build_default_team_rules(),
    )

    payload = build_agent_team_card_payload(team_card).to_dict()

    assert [agent["agent_id"] for agent in payload["team"]] == ["charlie"]
    assert [worker["agent_id"] for worker in payload["hidden_workers"]] == ["worker"]
    hidden_worker = payload["hidden_workers"][0]
    assert "strengths" not in hidden_worker
    assert "abc123" not in json.dumps(hidden_worker)
    assert "55" not in json.dumps(hidden_worker)


def test_obvious_secret_patterns_are_redacted_from_payload_fields():
    team_card = AgentTeamCard.create(
        profiles=[
            _profile(
                "charlie",
                display_name="Charlie token=abc123",
                visibility="primary",
                role_preset_id="coordinator-release",
                safety_rules=("No secret=topsecret logging",),
            ),
        ],
        rules=["Use password=hunter2 nowhere."],
    )

    payload = build_agent_team_card_payload(team_card).to_dict()
    encoded = json.dumps(payload)

    assert "abc123" not in encoded
    assert "topsecret" not in encoded
    assert "hunter2" not in encoded
    assert "[redacted]" in encoded


def test_payload_is_json_compatible():
    payload = build_default_agent_team_card_payload().to_dict()

    encoded = json.dumps(payload, sort_keys=True)

    assert '"team"' in encoded
    assert '"audit"' in encoded
    assert '"prompt_text"' in encoded


def test_visible_agent_timer_hint_can_be_attached_read_only():
    spec = AgentAutomationSpec.create(
        agent_id="alice",
        parent_agent_id="charlie",
        mode="interval",
        status="ready",
        interval_count=30,
        interval_unit="minutes",
        next_run_hint="next handoff in 30 minutes",
    )

    payload = build_default_agent_team_card_payload(automation_specs={"alice": spec}).to_dict()
    by_id = {agent["agent_id"]: agent for agent in payload["team"]}

    assert by_id["charlie"]["automation"] is None
    assert by_id["alice"]["automation"]["mode"] == "interval"
    assert by_id["alice"]["automation"]["interval_count"] == 30
    assert by_id["alice"]["automation"]["parent_agent_id"] == "charlie"


def test_hidden_worker_timer_hint_is_reduced_and_sanitized():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    spec = AgentAutomationSpec.create(
        agent_id="worker",
        parent_agent_id="charlie",
        mode="once",
        status="needs_review",
        scheduled_at=future,
        next_run_hint="chat_id=555 should never leak",
    )
    team_card = AgentTeamCard.create(
        profiles=[
            _profile("charlie", visibility="primary", role_preset_id="coordinator-release"),
            _profile("worker", visibility="hidden_worker", role_preset_id="background-worker"),
        ],
        rules=build_default_team_rules(),
    )

    payload = build_agent_team_card_payload(team_card, automation_specs={"worker": spec}).to_dict()
    worker = payload["hidden_workers"][0]
    encoded = json.dumps(worker)

    assert worker["automation"]["mode"] == "once"
    assert worker["automation"]["status"] == "needs_review"
    assert "555" not in encoded
    assert "[redacted]" in encoded

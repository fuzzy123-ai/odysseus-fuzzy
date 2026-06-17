from types import SimpleNamespace

import pytest

from src.agent_profile import AgentProfileOverride
from src.agent_profile_registry import (
    AgentProfileRegistry,
    AgentProfileRegistryError,
    AgentRolePreset,
    build_default_agent_profile_registry,
    default_agent_role_presets,
)


def test_default_registry_resolves_alice_bob_charlie_profiles():
    registry = build_default_agent_profile_registry()

    alice = registry.resolve_profile(agent_id="alice", role_preset_id="docs-runbook")
    bob = registry.resolve_profile(agent_id="bob", role_preset_id="code-test-audit", reports_to="charlie")
    charlie = registry.resolve_profile(agent_id="charlie", role_preset_id="coordinator-release")

    assert alice.display_name == "Alice"
    assert "Docs" in alice.strengths
    assert bob.reports_to == "charlie"
    assert "pytest" in bob.default_tools
    assert charlie.visibility.value == "primary"


def test_registry_builds_main_agent_team_card_from_assignments():
    registry = build_default_agent_profile_registry()

    card = registry.build_team_card(
        assignments=[
            {"agent_id": "charlie", "role_preset_id": "coordinator-release"},
            {"agent_id": "alice", "role_preset_id": "docs-runbook", "reports_to": "charlie"},
            {"agent_id": "bob", "role_preset_id": "code-test-audit", "reports_to": "charlie"},
        ]
    )

    prompt = card.to_prompt_text()

    assert "Alice: docs-runbook" in prompt
    assert "Bob: code-test-audit" in prompt
    assert "Charlie: coordinator-release" in prompt
    assert "Main agent orchestrates by default." in prompt


def test_crew_member_like_enabled_tools_extend_default_tools_without_personality_leak():
    registry = build_default_agent_profile_registry()
    crew = SimpleNamespace(
        name="Build Bob",
        enabled_tools='["pytest", "delegate", "search_chats"]',
        personality="token=should-not-leak",
    )

    profile = registry.resolve_profile(
        agent_id="bob",
        role_preset_id="code-test-audit",
        crew_member=crew,
    )
    summary = profile.audit_summary()

    assert profile.display_name == "Build Bob"
    assert "delegate" in profile.default_tools
    assert "search-chats" in profile.default_tools
    assert "should-not-leak" not in str(summary)
    assert "personality" not in str(summary).lower()


def test_explicit_overrides_extend_profile_without_removing_safety_rules():
    registry = build_default_agent_profile_registry()
    overrides = AgentProfileOverride.create(
        strengths_add=["Tool routing"],
        default_tools_add=["delegate"],
        safety_rules_add=["Keep parent chat informed"],
    )

    profile = registry.resolve_profile(
        agent_id="charlie",
        role_preset_id="coordinator-release",
        overrides=overrides,
    )

    assert "Tool routing" in profile.strengths
    assert "delegate" in profile.default_tools
    assert "No secret logging" in profile.safety_rules
    assert "Keep parent chat informed" in profile.safety_rules


def test_unknown_or_duplicate_presets_are_rejected():
    registry = build_default_agent_profile_registry()

    with pytest.raises(AgentProfileRegistryError, match="unknown role preset"):
        registry.resolve_profile(agent_id="eve", role_preset_id="missing")

    preset = default_agent_role_presets()[0]
    with pytest.raises(AgentProfileRegistryError, match="duplicate role preset"):
        AgentProfileRegistry.create(role_presets=[preset, preset])


def test_registry_requires_role_preset_instances():
    with pytest.raises(AgentProfileRegistryError, match="AgentRolePreset"):
        AgentProfileRegistry.create(role_presets=[object()])


def test_custom_role_preset_normalizes_through_agent_profile_model():
    preset = AgentRolePreset.create(
        role_preset_id="Research Lead",
        display_name="Research Alice",
        strengths=["Research"],
        best_for=["source mapping"],
        avoid_for=["secret handling"],
        default_tools=["search_chats"],
        allowed_actions=["summarize"],
        safety_rules=["No secrets"],
    )
    registry = AgentProfileRegistry.create(role_presets=[preset])

    profile = registry.resolve_profile(agent_id="researcher", role_preset_id="research-lead")

    assert preset.role_preset_id == "research-lead"
    assert profile.role_preset_id == "research-lead"
    assert profile.default_tools == ("search-chats",)

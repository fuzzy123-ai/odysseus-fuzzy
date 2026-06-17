"""Read-only agent profile resolver for presets, crew-like records, and overrides."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable, Mapping

from src.agent_profile import AgentProfile, AgentProfileOverride, AgentProfileVisibility
from src.agent_team_card import AgentTeamCard, build_default_team_rules


class AgentProfileRegistryError(ValueError):
    """Raised when profile registry inputs are invalid."""


@dataclass(frozen=True, slots=True)
class AgentRolePreset:
    role_preset_id: str
    display_name: str
    strengths: tuple[str, ...]
    best_for: tuple[str, ...]
    avoid_for: tuple[str, ...]
    default_tools: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    safety_rules: tuple[str, ...]
    timer_policy: str | None = None
    hidden_worker_policy: str | None = None
    visibility: AgentProfileVisibility = AgentProfileVisibility.SUBAGENT_VISIBLE

    @classmethod
    def create(
        cls,
        *,
        role_preset_id: str,
        display_name: str,
        strengths: Iterable[str],
        best_for: Iterable[str],
        avoid_for: Iterable[str],
        default_tools: Iterable[str],
        allowed_actions: Iterable[str],
        safety_rules: Iterable[str],
        timer_policy: str | None = None,
        hidden_worker_policy: str | None = None,
        visibility: AgentProfileVisibility | str = AgentProfileVisibility.SUBAGENT_VISIBLE,
    ) -> "AgentRolePreset":
        profile = AgentProfile.create(
            agent_id=f"{role_preset_id}-preset",
            display_name=display_name,
            role_preset_id=role_preset_id,
            strengths=strengths,
            best_for=best_for,
            avoid_for=avoid_for,
            default_tools=default_tools,
            allowed_actions=allowed_actions,
            safety_rules=safety_rules,
            timer_policy=timer_policy,
            hidden_worker_policy=hidden_worker_policy,
            visibility=visibility,
        )
        return cls(
            role_preset_id=profile.role_preset_id,
            display_name=profile.display_name,
            strengths=profile.strengths,
            best_for=profile.best_for,
            avoid_for=profile.avoid_for,
            default_tools=profile.default_tools,
            allowed_actions=profile.allowed_actions,
            safety_rules=profile.safety_rules,
            timer_policy=profile.timer_policy,
            hidden_worker_policy=profile.hidden_worker_policy,
            visibility=profile.visibility,
        )


@dataclass(frozen=True, slots=True)
class AgentProfileRegistry:
    role_presets: Mapping[str, AgentRolePreset]

    @classmethod
    def create(cls, *, role_presets: Iterable[AgentRolePreset]) -> "AgentProfileRegistry":
        indexed: dict[str, AgentRolePreset] = {}
        for preset in role_presets:
            if not isinstance(preset, AgentRolePreset):
                raise AgentProfileRegistryError("role_presets must contain AgentRolePreset instances")
            if preset.role_preset_id in indexed:
                raise AgentProfileRegistryError(f"duplicate role preset: {preset.role_preset_id}")
            indexed[preset.role_preset_id] = preset
        if not indexed:
            raise AgentProfileRegistryError("at least one role preset is required")
        return cls(role_presets=indexed)

    def resolve_profile(
        self,
        *,
        agent_id: str,
        role_preset_id: str,
        crew_member: Any | None = None,
        persona_preset_id: str | None = None,
        reports_to: str | None = None,
        visibility: AgentProfileVisibility | str | None = None,
        overrides: AgentProfileOverride | None = None,
    ) -> AgentProfile:
        preset = self.role_presets.get(str(role_preset_id).strip().lower().replace("_", "-"))
        if preset is None:
            raise AgentProfileRegistryError(f"unknown role preset: {role_preset_id}")

        crew_name = _get_field(crew_member, "name")
        crew_tools = _parse_enabled_tools(_get_field(crew_member, "enabled_tools"))
        combined_overrides = _combine_overrides(
            AgentProfileOverride.create(default_tools_add=crew_tools),
            overrides,
        )

        return AgentProfile.create(
            agent_id=agent_id,
            display_name=crew_name or preset.display_name,
            role_preset_id=preset.role_preset_id,
            persona_preset_id=persona_preset_id,
            strengths=preset.strengths,
            best_for=preset.best_for,
            avoid_for=preset.avoid_for,
            default_tools=preset.default_tools,
            allowed_actions=preset.allowed_actions,
            safety_rules=preset.safety_rules,
            timer_policy=preset.timer_policy,
            hidden_worker_policy=preset.hidden_worker_policy,
            reports_to=reports_to,
            visibility=visibility or preset.visibility,
            overrides=combined_overrides,
        )

    def build_team_card(
        self,
        *,
        assignments: Iterable[Mapping[str, Any]],
        rules: Iterable[str] | None = None,
    ) -> AgentTeamCard:
        profiles = [
            self.resolve_profile(
                agent_id=str(assignment["agent_id"]),
                role_preset_id=str(assignment["role_preset_id"]),
                crew_member=assignment.get("crew_member"),
                persona_preset_id=assignment.get("persona_preset_id"),
                reports_to=assignment.get("reports_to"),
                visibility=assignment.get("visibility"),
                overrides=assignment.get("overrides"),
            )
            for assignment in assignments
        ]
        return AgentTeamCard.create(
            profiles=profiles,
            rules=build_default_team_rules() if rules is None else rules,
        )


def default_agent_role_presets() -> tuple[AgentRolePreset, ...]:
    return (
        AgentRolePreset.create(
            role_preset_id="docs-runbook",
            display_name="Alice",
            strengths=("Docs", "Runbooks", "Operator language"),
            best_for=("operator-facing docs", "Go or No-Go language"),
            avoid_for=("runtime hooks",),
            default_tools=("markdown", "review"),
            allowed_actions=("draft_docs", "summarize"),
            safety_rules=("No secrets", "No runtime activation"),
        ),
        AgentRolePreset.create(
            role_preset_id="code-test-audit",
            display_name="Bob",
            strengths=("Code", "Tests", "Audit"),
            best_for=("focused implementation slices", "regression tests"),
            avoid_for=("broad architecture decisions",),
            default_tools=("pytest", "review"),
            allowed_actions=("edit_scoped_files", "run_focused_tests"),
            safety_rules=("Stay in scope", "No destructive git"),
        ),
        AgentRolePreset.create(
            role_preset_id="coordinator-release",
            display_name="Charlie",
            strengths=("Scope", "Git", "Release"),
            best_for=("integration", "handoff review"),
            avoid_for=("unapproved live actions",),
            default_tools=("git_status", "pytest", "review"),
            allowed_actions=("coordinate", "verify", "push_after_green_tests"),
            safety_rules=("No secret logging", "Require operator go for live steps"),
            visibility=AgentProfileVisibility.PRIMARY,
        ),
    )


def build_default_agent_profile_registry() -> AgentProfileRegistry:
    return AgentProfileRegistry.create(role_presets=default_agent_role_presets())


def _get_field(source: Any | None, name: str) -> Any | None:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _parse_enabled_tools(value: Any | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped == "all":
            return ()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return (stripped,)
        return _parse_enabled_tools(parsed)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value if str(item or "").strip())
    return (str(value),)


def _combine_overrides(
    base: AgentProfileOverride,
    extra: AgentProfileOverride | None,
) -> AgentProfileOverride:
    if extra is None:
        return base
    if not isinstance(extra, AgentProfileOverride):
        raise AgentProfileRegistryError("overrides must be an AgentProfileOverride")
    return AgentProfileOverride.create(
        strengths_add=base.strengths_add + extra.strengths_add,
        best_for_add=base.best_for_add + extra.best_for_add,
        avoid_for_add=base.avoid_for_add + extra.avoid_for_add,
        default_tools_add=base.default_tools_add + extra.default_tools_add,
        allowed_actions_add=base.allowed_actions_add + extra.allowed_actions_add,
        safety_rules_add=base.safety_rules_add + extra.safety_rules_add,
    )

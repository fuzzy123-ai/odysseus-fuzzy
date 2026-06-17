"""Compact team-card rendering for main-agent context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.agent_profile import AgentProfile, AgentProfileVisibility


_MAX_RULES = 6
_MAX_VISIBLE_AGENTS = 12
_MAX_HIDDEN_WORKERS = 12


class AgentTeamCardError(ValueError):
    """Raised when a team-card input cannot be rendered safely."""


@dataclass(frozen=True, slots=True)
class AgentTeamCard:
    visible_agents: tuple[AgentProfile, ...]
    hidden_workers: tuple[AgentProfile, ...]
    rules: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        profiles: Iterable[AgentProfile],
        rules: Iterable[str] = (),
    ) -> "AgentTeamCard":
        visible: list[AgentProfile] = []
        hidden: list[AgentProfile] = []
        for profile in profiles:
            if not isinstance(profile, AgentProfile):
                raise AgentTeamCardError("profiles must contain AgentProfile instances")
            if profile.visibility is AgentProfileVisibility.HIDDEN_WORKER:
                hidden.append(profile)
                continue
            visible.append(profile)

        if not visible:
            raise AgentTeamCardError("at least one visible agent profile is required")
        if len(visible) > _MAX_VISIBLE_AGENTS:
            raise AgentTeamCardError(f"visible agent count exceeds {_MAX_VISIBLE_AGENTS}")
        if len(hidden) > _MAX_HIDDEN_WORKERS:
            raise AgentTeamCardError(f"hidden worker count exceeds {_MAX_HIDDEN_WORKERS}")

        return cls(
            visible_agents=tuple(sorted(visible, key=lambda item: (item.reports_to or "", item.agent_id))),
            hidden_workers=tuple(sorted(hidden, key=lambda item: item.agent_id)),
            rules=_normalize_rules(rules),
        )

    def to_prompt_text(self) -> str:
        lines = ["Team:"]
        for profile in self.visible_agents:
            lines.append(f"- {profile.display_name}: {_profile_prompt_fragment(profile)}")
        if self.hidden_workers:
            worker_names = ", ".join(worker.display_name for worker in self.hidden_workers)
            lines.append(f"Hidden workers: {worker_names} appear only as bounded work steps.")
        if self.rules:
            lines.append("")
            lines.append("Rules:")
            lines.extend(f"- {rule}" for rule in self.rules)
        return "\n".join(lines)

    def audit_summary(self) -> dict[str, Any]:
        return {
            "visible_agent_ids": tuple(profile.agent_id for profile in self.visible_agents),
            "hidden_worker_ids": tuple(profile.agent_id for profile in self.hidden_workers),
            "visible_count": len(self.visible_agents),
            "hidden_worker_count": len(self.hidden_workers),
            "rule_count": len(self.rules),
            "reports_to": {
                profile.agent_id: profile.reports_to
                for profile in self.visible_agents
                if profile.reports_to is not None
            },
        }


def build_default_team_rules() -> tuple[str, ...]:
    return (
        "Main agent orchestrates by default.",
        "User can override with simple agent commands.",
        "Visible subagents report to their parent chat.",
        "Hidden workers appear only as work steps.",
        "Overrides can extend behavior, but safety rules win.",
    )


def _profile_prompt_fragment(profile: AgentProfile) -> str:
    strengths = ", ".join(profile.strengths[:3]) or profile.role_preset_id
    best_for = "; ".join(profile.best_for[:2])
    avoid_for = "; ".join(profile.avoid_for[:1])
    parts = [f"{profile.role_preset_id}; strong at {strengths}"]
    if best_for:
        parts.append(f"best for {best_for}")
    if avoid_for:
        parts.append(f"avoid {avoid_for}")
    if profile.reports_to:
        parts.append(f"reports to {profile.reports_to}")
    return "; ".join(parts) + "."


def _normalize_rules(rules: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for rule in rules:
        text = " ".join(str(rule or "").split())
        if not text:
            continue
        if len(text) > 160:
            raise AgentTeamCardError("rule exceeds max length 160")
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
        if len(normalized) > _MAX_RULES:
            raise AgentTeamCardError(f"rule count exceeds {_MAX_RULES}")
    return tuple(normalized)

"""Read-only API payload helpers for agent team cards."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from src.agent_automation_spec import AgentAutomationSpec
from src.agent_profile import AgentProfile
from src.agent_profile_registry import build_default_agent_profile_registry
from src.agent_team_card import AgentTeamCard


_SECRET_RE = re.compile(r"(?i)\b(token|password|api[_-]?key|secret|chat[_-]?id)\s*[:=]\s*\S+")


@dataclass(frozen=True, slots=True)
class AgentTeamCardApiPayload:
    team: tuple[dict[str, Any], ...]
    hidden_workers: tuple[dict[str, Any], ...]
    rules: tuple[str, ...]
    audit: dict[str, Any]
    prompt_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": [dict(item) for item in self.team],
            "hidden_workers": [dict(item) for item in self.hidden_workers],
            "rules": list(self.rules),
            "audit": dict(self.audit),
            "prompt_text": self.prompt_text,
        }


def build_default_agent_team_card_payload(
    automation_specs: Mapping[str, AgentAutomationSpec] | None = None,
) -> AgentTeamCardApiPayload:
    registry = build_default_agent_profile_registry()
    team_card = registry.build_team_card(
        assignments=[
            {"agent_id": "charlie", "role_preset_id": "coordinator-release"},
            {"agent_id": "alice", "role_preset_id": "docs-runbook", "reports_to": "charlie"},
            {"agent_id": "bob", "role_preset_id": "code-test-audit", "reports_to": "charlie"},
        ]
    )
    return build_agent_team_card_payload(team_card, automation_specs=automation_specs)


def build_agent_team_card_payload(
    team_card: AgentTeamCard,
    *,
    automation_specs: Mapping[str, AgentAutomationSpec] | None = None,
) -> AgentTeamCardApiPayload:
    if not isinstance(team_card, AgentTeamCard):
        raise TypeError("team_card must be an AgentTeamCard")
    return AgentTeamCardApiPayload(
        team=tuple(_visible_profile_payload(profile, automation_specs) for profile in team_card.visible_agents),
        hidden_workers=tuple(_hidden_worker_payload(profile, automation_specs) for profile in team_card.hidden_workers),
        rules=tuple(_sanitize_text(rule) for rule in team_card.rules),
        audit=_sanitize_value(team_card.audit_summary()),
        prompt_text=_sanitize_text(team_card.to_prompt_text()),
    )


def _visible_profile_payload(
    profile: AgentProfile,
    automation_specs: Mapping[str, AgentAutomationSpec] | None,
) -> dict[str, Any]:
    return {
        "agent_id": profile.agent_id,
        "display_name": _sanitize_text(profile.display_name),
        "role_preset_id": profile.role_preset_id,
        "strengths": [_sanitize_text(item) for item in profile.strengths],
        "best_for": [_sanitize_text(item) for item in profile.best_for],
        "avoid_for": [_sanitize_text(item) for item in profile.avoid_for],
        "reports_to": profile.reports_to,
        "visibility": profile.visibility.value,
        "timer_policy": _sanitize_optional_text(profile.timer_policy),
        "automation": _automation_hint_payload(profile.agent_id, automation_specs),
        "safety_summary": [_sanitize_text(item) for item in profile.safety_rules[:3]],
    }


def _hidden_worker_payload(
    profile: AgentProfile,
    automation_specs: Mapping[str, AgentAutomationSpec] | None,
) -> dict[str, Any]:
    return {
        "agent_id": profile.agent_id,
        "display_name": _sanitize_text(profile.display_name),
        "role_preset_id": profile.role_preset_id,
        "reports_to": profile.reports_to,
        "visibility": profile.visibility.value,
        "hidden_worker_policy": _sanitize_optional_text(profile.hidden_worker_policy),
        "automation": _automation_hint_payload(profile.agent_id, automation_specs),
        "safety_summary": [_sanitize_text(item) for item in profile.safety_rules[:2]],
    }


def _automation_hint_payload(
    agent_id: str,
    automation_specs: Mapping[str, AgentAutomationSpec] | None,
) -> dict[str, Any] | None:
    if not automation_specs:
        return None
    spec = automation_specs.get(agent_id)
    if spec is None:
        return None
    if not isinstance(spec, AgentAutomationSpec):
        raise TypeError("automation_specs values must be AgentAutomationSpec instances")
    return _sanitize_value(spec.to_overlay_payload())


def _sanitize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _sanitize_text(value)


def _sanitize_text(value: str) -> str:
    return _SECRET_RE.sub(r"\1=[redacted]", value)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    return value

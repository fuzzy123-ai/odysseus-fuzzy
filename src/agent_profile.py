"""Runtime-agnostic agent profile normalization for team cards and audit views."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable


_MAX_ID_LENGTH = 80
_MAX_NAME_LENGTH = 80
_MAX_TEXT_LENGTH = 140
_MAX_LIST_ITEMS = 16
_INVALID_PATH_BITS = ("..", "/", "\\", ":")
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_SECRET_RE = re.compile(r"(?i)\b(token|password|api[_-]?key|secret)\s*[:=]\s*\S+")


class AgentProfileError(ValueError):
    """Raised when an agent profile input cannot be normalized safely."""


class AgentProfileVisibility(StrEnum):
    PRIMARY = "primary"
    SUBAGENT_VISIBLE = "subagent_visible"
    HIDDEN_WORKER = "hidden_worker"


@dataclass(frozen=True, slots=True)
class AgentProfileOverride:
    strengths_add: tuple[str, ...] = ()
    best_for_add: tuple[str, ...] = ()
    avoid_for_add: tuple[str, ...] = ()
    default_tools_add: tuple[str, ...] = ()
    allowed_actions_add: tuple[str, ...] = ()
    safety_rules_add: tuple[str, ...] = ()
    remove_safety_rules: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        strengths_add: Iterable[Any] = (),
        best_for_add: Iterable[Any] = (),
        avoid_for_add: Iterable[Any] = (),
        default_tools_add: Iterable[Any] = (),
        allowed_actions_add: Iterable[Any] = (),
        safety_rules_add: Iterable[Any] = (),
        remove_safety_rules: Iterable[Any] = (),
    ) -> "AgentProfileOverride":
        removal_attempt = _normalize_text_list(remove_safety_rules, field_name="remove_safety_rule")
        if removal_attempt:
            raise AgentProfileError("safety_rules cannot be removed by overrides")
        return cls(
            strengths_add=_normalize_text_list(strengths_add, field_name="strength"),
            best_for_add=_normalize_text_list(best_for_add, field_name="best_for"),
            avoid_for_add=_normalize_text_list(avoid_for_add, field_name="avoid_for"),
            default_tools_add=_normalize_slug_list(default_tools_add, field_name="default_tool"),
            allowed_actions_add=_normalize_slug_list(allowed_actions_add, field_name="allowed_action"),
            safety_rules_add=_normalize_text_list(safety_rules_add, field_name="safety_rule"),
            remove_safety_rules=(),
        )


@dataclass(frozen=True, slots=True)
class AgentProfile:
    agent_id: str
    display_name: str
    role_preset_id: str
    persona_preset_id: str | None
    strengths: tuple[str, ...]
    best_for: tuple[str, ...]
    avoid_for: tuple[str, ...]
    default_tools: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    safety_rules: tuple[str, ...]
    timer_policy: str | None
    hidden_worker_policy: str | None
    reports_to: str | None
    visibility: AgentProfileVisibility
    overrides: AgentProfileOverride

    @classmethod
    def create(
        cls,
        *,
        agent_id: str,
        display_name: str,
        role_preset_id: str,
        persona_preset_id: str | None = None,
        strengths: Iterable[Any] = (),
        best_for: Iterable[Any] = (),
        avoid_for: Iterable[Any] = (),
        default_tools: Iterable[Any] = (),
        allowed_actions: Iterable[Any] = (),
        safety_rules: Iterable[Any] = (),
        timer_policy: str | None = None,
        hidden_worker_policy: str | None = None,
        reports_to: str | None = None,
        visibility: AgentProfileVisibility | str = AgentProfileVisibility.PRIMARY,
        overrides: AgentProfileOverride | None = None,
    ) -> "AgentProfile":
        normalized_visibility = (
            visibility if isinstance(visibility, AgentProfileVisibility) else AgentProfileVisibility(str(visibility))
        )
        normalized_overrides = overrides if isinstance(overrides, AgentProfileOverride) else AgentProfileOverride()
        normalized_safety = _normalize_text_list(safety_rules, field_name="safety_rule", allow_empty=False)

        return cls(
            agent_id=_normalize_slug(agent_id, field_name="agent_id"),
            display_name=_normalize_display_name(display_name),
            role_preset_id=_normalize_slug(role_preset_id, field_name="role_preset_id"),
            persona_preset_id=(
                _normalize_slug(persona_preset_id, field_name="persona_preset_id")
                if persona_preset_id is not None and str(persona_preset_id).strip()
                else None
            ),
            strengths=_merge_text_values(
                _normalize_text_list(strengths, field_name="strength"),
                normalized_overrides.strengths_add,
            ),
            best_for=_merge_text_values(
                _normalize_text_list(best_for, field_name="best_for"),
                normalized_overrides.best_for_add,
            ),
            avoid_for=_merge_text_values(
                _normalize_text_list(avoid_for, field_name="avoid_for"),
                normalized_overrides.avoid_for_add,
            ),
            default_tools=_merge_slug_values(
                _normalize_slug_list(default_tools, field_name="default_tool"),
                normalized_overrides.default_tools_add,
            ),
            allowed_actions=_merge_slug_values(
                _normalize_slug_list(allowed_actions, field_name="allowed_action"),
                normalized_overrides.allowed_actions_add,
            ),
            safety_rules=_merge_text_values(normalized_safety, normalized_overrides.safety_rules_add),
            timer_policy=_normalize_optional_text(timer_policy, field_name="timer_policy"),
            hidden_worker_policy=_normalize_optional_text(hidden_worker_policy, field_name="hidden_worker_policy"),
            reports_to=_normalize_optional_slug(reports_to, field_name="reports_to"),
            visibility=normalized_visibility,
            overrides=normalized_overrides,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "role_preset_id": self.role_preset_id,
            "persona_preset_id": self.persona_preset_id,
            "visibility": self.visibility.value,
            "reports_to": self.reports_to,
            "strength_count": len(self.strengths),
            "best_for_count": len(self.best_for),
            "avoid_for_count": len(self.avoid_for),
            "default_tools": self.default_tools,
            "allowed_actions": self.allowed_actions,
            "safety_rules": tuple(_sanitize_summary_text(rule) for rule in self.safety_rules),
            "timer_policy": _sanitize_summary_text(self.timer_policy) if self.timer_policy else None,
            "hidden_worker_policy": (
                _sanitize_summary_text(self.hidden_worker_policy) if self.hidden_worker_policy else None
            ),
            "override_flags": {
                "strengths_extended": bool(self.overrides.strengths_add),
                "tools_extended": bool(self.overrides.default_tools_add),
                "safety_rules_extended": bool(self.overrides.safety_rules_add),
            },
        }

    def team_card_summary(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "role_preset_id": self.role_preset_id,
            "visibility": self.visibility.value,
            "reports_to": self.reports_to,
            "best_for": tuple(_sanitize_summary_text(item) for item in self.best_for[:3]),
            "avoid_for": tuple(_sanitize_summary_text(item) for item in self.avoid_for[:3]),
            "default_tools": self.default_tools[:4],
            "safety_rules": tuple(_sanitize_summary_text(rule) for rule in self.safety_rules[:3]),
        }


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise AgentProfileError(f"{field_name} must not be empty")
    if len(raw) > _MAX_ID_LENGTH:
        raise AgentProfileError(f"{field_name} exceeds max length {_MAX_ID_LENGTH}")
    if any(token in raw for token in _INVALID_PATH_BITS):
        raise AgentProfileError(f"{field_name} must not contain path-like segments")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise AgentProfileError(f"{field_name} must contain slug characters")
    return normalized


def _normalize_optional_slug(value: Any, *, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _normalize_slug(value, field_name=field_name)


def _normalize_display_name(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise AgentProfileError("display_name must not be empty")
    if len(text) > _MAX_NAME_LENGTH:
        raise AgentProfileError(f"display_name exceeds max length {_MAX_NAME_LENGTH}")
    if any(token in text for token in _INVALID_PATH_BITS):
        raise AgentProfileError("display_name must not contain path-like segments")
    return text


def _normalize_optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _normalize_text(value, field_name=field_name)


def _normalize_text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise AgentProfileError(f"{field_name} must not be empty")
    if len(text) > _MAX_TEXT_LENGTH:
        raise AgentProfileError(f"{field_name} exceeds max length {_MAX_TEXT_LENGTH}")
    if any(token in text for token in _INVALID_PATH_BITS):
        raise AgentProfileError(f"{field_name} must not contain path-like segments")
    return text


def _normalize_text_list(
    values: Iterable[Any],
    *,
    field_name: str,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _normalize_text(value, field_name=field_name)
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) > _MAX_LIST_ITEMS:
            raise AgentProfileError(f"{field_name} exceeds max item count {_MAX_LIST_ITEMS}")
    if not allow_empty and not normalized:
        raise AgentProfileError(f"{field_name} must not be empty")
    return tuple(normalized)


def _normalize_slug_list(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _normalize_slug(value, field_name=field_name)
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
        if len(normalized) > _MAX_LIST_ITEMS:
            raise AgentProfileError(f"{field_name} exceeds max item count {_MAX_LIST_ITEMS}")
    return tuple(normalized)


def _merge_text_values(base: tuple[str, ...], extra: tuple[str, ...]) -> tuple[str, ...]:
    merged = list(base)
    seen = {item.casefold() for item in base}
    for item in extra:
        key = item.casefold()
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return tuple(merged)


def _merge_slug_values(base: tuple[str, ...], extra: tuple[str, ...]) -> tuple[str, ...]:
    merged = list(base)
    seen = set(base)
    for item in extra:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return tuple(merged)


def _sanitize_summary_text(value: str) -> str:
    return _SECRET_RE.sub(r"\1=[redacted]", value)

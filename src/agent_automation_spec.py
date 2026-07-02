"""Runtime-agnostic automation spec model for agent overlay payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any


_MAX_ID_LENGTH = 80
_MAX_TEXT_LENGTH = 160
_MAX_INTERVAL_COUNT = 365
_INVALID_PATH_BITS = ("..", "/", "\\", ":")
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_SECRET_RE = re.compile(r"(?i)\b(token|password|api[_-]?key|secret|chat[_-]?id)\s*[:=]\s*\S+")


class AgentAutomationSpecError(ValueError):
    """Raised when an automation spec cannot be normalized safely."""


class AgentAutomationMode(StrEnum):
    MANUAL = "manual"
    WATCH = "watch"
    INTERVAL = "interval"
    ONCE = "once"
    RECURRING = "recurring"


class AgentAutomationStatus(StrEnum):
    INACTIVE = "inactive"
    READY = "ready"
    PAUSED = "paused"
    NEEDS_REVIEW = "needs_review"


class AgentAutomationUnit(StrEnum):
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"


@dataclass(frozen=True, slots=True)
class AgentAutomationSpec:
    agent_id: str
    parent_agent_id: str | None
    mode: AgentAutomationMode
    status: AgentAutomationStatus
    interval_count: int | None = None
    interval_unit: AgentAutomationUnit | None = None
    scheduled_at: str | None = None
    recurrence: str | None = None
    timezone: str | None = None
    next_run_hint: str | None = None
    last_handoff: str | None = None
    change_effective: str | None = None

    @classmethod
    def create(
        cls,
        *,
        agent_id: str,
        parent_agent_id: str | None = None,
        mode: AgentAutomationMode | str,
        status: AgentAutomationStatus | str = AgentAutomationStatus.INACTIVE,
        interval_count: int | None = None,
        interval_unit: AgentAutomationUnit | str | None = None,
        scheduled_at: str | datetime | None = None,
        recurrence: str | None = None,
        timezone: str | None = None,
        next_run_hint: str | None = None,
        last_handoff: str | None = None,
        change_effective: str | None = None,
    ) -> "AgentAutomationSpec":
        normalized_mode = mode if isinstance(mode, AgentAutomationMode) else AgentAutomationMode(str(mode))
        normalized_status = status if isinstance(status, AgentAutomationStatus) else AgentAutomationStatus(str(status))
        normalized_unit = _normalize_interval_unit(interval_unit)
        normalized_interval = _normalize_interval_count(interval_count)
        normalized_scheduled_at = _normalize_scheduled_at(scheduled_at)
        normalized_recurrence = _normalize_optional_text(recurrence, field_name="recurrence")

        _validate_mode_requirements(
            mode=normalized_mode,
            interval_count=normalized_interval,
            interval_unit=normalized_unit,
            scheduled_at=normalized_scheduled_at,
            recurrence=normalized_recurrence,
        )

        return cls(
            agent_id=_normalize_slug(agent_id, field_name="agent_id"),
            parent_agent_id=_normalize_optional_slug(parent_agent_id, field_name="parent_agent_id"),
            mode=normalized_mode,
            status=normalized_status,
            interval_count=normalized_interval,
            interval_unit=normalized_unit,
            scheduled_at=normalized_scheduled_at,
            recurrence=normalized_recurrence,
            timezone=_normalize_optional_text(timezone, field_name="timezone"),
            next_run_hint=_normalize_optional_text(next_run_hint, field_name="next_run_hint"),
            last_handoff=_normalize_optional_text(last_handoff, field_name="last_handoff"),
            change_effective=_normalize_optional_text(change_effective, field_name="change_effective"),
        )

    def to_overlay_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "mode": self.mode.value,
            "status": self.status.value,
            "interval_count": self.interval_count,
            "interval_unit": self.interval_unit.value if self.interval_unit else None,
            "scheduled_at": self.scheduled_at,
            "recurrence": self.recurrence,
            "timezone": self.timezone,
            "next_run_hint": _sanitize_text(self.next_run_hint) if self.next_run_hint else None,
            "last_handoff": _sanitize_text(self.last_handoff) if self.last_handoff else None,
            "change_effective": _sanitize_text(self.change_effective) if self.change_effective else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.to_overlay_payload()

    def to_live_scheduler_cron(self) -> str | None:
        """Map safe interval specs to the existing live scheduler cron shape."""
        return interval_spec_to_cron(self)


def interval_spec_to_cron(spec: AgentAutomationSpec) -> str | None:
    """Return the live scheduler cron expression for safe interval specs.

    The task scheduler already supports cron. We keep that as the canonical
    live representation and only map intervals with deterministic cron
    semantics. Anything ambiguous returns ``None`` so callers can request review
    rather than silently scheduling the wrong cadence.
    """
    if spec.mode is not AgentAutomationMode.INTERVAL:
        return None
    count = spec.interval_count
    unit = spec.interval_unit
    if count is None or unit is None:
        return None
    if unit is AgentAutomationUnit.MINUTES and 60 % count == 0:
        return f"*/{count} * * * *"
    if unit is AgentAutomationUnit.HOURS and 24 % count == 0:
        return f"0 */{count} * * *"
    if unit is AgentAutomationUnit.DAYS and 31 % count == 0:
        return f"0 0 */{count} * *"
    return None


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise AgentAutomationSpecError(f"{field_name} must not be empty")
    if len(raw) > _MAX_ID_LENGTH:
        raise AgentAutomationSpecError(f"{field_name} exceeds max length {_MAX_ID_LENGTH}")
    if any(token in raw for token in _INVALID_PATH_BITS):
        raise AgentAutomationSpecError(f"{field_name} must not contain path-like segments")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise AgentAutomationSpecError(f"{field_name} must contain slug characters")
    return normalized


def _normalize_optional_slug(value: Any, *, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _normalize_slug(value, field_name=field_name)


def _normalize_optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    text = " ".join(str(value or "").split())
    if len(text) > _MAX_TEXT_LENGTH:
        raise AgentAutomationSpecError(f"{field_name} exceeds max length {_MAX_TEXT_LENGTH}")
    return text


def _normalize_interval_count(value: Any) -> int | None:
    if value is None:
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise AgentAutomationSpecError("interval_count must be an integer") from None
    if count <= 0:
        raise AgentAutomationSpecError("interval_count must be positive")
    if count > _MAX_INTERVAL_COUNT:
        raise AgentAutomationSpecError(f"interval_count exceeds max {_MAX_INTERVAL_COUNT}")
    return count


def _normalize_interval_unit(value: Any) -> AgentAutomationUnit | None:
    if value is None:
        return None
    if isinstance(value, AgentAutomationUnit):
        return value
    try:
        return AgentAutomationUnit(str(value))
    except ValueError as exc:
        raise AgentAutomationSpecError("interval_unit must be one of minutes, hours, days") from exc


def _normalize_scheduled_at(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AgentAutomationSpecError("scheduled_at must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed <= datetime.now(timezone.utc):
        raise AgentAutomationSpecError("scheduled_at must be in the future")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_mode_requirements(
    *,
    mode: AgentAutomationMode,
    interval_count: int | None,
    interval_unit: AgentAutomationUnit | None,
    scheduled_at: str | None,
    recurrence: str | None,
) -> None:
    if mode is AgentAutomationMode.INTERVAL:
        if interval_count is None or interval_unit is None:
            raise AgentAutomationSpecError("interval mode requires interval_count and interval_unit")
        return
    if mode is AgentAutomationMode.ONCE:
        if scheduled_at is None:
            raise AgentAutomationSpecError("once mode requires scheduled_at")
        return
    if mode is AgentAutomationMode.RECURRING:
        if recurrence is None:
            raise AgentAutomationSpecError("recurring mode requires recurrence")
        return
    if mode in {AgentAutomationMode.MANUAL, AgentAutomationMode.WATCH}:
        return


def _sanitize_text(value: str) -> str:
    return _SECRET_RE.sub(r"\1=[redacted]", value)

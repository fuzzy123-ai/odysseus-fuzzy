"""Rule engine and alert state model for System Health Checker snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping

from .health_model import CollectorStatus, HealthModelError, HealthState


class AlertState(StrEnum):
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    RECOVERED = "recovered"


@dataclass(frozen=True, slots=True)
class AlertRule:
    rule_id: str
    collector_kind: str
    trigger_state: HealthState
    severity: HealthState
    message: str
    cooldown_seconds: int

    @classmethod
    def create(
        cls,
        *,
        rule_id: Any,
        collector_kind: Any,
        trigger_state: HealthState | str,
        severity: HealthState | str,
        message: Any,
        cooldown_seconds: Any = 900,
    ) -> "AlertRule":
        cooldown = int(cooldown_seconds)
        if cooldown < 0:
            raise HealthModelError("cooldown_seconds must not be negative")
        return cls(
            rule_id=_text(rule_id, field_name="rule_id"),
            collector_kind=_text(collector_kind, field_name="collector_kind"),
            trigger_state=_state(trigger_state),
            severity=_state(severity),
            message=_text(message, field_name="message"),
            cooldown_seconds=cooldown,
        )


@dataclass(frozen=True, slots=True)
class AlertEvent:
    rule_id: str
    state: AlertState
    severity: HealthState
    message: str
    collector_summary: str
    next_allowed_at: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "state": self.state.value,
            "severity": self.severity.value,
            "message": self.message,
            "collector_summary": self.collector_summary,
            "next_allowed_at": self.next_allowed_at,
        }


def evaluate_alerts(
    *,
    collectors: Iterable[CollectorStatus],
    rules: Iterable[AlertRule],
    now: int,
    previous_next_allowed_at: Mapping[str, int] | None = None,
) -> tuple[AlertEvent, ...]:
    """Evaluate alert rules with cooldown and recovery events."""

    previous = dict(previous_next_allowed_at or {})
    events: list[AlertEvent] = []
    collector_by_kind = {collector.kind.value: collector for collector in collectors}
    for rule in rules:
        collector = collector_by_kind.get(rule.collector_kind)
        if collector is None:
            continue
        threshold_hit = _state_rank(collector.state) >= _state_rank(rule.trigger_state)
        previous_until = int(previous.get(rule.rule_id, 0))
        if threshold_hit and now < previous_until:
            events.append(
                AlertEvent(
                    rule_id=rule.rule_id,
                    state=AlertState.COOLDOWN,
                    severity=rule.severity,
                    message=rule.message,
                    collector_summary=collector.summary,
                    next_allowed_at=previous_until,
                )
            )
        elif threshold_hit:
            events.append(
                AlertEvent(
                    rule_id=rule.rule_id,
                    state=AlertState.ACTIVE,
                    severity=rule.severity,
                    message=rule.message,
                    collector_summary=collector.summary,
                    next_allowed_at=now + rule.cooldown_seconds,
                )
            )
        elif previous_until > 0:
            events.append(
                AlertEvent(
                    rule_id=rule.rule_id,
                    state=AlertState.RECOVERED,
                    severity=HealthState.OK,
                    message=f"Recovered: {rule.message}",
                    collector_summary=collector.summary,
                    next_allowed_at=0,
                )
            )
    return tuple(events)


def _text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise HealthModelError(f"{field_name} must not be empty")
    if len(text) > 240:
        raise HealthModelError(f"{field_name} exceeds max length 240")
    return text


def _state(value: HealthState | str) -> HealthState:
    if isinstance(value, HealthState):
        return value
    try:
        return HealthState(str(value or "").strip().lower())
    except ValueError as exc:
        raise HealthModelError("state must be ok, warn, critical, or unknown") from exc


def _state_rank(value: HealthState) -> int:
    return {
        HealthState.OK: 0,
        HealthState.UNKNOWN: 1,
        HealthState.WARN: 2,
        HealthState.CRITICAL: 3,
    }[value]

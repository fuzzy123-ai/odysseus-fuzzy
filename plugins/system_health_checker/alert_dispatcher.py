"""Alert dispatch planning for System Health Checker.

The dispatcher creates send/skip/recovery plans from alert events. It does not
call Telegram or any other external channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from .health_model import HealthModelError, HealthState
from .rule_engine import AlertEvent, AlertState


class DispatchAction(StrEnum):
    SEND = "send"
    SKIP_COOLDOWN = "skip_cooldown"
    SEND_RECOVERY = "send_recovery"


@dataclass(frozen=True, slots=True)
class AlertDispatchPlan:
    rule_id: str
    action: DispatchAction
    severity: HealthState
    message: str
    channel: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "action": self.action.value,
            "severity": self.severity.value,
            "message": self.message,
            "channel": self.channel,
        }


def build_dispatch_plan(
    *,
    events: Iterable[AlertEvent],
    channel: Any = "telegram",
    include_cooldown_skips: bool = False,
) -> tuple[AlertDispatchPlan, ...]:
    normalized_channel = _text(channel, field_name="channel")
    plans: list[AlertDispatchPlan] = []
    for event in events:
        if not isinstance(event, AlertEvent):
            raise HealthModelError("events must contain AlertEvent instances")
        if event.state == AlertState.ACTIVE:
            plans.append(
                AlertDispatchPlan(
                    rule_id=event.rule_id,
                    action=DispatchAction.SEND,
                    severity=event.severity,
                    message=event.message,
                    channel=normalized_channel,
                )
            )
        elif event.state == AlertState.RECOVERED:
            plans.append(
                AlertDispatchPlan(
                    rule_id=event.rule_id,
                    action=DispatchAction.SEND_RECOVERY,
                    severity=HealthState.OK,
                    message=event.message,
                    channel=normalized_channel,
                )
            )
        elif include_cooldown_skips:
            plans.append(
                AlertDispatchPlan(
                    rule_id=event.rule_id,
                    action=DispatchAction.SKIP_COOLDOWN,
                    severity=event.severity,
                    message=event.message,
                    channel=normalized_channel,
                )
            )
    return tuple(plans)


def render_dispatch_message(plan: AlertDispatchPlan) -> str:
    if not isinstance(plan, AlertDispatchPlan):
        raise HealthModelError("plan must be an AlertDispatchPlan")
    if plan.action == DispatchAction.SEND_RECOVERY:
        return f"Recovery: {plan.message}"
    if plan.action == DispatchAction.SKIP_COOLDOWN:
        return f"Cooldown: {plan.message}"
    return f"{plan.severity.value.upper()}: {plan.message}"


def _text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise HealthModelError(f"{field_name} must not be empty")
    if len(text) > 120:
        raise HealthModelError(f"{field_name} exceeds max length 120")
    return text

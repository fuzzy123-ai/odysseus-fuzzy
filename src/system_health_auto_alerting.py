"""Auto-alerting decision helpers for system health checker payloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from src.system_health_agent_interface import AlertSummary, HealthAgentInterfaceError
from src.system_health_rule_engine import AlertDecision


class AutoAlertAction(StrEnum):
    SEND = "send"
    SUPPRESS_COOLDOWN = "suppress_cooldown"
    SUPPRESS_DUPLICATE = "suppress_duplicate"
    SEND_RECOVERY = "send_recovery"
    NO_ACTION = "no_action"


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _normalize_key(value: Any, *, field_name: str = "dedupe_key") -> str:
    text = _normalize_text(value, field_name=field_name)
    normalized = text.strip().lower().replace(" ", "-")
    if not normalized:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return normalized


def _render_alert_payload(alert: AlertSummary) -> str:
    return f"{alert.severity.value.upper()}: {alert.title} -> {alert.next_action}"


@dataclass(frozen=True, slots=True)
class AutoAlertDecision:
    dedupe_key: str
    action: AutoAlertAction
    severity: str
    payload: str
    reason: str

    @classmethod
    def create(
        cls,
        *,
        dedupe_key: Any,
        action: AutoAlertAction | str,
        severity: Any = "",
        payload: Any = "",
        reason: Any,
    ) -> "AutoAlertDecision":
        normalized_action = action if isinstance(action, AutoAlertAction) else AutoAlertAction(str(action).strip().lower())
        return cls(
            dedupe_key=_normalize_key(dedupe_key),
            action=normalized_action,
            severity=_normalize_text(severity, field_name="severity", allow_empty=True).lower(),
            payload=_normalize_text(payload, field_name="payload", allow_empty=True),
            reason=_normalize_text(reason, field_name="reason"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dedupe_key": self.dedupe_key,
            "action": self.action.value,
            "severity": self.severity,
            "payload": self.payload,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AutoAlertBatch:
    decisions: tuple[AutoAlertDecision, ...]

    @property
    def send_count(self) -> int:
        return sum(1 for item in self.decisions if item.action == AutoAlertAction.SEND)

    @property
    def recovery_count(self) -> int:
        return sum(1 for item in self.decisions if item.action == AutoAlertAction.SEND_RECOVERY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions": tuple(item.to_dict() for item in self.decisions),
            "send_count": self.send_count,
            "recovery_count": self.recovery_count,
        }


def build_auto_alert_batch(
    alert_decision: AlertDecision,
    *,
    previous_active_keys: Iterable[Any] = (),
    recently_sent_keys: Iterable[Any] = (),
) -> AutoAlertBatch:
    if not isinstance(alert_decision, AlertDecision):
        raise HealthAgentInterfaceError("alert_decision must be an AlertDecision")

    normalized_previous = {_normalize_key(value, field_name="previous_active_key") for value in previous_active_keys if str(value or "").strip()}
    normalized_recent = {_normalize_key(value, field_name="recently_sent_key") for value in recently_sent_keys if str(value or "").strip()}

    current_alerts = {alert.dedupe_key: alert for alert in alert_decision.alerts}
    decisions: list[AutoAlertDecision] = []

    for dedupe_key, alert in sorted(current_alerts.items()):
        if dedupe_key in normalized_previous:
            decisions.append(
                AutoAlertDecision.create(
                    dedupe_key=dedupe_key,
                    action=AutoAlertAction.SUPPRESS_DUPLICATE,
                    severity=alert.severity.value,
                    payload="",
                    reason="alert is already active",
                )
            )
            continue
        if dedupe_key in normalized_recent:
            decisions.append(
                AutoAlertDecision.create(
                    dedupe_key=dedupe_key,
                    action=AutoAlertAction.SUPPRESS_COOLDOWN,
                    severity=alert.severity.value,
                    payload="",
                    reason="alert is within cooldown window",
                )
            )
            continue
        decisions.append(
            AutoAlertDecision.create(
                dedupe_key=dedupe_key,
                action=AutoAlertAction.SEND,
                severity=alert.severity.value,
                payload=_render_alert_payload(alert),
                reason="new alert should be sent",
            )
        )

    current_keys = set(current_alerts)
    for cleared_key in sorted(alert_decision.cleared_keys):
        decisions.append(
            AutoAlertDecision.create(
                dedupe_key=cleared_key,
                action=AutoAlertAction.SEND_RECOVERY,
                severity="ok",
                payload=f"RECOVERY: {cleared_key} cleared",
                reason="previous alert is no longer active",
            )
        )
    for previous_key in sorted(normalized_previous - current_keys - set(alert_decision.cleared_keys)):
        decisions.append(
            AutoAlertDecision.create(
                dedupe_key=previous_key,
                action=AutoAlertAction.NO_ACTION,
                severity="",
                payload="",
                reason="previous key has no current alert or recovery marker",
            )
        )

    return AutoAlertBatch(decisions=tuple(sorted(decisions, key=lambda item: (item.action.value, item.dedupe_key))))

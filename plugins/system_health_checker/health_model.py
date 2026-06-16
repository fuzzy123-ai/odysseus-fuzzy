"""Schema-stable health snapshot models for the System Health Checker plugin.

No host commands are executed here. Collectors and a Debian host agent can feed
these models later; Odysseus consumes only sanitized snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class HealthModelError(ValueError):
    """Raised when a health snapshot input is invalid."""


class HealthState(StrEnum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class CollectorKind(StrEnum):
    CPU = "cpu"
    MEMORY = "memory"
    LOAD = "load"
    UPTIME = "uptime"
    DISK = "disk"
    CONTAINERS = "containers"
    TEMPERATURE = "temperature"
    SMART = "smart"
    UPDATES = "updates"
    REBOOT = "reboot"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class CollectorStatus:
    kind: CollectorKind
    state: HealthState
    summary: str
    observed_at: str
    details: Mapping[str, Any]

    @classmethod
    def create(
        cls,
        *,
        kind: CollectorKind | str,
        state: HealthState | str,
        summary: Any,
        observed_at: Any,
        details: Mapping[str, Any] | None = None,
    ) -> "CollectorStatus":
        return cls(
            kind=_normalize_collector_kind(kind),
            state=_normalize_health_state(state),
            summary=_normalize_text(summary, field_name="summary"),
            observed_at=_normalize_text(observed_at, field_name="observed_at"),
            details=dict(details or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "state": self.state.value,
            "summary": self.summary,
            "observed_at": self.observed_at,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class AlertSummary:
    state: HealthState
    active_count: int
    highest_severity: HealthState
    messages: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        state: HealthState | str,
        active_count: int,
        highest_severity: HealthState | str,
        messages: tuple[Any, ...] | list[Any] = (),
    ) -> "AlertSummary":
        count = int(active_count)
        if count < 0:
            raise HealthModelError("active_count must not be negative")
        return cls(
            state=_normalize_health_state(state),
            active_count=count,
            highest_severity=_normalize_health_state(highest_severity),
            messages=tuple(_normalize_text(message, field_name="message") for message in messages),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "active_count": self.active_count,
            "highest_severity": self.highest_severity.value,
            "messages": list(self.messages),
        }


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    schema_version: int
    agent_id: str
    state: HealthState
    observed_at: str
    collectors: tuple[CollectorStatus, ...]
    alerts: AlertSummary

    @classmethod
    def create(
        cls,
        *,
        agent_id: Any,
        observed_at: Any,
        collectors: tuple[CollectorStatus, ...] | list[CollectorStatus],
        alerts: AlertSummary,
        schema_version: int = 1,
    ) -> "HealthSnapshot":
        if schema_version != 1:
            raise HealthModelError("schema_version must be 1")
        normalized_collectors = tuple(collectors)
        if not normalized_collectors:
            raise HealthModelError("collectors must not be empty")
        for collector in normalized_collectors:
            if not isinstance(collector, CollectorStatus):
                raise HealthModelError("collectors must contain CollectorStatus instances")
        if not isinstance(alerts, AlertSummary):
            raise HealthModelError("alerts must be an AlertSummary")
        return cls(
            schema_version=schema_version,
            agent_id=_normalize_text(agent_id, field_name="agent_id"),
            state=_overall_state(normalized_collectors, alerts),
            observed_at=_normalize_text(observed_at, field_name="observed_at"),
            collectors=normalized_collectors,
            alerts=alerts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "agent_id": self.agent_id,
            "state": self.state.value,
            "observed_at": self.observed_at,
            "collectors": [collector.to_dict() for collector in self.collectors],
            "alerts": self.alerts.to_dict(),
        }


def build_agent_offline_snapshot(*, observed_at: Any = "unknown") -> HealthSnapshot:
    collector = CollectorStatus.create(
        kind=CollectorKind.AGENT,
        state=HealthState.UNKNOWN,
        summary="Health agent is not connected",
        observed_at=observed_at,
        details={"setup_hint": "Install and start odysseus-health-agent.service on the Debian host."},
    )
    alerts = AlertSummary.create(
        state=HealthState.UNKNOWN,
        active_count=0,
        highest_severity=HealthState.UNKNOWN,
        messages=("Health status is unknown because the host agent is offline.",),
    )
    return HealthSnapshot.create(
        agent_id="odysseus-health-agent",
        observed_at=observed_at,
        collectors=(collector,),
        alerts=alerts,
    )


def _normalize_text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise HealthModelError(f"{field_name} must not be empty")
    if len(text) > 240:
        raise HealthModelError(f"{field_name} exceeds max length 240")
    return text


def _normalize_health_state(value: HealthState | str) -> HealthState:
    if isinstance(value, HealthState):
        return value
    raw = str(value or "").strip().lower()
    try:
        return HealthState(raw)
    except ValueError as exc:
        raise HealthModelError("state must be ok, warn, critical, or unknown") from exc


def _normalize_collector_kind(value: CollectorKind | str) -> CollectorKind:
    if isinstance(value, CollectorKind):
        return value
    raw = str(value or "").strip().lower()
    try:
        return CollectorKind(raw)
    except ValueError as exc:
        raise HealthModelError("collector kind is not supported") from exc


def _overall_state(collectors: tuple[CollectorStatus, ...], alerts: AlertSummary) -> HealthState:
    states = [collector.state for collector in collectors]
    states.append(alerts.highest_severity)
    if HealthState.CRITICAL in states:
        return HealthState.CRITICAL
    if HealthState.WARN in states:
        return HealthState.WARN
    if HealthState.UNKNOWN in states:
        return HealthState.UNKNOWN
    return HealthState.OK

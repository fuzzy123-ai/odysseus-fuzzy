"""Runtime-agnostic interface model for host-provided system health snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 180
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class HealthAgentInterfaceError(ValueError):
    """Raised when system health agent interface payloads are invalid."""


class CollectorState(StrEnum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class AlertSeverity(StrEnum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class _SeverityRank(IntEnum):
    OK = 0
    WARN = 1
    UNKNOWN = 2
    UNSUPPORTED = 2
    CRITICAL = 3


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise HealthAgentInterfaceError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise HealthAgentInterfaceError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_collector_state(value: CollectorState | str) -> CollectorState:
    if isinstance(value, CollectorState):
        return value
    normalized = _normalize_slug(value, field_name="collector_state")
    try:
        return CollectorState(normalized)
    except ValueError as exc:
        raise HealthAgentInterfaceError("unsupported collector_state") from exc


def _normalize_alert_severity(value: AlertSeverity | str) -> AlertSeverity:
    if isinstance(value, AlertSeverity):
        return value
    normalized = _normalize_slug(value, field_name="alert_severity")
    try:
        return AlertSeverity(normalized)
    except ValueError as exc:
        raise HealthAgentInterfaceError("unsupported alert_severity") from exc


def _normalize_schema_version(value: Any) -> str:
    text = _normalize_text(value, field_name="schema_version", allow_empty=False, limit=32)
    if not re.fullmatch(r"\d+\.\d+", text):
        raise HealthAgentInterfaceError("schema_version must look like major.minor")
    return text


def _severity_rank(value: CollectorState | AlertSeverity) -> _SeverityRank:
    return _SeverityRank[value.name]


@dataclass(frozen=True, slots=True)
class CollectorStatus:
    collector_id: str
    state: CollectorState
    summary: str
    observed_value: str
    setup_hint: str

    @classmethod
    def create(
        cls,
        *,
        collector_id: Any,
        state: CollectorState | str,
        summary: Any,
        observed_value: Any = "",
        setup_hint: Any = "",
    ) -> "CollectorStatus":
        normalized_state = _normalize_collector_state(state)
        normalized_setup_hint = _normalize_text(
            setup_hint,
            field_name="setup_hint",
            allow_empty=True,
        )
        if normalized_state in {CollectorState.UNKNOWN, CollectorState.UNSUPPORTED} and not normalized_setup_hint:
            normalized_setup_hint = "collector requires operator review or host-side setup clarification"
        return cls(
            collector_id=_normalize_slug(collector_id, field_name="collector_id"),
            state=normalized_state,
            summary=_normalize_text(summary, field_name="summary", allow_empty=False),
            observed_value=_normalize_text(observed_value, field_name="observed_value", allow_empty=True),
            setup_hint=normalized_setup_hint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "collector_id": self.collector_id,
            "state": self.state.value,
            "summary": self.summary,
            "observed_value": self.observed_value,
            "setup_hint": self.setup_hint,
        }


@dataclass(frozen=True, slots=True)
class AlertSummary:
    severity: AlertSeverity
    title: str
    cause: str
    next_action: str
    dedupe_key: str
    cooldown_hint: str

    @classmethod
    def create(
        cls,
        *,
        severity: AlertSeverity | str,
        title: Any,
        cause: Any,
        next_action: Any,
        dedupe_key: Any,
        cooldown_hint: Any = "",
    ) -> "AlertSummary":
        return cls(
            severity=_normalize_alert_severity(severity),
            title=_normalize_text(title, field_name="title", allow_empty=False),
            cause=_normalize_text(cause, field_name="cause", allow_empty=False),
            next_action=_normalize_text(next_action, field_name="next_action", allow_empty=False),
            dedupe_key=_normalize_slug(dedupe_key, field_name="dedupe_key"),
            cooldown_hint=_normalize_text(cooldown_hint, field_name="cooldown_hint", allow_empty=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "title": self.title,
            "cause": self.cause,
            "next_action": self.next_action,
            "dedupe_key": self.dedupe_key,
            "cooldown_hint": self.cooldown_hint,
        }


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    schema_version: str
    generated_at: str
    overall_status: CollectorState
    collectors: tuple[CollectorStatus, ...]
    alerts: tuple[AlertSummary, ...]
    host_label: str

    @classmethod
    def create(
        cls,
        *,
        schema_version: Any,
        generated_at: Any,
        collectors: Iterable[CollectorStatus],
        alerts: Iterable[AlertSummary] = (),
        host_label: Any = "",
        overall_status: CollectorState | str | None = None,
    ) -> "HealthSnapshot":
        normalized_collectors = tuple(collectors)
        normalized_alerts = tuple(alerts)
        if not normalized_collectors:
            raise HealthAgentInterfaceError("collectors must not be empty")
        if any(not isinstance(item, CollectorStatus) for item in normalized_collectors):
            raise HealthAgentInterfaceError("collectors must contain CollectorStatus items")
        if any(not isinstance(item, AlertSummary) for item in normalized_alerts):
            raise HealthAgentInterfaceError("alerts must contain AlertSummary items")

        derived_status = _derive_overall_status(normalized_collectors, normalized_alerts)
        normalized_overall_status = (
            derived_status if overall_status in (None, "") else _normalize_collector_state(overall_status)
        )
        if _severity_rank(normalized_overall_status) < _severity_rank(derived_status):
            raise HealthAgentInterfaceError("overall_status must not be less severe than derived health state")

        return cls(
            schema_version=_normalize_schema_version(schema_version),
            generated_at=_normalize_text(generated_at, field_name="generated_at", allow_empty=False, limit=48),
            overall_status=normalized_overall_status,
            collectors=tuple(sorted(normalized_collectors, key=lambda item: item.collector_id)),
            alerts=tuple(sorted(normalized_alerts, key=lambda item: (item.severity.value, item.dedupe_key))),
            host_label=_normalize_text(host_label, field_name="host_label", allow_empty=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "overall_status": self.overall_status.value,
            "collectors": tuple(item.to_dict() for item in self.collectors),
            "alerts": tuple(item.to_dict() for item in self.alerts),
            "host_label": self.host_label,
        }


def _derive_overall_status(
    collectors: tuple[CollectorStatus, ...],
    alerts: tuple[AlertSummary, ...],
) -> CollectorState:
    highest = _SeverityRank.OK
    derived = CollectorState.OK
    for collector in collectors:
        rank = _severity_rank(collector.state)
        if rank > highest:
            highest = rank
            derived = CollectorState(collector.state.value)
    for alert in alerts:
        rank = _severity_rank(alert.severity)
        if rank > highest:
            highest = rank
            derived = CollectorState(alert.severity.value)
    return derived

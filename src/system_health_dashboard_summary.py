"""Dashboard summary models for sanitized system health status."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from src.system_health_agent_interface import AlertSummary, CollectorState, HealthAgentInterfaceError, HealthSnapshot
from src.system_health_ops_readiness import OpsReadinessReport
from src.system_health_rule_engine import AlertDecision


class DashboardState(StrEnum):
    AGENT_OFFLINE = "agent_offline"
    NO_DATA = "no_data"
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    PARTIAL_UNKNOWN = "partial_unknown"
    SETUP_REQUIRED = "setup_required"


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _normalize_state(value: DashboardState | str) -> DashboardState:
    if isinstance(value, DashboardState):
        return value
    text = _normalize_text(value, field_name="state").lower()
    try:
        return DashboardState(text)
    except ValueError as exc:
        raise HealthAgentInterfaceError("unsupported dashboard state") from exc


def _normalize_alerts(alerts: Iterable[AlertSummary] | AlertDecision | None) -> tuple[AlertSummary, ...]:
    if alerts is None:
        return ()
    if isinstance(alerts, AlertDecision):
        return alerts.alerts
    normalized = tuple(alerts)
    if any(not isinstance(item, AlertSummary) for item in normalized):
        raise HealthAgentInterfaceError("alerts must contain AlertSummary items")
    return tuple(sorted(normalized, key=lambda item: (item.severity.value, item.dedupe_key)))


def _collector_counts(snapshot: HealthSnapshot | None) -> dict[str, int]:
    counts = {"ok": 0, "warn": 0, "critical": 0, "unknown": 0, "unsupported": 0}
    if snapshot is None:
        return counts
    for collector in snapshot.collectors:
        counts[collector.state.value] += 1
    return counts


def _derive_overview_state(
    snapshot: HealthSnapshot | None,
    alerts: tuple[AlertSummary, ...],
    readiness: OpsReadinessReport | None,
) -> DashboardState:
    if snapshot is None and readiness is None and not alerts:
        return DashboardState.NO_DATA
    if snapshot is None and (readiness is not None or alerts):
        return DashboardState.AGENT_OFFLINE
    if snapshot is None:
        return DashboardState.NO_DATA

    counts = _collector_counts(snapshot)
    if snapshot.overall_status == CollectorState.CRITICAL or any(alert.severity.value == "critical" for alert in alerts):
        return DashboardState.CRITICAL
    if snapshot.overall_status == CollectorState.WARN or any(alert.severity.value == "warn" for alert in alerts):
        return DashboardState.WARN
    if counts["unsupported"] > 0:
        return DashboardState.SETUP_REQUIRED
    if counts["unknown"] > 0:
        return DashboardState.PARTIAL_UNKNOWN
    if readiness is not None and readiness.overall_status == "no_go":
        return DashboardState.WARN
    return DashboardState.OK


def _readiness_state(readiness: OpsReadinessReport | None) -> DashboardState:
    if readiness is None:
        return DashboardState.NO_DATA
    if readiness.overall_status == "no_go":
        return DashboardState.CRITICAL
    if readiness.overall_status == "warn":
        return DashboardState.WARN
    return DashboardState.OK


@dataclass(frozen=True, slots=True)
class DashboardSection:
    section_id: str
    state: DashboardState
    summary: str
    counts: dict[str, int] | dict[str, str]

    @classmethod
    def create(
        cls,
        *,
        section_id: Any,
        state: DashboardState | str,
        summary: Any,
        counts: dict[str, int] | dict[str, str] | None = None,
    ) -> "DashboardSection":
        normalized_counts = dict(sorted((counts or {}).items(), key=lambda item: item[0]))
        return cls(
            section_id=_normalize_text(section_id, field_name="section_id"),
            state=_normalize_state(state),
            summary=_normalize_text(summary, field_name="summary"),
            counts=normalized_counts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "state": self.state.value,
            "summary": self.summary,
            "counts": dict(self.counts),
        }


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    overview_state: DashboardState
    sections: tuple[DashboardSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overview_state": self.overview_state.value,
            "sections": tuple(section.to_dict() for section in self.sections),
        }


def build_system_health_dashboard_summary(
    *,
    snapshot: HealthSnapshot | None = None,
    alerts: Iterable[AlertSummary] | AlertDecision | None = None,
    readiness: OpsReadinessReport | None = None,
) -> DashboardSummary:
    if snapshot is not None and not isinstance(snapshot, HealthSnapshot):
        raise HealthAgentInterfaceError("snapshot must be a HealthSnapshot or None")
    if readiness is not None and not isinstance(readiness, OpsReadinessReport):
        raise HealthAgentInterfaceError("readiness must be an OpsReadinessReport or None")

    normalized_alerts = _normalize_alerts(alerts)
    counts = _collector_counts(snapshot)
    collector_state = DashboardState.NO_DATA if snapshot is None else _derive_overview_state(snapshot, (), None)
    collector_summary = "collectors: no data"
    if snapshot is not None:
        collector_summary = f"collectors: {len(snapshot.collectors)} total on {snapshot.host_label or 'host'}"

    alert_state = DashboardState.NO_DATA if not normalized_alerts else (
        DashboardState.CRITICAL if any(alert.severity.value == "critical" for alert in normalized_alerts) else DashboardState.WARN
    )
    alert_summary = "alerts: quiet"
    if normalized_alerts:
        alert_summary = f"alerts: {len(normalized_alerts)} active"

    readiness_counts: dict[str, str] = {}
    readiness_summary = "readiness: no data"
    readiness_section_state = _readiness_state(readiness)
    if readiness is not None:
        readiness_counts = {"overall_status": readiness.overall_status, "item_count": str(len(readiness.items))}
        readiness_summary = f"readiness: {readiness.overall_status}"

    sections = (
        DashboardSection.create(
            section_id="collectors",
            state=collector_state,
            summary=collector_summary,
            counts=counts,
        ),
        DashboardSection.create(
            section_id="alerts",
            state=alert_state,
            summary=alert_summary,
            counts={"active": len(normalized_alerts)},
        ),
        DashboardSection.create(
            section_id="readiness",
            state=readiness_section_state,
            summary=readiness_summary,
            counts=readiness_counts,
        ),
    )

    return DashboardSummary(
        overview_state=_derive_overview_state(snapshot, normalized_alerts, readiness),
        sections=sections,
    )

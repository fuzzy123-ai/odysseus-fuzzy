"""Basic collector reading models for host-provided system health snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.system_health_agent_interface import (
    CollectorState,
    CollectorStatus,
    HealthAgentInterfaceError,
    HealthSnapshot,
)


_ALLOWED_COLLECTOR_IDS = ("cpu", "disk", "load", "memory", "uptime")


def _normalize_collector_id(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _ALLOWED_COLLECTOR_IDS:
        allowed = ", ".join(_ALLOWED_COLLECTOR_IDS)
        raise HealthAgentInterfaceError(f"collector_id must be one of: {allowed}")
    return normalized


def _normalize_value(value: Any) -> str:
    return " ".join(str(value or "").split())


@dataclass(frozen=True, slots=True)
class BasicCollectorReading:
    collector_id: str
    value: str
    unit: str
    state: CollectorState
    summary: str
    setup_hint: str

    @classmethod
    def create(
        cls,
        *,
        collector_id: Any,
        value: Any,
        unit: Any,
        state: CollectorState | str,
        summary: Any,
        setup_hint: Any = "",
    ) -> "BasicCollectorReading":
        collector_status = CollectorStatus.create(
            collector_id=_normalize_collector_id(collector_id),
            state=state,
            summary=summary,
            observed_value=_normalize_value(value),
            setup_hint=setup_hint,
        )
        return cls(
            collector_id=collector_status.collector_id,
            value=collector_status.observed_value,
            unit=_normalize_value(unit),
            state=collector_status.state,
            summary=collector_status.summary,
            setup_hint=collector_status.setup_hint,
        )

    def to_collector_status(self) -> CollectorStatus:
        observed_value = self.value if not self.unit else f"{self.value} {self.unit}".strip()
        return CollectorStatus.create(
            collector_id=self.collector_id,
            state=self.state,
            summary=self.summary,
            observed_value=observed_value,
            setup_hint=self.setup_hint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "collector_id": self.collector_id,
            "value": self.value,
            "unit": self.unit,
            "state": self.state.value,
            "summary": self.summary,
            "setup_hint": self.setup_hint,
        }


def build_basic_health_snapshot(
    readings: Iterable[BasicCollectorReading],
    *,
    generated_at: Any,
    host_label: Any = "",
    schema_version: Any = "1.0",
) -> HealthSnapshot:
    normalized_readings = tuple(readings)
    if not normalized_readings:
        raise HealthAgentInterfaceError("readings must not be empty")
    if any(not isinstance(item, BasicCollectorReading) for item in normalized_readings):
        raise HealthAgentInterfaceError("readings must contain BasicCollectorReading items")

    return HealthSnapshot.create(
        schema_version=schema_version,
        generated_at=generated_at,
        collectors=tuple(item.to_collector_status() for item in normalized_readings),
        host_label=host_label,
    )

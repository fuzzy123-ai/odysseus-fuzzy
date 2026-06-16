"""Small backend contract for memory diagnostics snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_MAX_TIMESTAMP = 40
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_NON_NEGATIVE_UNITS = {"ms", "count", "bytes", "percent", "seconds"}


class MemoryDiagnosticsError(ValueError):
    """Raised when a memory diagnostics payload is invalid or unsafe."""


class MetricFamily(StrEnum):
    INGEST = "ingest"
    INDEX = "index"
    QUERY = "query"
    GRAPH = "graph"
    UI = "ui"
    JOB = "job"
    STORAGE = "storage"
    REBUILD = "rebuild"
    MEMORY = "memory"


class MetricStatus(StrEnum):
    HEALTHY = "healthy"
    ATTENTION = "attention"
    WARNING = "warning"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNKNOWN = "unknown"


class MetricUnit(StrEnum):
    MS = "ms"
    COUNT = "count"
    BYTES = "bytes"
    PERCENT = "percent"
    BOOLEAN = "boolean"
    SECONDS = "seconds"


class MetricSeverity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise MemoryDiagnosticsError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise MemoryDiagnosticsError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise MemoryDiagnosticsError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise MemoryDiagnosticsError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_timestamp(value: Any, *, field_name: str, allow_empty: bool) -> str:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise MemoryDiagnosticsError(f"{field_name} must not be empty")
    if len(text) > _MAX_TIMESTAMP or not _TIMESTAMP_RE.fullmatch(text):
        raise MemoryDiagnosticsError(f"{field_name} must be an ISO-8601 UTC timestamp")
    return text


def _normalize_family(value: Any) -> MetricFamily:
    if isinstance(value, MetricFamily):
        return value
    try:
        return MetricFamily(_normalize_slug(value, field_name="family"))
    except ValueError as exc:
        raise MemoryDiagnosticsError("family is not supported") from exc


def _normalize_status(value: Any) -> MetricStatus:
    if isinstance(value, MetricStatus):
        return value
    try:
        return MetricStatus(_normalize_slug(value, field_name="status"))
    except ValueError as exc:
        raise MemoryDiagnosticsError("status is not supported") from exc


def _normalize_unit(value: Any) -> MetricUnit:
    if isinstance(value, MetricUnit):
        return value
    normalized = _normalize_slug(value, field_name="unit").replace("-", "_")
    try:
        return MetricUnit(normalized)
    except ValueError as exc:
        raise MemoryDiagnosticsError("unit is not supported") from exc


def _normalize_severity(value: Any) -> MetricSeverity:
    if isinstance(value, MetricSeverity):
        return value
    if isinstance(value, int):
        try:
            return MetricSeverity(value)
        except ValueError as exc:
            raise MemoryDiagnosticsError("severity must be between 1 and 4") from exc
    normalized = _normalize_slug(value, field_name="severity")
    name_map = {
        "low": MetricSeverity.LOW,
        "medium": MetricSeverity.MEDIUM,
        "high": MetricSeverity.HIGH,
        "critical": MetricSeverity.CRITICAL,
    }
    if normalized not in name_map:
        raise MemoryDiagnosticsError("severity must be low, medium, high, critical, or 1-4")
    return name_map[normalized]


def _normalize_value(value: Any, *, unit: MetricUnit) -> float | bool:
    if unit == MetricUnit.BOOLEAN:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
        raise MemoryDiagnosticsError("boolean metrics require a boolean value")
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        raise MemoryDiagnosticsError("metric value must be numeric for non-boolean units") from None
    if unit.value in _NON_NEGATIVE_UNITS and normalized < 0:
        raise MemoryDiagnosticsError("negative metric values are not allowed for timing, count, bytes, percent, or seconds")
    return normalized


@dataclass(frozen=True, slots=True)
class DiagnosticMetric:
    metric_id: str
    family: MetricFamily
    phase: str
    value: float | bool
    unit: MetricUnit
    budget: float | None
    status: MetricStatus
    severity: MetricSeverity
    clipped: bool
    stale: bool
    evidence_ref: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        metric_id: Any,
        family: MetricFamily | str,
        phase: Any,
        value: Any,
        unit: MetricUnit | str,
        budget: Any = None,
        status: MetricStatus | str,
        severity: MetricSeverity | int | str,
        clipped: bool,
        stale: bool,
        evidence_ref: Any,
        next_action: Any,
    ) -> "DiagnosticMetric":
        normalized_unit = _normalize_unit(unit)
        normalized_value = _normalize_value(value, unit=normalized_unit)
        normalized_budget: float | None
        if budget in (None, ""):
            normalized_budget = None
        else:
            try:
                normalized_budget = float(budget)
            except (TypeError, ValueError):
                raise MemoryDiagnosticsError("budget must be numeric or empty") from None
            if normalized_unit.value in _NON_NEGATIVE_UNITS and normalized_budget < 0:
                raise MemoryDiagnosticsError("budget must not be negative for non-boolean units")
        normalized_status = _normalize_status(status)
        normalized_evidence = _normalize_text(evidence_ref, field_name="evidence_ref", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_clipped = bool(clipped)
        normalized_stale = bool(stale)

        if normalized_clipped and not (normalized_evidence or normalized_next_action):
            raise MemoryDiagnosticsError("clipped metrics require evidence or next_action")
        if normalized_stale and not (normalized_evidence or normalized_next_action):
            raise MemoryDiagnosticsError("stale metrics require evidence or next_action")
        if normalized_status in {MetricStatus.BLOCKED, MetricStatus.FAILED} and not (
            normalized_evidence or normalized_next_action
        ):
            raise MemoryDiagnosticsError("blocked and failed metrics require evidence or next_action")
        if (
            normalized_budget is not None
            and normalized_unit != MetricUnit.BOOLEAN
            and isinstance(normalized_value, float)
            and normalized_value > normalized_budget
            and normalized_status in {MetricStatus.HEALTHY, MetricStatus.ATTENTION}
        ):
            raise MemoryDiagnosticsError("budget violations require warning, blocked, or failed status")

        return cls(
            metric_id=_normalize_slug(metric_id, field_name="metric_id"),
            family=_normalize_family(family),
            phase=_normalize_slug(phase, field_name="phase"),
            value=normalized_value,
            unit=normalized_unit,
            budget=normalized_budget,
            status=normalized_status,
            severity=_normalize_severity(severity),
            clipped=normalized_clipped,
            stale=normalized_stale,
            evidence_ref=normalized_evidence,
            next_action=normalized_next_action,
        )


@dataclass(frozen=True, slots=True)
class DiagnosticSnapshot:
    snapshot_id: str
    subject_ref: str
    metrics: tuple[DiagnosticMetric, ...]
    created_at: str
    summary: str

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: Any,
        subject_ref: Any,
        metrics: Iterable[DiagnosticMetric],
        created_at: Any,
        summary: Any,
    ) -> "DiagnosticSnapshot":
        normalized_metrics = tuple(metrics)
        if not normalized_metrics:
            raise MemoryDiagnosticsError("metrics must not be empty")
        if any(not isinstance(metric, DiagnosticMetric) for metric in normalized_metrics):
            raise MemoryDiagnosticsError("metrics must contain DiagnosticMetric items")
        metric_ids = {metric.metric_id for metric in normalized_metrics}
        if len(metric_ids) != len(normalized_metrics):
            raise MemoryDiagnosticsError("metric_id must be unique within a snapshot")
        return cls(
            snapshot_id=_normalize_slug(snapshot_id, field_name="snapshot_id"),
            subject_ref=_normalize_slug(subject_ref, field_name="subject_ref"),
            metrics=tuple(sorted(normalized_metrics, key=lambda metric: metric.metric_id)),
            created_at=_normalize_timestamp(created_at, field_name="created_at", allow_empty=False),
            summary=_normalize_text(summary, field_name="summary", allow_empty=False, limit=_MAX_LONG_TEXT),
        )

    def audit_summary(self) -> dict[str, Any]:
        status_counts = {
            status.value: sum(1 for metric in self.metrics if metric.status == status)
            for status in MetricStatus
        }
        family_counts: dict[str, int] = {}
        for metric in self.metrics:
            family_counts[metric.family.value] = family_counts.get(metric.family.value, 0) + 1
        return {
            "snapshot_id": self.snapshot_id,
            "subject_ref": self.subject_ref,
            "metric_count": len(self.metrics),
            "family_counts": family_counts,
            "status_counts": status_counts,
            "clipped_count": sum(1 for metric in self.metrics if metric.clipped),
            "stale_count": sum(1 for metric in self.metrics if metric.stale),
            "metrics": tuple(
                {
                    "metric_id": metric.metric_id,
                    "family": metric.family.value,
                    "status": metric.status.value,
                    "severity": int(metric.severity),
                    "clipped": metric.clipped,
                    "stale": metric.stale,
                }
                for metric in self.metrics
            ),
        }

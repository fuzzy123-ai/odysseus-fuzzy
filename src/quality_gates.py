"""Small backend contract for machine-readable quality gates."""

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


class QualityGateError(ValueError):
    """Raised when a quality gate payload is invalid or unsafe."""


class QualityGateType(StrEnum):
    TESTS = "tests"
    GIT = "git"
    EVIDENCE = "evidence"
    SCOPE = "scope"
    HOT_FILE = "hot_file"
    HANDOFF = "handoff"
    MANUAL = "manual"


class QualityGateStatus(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"
    FAIL = "fail"
    SKIP = "skip"


class QualityGateSeverity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise QualityGateError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise QualityGateError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise QualityGateError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise QualityGateError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_text_list(values: Iterable[Any], *, field_name: str, limit: int = _MAX_TEXT) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value, field_name=field_name, allow_empty=True, limit=limit)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _normalize_timestamp(value: Any, *, field_name: str, allow_empty: bool) -> str:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return ""
        raise QualityGateError(f"{field_name} must not be empty")
    if len(text) > _MAX_TIMESTAMP or not _TIMESTAMP_RE.fullmatch(text):
        raise QualityGateError(f"{field_name} must be an ISO-8601 UTC timestamp")
    return text


def _normalize_status(value: Any) -> QualityGateStatus:
    if isinstance(value, QualityGateStatus):
        return value
    normalized = _normalize_slug(value, field_name="status")
    try:
        return QualityGateStatus(normalized)
    except ValueError as exc:
        raise QualityGateError("status is not a supported quality gate status") from exc


def _normalize_gate_type(value: Any) -> QualityGateType:
    if isinstance(value, QualityGateType):
        return value
    normalized = _normalize_slug(value, field_name="gate_type")
    try:
        return QualityGateType(normalized)
    except ValueError as exc:
        raise QualityGateError("gate_type is not a supported quality gate type") from exc


def _normalize_severity(value: Any) -> QualityGateSeverity:
    if isinstance(value, QualityGateSeverity):
        return value
    if isinstance(value, int):
        try:
            return QualityGateSeverity(value)
        except ValueError as exc:
            raise QualityGateError("severity must be between 1 and 4") from exc
    normalized = _normalize_slug(value, field_name="severity")
    name_map = {
        "low": QualityGateSeverity.LOW,
        "medium": QualityGateSeverity.MEDIUM,
        "high": QualityGateSeverity.HIGH,
        "critical": QualityGateSeverity.CRITICAL,
    }
    if normalized not in name_map:
        raise QualityGateError("severity must be low, medium, high, critical, or 1-4")
    return name_map[normalized]


@dataclass(frozen=True, slots=True)
class QualityGate:
    gate_id: str
    gate_type: QualityGateType
    subject_ref: str
    agent_run_id: str
    plan_node_id: str
    status: QualityGateStatus
    severity: QualityGateSeverity
    required: bool
    evidence: tuple[str, ...]
    verified_at: str
    verified_by: str
    block_reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        gate_id: Any,
        gate_type: QualityGateType | str,
        subject_ref: Any,
        agent_run_id: Any,
        plan_node_id: Any,
        status: QualityGateStatus | str,
        severity: QualityGateSeverity | int | str,
        required: bool,
        evidence: Iterable[Any],
        verified_at: Any,
        verified_by: Any,
        block_reason: Any,
        next_action: Any,
    ) -> "QualityGate":
        normalized_status = _normalize_status(status)
        normalized_evidence = _normalize_text_list(evidence, field_name="evidence")
        normalized_verified_by = _normalize_text(
            verified_by,
            field_name="verified_by",
            allow_empty=True,
        )
        normalized_block_reason = _normalize_text(
            block_reason,
            field_name="block_reason",
            allow_empty=True,
            limit=_MAX_LONG_TEXT,
        )
        normalized_next_action = _normalize_text(
            next_action,
            field_name="next_action",
            allow_empty=True,
            limit=_MAX_LONG_TEXT,
        )
        if normalized_status == QualityGateStatus.PASS and not (normalized_evidence or normalized_verified_by):
            raise QualityGateError("pass gates require evidence or a verifier")
        if normalized_status in {QualityGateStatus.FAIL, QualityGateStatus.BLOCK} and not (
            normalized_block_reason or normalized_evidence
        ):
            raise QualityGateError("fail and block gates require a block_reason or evidence")
        if normalized_status == QualityGateStatus.SKIP and not normalized_block_reason:
            raise QualityGateError("skip gates require an explicit reason")
        if normalized_status == QualityGateStatus.PENDING and normalized_verified_by:
            raise QualityGateError("pending gates must not claim a verifier")
        return cls(
            gate_id=_normalize_slug(gate_id, field_name="gate_id"),
            gate_type=_normalize_gate_type(gate_type),
            subject_ref=_normalize_slug(subject_ref, field_name="subject_ref"),
            agent_run_id=_normalize_slug(agent_run_id, field_name="agent_run_id"),
            plan_node_id=_normalize_slug(plan_node_id, field_name="plan_node_id"),
            status=normalized_status,
            severity=_normalize_severity(severity),
            required=bool(required),
            evidence=normalized_evidence,
            verified_at=_normalize_timestamp(verified_at, field_name="verified_at", allow_empty=True),
            verified_by=normalized_verified_by,
            block_reason=normalized_block_reason,
            next_action=normalized_next_action,
        )


@dataclass(frozen=True, slots=True)
class QualityGateResult:
    gates: tuple[QualityGate, ...]
    verified_done: bool
    blocking_gate_ids: tuple[str, ...]
    warning_gate_ids: tuple[str, ...]

    @classmethod
    def create(cls, *, gates: Iterable[QualityGate]) -> "QualityGateResult":
        normalized: list[QualityGate] = []
        seen: set[str] = set()
        for gate in gates:
            if not isinstance(gate, QualityGate):
                raise QualityGateError("gates must contain QualityGate instances")
            if gate.gate_id in seen:
                raise QualityGateError("gate_id must be unique within a result")
            seen.add(gate.gate_id)
            normalized.append(gate)
        ordered = tuple(sorted(normalized, key=lambda gate: (gate.required is False, gate.gate_id)))
        blocking = tuple(
            gate.gate_id
            for gate in ordered
            if gate.required and gate.status in {QualityGateStatus.PENDING, QualityGateStatus.BLOCK, QualityGateStatus.FAIL}
        )
        warnings = tuple(gate.gate_id for gate in ordered if gate.status == QualityGateStatus.WARN)
        return cls(
            gates=ordered,
            verified_done=not blocking,
            blocking_gate_ids=blocking,
            warning_gate_ids=warnings,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "gate_count": len(self.gates),
            "verified_done": self.verified_done,
            "blocking_gate_ids": self.blocking_gate_ids,
            "warning_gate_ids": self.warning_gate_ids,
            "status_counts": {
                status.value: sum(1 for gate in self.gates if gate.status == status)
                for status in QualityGateStatus
            },
            "gates": tuple(
                {
                    "gate_id": gate.gate_id,
                    "gate_type": gate.gate_type.value,
                    "status": gate.status.value,
                    "required": gate.required,
                    "severity": int(gate.severity),
                }
                for gate in self.gates
            ),
        }

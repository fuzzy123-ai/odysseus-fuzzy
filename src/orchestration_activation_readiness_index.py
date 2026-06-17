"""Readiness index models for orchestration activation operator review."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from src.orchestration_activation_audit_trail import ActivationAuditEventType, ActivationAuditTrail, ActivationAuditError
from src.orchestration_activation_handoff_checklist import HandoffChecklistReport
from src.orchestration_operator_activation_packet import (
    OperatorActivationPacket,
    OperatorActivationPacketState,
)


class ActivationReadinessIndexStatus(StrEnum):
    READY = "ready"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    NOT_STARTED = "not_started"


_SECTION_IDS = (
    "prepared_foundation",
    "evidence_artifacts",
    "readiness_gates",
    "blocked_runtime_capabilities",
    "operator_next_steps",
    "known_limits",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ActivationAuditError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: ActivationReadinessIndexStatus | str) -> ActivationReadinessIndexStatus:
    if isinstance(value, ActivationReadinessIndexStatus):
        return value
    text = _normalize_text(value, field_name="status").strip().lower()
    try:
        return ActivationReadinessIndexStatus(text)
    except ValueError as exc:
        raise ActivationAuditError("unsupported readiness index status") from exc


def _normalize_section_id(value: Any) -> str:
    text = _normalize_text(value, field_name="section_id").strip().lower()
    if text not in _SECTION_IDS:
        raise ActivationAuditError("unsupported readiness index section_id")
    return text


@dataclass(frozen=True, slots=True)
class ActivationReadinessIndexItem:
    section_id: str
    status: ActivationReadinessIndexStatus
    summary: str
    detail_count: int

    @classmethod
    def create(
        cls,
        *,
        section_id: Any,
        status: ActivationReadinessIndexStatus | str,
        summary: Any,
        detail_count: int = 0,
    ) -> "ActivationReadinessIndexItem":
        if detail_count < 0:
            raise ActivationAuditError("detail_count must be non-negative")
        return cls(
            section_id=_normalize_section_id(section_id),
            status=_normalize_status(status),
            summary=_normalize_text(summary, field_name="summary"),
            detail_count=detail_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "status": self.status.value,
            "summary": self.summary,
            "detail_count": self.detail_count,
        }


@dataclass(frozen=True, slots=True)
class ActivationReadinessIndex:
    overall_status: ActivationReadinessIndexStatus
    items: tuple[ActivationReadinessIndexItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status.value,
            "items": tuple(item.to_dict() for item in self.items),
        }


def _derive_overall_status(items: tuple[ActivationReadinessIndexItem, ...]) -> ActivationReadinessIndexStatus:
    statuses = {item.status for item in items}
    if ActivationReadinessIndexStatus.BLOCKED in statuses:
        return ActivationReadinessIndexStatus.BLOCKED
    if ActivationReadinessIndexStatus.REVIEW_REQUIRED in statuses:
        return ActivationReadinessIndexStatus.REVIEW_REQUIRED
    if ActivationReadinessIndexStatus.DEFERRED in statuses:
        return ActivationReadinessIndexStatus.DEFERRED
    if statuses == {ActivationReadinessIndexStatus.NOT_STARTED}:
        return ActivationReadinessIndexStatus.NOT_STARTED
    return ActivationReadinessIndexStatus.READY


def build_activation_readiness_index(
    *,
    packet: OperatorActivationPacket | None = None,
    checklist: HandoffChecklistReport | None = None,
    audit_trail: ActivationAuditTrail | None = None,
) -> ActivationReadinessIndex:
    if packet is not None and not isinstance(packet, OperatorActivationPacket):
        raise ActivationAuditError("packet must be an OperatorActivationPacket or None")
    if checklist is not None and not isinstance(checklist, HandoffChecklistReport):
        raise ActivationAuditError("checklist must be a HandoffChecklistReport or None")
    if audit_trail is not None and not isinstance(audit_trail, ActivationAuditTrail):
        raise ActivationAuditError("audit_trail must be an ActivationAuditTrail or None")

    packet_state = None if packet is None else packet.state
    checklist_state = None if checklist is None else checklist.overall_status
    events = () if audit_trail is None else audit_trail.events

    has_blocking_event = any(
        event.event_type in {ActivationAuditEventType.GATE_BLOCKED, ActivationAuditEventType.ACTIVATION_CANCELLED}
        for event in events
    )
    has_deferred_event = any(event.event_type == ActivationAuditEventType.ACTIVATION_DEFERRED for event in events)
    has_approved = any(event.event_type == ActivationAuditEventType.OPERATOR_APPROVED for event in events)

    prepared_foundation_status = (
        ActivationReadinessIndexStatus.NOT_STARTED
        if packet is None and checklist is None and audit_trail is None
        else ActivationReadinessIndexStatus.READY
        if packet_state in {
            OperatorActivationPacketState.READY_FOR_REVIEW,
            OperatorActivationPacketState.APPROVED_PENDING_RUNTIME_GATE,
        }
        else ActivationReadinessIndexStatus.REVIEW_REQUIRED
        if packet_state == OperatorActivationPacketState.DEFERRED
        else ActivationReadinessIndexStatus.BLOCKED
    )

    evidence_status = (
        ActivationReadinessIndexStatus.NOT_STARTED
        if audit_trail is None
        else ActivationReadinessIndexStatus.READY
        if len(events) > 0
        else ActivationReadinessIndexStatus.REVIEW_REQUIRED
    )

    readiness_gates_status = (
        ActivationReadinessIndexStatus.BLOCKED
        if checklist_state == "blocked" or has_blocking_event
        else ActivationReadinessIndexStatus.READY
        if checklist_state == "ready"
        else ActivationReadinessIndexStatus.REVIEW_REQUIRED
        if checklist_state == "needs_review"
        else ActivationReadinessIndexStatus.DEFERRED
        if has_deferred_event or packet_state == OperatorActivationPacketState.DEFERRED
        else ActivationReadinessIndexStatus.NOT_STARTED
    )

    blocked_runtime_status = (
        ActivationReadinessIndexStatus.READY
        if packet is not None and packet.blocked_runtime_actions
        else ActivationReadinessIndexStatus.NOT_STARTED
        if packet is None
        else ActivationReadinessIndexStatus.REVIEW_REQUIRED
    )

    operator_next_steps_status = (
        ActivationReadinessIndexStatus.BLOCKED
        if packet_state in {OperatorActivationPacketState.BLOCKED, OperatorActivationPacketState.CANCELLED}
        else ActivationReadinessIndexStatus.REVIEW_REQUIRED
        if checklist_state == "needs_review" or packet_state == OperatorActivationPacketState.DEFERRED
        else ActivationReadinessIndexStatus.READY
        if has_approved or packet_state in {
            OperatorActivationPacketState.READY_FOR_REVIEW,
            OperatorActivationPacketState.APPROVED_PENDING_RUNTIME_GATE,
        }
        else ActivationReadinessIndexStatus.NOT_STARTED
    )

    known_limits_status = (
        ActivationReadinessIndexStatus.READY
        if packet is not None and packet.blocked_runtime_actions
        else ActivationReadinessIndexStatus.NOT_STARTED
        if packet is None
        else ActivationReadinessIndexStatus.REVIEW_REQUIRED
    )

    items = (
        ActivationReadinessIndexItem.create(
            section_id="prepared_foundation",
            status=prepared_foundation_status,
            summary="foundation packet models are prepared for operator review"
            if packet is not None or checklist is not None or audit_trail is not None
            else "foundation activation inputs have not been assembled yet",
            detail_count=0 if packet is None else len(packet.sections),
        ),
        ActivationReadinessIndexItem.create(
            section_id="evidence_artifacts",
            status=evidence_status,
            summary="audit evidence is present for operator review"
            if audit_trail is not None and len(events) > 0
            else "audit evidence is missing or not started",
            detail_count=len(events),
        ),
        ActivationReadinessIndexItem.create(
            section_id="readiness_gates",
            status=readiness_gates_status,
            summary=(
                f"handoff checklist is {checklist_state}"
                if checklist is not None
                else "handoff checklist has not been provided"
            ),
            detail_count=0 if checklist is None else len(checklist.items),
        ),
        ActivationReadinessIndexItem.create(
            section_id="blocked_runtime_capabilities",
            status=blocked_runtime_status,
            summary="runtime capabilities remain intentionally blocked as a known boundary"
            if packet is not None and packet.blocked_runtime_actions
            else "runtime capability boundaries are not yet recorded",
            detail_count=0 if packet is None else len(packet.blocked_runtime_actions),
        ),
        ActivationReadinessIndexItem.create(
            section_id="operator_next_steps",
            status=operator_next_steps_status,
            summary="operator next steps are defined from current packet state"
            if packet is not None
            else "operator next steps are not yet assembled",
            detail_count=1 if packet is not None else 0,
        ),
        ActivationReadinessIndexItem.create(
            section_id="known_limits",
            status=known_limits_status,
            summary="known runtime limits are documented in blocked capability boundaries"
            if packet is not None and packet.blocked_runtime_actions
            else "known limits are not yet summarized",
            detail_count=0 if packet is None else len(packet.blocked_runtime_actions),
        ),
    )

    return ActivationReadinessIndex(
        overall_status=_derive_overall_status(items),
        items=tuple(sorted(items, key=lambda item: item.section_id)),
    )

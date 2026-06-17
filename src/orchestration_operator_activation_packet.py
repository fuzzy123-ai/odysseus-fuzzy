"""Operator activation packet summary models for pre-runtime review."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from src.orchestration_activation_audit_trail import (
    ActivationAuditEventType,
    ActivationAuditTrail,
    ActivationAuditError,
)
from src.orchestration_activation_handoff_checklist import HandoffChecklistReport


class OperatorActivationPacketState(StrEnum):
    READY_FOR_REVIEW = "ready_for_review"
    BLOCKED = "blocked"
    APPROVED_PENDING_RUNTIME_GATE = "approved_pending_runtime_gate"
    CANCELLED = "cancelled"
    DEFERRED = "deferred"


_DEFAULT_BLOCKED_RUNTIME_ACTIONS = (
    "runtime_hooks",
    "thread_sends",
    "git_runner",
    "test_runner",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ActivationAuditError(f"{field_name} must not be empty")
    return text


def _normalize_runtime_actions(values: Iterable[Any] | None) -> tuple[str, ...]:
    raw_values = _DEFAULT_BLOCKED_RUNTIME_ACTIONS if values is None else tuple(values)
    normalized = [_normalize_text(value, field_name="blocked_runtime_action").lower().replace(" ", "_") for value in raw_values]
    return tuple(sorted(dict.fromkeys(normalized)))


@dataclass(frozen=True, slots=True)
class OperatorActivationPacketSection:
    section_id: str
    summary: str
    status: str
    item_count: int

    @classmethod
    def create(
        cls,
        *,
        section_id: Any,
        summary: Any,
        status: Any,
        item_count: int,
    ) -> "OperatorActivationPacketSection":
        if item_count < 0:
            raise ActivationAuditError("item_count must be non-negative")
        return cls(
            section_id=_normalize_text(section_id, field_name="section_id"),
            summary=_normalize_text(summary, field_name="summary"),
            status=_normalize_text(status, field_name="status"),
            item_count=item_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "summary": self.summary,
            "status": self.status,
            "item_count": self.item_count,
        }


@dataclass(frozen=True, slots=True)
class OperatorActivationPacket:
    state: OperatorActivationPacketState
    blocked_runtime_actions: tuple[str, ...]
    sections: tuple[OperatorActivationPacketSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "blocked_runtime_actions": self.blocked_runtime_actions,
            "sections": tuple(section.to_dict() for section in self.sections),
        }


def _derive_state(
    audit_trail: ActivationAuditTrail | None,
    checklist: HandoffChecklistReport | None,
) -> OperatorActivationPacketState:
    events = () if audit_trail is None else audit_trail.events
    has_cancelled = any(event.event_type == ActivationAuditEventType.ACTIVATION_CANCELLED for event in events)
    has_blocked = any(event.event_type == ActivationAuditEventType.GATE_BLOCKED for event in events)
    has_deferred = any(event.event_type == ActivationAuditEventType.ACTIVATION_DEFERRED for event in events)
    has_approved = any(event.event_type == ActivationAuditEventType.OPERATOR_APPROVED for event in events)

    if has_cancelled:
        return OperatorActivationPacketState.CANCELLED
    if checklist is not None and checklist.overall_status == "blocked":
        return OperatorActivationPacketState.BLOCKED
    if has_blocked:
        return OperatorActivationPacketState.BLOCKED
    if has_approved:
        return OperatorActivationPacketState.APPROVED_PENDING_RUNTIME_GATE
    if has_deferred:
        return OperatorActivationPacketState.DEFERRED
    if checklist is not None and checklist.overall_status == "ready":
        return OperatorActivationPacketState.READY_FOR_REVIEW
    return OperatorActivationPacketState.DEFERRED


def build_operator_activation_packet(
    *,
    audit_trail: ActivationAuditTrail | None = None,
    checklist: HandoffChecklistReport | None = None,
    blocked_runtime_actions: Iterable[Any] | None = None,
) -> OperatorActivationPacket:
    if audit_trail is not None and not isinstance(audit_trail, ActivationAuditTrail):
        raise ActivationAuditError("audit_trail must be an ActivationAuditTrail or None")
    if checklist is not None and not isinstance(checklist, HandoffChecklistReport):
        raise ActivationAuditError("checklist must be a HandoffChecklistReport or None")

    normalized_actions = _normalize_runtime_actions(blocked_runtime_actions)
    state = _derive_state(audit_trail, checklist)

    audit_events = () if audit_trail is None else audit_trail.events
    checklist_items = () if checklist is None else checklist.items

    sections = (
        OperatorActivationPacketSection.create(
            section_id="audit",
            summary="audit trail present" if audit_events else "audit trail not provided",
            status="present" if audit_events else "missing",
            item_count=len(audit_events),
        ),
        OperatorActivationPacketSection.create(
            section_id="checklist",
            summary=(
                f"handoff checklist is {checklist.overall_status}"
                if checklist is not None
                else "handoff checklist not provided"
            ),
            status=checklist.overall_status if checklist is not None else "missing",
            item_count=len(checklist_items),
        ),
        OperatorActivationPacketSection.create(
            section_id="runtime_gates",
            summary="runtime actions remain blocked pending operator-controlled runtime phase",
            status="blocked",
            item_count=len(normalized_actions),
        ),
    )

    return OperatorActivationPacket(
        state=state,
        blocked_runtime_actions=normalized_actions,
        sections=sections,
    )

"""Append-only audit trail models for orchestration activation decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SECRET_PAIR_RE = re.compile(r"(?i)\b(token|password|api[_-]?key)\b\s*[:=]\s*([^\s,;]+)")
_SECRET_WORD_RE = re.compile(r"(?i)\b(token|password|api[_-]?key)\b")


class ActivationAuditError(ValueError):
    """Raised when activation audit trail payloads are invalid."""


class ActivationAuditEventType(StrEnum):
    ACTIVATION_REQUESTED = "activation_requested"
    PREFLIGHT_CHECKED = "preflight_checked"
    GATE_PASSED = "gate_passed"
    GATE_BLOCKED = "gate_blocked"
    OPERATOR_APPROVED = "operator_approved"
    ACTIVATION_DEFERRED = "activation_deferred"
    ACTIVATION_CANCELLED = "activation_cancelled"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raise ActivationAuditError(f"{field_name} must not be empty")
    normalized = _SLUG_RE.sub("-", raw).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise ActivationAuditError(f"{field_name} must contain slug characters")
    return normalized


def _sanitize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ActivationAuditError(f"{field_name} must not be empty")
    redacted = _SECRET_PAIR_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    if _SECRET_WORD_RE.search(redacted):
        redacted = _SECRET_WORD_RE.sub("[REDACTED]", redacted)
    return redacted


def _normalize_event_type(value: ActivationAuditEventType | str) -> ActivationAuditEventType:
    if isinstance(value, ActivationAuditEventType):
        return value
    normalized = _sanitize_text(value, field_name="event_type").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return ActivationAuditEventType(normalized)
    except ValueError as exc:
        raise ActivationAuditError("unsupported event_type") from exc


def _normalize_refs(values: Iterable[Any], *, field_name: str, slugify: bool = False) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        if slugify:
            item = _normalize_slug(value, field_name=field_name)
        else:
            item = _sanitize_text(value, field_name=field_name, allow_empty=False)
        normalized.append(item)
    return tuple(sorted(dict.fromkeys(normalized)))


@dataclass(frozen=True, slots=True)
class ActivationAuditEvent:
    event_id: str
    event_type: ActivationAuditEventType
    run_id: str
    slice_id: str
    actor: str
    timestamp: str
    decision: str
    reason: str
    evidence_refs: tuple[str, ...]
    changed_files: tuple[str, ...]
    test_refs: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        event_id: Any,
        event_type: ActivationAuditEventType | str,
        run_id: Any,
        slice_id: Any,
        actor: Any,
        timestamp: Any,
        decision: Any,
        reason: Any,
        evidence_refs: Iterable[Any] = (),
        changed_files: Iterable[Any] = (),
        test_refs: Iterable[Any] = (),
    ) -> "ActivationAuditEvent":
        return cls(
            event_id=_normalize_slug(event_id, field_name="event_id"),
            event_type=_normalize_event_type(event_type),
            run_id=_normalize_slug(run_id, field_name="run_id"),
            slice_id=_normalize_slug(slice_id, field_name="slice_id"),
            actor=_normalize_slug(actor, field_name="actor"),
            timestamp=_sanitize_text(timestamp, field_name="timestamp"),
            decision=_sanitize_text(decision, field_name="decision"),
            reason=_sanitize_text(reason, field_name="reason"),
            evidence_refs=_normalize_refs(evidence_refs, field_name="evidence_ref"),
            changed_files=_normalize_refs(changed_files, field_name="changed_file"),
            test_refs=_normalize_refs(test_refs, field_name="test_ref"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "run_id": self.run_id,
            "slice_id": self.slice_id,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "decision": self.decision,
            "reason": self.reason,
            "evidence_refs": self.evidence_refs,
            "changed_files": self.changed_files,
            "test_refs": self.test_refs,
        }


@dataclass(frozen=True, slots=True)
class ActivationAuditTrail:
    events: tuple[ActivationAuditEvent, ...]

    @classmethod
    def create(cls, events: Iterable[ActivationAuditEvent] = ()) -> "ActivationAuditTrail":
        normalized = tuple(events)
        if any(not isinstance(item, ActivationAuditEvent) for item in normalized):
            raise ActivationAuditError("events must contain ActivationAuditEvent items")
        _validate_append_only_order(normalized)
        return cls(events=normalized)

    def append_event(self, event: ActivationAuditEvent) -> "ActivationAuditTrail":
        if not isinstance(event, ActivationAuditEvent):
            raise ActivationAuditError("event must be an ActivationAuditEvent")
        return ActivationAuditTrail.create(self.events + (event,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": tuple(event.to_dict() for event in self.events),
        }


def _validate_append_only_order(events: tuple[ActivationAuditEvent, ...]) -> None:
    seen_ids: set[str] = set()
    last_timestamp = ""
    for event in events:
        if event.event_id in seen_ids:
            raise ActivationAuditError("event_id must be unique within audit trail")
        if last_timestamp and event.timestamp < last_timestamp:
            raise ActivationAuditError("events must be appended in non-decreasing timestamp order")
        seen_ids.add(event.event_id)
        last_timestamp = event.timestamp

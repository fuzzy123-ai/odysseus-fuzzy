"""Small backend contract for a heartbeat coordinator lifecycle model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_MAX_TIMESTAMP = 40
_STATUS_COMPATIBLE = {"watching", "dispatching", "waiting", "blocked", "stale", "completed", "failed", "paused"}
_DECISIONS = {"read", "dispatch", "wait", "resolve", "stop", "noop"}
_DISPATCH_ACTIONS = {"send", "wait", "resolve", "stop"}
_MODES = {"observe", "assist", "manual_stop_pending"}


class HeartbeatCoordinatorError(ValueError):
    """Raised when heartbeat coordinator payloads are invalid or unsafe."""


class HeartbeatStatus(StrEnum):
    WATCHING = "watching"
    DISPATCHING = "dispatching"
    WAITING = "waiting"
    BLOCKED = "blocked"
    STALE = "stale"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class HeartbeatDecision(StrEnum):
    READ = "read"
    DISPATCH = "dispatch"
    WAIT = "wait"
    RESOLVE = "resolve"
    STOP = "stop"
    NOOP = "noop"


class HeartbeatMode(StrEnum):
    OBSERVE = "observe"
    ASSIST = "assist"
    MANUAL_STOP_PENDING = "manual_stop_pending"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise HeartbeatCoordinatorError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise HeartbeatCoordinatorError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise HeartbeatCoordinatorError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HeartbeatCoordinatorError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_text_list(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value, field_name=field_name, allow_empty=True)
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
        raise HeartbeatCoordinatorError(f"{field_name} must not be empty")
    if len(text) > _MAX_TIMESTAMP or not _TIMESTAMP_RE.fullmatch(text):
        raise HeartbeatCoordinatorError(f"{field_name} must be an ISO-8601 UTC timestamp")
    return text


def _normalize_status(value: Any, *, field_name: str) -> HeartbeatStatus:
    normalized = _normalize_slug(value, field_name=field_name)
    if normalized not in _STATUS_COMPATIBLE:
        raise HeartbeatCoordinatorError(f"{field_name} is not a supported heartbeat status")
    return HeartbeatStatus(normalized)


def _normalize_decision(value: Any, *, field_name: str) -> HeartbeatDecision:
    normalized = _normalize_slug(value, field_name=field_name)
    if normalized not in _DECISIONS:
        raise HeartbeatCoordinatorError(f"{field_name} is not a supported heartbeat decision")
    return HeartbeatDecision(normalized)


def _normalize_dispatch_action(value: Any) -> str:
    normalized = _normalize_slug(value, field_name="dispatch_action")
    if normalized not in _DISPATCH_ACTIONS:
        raise HeartbeatCoordinatorError("dispatch_action is not supported")
    return normalized


def _normalize_mode(value: Any) -> HeartbeatMode:
    normalized = _normalize_slug(value, field_name="mode")
    normalized = normalized.replace("-", "_")
    if normalized not in _MODES:
        raise HeartbeatCoordinatorError("mode is not supported")
    return HeartbeatMode(normalized)


@dataclass(frozen=True, slots=True)
class HeartbeatDispatch:
    target_thread_id: str
    agent_run_id: str
    action: str
    summary: str

    @classmethod
    def create(
        cls,
        *,
        target_thread_id: Any,
        agent_run_id: Any,
        action: Any,
        summary: Any,
    ) -> "HeartbeatDispatch":
        thread_id = str(target_thread_id or "").strip()
        if not thread_id:
            raise HeartbeatCoordinatorError("target_thread_id must not be empty")
        return cls(
            target_thread_id=thread_id,
            agent_run_id=_normalize_slug(agent_run_id, field_name="agent_run_id"),
            action=_normalize_dispatch_action(action),
            summary=_normalize_text(summary, field_name="summary", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class HeartbeatCoordinatorState:
    heartbeat_id: str
    plan_id: str
    coordinator_run_id: str
    agent_run_ids: tuple[str, ...]
    thread_refs: tuple[str, ...]
    interval_seconds: int
    mode: HeartbeatMode
    status: HeartbeatStatus
    last_decision: HeartbeatDecision | None
    dispatches: tuple[HeartbeatDispatch, ...]
    evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    last_tick_at: str
    next_tick_at: str
    stop_reason: str

    @classmethod
    def create(
        cls,
        *,
        heartbeat_id: Any,
        plan_id: Any,
        coordinator_run_id: Any,
        agent_run_ids: Iterable[Any],
        thread_refs: Iterable[Any],
        interval_seconds: Any,
        status: HeartbeatStatus | str,
        mode: HeartbeatMode | str = HeartbeatMode.OBSERVE,
        last_decision: HeartbeatDecision | str | None = None,
        dispatches: Iterable[HeartbeatDispatch] = (),
        last_tick_at: Any,
        next_tick_at: Any,
        stop_reason: Any,
        evidence: Iterable[Any] = (),
        warnings: Iterable[Any] = (),
        errors: Iterable[Any] = (),
    ) -> "HeartbeatCoordinatorState":
        try:
            interval = int(interval_seconds)
        except (TypeError, ValueError):
            raise HeartbeatCoordinatorError("interval_seconds must be an int") from None
        if interval <= 0:
            raise HeartbeatCoordinatorError("interval_seconds must be > 0")
        normalized_mode = mode if isinstance(mode, HeartbeatMode) else _normalize_mode(mode)
        normalized_status = status if isinstance(status, HeartbeatStatus) else _normalize_status(status, field_name="status")
        normalized_last_decision = (
            None
            if last_decision in (None, "")
            else last_decision
            if isinstance(last_decision, HeartbeatDecision)
            else _normalize_decision(last_decision, field_name="last_decision")
        )
        normalized_dispatches = tuple(dispatches)
        if any(not isinstance(dispatch, HeartbeatDispatch) for dispatch in normalized_dispatches):
            raise HeartbeatCoordinatorError("dispatches must contain HeartbeatDispatch items")
        normalized_evidence = _normalize_text_list(evidence, field_name="evidence")
        normalized_warnings = _normalize_text_list(warnings, field_name="warnings")
        normalized_errors = _normalize_text_list(errors, field_name="errors")
        normalized_stop_reason = _normalize_text(stop_reason, field_name="stop_reason", allow_empty=True)
        if normalized_status == HeartbeatStatus.DISPATCHING and not normalized_dispatches:
            raise HeartbeatCoordinatorError("dispatching heartbeats require at least one dispatch")
        if normalized_status in {HeartbeatStatus.COMPLETED, HeartbeatStatus.BLOCKED, HeartbeatStatus.PAUSED} and not (
            normalized_stop_reason or normalized_evidence or normalized_warnings or normalized_errors
        ):
            raise HeartbeatCoordinatorError("terminal or paused heartbeats require stop_reason or evidence")
        if normalized_status == HeartbeatStatus.FAILED and not normalized_errors:
            raise HeartbeatCoordinatorError("failed heartbeats require at least one error")
        if normalized_status == HeartbeatStatus.STALE and not (normalized_warnings or normalized_evidence):
            raise HeartbeatCoordinatorError("stale heartbeats require warning or evidence")
        return cls(
            heartbeat_id=_normalize_slug(heartbeat_id, field_name="heartbeat_id"),
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            coordinator_run_id=_normalize_slug(coordinator_run_id, field_name="coordinator_run_id"),
            agent_run_ids=tuple(sorted({_normalize_slug(v, field_name="agent_run_id") for v in agent_run_ids})),
            thread_refs=tuple(sorted({str(v).strip() for v in thread_refs if str(v).strip()})),
            interval_seconds=interval,
            mode=normalized_mode,
            status=normalized_status,
            last_decision=normalized_last_decision,
            dispatches=normalized_dispatches,
            evidence=normalized_evidence,
            warnings=normalized_warnings,
            errors=normalized_errors,
            last_tick_at=_normalize_timestamp(last_tick_at, field_name="last_tick_at", allow_empty=True),
            next_tick_at=_normalize_timestamp(next_tick_at, field_name="next_tick_at", allow_empty=True),
            stop_reason=normalized_stop_reason,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "heartbeat_id": self.heartbeat_id,
            "plan_id": self.plan_id,
            "coordinator_run_id": self.coordinator_run_id,
            "mode": self.mode.value,
            "status": self.status.value,
            "last_decision": self.last_decision.value if self.last_decision else "",
            "agent_run_count": len(self.agent_run_ids),
            "thread_ref_count": len(self.thread_refs),
            "dispatch_count": len(self.dispatches),
            "evidence_count": len(self.evidence),
            "warning_count": len(self.warnings),
            "error_count": len(self.errors),
            "interval_seconds": self.interval_seconds,
            "has_stop_reason": bool(self.stop_reason),
        }


@dataclass(frozen=True, slots=True)
class HeartbeatTick:
    tick_id: str
    heartbeat_id: str
    decision: HeartbeatDecision
    dispatches: tuple[HeartbeatDispatch, ...]
    evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        tick_id: Any,
        heartbeat_id: Any,
        decision: HeartbeatDecision | str,
        dispatches: Iterable[HeartbeatDispatch],
        evidence: Iterable[Any],
        warnings: Iterable[Any],
        errors: Iterable[Any],
    ) -> "HeartbeatTick":
        normalized_decision = decision if isinstance(decision, HeartbeatDecision) else _normalize_decision(decision, field_name="decision")
        normalized_dispatches = tuple(dispatches)
        if any(not isinstance(dispatch, HeartbeatDispatch) for dispatch in normalized_dispatches):
            raise HeartbeatCoordinatorError("dispatches must contain HeartbeatDispatch items")
        normalized_evidence = _normalize_text_list(evidence, field_name="evidence")
        normalized_warnings = _normalize_text_list(warnings, field_name="warnings")
        normalized_errors = _normalize_text_list(errors, field_name="errors")
        if normalized_decision == HeartbeatDecision.DISPATCH and not normalized_dispatches:
            raise HeartbeatCoordinatorError("dispatch decision requires at least one dispatch")
        if not (normalized_dispatches or normalized_evidence or normalized_warnings or normalized_errors):
            raise HeartbeatCoordinatorError("tick must carry dispatches, evidence, warnings, or errors")
        return cls(
            tick_id=_normalize_slug(tick_id, field_name="tick_id"),
            heartbeat_id=_normalize_slug(heartbeat_id, field_name="heartbeat_id"),
            decision=normalized_decision,
            dispatches=normalized_dispatches,
            evidence=normalized_evidence,
            warnings=normalized_warnings,
            errors=normalized_errors,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "tick_id": self.tick_id,
            "heartbeat_id": self.heartbeat_id,
            "decision": self.decision.value,
            "dispatch_count": len(self.dispatches),
            "evidence_count": len(self.evidence),
            "warning_count": len(self.warnings),
            "error_count": len(self.errors),
        }

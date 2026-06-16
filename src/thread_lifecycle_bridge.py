"""Small backend contract for a thread lifecycle bridge model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Any


_MAX_ID = 80
_MAX_TEXT = 160
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_THREAD_STATUS_COMPATIBLE = {"unknown", "idle", "running", "completed", "blocked", "stale", "ambiguous"}
_DISPATCH_ACTIONS = {"send", "wait", "blocked", "resolve", "noop"}
_ALLOWED_ACTIONS = {"send", "resolve", "read"}
_HANDOFF_STATUS = {
    "none",
    "waiting_for_agent",
    "waiting_for_charlie",
    "ready_for_handoff",
    "resolved",
    "ambiguous",
}
_DISPATCH_INTENTS = {"read_only", "send_instruction", "resolve_handoff", "stop"}
_MAX_TIMESTAMP = 40
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class ThreadLifecycleBridgeError(ValueError):
    """Raised when thread lifecycle bridge payloads are invalid or unsafe."""


class ThreadStatus(StrEnum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    STALE = "stale"
    AMBIGUOUS = "ambiguous"


class DispatchAction(StrEnum):
    SEND = "send"
    WAIT = "wait"
    BLOCKED = "blocked"
    RESOLVE = "resolve"
    NOOP = "noop"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise ThreadLifecycleBridgeError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise ThreadLifecycleBridgeError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise ThreadLifecycleBridgeError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ThreadLifecycleBridgeError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_thread_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ThreadLifecycleBridgeError("thread_id must not be empty")
    if len(text) > 120:
        raise ThreadLifecycleBridgeError("thread_id exceeds max length 120")
    return text


def _normalize_thread_status(value: Any, *, field_name: str) -> ThreadStatus:
    normalized = _normalize_slug(value, field_name=field_name)
    if normalized not in _THREAD_STATUS_COMPATIBLE:
        raise ThreadLifecycleBridgeError(f"{field_name} is not a supported thread status")
    return ThreadStatus(normalized)


def _normalize_dispatch_action(value: Any, *, field_name: str) -> str:
    normalized = _normalize_slug(value, field_name=field_name)
    if normalized not in _DISPATCH_ACTIONS:
        raise ThreadLifecycleBridgeError(f"{field_name} is not a supported dispatch action")
    return normalized


def _normalize_allowed_action(value: Any) -> str:
    normalized = _normalize_slug(value, field_name="allowed_action")
    if normalized not in _ALLOWED_ACTIONS:
        raise ThreadLifecycleBridgeError("allowed_action is not supported")
    return normalized


def _normalize_handoff_status(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    normalized = re.sub(r"_{2,}", "_", re.sub(r"[^a-z0-9_]+", "_", normalized)).strip("_")
    if not normalized:
        raise ThreadLifecycleBridgeError("handoff_status must not be empty")
    if normalized not in _HANDOFF_STATUS:
        raise ThreadLifecycleBridgeError("handoff_status is not supported")
    return normalized


def _normalize_dispatch_intent(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "read_only"
    normalized = normalized.replace("-", "_")
    normalized = re.sub(r"_{2,}", "_", re.sub(r"[^a-z0-9_]+", "_", normalized)).strip("_")
    if normalized not in _DISPATCH_INTENTS:
        raise ThreadLifecycleBridgeError("dispatch_intent is not supported")
    return normalized


def _normalize_timestamp(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > _MAX_TIMESTAMP or not _TIMESTAMP_RE.fullmatch(text):
        raise ThreadLifecycleBridgeError(f"{field_name} must be an ISO-8601 UTC timestamp")
    return text


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


@dataclass(frozen=True, slots=True)
class ThreadRef:
    thread_id: str
    agent_id: str
    agent_run_id: str
    plan_id: str
    node_id: str

    @classmethod
    def create(
        cls,
        *,
        thread_id: Any,
        agent_id: Any,
        agent_run_id: Any,
        plan_id: Any,
        node_id: Any,
    ) -> "ThreadRef":
        return cls(
            thread_id=_normalize_thread_id(thread_id),
            agent_id=_normalize_slug(agent_id, field_name="agent_id"),
            agent_run_id=_normalize_slug(agent_run_id, field_name="agent_run_id"),
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            node_id=_normalize_slug(node_id, field_name="node_id"),
        )


@dataclass(frozen=True, slots=True)
class ThreadLifecycleSnapshot:
    thread_ref: ThreadRef
    thread_status: ThreadStatus
    last_seen_turn: int
    handoff_status: str
    dispatch_intent: str = "read_only"
    acknowledged_at: str = ""
    resolved_at: str = ""

    @classmethod
    def create(
        cls,
        *,
        thread_ref: ThreadRef,
        thread_status: ThreadStatus | str,
        last_seen_turn: Any,
        handoff_status: Any,
        dispatch_intent: Any = "read_only",
        acknowledged_at: Any = "",
        resolved_at: Any = "",
    ) -> "ThreadLifecycleSnapshot":
        if not isinstance(thread_ref, ThreadRef):
            raise ThreadLifecycleBridgeError("thread_ref must be a ThreadRef")
        try:
            turn = int(last_seen_turn)
        except (TypeError, ValueError):
            raise ThreadLifecycleBridgeError("last_seen_turn must be an int") from None
        if turn < 0:
            raise ThreadLifecycleBridgeError("last_seen_turn must be >= 0")
        normalized_thread_status = thread_status if isinstance(thread_status, ThreadStatus) else _normalize_thread_status(thread_status, field_name="thread_status")
        normalized_handoff_status = _normalize_handoff_status(handoff_status)
        normalized_dispatch_intent = _normalize_dispatch_intent(dispatch_intent)
        normalized_acknowledged_at = _normalize_timestamp(acknowledged_at, field_name="acknowledged_at")
        normalized_resolved_at = _normalize_timestamp(resolved_at, field_name="resolved_at")
        if normalized_resolved_at and normalized_acknowledged_at:
            if _parse_timestamp(normalized_resolved_at) < _parse_timestamp(normalized_acknowledged_at):
                raise ThreadLifecycleBridgeError("resolved_at must not be before acknowledged_at")
        if normalized_thread_status == ThreadStatus.AMBIGUOUS and normalized_dispatch_intent == "send_instruction":
            raise ThreadLifecycleBridgeError("ambiguous threads cannot carry send_instruction intent")
        return cls(
            thread_ref=thread_ref,
            thread_status=normalized_thread_status,
            last_seen_turn=turn,
            handoff_status=normalized_handoff_status,
            dispatch_intent=normalized_dispatch_intent,
            acknowledged_at=normalized_acknowledged_at,
            resolved_at=normalized_resolved_at,
        )


@dataclass(frozen=True, slots=True)
class ThreadDispatchRequest:
    thread_ref: ThreadRef
    expected_agent_id: str
    expected_agent_run_id: str
    expected_node_id: str
    prompt_summary: str
    allowed_action: str

    @classmethod
    def create(
        cls,
        *,
        thread_ref: ThreadRef,
        expected_agent_id: Any,
        expected_agent_run_id: Any,
        expected_node_id: Any,
        prompt_summary: Any,
        allowed_action: Any,
    ) -> "ThreadDispatchRequest":
        if not isinstance(thread_ref, ThreadRef):
            raise ThreadLifecycleBridgeError("thread_ref must be a ThreadRef")
        return cls(
            thread_ref=thread_ref,
            expected_agent_id=_normalize_slug(expected_agent_id, field_name="expected_agent_id"),
            expected_agent_run_id=_normalize_slug(expected_agent_run_id, field_name="expected_agent_run_id"),
            expected_node_id=_normalize_slug(expected_node_id, field_name="expected_node_id"),
            prompt_summary=_normalize_text(prompt_summary, field_name="prompt_summary", allow_empty=False),
            allowed_action=_normalize_allowed_action(allowed_action),
        )


@dataclass(frozen=True, slots=True)
class ThreadDispatchDecision:
    action: DispatchAction
    allowed: bool
    reason: str
    required_user_action: str
    warnings: tuple[str, ...]

    @classmethod
    def decide(
        cls,
        *,
        snapshot: ThreadLifecycleSnapshot | None,
        request: ThreadDispatchRequest,
    ) -> "ThreadDispatchDecision":
        if not isinstance(request, ThreadDispatchRequest):
            raise ThreadLifecycleBridgeError("request must be a ThreadDispatchRequest")
        if snapshot is None:
            return cls(
                action=DispatchAction.BLOCKED,
                allowed=False,
                reason="missing_thread_snapshot",
                required_user_action="resolve_thread_ref",
                warnings=(),
            )
        if not isinstance(snapshot, ThreadLifecycleSnapshot):
            raise ThreadLifecycleBridgeError("snapshot must be a ThreadLifecycleSnapshot or None")

        warnings: list[str] = []
        if snapshot.thread_ref.thread_id != request.thread_ref.thread_id:
            return cls(
                action=DispatchAction.BLOCKED,
                allowed=False,
                reason="thread_id_mismatch",
                required_user_action="resolve_thread_ref",
                warnings=(),
            )

        if snapshot.thread_status == ThreadStatus.AMBIGUOUS:
            return cls(
                action=DispatchAction.BLOCKED,
                allowed=False,
                reason="ambiguous_thread",
                required_user_action="resolve_thread_ambiguity",
                warnings=(),
            )

        if snapshot.thread_ref.agent_id != request.expected_agent_id:
            return cls(
                action=DispatchAction.BLOCKED,
                allowed=False,
                reason="agent_mismatch",
                required_user_action="resolve_expected_agent",
                warnings=(),
            )

        if snapshot.thread_ref.agent_run_id != request.expected_agent_run_id:
            return cls(
                action=DispatchAction.BLOCKED,
                allowed=False,
                reason="agent_run_mismatch",
                required_user_action="resolve_expected_run",
                warnings=(),
            )

        if snapshot.thread_ref.node_id != request.expected_node_id:
            return cls(
                action=DispatchAction.BLOCKED,
                allowed=False,
                reason="node_mismatch",
                required_user_action="resolve_expected_node",
                warnings=(),
            )

        if snapshot.thread_status == ThreadStatus.RUNNING:
            return cls(
                action=DispatchAction.WAIT,
                allowed=False,
                reason="thread_already_running",
                required_user_action="wait_for_turn_completion",
                warnings=(),
            )

        if snapshot.thread_status == ThreadStatus.IDLE:
            if request.allowed_action != "send":
                return cls(
                    action=DispatchAction.NOOP,
                    allowed=False,
                    reason="idle_thread_without_send_permission",
                    required_user_action="expand_allowed_action",
                    warnings=(),
                )
            return cls(
                action=DispatchAction.SEND,
                allowed=True,
                reason="idle_thread_ready_for_dispatch",
                required_user_action="",
                warnings=(),
            )

        if snapshot.thread_status in {ThreadStatus.COMPLETED, ThreadStatus.BLOCKED, ThreadStatus.STALE}:
            reason = "thread_handoff_ready" if snapshot.handoff_status in {"ready_for_handoff", "resolved"} else "thread_needs_resolution"
            required = "advance_next_slice" if snapshot.handoff_status in {"ready_for_handoff", "resolved"} else "resolve_thread_status"
            return cls(
                action=DispatchAction.RESOLVE,
                allowed=False,
                reason=reason,
                required_user_action=required,
                warnings=tuple(warnings),
            )

        return cls(
            action=DispatchAction.NOOP,
            allowed=False,
            reason="thread_status_unknown",
            required_user_action="inspect_thread_state",
            warnings=tuple(warnings),
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "required_user_action": self.required_user_action,
            "warning_count": len(self.warnings),
            "warnings": self.warnings,
        }

"""Small backend contract for orchestration status snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_MAX_TIMESTAMP = 40
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class OrchestrationStatusError(ValueError):
    """Raised when an orchestration snapshot payload is invalid or unsafe."""


class OrchestrationHealth(StrEnum):
    LOADING = "loading"
    HEALTHY = "healthy"
    WAITING = "waiting"
    BLOCKED = "blocked"
    FAILED = "failed"
    STALE = "stale"
    COMPLETED = "completed"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise OrchestrationStatusError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise OrchestrationStatusError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise OrchestrationStatusError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise OrchestrationStatusError(f"{field_name} must not be empty")
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
        raise OrchestrationStatusError(f"{field_name} must not be empty")
    if len(text) > _MAX_TIMESTAMP or not _TIMESTAMP_RE.fullmatch(text):
        raise OrchestrationStatusError(f"{field_name} must be an ISO-8601 UTC timestamp")
    return text


def _normalize_health(value: Any, *, field_name: str) -> OrchestrationHealth:
    if isinstance(value, OrchestrationHealth):
        return value
    normalized = _normalize_slug(value, field_name=field_name)
    try:
        return OrchestrationHealth(normalized)
    except ValueError as exc:
        raise OrchestrationStatusError(f"{field_name} is not a supported orchestration status") from exc


@dataclass(frozen=True, slots=True)
class AgentPathSummary:
    agent_id: str
    role_id: str
    status: OrchestrationHealth
    progress_percent: int
    active_slice_id: str

    @classmethod
    def create(
        cls,
        *,
        agent_id: Any,
        role_id: Any,
        status: OrchestrationHealth | str,
        progress_percent: Any,
        active_slice_id: Any,
    ) -> "AgentPathSummary":
        try:
            progress = int(progress_percent)
        except (TypeError, ValueError):
            raise OrchestrationStatusError("progress_percent must be an int") from None
        if progress < 0 or progress > 100:
            raise OrchestrationStatusError("progress_percent must be between 0 and 100")
        return cls(
            agent_id=_normalize_slug(agent_id, field_name="agent_id"),
            role_id=_normalize_slug(role_id, field_name="role_id"),
            status=_normalize_health(status, field_name="agent_path_status"),
            progress_percent=progress,
            active_slice_id=_normalize_slug(active_slice_id, field_name="active_slice_id"),
        )


@dataclass(frozen=True, slots=True)
class DashboardItem:
    item_id: str
    title: str
    status: OrchestrationHealth
    summary: str

    @classmethod
    def create(
        cls,
        *,
        item_id: Any,
        title: Any,
        status: OrchestrationHealth | str,
        summary: Any,
    ) -> "DashboardItem":
        return cls(
            item_id=_normalize_slug(item_id, field_name="item_id"),
            title=_normalize_text(title, field_name="title", allow_empty=False),
            status=_normalize_health(status, field_name="item_status"),
            summary=_normalize_text(summary, field_name="summary", allow_empty=False, limit=_MAX_LONG_TEXT),
        )


@dataclass(frozen=True, slots=True)
class NextAction:
    owner: str
    action: str
    summary: str

    @classmethod
    def create(
        cls,
        *,
        owner: Any,
        action: Any,
        summary: Any,
    ) -> "NextAction":
        return cls(
            owner=_normalize_slug(owner, field_name="owner"),
            action=_normalize_text(action, field_name="action", allow_empty=False),
            summary=_normalize_text(summary, field_name="summary", allow_empty=False, limit=_MAX_LONG_TEXT),
        )


@dataclass(frozen=True, slots=True)
class OrchestrationStatusSnapshot:
    dashboard_id: str
    plan_id: str
    plan_status: OrchestrationHealth
    overall_progress_percent: int
    agent_paths: tuple[AgentPathSummary, ...]
    heartbeat_status: OrchestrationHealth
    quality_gate_summary: str
    blocking_items: tuple[DashboardItem, ...]
    next_actions: tuple[NextAction, ...]
    last_updated_at: str
    evidence_refs: tuple[str, ...]
    warnings: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        dashboard_id: Any,
        plan_id: Any,
        plan_status: OrchestrationHealth | str,
        overall_progress_percent: Any,
        agent_paths: Iterable[AgentPathSummary],
        heartbeat_status: OrchestrationHealth | str,
        quality_gate_summary: Any,
        blocking_items: Iterable[DashboardItem],
        next_actions: Iterable[NextAction],
        last_updated_at: Any,
        evidence_refs: Iterable[Any],
        warnings: Iterable[Any] = (),
    ) -> "OrchestrationStatusSnapshot":
        try:
            progress = int(overall_progress_percent)
        except (TypeError, ValueError):
            raise OrchestrationStatusError("overall_progress_percent must be an int") from None
        if progress < 0 or progress > 100:
            raise OrchestrationStatusError("overall_progress_percent must be between 0 and 100")

        normalized_agent_paths = tuple(agent_paths)
        if any(not isinstance(path, AgentPathSummary) for path in normalized_agent_paths):
            raise OrchestrationStatusError("agent_paths must contain AgentPathSummary items")
        normalized_blocking_items = tuple(blocking_items)
        if any(not isinstance(item, DashboardItem) for item in normalized_blocking_items):
            raise OrchestrationStatusError("blocking_items must contain DashboardItem items")
        normalized_next_actions = tuple(next_actions)
        if any(not isinstance(item, NextAction) for item in normalized_next_actions):
            raise OrchestrationStatusError("next_actions must contain NextAction items")

        normalized_plan_status = _normalize_health(plan_status, field_name="plan_status")
        normalized_heartbeat_status = _normalize_health(heartbeat_status, field_name="heartbeat_status")
        normalized_evidence_refs = _normalize_text_list(evidence_refs, field_name="evidence_ref")
        normalized_warnings = _normalize_text_list(warnings, field_name="warnings")

        if normalized_plan_status in {OrchestrationHealth.BLOCKED, OrchestrationHealth.FAILED} and not (
            normalized_blocking_items or normalized_evidence_refs
        ):
            raise OrchestrationStatusError("blocked or failed snapshots require blocking_items or evidence_refs")
        if normalized_plan_status == OrchestrationHealth.COMPLETED and progress < 100 and not normalized_evidence_refs:
            raise OrchestrationStatusError("completed snapshots require 100 percent progress or completion evidence")
        if normalized_plan_status == OrchestrationHealth.STALE and not (normalized_warnings or normalized_evidence_refs):
            raise OrchestrationStatusError("stale snapshots require warnings or evidence_refs")
        if normalized_plan_status == OrchestrationHealth.BLOCKED and not normalized_blocking_items:
            raise OrchestrationStatusError("blocked snapshots require at least one blocking item")

        return cls(
            dashboard_id=_normalize_slug(dashboard_id, field_name="dashboard_id"),
            plan_id=_normalize_slug(plan_id, field_name="plan_id"),
            plan_status=normalized_plan_status,
            overall_progress_percent=progress,
            agent_paths=tuple(sorted(normalized_agent_paths, key=lambda path: path.agent_id)),
            heartbeat_status=normalized_heartbeat_status,
            quality_gate_summary=_normalize_text(
                quality_gate_summary,
                field_name="quality_gate_summary",
                allow_empty=False,
                limit=_MAX_LONG_TEXT,
            ),
            blocking_items=tuple(sorted(normalized_blocking_items, key=lambda item: item.item_id)),
            next_actions=tuple(sorted(normalized_next_actions, key=lambda action: (action.owner, action.action))),
            last_updated_at=_normalize_timestamp(last_updated_at, field_name="last_updated_at", allow_empty=False),
            evidence_refs=normalized_evidence_refs,
            warnings=normalized_warnings,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "dashboard_id": self.dashboard_id,
            "plan_id": self.plan_id,
            "plan_status": self.plan_status.value,
            "heartbeat_status": self.heartbeat_status.value,
            "overall_progress_percent": self.overall_progress_percent,
            "agent_path_count": len(self.agent_paths),
            "blocking_item_count": len(self.blocking_items),
            "next_action_count": len(self.next_actions),
            "evidence_ref_count": len(self.evidence_refs),
            "warning_count": len(self.warnings),
            "agent_paths": tuple(
                {
                    "agent_id": path.agent_id,
                    "role_id": path.role_id,
                    "status": path.status.value,
                    "progress_percent": path.progress_percent,
                    "active_slice_id": path.active_slice_id,
                }
                for path in self.agent_paths
            ),
            "blocking_items": tuple(
                {
                    "item_id": item.item_id,
                    "status": item.status.value,
                }
                for item in self.blocking_items
            ),
            "next_actions": tuple(
                {
                    "owner": action.owner,
                    "action": action.action,
                }
                for action in self.next_actions
            ),
        }

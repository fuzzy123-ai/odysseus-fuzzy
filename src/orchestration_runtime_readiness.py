"""Dry-run readiness model for AUTO orchestration runtime hooks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Iterable


_MAX_ID = 80
_MAX_TEXT = 180
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")


class RuntimeReadinessError(ValueError):
    """Raised when orchestration runtime readiness payloads are invalid."""


class ReadinessCategory(StrEnum):
    THREADING = "threading"
    GIT = "git"
    TESTING = "testing"
    SCHEDULER = "scheduler"
    QUALITY = "quality"
    DASHBOARD = "dashboard"


class ReadinessStatus(StrEnum):
    READY = "ready"
    DRY_RUN_ONLY = "dry_run_only"
    REQUIRES_OPERATOR = "requires_operator"
    BLOCKED = "blocked"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise RuntimeReadinessError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise RuntimeReadinessError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise RuntimeReadinessError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise RuntimeReadinessError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_category(value: ReadinessCategory | str) -> ReadinessCategory:
    if isinstance(value, ReadinessCategory):
        return value
    normalized = _normalize_slug(value, field_name="category")
    try:
        return ReadinessCategory(normalized)
    except ValueError as exc:
        raise RuntimeReadinessError("unsupported readiness category") from exc


def _normalize_status(value: ReadinessStatus | str) -> ReadinessStatus:
    if isinstance(value, ReadinessStatus):
        return value
    raw = str(value or "").strip().lower()
    alias_map = {
        "ready": ReadinessStatus.READY,
        "dry_run_only": ReadinessStatus.DRY_RUN_ONLY,
        "dry-run-only": ReadinessStatus.DRY_RUN_ONLY,
        "requires_operator": ReadinessStatus.REQUIRES_OPERATOR,
        "requires-operator": ReadinessStatus.REQUIRES_OPERATOR,
        "blocked": ReadinessStatus.BLOCKED,
    }
    if raw in alias_map:
        return alias_map[raw]
    normalized = _normalize_slug(value, field_name="status")
    try:
        return ReadinessStatus(normalized)
    except ValueError as exc:
        raise RuntimeReadinessError("unsupported readiness status") from exc


@dataclass(frozen=True, slots=True)
class RuntimeCapability:
    capability_id: str
    category: ReadinessCategory
    status: ReadinessStatus
    live_hook: bool
    summary: str

    @classmethod
    def create(
        cls,
        *,
        capability_id: Any,
        category: ReadinessCategory | str,
        status: ReadinessStatus | str,
        live_hook: bool,
        summary: Any,
    ) -> "RuntimeCapability":
        normalized_status = _normalize_status(status)
        normalized_live_hook = bool(live_hook)
        if normalized_status != ReadinessStatus.READY and normalized_live_hook:
            raise RuntimeReadinessError("non-ready capabilities must not claim live_hook")
        return cls(
            capability_id=_normalize_slug(capability_id, field_name="capability_id"),
            category=_normalize_category(category),
            status=normalized_status,
            live_hook=normalized_live_hook,
            summary=_normalize_text(summary, field_name="summary", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class RuntimeGap:
    gap_id: str
    category: ReadinessCategory
    status: ReadinessStatus
    summary: str
    next_safe_action: str

    @classmethod
    def create(
        cls,
        *,
        gap_id: Any,
        category: ReadinessCategory | str,
        status: ReadinessStatus | str,
        summary: Any,
        next_safe_action: Any,
    ) -> "RuntimeGap":
        normalized_status = _normalize_status(status)
        if normalized_status == ReadinessStatus.READY:
            raise RuntimeReadinessError("runtime gaps must not use ready status")
        return cls(
            gap_id=_normalize_slug(gap_id, field_name="gap_id"),
            category=_normalize_category(category),
            status=normalized_status,
            summary=_normalize_text(summary, field_name="summary", allow_empty=False),
            next_safe_action=_normalize_text(next_safe_action, field_name="next_safe_action", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class RuntimeReadinessReport:
    capabilities: tuple[RuntimeCapability, ...]
    gaps: tuple[RuntimeGap, ...]
    ok: bool
    blocked: bool
    open_gap_count: int
    next_safe_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked": self.blocked,
            "open_gap_count": self.open_gap_count,
            "next_safe_action": self.next_safe_action,
            "capabilities": tuple(
                {
                    "capability_id": capability.capability_id,
                    "category": capability.category.value,
                    "status": capability.status.value,
                    "live_hook": capability.live_hook,
                    "summary": capability.summary,
                }
                for capability in self.capabilities
            ),
            "gaps": tuple(
                {
                    "gap_id": gap.gap_id,
                    "category": gap.category.value,
                    "status": gap.status.value,
                    "summary": gap.summary,
                    "next_safe_action": gap.next_safe_action,
                }
                for gap in self.gaps
            ),
        }


def build_runtime_readiness_report(
    *,
    capabilities: Iterable[RuntimeCapability],
    gaps: Iterable[RuntimeGap],
) -> RuntimeReadinessReport:
    normalized_capabilities = tuple(capabilities)
    normalized_gaps = tuple(gaps)
    if any(not isinstance(capability, RuntimeCapability) for capability in normalized_capabilities):
        raise RuntimeReadinessError("capabilities must contain RuntimeCapability items")
    if any(not isinstance(gap, RuntimeGap) for gap in normalized_gaps):
        raise RuntimeReadinessError("gaps must contain RuntimeGap items")

    sorted_capabilities = tuple(sorted(normalized_capabilities, key=lambda item: item.capability_id))
    sorted_gaps = tuple(sorted(normalized_gaps, key=lambda item: item.gap_id))
    blocked = any(gap.status == ReadinessStatus.BLOCKED for gap in sorted_gaps)
    ok = not sorted_gaps and all(capability.status == ReadinessStatus.READY for capability in sorted_capabilities)
    next_safe_action = _select_next_safe_action(sorted_gaps)
    return RuntimeReadinessReport(
        capabilities=sorted_capabilities,
        gaps=sorted_gaps,
        ok=ok,
        blocked=blocked,
        open_gap_count=len(sorted_gaps),
        next_safe_action=next_safe_action,
    )


def _select_next_safe_action(gaps: tuple[RuntimeGap, ...]) -> str:
    if not gaps:
        return "AUTO runtime hooks are live-ready for operator-approved orchestration."
    priority = {
        "scheduler-live-execution": 0,
        "thread-send-live-hook": 1,
        "test-runner-operator-gate": 2,
        "git-runner-operator-gate": 3,
    }
    selected = min(gaps, key=lambda gap: (priority.get(gap.gap_id, 99), gap.gap_id))
    return selected.next_safe_action


def build_current_runtime_readiness_report() -> RuntimeReadinessReport:
    capabilities = (
        RuntimeCapability.create(
            capability_id="registry-model",
            category="dashboard",
            status="ready",
            live_hook=False,
            summary="AUTO registry metadata and dashboard-facing models are prepared.",
        ),
        RuntimeCapability.create(
            capability_id="thread-send-hook",
            category="threading",
            status="dry_run_only",
            live_hook=False,
            summary="Thread send path is modeled, but live dispatch remains dry-run only.",
        ),
        RuntimeCapability.create(
            capability_id="git-command-runner",
            category="git",
            status="requires_operator",
            live_hook=False,
            summary="Git command runner is intentionally operator-gated and not live-wired.",
        ),
        RuntimeCapability.create(
            capability_id="test-command-runner",
            category="testing",
            status="requires_operator",
            live_hook=False,
            summary="Test execution hooks remain operator-required and are not auto-fired.",
        ),
        RuntimeCapability.create(
            capability_id="scheduler-heartbeat-execution",
            category="scheduler",
            status="dry_run_only",
            live_hook=False,
            summary="Heartbeat scheduler logic is prepared, but live execution stays dry-run only.",
        ),
    )
    gaps = (
        RuntimeGap.create(
            gap_id="thread-send-live-hook",
            category="threading",
            status="dry_run_only",
            summary="Thread send integration is modeled only and not safe for unattended live sends yet.",
            next_safe_action="Keep thread dispatch in dry-run mode and require operator confirmation before live sends.",
        ),
        RuntimeGap.create(
            gap_id="git-runner-operator-gate",
            category="git",
            status="requires_operator",
            summary="Git command execution still depends on explicit operator approval.",
            next_safe_action="Maintain operator approval for git actions until a safe audited command runner exists.",
        ),
        RuntimeGap.create(
            gap_id="test-runner-operator-gate",
            category="testing",
            status="requires_operator",
            summary="Automated test command execution is not yet live-safe for unattended orchestration.",
            next_safe_action="Run tests only through explicit operator flows until sandboxed live hooks are approved.",
        ),
        RuntimeGap.create(
            gap_id="scheduler-live-execution",
            category="scheduler",
            status="requires_operator",
            summary="Heartbeat scheduling is intentionally blocked from unattended execution.",
            next_safe_action="Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.",
        ),
    )
    return build_runtime_readiness_report(capabilities=capabilities, gaps=gaps)

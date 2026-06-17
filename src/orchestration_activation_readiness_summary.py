"""Compact readiness summary for orchestration activation planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.orchestration_operator_activation import OrchestrationActivationPlan
from src.orchestration_runtime_readiness import ReadinessStatus, RuntimeReadinessReport


@dataclass(frozen=True, slots=True)
class OrchestrationActivationReadinessSummary:
    mode: str
    status_label: str
    live_dispatch_allowed: bool
    open_gap_count: int
    blocking_reasons: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    next_safe_action: str
    operator_required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status_label": self.status_label,
            "live_dispatch_allowed": self.live_dispatch_allowed,
            "open_gap_count": self.open_gap_count,
            "blocking_reasons": self.blocking_reasons,
            "allowed_actions": self.allowed_actions,
            "next_safe_action": self.next_safe_action,
            "operator_required": self.operator_required,
        }


def build_activation_readiness_summary(
    report: RuntimeReadinessReport,
    plan: OrchestrationActivationPlan,
) -> OrchestrationActivationReadinessSummary:
    if not isinstance(report, RuntimeReadinessReport):
        raise TypeError("report must be a RuntimeReadinessReport")
    if not isinstance(plan, OrchestrationActivationPlan):
        raise TypeError("plan must be an OrchestrationActivationPlan")

    live_dispatch_allowed = any(item.action.value == "execute_live_dispatch" for item in plan.allowed_actions)
    operator_required = any(gap.status == ReadinessStatus.REQUIRES_OPERATOR for gap in report.gaps)
    blocked_by_hard_gap = report.blocked

    if live_dispatch_allowed and not operator_required and not blocked_by_hard_gap and report.open_gap_count == 0:
        status_label = "live_limited_ready"
    elif plan.mode.value == "prepare_dispatch":
        status_label = "prepare_only"
    elif plan.mode.value == "read_only":
        status_label = "read_only"
    else:
        status_label = "blocked"

    blocking_reasons = tuple(dict.fromkeys(gap.summary for gap in report.gaps if gap.status != ReadinessStatus.READY))
    allowed_actions = tuple(item.action.value for item in plan.allowed_actions)

    return OrchestrationActivationReadinessSummary(
        mode=plan.mode.value,
        status_label=status_label,
        live_dispatch_allowed=live_dispatch_allowed,
        open_gap_count=report.open_gap_count,
        blocking_reasons=blocking_reasons,
        allowed_actions=allowed_actions,
        next_safe_action=plan.next_safe_action,
        operator_required=operator_required,
    )

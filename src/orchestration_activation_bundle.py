"""Read-only bundle builder for current orchestration activation state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.orchestration_activation_readiness_summary import (
    OrchestrationActivationReadinessSummary,
    build_activation_readiness_summary,
)
from src.orchestration_activation_summary_renderers import (
    render_activation_readiness_summary_json,
    render_activation_readiness_summary_markdown,
)
from src.orchestration_operator_activation import (
    OperatorActivationPolicy,
    OrchestrationActivationPlan,
    build_orchestration_activation_plan,
)
from src.orchestration_runtime_readiness import (
    RuntimeReadinessReport,
    build_current_runtime_readiness_report,
)


@dataclass(frozen=True, slots=True)
class OrchestrationActivationBundle:
    readiness_report: RuntimeReadinessReport
    activation_plan: OrchestrationActivationPlan
    summary: OrchestrationActivationReadinessSummary
    json_snapshot: str
    markdown_snapshot: str
    label: str
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "generated_at": self.generated_at,
            "readiness_report": self.readiness_report.to_dict(),
            "activation_plan": self.activation_plan.to_dict(),
            "summary": self.summary.to_dict(),
            "json_snapshot": self.json_snapshot,
            "markdown_snapshot": self.markdown_snapshot,
        }


def build_orchestration_activation_bundle(
    *,
    readiness_report: RuntimeReadinessReport,
    policy: OperatorActivationPolicy,
    label: str = "orchestration-activation-bundle",
    generated_at: str = "",
) -> OrchestrationActivationBundle:
    if not isinstance(readiness_report, RuntimeReadinessReport):
        raise TypeError("readiness_report must be a RuntimeReadinessReport")
    if not isinstance(policy, OperatorActivationPolicy):
        raise TypeError("policy must be an OperatorActivationPolicy")

    normalized_label = " ".join(str(label or "").split()) or "orchestration-activation-bundle"
    normalized_generated_at = " ".join(str(generated_at or "").split())

    activation_plan = build_orchestration_activation_plan(readiness=readiness_report, policy=policy)
    summary = build_activation_readiness_summary(readiness_report, activation_plan)
    return OrchestrationActivationBundle(
        readiness_report=readiness_report,
        activation_plan=activation_plan,
        summary=summary,
        json_snapshot=render_activation_readiness_summary_json(summary),
        markdown_snapshot=render_activation_readiness_summary_markdown(summary),
        label=normalized_label,
        generated_at=normalized_generated_at,
    )


def build_current_orchestration_activation_bundle(
    *,
    policy: OperatorActivationPolicy | None = None,
    label: str = "current-orchestration-activation-bundle",
    generated_at: str = "",
) -> OrchestrationActivationBundle:
    effective_policy = policy or OperatorActivationPolicy.create(
        requested_mode="prepare_dispatch",
        operator_approved=False,
        allow_live_dispatch=False,
    )
    return build_orchestration_activation_bundle(
        readiness_report=build_current_runtime_readiness_report(),
        policy=effective_policy,
        label=label,
        generated_at=generated_at,
    )

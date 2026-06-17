"""Renderers for orchestration activation readiness summaries."""

from __future__ import annotations

import json

from src.orchestration_activation_readiness_summary import OrchestrationActivationReadinessSummary


def render_activation_readiness_summary_json(summary: OrchestrationActivationReadinessSummary) -> str:
    if not isinstance(summary, OrchestrationActivationReadinessSummary):
        raise TypeError("summary must be an OrchestrationActivationReadinessSummary")
    return json.dumps(summary.to_dict(), indent=2, sort_keys=True)


def render_activation_readiness_summary_markdown(summary: OrchestrationActivationReadinessSummary) -> str:
    if not isinstance(summary, OrchestrationActivationReadinessSummary):
        raise TypeError("summary must be an OrchestrationActivationReadinessSummary")

    lines = [
        "# Orchestration Activation Readiness",
        "",
        f"Status: {summary.status_label}",
        f"Mode: {summary.mode}",
        f"Live Dispatch Allowed: {'yes' if summary.live_dispatch_allowed else 'no'}",
        f"Open Gap Count: {summary.open_gap_count}",
        f"Operator Required: {'yes' if summary.operator_required else 'no'}",
        f"Next Safe Action: {summary.next_safe_action}",
        "",
        f"Allowed Actions: {', '.join(summary.allowed_actions) if summary.allowed_actions else 'none'}",
        f"Blocking Reasons: {'; '.join(summary.blocking_reasons) if summary.blocking_reasons else 'none'}",
    ]
    return "\n".join(lines)

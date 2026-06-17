"""Markdown rendering for read-only release orchestration status."""
from __future__ import annotations

from src.plugin_release_gate import PluginReleaseGate
from src.release_orchestration_status import ReleaseOrchestrationStatus, build_release_orchestration_status
from src.release_readiness_pipeline import build_current_release_readiness_pipeline


def render_release_status_markdown(status: ReleaseOrchestrationStatus) -> str:
    """Render a compact, stable status block for runbooks or chat updates."""
    lines = [
        "# Release Orchestration Status",
        "",
        f"- Status: `{status.status}`",
        f"- External release go: `{str(status.external_release_go).lower()}`",
        f"- Active owners: {_fmt(status.active_owners)}",
        f"- Parallel candidates: {_fmt(status.parallel_candidate_ids)}",
        f"- Sequential gates: {_fmt(status.sequential_gate_ids)}",
        f"- Next actions: {_fmt(status.next_action_ids)}",
    ]
    return "\n".join(lines)


def render_current_release_status_markdown(plugin_gate: PluginReleaseGate | None = None) -> str:
    """Render the documented current 1.0 release state without live checks."""
    pipeline = build_current_release_readiness_pipeline(plugin_gate=plugin_gate)
    return render_release_status_markdown(build_release_orchestration_status(pipeline))


def _fmt(values: tuple[str, ...]) -> str:
    if not values:
        return "`none`"
    return ", ".join(f"`{value}`" for value in values)

"""Markdown rendering for read-only release orchestration status."""
from __future__ import annotations

from src.release_orchestration_status import ReleaseOrchestrationStatus


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


def _fmt(values: tuple[str, ...]) -> str:
    if not values:
        return "`none`"
    return ", ".join(f"`{value}`" for value in values)

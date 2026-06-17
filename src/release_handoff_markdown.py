"""Markdown handoff for the current release readiness state."""
from __future__ import annotations

from src.plugin_release_gate import PluginReleaseGate
from src.release_followup_markdown import render_release_followup_markdown
from src.release_orchestration_status import build_release_orchestration_status
from src.release_readiness_pipeline import ReleaseReadinessPipelineSnapshot, build_current_release_readiness_pipeline
from src.release_status_markdown import render_release_status_markdown


def render_release_handoff_markdown(pipeline: ReleaseReadinessPipelineSnapshot) -> str:
    """Render status plus follow-up slices in one stable handoff block."""
    status = build_release_orchestration_status(pipeline)
    return (
        render_release_status_markdown(status)
        + "\n\n"
        + render_release_followup_markdown(pipeline.followup_slices)
    )


def render_current_release_handoff_markdown(plugin_gate: PluginReleaseGate | None = None) -> str:
    """Render the documented current 1.0 handoff without live checks."""
    return render_release_handoff_markdown(build_current_release_readiness_pipeline(plugin_gate=plugin_gate))

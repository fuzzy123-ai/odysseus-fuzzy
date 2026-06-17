"""Read-only morning brief for the current release handoff."""
from __future__ import annotations

from src.local_release_readiness_bundle import LocalReleaseReadinessBundle, build_local_release_readiness_bundle


def render_release_morning_brief(bundle: LocalReleaseReadinessBundle) -> str:
    sections = [
        "# Odysseus Release Morning Brief",
        "",
        "## Handoff",
        "",
        bundle.handoff_markdown,
        "",
        "## Artifact Traceability",
        "",
        bundle.artifact_markdown,
    ]
    return "\n".join(sections)


def render_current_release_morning_brief() -> str:
    return render_release_morning_brief(build_local_release_readiness_bundle())

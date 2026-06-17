"""Convenience helpers for the current manual release evidence artifact."""

from __future__ import annotations

from src.manual_release_evidence_artifact import (
    ManualReleaseEvidenceArtifact,
    build_manual_release_evidence_artifact,
)
from src.manual_release_evidence_artifact_markdown import render_manual_release_evidence_artifact_markdown
from src.manual_release_gap_report import build_current_manual_evidence_gap_report


def build_current_manual_release_evidence_artifact(
    *,
    label: str = "current-manual-release-evidence",
    generated_at: str = "",
) -> ManualReleaseEvidenceArtifact:
    return build_manual_release_evidence_artifact(
        build_current_manual_evidence_gap_report(),
        label=label,
        generated_at=generated_at,
    )


def render_current_manual_release_evidence_artifact_markdown(
    *,
    label: str = "current-manual-release-evidence",
    generated_at: str = "",
) -> str:
    return render_manual_release_evidence_artifact_markdown(
        build_current_manual_release_evidence_artifact(label=label, generated_at=generated_at)
    )

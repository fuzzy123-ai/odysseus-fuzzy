"""Markdown renderer for manual release evidence artifacts."""

from __future__ import annotations

from src.manual_release_evidence_artifact import ManualReleaseEvidenceArtifact


def render_manual_release_evidence_artifact_markdown(artifact: ManualReleaseEvidenceArtifact) -> str:
    if not isinstance(artifact, ManualReleaseEvidenceArtifact):
        raise TypeError("artifact must be a ManualReleaseEvidenceArtifact")

    status = "OK" if artifact.ok else "NO_GO"
    generated_at = artifact.generated_at or "unknown"

    return "\n".join(
        [
            "# Manual Release Evidence Artifact",
            "",
            f"Label: {artifact.label}",
            f"Generated At: {generated_at}",
            f"Status: {status}",
            f"SHA-256: {artifact.sha256}",
            "",
            "## Gap Report",
            "",
            artifact.markdown,
        ]
    )

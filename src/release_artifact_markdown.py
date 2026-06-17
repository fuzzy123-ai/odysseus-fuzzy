"""Markdown rendering for release artifact traceability manifests."""
from __future__ import annotations

from src.release_artifact_manifest import ReleaseArtifactManifest


def render_release_artifact_manifest_markdown(manifest: ReleaseArtifactManifest) -> str:
    status = "PASS" if manifest.ok else "BLOCKED"
    lines = [
        "# Release Artifact Manifest",
        "",
        f"Status: **{status}**",
        "",
        "| Artifact | Role | Required | State |",
        "| --- | --- | --- | --- |",
    ]
    missing = set(manifest.missing_required_paths)
    for artifact in manifest.artifacts:
        state = "missing" if artifact.path in missing else "present"
        required = "yes" if artifact.required else "no"
        lines.append(f"| `{artifact.path}` | {artifact.role} | {required} | {state} |")
    if manifest.missing_required_paths:
        lines.extend(
            [
                "",
                "Missing required artifacts:",
            ]
        )
        lines.extend(f"- `{path}`" for path in manifest.missing_required_paths)
    return "\n".join(lines)

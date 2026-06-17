"""Traceability manifest for read-only release readiness artifacts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ReleaseArtifact:
    path: str
    role: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "required": self.required,
        }


@dataclass(frozen=True)
class ReleaseArtifactManifest:
    artifacts: tuple[ReleaseArtifact, ...]
    missing_required_paths: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing_required_paths

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "artifacts": tuple(item.to_dict() for item in self.artifacts),
            "missing_required_paths": self.missing_required_paths,
        }


def build_release_artifact_manifest(
    *,
    root: str | Path = ".",
    artifacts: Iterable[ReleaseArtifact] | None = None,
) -> ReleaseArtifactManifest:
    root_path = Path(root)
    normalized = tuple(artifacts or default_release_artifacts())
    missing = tuple(
        item.path
        for item in normalized
        if item.required and not (root_path / item.path).exists()
    )
    return ReleaseArtifactManifest(artifacts=normalized, missing_required_paths=missing)


def default_release_artifacts() -> tuple[ReleaseArtifact, ...]:
    return (
        ReleaseArtifact("docs/plans/1.0-evidence-release-checklist.md", "release checklist"),
        ReleaseArtifact("docs/plans/1.0-manual-release-evidence-log.md", "manual evidence log"),
        ReleaseArtifact("docs/plans/1.0-manual-release-evidence-runbook.md", "manual evidence runbook"),
        ReleaseArtifact("docs/plans/unified-odysseus-roadmap.md", "master roadmap"),
        ReleaseArtifact("plugins/registry.json", "plugin registry"),
        ReleaseArtifact("src/local_release_readiness_bundle.py", "local readiness bundle"),
        ReleaseArtifact("src/release_handoff_markdown.py", "release handoff markdown renderer"),
        ReleaseArtifact("src/release_morning_brief.py", "release morning brief renderer"),
        ReleaseArtifact("src/release_morning_payload_contract.py", "release morning payload contract validator"),
        ReleaseArtifact("src/release_morning_payload_diff.py", "release morning payload diff helper"),
        ReleaseArtifact("src/release_morning_payload_diff_json.py", "release morning payload diff JSON renderer"),
        ReleaseArtifact("src/release_morning_payload_diff_markdown.py", "release morning payload diff markdown renderer"),
        ReleaseArtifact("src/release_morning_payload_json.py", "release morning payload JSON renderer"),
        ReleaseArtifact("src/release_morning_payload.py", "release morning dashboard payload"),
        ReleaseArtifact("src/release_morning_summary.py", "release morning summary snapshot"),
        ReleaseArtifact("src/release_readiness_pipeline.py", "release readiness pipeline"),
        ReleaseArtifact("src/release_slice_router.py", "release followup router"),
        ReleaseArtifact("src/plugin_release_gate.py", "plugin release gate"),
    )

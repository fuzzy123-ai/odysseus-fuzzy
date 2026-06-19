"""Read-only readiness summary for the current manual release evidence artifact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.current_manual_release_evidence_artifact import build_current_manual_release_evidence_artifact
from src.manual_release_evidence_artifact_naming import build_manual_release_evidence_artifact_filename
from src.manual_release_gap_report import build_current_manual_evidence_gap_report


@dataclass(frozen=True, slots=True)
class ManualReleaseEvidenceReadinessSummary:
    label: str
    generated_at: str
    ok: bool
    status_label: str
    sha256: str
    suggested_markdown_filename: str
    suggested_json_filename: str
    open_gate_count: int
    no_go_hint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "generated_at": self.generated_at,
            "ok": self.ok,
            "status_label": self.status_label,
            "sha256": self.sha256,
            "suggested_markdown_filename": self.suggested_markdown_filename,
            "suggested_json_filename": self.suggested_json_filename,
            "open_gate_count": self.open_gate_count,
            "no_go_hint": self.no_go_hint,
        }


def build_current_manual_release_evidence_readiness_summary(
    *,
    label: str = "current-manual-release-evidence",
    generated_at: str = "",
) -> ManualReleaseEvidenceReadinessSummary:
    artifact = build_current_manual_release_evidence_artifact(label=label, generated_at=generated_at)
    report = build_current_manual_evidence_gap_report()
    ok = artifact.ok and report.ok
    status_label = "OK" if ok else "NO_GO"
    open_gate_count = len(report.gaps)
    no_go_hint = (
        "Manual release evidence still has an open gate and remains no-go until Provider Proof is closed."
        if not ok
        else "All required manual release evidence gates are currently closed."
    )
    return ManualReleaseEvidenceReadinessSummary(
        label=artifact.label,
        generated_at=artifact.generated_at,
        ok=ok,
        status_label=status_label,
        sha256=artifact.sha256,
        suggested_markdown_filename=build_manual_release_evidence_artifact_filename(artifact, extension="md"),
        suggested_json_filename=build_manual_release_evidence_artifact_filename(artifact, extension="json"),
        open_gate_count=open_gate_count,
        no_go_hint=no_go_hint,
    )

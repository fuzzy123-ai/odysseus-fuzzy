"""Artifact bundle helper for manual release evidence gap reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.manual_release_gap_report import ManualEvidenceGapReport
from src.manual_release_gap_report_digest import manual_release_gap_report_sha256
from src.manual_release_gap_report_json import render_manual_release_gap_report_json
from src.manual_release_gap_report_markdown import render_manual_release_gap_report_markdown


@dataclass(frozen=True, slots=True)
class ManualReleaseEvidenceArtifact:
    label: str
    generated_at: str
    ok: bool
    markdown: str
    json: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "generated_at": self.generated_at,
            "ok": self.ok,
            "markdown": self.markdown,
            "json": self.json,
            "sha256": self.sha256,
        }


def build_manual_release_evidence_artifact(
    report: ManualEvidenceGapReport,
    *,
    label: str = "manual-release-evidence-gap-report",
    generated_at: str = "",
) -> ManualReleaseEvidenceArtifact:
    if not isinstance(report, ManualEvidenceGapReport):
        raise TypeError("report must be a ManualEvidenceGapReport")

    normalized_label = " ".join(str(label or "").split())
    if not normalized_label:
        raise ValueError("label must not be empty")
    normalized_generated_at = " ".join(str(generated_at or "").split())

    return ManualReleaseEvidenceArtifact(
        label=normalized_label,
        generated_at=normalized_generated_at,
        ok=report.ok,
        markdown=render_manual_release_gap_report_markdown(report),
        json=render_manual_release_gap_report_json(report),
        sha256=manual_release_gap_report_sha256(report),
    )

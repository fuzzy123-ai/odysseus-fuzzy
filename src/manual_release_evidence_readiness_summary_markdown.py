"""Markdown renderer for manual release evidence readiness summaries."""

from __future__ import annotations

from src.manual_release_evidence_readiness_summary import ManualReleaseEvidenceReadinessSummary


def render_manual_release_evidence_readiness_summary_markdown(
    summary: ManualReleaseEvidenceReadinessSummary,
) -> str:
    if not isinstance(summary, ManualReleaseEvidenceReadinessSummary):
        raise TypeError("summary must be a ManualReleaseEvidenceReadinessSummary")

    return "\n".join(
        [
            "# Manual Release Evidence Readiness Summary",
            "",
            f"Status: {summary.status_label}",
            f"OK: {'yes' if summary.ok else 'no'}",
            f"Open Gate Count: {summary.open_gate_count}",
            f"SHA-256: {summary.sha256}",
            f"Suggested Markdown Filename: {summary.suggested_markdown_filename}",
            f"Suggested JSON Filename: {summary.suggested_json_filename}",
            "",
            summary.no_go_hint,
        ]
    )

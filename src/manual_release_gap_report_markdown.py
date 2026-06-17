"""Markdown renderer for manual release evidence gap reports."""

from __future__ import annotations

from src.manual_release_gap_report import ManualEvidenceGapReport


_STATUS_LABELS = {
    "ok": "OK",
    "pending": "PENDING",
    "partial_no_go": "PARTIAL",
    "no_go": "BLOCKED",
}


def render_manual_release_gap_report_markdown(report: ManualEvidenceGapReport) -> str:
    if not isinstance(report, ManualEvidenceGapReport):
        raise TypeError("report must be a ManualEvidenceGapReport")

    lines = [
        "# Manual Release Evidence Gaps",
        "",
        f"Status: {_STATUS_LABELS.get(report.status, report.status.upper())}",
        "",
    ]

    if report.ok and not report.gaps:
        lines.extend(
            [
                "No manual release evidence gaps are currently open.",
                "",
                "All required manual 1.0 evidence gates are marked as go.",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            f"Open manual evidence gaps: {len(report.gaps)}",
            "",
            "| Gate | Status | Label | Blocker | Next Action | Owner |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )

    for gap in report.gaps:
        lines.append(
            "| {gate} | {status} | {label} | {blocker} | {next_action} | {owner} |".format(
                gate=_escape_cell(gap.gate_id),
                status=_escape_cell(gap.status.upper()),
                label=_escape_cell(gap.label),
                blocker=_escape_cell(gap.blocker or "-"),
                next_action=_escape_cell(gap.next_action),
                owner=_escape_cell(gap.owner),
            )
        )

    return "\n".join(lines)


def _escape_cell(value: str) -> str:
    return str(value or "").replace("\n", " ").replace("|", "\\|").strip()

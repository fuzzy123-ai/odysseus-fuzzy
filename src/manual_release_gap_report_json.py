"""Deterministic JSON renderer for manual release evidence gap reports."""

from __future__ import annotations

import json

from src.manual_release_gap_report import ManualEvidenceGapReport


def render_manual_release_gap_report_json(report: ManualEvidenceGapReport) -> str:
    if not isinstance(report, ManualEvidenceGapReport):
        raise TypeError("report must be a ManualEvidenceGapReport")
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)

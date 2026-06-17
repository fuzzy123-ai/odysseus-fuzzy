"""Deterministic digest helpers for manual release evidence gap reports."""

from __future__ import annotations

import hashlib
import json

from src.manual_release_gap_report import ManualEvidenceGapReport


def canonical_manual_release_gap_report_json(report: ManualEvidenceGapReport) -> str:
    if not isinstance(report, ManualEvidenceGapReport):
        raise TypeError("report must be a ManualEvidenceGapReport")
    return json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_manual_release_gap_report_bytes(report: ManualEvidenceGapReport) -> bytes:
    return canonical_manual_release_gap_report_json(report).encode("utf-8")


def manual_release_gap_report_sha256(report: ManualEvidenceGapReport) -> str:
    return hashlib.sha256(canonical_manual_release_gap_report_bytes(report)).hexdigest()

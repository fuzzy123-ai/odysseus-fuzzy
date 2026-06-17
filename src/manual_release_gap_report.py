"""Gap report helper for manual 1.0 release evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.manual_release_evidence import (
    GO,
    NO_GO,
    PARTIAL,
    PENDING,
    ManualEvidenceEntry,
    ManualEvidenceSummary,
    REQUIRED_GATE_IDS,
    current_manual_evidence_entries,
    summarize_manual_evidence,
)


_STATUS_PRIORITY = {
    "missing": 1,
    "pending": 2,
    "partial": 3,
    "no_go": 4,
}


class ManualReleaseGapReportError(ValueError):
    """Raised when manual release gap reporting inputs are invalid."""


@dataclass(frozen=True, slots=True)
class ManualEvidenceGap:
    gate_id: str
    status: str
    label: str
    blocker: str
    next_action: str
    owner: str
    evidence_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "label": self.label,
            "blocker": self.blocker,
            "next_action": self.next_action,
            "owner": self.owner,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True, slots=True)
class ManualEvidenceGapReport:
    ok: bool
    status: str
    gaps: tuple[ManualEvidenceGap, ...]
    summary: ManualEvidenceSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "gaps": tuple(gap.to_dict() for gap in self.gaps),
            "summary": self.summary.to_dict(),
        }


def build_current_manual_evidence_gap_report() -> ManualEvidenceGapReport:
    return build_manual_evidence_gap_report(current_manual_evidence_entries())


def build_manual_evidence_gap_report(
    entries: Iterable[ManualEvidenceEntry],
    *,
    required_gate_ids: Iterable[str] = REQUIRED_GATE_IDS,
) -> ManualEvidenceGapReport:
    normalized_entries = tuple(entries)
    required = tuple(required_gate_ids)
    summary = summarize_manual_evidence(normalized_entries, required_gate_ids=required)
    by_gate = {entry.gate_id: entry for entry in normalized_entries}
    gate_order = {
        gate_id: index
        for index, gate_id in enumerate(
            tuple(dict.fromkeys([entry.gate_id for entry in normalized_entries] + list(required)))
        )
    }

    gap_status_by_gate: dict[str, str] = {}
    for gate_id in summary.missing_gate_ids:
        gap_status_by_gate[gate_id] = "missing"
    for gate_id in summary.pending_gate_ids:
        gap_status_by_gate[gate_id] = _pick_stronger_status(gap_status_by_gate.get(gate_id), "pending")
    for gate_id in summary.partial_gate_ids:
        gap_status_by_gate[gate_id] = _pick_stronger_status(gap_status_by_gate.get(gate_id), "partial")
    for gate_id in summary.no_go_gate_ids:
        gap_status_by_gate[gate_id] = _pick_stronger_status(gap_status_by_gate.get(gate_id), "no_go")

    gaps = tuple(
        _build_gap(gate_id=gate_id, status=status, entry=by_gate.get(gate_id))
        for gate_id, status in sorted(
            gap_status_by_gate.items(),
            key=lambda item: (_STATUS_PRIORITY[item[1]], gate_order.get(item[0], len(gate_order)), item[0]),
        )
    )
    return ManualEvidenceGapReport(
        ok=not gaps and summary.external_go,
        status="ok" if not gaps and summary.external_go else summary.status,
        gaps=gaps,
        summary=summary,
    )


def _pick_stronger_status(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    return candidate if _STATUS_PRIORITY[candidate] > _STATUS_PRIORITY[current] else current


def _build_gap(*, gate_id: str, status: str, entry: ManualEvidenceEntry | None) -> ManualEvidenceGap:
    if status not in {"missing", "pending", "partial", "no_go"}:
        raise ManualReleaseGapReportError("unsupported gap status")
    label = entry.label if entry else gate_id.replace("-", " ").title()
    blocker = entry.blocker if entry else "required manual gate has no evidence entry yet"
    evidence_ref = entry.evidence_ref if entry else ""
    owner, next_action = _default_next_action(gate_id=gate_id, status=status)
    return ManualEvidenceGap(
        gate_id=gate_id,
        status=status,
        label=label,
        blocker=blocker,
        next_action=next_action,
        owner=owner,
        evidence_ref=evidence_ref,
    )


def _default_next_action(*, gate_id: str, status: str) -> tuple[str, str]:
    if gate_id == "provider-proof":
        return (
            "Charlie/Alice",
            "Run provider-proof operator runbook, verify query-index readiness, and avoid logging secrets or provider credentials.",
        )
    if gate_id == "export-import-rebuild":
        return (
            "Charlie/Bob",
            "Prepare a small disposable test vault with no user artifacts, then run the manual export/import/rebuild proof end-to-end.",
        )
    if gate_id == "fresh-install":
        return ("Charlie", f"Complete the manual fresh-install gate and attach evidence before release ({status}).")
    if gate_id == "upgrade-path":
        return ("Charlie", f"Complete the manual upgrade-path gate and attach evidence before release ({status}).")
    if gate_id == "known-limits-review":
        return ("Alice/Charlie", f"Finish the known-limits review and capture the release evidence ({status}).")
    return ("Charlie", f"Resolve manual evidence gap for {gate_id} and attach stable release evidence ({status}).")

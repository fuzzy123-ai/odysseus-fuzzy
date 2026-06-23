"""Read-only 1.0 release readiness pipeline snapshot.

This module wires the small release models together for dashboards, runbooks,
or future orchestration. It does not execute tests, git, providers, plugins, or
manual release actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.manual_release_evidence import current_manual_evidence_entries, summarize_manual_evidence
from src.mvp_master_roadmap_gate import MvpVersionGate, build_current_mvp_version_gate
from src.plugin_release_gate import PluginReleaseGate
from src.release_followup_matrix import ReleaseFollowupMatrix, build_release_followup_matrix
from src.release_evidence_snapshot import AUTOMATED, PASS, ReleaseGate, build_release_evidence_snapshot
from src.release_readiness_report import ReleaseReadinessReport, build_release_readiness_report
from src.release_slice_router import ReleaseFollowupSlice, route_release_followups


@dataclass(frozen=True)
class ReleaseReadinessPipelineSnapshot:
    report: ReleaseReadinessReport
    followup_slices: tuple[ReleaseFollowupSlice, ...]
    followup_matrix: ReleaseFollowupMatrix
    version_gate: MvpVersionGate

    @property
    def external_release_go(self) -> bool:
        return self.report.external_release_go

    @property
    def automated_gate_evidence_mode(self) -> str:
        return "documented_baseline"

    @property
    def automated_gate_is_live_measurement(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": self.report.to_dict(),
            "automated_gate_evidence_mode": self.automated_gate_evidence_mode,
            "automated_gate_is_live_measurement": self.automated_gate_is_live_measurement,
            "automated_gate_summary": {
                "status": "baseline_evidence_green",
                "operator_interpretation": (
                    "Automated release gates are documented green baselines, not fresh live measurements."
                ),
            },
            "followup_slices": tuple(item.to_dict() for item in self.followup_slices),
            "followup_matrix": self.followup_matrix.to_dict(),
            "version_gate": self.version_gate.to_dict(),
        }


def build_current_release_readiness_pipeline(
    plugin_gate: PluginReleaseGate | None = None,
    version_gate: MvpVersionGate | None = None,
) -> ReleaseReadinessPipelineSnapshot:
    """Build the current documented 1.0 state without running live checks."""
    release_snapshot = build_release_evidence_snapshot(current_automated_release_gates())
    manual_summary = summarize_manual_evidence(current_manual_evidence_entries())
    resolved_version_gate = version_gate or build_current_mvp_version_gate(ui_live=False)
    report = build_release_readiness_report(
        release_snapshot,
        plugin_gate=plugin_gate,
        manual_evidence=manual_summary,
        version_gate=resolved_version_gate,
    )
    followup_slices = route_release_followups(report)
    return ReleaseReadinessPipelineSnapshot(
        report=report,
        followup_slices=followup_slices,
        followup_matrix=build_release_followup_matrix(followup_slices),
        version_gate=resolved_version_gate,
    )


def current_automated_release_gates() -> tuple[ReleaseGate, ...]:
    """Automated REL1 gates documented as green baseline evidence in the active checklist."""
    return (
        ReleaseGate(
            gate_id="memory-obsidian-external-proof",
            label="Memory / Obsidian / External Proof",
            kind=AUTOMATED,
            status=PASS,
            evidence_refs=("REL1 documented baseline evidence",),
            risk="documented_baseline_not_fresh_measurement",
        ),
        ReleaseGate(
            gate_id="static-context-ui-safety-smoke",
            label="Static / Context / UI Safety Smoke",
            kind=AUTOMATED,
            status=PASS,
            evidence_refs=("REL1 documented baseline evidence",),
            risk="documented_baseline_not_fresh_measurement",
        ),
        ReleaseGate(
            gate_id="scale-lightweight-maintenance",
            label="Scale / Lightweight Maintenance",
            kind=AUTOMATED,
            status=PASS,
            evidence_refs=("REL1 documented baseline evidence",),
            risk="documented_baseline_not_fresh_measurement",
        ),
    )

"""Read-only 1.0 release readiness pipeline snapshot.

This module wires the small release models together for dashboards, runbooks,
or future orchestration. It does not execute tests, git, providers, plugins, or
manual release actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.manual_release_evidence import current_manual_evidence_entries, summarize_manual_evidence
from src.plugin_release_gate import PluginReleaseGate
from src.release_evidence_snapshot import AUTOMATED, PASS, ReleaseGate, build_release_evidence_snapshot
from src.release_readiness_report import ReleaseReadinessReport, build_release_readiness_report
from src.release_slice_router import ReleaseFollowupSlice, route_release_followups


@dataclass(frozen=True)
class ReleaseReadinessPipelineSnapshot:
    report: ReleaseReadinessReport
    followup_slices: tuple[ReleaseFollowupSlice, ...]

    @property
    def external_release_go(self) -> bool:
        return self.report.external_release_go

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": self.report.to_dict(),
            "followup_slices": tuple(item.to_dict() for item in self.followup_slices),
        }


def build_current_release_readiness_pipeline(
    plugin_gate: PluginReleaseGate | None = None,
) -> ReleaseReadinessPipelineSnapshot:
    """Build the current documented 1.0 state without running live checks."""
    release_snapshot = build_release_evidence_snapshot(current_automated_release_gates())
    manual_summary = summarize_manual_evidence(current_manual_evidence_entries())
    report = build_release_readiness_report(
        release_snapshot,
        plugin_gate=plugin_gate,
        manual_evidence=manual_summary,
    )
    return ReleaseReadinessPipelineSnapshot(
        report=report,
        followup_slices=route_release_followups(report),
    )


def current_automated_release_gates() -> tuple[ReleaseGate, ...]:
    """Automated REL1 gates documented as green in the active checklist."""
    return (
        ReleaseGate(
            gate_id="memory-obsidian-external-proof",
            label="Memory / Obsidian / External Proof",
            kind=AUTOMATED,
            status=PASS,
            evidence_refs=("REL1 automated gate run",),
        ),
        ReleaseGate(
            gate_id="static-context-ui-safety-smoke",
            label="Static / Context / UI Safety Smoke",
            kind=AUTOMATED,
            status=PASS,
            evidence_refs=("REL1 automated gate run",),
        ),
        ReleaseGate(
            gate_id="scale-lightweight-maintenance",
            label="Scale / Lightweight Maintenance",
            kind=AUTOMATED,
            status=PASS,
            evidence_refs=("REL1 automated gate run",),
        ),
    )

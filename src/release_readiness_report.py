"""Read-only release readiness report aggregation.

This is a presentation/contract layer: callers provide already-computed gate
objects. The report never runs pytest, git, providers, plugins, or host checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.plugin_release_gate import PluginReleaseGate
from src.release_evidence_snapshot import ReleaseEvidenceSnapshot


@dataclass(frozen=True)
class ReleaseReadinessReport:
    status: str
    external_release_go: bool
    release_gate_status: str
    plugin_gate_ok: bool
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "external_release_go": self.external_release_go,
            "release_gate_status": self.release_gate_status,
            "plugin_gate_ok": self.plugin_gate_ok,
            "blocking_reasons": self.blocking_reasons,
            "warnings": self.warnings,
            "next_actions": self.next_actions,
        }


def build_release_readiness_report(
    release_snapshot: ReleaseEvidenceSnapshot,
    plugin_gate: PluginReleaseGate | None = None,
) -> ReleaseReadinessReport:
    plugin_ok = True if plugin_gate is None else plugin_gate.ok
    blockers = _release_blockers(release_snapshot)
    warnings = tuple(f"release:{gate_id}" for gate_id in release_snapshot.warning_gate_ids)
    next_actions = _next_actions(release_snapshot, plugin_gate)

    if plugin_gate is not None and not plugin_gate.ok:
        blockers += tuple(f"plugin:{error}" for error in plugin_gate.errors)
    if plugin_gate is not None:
        warnings += tuple(f"plugin:{warning}" for warning in plugin_gate.warnings)

    external_go = release_snapshot.external_release_go and plugin_ok and not blockers
    if blockers:
        status = "blocked"
    elif warnings:
        status = "go_with_warnings" if external_go else "pending_with_warnings"
    else:
        status = "go" if external_go else release_snapshot.status

    return ReleaseReadinessReport(
        status=status,
        external_release_go=external_go,
        release_gate_status=release_snapshot.status,
        plugin_gate_ok=plugin_ok,
        blocking_reasons=blockers,
        warnings=warnings,
        next_actions=next_actions,
    )


def _release_blockers(snapshot: ReleaseEvidenceSnapshot) -> tuple[str, ...]:
    blockers = tuple(f"release:blocking:{gate_id}" for gate_id in snapshot.blocking_gate_ids)
    pending = tuple(f"release:manual_pending:{gate_id}" for gate_id in snapshot.pending_manual_gate_ids)
    return blockers + pending


def _next_actions(
    snapshot: ReleaseEvidenceSnapshot,
    plugin_gate: PluginReleaseGate | None,
) -> tuple[str, ...]:
    actions: list[str] = []
    if snapshot.pending_manual_gate_ids:
        actions.append("complete_manual_release_evidence")
    if snapshot.blocking_gate_ids:
        actions.append("fix_blocking_release_gates")
    if plugin_gate is not None and not plugin_gate.ok:
        actions.append("fix_plugin_release_gate")
    if not actions and snapshot.external_release_go:
        actions.append("prepare_external_release_review")
    return tuple(actions)

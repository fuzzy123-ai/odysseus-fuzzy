"""Machine-readable summary for the local release morning brief."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.local_release_readiness_bundle import LocalReleaseReadinessBundle, build_local_release_readiness_bundle
from src.release_orchestration_status import build_release_orchestration_status


@dataclass(frozen=True)
class ReleaseMorningSummary:
    status: str
    external_release_go: bool
    plugin_gate_ok: bool
    local_plugin_audit_ok: bool
    artifact_manifest_ok: bool
    active_owners: tuple[str, ...]
    next_action_ids: tuple[str, ...]
    local_plugin_failing_ids: tuple[str, ...] = ()
    missing_required_artifacts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "external_release_go": self.external_release_go,
            "plugin_gate_ok": self.plugin_gate_ok,
            "local_plugin_audit_ok": self.local_plugin_audit_ok,
            "artifact_manifest_ok": self.artifact_manifest_ok,
            "active_owners": self.active_owners,
            "next_action_ids": self.next_action_ids,
            "local_plugin_failing_ids": self.local_plugin_failing_ids,
            "missing_required_artifacts": self.missing_required_artifacts,
        }


def build_release_morning_summary(bundle: LocalReleaseReadinessBundle) -> ReleaseMorningSummary:
    orchestration = build_release_orchestration_status(bundle.pipeline)
    return ReleaseMorningSummary(
        status=orchestration.status,
        external_release_go=orchestration.external_release_go,
        plugin_gate_ok=bundle.plugin_gate.ok,
        local_plugin_audit_ok=bundle.local_plugin_audit.ok,
        artifact_manifest_ok=bundle.artifact_manifest.ok,
        active_owners=orchestration.active_owners,
        next_action_ids=orchestration.next_action_ids,
        local_plugin_failing_ids=bundle.local_plugin_audit.failing_ids,
        missing_required_artifacts=bundle.artifact_manifest.missing_required_paths,
    )


def build_current_release_morning_summary() -> ReleaseMorningSummary:
    return build_release_morning_summary(build_local_release_readiness_bundle())

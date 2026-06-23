"""MVP Roadmap 8 release/distribution evidence progress model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_STATUSES = ("go", "repo_open", "needs_live_go", "needs_design", "blocked", "deferred")
_SLICE_CLASSES = ("safe_offline", "repo_only", "needs_live_go", "needs_design", "blocked")


def _normalize_text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported release closure gate status")
    return text


def _normalize_slice_class(value: Any) -> str:
    text = _normalize_text(value, field_name="slice_class").strip().lower()
    if text not in _SLICE_CLASSES:
        raise ValueError("unsupported release closure slice class")
    return text


@dataclass(frozen=True, slots=True)
class ReleaseDistributionClosureGate:
    gate_id: str
    title: str
    status: str
    slice_class: str
    reason: str

    @classmethod
    def create(
        cls,
        *,
        gate_id: Any,
        title: Any,
        status: Any,
        slice_class: Any,
        reason: Any,
    ) -> "ReleaseDistributionClosureGate":
        return cls(
            gate_id=_normalize_text(gate_id, field_name="gate_id").strip().lower(),
            title=_normalize_text(title, field_name="title"),
            status=_normalize_status(status),
            slice_class=_normalize_slice_class(slice_class),
            reason=_normalize_text(reason, field_name="reason"),
        )

    @property
    def complete(self) -> bool:
        return self.status == "go"

    def to_dict(self) -> dict[str, str]:
        return {
            "gate_id": self.gate_id,
            "title": self.title,
            "status": self.status,
            "slice_class": self.slice_class,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ReleaseDistributionClosureReport:
    roadmap_id: str
    title: str
    gates: tuple[ReleaseDistributionClosureGate, ...]
    percent_complete: int
    why_not_100: str
    recommended_next_human_decision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "roadmap_id": self.roadmap_id,
            "title": self.title,
            "percent_complete": self.percent_complete,
            "why_not_100": self.why_not_100,
            "recommended_next_human_decision": self.recommended_next_human_decision,
            "gates": tuple(gate.to_dict() for gate in self.gates),
        }

    def to_markdown_row(self) -> str:
        reason = "-" if self.percent_complete == 100 else self.why_not_100
        return f"| 8 | {self.title} | {self.percent_complete} | {reason} |"


def _percent(gates: Iterable[ReleaseDistributionClosureGate]) -> int:
    items = tuple(gates)
    if not items:
        return 0
    complete = sum(1 for gate in items if gate.complete)
    return round((complete / len(items)) * 100)


def _first_incomplete(gates: Iterable[ReleaseDistributionClosureGate]) -> ReleaseDistributionClosureGate | None:
    for gate in gates:
        if not gate.complete:
            return gate
    return None


def build_release_distribution_closure_report(
    *,
    automated_release_gates_go: bool = True,
    manual_provider_proof_go: bool = True,
    manual_test_vault_go: bool = True,
    known_limits_go: bool = True,
    plugin_release_gate_go: bool = True,
    release_readiness_pipeline_go: bool = True,
    closeout_language_go: bool = True,
    live_phase_boundaries_go: bool = True,
    mvp_roadmap_aggregate_go: bool = True,
    deploy_tag_distribution_go: bool = False,
    new_ui_release_gate_go: bool = False,
) -> ReleaseDistributionClosureReport:
    gates = (
        ReleaseDistributionClosureGate.create(
            gate_id="automated_release_gates",
            title="Automated release gates",
            status="go" if automated_release_gates_go else "blocked",
            slice_class="repo_only",
            reason=(
                "automated release evidence snapshot and readiness reports are modeled"
                if automated_release_gates_go
                else "automated release evidence gates are missing or blocked"
            ),
        ),
        ReleaseDistributionClosureGate.create(
            gate_id="manual_provider_proof",
            title="Manual provider proof evidence",
            status="go" if manual_provider_proof_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "provider proof is recorded with isolated redacted evidence"
                if manual_provider_proof_go
                else "manual provider proof needs explicit operator-run evidence"
            ),
        ),
        ReleaseDistributionClosureGate.create(
            gate_id="manual_test_vault",
            title="Manual test-vault export/import/rebuild evidence",
            status="go" if manual_test_vault_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "test-vault export/import/rebuild proof is recorded with isolated redacted evidence"
                if manual_test_vault_go
                else "manual test-vault export/import/rebuild proof needs explicit operator-run evidence"
            ),
        ),
        ReleaseDistributionClosureGate.create(
            gate_id="known_limits",
            title="Known limits review",
            status="go" if known_limits_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "known limits are reviewed without implying deploy, tag or distribution execution"
                if known_limits_go
                else "known limits review needs update"
            ),
        ),
        ReleaseDistributionClosureGate.create(
            gate_id="plugin_release_gate",
            title="Plugin release gate",
            status="go" if plugin_release_gate_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "plugin release gate is modeled separately from release decision language"
                if plugin_release_gate_go
                else "plugin release gate needs repair"
            ),
        ),
        ReleaseDistributionClosureGate.create(
            gate_id="release_readiness_pipeline",
            title="Release readiness pipeline",
            status="go" if release_readiness_pipeline_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "readiness pipeline aggregates snapshots, manual evidence and plugin gates"
                if release_readiness_pipeline_go
                else "release readiness pipeline needs aggregation work"
            ),
        ),
        ReleaseDistributionClosureGate.create(
            gate_id="closeout_language",
            title="Evidence-Go closeout language",
            status="go" if closeout_language_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "closeout language separates Evidence-Go from deploy, tag and distribution"
                if closeout_language_go
                else "closeout language could blur Evidence-Go with deployment"
            ),
        ),
        ReleaseDistributionClosureGate.create(
            gate_id="live_phase_boundaries",
            title="Live phase boundary gates",
            status="go" if live_phase_boundaries_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "provider, export/import/rebuild, host, Telegram and network actions remain separate operator gates"
                if live_phase_boundaries_go
                else "live phase boundary gates need explicit separation"
            ),
        ),
        ReleaseDistributionClosureGate.create(
            gate_id="mvp_roadmap_aggregate",
            title="MVP roadmap aggregate for 1.0",
            status="go" if mvp_roadmap_aggregate_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "MVP MasterRoadmap aggregate and UI-live gate are modeled before Version 1.0 can be claimed"
                if mvp_roadmap_aggregate_go
                else "release language still needs all ten MVP roadmaps at 100 before Version 1.0 can be claimed"
            ),
        ),
        ReleaseDistributionClosureGate.create(
            gate_id="deploy_tag_distribution",
            title="Deploy, tag and distribution execution",
            status="go" if deploy_tag_distribution_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "deploy, tag and distribution execution are explicitly approved and evidenced"
                if deploy_tag_distribution_go
                else "deploy, tag and distribution remain separate explicit operator Go gates"
            ),
        ),
        ReleaseDistributionClosureGate.create(
            gate_id="new_ui_release_gate",
            title="New UI live release gate",
            status="go" if new_ui_release_gate_go else "needs_design",
            slice_class="needs_design",
            reason=(
                "new UI is live and included in Version 1.0 release language"
                if new_ui_release_gate_go
                else "Version 1.0 still requires the new UI to be live"
            ),
        ),
    )
    percent_complete = _percent(gates)
    first_incomplete = _first_incomplete(gates)
    if first_incomplete is None:
        why_not_100 = "-"
        next_decision = "Roadmap 8 is complete; continue to Image Tools Worker Final Smoke."
    else:
        why_not_100 = f"{first_incomplete.title}: {first_incomplete.reason}"
        if first_incomplete.status == "repo_open":
            next_decision = "Keep release language tied to the MVP roadmap aggregate; do not claim Version 1.0 before all ten MVP roadmaps and UI are complete."
        elif first_incomplete.slice_class == "needs_live_go":
            next_decision = "Grant or defer deploy/tag/distribution execution separately from Evidence-Go."
        elif first_incomplete.slice_class == "needs_design":
            next_decision = "Keep Version 1.0 release pending until the new UI is live."
        else:
            next_decision = f"Resolve {first_incomplete.title} before release/distribution closure."
    return ReleaseDistributionClosureReport(
        roadmap_id="release_distribution_evidence",
        title="Release / Distribution Evidence",
        gates=gates,
        percent_complete=percent_complete,
        why_not_100=why_not_100,
        recommended_next_human_decision=next_decision,
    )

"""MVP Roadmap 6 ORCA/Lens backend migration progress model."""

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
        raise ValueError("unsupported ORCA closure gate status")
    return text


def _normalize_slice_class(value: Any) -> str:
    text = _normalize_text(value, field_name="slice_class").strip().lower()
    if text not in _SLICE_CLASSES:
        raise ValueError("unsupported ORCA closure slice class")
    return text


@dataclass(frozen=True, slots=True)
class OrcaLensClosureGate:
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
    ) -> "OrcaLensClosureGate":
        return cls(
            gate_id=_normalize_text(gate_id, field_name="gate_id").strip().lower(),
            title=_normalize_text(title, field_name="title"),
            status=_normalize_status(status),
            slice_class=_normalize_slice_class(slice_class),
            reason=_normalize_text(reason, field_name="reason"),
        )

    @property
    def complete(self) -> bool:
        return self.status in {"go", "deferred"}

    def to_dict(self) -> dict[str, str]:
        return {
            "gate_id": self.gate_id,
            "title": self.title,
            "status": self.status,
            "slice_class": self.slice_class,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class OrcaLensClosureReport:
    roadmap_id: str
    title: str
    gates: tuple[OrcaLensClosureGate, ...]
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
        return f"| 6 | {self.title} | {self.percent_complete} | {reason} |"


def _percent(gates: Iterable[OrcaLensClosureGate]) -> int:
    items = tuple(gates)
    if not items:
        return 0
    complete = sum(1 for gate in items if gate.complete)
    return round((complete / len(items)) * 100)


def _first_incomplete(gates: Iterable[OrcaLensClosureGate]) -> OrcaLensClosureGate | None:
    for gate in gates:
        if not gate.complete:
            return gate
    return None


def build_orca_lens_closure_report(
    *,
    roadmap_and_delegation_go: bool = True,
    naming_contract_go: bool = True,
    technical_boundary_audit_go: bool = True,
    compatibility_design_go: bool = True,
    env_tool_provider_aliases_go: bool = True,
    route_aliases_go: bool = True,
    orca_core_modules_go: bool = True,
    frontend_lens_redesign_go: bool = False,
    frontend_lens_redesign_deferred: bool = True,
    legacy_deprecation_go: bool = True,
    data_path_migration_go: bool = False,
    data_path_migration_deferred: bool = True,
) -> OrcaLensClosureReport:
    gates = (
        OrcaLensClosureGate.create(
            gate_id="roadmap_and_delegation",
            title="ORCA roadmap and delegation",
            status="go" if roadmap_and_delegation_go else "blocked",
            slice_class="repo_only",
            reason=(
                "canonical ORCA migration roadmap and ABC slices exist"
                if roadmap_and_delegation_go
                else "ORCA migration roadmap or delegation is missing"
            ),
        ),
        OrcaLensClosureGate.create(
            gate_id="naming_contract",
            title="ORCA naming contract",
            status="go" if naming_contract_go else "blocked",
            slice_class="repo_only",
            reason=(
                "ORCA, Local Markdown Vault, Lens, Atlas and legacy Obsidian wording are defined"
                if naming_contract_go
                else "ORCA naming and operator language contract is missing"
            ),
        ),
        OrcaLensClosureGate.create(
            gate_id="technical_boundary_audit",
            title="Technical boundary audit",
            status="go" if technical_boundary_audit_go else "blocked",
            slice_class="repo_only",
            reason=(
                "ORCA core, source adapter, Lens and legacy compatibility boundaries are mapped"
                if technical_boundary_audit_go
                else "technical boundary audit is missing"
            ),
        ),
        OrcaLensClosureGate.create(
            gate_id="compatibility_design",
            title="Compatibility surface design",
            status="go" if compatibility_design_go else "blocked",
            slice_class="repo_only",
            reason=(
                "route, tool, env and docs compatibility design exists without deleting legacy surfaces"
                if compatibility_design_go
                else "compatibility surface design is missing"
            ),
        ),
        OrcaLensClosureGate.create(
            gate_id="env_tool_provider_aliases",
            title="Env, tool and provider aliases",
            status="go" if env_tool_provider_aliases_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "ODYSSEUS_ORCA flags, orca_* tools and orca.vault_context provider aliases are tested"
                if env_tool_provider_aliases_go
                else "ORCA env/tool/provider aliases need implementation or tests"
            ),
        ),
        OrcaLensClosureGate.create(
            gate_id="route_aliases",
            title="ORCA route aliases",
            status="go" if route_aliases_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "/api/plugins/orca aliases are mounted while legacy /api/plugins/obsidian routes remain intact"
                if route_aliases_go
                else "ORCA route aliases need implementation or tests"
            ),
        ),
        OrcaLensClosureGate.create(
            gate_id="orca_core_modules",
            title="ORCA core modules",
            status="go" if orca_core_modules_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "memory graph, retrieval, readiness, RAPTOR, query and Lens contracts sit behind ORCA modules"
                if orca_core_modules_go
                else "memory graph, retrieval, readiness, RAPTOR, query and Lens contracts still need ORCA core extraction behind adapters"
            ),
        ),
        OrcaLensClosureGate.create(
            gate_id="frontend_lens_redesign",
            title="Frontend Lens naming",
            status=(
                "go"
                if frontend_lens_redesign_go
                else "deferred"
                if frontend_lens_redesign_deferred
                else "needs_design"
            ),
            slice_class="needs_design",
            reason=(
                "new UI uses ORCA/Lens wording live"
                if frontend_lens_redesign_go
                else "frontend Lens wording is deliberately deferred to the shared UI redesign"
                if frontend_lens_redesign_deferred
                else "frontend Lens wording is parked until the shared UI redesign"
            ),
        ),
        OrcaLensClosureGate.create(
            gate_id="legacy_deprecation",
            title="Legacy Obsidian deprecation",
            status="go" if legacy_deprecation_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "legacy Obsidian compatibility surfaces have warnings, migration callers, and removal gates"
                if legacy_deprecation_go
                else "legacy Obsidian deprecation warnings, caller migration and removal gates are not complete"
            ),
        ),
        OrcaLensClosureGate.create(
            gate_id="data_path_migration",
            title="Data path migration and final removal plan",
            status=(
                "go"
                if data_path_migration_go
                else "deferred"
                if data_path_migration_deferred
                else "needs_live_go"
            ),
            slice_class="needs_live_go",
            reason=(
                "data-path migration and final Obsidian removal are proven with rollback"
                if data_path_migration_go
                else "data path migration and final Obsidian removal are deliberately deferred; ORCA stays canonical while legacy compatibility remains"
                if data_path_migration_deferred
                else "data path migration and final Obsidian removal require explicit operator Go and rollback evidence"
            ),
        ),
    )
    percent_complete = _percent(gates)
    first_incomplete = _first_incomplete(gates)
    if first_incomplete is None:
        why_not_100 = "-"
        next_decision = "Roadmap 6 is complete; continue to PlanRuntime / Visual Planning Logic."
    else:
        why_not_100 = f"{first_incomplete.title}: {first_incomplete.reason}"
        if first_incomplete.status == "repo_open":
            next_decision = "Continue backend-safe ORCA migration, starting with ORCA core modules behind adapters."
        elif first_incomplete.slice_class == "needs_design":
            next_decision = "Keep ORCA/Lens UI wording parked until the shared UI redesign."
        elif first_incomplete.slice_class == "needs_live_go":
            next_decision = "Grant or defer data-path migration before claiming final Obsidian removal."
        else:
            next_decision = f"Resolve {first_incomplete.title} before ORCA/Lens closure."
    return OrcaLensClosureReport(
        roadmap_id="orca_lens_naming_backend_migration",
        title="ORCA / Lens Naming & Backend Migration",
        gates=gates,
        percent_complete=percent_complete,
        why_not_100=why_not_100,
        recommended_next_human_decision=next_decision,
    )

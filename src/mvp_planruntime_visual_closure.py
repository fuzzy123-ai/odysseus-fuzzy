"""MVP Roadmap 7 PlanRuntime / visual planning logic progress model."""

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
        raise ValueError("unsupported PlanRuntime closure gate status")
    return text


def _normalize_slice_class(value: Any) -> str:
    text = _normalize_text(value, field_name="slice_class").strip().lower()
    if text not in _SLICE_CLASSES:
        raise ValueError("unsupported PlanRuntime closure slice class")
    return text


@dataclass(frozen=True, slots=True)
class PlanRuntimeVisualClosureGate:
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
    ) -> "PlanRuntimeVisualClosureGate":
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
class PlanRuntimeVisualClosureReport:
    roadmap_id: str
    title: str
    gates: tuple[PlanRuntimeVisualClosureGate, ...]
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
        return f"| 7 | {self.title} | {self.percent_complete} | {reason} |"


def _percent(gates: Iterable[PlanRuntimeVisualClosureGate]) -> int:
    items = tuple(gates)
    if not items:
        return 0
    complete = sum(1 for gate in items if gate.complete)
    return round((complete / len(items)) * 100)


def _first_incomplete(gates: Iterable[PlanRuntimeVisualClosureGate]) -> PlanRuntimeVisualClosureGate | None:
    for gate in gates:
        if not gate.complete:
            return gate
    return None


def build_planruntime_visual_closure_report(
    *,
    planruntime_source_go: bool = True,
    subagent_plan_binding_go: bool = True,
    roadmap_lens_readonly_go: bool = True,
    visual_readonly_snapshot_go: bool = True,
    edit_validator_go: bool = True,
    proposal_queue_go: bool = True,
    acceptance_contract_go: bool = True,
    mutation_patch_contract_go: bool = True,
    apply_adapter_go: bool = True,
    route_contracts_go: bool = True,
    browser_editor_ui_go: bool = False,
    browser_editor_ui_deferred: bool = True,
    post_apply_dispatch_go: bool = True,
) -> PlanRuntimeVisualClosureReport:
    gates = (
        PlanRuntimeVisualClosureGate.create(
            gate_id="planruntime_source",
            title="PlanRuntime source of truth",
            status="go" if planruntime_source_go else "blocked",
            slice_class="repo_only",
            reason=(
                "structured roadmap JSON loads, validates and projects into PlanGraph"
                if planruntime_source_go
                else "PlanRuntime source-of-truth loader is missing or blocked"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="subagent_plan_binding",
            title="Subagent PlanRuntime binding",
            status="go" if subagent_plan_binding_go else "blocked",
            slice_class="repo_only",
            reason=(
                "subagent run specs are bound to PlanRuntime nodes and context capsules"
                if subagent_plan_binding_go
                else "subagent PlanRuntime binding is missing or blocked"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="roadmap_lens_readonly",
            title="Roadmap Lens read-only graph",
            status="go" if roadmap_lens_readonly_go else "blocked",
            slice_class="repo_only",
            reason=(
                "Roadmap Lens exposes bounded read-only graph snapshots"
                if roadmap_lens_readonly_go
                else "read-only Roadmap Lens graph is missing or blocked"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="visual_readonly_snapshot",
            title="Visual programming read-only snapshot",
            status="go" if visual_readonly_snapshot_go else "blocked",
            slice_class="repo_only",
            reason=(
                "visual programming snapshot exposes policy-gated controls without mutation"
                if visual_readonly_snapshot_go
                else "visual programming read-only snapshot is missing or blocked"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="edit_validator",
            title="Visual edit dry-run validator",
            status="go" if edit_validator_go else "blocked",
            slice_class="repo_only",
            reason=(
                "create-node and connect-dependency proposals validate without writing or starting agents"
                if edit_validator_go
                else "visual edit dry-run validator is missing or blocked"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="proposal_queue",
            title="Proposal review queue",
            status="go" if proposal_queue_go else "blocked",
            slice_class="repo_only",
            reason=(
                "validated proposals are exposed as read-only review queue items"
                if proposal_queue_go
                else "proposal review queue is missing or blocked"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="acceptance_contract",
            title="Operator acceptance contract",
            status="go" if acceptance_contract_go else "blocked",
            slice_class="repo_only",
            reason=(
                "operator-gated acceptance creates auditable accepted/rejected events"
                if acceptance_contract_go
                else "operator acceptance contract is missing or blocked"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="mutation_patch_contract",
            title="Mutation patch contract",
            status="go" if mutation_patch_contract_go else "blocked",
            slice_class="repo_only",
            reason=(
                "accepted proposals become mutation patches with audit and version metadata"
                if mutation_patch_contract_go
                else "mutation patch contract is missing or blocked"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="apply_adapter",
            title="Mutation apply adapter",
            status="go" if apply_adapter_go else "blocked",
            slice_class="repo_only",
            reason=(
                "authorized patches apply to roadmap payloads with rollback metadata and no implicit agent start"
                if apply_adapter_go
                else "mutation apply adapter is missing or blocked"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="route_contracts",
            title="Admin route contracts",
            status="go" if route_contracts_go else "blocked",
            slice_class="repo_only",
            reason=(
                "Roadmap graph, visual snapshot, validate, review, acceptance, patch and apply routes are admin-protected"
                if route_contracts_go
                else "admin route contracts are missing or blocked"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="browser_editor_ui",
            title="Browser proposal editor UI",
            status=(
                "go"
                if browser_editor_ui_go
                else "deferred"
                if browser_editor_ui_deferred
                else "needs_design"
            ),
            slice_class="needs_design",
            reason=(
                "browser proposal editor UI is live on the redesigned interface"
                if browser_editor_ui_go
                else "browser proposal editor UI is deliberately deferred until the shared UI redesign"
                if browser_editor_ui_deferred
                else "browser proposal editor UI is deferred until the shared UI redesign"
            ),
        ),
        PlanRuntimeVisualClosureGate.create(
            gate_id="post_apply_dispatch",
            title="Post-apply agent dispatch",
            status="go" if post_apply_dispatch_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "post-apply dispatch request is confirmed, auditable and kept separate from runtime execution"
                if post_apply_dispatch_go
                else "post-apply dispatch remains a request object and is not wired into orchestration runtime"
            ),
        ),
    )
    percent_complete = _percent(gates)
    first_incomplete = _first_incomplete(gates)
    if first_incomplete is None:
        why_not_100 = "-"
        next_decision = "Roadmap 7 is complete; continue to Release / Distribution Evidence."
    else:
        why_not_100 = f"{first_incomplete.title}: {first_incomplete.reason}"
        if first_incomplete.slice_class == "needs_design":
            next_decision = "Keep the PlanRuntime browser editor parked until the shared UI redesign."
        elif first_incomplete.status == "repo_open":
            next_decision = "Continue backend-safe PlanRuntime work by wiring post-apply dispatch through a separate orchestration confirmation gate."
        else:
            next_decision = f"Resolve {first_incomplete.title} before PlanRuntime closure."
    return PlanRuntimeVisualClosureReport(
        roadmap_id="planruntime_visual_planning_logic",
        title="PlanRuntime / Visual Planning Logic",
        gates=gates,
        percent_complete=percent_complete,
        why_not_100=why_not_100,
        recommended_next_human_decision=next_decision,
    )

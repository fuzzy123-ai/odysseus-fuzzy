"""MVP Roadmap 10 GameDev mount write-smoke progress model."""

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
        raise ValueError("unsupported gamedev mount closure gate status")
    return text


def _normalize_slice_class(value: Any) -> str:
    text = _normalize_text(value, field_name="slice_class").strip().lower()
    if text not in _SLICE_CLASSES:
        raise ValueError("unsupported gamedev mount closure slice class")
    return text


@dataclass(frozen=True, slots=True)
class GameDevMountClosureGate:
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
    ) -> "GameDevMountClosureGate":
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
class GameDevMountClosureReport:
    roadmap_id: str
    title: str
    gates: tuple[GameDevMountClosureGate, ...]
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
        return f"| 10 | {self.title} | {self.percent_complete} | {reason} |"


def _percent(gates: Iterable[GameDevMountClosureGate]) -> int:
    items = tuple(gates)
    if not items:
        return 0
    complete = sum(1 for gate in items if gate.complete)
    return round((complete / len(items)) * 100)


def _first_incomplete(gates: Iterable[GameDevMountClosureGate]) -> GameDevMountClosureGate | None:
    for gate in gates:
        if not gate.complete:
            return gate
    return None


def build_gamedev_mount_closure_report(
    *,
    mount_profile_go: bool = True,
    runtime_read_smoke_go: bool = True,
    path_owner_scope_go: bool = True,
    write_policy_guards_go: bool = True,
    command_gate_go: bool = True,
    operator_runbook_go: bool = True,
    reversible_write_plan_go: bool = True,
    manual_write_smoke_go: bool = False,
) -> GameDevMountClosureReport:
    gates = (
        GameDevMountClosureGate.create(
            gate_id="mount_profile",
            title="GameDev mount profile",
            status="go" if mount_profile_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "safe Godot mount profile includes write extensions, backup policy and broad-root rejection"
                if mount_profile_go
                else "safe Godot mount profile still needs implementation"
            ),
        ),
        GameDevMountClosureGate.create(
            gate_id="runtime_read_smoke",
            title="Runtime config and read smoke",
            status="go" if runtime_read_smoke_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "runtime config validation and read-only virtual mount smoke are recorded without host-path leakage"
                if runtime_read_smoke_go
                else "runtime config validation or read-only smoke needs operator evidence"
            ),
        ),
        GameDevMountClosureGate.create(
            gate_id="path_owner_scope",
            title="Path and owner scope",
            status="go" if path_owner_scope_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "virtual mount access is owner-scoped and blocks traversal, broad roots and sensitive paths"
                if path_owner_scope_go
                else "path or owner-scope checks need repair"
            ),
        ),
        GameDevMountClosureGate.create(
            gate_id="write_policy_guards",
            title="Write policy guards",
            status="go" if write_policy_guards_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "write_file/edit_file require explicit tools, allowed extensions, size limits and symlink checks"
                if write_policy_guards_go
                else "write policy guard coverage is incomplete"
            ),
        ),
        GameDevMountClosureGate.create(
            gate_id="command_gate",
            title="Named command gate",
            status="go" if command_gate_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "GameDev commands are named argv plans and free-form shell is rejected"
                if command_gate_go
                else "GameDev command gate still allows unsafe command shapes"
            ),
        ),
        GameDevMountClosureGate.create(
            gate_id="operator_runbook",
            title="Operator runbook",
            status="go" if operator_runbook_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "runbook documents enablement, dry-run validation, smoke steps and cleanup constraints"
                if operator_runbook_go
                else "operator runbook is missing or incomplete"
            ),
        ),
        GameDevMountClosureGate.create(
            gate_id="reversible_write_plan",
            title="Reversible write-smoke plan",
            status="go" if reversible_write_plan_go else "repo_open",
            slice_class="safe_offline",
            reason=(
                "write-smoke target and cleanup are planned with virtual paths, size limits and operator gate"
                if reversible_write_plan_go
                else "reversible write-smoke plan is missing"
            ),
        ),
        GameDevMountClosureGate.create(
            gate_id="manual_write_smoke",
            title="Manual write smoke",
            status="go" if manual_write_smoke_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "operator-approved write smoke wrote and cleaned a small artifact with redacted evidence"
                if manual_write_smoke_go
                else "optional write smoke mutates the project mount and needs explicit operator Go"
            ),
        ),
    )
    percent_complete = _percent(gates)
    first_incomplete = _first_incomplete(gates)
    if first_incomplete is None:
        why_not_100 = "-"
        next_decision = "Roadmap 10 is complete; keep Version 1.0 pending until all MVP roadmaps and the new UI are complete."
    else:
        why_not_100 = f"{first_incomplete.title}: {first_incomplete.reason}"
        if first_incomplete.slice_class == "needs_live_go":
            next_decision = "Grant or defer the optional GameDev write smoke; do not run it without explicit operator Go."
        elif first_incomplete.status == "repo_open":
            next_decision = "Continue backend-safe mount closure by repairing the offline GameDev write gate."
        else:
            next_decision = f"Resolve {first_incomplete.title} before GameDev mount closure."
    return GameDevMountClosureReport(
        roadmap_id="gamedev_mount_write_smoke",
        title="GameDev Mount Write Smoke",
        gates=gates,
        percent_complete=percent_complete,
        why_not_100=why_not_100,
        recommended_next_human_decision=next_decision,
    )

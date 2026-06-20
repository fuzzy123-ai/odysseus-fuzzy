"""Read-only operator plan for the manual test-vault rebuild proof."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "test_vault_selected",
    "export_artifact_plan",
    "isolated_import_target",
    "rebuild_verification_plan",
    "evidence_redaction_review",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_operator_input",
    "deferred",
)

_DECISION_VALUES = (
    "ready_for_manual_operator_run",
    "needs_operator_input",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "select a minimal test vault manually",
    "define the export artifact capture plan",
    "prepare an isolated import target manually",
    "review rebuild verification evidence before recording results",
)

_BLOCKED_LIVE_ACTIONS = (
    "export_execution",
    "import_execution",
    "rebuild_execution",
    "host_path_capture",
    "raw_log_capture",
    "automatic_release_go",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported test vault rebuild gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported test vault rebuild gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported test vault rebuild decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class TestVaultRebuildGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "TestVaultRebuildGate":
        return cls(
            gate_id=_normalize_gate_id(gate_id),
            status=_normalize_gate_status(status),
            summary=_normalize_text(summary, field_name="summary"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class TestVaultRebuildDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LiveTestVaultRebuildPlan:
    gates: tuple[TestVaultRebuildGate, ...]
    decision: TestVaultRebuildDecision
    next_allowed_actions: tuple[str, ...]
    blocked_live_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
            "blocked_live_actions": self.blocked_live_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Test Vault Rebuild Plan",
            "",
            f"- Decision: `{self.decision.decision}`",
            f"- Next action: {self.decision.next_action}",
            "",
            "## Gates",
        ]
        for gate in self.gates:
            lines.append(f"- `{gate.gate_id}`: {gate.status} - {gate.summary}")
        if self.next_allowed_actions:
            lines.extend(["", "## Next Allowed Actions"])
            for action in self.next_allowed_actions:
                lines.append(f"- {action}")
        lines.extend(["", "## Blocked Live Actions"])
        for action in self.blocked_live_actions:
            lines.append(f"- {action}")
        return "\n".join(lines).rstrip()


def build_live_test_vault_rebuild_plan(
    *,
    test_vault_selected: bool = False,
    export_artifact_plan_ready: bool = False,
    isolated_import_target_ready: bool = False,
    rebuild_verification_plan_ready: bool = False,
    evidence_redaction_ready: bool = False,
) -> LiveTestVaultRebuildPlan:
    gate_map = {
        "test_vault_selected": TestVaultRebuildGate.create(
            gate_id="test_vault_selected",
            status="go" if test_vault_selected else "needs_operator_input",
            summary=(
                "a small test vault has been selected for the manual proof run"
                if test_vault_selected
                else "manual selection of a small test vault is still required"
            ),
        ),
        "export_artifact_plan": TestVaultRebuildGate.create(
            gate_id="export_artifact_plan",
            status="go" if export_artifact_plan_ready else "needs_operator_input",
            summary=(
                "the export artifact capture plan is prepared for the manual proof run"
                if export_artifact_plan_ready
                else "manual preparation of the export artifact plan is still required"
            ),
        ),
        "isolated_import_target": TestVaultRebuildGate.create(
            gate_id="isolated_import_target",
            status="go" if isolated_import_target_ready else "needs_operator_input",
            summary=(
                "an isolated import target is prepared for the manual proof run"
                if isolated_import_target_ready
                else "manual preparation of an isolated import target is still required"
            ),
        ),
        "rebuild_verification_plan": TestVaultRebuildGate.create(
            gate_id="rebuild_verification_plan",
            status="go" if rebuild_verification_plan_ready else "needs_operator_input",
            summary=(
                "the rebuild verification plan is prepared for the manual proof run"
                if rebuild_verification_plan_ready
                else "manual preparation of the rebuild verification plan is still required"
            ),
        ),
        "evidence_redaction_review": TestVaultRebuildGate.create(
            gate_id="evidence_redaction_review",
            status="go" if evidence_redaction_ready else "needs_operator_input",
            summary=(
                "evidence redaction rules are ready for the manual rebuild proof run"
                if evidence_redaction_ready
                else "manual evidence-redaction review is still required"
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))
    blocked_gates = tuple(gate.gate_id for gate in gates if gate.status == "blocked")

    if blocked_gates:
        decision_value = "blocked"
        next_action = "resolve blocked test-vault rebuild prerequisites before planning the operator run"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "ready_for_manual_operator_run"
        next_action = "perform the manual test-vault export/import/rebuild proof with evidence capture"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred test-vault planning inputs before operator review"
    else:
        decision_value = "needs_operator_input"
        next_action = "complete the remaining manual test-vault proof preparation inputs"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LiveTestVaultRebuildPlan(
        gates=gates,
        decision=TestVaultRebuildDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
        blocked_live_actions=_normalize_tuple(_BLOCKED_LIVE_ACTIONS, field_name="blocked_live_action"),
    )

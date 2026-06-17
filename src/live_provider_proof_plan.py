"""Read-only operator plan for the manual provider proof run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "ready_query_index_precheck",
    "default_model_answer_run",
    "fallback_model_answer_run",
    "local_or_deepseek_availability",
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
    "verify query-index readiness manually",
    "confirm default-model answer capture plan",
    "confirm fallback-model answer capture plan",
    "review evidence redaction before recording results",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported provider proof gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported provider proof gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported provider proof decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class ProviderProofGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "ProviderProofGate":
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
class ProviderProofDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LiveProviderProofPlan:
    gates: tuple[ProviderProofGate, ...]
    decision: ProviderProofDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Provider Proof Plan",
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
        return "\n".join(lines).rstrip()


def build_live_provider_proof_plan(
    *,
    query_index_ready: bool = False,
    default_model_inputs_ready: bool = False,
    fallback_model_inputs_ready: bool = False,
    local_or_deepseek_available: bool = False,
    evidence_redaction_ready: bool = False,
) -> LiveProviderProofPlan:
    gate_map = {
        "ready_query_index_precheck": ProviderProofGate.create(
            gate_id="ready_query_index_precheck",
            status="go" if query_index_ready else "needs_operator_input",
            summary=(
                "query index precheck is ready for the manual provider proof run"
                if query_index_ready
                else "manual confirmation of query index readiness is still required"
            ),
        ),
        "default_model_answer_run": ProviderProofGate.create(
            gate_id="default_model_answer_run",
            status="go" if default_model_inputs_ready else "needs_operator_input",
            summary=(
                "default-model answer capture inputs are prepared for the operator run"
                if default_model_inputs_ready
                else "manual preparation of default-model answer capture inputs is still required"
            ),
        ),
        "fallback_model_answer_run": ProviderProofGate.create(
            gate_id="fallback_model_answer_run",
            status="go" if fallback_model_inputs_ready else "needs_operator_input",
            summary=(
                "fallback-model answer capture inputs are prepared for the operator run"
                if fallback_model_inputs_ready
                else "manual preparation of fallback-model answer capture inputs is still required"
            ),
        ),
        "local_or_deepseek_availability": ProviderProofGate.create(
            gate_id="local_or_deepseek_availability",
            status="go" if local_or_deepseek_available else "needs_operator_input",
            summary=(
                "the local or DeepSeek availability check is prepared for operator verification"
                if local_or_deepseek_available
                else "manual confirmation of local or DeepSeek availability is still required"
            ),
        ),
        "evidence_redaction_review": ProviderProofGate.create(
            gate_id="evidence_redaction_review",
            status="go" if evidence_redaction_ready else "needs_operator_input",
            summary=(
                "evidence redaction rules are ready for the manual provider proof run"
                if evidence_redaction_ready
                else "manual evidence-redaction review is still required"
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))
    blocked_gates = tuple(gate.gate_id for gate in gates if gate.status == "blocked")
    manual_inputs_missing = tuple(gate.gate_id for gate in gates if gate.status == "needs_operator_input")

    if blocked_gates:
        decision_value = "blocked"
        next_action = "resolve blocked provider-proof prerequisites before planning the operator run"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "ready_for_manual_operator_run"
        next_action = "perform the manual provider and fallback answer run with evidence capture"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred provider-proof planning inputs before operator review"
    else:
        decision_value = "needs_operator_input"
        next_action = "complete the remaining manual provider-proof preparation inputs"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LiveProviderProofPlan(
        gates=gates,
        decision=ProviderProofDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
    )

"""Read-only planning model for a future system health host-agent MVP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "operator_host_scope_selected",
    "install_method_reviewed",
    "snapshot_api_contract_ready",
    "permissions_reviewed",
    "rollback_plan_ready",
    "secrets_policy_ready",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_operator_input",
    "deferred",
)

_DECISION_VALUES = (
    "host_agent_plan_ready",
    "needs_operator_input",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review the host scope and installation method manually",
    "confirm the snapshot API contract and permissions review offline",
    "prepare rollback and secrets-policy notes before any host-agent follow-up",
    "keep runtime execution disabled during operator planning",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported host agent plan gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported host agent plan gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported host agent plan decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class HostAgentPlanGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "HostAgentPlanGate":
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
class HostAgentPlanDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LiveSystemHealthHostAgentPlan:
    gates: tuple[HostAgentPlanGate, ...]
    decision: HostAgentPlanDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live System Health Host Agent MVP Plan",
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


def build_live_system_health_host_agent_plan(
    *,
    operator_host_scope_selected: bool = False,
    install_method_reviewed: bool = False,
    snapshot_api_contract_ready: bool = False,
    permissions_reviewed: bool = False,
    rollback_plan_ready: bool = False,
    secrets_policy_ready: bool = False,
    runtime_execution_enabled: bool = False,
    host_command_enabled: bool = False,
    systemd_enabled: bool = False,
    socket_access_enabled: bool = False,
    network_enabled: bool = False,
    token_present: bool = False,
) -> LiveSystemHealthHostAgentPlan:
    unsafe_runtime_claimed = any(
        (
            runtime_execution_enabled,
            host_command_enabled,
            systemd_enabled,
            socket_access_enabled,
            network_enabled,
            token_present,
        )
    )
    gate_map = {
        "operator_host_scope_selected": HostAgentPlanGate.create(
            gate_id="operator_host_scope_selected",
            status="go" if operator_host_scope_selected else "needs_operator_input",
            summary=(
                "operator host scope is selected for the MVP plan"
                if operator_host_scope_selected
                else "manual host-scope selection is still required"
            ),
        ),
        "install_method_reviewed": HostAgentPlanGate.create(
            gate_id="install_method_reviewed",
            status="go" if install_method_reviewed else "needs_operator_input",
            summary=(
                "install method review is prepared for the MVP plan"
                if install_method_reviewed
                else "manual review of the install method is still required"
            ),
        ),
        "snapshot_api_contract_ready": HostAgentPlanGate.create(
            gate_id="snapshot_api_contract_ready",
            status="go" if snapshot_api_contract_ready else "needs_operator_input",
            summary=(
                "snapshot API contract is prepared for offline host-agent review"
                if snapshot_api_contract_ready
                else "manual review of the snapshot API contract is still required"
            ),
        ),
        "permissions_reviewed": HostAgentPlanGate.create(
            gate_id="permissions_reviewed",
            status="go" if permissions_reviewed else "needs_operator_input",
            summary=(
                "permissions review is prepared for the host-agent MVP plan"
                if permissions_reviewed
                else "manual permissions review is still required"
            ),
        ),
        "rollback_plan_ready": HostAgentPlanGate.create(
            gate_id="rollback_plan_ready",
            status="go" if rollback_plan_ready else "needs_operator_input",
            summary=(
                "rollback planning is prepared for the host-agent MVP plan"
                if rollback_plan_ready
                else "manual rollback-plan review is still required"
            ),
        ),
        "secrets_policy_ready": HostAgentPlanGate.create(
            gate_id="secrets_policy_ready",
            status="blocked" if unsafe_runtime_claimed else ("go" if secrets_policy_ready else "needs_operator_input"),
            summary=(
                "runtime/host/systemd/socket/network/token enablement was claimed, which blocks the host-agent plan"
                if unsafe_runtime_claimed
                else (
                    "secrets policy is prepared for the host-agent MVP plan"
                    if secrets_policy_ready
                    else "manual secrets-policy review is still required"
                )
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "remove runtime/host/systemd/socket/network/token enablement claims and return to offline planning"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "host_agent_plan_ready"
        next_action = "perform manual operator review of the host-agent MVP plan while keeping runtime execution disabled"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred host-agent planning inputs before operator review"
    else:
        decision_value = "needs_operator_input"
        next_action = "complete the remaining host-agent MVP planning inputs before operator review"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LiveSystemHealthHostAgentPlan(
        gates=gates,
        decision=HostAgentPlanDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
    )

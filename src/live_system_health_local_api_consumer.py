"""Read-only planning model for a local system-health API consumer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "snapshot_api_contract_selected",
    "offline_fixture_available",
    "timeout_policy_reviewed",
    "sanitized_payload_reviewed",
    "operator_review_required",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_operator_input",
    "deferred",
)

_DECISION_VALUES = (
    "consumer_plan_ready",
    "needs_operator_input",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review the local snapshot API contract manually",
    "prepare an offline sanitized fixture for consumer review",
    "confirm timeout and payload-redaction policy before any follow-up",
    "keep runtime polling and network access disabled during operator planning",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported local api consumer gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported local api consumer gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported local api consumer decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class LocalApiConsumerGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "LocalApiConsumerGate":
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
class LocalApiConsumerDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LiveSystemHealthLocalApiConsumerPlan:
    gates: tuple[LocalApiConsumerGate, ...]
    decision: LocalApiConsumerDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live System Health Local API Consumer Plan",
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


def build_live_system_health_local_api_consumer_plan(
    *,
    snapshot_api_contract_selected: bool = False,
    offline_fixture_available: bool = False,
    timeout_policy_reviewed: bool = False,
    sanitized_payload_reviewed: bool = False,
    operator_review_required: bool = True,
    network_enabled: bool = False,
    host_access_enabled: bool = False,
    token_present: bool = False,
    runtime_polling_enabled: bool = False,
    unsafe_payload_logging_enabled: bool = False,
) -> LiveSystemHealthLocalApiConsumerPlan:
    unsafe_runtime_claimed = any(
        (
            network_enabled,
            host_access_enabled,
            token_present,
            runtime_polling_enabled,
            unsafe_payload_logging_enabled,
        )
    )
    gate_map = {
        "snapshot_api_contract_selected": LocalApiConsumerGate.create(
            gate_id="snapshot_api_contract_selected",
            status="go" if snapshot_api_contract_selected else "needs_operator_input",
            summary=(
                "snapshot API contract is selected for offline consumer planning"
                if snapshot_api_contract_selected
                else "manual review of the snapshot API contract is still required"
            ),
        ),
        "offline_fixture_available": LocalApiConsumerGate.create(
            gate_id="offline_fixture_available",
            status="go" if offline_fixture_available else "needs_operator_input",
            summary=(
                "an offline fixture is available for consumer planning"
                if offline_fixture_available
                else "manual preparation of an offline fixture is still required"
            ),
        ),
        "timeout_policy_reviewed": LocalApiConsumerGate.create(
            gate_id="timeout_policy_reviewed",
            status="go" if timeout_policy_reviewed else "needs_operator_input",
            summary=(
                "timeout policy is reviewed for the local consumer plan"
                if timeout_policy_reviewed
                else "manual timeout-policy review is still required"
            ),
        ),
        "sanitized_payload_reviewed": LocalApiConsumerGate.create(
            gate_id="sanitized_payload_reviewed",
            status="blocked" if unsafe_runtime_claimed else ("go" if sanitized_payload_reviewed else "needs_operator_input"),
            summary=(
                "runtime/network/host/token/unsafe-logging enablement was claimed, which blocks the consumer plan"
                if unsafe_runtime_claimed
                else (
                    "sanitized payload review is complete for the local consumer plan"
                    if sanitized_payload_reviewed
                    else "manual sanitized-payload review is still required"
                )
            ),
        ),
        "operator_review_required": LocalApiConsumerGate.create(
            gate_id="operator_review_required",
            status="go" if operator_review_required else "blocked",
            summary=(
                "operator review remains explicitly required before any consumer follow-up"
                if operator_review_required
                else "operator review requirement was removed, which is not allowed for this consumer plan"
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "restore offline-only planning and remove network, host, token, polling, or unsafe-logging claims"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "consumer_plan_ready"
        next_action = "perform manual operator review of the local API consumer plan without enabling runtime access"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred local consumer planning inputs before operator review"
    else:
        decision_value = "needs_operator_input"
        next_action = "complete the remaining local API consumer planning inputs before operator review"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LiveSystemHealthLocalApiConsumerPlan(
        gates=gates,
        decision=LocalApiConsumerDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
    )

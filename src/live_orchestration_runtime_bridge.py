"""Read-only dry-run planning model for orchestration runtime bridge actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "thread_ref_resolved",
    "mailbox_dispatch_ready",
    "dry_run_payload_valid",
    "operator_review_required",
    "live_send_disabled",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_operator_review",
    "deferred",
)

_DECISION_VALUES = (
    "dry_run_ready",
    "needs_operator_review",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review thread reference resolution manually",
    "review mailbox dispatch payload in dry-run form",
    "keep live send disabled during operator review",
    "capture operator notes before any runtime bridge follow-up",
)

_BLOCKED_LIVE_ACTIONS = (
    "codex_thread_send",
    "odysseus_thread_send",
    "mailbox_dispatch_execution",
    "provider_call",
    "automatic_agent_start",
    "automatic_verified_done",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported live runtime bridge gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported live runtime bridge gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported live runtime bridge decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class LiveRuntimeBridgeGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "LiveRuntimeBridgeGate":
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
class LiveRuntimeBridgeDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LiveOrchestrationRuntimeBridgePlan:
    gates: tuple[LiveRuntimeBridgeGate, ...]
    decision: LiveRuntimeBridgeDecision
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
            "# Live Orchestration Runtime Bridge Dry Run",
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


def build_live_orchestration_runtime_bridge_plan(
    *,
    thread_ref_resolved: bool = False,
    mailbox_dispatch_ready: bool = False,
    dry_run_payload_valid: bool = False,
    operator_review_required: bool = True,
    live_send_disabled: bool = True,
    live_send_enabled: bool = False,
) -> LiveOrchestrationRuntimeBridgePlan:
    live_send_claimed = bool(live_send_enabled) or not bool(live_send_disabled)
    gate_map = {
        "thread_ref_resolved": LiveRuntimeBridgeGate.create(
            gate_id="thread_ref_resolved",
            status="go" if thread_ref_resolved else "needs_operator_review",
            summary=(
                "thread reference is resolved for dry-run planning"
                if thread_ref_resolved
                else "manual review of thread reference resolution is still required"
            ),
        ),
        "mailbox_dispatch_ready": LiveRuntimeBridgeGate.create(
            gate_id="mailbox_dispatch_ready",
            status="go" if mailbox_dispatch_ready else "needs_operator_review",
            summary=(
                "mailbox dispatch shape is prepared for dry-run review"
                if mailbox_dispatch_ready
                else "manual review of mailbox dispatch planning is still required"
            ),
        ),
        "dry_run_payload_valid": LiveRuntimeBridgeGate.create(
            gate_id="dry_run_payload_valid",
            status="go" if dry_run_payload_valid else "needs_operator_review",
            summary=(
                "dry-run payload is valid for operator inspection"
                if dry_run_payload_valid
                else "manual review of dry-run payload validity is still required"
            ),
        ),
        "operator_review_required": LiveRuntimeBridgeGate.create(
            gate_id="operator_review_required",
            status="go" if operator_review_required else "blocked",
            summary=(
                "operator review remains explicitly required before any runtime bridge follow-up"
                if operator_review_required
                else "operator review requirement was removed, which is not allowed for this dry-run model"
            ),
        ),
        "live_send_disabled": LiveRuntimeBridgeGate.create(
            gate_id="live_send_disabled",
            status="blocked" if live_send_claimed else "go",
            summary=(
                "live send was claimed or enabled, which blocks the dry-run bridge plan"
                if live_send_claimed
                else "live send remains disabled during dry-run bridge planning"
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "remove any live-send claim and restore explicit operator-review-only dry-run planning"
    elif all(
        gate.status == "go"
        for gate in gates
        if gate.gate_id in {
            "thread_ref_resolved",
            "mailbox_dispatch_ready",
            "dry_run_payload_valid",
            "operator_review_required",
            "live_send_disabled",
        }
    ):
        decision_value = "dry_run_ready"
        next_action = "perform manual operator review of the dry-run bridge payload without sending anything"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred dry-run planning inputs before operator review"
    else:
        decision_value = "needs_operator_review"
        next_action = "complete the remaining dry-run bridge review inputs before operator signoff"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LiveOrchestrationRuntimeBridgePlan(
        gates=gates,
        decision=LiveRuntimeBridgeDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
        blocked_live_actions=_normalize_tuple(_BLOCKED_LIVE_ACTIONS, field_name="blocked_live_action"),
    )

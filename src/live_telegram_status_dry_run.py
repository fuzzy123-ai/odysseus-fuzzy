"""Read-only planning model for Telegram status dry-run review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_IDS = (
    "status_payload_selected",
    "offline_preview_fixture_available",
    "redaction_policy_reviewed",
    "operator_review_required",
    "send_path_disabled",
)

_GATE_STATUSES = (
    "go",
    "blocked",
    "needs_operator_review",
    "deferred",
)

_DECISION_VALUES = (
    "dry_run_plan_ready",
    "needs_operator_review",
    "blocked",
    "deferred",
)

_DEFAULT_NEXT_ALLOWED_ACTIONS = (
    "review the Telegram status payload manually",
    "prepare an offline preview fixture for operator inspection",
    "confirm redaction policy before any Telegram follow-up",
    "keep send, scheduler, network, and runtime hooks disabled during dry-run planning",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    text = _normalize_text(value, field_name="gate_id").strip().lower()
    if text not in _GATE_IDS:
        raise ValueError("unsupported telegram dry-run gate_id")
    return text


def _normalize_gate_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported telegram dry-run gate status")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISION_VALUES:
        raise ValueError("unsupported telegram dry-run decision")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class TelegramStatusDryRunGate:
    gate_id: str
    status: str
    summary: str

    @classmethod
    def create(cls, *, gate_id: Any, status: Any, summary: Any) -> "TelegramStatusDryRunGate":
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
class TelegramStatusDryRunDecision:
    decision: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "next_action": self.next_action,
        }


@dataclass(frozen=True, slots=True)
class LiveTelegramStatusDryRunPlan:
    gates: tuple[TelegramStatusDryRunGate, ...]
    decision: TelegramStatusDryRunDecision
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gates": tuple(gate.to_dict() for gate in self.gates),
            "decision": self.decision.to_dict(),
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Live Telegram Status Dry Run Plan",
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


def build_live_telegram_status_dry_run_plan(
    *,
    status_payload_selected: bool = False,
    offline_preview_fixture_available: bool = False,
    redaction_policy_reviewed: bool = False,
    operator_review_required: bool = True,
    send_path_disabled: bool = True,
    token_present: bool = False,
    network_enabled: bool = False,
    send_enabled: bool = False,
    scheduler_enabled: bool = False,
    runtime_hook_enabled: bool = False,
    unsafe_payload_logging_enabled: bool = False,
) -> LiveTelegramStatusDryRunPlan:
    unsafe_runtime_claimed = any(
        (
            token_present,
            network_enabled,
            send_enabled,
            scheduler_enabled,
            runtime_hook_enabled,
            unsafe_payload_logging_enabled,
            not send_path_disabled,
        )
    )
    gate_map = {
        "status_payload_selected": TelegramStatusDryRunGate.create(
            gate_id="status_payload_selected",
            status="go" if status_payload_selected else "needs_operator_review",
            summary=(
                "status payload is selected for dry-run review"
                if status_payload_selected
                else "manual review of the status payload is still required"
            ),
        ),
        "offline_preview_fixture_available": TelegramStatusDryRunGate.create(
            gate_id="offline_preview_fixture_available",
            status="go" if offline_preview_fixture_available else "needs_operator_review",
            summary=(
                "offline preview fixture is available for Telegram dry-run review"
                if offline_preview_fixture_available
                else "manual preparation of an offline preview fixture is still required"
            ),
        ),
        "redaction_policy_reviewed": TelegramStatusDryRunGate.create(
            gate_id="redaction_policy_reviewed",
            status="go" if redaction_policy_reviewed else "needs_operator_review",
            summary=(
                "redaction policy is reviewed for Telegram dry-run planning"
                if redaction_policy_reviewed
                else "manual redaction-policy review is still required"
            ),
        ),
        "operator_review_required": TelegramStatusDryRunGate.create(
            gate_id="operator_review_required",
            status="go" if operator_review_required else "blocked",
            summary=(
                "operator review remains explicitly required before any Telegram follow-up"
                if operator_review_required
                else "operator review requirement was removed, which is not allowed for this dry-run model"
            ),
        ),
        "send_path_disabled": TelegramStatusDryRunGate.create(
            gate_id="send_path_disabled",
            status="blocked" if unsafe_runtime_claimed else "go",
            summary=(
                "token, network, send, scheduler, runtime hook, or unsafe logging was enabled, which blocks the dry-run plan"
                if unsafe_runtime_claimed
                else "Telegram send path remains disabled during dry-run review"
            ),
        ),
    }

    gates = tuple(sorted(gate_map.values(), key=lambda item: item.gate_id))

    if any(gate.status == "blocked" for gate in gates):
        decision_value = "blocked"
        next_action = "restore offline-only dry-run conditions and remove token/network/send/runtime claims"
    elif all(gate.status == "go" for gate in gates):
        decision_value = "dry_run_plan_ready"
        next_action = "perform manual operator review of the Telegram status dry-run payload without sending anything"
    elif any(gate.status == "deferred" for gate in gates):
        decision_value = "deferred"
        next_action = "finish deferred Telegram dry-run inputs before operator review"
    else:
        decision_value = "needs_operator_review"
        next_action = "complete the remaining Telegram dry-run review inputs before operator signoff"

    next_allowed_actions = (
        ()
        if decision_value == "blocked"
        else _normalize_tuple(_DEFAULT_NEXT_ALLOWED_ACTIONS, field_name="next_allowed_action")
    )

    return LiveTelegramStatusDryRunPlan(
        gates=gates,
        decision=TelegramStatusDryRunDecision(
            decision=_normalize_decision(decision_value),
            next_action=next_action,
        ),
        next_allowed_actions=next_allowed_actions,
    )

"""Read-only release boundary model for Telegram integration safety."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_ID = "telegram_release_boundary"
_DECISIONS = (
    "telegram_boundary_ready",
    "needs_secret_rotation",
    "blocked",
    "deferred",
)
_STATUSES = (
    "go",
    "blocked",
    "needs_secret_rotation",
    "deferred",
)
_DEFAULT_ACTIONS = (
    "verify token rotation outside the repository through operator-controlled secret handling",
    "confirm environment-only loading, dry-run defaults, and rollback instructions offline",
    "keep network and send paths disabled until a manual live-smoke review is approved",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_decision(value: Any) -> str:
    text = _normalize_text(value, field_name="decision").strip().lower()
    if text not in _DECISIONS:
        raise ValueError("unsupported telegram release boundary decision")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _STATUSES:
        raise ValueError("unsupported telegram release boundary status")
    return text


def _normalize_tuple(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    normalized = [_normalize_text(value, field_name=field_name) for value in values]
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class TelegramReleaseBoundary:
    gate_id: str
    decision: str
    status: str
    summary: str
    next_allowed_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "decision": self.decision,
            "status": self.status,
            "summary": self.summary,
            "next_allowed_actions": self.next_allowed_actions,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Telegram Release Boundary",
            "",
            f"- Gate: `{self.gate_id}`",
            f"- Decision: `{self.decision}`",
            f"- Status: `{self.status}`",
            f"- Summary: {self.summary}",
        ]
        if self.next_allowed_actions:
            lines.extend(["", "## Next Allowed Actions"])
            for action in self.next_allowed_actions:
                lines.append(f"- {action}")
        return "\n".join(lines).rstrip()


def build_telegram_release_boundary(
    *,
    token_rotated_out_of_band: bool = False,
    token_not_persisted: bool = False,
    env_only_secret_loading: bool = False,
    dry_run_plan_recorded: bool = False,
    no_network_default: bool = False,
    no_send_default: bool = False,
    operator_live_smoke_required: bool = False,
    rollback_instruction_recorded: bool = False,
    raw_token_persisted: bool = False,
    raw_token_logged: bool = False,
    token_in_tests: bool = False,
    token_in_docs: bool = False,
    token_in_automation_prompt: bool = False,
    network_enabled_by_default: bool = False,
    send_enabled_by_default: bool = False,
    plugin_scope_touched: bool = False,
    unsafe_secret_handling_enabled: bool = False,
) -> TelegramReleaseBoundary:
    blocked_claimed = any(
        (
            raw_token_persisted,
            raw_token_logged,
            token_in_tests,
            token_in_docs,
            token_in_automation_prompt,
            network_enabled_by_default,
            send_enabled_by_default,
            plugin_scope_touched,
            unsafe_secret_handling_enabled,
        )
    )
    all_positive_gates = all(
        (
            token_rotated_out_of_band,
            token_not_persisted,
            env_only_secret_loading,
            dry_run_plan_recorded,
            no_network_default,
            no_send_default,
            operator_live_smoke_required,
            rollback_instruction_recorded,
        )
    )

    if blocked_claimed:
        decision = "blocked"
        status = "blocked"
        summary = "token persistence/logging leakage, default network/send behavior, plugin scope, or unsafe secret handling was enabled"
    elif all_positive_gates:
        decision = "telegram_boundary_ready"
        status = "go"
        summary = "telegram release boundary is ready with out-of-band rotation, environment-only loading, dry-run defaults, and operator-gated live smoke"
    elif any(
        value is None
        for value in (
            token_rotated_out_of_band,
            token_not_persisted,
            env_only_secret_loading,
            dry_run_plan_recorded,
            no_network_default,
            no_send_default,
            operator_live_smoke_required,
            rollback_instruction_recorded,
        )
    ):
        decision = "deferred"
        status = "deferred"
        summary = "telegram release boundary evidence is deferred until incomplete gate signals are provided"
    else:
        decision = "needs_secret_rotation"
        status = "needs_secret_rotation"
        summary = "telegram release boundary still needs rotation, dry-run, or rollback evidence before review"

    next_allowed_actions = (
        ()
        if decision == "blocked"
        else _normalize_tuple(_DEFAULT_ACTIONS, field_name="next_allowed_action")
    )

    return TelegramReleaseBoundary(
        gate_id=_GATE_ID,
        decision=_normalize_decision(decision),
        status=_normalize_status(status),
        summary=summary,
        next_allowed_actions=next_allowed_actions,
    )

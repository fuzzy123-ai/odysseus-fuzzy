"""Read-only offline smoke plan model for Telegram release checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

_GATE_ID = "telegram_offline_smoke_plan"
_DECISIONS = (
    "telegram_offline_smoke_ready",
    "needs_offline_smoke_evidence",
    "blocked",
    "deferred",
)
_STATUSES = (
    "go",
    "blocked",
    "needs_offline_smoke_evidence",
    "deferred",
)
_DEFAULT_ACTIONS = (
    "Confirm redacted environment variable naming only.",
    "Review offline dry-run payload without any secret or chat-id values.",
    "Keep network and send paths disabled until manual operator go.",
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_decision(value: str) -> str:
    normalized = _normalize_text(value).lower().replace(" ", "_")
    if normalized not in _DECISIONS:
        raise ValueError(f"Unsupported telegram offline smoke decision: {value!r}")
    return normalized


def _normalize_status(value: str) -> str:
    normalized = _normalize_text(value).lower().replace(" ", "_")
    if normalized not in _STATUSES:
        raise ValueError(f"Unsupported telegram offline smoke status: {value!r}")
    return normalized


def _normalize_tuple(values: Iterable[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return tuple(cleaned)


@dataclass(frozen=True)
class TelegramOfflineSmokePlan:
    gate_id: str
    decision: str
    status: str
    summary: str
    next_allowed_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_id", _normalize_text(self.gate_id) or _GATE_ID)
        object.__setattr__(self, "decision", _normalize_decision(self.decision))
        object.__setattr__(self, "status", _normalize_status(self.status))
        object.__setattr__(self, "summary", _normalize_text(self.summary))
        object.__setattr__(
            self,
            "next_allowed_actions",
            _normalize_tuple(self.next_allowed_actions),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "decision": self.decision,
            "status": self.status,
            "summary": self.summary,
            "next_allowed_actions": list(self.next_allowed_actions),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Telegram Offline Smoke Plan",
            "",
            f"- Gate ID: `{self.gate_id}`",
            f"- Decision: `{self.decision}`",
            f"- Status: `{self.status}`",
            f"- Summary: {self.summary}",
        ]
        if self.next_allowed_actions:
            lines.append("- Next Allowed Actions:")
            lines.extend(f"  - {item}" for item in self.next_allowed_actions)
        return "\n".join(lines)


def build_telegram_offline_smoke_plan(
    *,
    redacted_secret_reference_recorded: bool | None = False,
    env_var_name_recorded: bool | None = False,
    dry_run_payload_recorded: bool | None = False,
    send_disabled_recorded: bool | None = False,
    network_disabled_recorded: bool | None = False,
    operator_confirmation_required: bool | None = False,
    rollback_command_documented: bool | None = False,
    live_smoke_deferred_until_manual_go: bool | None = False,
    raw_token_persisted: bool | None = False,
    raw_token_logged: bool | None = False,
    raw_chat_id_persisted: bool | None = False,
    network_enabled: bool | None = False,
    send_enabled: bool | None = False,
    bot_api_called: bool | None = False,
    plugin_scope_touched: bool | None = False,
    unsafe_secret_handling_enabled: bool | None = False,
) -> TelegramOfflineSmokePlan:
    blocked_claimed = any(
        (
            raw_token_persisted,
            raw_token_logged,
            raw_chat_id_persisted,
            network_enabled,
            send_enabled,
            bot_api_called,
            plugin_scope_touched,
            unsafe_secret_handling_enabled,
        )
    )
    all_positive_gates = all(
        (
            redacted_secret_reference_recorded,
            env_var_name_recorded,
            dry_run_payload_recorded,
            send_disabled_recorded,
            network_disabled_recorded,
            operator_confirmation_required,
            rollback_command_documented,
            live_smoke_deferred_until_manual_go,
        )
    )
    if blocked_claimed:
        return TelegramOfflineSmokePlan(
            gate_id=_GATE_ID,
            decision="blocked",
            status="blocked",
            summary=(
                "Offline smoke plan is blocked because unsafe Telegram secret, chat-id, "
                "network, send, API, plugin, or logging behavior was claimed."
            ),
            next_allowed_actions=(),
        )
    if all_positive_gates:
        return TelegramOfflineSmokePlan(
            gate_id=_GATE_ID,
            decision="telegram_offline_smoke_ready",
            status="go",
            summary=(
                "Offline smoke evidence is redacted, environment-name-only, network/send "
                "disabled by default, and live smoke remains deferred pending manual go."
            ),
            next_allowed_actions=_DEFAULT_ACTIONS,
        )
    if any(
        value is None
        for value in (
            redacted_secret_reference_recorded,
            env_var_name_recorded,
            dry_run_payload_recorded,
            send_disabled_recorded,
            network_disabled_recorded,
            operator_confirmation_required,
            rollback_command_documented,
            live_smoke_deferred_until_manual_go,
        )
    ):
        return TelegramOfflineSmokePlan(
            gate_id=_GATE_ID,
            decision="deferred",
            status="deferred",
            summary=(
                "Offline smoke plan is deferred until required redacted evidence and "
                "manual operator controls are explicitly recorded."
            ),
            next_allowed_actions=_DEFAULT_ACTIONS,
        )
    return TelegramOfflineSmokePlan(
        gate_id=_GATE_ID,
        decision="needs_offline_smoke_evidence",
        status="needs_offline_smoke_evidence",
        summary=(
            "Offline smoke plan still needs redacted evidence for environment-only secret "
            "loading, dry-run payload, disabled network/send paths, rollback, and manual go."
        ),
        next_allowed_actions=_DEFAULT_ACTIONS,
    )

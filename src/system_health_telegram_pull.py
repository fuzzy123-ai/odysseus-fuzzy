"""Telegram pull command models for system health snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from src.system_health_agent_interface import AlertSummary, CollectorStatus, HealthAgentInterfaceError, HealthSnapshot


class TelegramHealthCommandType(StrEnum):
    STATUS = "status"
    ALERTS = "alerts"
    DISK = "disk"
    UPDATES = "updates"
    CONTAINERS = "containers"
    UNSUPPORTED_COMMAND = "unsupported_command"


class TelegramHealthResponseType(StrEnum):
    OK = "ok"
    BLOCKED_UNAUTHORIZED = "blocked_unauthorized"
    UNSUPPORTED_COMMAND = "unsupported_command"
    NO_DATA = "no_data"


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _normalize_user_id(value: Any) -> str:
    text = _normalize_text(value, field_name="user_id")
    if not text.lstrip("-").isdigit():
        raise HealthAgentInterfaceError("user_id must be numeric")
    return text


def _normalize_command_token(value: Any) -> str:
    token = _normalize_text(value, field_name="command").lower()
    if token.startswith("/"):
        token = token[1:]
    token = token.split("@", 1)[0]
    return token


def _normalize_command_type(value: Any) -> TelegramHealthCommandType:
    token = _normalize_command_token(value)
    try:
        return TelegramHealthCommandType(token)
    except ValueError:
        return TelegramHealthCommandType.UNSUPPORTED_COMMAND


def _collectors_by_id(snapshot: HealthSnapshot) -> dict[str, CollectorStatus]:
    return {collector.collector_id: collector for collector in snapshot.collectors}


def _alerts_by_severity(alerts: Iterable[AlertSummary]) -> tuple[AlertSummary, ...]:
    return tuple(sorted(alerts, key=lambda item: (item.severity.value, item.dedupe_key)))


@dataclass(frozen=True, slots=True)
class TelegramHealthCommand:
    user_id: str
    command_type: TelegramHealthCommandType
    raw_command: str

    @classmethod
    def create(cls, *, user_id: Any, command: Any) -> "TelegramHealthCommand":
        return cls(
            user_id=_normalize_user_id(user_id),
            command_type=_normalize_command_type(command),
            raw_command=_normalize_text(command, field_name="command"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "command_type": self.command_type.value,
            "raw_command": self.raw_command,
        }


@dataclass(frozen=True, slots=True)
class TelegramHealthResponse:
    response_type: TelegramHealthResponseType
    command_type: TelegramHealthCommandType
    text: str
    blocked_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_type": self.response_type.value,
            "command_type": self.command_type.value,
            "text": self.text,
            "blocked_reason": self.blocked_reason,
        }


def _render_status(snapshot: HealthSnapshot) -> str:
    critical_alerts = [alert for alert in snapshot.alerts if alert.severity.value == "critical"]
    if critical_alerts:
        first = critical_alerts[0]
        return (
            f"Status: {snapshot.overall_status.value.upper()} on {snapshot.host_label or 'host'} | "
            f"critical alerts: {len(critical_alerts)} | next: {first.next_action}"
        )
    return f"Status: {snapshot.overall_status.value.upper()} on {snapshot.host_label or 'host'} | no critical alerts"


def _render_alerts(snapshot: HealthSnapshot) -> tuple[TelegramHealthResponseType, str]:
    if not snapshot.alerts:
        return TelegramHealthResponseType.OK, "Alerts: quiet | no active alerts"
    lines = ["Alerts:"]
    for alert in _alerts_by_severity(snapshot.alerts)[:3]:
        lines.append(f"- {alert.severity.value.upper()}: {alert.title} -> {alert.next_action}")
    return TelegramHealthResponseType.OK, "\n".join(lines)


def _render_collector(
    snapshot: HealthSnapshot,
    *,
    collector_ids: tuple[str, ...],
    label: str,
) -> tuple[TelegramHealthResponseType, str]:
    collectors = _collectors_by_id(snapshot)
    for collector_id in collector_ids:
        collector = collectors.get(collector_id)
        if collector is not None:
            observed = f" | observed: {collector.observed_value}" if collector.observed_value else ""
            hint = f" | hint: {collector.setup_hint}" if collector.setup_hint else ""
            return (
                TelegramHealthResponseType.OK,
                f"{label}: {collector.state.value.upper()} | {collector.summary}{observed}{hint}",
            )
    return TelegramHealthResponseType.NO_DATA, f"{label}: no data"


def render_telegram_health_response(
    command: TelegramHealthCommand,
    *,
    snapshot: HealthSnapshot,
    allowed_user_ids: Iterable[Any],
) -> TelegramHealthResponse:
    if not isinstance(snapshot, HealthSnapshot):
        raise HealthAgentInterfaceError("snapshot must be a HealthSnapshot")
    if not isinstance(command, TelegramHealthCommand):
        raise HealthAgentInterfaceError("command must be a TelegramHealthCommand")

    normalized_allowed_ids = {_normalize_user_id(value) for value in allowed_user_ids}
    if command.user_id not in normalized_allowed_ids:
        return TelegramHealthResponse(
            response_type=TelegramHealthResponseType.BLOCKED_UNAUTHORIZED,
            command_type=command.command_type,
            text="Access denied: this user is not allowed to read health status.",
            blocked_reason="blocked_unauthorized",
        )

    if command.command_type == TelegramHealthCommandType.UNSUPPORTED_COMMAND:
        return TelegramHealthResponse(
            response_type=TelegramHealthResponseType.UNSUPPORTED_COMMAND,
            command_type=command.command_type,
            text="Unsupported command. Allowed: /status, /alerts, /disk, /updates, /containers",
            blocked_reason="unsupported_command",
        )

    if command.command_type == TelegramHealthCommandType.STATUS:
        return TelegramHealthResponse(
            response_type=TelegramHealthResponseType.OK,
            command_type=command.command_type,
            text=_render_status(snapshot),
            blocked_reason="",
        )
    if command.command_type == TelegramHealthCommandType.ALERTS:
        response_type, text = _render_alerts(snapshot)
        return TelegramHealthResponse(response_type=response_type, command_type=command.command_type, text=text, blocked_reason="")
    if command.command_type == TelegramHealthCommandType.DISK:
        response_type, text = _render_collector(snapshot, collector_ids=("disk",), label="Disk")
        return TelegramHealthResponse(response_type=response_type, command_type=command.command_type, text=text, blocked_reason="")
    if command.command_type == TelegramHealthCommandType.UPDATES:
        response_type, text = _render_collector(
            snapshot,
            collector_ids=("host-agent", "updates", "system-updates"),
            label="Updates",
        )
        return TelegramHealthResponse(response_type=response_type, command_type=command.command_type, text=text, blocked_reason="")

    response_type, text = _render_collector(
        snapshot,
        collector_ids=("containers", "container-runtime"),
        label="Containers",
    )
    return TelegramHealthResponse(response_type=response_type, command_type=command.command_type, text=text, blocked_reason="")

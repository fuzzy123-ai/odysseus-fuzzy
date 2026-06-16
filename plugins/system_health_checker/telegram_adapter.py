"""Telegram command adapter model for System Health Checker.

This module does not start a bot, store a token, or call Telegram. It only
parses allowlisted pull commands and renders sanitized health text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from .health_model import CollectorKind, HealthSnapshot, HealthState
from .rule_engine import AlertEvent, AlertState


class TelegramAdapterError(ValueError):
    """Raised when Telegram adapter inputs are invalid."""


class TelegramCommand(StrEnum):
    STATUS = "status"
    ALERTS = "alerts"
    DISK = "disk"
    UPDATES = "updates"
    CONTAINERS = "containers"


@dataclass(frozen=True, slots=True)
class TelegramRequest:
    user_id: str
    command: TelegramCommand
    raw_text: str


@dataclass(frozen=True, slots=True)
class TelegramCommandDecision:
    allowed: bool
    command: TelegramCommand | None
    block_reason: str
    response_text: str


def parse_telegram_command(*, user_id: Any, text: Any) -> TelegramRequest:
    normalized_user = _text(user_id, field_name="user_id")
    raw_text = _text(text, field_name="text")
    command_token = raw_text.strip().split()[0].lstrip("/").split("@", 1)[0].lower()
    try:
        command = TelegramCommand(command_token)
    except ValueError as exc:
        raise TelegramAdapterError("unsupported telegram command") from exc
    return TelegramRequest(user_id=normalized_user, command=command, raw_text=raw_text)


def decide_telegram_command(
    *,
    request: TelegramRequest,
    allowlisted_user_ids: Iterable[Any],
    snapshot: HealthSnapshot,
    alert_events: Iterable[AlertEvent] = (),
) -> TelegramCommandDecision:
    if not isinstance(request, TelegramRequest):
        raise TelegramAdapterError("request must be a TelegramRequest")
    if not isinstance(snapshot, HealthSnapshot):
        raise TelegramAdapterError("snapshot must be a HealthSnapshot")

    allowlist = {_text(user_id, field_name="allowlisted_user_id") for user_id in allowlisted_user_ids}
    if request.user_id not in allowlist:
        return TelegramCommandDecision(
            allowed=False,
            command=request.command,
            block_reason="telegram_user_not_allowlisted",
            response_text="This Telegram user is not authorized for Odysseus health status.",
        )

    return TelegramCommandDecision(
        allowed=True,
        command=request.command,
        block_reason="",
        response_text=render_telegram_response(command=request.command, snapshot=snapshot, alert_events=alert_events),
    )


def render_telegram_response(
    *,
    command: TelegramCommand,
    snapshot: HealthSnapshot,
    alert_events: Iterable[AlertEvent] = (),
) -> str:
    if command == TelegramCommand.STATUS:
        return _render_status(snapshot)
    if command == TelegramCommand.ALERTS:
        return _render_alerts(alert_events)
    if command == TelegramCommand.DISK:
        return _render_collector(snapshot, CollectorKind.DISK, missing="Disk collector is not available yet.")
    if command == TelegramCommand.CONTAINERS:
        return _render_collector(
            snapshot,
            CollectorKind.CONTAINERS,
            missing="Container runtime collector is not available yet.",
        )
    if command == TelegramCommand.UPDATES:
        return "Update collector is not available yet. This will require the Debian host-agent."
    raise TelegramAdapterError("unsupported telegram command")


def _render_status(snapshot: HealthSnapshot) -> str:
    return (
        f"Odysseus health: {snapshot.state.value}\n"
        f"Agent: {snapshot.agent_id}\n"
        f"Collectors: {len(snapshot.collectors)}\n"
        f"Alerts: {snapshot.alerts.active_count}"
    )


def _render_alerts(alert_events: Iterable[AlertEvent]) -> str:
    visible = [event for event in alert_events if event.state != AlertState.COOLDOWN]
    if not visible:
        return "No active health alerts."
    lines = ["Health alerts:"]
    for event in visible:
        lines.append(f"- {event.severity.value}: {event.message} ({event.collector_summary})")
    return "\n".join(lines)


def _render_collector(snapshot: HealthSnapshot, kind: CollectorKind, *, missing: str) -> str:
    for collector in snapshot.collectors:
        if collector.kind == kind:
            return f"{kind.value}: {collector.state.value} - {collector.summary}"
    return missing


def _text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise TelegramAdapterError(f"{field_name} must not be empty")
    if len(text) > 240:
        raise TelegramAdapterError(f"{field_name} exceeds max length 240")
    return text

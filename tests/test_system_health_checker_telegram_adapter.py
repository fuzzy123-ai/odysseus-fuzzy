import pytest

from plugins.system_health_checker.basic_collectors import build_disk_status
from plugins.system_health_checker.health_model import AlertSummary, HealthSnapshot
from plugins.system_health_checker.rule_engine import AlertRule, AlertState, evaluate_alerts
from plugins.system_health_checker.telegram_adapter import (
    TelegramAdapterError,
    TelegramCommand,
    decide_telegram_command,
    parse_telegram_command,
    render_telegram_response,
)


OBSERVED_AT = "2026-06-16T12:00:00Z"


def _snapshot() -> HealthSnapshot:
    collector = build_disk_status(mount="/srv", used_percent=91, observed_at=OBSERVED_AT)
    alerts = AlertSummary.create(
        state="warn",
        active_count=1,
        highest_severity="warn",
        messages=["Disk usage is high"],
    )
    return HealthSnapshot.create(
        agent_id="agent-1",
        observed_at=OBSERVED_AT,
        collectors=[collector],
        alerts=alerts,
    )


def _alert_events():
    return evaluate_alerts(
        collectors=_snapshot().collectors,
        rules=[
            AlertRule.create(
                rule_id="disk-high",
                collector_kind="disk",
                trigger_state="warn",
                severity="warn",
                message="Disk usage is high",
            )
        ],
        now=100,
    )


def test_parse_telegram_command_normalizes_bot_suffix():
    request = parse_telegram_command(user_id=123, text="/status@OdysseusBot please")

    assert request.user_id == "123"
    assert request.command == TelegramCommand.STATUS


def test_unsupported_telegram_command_raises():
    with pytest.raises(TelegramAdapterError, match="unsupported telegram command"):
        parse_telegram_command(user_id=123, text="/reboot")


def test_not_allowlisted_user_is_blocked_without_health_details():
    request = parse_telegram_command(user_id=999, text="/status")
    decision = decide_telegram_command(
        request=request,
        allowlisted_user_ids=[123],
        snapshot=_snapshot(),
    )

    assert decision.allowed is False
    assert decision.block_reason == "telegram_user_not_allowlisted"
    assert "not authorized" in decision.response_text
    assert "Disk usage" not in decision.response_text


def test_allowlisted_status_command_returns_compact_snapshot():
    request = parse_telegram_command(user_id=123, text="/status")
    decision = decide_telegram_command(
        request=request,
        allowlisted_user_ids=["123"],
        snapshot=_snapshot(),
    )

    assert decision.allowed is True
    assert decision.block_reason == ""
    assert "Odysseus health: warn" in decision.response_text
    assert "Collectors: 1" in decision.response_text


def test_alerts_command_hides_cooldown_events():
    cooldown_event = _alert_events()[0]
    cooldown_event = type(cooldown_event)(
        rule_id=cooldown_event.rule_id,
        state=AlertState.COOLDOWN,
        severity=cooldown_event.severity,
        message=cooldown_event.message,
        collector_summary=cooldown_event.collector_summary,
        next_allowed_at=999,
    )

    text = render_telegram_response(
        command=TelegramCommand.ALERTS,
        snapshot=_snapshot(),
        alert_events=[cooldown_event],
    )

    assert text == "No active health alerts."


def test_alerts_command_renders_active_events():
    text = render_telegram_response(
        command=TelegramCommand.ALERTS,
        snapshot=_snapshot(),
        alert_events=_alert_events(),
    )

    assert "Health alerts:" in text
    assert "warn: Disk usage is high" in text


def test_disk_command_renders_disk_collector():
    text = render_telegram_response(
        command=TelegramCommand.DISK,
        snapshot=_snapshot(),
    )

    assert text == "disk: warn - Disk /srv used 91.0%"


def test_containers_command_explains_missing_collector():
    text = render_telegram_response(
        command=TelegramCommand.CONTAINERS,
        snapshot=_snapshot(),
    )

    assert text == "Container runtime collector is not available yet."


def test_updates_command_explains_host_agent_requirement():
    text = render_telegram_response(
        command=TelegramCommand.UPDATES,
        snapshot=_snapshot(),
    )

    assert "Debian host-agent" in text

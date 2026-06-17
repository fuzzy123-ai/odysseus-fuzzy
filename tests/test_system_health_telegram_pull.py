from src.system_health_agent_interface import AlertSummary, CollectorStatus, HealthSnapshot
from src.system_health_telegram_pull import (
    TelegramHealthCommand,
    TelegramHealthCommandType,
    TelegramHealthResponseType,
    render_telegram_health_response,
)


def _snapshot(*, alerts=(), collectors=(), overall_status=None):
    return HealthSnapshot.create(
        schema_version="1.0",
        generated_at="2026-06-17T20:00:00Z",
        collectors=collectors
        or (
            CollectorStatus.create(
                collector_id="disk",
                state="ok",
                summary="disk usage is healthy",
                observed_value="42 percent",
            ),
        ),
        alerts=alerts,
        host_label="ops-node-1",
        overall_status=overall_status,
    )


def test_command_normalization_and_unsupported_mapping():
    status_command = TelegramHealthCommand.create(user_id="1234", command="/status@odysseus_bot")
    unknown_command = TelegramHealthCommand.create(user_id="1234", command="/reboot")

    assert status_command.command_type == TelegramHealthCommandType.STATUS
    assert unknown_command.command_type == TelegramHealthCommandType.UNSUPPORTED_COMMAND


def test_unauthorized_user_is_blocked():
    command = TelegramHealthCommand.create(user_id="999", command="/status")
    response = render_telegram_health_response(
        command,
        snapshot=_snapshot(),
        allowed_user_ids=("123", "456"),
    )

    assert response.response_type == TelegramHealthResponseType.BLOCKED_UNAUTHORIZED
    assert response.blocked_reason == "blocked_unauthorized"


def test_status_command_summarizes_overall_status_and_critical_alerts():
    snapshot = _snapshot(
        overall_status="critical",
        alerts=(
            AlertSummary.create(
                severity="critical",
                title="Disk pressure",
                cause="root volume exceeded threshold",
                next_action="free disk space",
                dedupe_key="disk-pressure",
            ),
        ),
    )
    response = render_telegram_health_response(
        TelegramHealthCommand.create(user_id="123", command="/status"),
        snapshot=snapshot,
        allowed_user_ids=("123",),
    )

    assert response.response_type == TelegramHealthResponseType.OK
    assert "CRITICAL" in response.text
    assert "free disk space" in response.text


def test_alerts_command_lists_alerts_or_quiet_message():
    quiet_response = render_telegram_health_response(
        TelegramHealthCommand.create(user_id="123", command="/alerts"),
        snapshot=_snapshot(),
        allowed_user_ids=("123",),
    )
    active_response = render_telegram_health_response(
        TelegramHealthCommand.create(user_id="123", command="/alerts"),
        snapshot=_snapshot(
            alerts=(
                AlertSummary.create(
                    severity="warn",
                    title="Agent version lagging",
                    cause="build is stale",
                    next_action="schedule upgrade",
                    dedupe_key="agent-version",
                ),
            )
        ),
        allowed_user_ids=("123",),
    )

    assert "quiet" in quiet_response.text.lower()
    assert "schedule upgrade" in active_response.text


def test_containers_command_uses_container_collector_or_returns_no_data():
    with_containers = render_telegram_health_response(
        TelegramHealthCommand.create(user_id="123", command="/containers"),
        snapshot=_snapshot(
            collectors=(
                CollectorStatus.create(
                    collector_id="container-runtime",
                    state="warn",
                    summary="podman exists but access is degraded",
                    observed_value="podman",
                    setup_hint="grant runtime permissions",
                ),
            )
        ),
        allowed_user_ids=("123",),
    )
    without_containers = render_telegram_health_response(
        TelegramHealthCommand.create(user_id="123", command="/containers"),
        snapshot=_snapshot(
            collectors=(
                CollectorStatus.create(
                    collector_id="disk",
                    state="ok",
                    summary="disk usage is healthy",
                    observed_value="42 percent",
                ),
            )
        ),
        allowed_user_ids=("123",),
    )

    assert with_containers.response_type == TelegramHealthResponseType.OK
    assert "podman" in with_containers.text
    assert without_containers.response_type == TelegramHealthResponseType.NO_DATA
    assert "no data" in without_containers.text.lower()


def test_to_dict_is_stable_and_no_secrets_are_rendered():
    command = TelegramHealthCommand.create(user_id="123", command="/disk")
    response = render_telegram_health_response(
        command,
        snapshot=_snapshot(),
        allowed_user_ids=("123",),
    )

    assert command.to_dict() == {
        "user_id": "123",
        "command_type": "disk",
        "raw_command": "/disk",
    }
    assert response.to_dict() == {
        "response_type": "ok",
        "command_type": "disk",
        "text": "Disk: OK | disk usage is healthy | observed: 42 percent",
        "blocked_reason": "",
    }
    assert "token" not in response.text.lower()

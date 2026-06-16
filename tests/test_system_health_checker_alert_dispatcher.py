import pytest

from plugins.system_health_checker.alert_dispatcher import (
    DispatchAction,
    build_dispatch_plan,
    render_dispatch_message,
)
from plugins.system_health_checker.basic_collectors import build_disk_status
from plugins.system_health_checker.health_model import HealthModelError, HealthState
from plugins.system_health_checker.rule_engine import AlertRule, AlertState, evaluate_alerts


OBSERVED_AT = "2026-06-16T12:00:00Z"


def _events_for_disk(*, used_percent: float, previous_until: int = 0, now: int = 100):
    return evaluate_alerts(
        collectors=[build_disk_status(mount="/srv", used_percent=used_percent, observed_at=OBSERVED_AT)],
        rules=[
            AlertRule.create(
                rule_id="disk-high",
                collector_kind="disk",
                trigger_state="warn",
                severity="warn",
                message="Disk usage is high",
                cooldown_seconds=300,
            )
        ],
        now=now,
        previous_next_allowed_at={"disk-high": previous_until} if previous_until else None,
    )


def test_active_event_creates_send_plan():
    plans = build_dispatch_plan(events=_events_for_disk(used_percent=91))

    assert len(plans) == 1
    assert plans[0].action == DispatchAction.SEND
    assert plans[0].severity == HealthState.WARN
    assert plans[0].channel == "telegram"


def test_cooldown_event_is_skipped_by_default():
    plans = build_dispatch_plan(events=_events_for_disk(used_percent=91, previous_until=300, now=150))

    assert plans == ()


def test_cooldown_event_can_be_included_for_diagnostics():
    plans = build_dispatch_plan(
        events=_events_for_disk(used_percent=91, previous_until=300, now=150),
        include_cooldown_skips=True,
    )

    assert len(plans) == 1
    assert plans[0].action == DispatchAction.SKIP_COOLDOWN


def test_recovery_event_creates_recovery_plan():
    plans = build_dispatch_plan(events=_events_for_disk(used_percent=20, previous_until=300, now=350))

    assert len(plans) == 1
    assert plans[0].action == DispatchAction.SEND_RECOVERY
    assert plans[0].severity == HealthState.OK


def test_dispatch_message_rendering_is_stable():
    active, cooldown, recovery = (
        build_dispatch_plan(events=_events_for_disk(used_percent=91))[0],
        build_dispatch_plan(
            events=_events_for_disk(used_percent=91, previous_until=300, now=150),
            include_cooldown_skips=True,
        )[0],
        build_dispatch_plan(events=_events_for_disk(used_percent=20, previous_until=300, now=350))[0],
    )

    assert render_dispatch_message(active) == "WARN: Disk usage is high"
    assert render_dispatch_message(cooldown) == "Cooldown: Disk usage is high"
    assert render_dispatch_message(recovery) == "Recovery: Recovered: Disk usage is high"


def test_channel_must_not_be_empty():
    with pytest.raises(HealthModelError, match="channel must not be empty"):
        build_dispatch_plan(events=_events_for_disk(used_percent=91), channel="")


def test_events_must_be_alert_events():
    with pytest.raises(HealthModelError, match="events must contain AlertEvent instances"):
        build_dispatch_plan(events=["not-an-alert"])

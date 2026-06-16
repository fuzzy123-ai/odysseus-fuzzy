import pytest

from plugins.system_health_checker.basic_collectors import build_disk_status, build_memory_status
from plugins.system_health_checker.health_model import HealthModelError, HealthState
from plugins.system_health_checker.rule_engine import AlertRule, AlertState, evaluate_alerts


OBSERVED_AT = "2026-06-16T12:00:00Z"


def _disk_rule() -> AlertRule:
    return AlertRule.create(
        rule_id="disk-high",
        collector_kind="disk",
        trigger_state="warn",
        severity="warn",
        message="Disk usage is high",
        cooldown_seconds=300,
    )


def test_alert_rule_fires_when_threshold_is_met():
    events = evaluate_alerts(
        collectors=[build_disk_status(mount="/srv", used_percent=91, observed_at=OBSERVED_AT)],
        rules=[_disk_rule()],
        now=1000,
    )

    assert len(events) == 1
    assert events[0].state == AlertState.ACTIVE
    assert events[0].severity == HealthState.WARN
    assert events[0].next_allowed_at == 1300


def test_alert_rule_enters_cooldown_before_next_allowed_time():
    events = evaluate_alerts(
        collectors=[build_disk_status(mount="/srv", used_percent=91, observed_at=OBSERVED_AT)],
        rules=[_disk_rule()],
        now=1100,
        previous_next_allowed_at={"disk-high": 1300},
    )

    assert len(events) == 1
    assert events[0].state == AlertState.COOLDOWN
    assert events[0].next_allowed_at == 1300


def test_alert_rule_recovers_after_previous_alert_when_collector_is_ok():
    events = evaluate_alerts(
        collectors=[build_disk_status(mount="/srv", used_percent=40, observed_at=OBSERVED_AT)],
        rules=[_disk_rule()],
        now=1400,
        previous_next_allowed_at={"disk-high": 1300},
    )

    assert len(events) == 1
    assert events[0].state == AlertState.RECOVERED
    assert events[0].severity == HealthState.OK
    assert events[0].next_allowed_at == 0


def test_no_event_when_rule_not_hit_and_no_previous_alert():
    events = evaluate_alerts(
        collectors=[build_disk_status(mount="/srv", used_percent=40, observed_at=OBSERVED_AT)],
        rules=[_disk_rule()],
        now=1000,
    )

    assert events == ()


def test_critical_collector_satisfies_warn_threshold():
    events = evaluate_alerts(
        collectors=[build_memory_status(available_percent=4, observed_at=OBSERVED_AT)],
        rules=[
            AlertRule.create(
                rule_id="memory-low",
                collector_kind="memory",
                trigger_state="warn",
                severity="critical",
                message="Memory is low",
            )
        ],
        now=1000,
    )

    assert events[0].state == AlertState.ACTIVE
    assert events[0].severity == HealthState.CRITICAL


def test_missing_collector_does_not_create_event():
    events = evaluate_alerts(
        collectors=[build_disk_status(mount="/srv", used_percent=91, observed_at=OBSERVED_AT)],
        rules=[
            AlertRule.create(
                rule_id="memory-low",
                collector_kind="memory",
                trigger_state="warn",
                severity="critical",
                message="Memory is low",
            )
        ],
        now=1000,
    )

    assert events == ()


def test_rule_cooldown_must_not_be_negative():
    with pytest.raises(HealthModelError, match="cooldown_seconds must not be negative"):
        AlertRule.create(
            rule_id="bad",
            collector_kind="disk",
            trigger_state="warn",
            severity="warn",
            message="Bad rule",
            cooldown_seconds=-1,
        )

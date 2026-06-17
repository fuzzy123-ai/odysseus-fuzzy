from src.system_health_agent_interface import AlertSummary
from src.system_health_auto_alerting import AutoAlertAction, AutoAlertDecision, build_auto_alert_batch
from src.system_health_rule_engine import AlertDecision, RuleEvaluation


def _alert_decision(*, alerts=(), cleared_keys=(), evaluations=()):
    return AlertDecision(
        alerts=alerts,
        cleared_keys=cleared_keys,
        evaluations=evaluations
        or (
            RuleEvaluation(
                rule_id="disk-pressure",
                collector_id="disk",
                dedupe_key="disk-pressure-disk",
                collector_state=None,
                triggered=bool(alerts),
                suppressed=False,
                repeated=False,
                recovered=False,
                severity=None,
                reason="test",
                setup_hint="",
            ),
        ),
    )


def test_new_alert_is_sent_with_short_payload():
    decision = _alert_decision(
        alerts=(
            AlertSummary.create(
                severity="critical",
                title="Disk pressure",
                cause="root volume exceeded threshold",
                next_action="free disk space",
                dedupe_key="disk-pressure-disk",
            ),
        )
    )

    batch = build_auto_alert_batch(decision)

    assert batch.send_count == 1
    assert batch.decisions[0].action == AutoAlertAction.SEND
    assert "free disk space" in batch.decisions[0].payload


def test_duplicate_active_key_is_not_resent():
    decision = _alert_decision(
        alerts=(
            AlertSummary.create(
                severity="warn",
                title="Memory pressure",
                cause="memory usage is elevated",
                next_action="review memory-heavy services",
                dedupe_key="memory-pressure-memory",
            ),
        )
    )

    batch = build_auto_alert_batch(decision, previous_active_keys=("memory-pressure-memory",))

    assert batch.decisions[0].action == AutoAlertAction.SUPPRESS_DUPLICATE


def test_recently_sent_key_is_suppressed_by_cooldown():
    decision = _alert_decision(
        alerts=(
            AlertSummary.create(
                severity="warn",
                title="Agent version lagging",
                cause="build is stale",
                next_action="schedule upgrade",
                dedupe_key="agent-version",
            ),
        )
    )

    batch = build_auto_alert_batch(decision, recently_sent_keys=("agent-version",))

    assert batch.decisions[0].action == AutoAlertAction.SUPPRESS_COOLDOWN


def test_recovery_is_emitted_for_cleared_key():
    decision = _alert_decision(alerts=(), cleared_keys=("disk-pressure-disk",), evaluations=())

    batch = build_auto_alert_batch(decision, previous_active_keys=("disk-pressure-disk",))

    assert batch.recovery_count == 1
    assert any(item.action == AutoAlertAction.SEND_RECOVERY for item in batch.decisions)


def test_previous_key_without_current_or_cleared_marker_becomes_no_action():
    decision = _alert_decision(alerts=(), cleared_keys=(), evaluations=())

    batch = build_auto_alert_batch(decision, previous_active_keys=("orphaned-key",))

    assert any(item.action == AutoAlertAction.NO_ACTION for item in batch.decisions)


def test_to_dict_is_stable():
    batch = build_auto_alert_batch(
        _alert_decision(
            alerts=(
                AlertSummary.create(
                    severity="critical",
                    title="Disk pressure",
                    cause="root volume exceeded threshold",
                    next_action="free disk space",
                    dedupe_key="disk-pressure-disk",
                ),
            ),
            cleared_keys=("memory-pressure-memory",),
            evaluations=(),
        )
    )

    assert batch.to_dict() == {
        "decisions": (
            {
                "dedupe_key": "disk-pressure-disk",
                "action": "send",
                "severity": "critical",
                "payload": "CRITICAL: Disk pressure -> free disk space",
                "reason": "new alert should be sent",
            },
            {
                "dedupe_key": "memory-pressure-memory",
                "action": "send_recovery",
                "severity": "ok",
                "payload": "RECOVERY: memory-pressure-memory cleared",
                "reason": "previous alert is no longer active",
            },
        ),
        "send_count": 1,
        "recovery_count": 1,
    }

    decision = AutoAlertDecision.create(
        dedupe_key="disk-pressure-disk",
        action="send",
        severity="critical",
        payload="CRITICAL: Disk pressure -> free disk space",
        reason="new alert should be sent",
    )
    assert decision.to_dict() == {
        "dedupe_key": "disk-pressure-disk",
        "action": "send",
        "severity": "critical",
        "payload": "CRITICAL: Disk pressure -> free disk space",
        "reason": "new alert should be sent",
    }

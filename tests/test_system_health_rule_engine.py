from src.system_health_agent_interface import CollectorState, HealthSnapshot
from src.system_health_basic_collectors import BasicCollectorReading, build_basic_health_snapshot
from src.system_health_rule_engine import AlertDecision, RuleDefinition, evaluate_health_rules


def _build_snapshot(*readings: BasicCollectorReading) -> HealthSnapshot:
    return build_basic_health_snapshot(readings, generated_at="2026-06-17T19:00:00Z", host_label="debian-host")


def test_rule_engine_emits_conservative_alerts():
    snapshot = _build_snapshot(
        BasicCollectorReading.create(
            collector_id="memory",
            value="88",
            unit="percent",
            state="warn",
            summary="memory usage is elevated",
        ),
        BasicCollectorReading.create(
            collector_id="disk",
            value="97",
            unit="percent",
            state="critical",
            summary="disk usage crossed the critical threshold",
        ),
    )
    rules = (
        RuleDefinition.create(
            rule_id="memory-pressure",
            collector_id="memory",
            warn_state="warn",
            title="Memory pressure",
            next_action="review memory-heavy services",
        ),
        RuleDefinition.create(
            rule_id="disk-pressure",
            collector_id="disk",
            critical_state="critical",
            title="Disk pressure",
            next_action="free disk space immediately",
        ),
    )

    decision = evaluate_health_rules(snapshot, rules)

    assert isinstance(decision, AlertDecision)
    assert tuple(alert.dedupe_key for alert in decision.alerts) == (
        "disk-pressure-disk",
        "memory-pressure-memory",
    )
    assert decision.evaluations[0].severity.value in {"critical", "warn"}


def test_previous_alert_key_suppresses_new_alert_and_marks_repeated():
    snapshot = _build_snapshot(
        BasicCollectorReading.create(
            collector_id="memory",
            value="88",
            unit="percent",
            state="warn",
            summary="memory usage is elevated",
        ),
    )
    rules = (
        RuleDefinition.create(
            rule_id="memory-pressure",
            collector_id="memory",
            warn_state="warn",
            title="Memory pressure",
            next_action="review memory-heavy services",
        ),
    )

    decision = evaluate_health_rules(snapshot, rules, previous_alert_keys=("memory-pressure-memory",))

    assert decision.alerts == ()
    assert decision.evaluations[0].suppressed is True
    assert decision.evaluations[0].repeated is True


def test_recovery_is_detected_when_previous_alert_key_no_longer_triggers():
    snapshot = _build_snapshot(
        BasicCollectorReading.create(
            collector_id="disk",
            value="42",
            unit="percent",
            state="ok",
            summary="disk is healthy",
        ),
    )
    rules = (
        RuleDefinition.create(
            rule_id="disk-pressure",
            collector_id="disk",
            critical_state="critical",
            title="Disk pressure",
            next_action="free disk space immediately",
        ),
    )

    decision = evaluate_health_rules(snapshot, rules, previous_alert_keys=("disk-pressure-disk",))

    assert decision.alerts == ()
    assert decision.cleared_keys == ("disk-pressure-disk",)
    assert decision.evaluations[0].recovered is True


def test_unknown_or_unsupported_state_remains_conservative():
    snapshot = _build_snapshot(
        BasicCollectorReading.create(
            collector_id="uptime",
            value="unknown",
            unit="",
            state="unknown",
            summary="uptime collector is unavailable",
        ),
    )
    rules = (
        RuleDefinition.create(
            rule_id="uptime-collector",
            collector_id="uptime",
            warn_state="unknown",
            title="Uptime collector needs review",
            next_action="check host-side uptime source",
            cooldown_hint="review host agent setup",
        ),
    )

    decision = evaluate_health_rules(snapshot, rules)

    assert decision.alerts[0].severity.value == "unknown"
    assert "review" in decision.evaluations[0].reason
    assert decision.evaluations[0].setup_hint


def test_to_dict_is_stable():
    snapshot = _build_snapshot(
        BasicCollectorReading.create(
            collector_id="cpu",
            value="93",
            unit="percent",
            state="warn",
            summary="cpu utilization is high",
        ),
    )
    rules = (
        RuleDefinition.create(
            rule_id="cpu-pressure",
            collector_id="cpu",
            warn_state="warn",
            title="CPU pressure",
            next_action="inspect hot processes",
            cooldown_hint="15m",
        ),
    )

    decision = evaluate_health_rules(snapshot, rules)

    assert decision.to_dict() == {
        "evaluations": (
            {
                "rule_id": "cpu-pressure",
                "collector_id": "cpu",
                "dedupe_key": "cpu-pressure-cpu",
                "collector_state": "warn",
                "triggered": True,
                "suppressed": False,
                "repeated": False,
                "recovered": False,
                "severity": "warn",
                "reason": "collector triggered rule threshold",
                "setup_hint": "",
            },
        ),
        "alerts": (
            {
                "severity": "warn",
                "title": "CPU pressure",
                "cause": "cpu utilization is high",
                "next_action": "inspect hot processes",
                "dedupe_key": "cpu-pressure-cpu",
                "cooldown_hint": "15m",
            },
        ),
        "cleared_keys": (),
    }

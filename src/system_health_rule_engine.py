"""Rule evaluation helpers for system health snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from src.system_health_agent_interface import (
    AlertSeverity,
    AlertSummary,
    CollectorState,
    CollectorStatus,
    HealthAgentInterfaceError,
    HealthSnapshot,
)


_STATE_ORDER = {
    CollectorState.OK: 0,
    CollectorState.WARN: 1,
    CollectorState.UNKNOWN: 2,
    CollectorState.UNSUPPORTED: 2,
    CollectorState.CRITICAL: 3,
}


def _normalize_rule_id(value: Any) -> str:
    return CollectorStatus.create(
        collector_id=value,
        state=CollectorState.OK,
        summary="rule id normalization",
    ).collector_id


def _normalize_trigger_state(field_name: str, value: CollectorState | str) -> CollectorState:
    status = CollectorStatus.create(
        collector_id="rule-trigger",
        state=value,
        summary=f"{field_name} normalization",
    ).state
    if status == CollectorState.OK:
        raise HealthAgentInterfaceError(f"{field_name} must be warn, unknown, unsupported, or critical")
    return status


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _severity_for_state(state: CollectorState) -> AlertSeverity:
    return AlertSeverity(state.value)


def _state_meets_threshold(actual: CollectorState, threshold: CollectorState | None) -> bool:
    if threshold is None:
        return actual != CollectorState.OK
    return _STATE_ORDER[actual] >= _STATE_ORDER[threshold]


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    rule_id: str
    collector_id: str
    warn_state: CollectorState | None
    critical_state: CollectorState | None
    title: str
    next_action: str
    cooldown_hint: str

    @classmethod
    def create(
        cls,
        *,
        rule_id: Any,
        collector_id: Any,
        warn_state: CollectorState | str | None = CollectorState.WARN,
        critical_state: CollectorState | str | None = CollectorState.CRITICAL,
        title: Any,
        next_action: Any,
        cooldown_hint: Any = "",
    ) -> "RuleDefinition":
        normalized_warn = None if warn_state in (None, "") else _normalize_trigger_state("warn_state", warn_state)
        normalized_critical = (
            None if critical_state in (None, "") else _normalize_trigger_state("critical_state", critical_state)
        )
        if normalized_warn is None and normalized_critical is None:
            raise HealthAgentInterfaceError("rule must define warn_state or critical_state")
        if (
            normalized_warn is not None
            and normalized_critical is not None
            and _STATE_ORDER[normalized_critical] < _STATE_ORDER[normalized_warn]
        ):
            raise HealthAgentInterfaceError("critical_state must not be less severe than warn_state")
        return cls(
            rule_id=_normalize_rule_id(rule_id),
            collector_id=CollectorStatus.create(
                collector_id=collector_id,
                state=CollectorState.OK,
                summary="collector id normalization",
            ).collector_id,
            warn_state=normalized_warn,
            critical_state=normalized_critical,
            title=_normalize_text(title, field_name="title"),
            next_action=_normalize_text(next_action, field_name="next_action"),
            cooldown_hint=_normalize_text(cooldown_hint, field_name="cooldown_hint", allow_empty=True),
        )

    def dedupe_key(self) -> str:
        return f"{self.rule_id}-{self.collector_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "collector_id": self.collector_id,
            "warn_state": None if self.warn_state is None else self.warn_state.value,
            "critical_state": None if self.critical_state is None else self.critical_state.value,
            "title": self.title,
            "next_action": self.next_action,
            "cooldown_hint": self.cooldown_hint,
        }


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    rule_id: str
    collector_id: str
    dedupe_key: str
    collector_state: CollectorState | None
    triggered: bool
    suppressed: bool
    repeated: bool
    recovered: bool
    severity: AlertSeverity | None
    reason: str
    setup_hint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "collector_id": self.collector_id,
            "dedupe_key": self.dedupe_key,
            "collector_state": None if self.collector_state is None else self.collector_state.value,
            "triggered": self.triggered,
            "suppressed": self.suppressed,
            "repeated": self.repeated,
            "recovered": self.recovered,
            "severity": None if self.severity is None else self.severity.value,
            "reason": self.reason,
            "setup_hint": self.setup_hint,
        }


@dataclass(frozen=True, slots=True)
class AlertDecision:
    evaluations: tuple[RuleEvaluation, ...]
    alerts: tuple[AlertSummary, ...]
    cleared_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluations": tuple(item.to_dict() for item in self.evaluations),
            "alerts": tuple(item.to_dict() for item in self.alerts),
            "cleared_keys": self.cleared_keys,
        }


def _collectors_by_id(snapshot: HealthSnapshot) -> dict[str, CollectorStatus]:
    return {collector.collector_id: collector for collector in snapshot.collectors}


def _evaluate_rule(
    rule: RuleDefinition,
    collector: CollectorStatus | None,
    previous_alert_keys: set[str],
) -> tuple[RuleEvaluation, AlertSummary | None]:
    dedupe_key = rule.dedupe_key()
    if collector is None:
        recovered = dedupe_key in previous_alert_keys
        return (
            RuleEvaluation(
                rule_id=rule.rule_id,
                collector_id=rule.collector_id,
                dedupe_key=dedupe_key,
                collector_state=None,
                triggered=False,
                suppressed=False,
                repeated=False,
                recovered=recovered,
                severity=None,
                reason="collector not present in snapshot",
                setup_hint="",
            ),
            None,
        )

    severity: AlertSeverity | None = None
    if rule.critical_state and _state_meets_threshold(collector.state, rule.critical_state):
        severity = _severity_for_state(CollectorState.CRITICAL)
    elif rule.warn_state and _state_meets_threshold(collector.state, rule.warn_state):
        severity = _severity_for_state(collector.state)

    if severity is None:
        recovered = dedupe_key in previous_alert_keys
        return (
            RuleEvaluation(
                rule_id=rule.rule_id,
                collector_id=rule.collector_id,
                dedupe_key=dedupe_key,
                collector_state=collector.state,
                triggered=False,
                suppressed=False,
                repeated=False,
                recovered=recovered,
                severity=None,
                reason="collector state is below rule threshold",
                setup_hint=collector.setup_hint,
            ),
            None,
        )

    repeated = dedupe_key in previous_alert_keys
    reason = "collector triggered rule threshold"
    if collector.state in {CollectorState.UNKNOWN, CollectorState.UNSUPPORTED}:
        reason = "collector requires setup clarification or operator review"
    evaluation = RuleEvaluation(
        rule_id=rule.rule_id,
        collector_id=rule.collector_id,
        dedupe_key=dedupe_key,
        collector_state=collector.state,
        triggered=True,
        suppressed=repeated,
        repeated=repeated,
        recovered=False,
        severity=severity,
        reason=reason,
        setup_hint=collector.setup_hint,
    )
    if repeated:
        return evaluation, None
    alert = AlertSummary.create(
        severity=severity,
        title=rule.title,
        cause=collector.summary if collector.summary else reason,
        next_action=rule.next_action,
        dedupe_key=dedupe_key,
        cooldown_hint=rule.cooldown_hint or collector.setup_hint,
    )
    return evaluation, alert


def evaluate_health_rules(
    snapshot: HealthSnapshot,
    rules: Iterable[RuleDefinition],
    *,
    previous_alert_keys: Iterable[str] = (),
) -> AlertDecision:
    normalized_rules = tuple(rules)
    if not isinstance(snapshot, HealthSnapshot):
        raise HealthAgentInterfaceError("snapshot must be a HealthSnapshot")
    if any(not isinstance(rule, RuleDefinition) for rule in normalized_rules):
        raise HealthAgentInterfaceError("rules must contain RuleDefinition items")

    previous_keys = {
        CollectorStatus.create(
            collector_id=str(value or ""),
            state=CollectorState.OK,
            summary="previous alert key normalization",
        ).collector_id
        for value in previous_alert_keys
        if str(value or "").strip()
    }
    collectors = _collectors_by_id(snapshot)

    evaluations: list[RuleEvaluation] = []
    alerts: list[AlertSummary] = []
    seen_keys: set[str] = set()

    for rule in normalized_rules:
        seen_keys.add(rule.dedupe_key())
        evaluation, alert = _evaluate_rule(rule, collectors.get(rule.collector_id), previous_keys)
        evaluations.append(evaluation)
        if alert is not None:
            alerts.append(alert)

    cleared_keys = tuple(sorted(key for key in previous_keys if key in seen_keys and key not in {a.dedupe_key for a in alerts} and any(e.dedupe_key == key and e.recovered for e in evaluations)))
    return AlertDecision(
        evaluations=tuple(sorted(evaluations, key=lambda item: item.dedupe_key)),
        alerts=tuple(sorted(alerts, key=lambda item: (item.severity.value, item.dedupe_key))),
        cleared_keys=cleared_keys,
    )

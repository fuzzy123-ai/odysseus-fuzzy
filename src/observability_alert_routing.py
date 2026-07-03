"""Content-free alert routing policy for Odysseus observability.

This module prepares operator alert decisions only. It does not query
Prometheus, send Telegram messages, mutate Grafana, or perform remediation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from src.observability_metrics import RuntimeMetricSample, build_runtime_metric_sample
from src.user_notification_contract import build_user_notification_decision


OBSERVABILITY_ALERT_ROUTING_SCHEMA = "odysseus.observability_alert_routing.v1"

ALLOWED_ALERT_SEVERITIES = {"info", "warning", "error"}
ALLOWED_ALERT_CHANNELS = {"auto", "telegram"}
ALLOWED_OPERATORS = {"gt", "gte"}


class ObservabilityAlertRoutingError(ValueError):
    """Raised when an alert routing rule or sample is unsupported."""


@dataclass(frozen=True)
class ObservabilityAlertRule:
    rule_id: str
    metric_name: str
    threshold: float
    operator: str
    severity: str
    title: str
    next_action: str
    channel: str = "auto"
    suppress_during_maintenance: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObservabilityAlertRoute:
    rule_id: str
    metric_name: str
    status: str
    severity: str
    reason: str
    dedupe_key: str
    notification: dict[str, Any] | None
    raw_content_visible: bool = False
    delivery_performed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_ALERT_RULES: tuple[ObservabilityAlertRule, ...] = (
    ObservabilityAlertRule(
        rule_id="telegram-poll-failing",
        metric_name="telegram_poll_failure_total",
        threshold=0,
        operator="gt",
        severity="error",
        title="Telegram polling is failing",
        next_action="inspect Telegram flow diagnostics and recent app logs",
    ),
    ObservabilityAlertRule(
        rule_id="telegram-poll-stale",
        metric_name="telegram_last_update_age_seconds",
        threshold=900,
        operator="gt",
        severity="warning",
        title="Telegram polling is stale",
        next_action="check whether the bot received updates and whether polling is active",
    ),
    ObservabilityAlertRule(
        rule_id="scheduler-delivery-failing",
        metric_name="scheduler_delivery_failures_total",
        threshold=0,
        operator="gt",
        severity="error",
        title="Scheduler delivery failures detected",
        next_action="inspect due task delivery diagnostics before creating new reminders",
    ),
    ObservabilityAlertRule(
        rule_id="scheduler-backlog",
        metric_name="scheduler_due_tasks",
        threshold=0,
        operator="gt",
        severity="warning",
        title="Scheduler has due tasks",
        next_action="inspect scheduler queue and delivery status",
    ),
    ObservabilityAlertRule(
        rule_id="universal-inbox-blocked",
        metric_name="universal_inbox_blocked_total",
        threshold=0,
        operator="gt",
        severity="warning",
        title="Universal Inbox has blocked items",
        next_action="inspect redacted inbox blockers and policy decisions",
    ),
    ObservabilityAlertRule(
        rule_id="memory-write-blocked",
        metric_name="memory_write_blocked_total",
        threshold=0,
        operator="gt",
        severity="warning",
        title="Memory writes are blocked",
        next_action="inspect memory write intent policy and DSGVO mode",
    ),
    ObservabilityAlertRule(
        rule_id="raptorgraph-maintenance-failing",
        metric_name="raptorgraph_maintenance_failures_total",
        threshold=0,
        operator="gt",
        severity="error",
        title="RaptorGraph maintenance is failing",
        next_action="inspect RaptorGraph maintenance provenance and rebuild config",
        suppress_during_maintenance=False,
    ),
    ObservabilityAlertRule(
        rule_id="llm-call-failing",
        metric_name="llm_call_failures_total",
        threshold=0,
        operator="gt",
        severity="warning",
        title="Model calls are failing",
        next_action="inspect AI activity ledger by provider/model scope",
    ),
    ObservabilityAlertRule(
        rule_id="local-model-slow",
        metric_name="local_model_latency_seconds",
        threshold=30,
        operator="gt",
        severity="warning",
        title="Local model latency is high",
        next_action="inspect Gemma maintenance queue and local model resource pressure",
        suppress_during_maintenance=True,
    ),
)


def alert_rule_catalog() -> dict[str, Any]:
    return {
        "schema": OBSERVABILITY_ALERT_ROUTING_SCHEMA,
        "status": "ready",
        "rule_count": len(DEFAULT_ALERT_RULES),
        "rules": tuple(rule.to_dict() for rule in DEFAULT_ALERT_RULES),
        "raw_content_visible": False,
        "delivery_performed": False,
        "live_dispatch_configured": False,
    }


def build_observability_alert_routes(
    samples: Iterable[RuntimeMetricSample | Mapping[str, Any]],
    *,
    rules: Iterable[ObservabilityAlertRule | Mapping[str, Any]] = DEFAULT_ALERT_RULES,
    maintenance_active: bool = False,
    previous_active_keys: Iterable[Any] = (),
    recently_sent_keys: Iterable[Any] = (),
    configured_channels: Iterable[str] = ("telegram",),
    live_dispatch_enabled: bool = False,
    target_configured: bool = False,
) -> dict[str, Any]:
    normalized_samples = {_normalize_sample(sample).name: _normalize_sample(sample) for sample in samples}
    normalized_rules = tuple(_normalize_rule(rule) for rule in rules)
    previous_keys = {_safe_key(value) for value in previous_active_keys if str(value or "").strip()}
    recent_keys = {_safe_key(value) for value in recently_sent_keys if str(value or "").strip()}
    channels = tuple(channel for channel in configured_channels if str(channel) in ALLOWED_ALERT_CHANNELS - {"auto"})

    routes: list[ObservabilityAlertRoute] = []
    for rule in normalized_rules:
        sample = normalized_samples.get(rule.metric_name)
        if sample is None or not _matches(rule, sample.value):
            continue
        dedupe_key = f"observability-alert-{rule.rule_id}"
        if maintenance_active and rule.suppress_during_maintenance:
            routes.append(_route(rule, "suppressed", "suppressed_by_maintenance_window", dedupe_key))
        elif dedupe_key in previous_keys:
            routes.append(_route(rule, "suppressed", "alert_already_active", dedupe_key))
        elif dedupe_key in recent_keys:
            routes.append(_route(rule, "suppressed", "alert_cooldown_active", dedupe_key))
        else:
            notification = build_user_notification_decision(
                {
                    "event": rule.rule_id,
                    "message": f"{rule.title}. Next action: {rule.next_action}.",
                    "severity": rule.severity,
                    "channel": rule.channel,
                    "dry_run": True,
                    "metadata": {
                        "metric": rule.metric_name,
                        "dedupe_key": dedupe_key,
                        "threshold": str(rule.threshold),
                    },
                },
                configured_channels=channels,
                live_dispatch_enabled=live_dispatch_enabled,
                target_configured=target_configured,
            ).as_public_dict()
            routes.append(_route(rule, "notify_dry_run", "operator_notification_prepared", dedupe_key, notification))

    return {
        "schema": OBSERVABILITY_ALERT_ROUTING_SCHEMA,
        "status": "ready",
        "route_count": len(routes),
        "routes": tuple(route.to_dict() for route in routes),
        "raw_content_visible": False,
        "delivery_performed": False,
        "maintenance_active": bool(maintenance_active),
        "live_dispatch_enabled": bool(live_dispatch_enabled),
    }


def _route(
    rule: ObservabilityAlertRule,
    status: str,
    reason: str,
    dedupe_key: str,
    notification: dict[str, Any] | None = None,
) -> ObservabilityAlertRoute:
    return ObservabilityAlertRoute(
        rule_id=rule.rule_id,
        metric_name=rule.metric_name,
        status=status,
        severity=rule.severity,
        reason=reason,
        dedupe_key=dedupe_key,
        notification=notification,
    )


def _normalize_sample(sample: RuntimeMetricSample | Mapping[str, Any]) -> RuntimeMetricSample:
    if isinstance(sample, RuntimeMetricSample):
        return build_runtime_metric_sample(sample.name, sample.value, labels=sample.labels)
    if not isinstance(sample, Mapping):
        raise ObservabilityAlertRoutingError("sample must be a metric sample")
    return build_runtime_metric_sample(sample.get("name") or "", sample.get("value"), labels=sample.get("labels") or {})


def _normalize_rule(rule: ObservabilityAlertRule | Mapping[str, Any]) -> ObservabilityAlertRule:
    if isinstance(rule, ObservabilityAlertRule):
        item = rule
    elif isinstance(rule, Mapping):
        item = ObservabilityAlertRule(
            rule_id=_safe_key(rule.get("rule_id")),
            metric_name=str(rule.get("metric_name") or "").strip(),
            threshold=float(rule.get("threshold") or 0),
            operator=str(rule.get("operator") or "gt").strip().lower(),
            severity=str(rule.get("severity") or "warning").strip().lower(),
            title=" ".join(str(rule.get("title") or "").split()),
            next_action=" ".join(str(rule.get("next_action") or "").split()),
            channel=str(rule.get("channel") or "auto").strip().lower(),
            suppress_during_maintenance=bool(rule.get("suppress_during_maintenance", True)),
        )
    else:
        raise ObservabilityAlertRoutingError("rule must be a mapping")
    if item.operator not in ALLOWED_OPERATORS:
        raise ObservabilityAlertRoutingError("unsupported alert operator")
    if item.severity not in ALLOWED_ALERT_SEVERITIES:
        raise ObservabilityAlertRoutingError("unsupported alert severity")
    if item.channel not in ALLOWED_ALERT_CHANNELS:
        raise ObservabilityAlertRoutingError("unsupported alert channel")
    if not item.title or not item.next_action:
        raise ObservabilityAlertRoutingError("alert title and next_action are required")
    build_runtime_metric_sample(item.metric_name, 0)
    return item


def _matches(rule: ObservabilityAlertRule, value: float) -> bool:
    if rule.operator == "gte":
        return value >= rule.threshold
    return value > rule.threshold


def _safe_key(value: Any) -> str:
    key = "-".join(str(value or "").strip().lower().replace("_", "-").split())
    if not key:
        raise ObservabilityAlertRoutingError("key must not be empty")
    if len(key) > 120:
        raise ObservabilityAlertRoutingError("key is too long")
    if not all(char.isalnum() or char in ".:-" for char in key):
        raise ObservabilityAlertRoutingError("key contains unsupported characters")
    return key

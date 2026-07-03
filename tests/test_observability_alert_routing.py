from src.observability_alert_routing import (
    alert_rule_catalog,
    build_observability_alert_routes,
)
from src.observability_metrics import build_runtime_metric_sample


def test_alert_catalog_contains_operator_relevant_rules_without_live_dispatch():
    catalog = alert_rule_catalog()

    rule_ids = {rule["rule_id"] for rule in catalog["rules"]}

    assert catalog["status"] == "ready"
    assert "telegram-poll-failing" in rule_ids
    assert "scheduler-delivery-failing" in rule_ids
    assert "universal-inbox-blocked" in rule_ids
    assert "raptorgraph-maintenance-failing" in rule_ids
    assert catalog["raw_content_visible"] is False
    assert catalog["delivery_performed"] is False
    assert catalog["live_dispatch_configured"] is False


def test_builds_dry_run_operator_notification_for_triggered_metric():
    routes = build_observability_alert_routes(
        [
            build_runtime_metric_sample("telegram_poll_failure_total", 2),
            build_runtime_metric_sample("scheduler_due_tasks", 0),
        ]
    )

    route = routes["routes"][0]

    assert routes["route_count"] == 1
    assert route["rule_id"] == "telegram-poll-failing"
    assert route["status"] == "notify_dry_run"
    assert route["delivery_performed"] is False
    assert route["notification"]["status"] == "dry_run"
    assert route["notification"]["dispatch_allowed"] is False
    assert route["notification"]["token_value_visible"] is False
    assert route["notification"]["chat_target_value_visible"] is False


def test_suppresses_noisy_alerts_during_maintenance_window():
    routes = build_observability_alert_routes(
        [build_runtime_metric_sample("local_model_latency_seconds", 45)],
        maintenance_active=True,
    )

    assert routes["routes"][0]["status"] == "suppressed"
    assert routes["routes"][0]["reason"] == "suppressed_by_maintenance_window"
    assert routes["routes"][0]["notification"] is None


def test_raptorgraph_failure_is_not_suppressed_by_maintenance_window():
    routes = build_observability_alert_routes(
        [build_runtime_metric_sample("raptorgraph_maintenance_failures_total", 1)],
        maintenance_active=True,
    )

    assert routes["routes"][0]["rule_id"] == "raptorgraph-maintenance-failing"
    assert routes["routes"][0]["status"] == "notify_dry_run"


def test_duplicate_and_cooldown_routes_do_not_prepare_new_notifications():
    duplicate = build_observability_alert_routes(
        [build_runtime_metric_sample("memory_write_blocked_total", 1)],
        previous_active_keys=("observability-alert-memory-write-blocked",),
    )
    cooldown = build_observability_alert_routes(
        [build_runtime_metric_sample("memory_write_blocked_total", 1)],
        recently_sent_keys=("observability-alert-memory-write-blocked",),
    )

    assert duplicate["routes"][0]["status"] == "suppressed"
    assert duplicate["routes"][0]["reason"] == "alert_already_active"
    assert duplicate["routes"][0]["notification"] is None
    assert cooldown["routes"][0]["status"] == "suppressed"
    assert cooldown["routes"][0]["reason"] == "alert_cooldown_active"
    assert cooldown["routes"][0]["notification"] is None

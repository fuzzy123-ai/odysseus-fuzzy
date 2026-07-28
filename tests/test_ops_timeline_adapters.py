import json
from types import SimpleNamespace

import src.constants as constants
from src.observability_alert_routing import build_observability_alert_routes
from src.observability_diagnostics_bridge import build_observability_diagnostic_packet
from src.observability_metrics import build_runtime_metric_sample, build_runtime_metrics_snapshot
from src.ops_timeline_adapters import (
    OPS_TIMELINE_ADAPTERS_SCHEMA,
    build_ops_timeline_from_sources,
    create_default_security_incident_store,
    observability_diagnostic_events,
    security_remediation_plan_events,
    persisted_security_store_events,
    system_health_dashboard_events,
)
from src.security_incident_model import build_recommended_action, build_security_incident
from src.security_remediation_actions import prepare_remediation_plan
from src.security_response_policy import decide_incident_response
from src.system_health_agent_interface import AlertSummary, CollectorStatus, HealthSnapshot
from src.system_health_dashboard_summary import build_system_health_dashboard_summary
from src.security_incident_store import SecurityIncidentStore


def _dashboard_summary():
    snapshot = HealthSnapshot.create(
        schema_version="1.0",
        generated_at="2026-07-06T10:00:00Z",
        overall_status="warn",
        collectors=(
            CollectorStatus.create(
                collector_id="disk",
                state="warn",
                summary="disk usage is elevated",
                observed_value="80 percent",
            ),
        ),
        host_label="ops-node",
    )
    alert = AlertSummary.create(
        severity="warn",
        title="Disk pressure",
        cause="redacted collector threshold",
        next_action="review redacted host-agent snapshot",
        dedupe_key="disk-pressure",
    )
    return build_system_health_dashboard_summary(snapshot=snapshot, alerts=(alert,))


def _diagnostic_packet():
    return build_observability_diagnostic_packet(
        question="Warum kam die Telegram Erinnerung nicht?",
        metrics_snapshot=build_runtime_metrics_snapshot(
            (
                build_runtime_metric_sample("telegram_poll_failure_total", 1),
                build_runtime_metric_sample("scheduler_delivery_failures_total", 2),
            )
        ),
    )


def _alert_routes():
    return build_observability_alert_routes(
        (
            build_runtime_metric_sample("telegram_poll_failure_total", 1),
            build_runtime_metric_sample("local_model_latency_seconds", 45),
        ),
        maintenance_active=True,
    )


def _incident_policy_and_plan():
    action = build_recommended_action(
        action_type="service_restart",
        summary="Prepare a service restart request.",
        risk="Brief service interruption requires operator review.",
        action_id="act-restart",
    )
    incident = build_security_incident(
        incident_id="inc-ops",
        level=3,
        severity="high",
        confidence=0.9,
        status="open",
        trigger="Service down event exceeded policy threshold.",
        affected_surfaces=("ops",),
        correlation_ids=("corr-ops-1",),
        evidence_refs=("runtime-event:evt-ops-1",),
        recommended_actions=(action,),
    )
    policy = decide_incident_response(incident)
    plan = prepare_remediation_plan(incident, requested_action_ids=("act-restart",))
    return incident, policy, plan


def test_dashboard_adapter_maps_overview_and_sections_to_readonly_events():
    events = system_health_dashboard_events(_dashboard_summary())

    assert events[0]["event_id"] == "ops-system-health-overview"
    assert events[0]["stage"] == "signal"
    assert events[0]["status"] == "watch"
    assert events[0]["surface"] == "system_health"
    assert any(event["event_id"] == "ops-system-health-section-alerts" for event in events)
    assert all(event["writes_performed"] is False for event in events)
    assert all(event["host_commands_performed"] is False for event in events)


def test_observability_adapters_map_diagnostics_and_routes_without_raw_query():
    diagnostic_events = observability_diagnostic_events(_diagnostic_packet())
    route_timeline = build_ops_timeline_from_sources(alert_routes=_alert_routes(), timeline_id="ops-routes")
    encoded = json.dumps({"diagnostic_events": diagnostic_events, "route_timeline": route_timeline}, sort_keys=True)

    assert diagnostic_events[0]["stage"] == "triage"
    assert diagnostic_events[0]["status"] == "alert"
    assert "inspect_scheduler_queue" in diagnostic_events[0]["action_refs"]
    assert any(event["event_id"] == "ops-alert-route-telegram-poll-failing" for event in route_timeline["events"])
    assert any(event["status"] == "blocked" for event in route_timeline["events"])
    assert "Warum kam" not in encoded
    assert "chat_id" not in encoded.lower()
    assert route_timeline["adapter_schema"] == OPS_TIMELINE_ADAPTERS_SCHEMA


def test_security_and_remediation_adapters_preserve_operator_gates():
    incident, policy, plan = _incident_policy_and_plan()

    timeline = build_ops_timeline_from_sources(
        security_incident=incident,
        response_policy=policy,
        remediation_plan=plan,
        timeline_id="ops-security",
    )
    gates = set(timeline["required_gates"])
    action_events = [event for event in timeline["events"] if event["surface"] == "remediation"]

    assert timeline["status"] == "contain"
    assert "service_restart-operator-go" in gates
    assert any(event["stage"] == "action_plan" and event["status"] == "contain" for event in action_events)
    assert any(event["stage"] == "operator_gate" and event["status"] == "blocked" for event in action_events)
    assert timeline["writes_performed"] is False
    assert timeline["live_actions_performed"] is False


def test_adapter_hashes_sensitive_evidence_from_legacy_packet_shape():
    events = observability_diagnostic_events(
        {
            "status": "attention",
            "intent": "general_operations",
            "findings": (
                {
                    "code": "legacy-evidence",
                    "severity": "warning",
                    "evidence": r"C:\Users\nkatz\private.log",
                },
            ),
            "recommended_next_actions": (),
        }
    )
    encoded = json.dumps(events, sort_keys=True)

    assert events[1]["evidence_refs"][0].startswith("evidence:sha256:")
    assert "private.log" not in encoded


def test_empty_source_adapter_returns_normal_readonly_timeline():
    timeline = build_ops_timeline_from_sources(timeline_id="ops-empty")

    assert timeline["status"] == "normal"
    assert timeline["event_count"] == 0
    assert timeline["adapter_sources"] == ()
    assert timeline["raw_content_visible"] is False
    assert timeline["host_paths_visible"] is False


def test_persisted_store_timeline_uses_audit_only_and_represents_expiry(tmp_path):
    now = [100.0]
    store = SecurityIncidentStore(tmp_path / "timeline.sqlite", clock=lambda: now[0])
    store.create_incident(incident_id="inc-time", incident_ref="evidence:sha256:" + "a" * 64, audit_ref="audit:sha256:" + "b" * 64)
    store.create_action(
        action_id="act-time", incident_id="inc-time", action_type="service_restart",
        scope_fingerprint="scope:sha256:" + "c" * 64, policy_revision="policy:sha256:" + "d" * 64,
        idempotency_key="idem-time", ttl_seconds=5, audit_ref="audit:sha256:" + "e" * 64,
    )
    now[0] = 110.0
    store.get_action("act-time")  # persist the expiry before the read-only adapter runs.

    events = persisted_security_store_events(store)
    timeline = build_ops_timeline_from_sources(store=store, timeline_id="ops-persisted")
    encoded = json.dumps({"events": events, "timeline": timeline}, sort_keys=True)

    assert events[0]["summary"].endswith("state is expired.")
    assert timeline["status"] == "recovery"
    assert "incident_ref" not in encoded
    assert "scope:sha256:" not in encoded
    assert "idempotency" not in encoded


def test_persisted_store_orders_by_audit_sequence_and_selects_after_long_history():
    audit = [SimpleNamespace(sequence=index, incident_id=f"inc-{index:03}", action_id=None, event_type="incident_created") for index in range(1, 102)]
    audit.extend((
        SimpleNamespace(sequence=102, incident_id="inc-target", action_id="act-a", event_type="action_proposed"),
        SimpleNamespace(sequence=103, incident_id="inc-target", action_id="act-b", event_type="action_proposed"),
        SimpleNamespace(sequence=104, incident_id="inc-target", action_id="act-a", event_type="action_prepared"),
        SimpleNamespace(sequence=105, incident_id="inc-target", action_id="act-old", event_type="action_expired"),
        SimpleNamespace(sequence=106, incident_id="inc-target", action_id="act-live", event_type="action_proposed"),
    ))

    class _Store:
        def audit_events(self):
            return tuple(audit)

        def get_incident(self, incident_id):
            return SimpleNamespace(incident_id=incident_id)

    events = persisted_security_store_events(_Store())
    target = next(event for event in events if event["event_id"].startswith("ops-persisted-security-"))
    timeline = build_ops_timeline_from_sources(store=_Store(), timeline_id="ops-long")

    assert len(events) == 20
    assert "4 action records (3 active, 1 expired)" in target["summary"]
    assert target["summary"].endswith("latest action state is proposed.")
    assert target["status"] == "contain"
    assert timeline["status"] == "contain"


def test_default_store_provider_uses_fixed_data_dir_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path))

    store = create_default_security_incident_store()

    assert store is not None
    assert store.database_path == tmp_path / "security_incidents.sqlite"

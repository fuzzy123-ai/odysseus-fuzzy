"""Adapters from existing ops contracts into the canonical ops timeline."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.ops_timeline import build_ops_timeline, build_ops_timeline_event


OPS_TIMELINE_ADAPTERS_SCHEMA = "odysseus.ops_timeline.adapters.v1"
_DEFAULT_INCIDENT_DB_NAME = "security_incidents.sqlite"


def create_default_security_incident_store(*, database_path: str | Path | None = None) -> Any | None:
    """Create the fixed local store during server setup, never during a read."""

    try:
        from src.constants import DATA_DIR
        from src.security_incident_store import SecurityIncidentStore

        path = Path(database_path) if database_path is not None else Path(DATA_DIR) / _DEFAULT_INCIDENT_DB_NAME
        return SecurityIncidentStore(path)
    except Exception:
        return None


def build_ops_timeline_from_sources(
    *,
    dashboard_summary: Any = None,
    diagnostic_packet: Mapping[str, Any] | None = None,
    alert_routes: Mapping[str, Any] | None = None,
    security_incident: Mapping[str, Any] | None = None,
    response_policy: Mapping[str, Any] | None = None,
    remediation_plan: Mapping[str, Any] | None = None,
    store: Any = None,
    timeline_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compose a read-only ops timeline from already-redacted source packets."""

    events = []
    events.extend(system_health_dashboard_events(dashboard_summary))
    events.extend(observability_diagnostic_events(diagnostic_packet))
    events.extend(observability_alert_route_events(alert_routes))
    events.extend(security_incident_events(security_incident))
    events.extend(security_response_policy_events(response_policy))
    events.extend(security_remediation_plan_events(remediation_plan))
    events.extend(persisted_security_store_events(store))
    timeline = build_ops_timeline(events, timeline_id=timeline_id, generated_at=generated_at)
    timeline["adapter_schema"] = OPS_TIMELINE_ADAPTERS_SCHEMA
    timeline["adapter_sources"] = tuple(
        source
        for source, value in (
            ("system_health_dashboard", dashboard_summary),
            ("observability_diagnostic_packet", diagnostic_packet),
            ("observability_alert_routes", alert_routes),
            ("security_incident", security_incident),
            ("security_response_policy", response_policy),
            ("security_remediation_plan", remediation_plan),
            ("security_incident_store", store),
        )
        if value is not None
    )
    return timeline


def persisted_security_store_status(store: Any) -> str:
    if store is None or not callable(getattr(store, "audit_events", None)) or not callable(getattr(store, "get_incident", None)):
        return "not_configured"
    try:
        records = tuple(store.audit_events())
        for record in records:
            int(record.sequence)
            str(record.incident_id)
        incident_ids = tuple(dict.fromkeys(str(record.incident_id) for record in sorted(records, key=lambda record: int(record.sequence), reverse=True)))[:20]
        for incident_id in incident_ids:
            store.get_incident(incident_id)
    except Exception:
        return "unavailable"
    return "available"


def persisted_security_store_events(store: Any) -> tuple[dict[str, Any], ...]:
    """Summarize durable state through public, non-expiring store reads only.

    `get_action()` intentionally performs expiry maintenance, so it is never
    used here.  Audit history gives a deterministic, bounded view including
    already-persisted expiry transitions without turning a snapshot into a
    hidden write path.
    """

    if persisted_security_store_status(store) != "available":
        return ()
    try:
        audit = tuple(sorted(tuple(store.audit_events()), key=lambda event: int(event.sequence)))
    except Exception:
        return ()
    # Newest audit sequence first, deduplicated by incident, then bounded for
    # output.  We intentionally select before bounding so old history cannot
    # hide a newer persisted incident.
    incident_ids = tuple(dict.fromkeys(str(event.incident_id) for event in reversed(audit)))[:20]
    events: list[dict[str, Any]] = []
    for incident_id in incident_ids:
        try:
            store.get_incident(incident_id)
        except Exception:
            continue
        incident_events = tuple(event for event in audit if str(event.incident_id) == incident_id)
        action_states: dict[str, tuple[int, str]] = {}
        for event in incident_events:
            action_id = getattr(event, "action_id", None)
            if action_id:
                action_states[str(action_id)] = (int(event.sequence), _audit_action_state(str(getattr(event, "event_type", ""))))
        states = tuple(value[1] for value in action_states.values())
        latest = max(action_states.values(), default=(0, "none"), key=lambda value: value[0])[1]
        active_count = sum(state in {"proposed", "prepared", "approved", "executing"} for state in states)
        expired_count = sum(state == "expired" for state in states)
        status = "contain" if active_count else ("recovery" if latest == "expired" else "watch")
        gates = ("OPS-REMEDIATION-GO",) if status == "contain" else ()
        opaque = hashlib.sha256(incident_id.encode("utf-8")).hexdigest()[:16]
        events.append(build_ops_timeline_event(
            event_id=f"ops-persisted-security-{opaque}",
            stage="action_plan" if action_states else "signal",
            status=status,
            surface="security",
            severity="warning" if latest == "expired" else ("error" if action_states else "info"),
            summary=f"Persisted security incident has {len(action_states)} action records ({active_count} active, {expired_count} expired); latest action state is {latest}.",
            evidence_refs=("security-incident-store",),
            required_gates=gates,
        ))
    return tuple(events)


def _audit_action_state(event_type: str) -> str:
    if event_type.startswith("action_"):
        candidate = event_type.removeprefix("action_")
        if candidate in {"proposed", "prepared", "approved", "denied", "expired", "executing", "executed", "verified", "failed", "rolled_back"}:
            return candidate
    return "recorded"


def system_health_dashboard_events(summary: Any) -> tuple[dict[str, Any], ...]:
    payload = _as_mapping(summary)
    if not payload:
        return ()
    overview = _text(payload.get("overview_state") or "no_data")
    events = [
        build_ops_timeline_event(
            event_id="ops-system-health-overview",
            stage="signal",
            status=_dashboard_status(overview),
            surface="system_health",
            severity=_dashboard_severity(overview),
            summary=f"System Health overview state is {_summary_token(overview)}.",
            evidence_refs=("system-health-dashboard-summary",),
        )
    ]
    for section in tuple(payload.get("sections") or ())[:8]:
        if not isinstance(section, Mapping):
            continue
        section_id = _text(section.get("section_id") or "section")
        section_state = _text(section.get("state") or "no_data")
        events.append(
            build_ops_timeline_event(
                event_id=f"ops-system-health-section-{_safe_ref(section_id)}",
                stage="evidence",
                status=_dashboard_status(section_state),
                surface="system_health",
                severity=_dashboard_severity(section_state),
                summary=f"System Health section {_summary_token(section_id)} state is {_summary_token(section_state)}.",
                evidence_refs=(f"system-health-section:{_safe_ref(section_id)}",),
            )
        )
    return tuple(events)


def observability_diagnostic_events(packet: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if not isinstance(packet, Mapping):
        return ()
    status = _text(packet.get("status") or "insufficient_evidence")
    intent = _text(packet.get("intent") or "general_operations")
    events = [
        build_ops_timeline_event(
            event_id=f"ops-diagnostics-{_safe_ref(intent)}",
            stage="triage",
            status=_diagnostic_status(status),
            surface="diagnostics",
            severity=_diagnostic_severity(status),
            summary=f"Diagnostics triage intent {_summary_token(intent)} status {_summary_token(status)}.",
            evidence_refs=("observability-diagnostic-packet",),
            action_refs=tuple(_safe_ref(value) for value in packet.get("recommended_next_actions") or ()),
        )
    ]
    for finding in tuple(packet.get("findings") or ())[:10]:
        if not isinstance(finding, Mapping):
            continue
        code = _text(finding.get("code") or "finding")
        severity = _finding_severity(finding.get("severity"))
        evidence = _text(finding.get("evidence") or "redacted-diagnostics")
        events.append(
            build_ops_timeline_event(
                event_id=f"ops-diagnostics-finding-{_safe_ref(code)}",
                stage="evidence",
                status="alert" if severity in {"error", "critical"} else "watch",
                surface="diagnostics",
                severity=severity,
                summary=f"Diagnostics finding {_summary_token(code)} observed.",
                evidence_refs=(evidence,),
                correlation_ids=(code,),
            )
        )
    return tuple(events)


def observability_alert_route_events(routes: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if not isinstance(routes, Mapping):
        return ()
    events = []
    for route in tuple(routes.get("routes") or ())[:20]:
        if not isinstance(route, Mapping):
            continue
        rule_id = _text(route.get("rule_id") or "observability-alert")
        route_status = _text(route.get("status") or "unknown")
        severity = _finding_severity(route.get("severity"))
        events.append(
            build_ops_timeline_event(
                event_id=f"ops-alert-route-{_safe_ref(rule_id)}",
                stage="signal",
                status=_alert_route_status(route_status, severity),
                surface="observability",
                severity=severity,
                summary=f"Observability alert route {_summary_token(rule_id)} is {_summary_token(route_status)}.",
                evidence_refs=(_text(route.get("metric_name") or "observability-alert-route"),),
                correlation_ids=(_text(route.get("dedupe_key") or rule_id),),
            )
        )
    return tuple(events)


def security_incident_events(incident: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if not isinstance(incident, Mapping):
        return ()
    incident_id = _text(incident.get("incident_id") or "incident")
    level_name = _text(incident.get("level_name") or incident.get("status") or "watch")
    status = _incident_status(level_name, incident.get("status"))
    gates = ("OPS-REMEDIATION-GO",) if status in {"contain", "lockdown"} else ()
    return (
        build_ops_timeline_event(
            event_id=f"ops-security-incident-{hashlib.sha256(incident_id.encode('utf-8')).hexdigest()[:16]}",
            stage="signal",
            status=status,
            surface="security",
            severity=_incident_severity(incident.get("severity"), status),
            summary=f"Security incident summary is {_summary_token(status)}.",
            # Do not relay incident evidence/correlation material into the Ops
            # Console; the timeline is a status summary, not an evidence reader.
            evidence_refs=("security-incident-summary",),
            required_gates=gates,
        ),
    )


def security_response_policy_events(policy: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if not isinstance(policy, Mapping):
        return ()
    decision = _text(policy.get("decision") or "observe")
    gate_required = bool(policy.get("operator_gate_required"))
    status = _policy_status(decision, gate_required)
    gates = tuple(
        _policy_gate_from_action(action)
        for action in tuple(policy.get("action_results") or ())
        if isinstance(action, Mapping) and bool(action.get("operator_gate_required"))
    )
    if status == "contain" and not gates:
        gates = ("OPS-REMEDIATION-GO",)
    return (
        build_ops_timeline_event(
            event_id=f"ops-security-policy-{_safe_ref(decision)}",
            stage="decision",
            status=status,
            surface="security",
            severity="critical" if status in {"lockdown", "denied"} else ("error" if status in {"contain", "blocked"} else "warning"),
            summary=f"Security response policy decision is {_summary_token(decision)}.",
            evidence_refs=("security-response-policy",),
            required_gates=gates,
        ),
    )


def security_remediation_plan_events(plan: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if not isinstance(plan, Mapping):
        return ()
    events = []
    for action in tuple(plan.get("actions") or ())[:20]:
        if not isinstance(action, Mapping):
            continue
        action_id = _text(action.get("action_id") or "action")
        action_status = _text(action.get("status") or "prepared")
        gate = _text(action.get("policy_gate") or "")
        gates = (gate,) if gate and bool(action.get("live_gate_required") or action.get("requires_operator_confirmation")) else ()
        events.append(
            build_ops_timeline_event(
                event_id=f"ops-remediation-action-{_safe_ref(action_id)}",
                stage="action_plan",
                status=_remediation_status(action_status, gates),
                surface="remediation",
                severity="critical" if gates else "warning",
                summary=f"Remediation action {_summary_token(action_id)} is {_summary_token(action_status)}.",
                evidence_refs=("security-remediation-plan",),
                required_gates=gates,
                action_refs=(action_id,),
            )
        )
        if gates:
            events.append(
                build_ops_timeline_event(
                    event_id=f"ops-remediation-gate-{_safe_ref(action_id)}",
                    stage="operator_gate",
                    status="blocked",
                    surface="remediation",
                    severity="critical",
                    summary=f"Remediation action {_summary_token(action_id)} awaits operator gate.",
                    evidence_refs=("security-remediation-plan",),
                    required_gates=gates,
                    action_refs=(action_id,),
                )
            )
    return tuple(events)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "to_dict"):
        mapped = value.to_dict()
        return mapped if isinstance(mapped, Mapping) else {}
    return {}


def _dashboard_status(state: str) -> str:
    if state == "ok":
        return "normal"
    if state == "critical":
        return "alert"
    if state in {"agent_offline", "warn", "setup_required", "partial_unknown"}:
        return "watch"
    return "watch"


def _dashboard_severity(state: str) -> str:
    if state == "critical":
        return "critical"
    if state in {"agent_offline", "warn", "setup_required", "partial_unknown"}:
        return "warning"
    return "info"


def _diagnostic_status(status: str) -> str:
    if status == "needs_attention":
        return "alert"
    if status in {"attention", "insufficient_evidence"}:
        return "watch"
    return "normal"


def _diagnostic_severity(status: str) -> str:
    if status == "needs_attention":
        return "error"
    if status in {"attention", "insufficient_evidence"}:
        return "warning"
    return "info"


def _alert_route_status(status: str, severity: str) -> str:
    if status == "notify_dry_run":
        return "alert" if severity in {"error", "critical"} else "watch"
    if status == "suppressed":
        return "blocked"
    return "watch"


def _incident_status(level_name: str, status: Any) -> str:
    normalized_status = _text(status)
    if normalized_status in {"dismissed", "closed"}:
        return "recovery"
    if level_name == "lockdown":
        return "lockdown"
    if level_name == "contain":
        return "contain"
    if level_name == "alert":
        return "alert"
    if level_name == "watch":
        return "watch"
    return "normal"


def _incident_severity(severity: Any, status: str) -> str:
    text = _text(severity)
    if text in {"critical"} or status == "lockdown":
        return "critical"
    if text in {"high"} or status in {"contain", "alert"}:
        return "error"
    if text in {"medium", "low"} or status == "watch":
        return "warning"
    return "info"


def _policy_status(decision: str, gate_required: bool) -> str:
    if decision == "denied":
        return "denied"
    if decision == "blocked":
        return "blocked"
    if decision == "gated_action" or gate_required:
        return "contain"
    if decision == "recommend":
        return "alert"
    return "watch"


def _remediation_status(status: str, gates: tuple[str, ...]) -> str:
    if status == "blocked":
        return "blocked"
    if gates:
        return "contain"
    return "watch"


def _finding_severity(value: Any) -> str:
    text = _text(value)
    if text in {"critical", "error"}:
        return "error" if text == "error" else "critical"
    if text in {"warning", "warn"}:
        return "warning"
    return "info"


def _policy_gate_from_action(action: Mapping[str, Any]) -> str:
    gate = _text(action.get("policy_gate") or "")
    if gate:
        return gate
    action_type = _safe_ref(action.get("action_type") or "operator-action")
    return f"{action_type}-operator-go"


def _safe_ref(value: Any) -> str:
    text = _text(value)
    out = "".join(char if char.isalnum() or char in "_.:@/-" else "-" for char in text)[:120]
    return out.strip("-") or "ref"


def _summary_token(value: Any) -> str:
    return _safe_ref(value).replace("_", "-")


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())

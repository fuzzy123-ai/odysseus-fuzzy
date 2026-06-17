from src.system_health_agent_interface import AlertSummary, CollectorStatus, HealthSnapshot
from src.system_health_dashboard_summary import DashboardState, build_system_health_dashboard_summary
from src.system_health_ops_readiness import OpsReadinessItem, OpsReadinessReport
from src.system_health_rule_engine import AlertDecision, RuleEvaluation


def _snapshot(*, overall_status=None, collectors=(), host_label="ops-node-1"):
    return HealthSnapshot.create(
        schema_version="1.0",
        generated_at="2026-06-17T22:00:00Z",
        overall_status=overall_status,
        collectors=collectors
        or (
            CollectorStatus.create(
                collector_id="disk",
                state="ok",
                summary="disk usage is healthy",
                observed_value="42 percent",
            ),
        ),
        host_label=host_label,
    )


def _readiness(overall_status: str) -> OpsReadinessReport:
    status_map = {"go": "pass", "warn": "warn", "no_go": "fail"}
    return OpsReadinessReport.create(
        mode="foundation",
        items=(
            OpsReadinessItem.create(
                item_id="host_agent_boundary",
                status=status_map[overall_status],
                summary=f"readiness is {overall_status}",
                next_action="" if overall_status == "go" else "operator review required",
            ),
        ),
    )


def test_no_data_and_agent_offline_are_conservative():
    no_data = build_system_health_dashboard_summary()
    agent_offline = build_system_health_dashboard_summary(
        alerts=(
            AlertSummary.create(
                severity="warn",
                title="Agent stale",
                cause="no recent host snapshot",
                next_action="check agent",
                dedupe_key="agent-stale",
            ),
        )
    )

    assert no_data.overview_state == DashboardState.NO_DATA
    assert agent_offline.overview_state == DashboardState.AGENT_OFFLINE


def test_overview_prioritizes_critical_then_warn():
    critical_summary = build_system_health_dashboard_summary(
        snapshot=_snapshot(overall_status="critical"),
    )
    warn_summary = build_system_health_dashboard_summary(
        snapshot=_snapshot(
            collectors=(
                CollectorStatus.create(
                    collector_id="memory",
                    state="warn",
                    summary="memory is elevated",
                ),
            ),
            overall_status="warn",
        ),
    )

    assert critical_summary.overview_state == DashboardState.CRITICAL
    assert warn_summary.overview_state == DashboardState.WARN


def test_setup_required_and_partial_unknown_are_detected():
    setup_required = build_system_health_dashboard_summary(
        snapshot=_snapshot(
            collectors=(
                CollectorStatus.create(
                    collector_id="container-runtime",
                    state="unsupported",
                    summary="container runtime not configured",
                    setup_hint="install podman",
                ),
            ),
        ),
    )
    partial_unknown = build_system_health_dashboard_summary(
        snapshot=_snapshot(
            collectors=(
                CollectorStatus.create(
                    collector_id="smartctl",
                    state="unknown",
                    summary="smart data is incomplete",
                ),
            ),
        ),
    )

    assert setup_required.overview_state == DashboardState.SETUP_REQUIRED
    assert partial_unknown.overview_state == DashboardState.PARTIAL_UNKNOWN


def test_collectors_section_counts_states_and_readiness_section_reflects_report():
    summary = build_system_health_dashboard_summary(
        snapshot=_snapshot(
            collectors=(
                CollectorStatus.create(collector_id="cpu", state="ok", summary="cpu is healthy"),
                CollectorStatus.create(collector_id="memory", state="warn", summary="memory is elevated"),
                CollectorStatus.create(collector_id="smartctl", state="unknown", summary="smart data missing"),
                CollectorStatus.create(
                    collector_id="container-runtime",
                    state="unsupported",
                    summary="container runtime missing",
                ),
            ),
        ),
        readiness=_readiness("warn"),
    )

    collectors_section = next(section for section in summary.sections if section.section_id == "collectors")
    readiness_section = next(section for section in summary.sections if section.section_id == "readiness")

    assert collectors_section.counts == {
        "critical": 0,
        "ok": 1,
        "unknown": 1,
        "unsupported": 1,
        "warn": 1,
    }
    assert readiness_section.summary == "readiness: warn"
    assert readiness_section.state == DashboardState.WARN


def test_alerts_can_come_from_alert_decision():
    alert_decision = AlertDecision(
        evaluations=(
            RuleEvaluation(
                rule_id="disk-pressure",
                collector_id="disk",
                dedupe_key="disk-pressure-disk",
                collector_state=None,
                triggered=True,
                suppressed=False,
                repeated=False,
                recovered=False,
                severity=None,
                reason="test",
                setup_hint="",
            ),
        ),
        alerts=(
            AlertSummary.create(
                severity="critical",
                title="Disk pressure",
                cause="root volume exceeded threshold",
                next_action="free disk space",
                dedupe_key="disk-pressure-disk",
            ),
        ),
        cleared_keys=(),
    )

    summary = build_system_health_dashboard_summary(snapshot=_snapshot(), alerts=alert_decision)
    alerts_section = next(section for section in summary.sections if section.section_id == "alerts")

    assert summary.overview_state == DashboardState.CRITICAL
    assert alerts_section.counts == {"active": 1}


def test_to_dict_is_stable():
    summary = build_system_health_dashboard_summary(
        snapshot=_snapshot(),
        readiness=_readiness("go"),
    )

    assert summary.to_dict() == {
        "overview_state": "ok",
        "sections": (
            {
                "section_id": "collectors",
                "state": "ok",
                "summary": "collectors: 1 total on ops-node-1",
                "counts": {
                    "critical": 0,
                    "ok": 1,
                    "unknown": 0,
                    "unsupported": 0,
                    "warn": 0,
                },
            },
            {
                "section_id": "alerts",
                "state": "no_data",
                "summary": "alerts: quiet",
                "counts": {
                    "active": 0,
                },
            },
            {
                "section_id": "readiness",
                "state": "ok",
                "summary": "readiness: go",
                "counts": {
                    "item_count": "1",
                    "overall_status": "go",
                },
            },
        ),
    }

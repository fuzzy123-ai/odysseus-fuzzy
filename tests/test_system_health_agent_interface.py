from src.system_health_agent_interface import (
    AlertSummary,
    CollectorState,
    CollectorStatus,
    HealthAgentInterfaceError,
    HealthSnapshot,
)


def test_snapshot_derives_conservative_overall_status():
    snapshot = HealthSnapshot.create(
        schema_version="1.0",
        generated_at="2026-06-17T13:00:00Z",
        collectors=(
            CollectorStatus.create(
                collector_id="docker-daemon",
                state="warn",
                summary="docker daemon is degraded",
                observed_value="unresponsive",
            ),
            CollectorStatus.create(
                collector_id="disk-space",
                state="ok",
                summary="disk space is healthy",
                observed_value="42%",
            ),
        ),
        alerts=(
            AlertSummary.create(
                severity="critical",
                title="Disk almost full",
                cause="root volume exceeded threshold",
                next_action="free disk space",
                dedupe_key="disk-root-critical",
            ),
        ),
        host_label="lab-host",
    )

    assert snapshot.overall_status == CollectorState.CRITICAL


def test_unknown_or_unsupported_collectors_do_not_crash():
    snapshot = HealthSnapshot.create(
        schema_version="1.0",
        generated_at="2026-06-17T13:05:00Z",
        collectors=(
            CollectorStatus.create(
                collector_id="podman-socket",
                state="unsupported",
                summary="podman socket not configured",
            ),
            CollectorStatus.create(
                collector_id="telegram-dispatch",
                state="unknown",
                summary="health agent has no token visibility",
            ),
        ),
    )

    assert snapshot.overall_status in {CollectorState.UNKNOWN, CollectorState.UNSUPPORTED}
    assert all(item.setup_hint for item in snapshot.collectors)


def test_overall_status_must_not_undercut_derived_severity():
    try:
        HealthSnapshot.create(
            schema_version="1.0",
            generated_at="2026-06-17T13:10:00Z",
            collectors=(
                CollectorStatus.create(
                    collector_id="docker-daemon",
                    state="critical",
                    summary="docker daemon unreachable",
                ),
            ),
            overall_status="ok",
        )
    except HealthAgentInterfaceError as exc:
        assert "overall_status" in str(exc)
    else:
        raise AssertionError("expected HealthAgentInterfaceError")


def test_to_dict_is_stable():
    snapshot = HealthSnapshot.create(
        schema_version="1.0",
        generated_at="2026-06-17T13:15:00Z",
        collectors=(
            CollectorStatus.create(
                collector_id="disk-space",
                state="ok",
                summary="disk space is healthy",
                observed_value="42%",
            ),
            CollectorStatus.create(
                collector_id="host-agent",
                state="warn",
                summary="agent version is behind",
                setup_hint="upgrade during next maintenance window",
            ),
        ),
        alerts=(
            AlertSummary.create(
                severity="warn",
                title="Agent version lagging",
                cause="host agent build is stale",
                next_action="schedule upgrade",
                dedupe_key="agent-version-warn",
                cooldown_hint="24h",
            ),
        ),
        host_label="ops-node-1",
    )

    assert snapshot.to_dict() == {
        "schema_version": "1.0",
        "generated_at": "2026-06-17T13:15:00Z",
        "overall_status": "warn",
        "collectors": (
            {
                "collector_id": "disk-space",
                "state": "ok",
                "summary": "disk space is healthy",
                "observed_value": "42%",
                "setup_hint": "",
            },
            {
                "collector_id": "host-agent",
                "state": "warn",
                "summary": "agent version is behind",
                "observed_value": "",
                "setup_hint": "upgrade during next maintenance window",
            },
        ),
        "alerts": (
            {
                "severity": "warn",
                "title": "Agent version lagging",
                "cause": "host agent build is stale",
                "next_action": "schedule upgrade",
                "dedupe_key": "agent-version-warn",
                "cooldown_hint": "24h",
            },
        ),
        "host_label": "ops-node-1",
    }

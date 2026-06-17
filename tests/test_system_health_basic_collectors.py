from src.system_health_agent_interface import CollectorState, HealthAgentInterfaceError
from src.system_health_basic_collectors import BasicCollectorReading, build_basic_health_snapshot


def test_basic_collector_reading_normalizes_and_builds_snapshot():
    snapshot = build_basic_health_snapshot(
        (
            BasicCollectorReading.create(
                collector_id=" memory ",
                value=" 68 ",
                unit="percent",
                state="warn",
                summary="memory pressure is rising",
            ),
            BasicCollectorReading.create(
                collector_id="cpu",
                value="22",
                unit="percent",
                state="ok",
                summary="cpu usage is healthy",
            ),
        ),
        generated_at="2026-06-17T18:00:00Z",
        host_label="debian-node-1",
    )

    assert tuple(item.collector_id for item in snapshot.collectors) == ("cpu", "memory")
    assert snapshot.overall_status == CollectorState.WARN
    assert snapshot.host_label == "debian-node-1"


def test_unknown_or_unsupported_states_do_not_crash_snapshot_builder():
    snapshot = build_basic_health_snapshot(
        (
            BasicCollectorReading.create(
                collector_id="disk",
                value="n/a",
                unit="",
                state="unsupported",
                summary="disk collector is not configured on this host",
            ),
            BasicCollectorReading.create(
                collector_id="uptime",
                value="unknown",
                unit="",
                state="unknown",
                summary="uptime source is temporarily unavailable",
            ),
        ),
        generated_at="2026-06-17T18:05:00Z",
    )

    assert snapshot.overall_status in {CollectorState.UNKNOWN, CollectorState.UNSUPPORTED}
    assert all(item.setup_hint for item in snapshot.collectors)


def test_critical_states_influence_snapshot_conservatively():
    snapshot = build_basic_health_snapshot(
        (
            BasicCollectorReading.create(
                collector_id="load",
                value="8.5",
                unit="avg",
                state="critical",
                summary="load average exceeded host threshold",
            ),
            BasicCollectorReading.create(
                collector_id="memory",
                value="70",
                unit="percent",
                state="warn",
                summary="memory is elevated",
            ),
        ),
        generated_at="2026-06-17T18:10:00Z",
    )

    assert snapshot.overall_status == CollectorState.CRITICAL


def test_invalid_collector_id_is_rejected():
    try:
        BasicCollectorReading.create(
            collector_id="network",
            value="12",
            unit="percent",
            state="ok",
            summary="network is healthy",
        )
    except HealthAgentInterfaceError as exc:
        assert "collector_id" in str(exc)
    else:
        raise AssertionError("expected HealthAgentInterfaceError")


def test_to_dict_is_stable():
    reading = BasicCollectorReading.create(
        collector_id="disk",
        value="91",
        unit="percent",
        state="warn",
        summary="disk usage is elevated",
        setup_hint="schedule cleanup",
    )

    assert reading.to_dict() == {
        "collector_id": "disk",
        "value": "91",
        "unit": "percent",
        "state": "warn",
        "summary": "disk usage is elevated",
        "setup_hint": "schedule cleanup",
    }

    snapshot = build_basic_health_snapshot((reading,), generated_at="2026-06-17T18:15:00Z")
    assert snapshot.to_dict() == {
        "schema_version": "1.0",
        "generated_at": "2026-06-17T18:15:00Z",
        "overall_status": "warn",
        "collectors": (
            {
                "collector_id": "disk",
                "state": "warn",
                "summary": "disk usage is elevated",
                "observed_value": "91 percent",
                "setup_hint": "schedule cleanup",
            },
        ),
        "alerts": (),
        "host_label": "",
    }

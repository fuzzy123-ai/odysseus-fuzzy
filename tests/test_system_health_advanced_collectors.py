from src.system_health_agent_interface import CollectorState, HealthAgentInterfaceError
from src.system_health_advanced_collectors import (
    build_advanced_health_snapshot,
    build_updates_collector_status,
    parse_smartctl_payload,
    parse_temperature_sensor_payload,
)


def test_temperature_parser_marks_warn_and_critical_conservatively():
    warn_status = parse_temperature_sensor_payload(
        {
            "coretemp-isa-0000": {
                "Package id 0": {
                    "temp1_input": 79.0,
                }
            }
        }
    )
    critical_status = parse_temperature_sensor_payload(
        {
            "coretemp-isa-0000": {
                "Package id 0": {
                    "temp1_input": 88.4,
                }
            }
        }
    )

    assert warn_status.state == CollectorState.WARN
    assert critical_status.state == CollectorState.CRITICAL


def test_temperature_parser_returns_unknown_for_missing_payload():
    status = parse_temperature_sensor_payload({})

    assert status.state == CollectorState.UNKNOWN
    assert status.setup_hint


def test_smartctl_parser_marks_critical_on_failed_drive():
    status = parse_smartctl_payload(
        {
            "smart_status": {"passed": False},
            "temperature": {"current": 41},
        }
    )

    assert status.state == CollectorState.CRITICAL
    assert "failing drive" in status.summary.lower()


def test_smartctl_parser_returns_unknown_for_incomplete_payload():
    status = parse_smartctl_payload({"temperature": {"current": 37}})

    assert status.state == CollectorState.UNKNOWN
    assert status.setup_hint


def test_updates_status_reflects_pending_updates_and_reboot_requirement():
    pending = build_updates_collector_status(5)
    reboot = build_updates_collector_status(0, reboot_required=True)
    ok = build_updates_collector_status(0)

    assert pending.state == CollectorState.WARN
    assert reboot.state == CollectorState.WARN
    assert ok.state == CollectorState.OK
    assert "reboot required" in reboot.observed_value


def test_advanced_snapshot_builds_and_derives_overall_status():
    snapshot = build_advanced_health_snapshot(
        (
            parse_temperature_sensor_payload(
                {
                    "coretemp-isa-0000": {
                        "Package id 0": {
                            "temp1_input": 88.4,
                        }
                    }
                }
            ),
            build_updates_collector_status(2),
        ),
        generated_at="2026-06-17T21:00:00Z",
        host_label="debian-advanced-node",
    )

    assert snapshot.overall_status == CollectorState.CRITICAL
    assert snapshot.host_label == "debian-advanced-node"


def test_invalid_updates_input_and_empty_snapshot_are_rejected_conservatively():
    status = build_updates_collector_status("bad-input")
    assert status.state == CollectorState.UNKNOWN

    try:
        build_advanced_health_snapshot((), generated_at="2026-06-17T21:05:00Z")
    except HealthAgentInterfaceError as exc:
        assert "collectors" in str(exc)
    else:
        raise AssertionError("expected HealthAgentInterfaceError")

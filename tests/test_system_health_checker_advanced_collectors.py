import pytest

from plugins.system_health_checker.advanced_collectors import (
    build_missing_advanced_collector,
    build_reboot_required_status,
    build_smart_status,
    build_temperature_status,
    build_updates_status,
)
from plugins.system_health_checker.health_model import CollectorKind, HealthModelError, HealthState


OBSERVED_AT = "2026-06-16T12:00:00Z"


def test_temperature_thresholds():
    ok = build_temperature_status(max_celsius=45, observed_at=OBSERVED_AT)
    warn = build_temperature_status(max_celsius=78, observed_at=OBSERVED_AT)
    critical = build_temperature_status(max_celsius=90, observed_at=OBSERVED_AT)

    assert ok.state == HealthState.OK
    assert warn.state == HealthState.WARN
    assert critical.state == HealthState.CRITICAL
    assert critical.details["max_celsius"] == 90.0


def test_smart_status_passed_and_failed():
    passed = build_smart_status(passed=True, device="/dev/nvme0", observed_at=OBSERVED_AT)
    failed = build_smart_status(passed=False, device="/dev/nvme0", observed_at=OBSERVED_AT)

    assert passed.kind == CollectorKind.SMART
    assert passed.state == HealthState.OK
    assert failed.state == HealthState.CRITICAL
    assert failed.summary == "SMART /dev/nvme0 critical"


def test_updates_status_prioritizes_security_updates():
    ok = build_updates_status(security_updates=0, regular_updates=0, observed_at=OBSERVED_AT)
    warn = build_updates_status(security_updates=0, regular_updates=5, observed_at=OBSERVED_AT)
    critical = build_updates_status(security_updates=1, regular_updates=5, observed_at=OBSERVED_AT)

    assert ok.state == HealthState.OK
    assert warn.state == HealthState.WARN
    assert critical.state == HealthState.CRITICAL


def test_reboot_required_status():
    required = build_reboot_required_status(reboot_required=True, observed_at=OBSERVED_AT)
    not_required = build_reboot_required_status(reboot_required=False, observed_at=OBSERVED_AT)

    assert required.kind == CollectorKind.REBOOT
    assert required.state == HealthState.WARN
    assert not_required.state == HealthState.OK


def test_missing_advanced_collector_has_setup_hint():
    status = build_missing_advanced_collector(
        kind="temperature",
        observed_at=OBSERVED_AT,
        package_name="lm-sensors",
    )

    assert status.state == HealthState.UNKNOWN
    assert status.details["setup_hint"] == "Install or configure lm-sensors on the Debian host-agent."


def test_temperature_must_not_be_negative():
    with pytest.raises(HealthModelError, match="max_celsius must not be negative"):
        build_temperature_status(max_celsius=-1, observed_at=OBSERVED_AT)


def test_update_counts_must_not_be_negative():
    with pytest.raises(HealthModelError, match="security_updates must not be negative"):
        build_updates_status(security_updates=-1, regular_updates=0, observed_at=OBSERVED_AT)


def test_smart_device_must_not_be_empty():
    with pytest.raises(HealthModelError, match="device must not be empty"):
        build_smart_status(passed=True, device="", observed_at=OBSERVED_AT)

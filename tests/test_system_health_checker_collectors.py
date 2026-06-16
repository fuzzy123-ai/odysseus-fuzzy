import pytest

from plugins.system_health_checker.basic_collectors import (
    build_cpu_status,
    build_disk_status,
    build_load_status,
    build_memory_status,
    build_unknown_collector,
    build_uptime_status,
)
from plugins.system_health_checker.health_model import CollectorKind, HealthModelError, HealthState


OBSERVED_AT = "2026-06-16T12:00:00Z"


def test_cpu_status_thresholds():
    ok = build_cpu_status(usage_percent=42, observed_at=OBSERVED_AT)
    warn = build_cpu_status(usage_percent=82, observed_at=OBSERVED_AT)
    critical = build_cpu_status(usage_percent=98, observed_at=OBSERVED_AT)

    assert ok.state == HealthState.OK
    assert warn.state == HealthState.WARN
    assert critical.state == HealthState.CRITICAL
    assert critical.details["usage_percent"] == 98.0


def test_memory_status_uses_available_percent_thresholds():
    ok = build_memory_status(available_percent=35, observed_at=OBSERVED_AT)
    warn = build_memory_status(available_percent=12, observed_at=OBSERVED_AT)
    critical = build_memory_status(available_percent=5, observed_at=OBSERVED_AT)

    assert ok.state == HealthState.OK
    assert warn.state == HealthState.WARN
    assert critical.state == HealthState.CRITICAL


def test_disk_status_keeps_mount_and_used_percent():
    status = build_disk_status(mount="/srv", used_percent=91, observed_at=OBSERVED_AT)

    assert status.kind == CollectorKind.DISK
    assert status.state == HealthState.WARN
    assert status.details == {"mount": "/srv", "used_percent": 91.0}


def test_load_status_uses_cpu_normalized_ratio():
    ok = build_load_status(load_1m=2, cpu_count=4, observed_at=OBSERVED_AT)
    critical = build_load_status(load_1m=9, cpu_count=4, observed_at=OBSERVED_AT)

    assert ok.state == HealthState.OK
    assert ok.details["load_ratio"] == 0.5
    assert critical.state == HealthState.CRITICAL


def test_uptime_status_is_ok_for_non_negative_seconds():
    status = build_uptime_status(uptime_seconds=3600, observed_at=OBSERVED_AT)

    assert status.kind == CollectorKind.UPTIME
    assert status.state == HealthState.OK
    assert status.details["uptime_seconds"] == 3600.0


def test_unknown_collector_returns_setup_hint():
    status = build_unknown_collector(
        kind="containers",
        observed_at=OBSERVED_AT,
        setup_hint="Install podman or docker on the host.",
    )

    assert status.kind == CollectorKind.CONTAINERS
    assert status.state == HealthState.UNKNOWN
    assert status.details["setup_hint"].startswith("Install podman")


def test_percent_must_not_exceed_100():
    with pytest.raises(HealthModelError, match="usage_percent must not exceed 100"):
        build_cpu_status(usage_percent=120, observed_at=OBSERVED_AT)


def test_load_cpu_count_must_be_positive():
    with pytest.raises(HealthModelError, match="cpu_count must be positive"):
        build_load_status(load_1m=1, cpu_count=0, observed_at=OBSERVED_AT)


def test_disk_mount_must_not_be_empty():
    with pytest.raises(HealthModelError, match="mount must not be empty"):
        build_disk_status(mount="", used_percent=50, observed_at=OBSERVED_AT)

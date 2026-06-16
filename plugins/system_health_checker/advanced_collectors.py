"""Advanced collector builders for System Health Checker.

These builders normalize outputs that a Debian host-agent may collect later.
They do not call sensors, smartctl, apt, or read host files.
"""

from __future__ import annotations

from typing import Any

from .health_model import CollectorKind, CollectorStatus, HealthModelError, HealthState


def build_temperature_status(*, max_celsius: Any, observed_at: Any) -> CollectorStatus:
    temperature = _non_negative_float(max_celsius, field_name="max_celsius")
    if temperature >= 85:
        state = HealthState.CRITICAL
    elif temperature >= 75:
        state = HealthState.WARN
    else:
        state = HealthState.OK
    return CollectorStatus.create(
        kind=CollectorKind.TEMPERATURE,
        state=state,
        summary=f"Max temperature {temperature:.1f}C",
        observed_at=observed_at,
        details={"max_celsius": temperature},
    )


def build_smart_status(*, passed: Any, observed_at: Any, device: Any = "unknown") -> CollectorStatus:
    healthy = bool(passed)
    device_text = _text(device, field_name="device")
    return CollectorStatus.create(
        kind=CollectorKind.SMART,
        state=HealthState.OK if healthy else HealthState.CRITICAL,
        summary=f"SMART {device_text} {'passed' if healthy else 'critical'}",
        observed_at=observed_at,
        details={"device": device_text, "passed": healthy},
    )


def build_updates_status(*, security_updates: Any, regular_updates: Any, observed_at: Any) -> CollectorStatus:
    security = _non_negative_int(security_updates, field_name="security_updates")
    regular = _non_negative_int(regular_updates, field_name="regular_updates")
    state = HealthState.CRITICAL if security > 0 else HealthState.WARN if regular > 0 else HealthState.OK
    return CollectorStatus.create(
        kind=CollectorKind.UPDATES,
        state=state,
        summary=f"Updates security={security} regular={regular}",
        observed_at=observed_at,
        details={"security_updates": security, "regular_updates": regular},
    )


def build_reboot_required_status(*, reboot_required: Any, observed_at: Any) -> CollectorStatus:
    required = bool(reboot_required)
    return CollectorStatus.create(
        kind=CollectorKind.REBOOT,
        state=HealthState.WARN if required else HealthState.OK,
        summary="Reboot required" if required else "No reboot required",
        observed_at=observed_at,
        details={"reboot_required": required},
    )


def build_missing_advanced_collector(*, kind: CollectorKind | str, observed_at: Any, package_name: Any) -> CollectorStatus:
    package = _text(package_name, field_name="package_name")
    return CollectorStatus.create(
        kind=kind,
        state=HealthState.UNKNOWN,
        summary="Advanced collector unavailable",
        observed_at=observed_at,
        details={"setup_hint": f"Install or configure {package} on the Debian host-agent."},
    )


def _non_negative_float(value: Any, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HealthModelError(f"{field_name} must be a number") from exc
    if number < 0:
        raise HealthModelError(f"{field_name} must not be negative")
    return number


def _non_negative_int(value: Any, *, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HealthModelError(f"{field_name} must be an integer") from exc
    if number < 0:
        raise HealthModelError(f"{field_name} must not be negative")
    return number


def _text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise HealthModelError(f"{field_name} must not be empty")
    return text

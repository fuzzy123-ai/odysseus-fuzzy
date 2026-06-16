"""Mockable basic collector builders for the System Health Checker plugin.

These functions normalize already-collected values into CollectorStatus objects.
They do not read /proc, call df, or execute host commands.
"""

from __future__ import annotations

from typing import Any

from .health_model import CollectorKind, CollectorStatus, HealthModelError, HealthState


def build_cpu_status(*, usage_percent: Any, observed_at: Any) -> CollectorStatus:
    usage = _percent(usage_percent, field_name="usage_percent")
    state = _threshold_state(usage, warn_at=80.0, critical_at=95.0)
    return CollectorStatus.create(
        kind=CollectorKind.CPU,
        state=state,
        summary=f"CPU usage {usage:.1f}%",
        observed_at=observed_at,
        details={"usage_percent": usage},
    )


def build_memory_status(*, available_percent: Any, observed_at: Any) -> CollectorStatus:
    available = _percent(available_percent, field_name="available_percent")
    if available < 10:
        state = HealthState.CRITICAL
    elif available < 15:
        state = HealthState.WARN
    else:
        state = HealthState.OK
    return CollectorStatus.create(
        kind=CollectorKind.MEMORY,
        state=state,
        summary=f"Memory available {available:.1f}%",
        observed_at=observed_at,
        details={"available_percent": available},
    )


def build_disk_status(*, mount: Any, used_percent: Any, observed_at: Any) -> CollectorStatus:
    used = _percent(used_percent, field_name="used_percent")
    state = _threshold_state(used, warn_at=85.0, critical_at=95.0)
    mount_text = _text(mount, field_name="mount")
    return CollectorStatus.create(
        kind=CollectorKind.DISK,
        state=state,
        summary=f"Disk {mount_text} used {used:.1f}%",
        observed_at=observed_at,
        details={"mount": mount_text, "used_percent": used},
    )


def build_load_status(*, load_1m: Any, cpu_count: Any, observed_at: Any) -> CollectorStatus:
    load = _non_negative_float(load_1m, field_name="load_1m")
    cpus = _positive_int(cpu_count, field_name="cpu_count")
    ratio = load / cpus
    state = _threshold_state(ratio, warn_at=1.0, critical_at=2.0)
    return CollectorStatus.create(
        kind=CollectorKind.LOAD,
        state=state,
        summary=f"Load ratio {ratio:.2f}",
        observed_at=observed_at,
        details={"load_1m": load, "cpu_count": cpus, "load_ratio": ratio},
    )


def build_uptime_status(*, uptime_seconds: Any, observed_at: Any) -> CollectorStatus:
    seconds = _non_negative_float(uptime_seconds, field_name="uptime_seconds")
    return CollectorStatus.create(
        kind=CollectorKind.UPTIME,
        state=HealthState.OK,
        summary=f"Uptime {int(seconds)} seconds",
        observed_at=observed_at,
        details={"uptime_seconds": seconds},
    )


def build_unknown_collector(*, kind: CollectorKind | str, observed_at: Any, setup_hint: Any) -> CollectorStatus:
    return CollectorStatus.create(
        kind=kind,
        state=HealthState.UNKNOWN,
        summary="Collector unavailable",
        observed_at=observed_at,
        details={"setup_hint": _text(setup_hint, field_name="setup_hint")},
    )


def _threshold_state(value: float, *, warn_at: float, critical_at: float) -> HealthState:
    if value >= critical_at:
        return HealthState.CRITICAL
    if value >= warn_at:
        return HealthState.WARN
    return HealthState.OK


def _percent(value: Any, *, field_name: str) -> float:
    percent = _non_negative_float(value, field_name=field_name)
    if percent > 100:
        raise HealthModelError(f"{field_name} must not exceed 100")
    return percent


def _non_negative_float(value: Any, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HealthModelError(f"{field_name} must be a number") from exc
    if number < 0:
        raise HealthModelError(f"{field_name} must not be negative")
    return number


def _positive_int(value: Any, *, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HealthModelError(f"{field_name} must be an integer") from exc
    if number <= 0:
        raise HealthModelError(f"{field_name} must be positive")
    return number


def _text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise HealthModelError(f"{field_name} must not be empty")
    return text

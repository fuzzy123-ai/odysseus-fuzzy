"""Advanced Debian collector parsers for host-provided health snapshots."""

from __future__ import annotations

from typing import Any, Iterable

from src.system_health_agent_interface import CollectorState, CollectorStatus, HealthAgentInterfaceError, HealthSnapshot


_TEMP_WARN_C = 75.0
_TEMP_CRITICAL_C = 85.0


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise HealthAgentInterfaceError(f"{field_name} must not be empty")
    return text


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unknown_collector(*, collector_id: str, summary: str, setup_hint: str) -> CollectorStatus:
    return CollectorStatus.create(
        collector_id=collector_id,
        state=CollectorState.UNKNOWN,
        summary=summary,
        setup_hint=setup_hint,
    )


def parse_temperature_sensor_payload(payload: Any) -> CollectorStatus:
    if not isinstance(payload, dict) or not payload:
        return _unknown_collector(
            collector_id="temperature",
            summary="temperature payload is missing or invalid",
            setup_hint="provide parsed sensors -j fixture data for temperature checks",
        )

    highest: float | None = None
    sensor_label = ""
    for chip_name, chip_payload in payload.items():
        if not isinstance(chip_payload, dict):
            continue
        for entry_name, entry_payload in chip_payload.items():
            if not isinstance(entry_payload, dict):
                continue
            current = _safe_float(entry_payload.get("temp1_input"))
            if current is None:
                current = _safe_float(entry_payload.get("input"))
            if current is None:
                continue
            if highest is None or current > highest:
                highest = current
                sensor_label = f"{chip_name}/{entry_name}"

    if highest is None:
        return _unknown_collector(
            collector_id="temperature",
            summary="temperature payload did not contain a readable sensor value",
            setup_hint="check sensors -j parsing or host lm-sensors setup",
        )

    if highest >= _TEMP_CRITICAL_C:
        state = CollectorState.CRITICAL
        summary = f"temperature reached a critical threshold at {sensor_label or 'sensor'}"
    elif highest >= _TEMP_WARN_C:
        state = CollectorState.WARN
        summary = f"temperature is elevated at {sensor_label or 'sensor'}"
    else:
        state = CollectorState.OK
        summary = f"temperature is within range at {sensor_label or 'sensor'}"

    return CollectorStatus.create(
        collector_id="temperature",
        state=state,
        summary=summary,
        observed_value=f"{highest:.1f} C",
    )


def parse_smartctl_payload(payload: Any) -> CollectorStatus:
    if not isinstance(payload, dict) or not payload:
        return _unknown_collector(
            collector_id="smartctl",
            summary="smartctl payload is missing or invalid",
            setup_hint="provide parsed smartctl -a -j fixture data for disk health checks",
        )

    smart_status = payload.get("smart_status")
    passed = smart_status.get("passed") if isinstance(smart_status, dict) else None
    temperature = None
    temperature_block = payload.get("temperature")
    if isinstance(temperature_block, dict):
        temperature = _safe_float(temperature_block.get("current"))

    if passed is False:
        return CollectorStatus.create(
            collector_id="smartctl",
            state=CollectorState.CRITICAL,
            summary="SMART health check reported a failing drive",
            observed_value="" if temperature is None else f"{temperature:.1f} C",
            setup_hint="replace or inspect the failing disk before continued use",
        )
    if passed is True and temperature is not None and temperature >= _TEMP_CRITICAL_C:
        return CollectorStatus.create(
            collector_id="smartctl",
            state=CollectorState.WARN,
            summary="SMART health passed, but disk temperature is elevated",
            observed_value=f"{temperature:.1f} C",
            setup_hint="review disk cooling and airflow on the host",
        )
    if passed is True:
        return CollectorStatus.create(
            collector_id="smartctl",
            state=CollectorState.OK,
            summary="SMART health check passed",
            observed_value="" if temperature is None else f"{temperature:.1f} C",
        )

    return CollectorStatus.create(
        collector_id="smartctl",
        state=CollectorState.UNKNOWN,
        summary="SMART health payload is incomplete",
        observed_value="" if temperature is None else f"{temperature:.1f} C",
        setup_hint="verify smartctl JSON output includes smart_status.passed",
    )


def build_updates_collector_status(update_count: Any, *, reboot_required: bool = False) -> CollectorStatus:
    count = None
    try:
        count = int(update_count)
    except (TypeError, ValueError):
        pass
    if count is None or count < 0:
        return _unknown_collector(
            collector_id="updates",
            summary="update count is missing or invalid",
            setup_hint="provide a non-negative apt simulation/update count fixture",
        )

    if reboot_required:
        state = CollectorState.WARN
        summary = "system updates indicate a reboot is required"
    elif count > 0:
        state = CollectorState.WARN
        summary = "system updates are pending"
    else:
        state = CollectorState.OK
        summary = "system updates are up to date"

    observed = f"{count} pending update" if count == 1 else f"{count} pending updates"
    if reboot_required:
        observed = f"{observed}; reboot required"
    return CollectorStatus.create(
        collector_id="updates",
        state=state,
        summary=summary,
        observed_value=observed,
    )


def build_advanced_health_snapshot(
    collectors: Iterable[CollectorStatus],
    *,
    generated_at: Any,
    host_label: Any = "",
    schema_version: Any = "1.0",
) -> HealthSnapshot:
    normalized_collectors = tuple(collectors)
    if not normalized_collectors:
        raise HealthAgentInterfaceError("collectors must not be empty")
    if any(not isinstance(item, CollectorStatus) for item in normalized_collectors):
        raise HealthAgentInterfaceError("collectors must contain CollectorStatus items")
    return HealthSnapshot.create(
        schema_version=schema_version,
        generated_at=_normalize_text(generated_at, field_name="generated_at"),
        collectors=normalized_collectors,
        host_label=_normalize_text(host_label, field_name="host_label", allow_empty=True),
    )

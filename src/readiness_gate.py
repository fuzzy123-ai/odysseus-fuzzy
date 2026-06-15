"""Shared helpers for compact readiness gate payloads."""

from __future__ import annotations

from typing import Any


def sanitize_readiness_signal(signal: dict[str, Any]) -> dict[str, Any]:
    gaps = signal.get("gaps") if isinstance(signal.get("gaps"), list) else []
    return {
        "family": str(signal.get("family") or "generic"),
        "source": str(signal.get("source") or "unknown"),
        "state": str(signal.get("state") or "unknown"),
        "ready": signal_ready(signal),
        "gaps": [str(item) for item in gaps[:10]],
        "gap_count": int(signal.get("gap_count") or len(gaps)),
    }


def signal_ready(signal: dict[str, Any]) -> bool:
    value = signal.get("ready")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "ready"}:
            return True
        if normalized in {"false", "0", "no", "not_ready", "blocked"}:
            return False
    state = str(signal.get("state") or "").lower()
    return state == "ready" and int(signal.get("gap_count") or 0) == 0


def readiness_by_family(signals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        sanitized = sanitize_readiness_signal(signal)
        grouped[sanitized["family"]] = sanitized
    return dict(sorted(grouped.items()))


def readiness_gate(signals: list[dict[str, Any]]) -> dict[str, Any]:
    sanitized = [
        sanitize_readiness_signal(signal)
        for signal in signals
        if isinstance(signal, dict)
    ]
    by_family = {
        signal["family"]: signal
        for signal in sanitized
    }
    family_signals = list(by_family.values())
    blocked = [signal for signal in family_signals if not signal.get("ready", False)]
    gaps: list[str] = []
    seen = set()
    for signal in blocked:
        names = signal.get("gaps") if isinstance(signal.get("gaps"), list) else []
        candidates = names or ([signal.get("family")] if signal.get("gap_count") else [])
        for candidate in candidates:
            name = str(candidate or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            gaps.append(name)
    return {
        "required": bool(sanitized),
        "satisfied": not blocked,
        "state": "ready" if sanitized and not blocked else ("blocked" if blocked else "not_applicable"),
        "families": len(family_signals),
        "ready_families": len(family_signals) - len(blocked),
        "blocked_families": sorted({signal["family"] for signal in blocked}),
        "gaps": gaps[:25],
    }

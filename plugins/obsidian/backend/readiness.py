from typing import Any, Dict, List


def readiness_gate_from_signals(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_family = {
        str(signal.get("family") or "generic"): signal
        for signal in signals
        if isinstance(signal, dict)
    }
    gaps = _gap_names(list(by_family.values()))
    return readiness_gate_from_family(by_family, gaps)


def readiness_gate_from_family(by_family: Dict[str, Dict[str, Any]], gaps: List[str]) -> Dict[str, Any]:
    blocked = sorted(
        family
        for family, signal in by_family.items()
        if isinstance(signal, dict) and not signal.get("ready", False)
    )
    required = bool(by_family)
    return {
        "required": required,
        "satisfied": not blocked,
        "state": "ready" if required and not blocked else ("blocked" if required else "not_applicable"),
        "families": len(by_family),
        "ready_families": len(by_family) - len(blocked),
        "blocked_families": blocked,
        "gaps": gaps,
    }


def _gap_names(signals: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen = set()
    for signal in signals:
        for gap in signal.get("gaps") or []:
            name = str(gap or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
    return names[:25]

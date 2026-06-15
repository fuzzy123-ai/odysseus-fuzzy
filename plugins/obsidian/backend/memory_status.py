from typing import Any, Dict, List

from .freshness import audit_knowledge, quarantine_list
from .hybrid_retrieval import raptor_status
from .memory_tree import memory_tree_status
from .readiness import readiness_gate_from_family


def memory_status(vault_dir: str) -> Dict[str, Any]:
    """Return a compact read-only status across all derived memory layers."""

    somt = memory_tree_status(vault_dir)
    freshness = audit_knowledge(vault_dir)
    quarantine = quarantine_list(vault_dir)
    raptor = raptor_status(vault_dir)
    readiness_signals = _dedupe_signals(
        _payload_signals(somt, "somt")
        + _payload_signals(freshness, "freshness")
        + _payload_signals(raptor, "raptor")
    )
    readiness_by_family = {
        signal["family"]: signal
        for signal in readiness_signals
    }
    blocked = [
        family
        for family, signal in readiness_by_family.items()
        if not signal.get("ready", False)
    ]
    readiness_gap_names = _readiness_gap_names(readiness_signals)
    readiness_gate = readiness_gate_from_family(readiness_by_family, readiness_gap_names)
    families = {
        "somt": _family_status(somt),
        "freshness": _family_status(freshness),
        "quarantine": _family_status(quarantine),
        "raptor": _family_status(raptor),
    }
    flags = {
        **_mapping(somt.get("flags")),
        **_mapping(freshness.get("flags")),
        **_mapping(quarantine.get("flags")),
        **_mapping(raptor.get("flags")),
    }
    filtering_state = (
        freshness.get("filtering_state")
        or _mapping(freshness.get("summary")).get("filtering_state")
        or quarantine.get("filtering_state")
        or _mapping(quarantine.get("summary")).get("filtering_state")
        or "disabled"
    )
    raptor_lineage_flags = _mapping(
        raptor.get("lineage_flags")
        or _mapping(raptor.get("summary")).get("lineage_flags")
    )
    raptor_write_gate = _mapping(
        raptor.get("write_gate")
        or _mapping(raptor.get("summary")).get("write_gate")
    )
    freshness_isolation_flags = _mapping(
        freshness.get("isolation_flags")
        or _mapping(freshness.get("summary")).get("isolation_flags")
        or quarantine.get("isolation_flags")
        or _mapping(quarantine.get("summary")).get("isolation_flags")
    )
    retrieval_policy = {
        "filtering_state": filtering_state,
        "default_retrieval_is_filtered": filtering_state == "active",
        "isolated_knowledge_retained_in_audit": True,
    }
    warnings = _warnings(somt, freshness, quarantine, raptor)
    return {
        "read_only": True,
        "writes_supported": False,
        "filtering_state": filtering_state,
        "families": families,
        "readiness_signals": readiness_signals,
        "readiness_by_family": dict(sorted(readiness_by_family.items())),
        "readiness_gate": readiness_gate,
        "retrieval_policy": retrieval_policy,
        "freshness_isolation_flags": freshness_isolation_flags,
        "raptor_lineage_flags": raptor_lineage_flags,
        "raptor_write_gate": raptor_write_gate,
        "summary": {
            "families": len(readiness_by_family),
            "status_families": len(families),
            "readiness_families": len(readiness_by_family),
            "ready_families": len(readiness_by_family) - len(blocked),
            "blocked_families": sorted(blocked),
            "readiness_state": "ready" if not blocked else "blocked",
            "readiness_gaps": sum(int(signal.get("gap_count") or 0) for signal in readiness_signals),
            "readiness_gap_names": readiness_gap_names,
            "readiness_gate": readiness_gate,
            "filtering_state": filtering_state,
            "retrieval_policy": retrieval_policy,
            "somt_notes": somt.get("summary", {}).get("total_notes", 0),
            "default_retrieval": freshness.get("summary", {}).get("default_retrieval", 0),
            "isolated": freshness.get("summary", {}).get("isolated", 0),
            "quarantine_items": quarantine.get("summary", {}).get("total", 0),
            "freshness_isolation_flags": freshness_isolation_flags,
            "raptor_sources": raptor.get("summary", {}).get("source_count", 0),
            "raptor_lineage_flags": raptor_lineage_flags,
            "raptor_write_gate": raptor_write_gate,
            "writes_supported": False,
            "warnings": warnings,
        },
        "flags": flags,
        "warnings": warnings,
    }


def _mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _family_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
    return {
        "enabled": payload.get("enabled"),
        "readiness": readiness,
        "summary": summary,
        "writes_supported": bool(summary.get("writes_supported") or payload.get("writes_supported", False)),
    }


def _payload_signals(payload: Dict[str, Any], family: str) -> List[Dict[str, Any]]:
    signals = payload.get("readiness_signals")
    if isinstance(signals, list):
        return [
            _sanitize_signal(signal, family)
            for signal in signals
            if isinstance(signal, dict)
        ]
    readiness = payload.get("readiness")
    if isinstance(readiness, dict):
        return [_signal_from_readiness(family, readiness)]
    return []


def _signal_from_readiness(family: str, readiness: Dict[str, Any]) -> Dict[str, Any]:
    gaps = [str(gap) for gap in readiness.get("gaps") or []]
    state = str(readiness.get("state") or "unknown")
    return {
        "family": family,
        "source": "readiness",
        "state": state,
        "ready": _ready_value(readiness.get("ready", state == "ready"), state),
        "gaps": gaps,
        "gap_count": len(gaps),
    }


def _sanitize_signal(signal: Dict[str, Any], default_family: str) -> Dict[str, Any]:
    gaps = [str(gap) for gap in signal.get("gaps") or []]
    state = str(signal.get("state") or "unknown")
    return {
        "family": str(signal.get("family") or default_family),
        "source": str(signal.get("source") or "readiness"),
        "state": state,
        "ready": _ready_value(signal.get("ready", state == "ready"), state),
        "gaps": gaps,
        "gap_count": int(signal.get("gap_count") or len(gaps)),
    }


def _ready_value(value: Any, state: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return state == "ready"
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "ready"}
    return bool(value)


def _dedupe_signals(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped = []
    seen = set()
    for signal in signals:
        key = (signal["family"], signal["source"], signal["state"], signal["gap_count"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
    return deduped


def _readiness_gap_names(signals: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen = set()
    for signal in signals:
        gaps = signal.get("gaps") if isinstance(signal.get("gaps"), list) else []
        candidates = gaps or ([] if signal.get("ready") else [signal.get("family")])
        for candidate in candidates:
            name = str(candidate or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
    return names[:25]


def _warnings(*payloads: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    seen = set()
    for payload in payloads:
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        for container in (payload, summary):
            for warning in container.get("warnings") or []:
                text = str(warning).strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                warnings.append(text)
                if len(warnings) >= 25:
                    return warnings
    return warnings[:25]

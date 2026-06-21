from typing import Any, Dict, List

from .derived_index import derived_index_status
from .freshness import audit_knowledge, quarantine_list
from .hybrid_retrieval import raptor_status
from .memory_ledger import memory_ledger_status
from .query_layer import query_layer_status
from .memory_tree import memory_tree_status
from .raptor_cache import bounded_raptor_graph_view
from .readiness import readiness_gate_from_family


BASELINE_SCHEMA_VERSION = "orca-memory-baseline-v1"


def memory_status(vault_dir: str) -> Dict[str, Any]:
    """Return a compact read-only status across all derived memory layers."""

    somt = memory_tree_status(vault_dir)
    freshness = audit_knowledge(vault_dir)
    quarantine = quarantine_list(vault_dir)
    raptor = raptor_status(vault_dir)
    ledger = memory_ledger_status(vault_dir)
    derived_index = derived_index_status(vault_dir)
    query_layer = query_layer_status(vault_dir)
    readiness_signals = _dedupe_signals(
        _payload_signals(ledger, "ledger")
        + _payload_signals(derived_index, "derived_index")
        + _payload_signals(query_layer, "query_layer")
        + _payload_signals(somt, "somt")
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
        "ledger": _family_status(ledger),
        "derived_index": _family_status(derived_index),
        "query_layer": _family_status(query_layer),
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
        "excluded_relevant_count": 0,
    }
    warnings = _warnings(ledger, derived_index, query_layer, somt, freshness, quarantine, raptor)
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
            "ledger_sources": ledger.get("summary", {}).get("total_sources", 0),
            "ledger_status_counts": ledger.get("summary", {}).get("status_counts", {}),
            "ledger_source_types": ledger.get("summary", {}).get("source_types", {}),
            "derived_index_sources": derived_index.get("summary", {}).get("source_count", 0),
            "derived_index_chunks": derived_index.get("summary", {}).get("chunk_count", 0),
            "derived_index_graph_nodes": derived_index.get("summary", {}).get("graph_nodes", 0),
            "derived_index_graph_edges": derived_index.get("summary", {}).get("graph_edges", 0),
            "query_layer_sources": query_layer.get("summary", {}).get("source_count", 0),
            "query_layer_chunks": query_layer.get("summary", {}).get("chunk_count", 0),
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


def memory_baseline_report(vault_dir: str) -> Dict[str, Any]:
    """Return the roadmap baseline as a read-only, summary-only evidence packet."""

    status = memory_status(vault_dir)
    status_summary = _mapping(status.get("summary"))
    flags = _mapping(status.get("flags"))
    raptor = raptor_status(vault_dir)
    graph = bounded_raptor_graph_view(vault_dir, limit=500)
    quarantine = quarantine_list(vault_dir)
    quarantine_summary = _mapping(quarantine.get("summary"))
    derived_graph = {
        "node_count": int(status_summary.get("derived_index_graph_nodes") or graph.get("node_count") or 0),
        "edge_count": int(status_summary.get("derived_index_graph_edges") or graph.get("edge_count") or 0),
        "raptor_node_count": int(graph.get("node_count") or 0),
        "raptor_edge_count": int(graph.get("edge_count") or 0),
        "raptor_returned_edge_count": int(graph.get("returned_edge_count") or 0),
        "bounded": True,
        "clipped": bool(graph.get("clipped", False)),
    }
    filtering_state = str(status.get("filtering_state") or status_summary.get("filtering_state") or "disabled")
    systems = {
        "memory_tree_ui": {
            "enabled": bool(flags.get("obsidian_memory_tree_ui_enabled", False)),
            "state": "enabled" if flags.get("obsidian_memory_tree_ui_enabled", False) else "disabled",
        },
        "hybrid_retrieval": {
            "enabled": bool(flags.get("obsidian_hybrid_retrieval_enabled", False)),
            "state": "enabled" if flags.get("obsidian_hybrid_retrieval_enabled", False) else "disabled",
        },
        "freshness_gate": {
            "enabled": bool(flags.get("obsidian_freshness_gate_enabled", False)),
            "filtering_state": filtering_state,
            "default_retrieval_is_filtered": filtering_state == "active",
            "isolation_flags": _mapping(status.get("freshness_isolation_flags")),
        },
        "quarantine": {
            "enabled": bool(quarantine.get("enabled", flags.get("obsidian_freshness_gate_enabled", False))),
            "items": int(quarantine_summary.get("total") or 0),
            "isolated": int(quarantine_summary.get("isolated") or 0),
            "by_channel": _mapping(quarantine_summary.get("by_channel")),
        },
        "derived_graph": derived_graph,
        "raptor": {
            "enabled": bool(raptor.get("enabled", False)),
            "configured": bool(raptor.get("configured", False)),
            "index_present": bool(raptor.get("index_present", False)),
            "summaries_present": bool(raptor.get("summaries_present", False)),
            "readiness": _mapping(raptor.get("readiness")),
            "write_gate": _mapping(raptor.get("write_gate")),
            "lineage_flags": _mapping(raptor.get("lineage_flags")),
        },
    }
    recommendations = _activation_recommendations(systems, status)
    return {
        "schema": BASELINE_SCHEMA_VERSION,
        "read_only": True,
        "writes_supported": False,
        "routes": {
            "preferred": "/api/plugins/orca/memory/baseline",
            "legacy": "/api/plugins/obsidian/memory/baseline",
        },
        "flags": flags,
        "systems": systems,
        "readiness_gate": _mapping(status.get("readiness_gate")),
        "readiness_by_family": _mapping(status.get("readiness_by_family")),
        "summary": {
            "readiness_state": status_summary.get("readiness_state", "unknown"),
            "ready_families": int(status_summary.get("ready_families") or 0),
            "readiness_families": int(status_summary.get("readiness_families") or 0),
            "blocked_families": list(status_summary.get("blocked_families") or []),
            "readiness_gap_names": list(status_summary.get("readiness_gap_names") or []),
            "filtering_state": filtering_state,
            "quarantine_items": systems["quarantine"]["items"],
            "derived_graph_nodes": derived_graph["node_count"],
            "derived_graph_edges": derived_graph["edge_count"],
            "raptor_configured": systems["raptor"]["configured"],
            "raptor_write_gate_state": systems["raptor"]["write_gate"].get("state", "unknown"),
            "warnings": list(status_summary.get("warnings") or [])[:25],
        },
        "activation_recommendations": recommendations,
        "evidence_contract": {
            "raw_note_bodies_included": False,
            "absolute_host_paths_included": False,
            "provider_outputs_included": False,
            "bounded_graph_payload": True,
            "requires_operator_go_before_writes": True,
        },
    }


def _activation_recommendations(systems: Dict[str, Any], status: Dict[str, Any]) -> List[Dict[str, str]]:
    summary = _mapping(status.get("summary"))
    gaps = set(str(gap) for gap in summary.get("readiness_gap_names") or [])
    graph_edges = int(_mapping(systems.get("derived_graph")).get("edge_count") or 0)
    recommendations = [
        {
            "node_id": "memory-tree-ui-live",
            "decision": "go" if status.get("read_only") is True else "no_go",
            "reason": "Read-only memory diagnostics are available; enabling the UI flag does not mutate the vault.",
        },
        {
            "node_id": "canonical-vault-foundation",
            "decision": "operator_go_required",
            "reason": "Creating or linking canonical notes mutates the vault and must stay explicitly approved.",
        },
        {
            "node_id": "derived-graph-edges-live",
            "decision": "go" if graph_edges > 0 else "needs_source_links",
            "reason": "Derived Graph needs explicit source relationships before edge readiness can turn green.",
        },
        {
            "node_id": "freshness-hybrid-filtering-live",
            "decision": "wait_for_dependencies" if graph_edges <= 0 else "operator_go_required",
            "reason": "Freshness filtering changes default retrieval behavior and should follow graph provenance evidence.",
        },
        {
            "node_id": "raptor-rebuild-live",
            "decision": "operator_go_required",
            "reason": "RAPTOR rebuild is a write-capable derived-memory action and remains gated.",
        },
    ]
    if "raptor_index_missing" in gaps:
        recommendations.append({
            "node_id": "raptor-read-policy-live",
            "decision": "blocked",
            "reason": "RAPTOR read policy cannot be marked ready before derived RAPTOR artifacts exist.",
        })
    return recommendations


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

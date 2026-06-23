"""ORCA core adapter contracts for the legacy Obsidian backend.

The plugin still keeps legacy Obsidian routes and tool names for compatibility,
but ORCA callers should get a small, stable backend contract that speaks in
ORCA/Lens terms. These helpers are read-only: they summarize existing memory,
retrieval, query, RAPTOR, and Lens payloads without mutating vault state.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from .hybrid_retrieval import raptor_status
from .memory_status import memory_baseline_report, memory_status
from .query_layer import query_layer_status
from .raptor_cache import bounded_raptor_graph_view


ORCA_CORE_SCHEMA = "odysseus.orca.core_adapter.v1"
ORCA_PROVIDER_ID = "orca.vault_context"
LEGACY_PROVIDER_ID = "obsidian.vault_context"
ORCA_API_PREFIX = "/api/plugins/orca"
LEGACY_API_PREFIX = "/api/plugins/obsidian"


def decorate_orca_context_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Annotate a legacy context payload with the ORCA core contract."""

    decorated = deepcopy(dict(payload or {}))
    memory = _mapping(decorated.get("memory"))
    decorated["provider"] = {
        "id": ORCA_PROVIDER_ID,
        "label": "ORCA Vault Context",
        "legacy_adapter": LEGACY_PROVIDER_ID,
    }
    decorated["orca_core"] = {
        "schema": ORCA_CORE_SCHEMA,
        "namespace": "orca",
        "read_only": True,
        "writes_supported": False,
        "adapter": "legacy_obsidian_backend",
        "preferred_routes": {
            "memory_status": f"{ORCA_API_PREFIX}/memory/status",
            "memory_baseline": f"{ORCA_API_PREFIX}/memory/baseline",
            "query": f"{ORCA_API_PREFIX}/memory/query",
            "raptor_status": f"{ORCA_API_PREFIX}/raptor/status",
            "raptor_graph": f"{ORCA_API_PREFIX}/raptor/graph",
        },
        "legacy_routes": {
            "memory_status": f"{LEGACY_API_PREFIX}/memory/status",
            "memory_baseline": f"{LEGACY_API_PREFIX}/memory/baseline",
            "query": f"{LEGACY_API_PREFIX}/memory/query",
            "raptor_status": f"{LEGACY_API_PREFIX}/raptor/status",
            "raptor_graph": f"{LEGACY_API_PREFIX}/raptor/graph",
        },
        "contracts": {
            "retrieval_policy": _mapping(memory.get("retrieval_policy")),
            "readiness_gate": _mapping(memory.get("readiness_gate")),
            "readiness_by_family": _mapping(memory.get("readiness_by_family")),
            "raptor_lineage_flags": _mapping(memory.get("raptor_lineage_flags")),
            "raptor_write_gate": _mapping(memory.get("raptor_write_gate")),
        },
    }
    if memory:
        memory["namespace"] = "orca"
        memory["legacy_adapter"] = {
            "provider_id": LEGACY_PROVIDER_ID,
            "delete_legacy": False,
        }
        decorated["memory"] = memory
    return decorated


def build_orca_memory_readiness_contract(
    vault_dir: str,
    *,
    status_loader: Callable[[str], Mapping[str, Any]] = memory_status,
) -> dict[str, Any]:
    """Return read-only ORCA readiness and retrieval-policy state."""

    status = dict(status_loader(vault_dir) or {})
    summary = _mapping(status.get("summary"))
    return {
        "schema": ORCA_CORE_SCHEMA,
        "contract": "orca_memory_readiness",
        "namespace": "orca",
        "read_only": True,
        "writes_supported": False,
        "legacy_adapter": {
            "module": "plugins.obsidian.backend.memory_status",
            "provider_id": LEGACY_PROVIDER_ID,
            "delete_legacy": False,
        },
        "readiness_gate": _mapping(status.get("readiness_gate") or summary.get("readiness_gate")),
        "readiness_by_family": _mapping(status.get("readiness_by_family")),
        "retrieval_policy": _mapping(status.get("retrieval_policy") or summary.get("retrieval_policy")),
        "families": _mapping(status.get("families")),
        "summary": {
            "readiness_state": str(summary.get("readiness_state") or "unknown"),
            "ready_families": int(summary.get("ready_families") or 0),
            "readiness_families": int(summary.get("readiness_families") or 0),
            "blocked_families": [str(item) for item in summary.get("blocked_families") or []],
            "readiness_gap_names": [str(item) for item in summary.get("readiness_gap_names") or []],
            "filtering_state": str(summary.get("filtering_state") or status.get("filtering_state") or "disabled"),
            "warnings": [str(item) for item in (status.get("warnings") or summary.get("warnings") or [])][:25],
        },
    }


def build_orca_raptor_contract(
    vault_dir: str,
    *,
    status_loader: Callable[[str], Mapping[str, Any]] = raptor_status,
    graph_loader: Callable[..., Mapping[str, Any]] = bounded_raptor_graph_view,
    graph_limit: int = 500,
) -> dict[str, Any]:
    """Return bounded ORCA RAPTOR status without rebuilding artifacts."""

    status = dict(status_loader(vault_dir) or {})
    graph = dict(graph_loader(vault_dir, limit=max(1, min(int(graph_limit or 500), 5000))) or {})
    summary = _mapping(status.get("summary"))
    return {
        "schema": ORCA_CORE_SCHEMA,
        "contract": "orca_raptor",
        "namespace": "orca",
        "read_only": True,
        "writes_supported": False,
        "legacy_adapter": {
            "module": "plugins.obsidian.backend.hybrid_retrieval",
            "delete_legacy": False,
        },
        "readiness": _mapping(status.get("readiness")),
        "readiness_gate": _mapping(status.get("readiness_gate") or summary.get("readiness_gate")),
        "lineage_flags": _mapping(status.get("lineage_flags") or summary.get("lineage_flags")),
        "write_gate": _mapping(status.get("write_gate") or summary.get("write_gate")),
        "graph": {
            "bounded": True,
            "node_count": int(graph.get("node_count") or 0),
            "edge_count": int(graph.get("edge_count") or 0),
            "returned_edge_count": int(graph.get("returned_edge_count") or 0),
            "clipped": bool(graph.get("clipped", False)),
            "cursor": _mapping(graph.get("cursor")),
        },
        "summary": {
            "enabled": bool(status.get("enabled", False)),
            "configured": bool(status.get("configured", False)),
            "readiness_state": str(summary.get("readiness_state") or _mapping(status.get("readiness")).get("state") or "unknown"),
            "readiness_gap_names": [str(item) for item in summary.get("readiness_gap_names") or []],
            "warnings": [str(item) for item in (status.get("warnings") or summary.get("warnings") or [])][:25],
        },
    }


def build_orca_query_contract(
    vault_dir: str,
    *,
    owner: str | None = None,
    status_loader: Callable[..., Mapping[str, Any]] = query_layer_status,
) -> dict[str, Any]:
    """Return ORCA query-layer status without executing an answer query."""

    status = dict(status_loader(vault_dir, owner=owner) or {})
    summary = _mapping(status.get("summary"))
    return {
        "schema": ORCA_CORE_SCHEMA,
        "contract": "orca_query_layer",
        "namespace": "orca",
        "read_only": True,
        "writes_supported": False,
        "legacy_adapter": {
            "module": "plugins.obsidian.backend.query_layer",
            "delete_legacy": False,
        },
        "answer_modes": ("auto", "extractive", "cloud", "local"),
        "readiness": _mapping(status.get("readiness")),
        "readiness_gate": _mapping(status.get("readiness_gate") or summary.get("readiness_gate")),
        "model_router": _mapping(status.get("model_router")),
        "cache": _mapping(status.get("cache")),
        "summary": {
            "source_count": int(summary.get("source_count") or 0),
            "chunk_count": int(summary.get("chunk_count") or 0),
            "readiness_state": str(summary.get("readiness_state") or "unknown"),
            "readiness_gap_names": [str(item) for item in summary.get("readiness_gap_names") or []],
            "warnings": [str(item) for item in (status.get("warnings") or summary.get("warnings") or [])][:25],
        },
    }


def build_orca_lens_contract(
    vault_dir: str,
    *,
    baseline_loader: Callable[[str], Mapping[str, Any]] = memory_baseline_report,
) -> dict[str, Any]:
    """Return the ORCA/Lens read-only contract backed by memory baseline data."""

    baseline = dict(baseline_loader(vault_dir) or {})
    systems = _mapping(baseline.get("systems"))
    derived_graph = _mapping(systems.get("derived_graph"))
    summary = _mapping(baseline.get("summary"))
    return {
        "schema": ORCA_CORE_SCHEMA,
        "contract": "orca_lens",
        "namespace": "orca",
        "read_only": True,
        "writes_supported": False,
        "preferred_routes": {
            "app": f"{ORCA_API_PREFIX}/app",
            "memory_baseline": f"{ORCA_API_PREFIX}/memory/baseline",
            "raptor_graph": f"{ORCA_API_PREFIX}/raptor/graph",
        },
        "legacy_routes": {
            "app": f"{LEGACY_API_PREFIX}/app",
            "memory_baseline": f"{LEGACY_API_PREFIX}/memory/baseline",
            "raptor_graph": f"{LEGACY_API_PREFIX}/raptor/graph",
        },
        "graph": {
            "bounded": True,
            "node_count": int(derived_graph.get("node_count") or 0),
            "edge_count": int(derived_graph.get("edge_count") or 0),
            "raptor_node_count": int(derived_graph.get("raptor_node_count") or 0),
            "raptor_edge_count": int(derived_graph.get("raptor_edge_count") or 0),
            "clipped": bool(derived_graph.get("clipped", False)),
        },
        "readiness_gate": _mapping(baseline.get("readiness_gate")),
        "activation_recommendations": list(baseline.get("activation_recommendations") or [])[:25],
        "evidence_contract": {
            **_mapping(baseline.get("evidence_contract")),
            "legacy_adapter_delete_required": False,
        },
        "summary": {
            "readiness_state": str(summary.get("readiness_state") or "unknown"),
            "blocked_families": [str(item) for item in summary.get("blocked_families") or []],
            "readiness_gap_names": [str(item) for item in summary.get("readiness_gap_names") or []],
            "warnings": [str(item) for item in summary.get("warnings") or []][:25],
        },
    }


def build_legacy_obsidian_deprecation_contract(
    *,
    ui_lens_redesign_live: bool = False,
    data_path_migration_go: bool = False,
    explicit_removal_go: bool = False,
) -> dict[str, Any]:
    """Return migration/removal gates for legacy Obsidian compatibility surfaces."""

    migration_map = {
        "provider": {
            LEGACY_PROVIDER_ID: ORCA_PROVIDER_ID,
        },
        "routes": {
            f"{LEGACY_API_PREFIX}/app": f"{ORCA_API_PREFIX}/app",
            f"{LEGACY_API_PREFIX}/memory/status": f"{ORCA_API_PREFIX}/memory/status",
            f"{LEGACY_API_PREFIX}/memory/baseline": f"{ORCA_API_PREFIX}/memory/baseline",
            f"{LEGACY_API_PREFIX}/memory/query": f"{ORCA_API_PREFIX}/memory/query",
            f"{LEGACY_API_PREFIX}/raptor/status": f"{ORCA_API_PREFIX}/raptor/status",
            f"{LEGACY_API_PREFIX}/raptor/graph": f"{ORCA_API_PREFIX}/raptor/graph",
        },
        "tools": {
            "obsidian_read_note": "orca_read_note",
            "obsidian_search_notes": "orca_search_notes",
            "obsidian_graph": "orca_graph",
            "obsidian_memory_status": "orca_memory_status",
            "obsidian_raptor_status": "orca_raptor_status",
        },
        "env": {
            "ODYSSEUS_OBSIDIAN_*": "ODYSSEUS_ORCA_*",
        },
    }
    gates = {
        "ui_lens_redesign_live": bool(ui_lens_redesign_live),
        "data_path_migration_go": bool(data_path_migration_go),
        "explicit_removal_go": bool(explicit_removal_go),
    }
    removal_allowed = all(gates.values())
    warnings = [
        "Legacy Obsidian compatibility surfaces remain available until ORCA/Lens UI wording is live.",
        "Data-path migration and final legacy removal require explicit operator Go plus rollback evidence.",
    ]
    return {
        "schema": ORCA_CORE_SCHEMA,
        "contract": "legacy_obsidian_deprecation",
        "namespace": "orca",
        "read_only": True,
        "writes_supported": False,
        "legacy_surfaces_retained": True,
        "removal_allowed": removal_allowed,
        "state": "removal_ready" if removal_allowed else "compatibility_retained",
        "migration_map": migration_map,
        "removal_gates": gates,
        "warnings": warnings,
        "next_safe_action": (
            "remove legacy surfaces under operator-approved migration plan"
            if removal_allowed
            else "keep legacy surfaces and prefer ORCA routes, tools, provider ids, and env names in new callers"
        ),
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}

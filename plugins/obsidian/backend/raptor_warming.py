"""Background warming helpers for RAPTOR dynamic cache."""

from __future__ import annotations

import os
from typing import Any, Dict

from .hybrid_retrieval import raptor_status
from .raptor_cache import bounded_raptor_graph_view, raptor_cache_diagnostics


def _warming_enabled() -> bool:
    raw = os.getenv("ODYSSEUS_OBSIDIAN_RAPTOR_CACHE_WARMING_ENABLED", "true")
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _graph_limit() -> int:
    return max(1, min(int(os.getenv("ODYSSEUS_OBSIDIAN_RAPTOR_CACHE_WARMING_GRAPH_LIMIT", "500") or 500), 5000))


def raptor_cache_warming_status(vault_dir: str) -> Dict[str, Any]:
    diagnostics = raptor_cache_diagnostics(vault_dir)
    return {
        "enabled": _warming_enabled(),
        "pending": _warming_enabled() and int(diagnostics.get("entry_count") or 0) == 0,
        "graph_limit": _graph_limit(),
        "cache": diagnostics,
        "safety": {
            "source_note_writes": False,
            "derived_data_writes_only": True,
            "provider_calls": False,
        },
    }


def warm_raptor_cache(vault_dir: str, *, include_graph: bool = True) -> Dict[str, Any]:
    if not _warming_enabled():
        return {"skipped": True, "reason": "raptor_cache_warming_disabled", "warmed": []}
    warmed = []
    status = raptor_status(vault_dir)
    warmed.append("raptor_status")
    graph = None
    if include_graph and bool(status.get("index_present")):
        graph = bounded_raptor_graph_view(vault_dir, edge_offset=0, limit=_graph_limit())
        warmed.append("raptor_graph_view")
    return {
        "skipped": False,
        "warmed": warmed,
        "status_cache_hit": bool((status.get("cache") or {}).get("hit", False)),
        "graph_cache_hit": bool(((graph or {}).get("cache") or {}).get("hit", False)),
        "cache": raptor_cache_diagnostics(vault_dir),
        "safety": {
            "source_note_writes": False,
            "derived_data_writes_only": True,
            "provider_calls": False,
        },
    }

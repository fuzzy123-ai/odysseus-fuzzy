"""In-process dynamic cache for derived RAPTOR metadata."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
import time
from typing import Any, Callable, Dict, Mapping

from . import vault_service
from .feature_flags import all_flags


CACHE_SCHEMA_VERSION = "raptor-dynamic-cache-v1"
RAPTOR_INDEX_PATH = ".obsidian/odysseus/raptor/index.json"
RAPTOR_SUMMARIES_PATH = ".obsidian/odysseus/raptor/summaries.json"
RAPTOR_REBUILD_REPORT_PATH = ".obsidian/odysseus/raptor/rebuild_report.json"
DEFAULT_TTL_SECONDS = 30.0
DEFAULT_MAX_ENTRIES = 64
MAX_GRAPH_VIEW_LIMIT = 5_000


@dataclass(slots=True)
class _CacheEntry:
    key: str
    vault_dir: str
    namespace: str
    created_at: float
    payload: Any


_CACHE: dict[str, _CacheEntry] = {}
_STATS = {
    "hits": 0,
    "misses": 0,
    "stale": 0,
    "evictions": 0,
}


def cached_raptor_payload(
    vault_dir: str,
    namespace: str,
    params: Mapping[str, Any] | None,
    loader: Callable[[], Any],
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> Any:
    """Return a cached derived RAPTOR payload, or compute and cache it."""

    key = build_raptor_cache_key(vault_dir, namespace, params or {})
    now = time.monotonic()
    entry = _CACHE.get(key)
    if entry is not None and now - entry.created_at <= max(0.0, float(ttl_seconds)):
        _STATS["hits"] += 1
        return _with_cache_diagnostics(entry.payload, hit=True, namespace=namespace, key=key)
    if entry is not None:
        _STATS["stale"] += 1
        _CACHE.pop(key, None)
    _STATS["misses"] += 1
    payload = loader()
    _CACHE[key] = _CacheEntry(
        key=key,
        vault_dir=os.path.abspath(vault_dir),
        namespace=str(namespace),
        created_at=now,
        payload=deepcopy(payload),
    )
    _evict_if_needed(max_entries=max_entries)
    return _with_cache_diagnostics(payload, hit=False, namespace=namespace, key=key)


def clear_raptor_cache(vault_dir: str | None = None) -> dict[str, int]:
    """Clear all RAPTOR cache entries, or only entries for one vault."""

    if vault_dir is None:
        cleared = len(_CACHE)
        _CACHE.clear()
        return {"cleared": cleared, "entry_count": 0}
    target = os.path.abspath(vault_dir)
    keys = [key for key, entry in _CACHE.items() if entry.vault_dir == target]
    for key in keys:
        _CACHE.pop(key, None)
    return {"cleared": len(keys), "entry_count": len(_CACHE)}


def raptor_cache_diagnostics() -> dict[str, int]:
    return {
        "hits": int(_STATS["hits"]),
        "misses": int(_STATS["misses"]),
        "stale": int(_STATS["stale"]),
        "evictions": int(_STATS["evictions"]),
        "entry_count": len(_CACHE),
    }


def build_raptor_cache_key(vault_dir: str, namespace: str, params: Mapping[str, Any] | None = None) -> str:
    raw = {
        "schema": CACHE_SCHEMA_VERSION,
        "namespace": str(namespace or "status"),
        "params": _stable_params(params or {}),
        "source_signature": source_signature(vault_dir),
        "artifact_signature": artifact_signature(vault_dir),
        "feature_flags": _feature_signature(),
    }
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def source_signature(vault_dir: str) -> tuple[tuple[str, int, int], ...]:
    entries = []
    for path in vault_service.markdown_notes(vault_dir):
        abs_path = vault_service.secure_path(vault_dir, path)
        try:
            stat = os.stat(abs_path)
        except OSError:
            continue
        entries.append((path.replace("\\", "/"), int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(sorted(entries, key=lambda item: item[0].lower()))


def artifact_signature(vault_dir: str) -> tuple[tuple[str, int, int], ...]:
    entries = []
    for rel_path in (RAPTOR_INDEX_PATH, RAPTOR_SUMMARIES_PATH, RAPTOR_REBUILD_REPORT_PATH):
        abs_path = vault_service.secure_path(vault_dir, rel_path)
        try:
            stat = os.stat(abs_path)
        except OSError:
            entries.append((rel_path, 0, 0))
            continue
        entries.append((rel_path, int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(entries)


def bounded_raptor_graph_view(
    vault_dir: str,
    *,
    edge_offset: int = 0,
    limit: int = 500,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    offset = max(0, int(edge_offset or 0))
    bounded_limit = max(1, min(int(limit or 500), MAX_GRAPH_VIEW_LIMIT))
    params = {"edge_offset": offset, "limit": bounded_limit}
    return cached_raptor_payload(
        vault_dir,
        "graph_view",
        params,
        lambda: _load_bounded_graph_view(vault_dir, edge_offset=offset, limit=bounded_limit),
        ttl_seconds=ttl_seconds,
    )


def _load_bounded_graph_view(vault_dir: str, *, edge_offset: int, limit: int) -> dict[str, Any]:
    index_path = vault_service.secure_path(vault_dir, RAPTOR_INDEX_PATH)
    try:
        with open(index_path, "r", encoding="utf-8") as handle:
            index_payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        index_payload = {}
    graph = index_payload.get("graph") if isinstance(index_payload.get("graph"), dict) else {}
    raw_edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    total_edges = int(graph.get("edge_count") or len(raw_edges) or 0)
    stored_edge_count = int(graph.get("stored_edge_count") or len(raw_edges) or 0)
    edges = [
        {
            "source": str(edge.get("source") or ""),
            "target": str(edge.get("target") or ""),
            "type": str(edge.get("type") or "derived"),
        }
        for edge in raw_edges[edge_offset : edge_offset + limit]
        if isinstance(edge, dict)
    ]
    next_offset = edge_offset + len(edges)
    has_more_stored = next_offset < len(raw_edges)
    return {
        "schema": CACHE_SCHEMA_VERSION,
        "artifact": "graph_view",
        "node_count": int(graph.get("node_count") or 0),
        "edge_count": total_edges,
        "stored_edge_count": stored_edge_count,
        "returned_edge_count": len(edges),
        "edge_offset": edge_offset,
        "limit": limit,
        "clipped": bool(graph.get("clipped")) or has_more_stored or total_edges > len(edges),
        "cursor": {"next_edge_offset": next_offset if has_more_stored else None},
        "edges": edges,
    }


def _with_cache_diagnostics(payload: Any, *, hit: bool, namespace: str, key: str) -> Any:
    cloned = deepcopy(payload)
    diagnostics = {
        "schema": CACHE_SCHEMA_VERSION,
        "namespace": str(namespace),
        "hit": bool(hit),
        "cache_key": key,
        **raptor_cache_diagnostics(),
    }
    if isinstance(cloned, dict):
        cloned["cache"] = diagnostics
        summary = cloned.get("summary")
        if isinstance(summary, dict):
            summary["cache"] = diagnostics
    return cloned


def _evict_if_needed(*, max_entries: int) -> None:
    max_entries = max(1, int(max_entries or DEFAULT_MAX_ENTRIES))
    while len(_CACHE) > max_entries:
        oldest_key = min(_CACHE, key=lambda key: _CACHE[key].created_at)
        _CACHE.pop(oldest_key, None)
        _STATS["evictions"] += 1


def _feature_signature() -> tuple[tuple[str, bool], ...]:
    flags = all_flags()
    keys = (
        "obsidian_raptor_enabled",
        "obsidian_raptor_rebuild_enabled",
        "obsidian_freshness_gate_enabled",
        "obsidian_hybrid_retrieval_enabled",
    )
    return tuple((key, bool(flags.get(key))) for key in keys)


def _stable_params(params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _safe_param_value(value)
        for key, value in sorted(params.items(), key=lambda item: str(item[0]))
    }


def _safe_param_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_param_value(item) for item in value[:25]]
    if isinstance(value, dict):
        return _stable_params(value)
    return str(value)

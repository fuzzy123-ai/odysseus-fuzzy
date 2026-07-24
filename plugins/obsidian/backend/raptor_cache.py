"""In-process dynamic cache for derived RAPTOR metadata."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
import threading
import time
from typing import Any, Callable, Dict, Mapping

from src.memory_runtime_metrics import get_memory_runtime_metrics_registry

from . import vault_service
from .feature_flags import all_flags


CACHE_SCHEMA_VERSION = "raptor-dynamic-cache-v2"
RAPTOR_INDEX_PATH = ".obsidian/odysseus/raptor/index.json"
RAPTOR_SUMMARIES_PATH = ".obsidian/odysseus/raptor/summaries.json"
RAPTOR_REBUILD_REPORT_PATH = ".obsidian/odysseus/raptor/rebuild_report.json"
DEFAULT_TTL_SECONDS = 30.0
DEFAULT_MAX_ENTRIES = 64
DEFAULT_EXTERNAL_VALIDATION_SECONDS = 5.0
MAX_GRAPH_VIEW_LIMIT = 5_000
_CLOCK = time.monotonic


@dataclass(slots=True)
class _CacheEntry:
    key: str
    variant_key: str
    vault_dir: str
    namespace: str
    created_at: float
    payload: Any


@dataclass(slots=True)
class _VaultGeneration:
    generation: int
    source_signature: tuple[tuple[str, int, int], ...] | None
    last_validated_at: float


_LOCK = threading.RLock()
_CACHE: OrderedDict[str, _CacheEntry] = OrderedDict()
_VAULT_GENERATIONS: dict[str, _VaultGeneration] = {}
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
    external_validation_seconds: float = DEFAULT_EXTERNAL_VALIDATION_SECONDS,
) -> Any:
    """Return a cached derived RAPTOR payload, or compute and cache it."""

    normalized_vault = os.path.abspath(vault_dir)
    normalized_namespace = str(namespace or "status")
    stable_params = _stable_params(params or {})
    now = _CLOCK()
    generation = _source_generation(
        normalized_vault,
        now=now,
        validation_interval_seconds=external_validation_seconds,
    )
    variant_key = _variant_key(normalized_namespace, stable_params)
    key = _cache_key(
        normalized_vault,
        normalized_namespace,
        stable_params,
        generation=generation,
    )
    ttl = max(0.0, float(ttl_seconds))
    cache_result = "miss"
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is not None and now - entry.created_at <= ttl:
            _CACHE.move_to_end(key)
            _STATS["hits"] += 1
            cached = deepcopy(entry.payload)
            entry_count = len(_CACHE)
        else:
            cached = None
            stale_keys = []
            if entry is not None:
                stale_keys.append(key)
            stale_keys.extend(
                candidate_key
                for candidate_key, candidate in _CACHE.items()
                if candidate_key != key
                and candidate.vault_dir == normalized_vault
                and candidate.variant_key == variant_key
            )
            for stale_key in stale_keys:
                _CACHE.pop(stale_key, None)
            if stale_keys:
                cache_result = "stale"
                _STATS["stale"] += 1
            else:
                _STATS["misses"] += 1
            entry_count = len(_CACHE)
    if cached is not None:
        _record_cache_metrics("hit", entry_count=entry_count)
        return _with_cache_diagnostics(
            cached,
            cache_result="hit",
            namespace=normalized_namespace,
            key=key,
        )

    payload = loader()
    with _LOCK:
        _CACHE[key] = _CacheEntry(
            key=key,
            variant_key=variant_key,
            vault_dir=normalized_vault,
            namespace=normalized_namespace,
            created_at=now,
            payload=deepcopy(payload),
        )
        _CACHE.move_to_end(key)
        evicted = _evict_if_needed_locked(max_entries=max_entries)
        entry_count = len(_CACHE)
    _record_cache_metrics(cache_result, entry_count=entry_count, evicted=evicted)
    return _with_cache_diagnostics(
        payload,
        cache_result=cache_result,
        namespace=normalized_namespace,
        key=key,
    )


def clear_raptor_cache(vault_dir: str | None = None) -> dict[str, int]:
    """Clear all RAPTOR cache entries, or only entries for one vault."""

    with _LOCK:
        if vault_dir is None:
            cleared = len(_CACHE)
            _CACHE.clear()
            _VAULT_GENERATIONS.clear()
            entry_count = 0
        else:
            target = os.path.abspath(vault_dir)
            keys = [key for key, entry in _CACHE.items() if entry.vault_dir == target]
            for key in keys:
                _CACHE.pop(key, None)
            _VAULT_GENERATIONS.pop(target, None)
            cleared = len(keys)
            entry_count = len(_CACHE)
    _record_cache_entry_gauge(entry_count)
    return {"cleared": cleared, "entry_count": entry_count}


def notify_raptor_vault_changed(vault_dir: str, *, event: str = "write") -> dict[str, int | str]:
    """Advance a vault generation without scanning note contents or paths."""

    target = os.path.abspath(vault_dir)
    now = _CLOCK()
    with _LOCK:
        state = _VAULT_GENERATIONS.get(target)
        if state is None:
            state = _VaultGeneration(generation=1, source_signature=None, last_validated_at=now)
            _VAULT_GENERATIONS[target] = state
        else:
            state.generation += 1
            state.last_validated_at = now
        generation = state.generation
        entry_count = len(_CACHE)
    return {
        "event": str(event or "write"),
        "generation": generation,
        "entry_count": entry_count,
    }


def raptor_cache_diagnostics(vault_dir: str | None = None) -> dict[str, int]:
    with _LOCK:
        if vault_dir is not None:
            target = os.path.abspath(vault_dir)
            entry_count = sum(1 for entry in _CACHE.values() if entry.vault_dir == target)
            generation = (_VAULT_GENERATIONS.get(target) or _VaultGeneration(0, None, 0.0)).generation
        else:
            entry_count = len(_CACHE)
            generation = sum(state.generation for state in _VAULT_GENERATIONS.values())
        return {
            "hits": int(_STATS["hits"]),
            "misses": int(_STATS["misses"]),
            "stale": int(_STATS["stale"]),
            "evictions": int(_STATS["evictions"]),
            "entry_count": entry_count,
            "generation": generation,
        }


def build_raptor_cache_key(
    vault_dir: str,
    namespace: str,
    params: Mapping[str, Any] | None = None,
    *,
    external_validation_seconds: float = DEFAULT_EXTERNAL_VALIDATION_SECONDS,
) -> str:
    normalized_vault = os.path.abspath(vault_dir)
    normalized_namespace = str(namespace or "status")
    stable_params = _stable_params(params or {})
    generation = _source_generation(
        normalized_vault,
        now=_CLOCK(),
        validation_interval_seconds=external_validation_seconds,
    )
    return _cache_key(
        normalized_vault,
        normalized_namespace,
        stable_params,
        generation=generation,
    )


def _cache_key(
    vault_dir: str,
    namespace: str,
    params: Mapping[str, Any],
    *,
    generation: int,
) -> str:
    raw = {
        "schema": CACHE_SCHEMA_VERSION,
        "vault_fingerprint": hashlib.sha256(vault_dir.encode("utf-8")).hexdigest(),
        "namespace": namespace,
        "params": params,
        "source_generation": int(generation),
        "artifact_signature": artifact_signature(vault_dir),
        "feature_flags": _feature_signature(),
    }
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _variant_key(namespace: str, params: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"schema": CACHE_SCHEMA_VERSION, "namespace": namespace, "params": params},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_generation(
    vault_dir: str,
    *,
    now: float,
    validation_interval_seconds: float,
) -> int:
    interval = max(0.0, min(float(validation_interval_seconds), DEFAULT_EXTERNAL_VALIDATION_SECONDS))
    with _LOCK:
        state = _VAULT_GENERATIONS.get(vault_dir)
        if state is not None and now - state.last_validated_at < interval:
            return state.generation

    observed_signature = source_signature(vault_dir)
    with _LOCK:
        state = _VAULT_GENERATIONS.get(vault_dir)
        if state is None:
            state = _VaultGeneration(
                generation=0,
                source_signature=observed_signature,
                last_validated_at=now,
            )
            _VAULT_GENERATIONS[vault_dir] = state
        elif state.source_signature is None:
            state.source_signature = observed_signature
            state.last_validated_at = now
        elif observed_signature != state.source_signature:
            state.generation += 1
            state.source_signature = observed_signature
            state.last_validated_at = now
        else:
            state.last_validated_at = now
        return state.generation


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


def _with_cache_diagnostics(
    payload: Any,
    *,
    cache_result: str,
    namespace: str,
    key: str,
) -> Any:
    cloned = deepcopy(payload)
    diagnostics = {
        "schema": CACHE_SCHEMA_VERSION,
        "namespace": str(namespace),
        "hit": cache_result == "hit",
        "result": cache_result,
        "cache_key": key,
        **raptor_cache_diagnostics(),
    }
    if isinstance(cloned, dict):
        cloned["cache"] = diagnostics
        summary = cloned.get("summary")
        if isinstance(summary, dict):
            summary["cache"] = diagnostics
    return cloned


def _evict_if_needed_locked(*, max_entries: int) -> int:
    max_entries = max(1, int(max_entries or DEFAULT_MAX_ENTRIES))
    evicted = 0
    while len(_CACHE) > max_entries:
        _CACHE.popitem(last=False)
        _STATS["evictions"] += 1
        evicted += 1
    return evicted


def _record_cache_metrics(cache_result: str, *, entry_count: int, evicted: int = 0) -> None:
    try:
        registry = get_memory_runtime_metrics_registry()
        registry.increment_counter(
            "odysseus_raptor_cache_requests_total",
            {"cache_result": cache_result, "runtime": "app"},
        )
        if evicted:
            registry.increment_counter(
                "odysseus_raptor_cache_requests_total",
                {"cache_result": "evicted", "runtime": "app"},
                evicted,
            )
        registry.set_gauge(
            "odysseus_raptor_cache_entries",
            {"runtime": "app"},
            max(0, int(entry_count)),
        )
    except Exception:
        pass


def _record_cache_entry_gauge(entry_count: int) -> None:
    try:
        get_memory_runtime_metrics_registry().set_gauge(
            "odysseus_raptor_cache_entries",
            {"runtime": "app"},
            max(0, int(entry_count)),
        )
    except Exception:
        pass


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

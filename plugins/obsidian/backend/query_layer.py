import json
import os
import asyncio
from copy import deepcopy
import hashlib
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.memory_runtime_metrics import get_memory_runtime_metrics_registry

from .derived_index import DERIVED_INDEX_PATH, derived_index_status, retrieve_derived_chunks
from .model_router import resolve_memory_role_status, synthesize_answer
from .raptor_cache import build_raptor_cache_key
from .readiness import readiness_gate_from_signals

QUERY_CACHE_PATH = ".obsidian/odysseus/memory/query_cache.json"
QUERY_CACHE_SCHEMA_VERSION = "query-cache-v2"
QUERY_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
QUERY_CACHE_MAX_ENTRIES = 512
QUERY_CACHE_MAX_BYTES = 8 * 1024 * 1024
_METRICS_CLOCK = time.perf_counter
_WALL_CLOCK = time.time
_QUERY_CACHE_LOCK = threading.RLock()
_QUERY_CACHE_ACCESS: Dict[tuple[str, str], float] = {}
_QUERY_CACHE_RUNTIME_STATS: Dict[str, Dict[str, int]] = {}
_QUERY_GENERATION_CLOCK = time.monotonic
_QUERY_EXTERNAL_VALIDATION_SECONDS = 5.0
_QUERY_SOURCE_STATE_MAX_VAULTS = 32
_QUERY_SOURCE_STATE: OrderedDict[str, Dict[str, Any]] = OrderedDict()
_QUERY_SOURCE_IGNORED_DIRS = {".git", ".obsidian", ".snapshots", ".trash", "__pycache__"}


def _record_query_duration(phase: str, outcome: str, started: float) -> None:
    try:
        get_memory_runtime_metrics_registry().observe_histogram(
            "odysseus_memory_operation_duration_seconds",
            {
                "component": "memory",
                "operation": "query",
                "phase": phase,
                "outcome": outcome,
                "runtime": "app",
            },
            max(0.0, _METRICS_CLOCK() - started),
        )
    except Exception:
        pass


def _record_query_outcome(outcome: str) -> None:
    try:
        get_memory_runtime_metrics_registry().increment_counter(
            "odysseus_memory_operations_total",
            {
                "component": "memory",
                "operation": "query",
                "outcome": outcome,
                "runtime": "app",
            },
        )
    except Exception:
        pass


def _record_query_cache_state(cache: Dict[str, Any]) -> None:
    try:
        registry = get_memory_runtime_metrics_registry()
        registry.set_gauge(
            "odysseus_query_cache_entries",
            {"runtime": "app"},
            len(cache.get("entries") or {}),
        )
        registry.set_gauge(
            "odysseus_query_cache_bytes",
            {"runtime": "app"},
            len(json.dumps(cache, sort_keys=True, separators=(",", ":")).encode("utf-8")),
        )
    except Exception:
        pass


def _cache_abspath(vault_dir: str) -> str:
    return os.path.join(vault_dir, QUERY_CACHE_PATH.replace("/", os.sep))


def _empty_cache() -> Dict[str, Any]:
    return {
        "schema": QUERY_CACHE_SCHEMA_VERSION,
        "entries": {},
        "legacy_stats": {"hits": 0, "misses": 0},
    }


def _load_cache(vault_dir: str) -> Dict[str, Any]:
    with _QUERY_CACHE_LOCK:
        payload, changed = _read_cache_unlocked(vault_dir)
        if _enforce_cache_bounds(vault_dir, payload, now=_WALL_CLOCK()):
            changed = True
        if changed:
            _save_cache_unlocked(vault_dir, payload)
        return deepcopy(payload)


def _read_cache_unlocked(vault_dir: str) -> tuple[Dict[str, Any], bool]:
    path = _cache_abspath(vault_dir)
    if not os.path.exists(path):
        return _empty_cache(), False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_cache(), False
    if not isinstance(payload, dict):
        return _empty_cache(), False
    if payload.get("schema") != QUERY_CACHE_SCHEMA_VERSION:
        return _migrate_v1_cache(vault_dir, payload), True
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        payload["entries"] = {}
        return payload, True
    payload.setdefault("legacy_stats", {"hits": 0, "misses": 0})
    removed_stats = payload.pop("stats", None) is not None
    return payload, removed_stats


def _save_cache_unlocked(vault_dir: str, payload: Dict[str, Any]) -> None:
    path = _cache_abspath(vault_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def _cache_key(
    vault_dir: str,
    query: str,
    top_k: int,
    path_prefix: str,
    answer_mode: str,
    derived: Dict[str, Any],
) -> str:
    return _cache_key_from_generation(
        query,
        top_k,
        path_prefix,
        answer_mode,
        _query_cache_generation(vault_dir),
    )


def _derived_generation(vault_dir: str, derived: Dict[str, Any]) -> str:
    built_at = str(derived.get("built_at") or "")
    path = os.path.join(vault_dir, DERIVED_INDEX_PATH.replace("/", os.sep))
    try:
        stat = os.stat(path)
    except OSError:
        return f"{built_at}:0:0"
    return f"{built_at}:{int(stat.st_mtime_ns)}:{int(stat.st_size)}"


def _query_cache_generation(vault_dir: str) -> str:
    return build_raptor_cache_key(
        vault_dir,
        "query_cache_generation",
        {
            "derived_index": _derived_generation(vault_dir, {}),
            "source_generation": _query_source_generation(vault_dir),
        },
    )


def _query_source_generation(vault_dir: str) -> int:
    target = os.path.abspath(vault_dir)
    now = _QUERY_GENERATION_CLOCK()
    with _QUERY_CACHE_LOCK:
        state = _QUERY_SOURCE_STATE.get(target)
        if (
            state is not None
            and now - float(state.get("last_validated_at") or 0.0)
            < _QUERY_EXTERNAL_VALIDATION_SECONDS
        ):
            _QUERY_SOURCE_STATE.move_to_end(target)
            return int(state.get("generation") or 0)

    signature = _query_source_signature(target)
    with _QUERY_CACHE_LOCK:
        state = _QUERY_SOURCE_STATE.get(target)
        generation = int((state or {}).get("generation") or 0)
        if state is not None and state.get("signature") != signature:
            generation += 1
        _QUERY_SOURCE_STATE[target] = {
            "generation": generation,
            "signature": signature,
            "last_validated_at": now,
        }
        _QUERY_SOURCE_STATE.move_to_end(target)
        while len(_QUERY_SOURCE_STATE) > _QUERY_SOURCE_STATE_MAX_VAULTS:
            _QUERY_SOURCE_STATE.popitem(last=False)
        return generation


def _query_source_signature(vault_dir: str) -> tuple[tuple[str, int, int], ...]:
    entries: List[tuple[str, int, int]] = []
    for root, dirs, files in os.walk(vault_dir):
        dirs[:] = [directory for directory in dirs if directory not in _QUERY_SOURCE_IGNORED_DIRS]
        for filename in files:
            path = os.path.join(root, filename)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            relative = os.path.relpath(path, vault_dir).replace("\\", "/")
            entries.append((relative, int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(sorted(entries, key=lambda item: item[0].casefold()))


def _clear_query_generation_state(vault_dir: str | None = None) -> None:
    with _QUERY_CACHE_LOCK:
        if vault_dir is None:
            _QUERY_SOURCE_STATE.clear()
            return
        _QUERY_SOURCE_STATE.pop(os.path.abspath(vault_dir), None)


def _cache_key_from_generation(
    query: str,
    top_k: int,
    path_prefix: str,
    answer_mode: str,
    derived_generation: str,
) -> str:
    raw = json.dumps(
        {
            "query": " ".join(str(query or "").split()).casefold(),
            "top_k": max(1, int(top_k or 5)),
            "path_prefix": str(path_prefix or "").strip().replace("\\", "/").strip("/"),
            "answer_mode": str(answer_mode or "auto").strip().casefold() or "auto",
            "derived_generation": str(derived_generation or ""),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _runtime_cache_stats(vault_dir: str) -> Dict[str, int]:
    target = os.path.abspath(vault_dir)
    with _QUERY_CACHE_LOCK:
        return dict(_QUERY_CACHE_RUNTIME_STATS.get(target) or {"hits": 0, "misses": 0})


def _increment_runtime_cache_stat(vault_dir: str, name: str) -> None:
    target = os.path.abspath(vault_dir)
    with _QUERY_CACHE_LOCK:
        stats = _QUERY_CACHE_RUNTIME_STATS.setdefault(target, {"hits": 0, "misses": 0})
        stats[name] = int(stats.get(name) or 0) + 1


def _cache_lookup(vault_dir: str, key: str) -> Optional[Dict[str, Any]]:
    target = os.path.abspath(vault_dir)
    now = _WALL_CLOCK()
    with _QUERY_CACHE_LOCK:
        payload, changed = _read_cache_unlocked(vault_dir)
        if _enforce_cache_bounds(vault_dir, payload, now=now):
            changed = True
        entry = (payload.get("entries") or {}).get(key)
        if changed:
            _save_cache_unlocked(vault_dir, payload)
        if isinstance(entry, dict):
            _QUERY_CACHE_ACCESS[(target, key)] = now
            _increment_runtime_cache_stat(vault_dir, "hits")
            return deepcopy(entry)
        _increment_runtime_cache_stat(vault_dir, "misses")
        return None


def _cache_store(vault_dir: str, key: str, result: Dict[str, Any]) -> None:
    target = os.path.abspath(vault_dir)
    now = _WALL_CLOCK()
    with _QUERY_CACHE_LOCK:
        payload, _changed = _read_cache_unlocked(vault_dir)
        entries = payload.setdefault("entries", {})
        stored = deepcopy(result)
        stored.pop("query", None)
        stored["created_at"] = now
        stored.pop("cached_at", None)
        entries[key] = stored
        _QUERY_CACHE_ACCESS[(target, key)] = now
        _enforce_cache_bounds(vault_dir, payload, now=now)
        _save_cache_unlocked(vault_dir, payload)


def _migrate_v1_cache(vault_dir: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    migrated = _empty_cache()
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    migrated["legacy_stats"] = {
        "hits": _nonnegative_int(stats.get("hits")),
        "misses": _nonnegative_int(stats.get("misses")),
    }
    entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}
    for legacy_key, legacy_entry in entries.items():
        if not isinstance(legacy_entry, dict):
            continue
        try:
            parameters = json.loads(str(legacy_key))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(parameters, dict):
            continue
        key = _cache_key_from_generation(
            str(parameters.get("query") or legacy_entry.get("query") or ""),
            int(parameters.get("top_k") or 5),
            str(parameters.get("path_prefix") or ""),
            str(parameters.get("answer_mode") or "auto"),
            _query_cache_generation(vault_dir),
        )
        stored = deepcopy(legacy_entry)
        stored.pop("query", None)
        stored["created_at"] = _iso_to_epoch(stored.pop("cached_at", "")) or _WALL_CLOCK()
        migrated["entries"][key] = stored
    return migrated


def _iso_to_epoch(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _enforce_cache_bounds(vault_dir: str, payload: Dict[str, Any], *, now: float) -> bool:
    target = os.path.abspath(vault_dir)
    entries = payload.setdefault("entries", {})
    changed = False
    expired = [
        key
        for key, entry in entries.items()
        if not isinstance(entry, dict)
        or now - _entry_time(entry) > QUERY_CACHE_TTL_SECONDS
    ]
    for key in expired:
        entries.pop(key, None)
        _QUERY_CACHE_ACCESS.pop((target, key), None)
        changed = True
    while len(entries) > QUERY_CACHE_MAX_ENTRIES:
        _evict_lru_entry(target, entries)
        changed = True
    while entries and _serialized_cache_bytes(payload) > QUERY_CACHE_MAX_BYTES:
        _evict_lru_entry(target, entries)
        changed = True
    return changed


def _evict_lru_entry(target: str, entries: Dict[str, Any]) -> None:
    oldest_key = min(
        entries,
        key=lambda key: _QUERY_CACHE_ACCESS.get(
            (target, key),
            _entry_time(entries.get(key)),
        ),
    )
    entries.pop(oldest_key, None)
    _QUERY_CACHE_ACCESS.pop((target, oldest_key), None)


def _serialized_cache_bytes(payload: Dict[str, Any]) -> int:
    return len(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def _entry_time(entry: Any) -> float:
    try:
        value = float((entry or {}).get("created_at") or 0.0)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0.0
    return value if value >= 0.0 else 0.0


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _query_layer_status_impl(vault_dir: str, owner: Optional[str] = None) -> Dict[str, Any]:
    derived = derived_index_status(vault_dir)
    cache = _load_cache(vault_dir)
    runtime_stats = _runtime_cache_stats(vault_dir)
    legacy_stats = cache.get("legacy_stats") if isinstance(cache.get("legacy_stats"), dict) else {}
    cache_hits = _nonnegative_int(legacy_stats.get("hits")) + _nonnegative_int(runtime_stats.get("hits"))
    cache_misses = _nonnegative_int(legacy_stats.get("misses")) + _nonnegative_int(runtime_stats.get("misses"))
    model_router = resolve_memory_role_status(owner)
    derived_ready = bool((derived.get("readiness") or {}).get("ready", False))
    source_count = int((derived.get("summary") or {}).get("source_count") or 0)
    chunk_count = int((derived.get("summary") or {}).get("chunk_count") or 0)
    gaps: List[str] = []
    if not bool(derived.get("configured")):
        gaps.append("query_index_missing")
    if not derived_ready:
        gaps.append("query_index_not_ready")
    if source_count <= 0 or chunk_count <= 0:
        gaps.append("query_index_empty")
    state = "ready" if not gaps else ("not_configured" if not bool(derived.get("configured")) else "blocked")
    readiness = {
        "ready": not gaps,
        "state": state,
        "gaps": gaps,
        "writes_supported": False,
    }
    readiness_signal = {
        "family": "query_layer",
        "source": "readiness",
        "state": readiness["state"],
        "ready": readiness["ready"],
        "gaps": list(readiness["gaps"]),
        "gap_count": len(readiness["gaps"]),
    }
    readiness_gate = readiness_gate_from_signals([readiness_signal])
    warnings: List[str] = []
    if not derived_ready and derived.get("readiness"):
        warnings.append(
            f"Query layer depends on a ready derived index; current derived index state is {derived['readiness'].get('state', 'unknown')}."
        )
    _record_query_cache_state(cache)
    return {
        "enabled": True,
        "model_router": model_router,
        "cache": {
            "path": QUERY_CACHE_PATH,
            "entries": len(cache.get("entries") or {}),
            "hits": cache_hits,
            "misses": cache_misses,
            "bytes": _serialized_cache_bytes(cache),
            "schema": QUERY_CACHE_SCHEMA_VERSION,
        },
        "readiness": readiness,
        "readiness_signals": [readiness_signal],
        "readiness_gate": readiness_gate,
        "summary": {
            "source_count": source_count,
            "chunk_count": chunk_count,
            "cache_entries": len(cache.get("entries") or {}),
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_bytes": _serialized_cache_bytes(cache),
            "router_warning_count": len((model_router.get("warnings") or [])),
            "readiness_state": readiness["state"],
            "readiness_gaps": len(readiness["gaps"]),
            "readiness_gap_names": list(readiness["gaps"]),
            "readiness_gate": readiness_gate,
            "writes_supported": False,
            "warnings": warnings,
        },
        "writes_supported": False,
        "warnings": warnings,
    }


def query_layer_status(
    vault_dir: str,
    owner: Optional[str] = None,
    *,
    _record_total: bool = True,
) -> Dict[str, Any]:
    if not _record_total:
        return _query_layer_status_impl(vault_dir, owner)
    started = _METRICS_CLOCK()
    outcome = "error"
    try:
        result = _query_layer_status_impl(vault_dir, owner)
        outcome = "success" if bool((result.get("readiness") or {}).get("ready")) else "blocked"
        return result
    except (FileNotFoundError, PermissionError, ValueError):
        outcome = "blocked"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        _record_query_duration("total", outcome, started)
        _record_query_outcome(outcome)


def _extractive_result(vault_dir: str, query: str, *, top_k: int = 5, path_prefix: str = "") -> Dict[str, Any]:
    status = query_layer_status(vault_dir)
    if not status["readiness"]["ready"]:
        return {
            "query": str(query or ""),
            "path_prefix": str(path_prefix or "").strip(),
            "answer": "",
            "requested_answer_mode": "auto",
            "answer_mode": "extractive",
            "provider": "",
            "selected_role": "memory.answer",
            "selected_model": "extractive",
            "selected_endpoint_id": "",
            "fallback_reason": "query_layer_not_ready",
            "model_context_tokens": 0,
            "model_capability_warnings": ["query_layer_not_ready"],
            "citations": [],
            "confidence": "low",
            "confidence_score": 0.0,
            "summary": {
                "matched_chunks": 0,
                "matched_sources": 0,
                "cache_hit": False,
                "readiness_state": status["readiness"]["state"],
                "readiness_gate": status["readiness_gate"],
                "warnings": status["warnings"],
            },
            "readiness": status["readiness"],
            "readiness_gate": status["readiness_gate"],
            "warnings": status["warnings"],
        }

    normalized_prefix = str(path_prefix or "").strip().replace("\\", "/").strip("/")
    derived = derived_index_status(vault_dir)
    retrieval = retrieve_derived_chunks(
        vault_dir,
        query,
        top_k=max(1, int(top_k or 5)),
        path_prefix=normalized_prefix,
    )
    results = list(retrieval.get("results") or [])
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in results:
        source_path = str(row.get("source_path") or "")
        bucket = grouped.setdefault(
            source_path,
            {
                "path": source_path,
                "title": str(row.get("title") or ""),
                "score": 0,
                "snippets": [],
                "source_hash": str(row.get("source_hash") or ""),
            },
        )
        bucket["score"] = max(int(bucket["score"]), int(row.get("score") or 0))
        snippet = str(row.get("text") or "").strip()
        if snippet and snippet not in bucket["snippets"]:
            bucket["snippets"].append(snippet)
    citations = sorted(grouped.values(), key=lambda item: (-int(item["score"]), item["path"].lower()))
    answer_parts = [
        f"{item['title']}: {item['snippets'][0][:220].strip()}"
        for item in citations
        if item["snippets"]
    ]
    answer = "\n".join(answer_parts[:3])
    confidence_score = _confidence_score(results, citations)
    confidence = _confidence_label(confidence_score)
    warnings: List[str] = []
    if not citations:
        warnings.append("No derived chunks matched this query with the current lightweight ranking.")
    if normalized_prefix and not citations:
        warnings.append(f"No citations matched the requested subtree prefix '{normalized_prefix}'.")
    if confidence == "low" and citations:
        warnings.append("Confidence is low because only weak or sparse chunk evidence matched this query.")
    result = {
        "query": str(query or ""),
        "path_prefix": normalized_prefix,
        "answer": answer,
        "requested_answer_mode": "auto",
        "answer_mode": "extractive",
        "provider": "",
        "selected_role": "memory.answer",
        "selected_model": "extractive",
        "selected_endpoint_id": "",
        "fallback_reason": "",
        "model_context_tokens": 0,
        "model_capability_warnings": [],
        "citations": [
            {
                "path": item["path"],
                "title": item["title"],
                "score": item["score"],
                "source_hash": item["source_hash"],
                "snippets": item["snippets"][:2],
            }
            for item in citations
        ],
        "confidence": confidence,
        "confidence_score": confidence_score,
        "summary": {
            "matched_chunks": len(results),
            "matched_sources": len(citations),
            "cache_hit": False,
            "readiness_state": status["readiness"]["state"],
            "readiness_gate": status["readiness_gate"],
            "warnings": warnings,
        },
        "readiness": status["readiness"],
        "readiness_gate": status["readiness_gate"],
        "warnings": warnings,
    }
    return result


async def _answer_query_async_impl(
    vault_dir: str,
    query: str,
    *,
    top_k: int = 5,
    path_prefix: str = "",
    owner: Optional[str] = None,
    answer_mode: str = "auto",
) -> Dict[str, Any]:
    normalized_mode = str(answer_mode or "auto").strip().lower() or "auto"
    normalized_prefix = str(path_prefix or "").strip().replace("\\", "/").strip("/")
    key = _cache_key(vault_dir, query, top_k, normalized_prefix, normalized_mode, {})
    entry = _cache_lookup(vault_dir, key)
    if isinstance(entry, dict):
        entry.pop("created_at", None)
        entry["query"] = str(query or "")
        entry["summary"] = dict(entry.get("summary") or {})
        entry["summary"]["cache_hit"] = True
        return entry

    extractive = _extractive_result(vault_dir, query, top_k=top_k, path_prefix=path_prefix)
    extractive["requested_answer_mode"] = normalized_mode
    if not extractive["readiness"]["ready"]:
        extractive["fallback_reason"] = "query_layer_not_ready"
        return extractive

    synthesis = await synthesize_answer(
        owner=owner,
        query=query,
        citations=list(extractive.get("citations") or []),
        requested_mode=normalized_mode,
        confidence=str(extractive.get("confidence") or "low"),
    )
    warnings = list(extractive.get("warnings") or [])
    for warning in list(synthesis.get("warnings") or []):
        if warning not in warnings:
            warnings.append(warning)
    result = {
        **extractive,
        "requested_answer_mode": normalized_mode,
        "answer_mode": str(synthesis.get("answer_mode") or "extractive"),
        "provider": str(synthesis.get("provider") or ""),
        "selected_role": str(synthesis.get("selected_role") or "memory.answer"),
        "selected_model": str(synthesis.get("selected_model") or "extractive"),
        "selected_endpoint_id": str(synthesis.get("selected_endpoint_id") or ""),
        "fallback_reason": str(synthesis.get("fallback_reason") or ""),
        "model_context_tokens": int(synthesis.get("model_context_tokens") or 0),
        "model_capability_warnings": list(synthesis.get("model_capability_warnings") or []),
        "warnings": warnings,
        "answer": str(synthesis.get("answer") or "").strip() or str(extractive.get("answer") or ""),
    }
    result["summary"] = dict(result.get("summary") or {})
    result["summary"]["requested_answer_mode"] = normalized_mode
    result["summary"]["answer_mode"] = result["answer_mode"]
    result["summary"]["fallback_reason"] = result["fallback_reason"]
    result["summary"]["selected_model"] = result["selected_model"]
    result["summary"]["selected_endpoint_id"] = result["selected_endpoint_id"]
    result["summary"]["provider"] = result["provider"]
    _cache_store(vault_dir, key, result)
    return result


async def answer_query_async(
    vault_dir: str,
    query: str,
    *,
    top_k: int = 5,
    path_prefix: str = "",
    owner: Optional[str] = None,
    answer_mode: str = "auto",
) -> Dict[str, Any]:
    started = _METRICS_CLOCK()
    outcome = "error"
    try:
        result = await _answer_query_async_impl(
            vault_dir,
            query,
            top_k=top_k,
            path_prefix=path_prefix,
            owner=owner,
            answer_mode=answer_mode,
        )
        outcome = "success" if bool((result.get("readiness") or {}).get("ready")) else "blocked"
        return result
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except (FileNotFoundError, PermissionError, ValueError):
        outcome = "blocked"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        _record_query_duration("total", outcome, started)
        _record_query_outcome(outcome)


def answer_query(
    vault_dir: str,
    query: str,
    *,
    top_k: int = 5,
    path_prefix: str = "",
    owner: Optional[str] = None,
    answer_mode: str = "auto",
) -> Dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            answer_query_async(
                vault_dir,
                query,
                top_k=top_k,
                path_prefix=path_prefix,
                owner=owner,
                answer_mode=answer_mode,
            )
        )
    result = _extractive_result(vault_dir, query, top_k=top_k, path_prefix=path_prefix)
    result["requested_answer_mode"] = str(answer_mode or "auto").strip().lower() or "auto"
    if result["requested_answer_mode"] != "extractive":
        result["fallback_reason"] = "sync_fallback_extract_only"
        warnings = list(result.get("warnings") or [])
        if "sync_fallback_extract_only" not in warnings:
            warnings.append("sync_fallback_extract_only")
        result["warnings"] = warnings
    return result


def _confidence_score(results: List[Dict[str, Any]], citations: List[Dict[str, Any]]) -> float:
    if not results or not citations:
        return 0.0
    top_score = max(float(row.get("score") or 0.0) for row in results)
    source_bonus = min(0.25, 0.08 * max(0, len(citations) - 1))
    chunk_bonus = min(0.2, 0.04 * max(0, len(results) - 1))
    raw = min(1.0, (top_score / 8.0) + source_bonus + chunk_bonus)
    return round(raw, 2)


def _confidence_label(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"

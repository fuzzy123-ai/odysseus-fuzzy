import json
import os
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .derived_index import derived_index_status, retrieve_derived_chunks
from .model_router import resolve_memory_role_status, synthesize_answer
from .readiness import readiness_gate_from_signals

QUERY_CACHE_PATH = ".obsidian/odysseus/memory/query_cache.json"


def _cache_abspath(vault_dir: str) -> str:
    return os.path.join(vault_dir, QUERY_CACHE_PATH.replace("/", os.sep))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_cache(vault_dir: str) -> Dict[str, Any]:
    path = _cache_abspath(vault_dir)
    if not os.path.exists(path):
        return {"entries": {}, "stats": {"hits": 0, "misses": 0}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"entries": {}, "stats": {"hits": 0, "misses": 0}}
    if not isinstance(payload, dict):
        return {"entries": {}, "stats": {"hits": 0, "misses": 0}}
    payload.setdefault("entries", {})
    payload.setdefault("stats", {"hits": 0, "misses": 0})
    return payload


def _save_cache(vault_dir: str, payload: Dict[str, Any]) -> None:
    path = _cache_abspath(vault_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)


def _cache_key(query: str, top_k: int, path_prefix: str, answer_mode: str, derived: Dict[str, Any]) -> str:
    built_at = str(derived.get("built_at") or "")
    return json.dumps(
        {
            "query": str(query or "").strip().lower(),
            "top_k": max(1, int(top_k or 5)),
            "path_prefix": str(path_prefix or "").strip(),
            "answer_mode": str(answer_mode or "auto").strip().lower(),
            "derived_built_at": built_at,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def query_layer_status(vault_dir: str, owner: Optional[str] = None) -> Dict[str, Any]:
    derived = derived_index_status(vault_dir)
    cache = _load_cache(vault_dir)
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
    return {
        "enabled": True,
        "model_router": model_router,
        "cache": {
            "path": QUERY_CACHE_PATH,
            "entries": len(cache.get("entries") or {}),
            "hits": int((cache.get("stats") or {}).get("hits") or 0),
            "misses": int((cache.get("stats") or {}).get("misses") or 0),
        },
        "readiness": readiness,
        "readiness_signals": [readiness_signal],
        "readiness_gate": readiness_gate,
        "summary": {
            "source_count": source_count,
            "chunk_count": chunk_count,
            "cache_entries": len(cache.get("entries") or {}),
            "cache_hits": int((cache.get("stats") or {}).get("hits") or 0),
            "cache_misses": int((cache.get("stats") or {}).get("misses") or 0),
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
    cache = _load_cache(vault_dir)

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


async def answer_query_async(
    vault_dir: str,
    query: str,
    *,
    top_k: int = 5,
    path_prefix: str = "",
    owner: Optional[str] = None,
    answer_mode: str = "auto",
) -> Dict[str, Any]:
    extractive = _extractive_result(vault_dir, query, top_k=top_k, path_prefix=path_prefix)
    normalized_mode = str(answer_mode or "auto").strip().lower() or "auto"
    extractive["requested_answer_mode"] = normalized_mode
    if not extractive["readiness"]["ready"]:
        extractive["fallback_reason"] = "query_layer_not_ready"
        return extractive

    normalized_prefix = str(path_prefix or "").strip().replace("\\", "/").strip("/")
    derived = derived_index_status(vault_dir)
    cache = _load_cache(vault_dir)
    key = _cache_key(query, top_k, normalized_prefix, normalized_mode, derived)
    entry = (cache.get("entries") or {}).get(key)
    if isinstance(entry, dict):
        stats = dict(cache.get("stats") or {})
        stats["hits"] = int(stats.get("hits") or 0) + 1
        cache["stats"] = stats
        _save_cache(vault_dir, cache)
        cached = dict(entry)
        cached["summary"] = dict(cached.get("summary") or {})
        cached["summary"]["cache_hit"] = True
        return cached

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
    stats = dict(cache.get("stats") or {})
    stats["misses"] = int(stats.get("misses") or 0) + 1
    cache["stats"] = stats
    entries = dict(cache.get("entries") or {})
    entries[key] = {
        **result,
        "cached_at": _utc_iso(),
    }
    cache["entries"] = entries
    _save_cache(vault_dir, cache)
    return result


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

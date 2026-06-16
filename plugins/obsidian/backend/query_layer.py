import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from .derived_index import derived_index_status, retrieve_derived_chunks
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


def _cache_key(query: str, top_k: int, path_prefix: str, derived: Dict[str, Any]) -> str:
    built_at = str(derived.get("built_at") or "")
    return json.dumps(
        {
            "query": str(query or "").strip().lower(),
            "top_k": max(1, int(top_k or 5)),
            "path_prefix": str(path_prefix or "").strip(),
            "derived_built_at": built_at,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def query_layer_status(vault_dir: str) -> Dict[str, Any]:
    derived = derived_index_status(vault_dir)
    cache = _load_cache(vault_dir)
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


def answer_query(vault_dir: str, query: str, *, top_k: int = 5, path_prefix: str = "") -> Dict[str, Any]:
    status = query_layer_status(vault_dir)
    if not status["readiness"]["ready"]:
        return {
            "query": str(query or ""),
            "path_prefix": str(path_prefix or "").strip(),
            "answer": "",
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
    key = _cache_key(query, top_k, normalized_prefix, derived)
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

from typing import Any, Dict, List

from .derived_index import derived_index_status, retrieve_derived_chunks
from .readiness import readiness_gate_from_signals


def query_layer_status(vault_dir: str) -> Dict[str, Any]:
    derived = derived_index_status(vault_dir)
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
        "readiness": readiness,
        "readiness_signals": [readiness_signal],
        "readiness_gate": readiness_gate,
        "summary": {
            "source_count": source_count,
            "chunk_count": chunk_count,
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


def answer_query(vault_dir: str, query: str, *, top_k: int = 5) -> Dict[str, Any]:
    status = query_layer_status(vault_dir)
    if not status["readiness"]["ready"]:
        return {
            "query": str(query or ""),
            "answer": "",
            "citations": [],
            "confidence": "low",
            "confidence_score": 0.0,
            "summary": {
                "matched_chunks": 0,
                "matched_sources": 0,
                "readiness_state": status["readiness"]["state"],
                "readiness_gate": status["readiness_gate"],
                "warnings": status["warnings"],
            },
            "readiness": status["readiness"],
            "readiness_gate": status["readiness_gate"],
            "warnings": status["warnings"],
        }

    retrieval = retrieve_derived_chunks(vault_dir, query, top_k=max(1, int(top_k or 5)))
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
    return {
        "query": str(query or ""),
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
            "readiness_state": status["readiness"]["state"],
            "readiness_gate": status["readiness_gate"],
            "warnings": warnings,
        },
        "readiness": status["readiness"],
        "readiness_gate": status["readiness_gate"],
        "warnings": warnings,
    }


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

import json
import os
import re
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from src.memory_runtime_metrics import get_memory_runtime_metrics_registry

from . import vault_service
from .memory_ledger import (
    ledger_db_abspath,
    mark_source_failed,
    mark_source_indexed,
    memory_ledger_status,
    sync_memory_ledger,
)
from .readiness import readiness_gate_from_signals
from .vault_model import extract_tags


DERIVED_INDEX_PATH = ".obsidian/odysseus/memory/derived_index.json"
TEXT_DOCUMENT_EXTENSIONS = {".txt", ".json", ".csv", ".tsv", ".html", ".htm", ".rtf"}
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_METRICS_CLOCK = time.perf_counter
_DERIVED_STATUS_CACHE_MAX_VAULTS = 32
_DERIVED_STATUS_CACHE_LOCK = threading.RLock()
_DERIVED_STATUS_LOCK_STRIPES = tuple(threading.RLock() for _ in range(16))
_DERIVED_STATUS_CACHE: OrderedDict[str, Dict[str, Any]] = OrderedDict()


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


def derived_index_abspath(vault_dir: str) -> str:
    return os.path.join(vault_dir, DERIVED_INDEX_PATH.replace("/", os.sep))


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_text(text: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _sha256_file(vault_dir: str, path: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    abs_path = vault_service.secure_path(vault_dir, path)
    with open(abs_path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _normalize_path(path: Any) -> str:
    return str(path or "").replace("\\", "/").strip("/")


def _query_terms(query: Any) -> List[str]:
    return [
        term.lower()
        for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_/-]{1,}", str(query or ""))
    ]


def _normalized_query_phrase(query: Any) -> str:
    return " ".join(_query_terms(query))


def _title_from_body(body: str, path: str) -> str:
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return os.path.splitext(os.path.basename(path))[0]


def _split_into_chunks(text: str, *, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    clean = str(text or "").strip()
    if not clean:
        return []
    if len(clean) <= chunk_size:
        return [clean]
    chunks: List[str] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + chunk_size)
        chunk = clean[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(clean):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _read_source_text(vault_dir: str, path: str, source_type: str) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    ext = os.path.splitext(path.lower())[1]
    if source_type in {"markdown", "chat_capture"}:
        return vault_service.read_file(vault_dir, path), warnings
    if ext in TEXT_DOCUMENT_EXTENSIONS:
        abs_path = vault_service.secure_path(vault_dir, path)
        with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(), warnings
    warnings.append(f"Derived index stored metadata only for {path}; binary document text extraction is not implemented yet.")
    return "", warnings


def _source_links(path: str, body: str, existing_paths: set[str]) -> List[str]:
    links: List[str] = []
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    stems = {
        os.path.splitext(os.path.basename(existing))[0].lower(): existing
        for existing in existing_paths
    }
    for raw in WIKI_LINK_RE.findall(body or ""):
        target = raw.strip().replace("\\", "/")
        if not target.lower().endswith(".md"):
            target += ".md"
        if "/" not in target and folder:
            target = f"{folder}/{target}"
        normalized = _normalize_path(target)
        if normalized not in existing_paths:
            normalized = stems.get(
                os.path.splitext(os.path.basename(normalized))[0].lower(),
                normalized,
            )
        if normalized in existing_paths:
            links.append(normalized)
    return sorted(set(links))


def build_derived_index(vault_dir: str, *, chunk_size: int = 1000, overlap: int = 200) -> Dict[str, Any]:
    sync_memory_ledger(vault_dir)
    ledger = memory_ledger_status(vault_dir)
    entries = ledger.get("entries") or []
    built_at = _utcnow()
    warnings: List[str] = []
    chunks: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    existing_paths = {_normalize_path(entry.get("path")) for entry in entries}

    for entry in entries:
        path = _normalize_path(entry.get("path"))
        source_type = str(entry.get("source_type") or "")
        source_hash = str(entry.get("source_hash") or "")
        source_warnings: List[str] = []
        try:
            raw_text, source_warnings = _read_source_text(vault_dir, path, source_type)
            frontmatter, body = vault_service.parse_frontmatter(raw_text) if raw_text else ({}, "")
            title = str(frontmatter.get("title") or _title_from_body(body or raw_text, path))
            tags = extract_tags(raw_text, path)["tags"] if raw_text and source_type in {"markdown", "chat_capture"} else []
            chunk_text = body if body else raw_text
            source_chunks = _split_into_chunks(chunk_text, chunk_size=chunk_size, overlap=overlap)
            links = _source_links(path, body or raw_text, existing_paths) if source_type in {"markdown", "chat_capture"} else []
            for ordinal, text in enumerate(source_chunks):
                chunks.append(
                    {
                        "id": f"{path}::chunk:{ordinal}",
                        "source_path": path,
                        "source_hash": source_hash,
                        "source_type": source_type,
                        "ordinal": ordinal,
                        "title": title,
                        "tags": tags,
                        "text": text,
                        "char_count": len(text),
                    }
                )
            sources.append(
                {
                    "path": path,
                    "source_type": source_type,
                    "source_hash": source_hash,
                    "title": title,
                    "tags": tags,
                    "chunk_count": len(source_chunks),
                    "links": links,
                }
            )
            warnings.extend(source_warnings)
            mark_source_indexed(vault_dir, path, chunk_count=len(source_chunks))
        except Exception as exc:
            mark_source_failed(vault_dir, path, str(exc))
            warnings.append(f"Failed to index {path}: {exc}")

    graph_edges = [
        {"source": source["path"], "target": target, "type": "wiki_link"}
        for source in sources
        for target in source.get("links", [])
    ]
    payload = {
        "built_at": built_at,
        "chunk_size": int(chunk_size),
        "overlap": int(overlap),
        "source_hashes": {source["path"]: source["source_hash"] for source in sources},
        "sources": sources,
        "chunks": chunks,
        "graph": {
            "node_count": len(sources),
            "edge_count": len(graph_edges),
            "edges": graph_edges,
        },
        "warnings": warnings[:50],
    }
    out_path = derived_index_abspath(vault_dir)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return derived_index_status(vault_dir)


def _load_payload(vault_dir: str) -> Tuple[Dict[str, Any], bool]:
    path = derived_index_abspath(vault_dir)
    if not os.path.exists(path):
        return {}, False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}, True
    return payload if isinstance(payload, dict) else {}, False


def _readiness(*, configured: bool, invalid: bool, dirty: bool, failed_sources: int) -> Dict[str, Any]:
    gaps: List[str] = []
    if not configured:
        gaps.append("derived_index_missing")
    if invalid:
        gaps.append("derived_index_invalid")
    if dirty:
        gaps.append("derived_index_dirty")
    if failed_sources:
        gaps.append("derived_index_failed_sources")
    if invalid:
        state = "invalid"
    elif not configured:
        state = "not_configured"
    elif dirty or failed_sources:
        state = "dirty"
    else:
        state = "ready"
    return {
        "ready": state == "ready",
        "state": state,
        "gaps": gaps,
        "writes_supported": True,
    }


def _file_signature(path: str) -> Tuple[int, int]:
    try:
        stat = os.stat(path)
    except OSError:
        return (0, 0)
    return (int(stat.st_mtime_ns), int(stat.st_size))


def _derived_status_artifact_signature(vault_dir: str) -> Tuple[Tuple[int, int], ...]:
    ledger_path = ledger_db_abspath(vault_dir)
    return (
        _file_signature(derived_index_abspath(vault_dir)),
        _file_signature(ledger_path),
        _file_signature(f"{ledger_path}-wal"),
    )


def _derived_status_source_signature(
    vault_dir: str,
    source_paths: Tuple[str, ...],
) -> Tuple[Tuple[str, int, int], ...]:
    signature: List[Tuple[str, int, int]] = []
    for raw_path in source_paths:
        path = _normalize_path(raw_path)
        try:
            stat = os.stat(vault_service.secure_path(vault_dir, path))
            signature.append((path, int(stat.st_mtime_ns), int(stat.st_size)))
        except (OSError, ValueError):
            signature.append((path, 0, 0))
    return tuple(signature)


def _clear_derived_status_cache(vault_dir: str | None = None) -> None:
    with _DERIVED_STATUS_CACHE_LOCK:
        if vault_dir is None:
            _DERIVED_STATUS_CACHE.clear()
            return
        _DERIVED_STATUS_CACHE.pop(os.path.abspath(vault_dir), None)


def derived_index_status(vault_dir: str) -> Dict[str, Any]:
    target = os.path.abspath(vault_dir)
    stripe = _DERIVED_STATUS_LOCK_STRIPES[hash(target) % len(_DERIVED_STATUS_LOCK_STRIPES)]
    with stripe:
        with _DERIVED_STATUS_CACHE_LOCK:
            entry = _DERIVED_STATUS_CACHE.get(target)
        if entry is not None:
            source_paths = tuple(entry.get("source_paths") or ())
            if (
                entry.get("artifact_signature") == _derived_status_artifact_signature(target)
                and entry.get("source_signature")
                == _derived_status_source_signature(target, source_paths)
            ):
                with _DERIVED_STATUS_CACHE_LOCK:
                    _DERIVED_STATUS_CACHE.move_to_end(target)
                return deepcopy(entry["payload"])

        status, source_paths = _derived_index_status_uncached(target)
        cache_entry = {
            "artifact_signature": _derived_status_artifact_signature(target),
            "source_signature": _derived_status_source_signature(target, source_paths),
            "source_paths": source_paths,
            "payload": deepcopy(status),
        }
        with _DERIVED_STATUS_CACHE_LOCK:
            _DERIVED_STATUS_CACHE[target] = cache_entry
            _DERIVED_STATUS_CACHE.move_to_end(target)
            while len(_DERIVED_STATUS_CACHE) > _DERIVED_STATUS_CACHE_MAX_VAULTS:
                _DERIVED_STATUS_CACHE.popitem(last=False)
        return deepcopy(status)


def _derived_index_status_uncached(vault_dir: str) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    payload, invalid = _load_payload(vault_dir)
    ledger = memory_ledger_status(vault_dir)
    ledger_entries = ledger.get("entries") or []
    ledger_by_path = {
        _normalize_path(entry.get("path")): entry
        for entry in ledger_entries
    }
    source_hashes = payload.get("source_hashes") if isinstance(payload.get("source_hashes"), dict) else {}
    changed_sources: List[Dict[str, Any]] = []
    missing_sources: List[str] = []
    for path, indexed_hash in sorted(source_hashes.items()):
        current = ledger_by_path.get(_normalize_path(path))
        if current is None:
            missing_sources.append(_normalize_path(path))
            continue
        current_hash = str(current.get("source_hash") or "")
        try:
            current_hash = _sha256_file(vault_dir, _normalize_path(path))
        except Exception:
            pass
        if current_hash and current_hash != str(indexed_hash or ""):
            changed_sources.append(
                {
                    "path": _normalize_path(path),
                    "expected": str(indexed_hash or ""),
                    "actual": current_hash,
                }
            )
    pending_sources = sum(1 for entry in ledger_entries if str(entry.get("status") or "") in {"pending", "stale"})
    failed_sources = sum(1 for entry in ledger_entries if str(entry.get("status") or "") == "failed")
    dirty = bool(changed_sources or missing_sources or pending_sources)
    readiness = _readiness(
        configured=bool(payload),
        invalid=invalid,
        dirty=dirty,
        failed_sources=failed_sources,
    )
    readiness_signal = {
        "family": "derived_index",
        "source": "readiness",
        "state": readiness["state"],
        "ready": readiness["ready"],
        "gaps": list(readiness["gaps"]),
        "gap_count": len(readiness["gaps"]),
    }
    readiness_gate = readiness_gate_from_signals([readiness_signal])
    warnings = []
    if invalid:
        warnings.append("Derived index metadata is invalid; rebuild is required.")
    warnings.extend(list(payload.get("warnings") or []))
    status = {
        "enabled": True,
        "configured": bool(payload),
        "path": DERIVED_INDEX_PATH,
        "storage": {"mode": "json", "path": DERIVED_INDEX_PATH},
        "built_at": str(payload.get("built_at") or ""),
        "chunk_size": int(payload.get("chunk_size") or 0),
        "overlap": int(payload.get("overlap") or 0),
        "readiness": readiness,
        "readiness_signals": [readiness_signal],
        "readiness_gate": readiness_gate,
        "lineage": {
            "source_count": len(source_hashes),
            "changed_sources": changed_sources,
            "missing_sources": missing_sources,
            "pending_sources": pending_sources,
            "failed_sources": failed_sources,
        },
        "summary": {
            "source_count": len(payload.get("sources") or []),
            "chunk_count": len(payload.get("chunks") or []),
            "graph_nodes": int((payload.get("graph") or {}).get("node_count") or 0),
            "graph_edges": int((payload.get("graph") or {}).get("edge_count") or 0),
            "changed_sources": len(changed_sources),
            "missing_sources": len(missing_sources),
            "pending_sources": pending_sources,
            "failed_sources": failed_sources,
            "readiness_state": readiness["state"],
            "readiness_gaps": len(readiness["gaps"]),
            "readiness_gap_names": list(readiness["gaps"]),
            "readiness_gate": readiness_gate,
            "writes_supported": True,
            "warnings": warnings[:50],
        },
        "writes_supported": True,
        "warnings": warnings[:50],
    }
    return status, tuple(sorted((_normalize_path(path) for path in source_hashes), key=str.casefold))


def retrieve_derived_chunks(
    vault_dir: str,
    query: str,
    *,
    top_k: int = 5,
    path_prefix: str = "",
    _record_total: bool = True,
) -> Dict[str, Any]:
    total_started = _METRICS_CLOCK()
    outcome = "error"
    try:
        load_started = _METRICS_CLOCK()
        try:
            payload, invalid = _load_payload(vault_dir)
        except (FileNotFoundError, PermissionError, ValueError):
            _record_query_duration("load_index", "blocked", load_started)
            raise
        except Exception:
            _record_query_duration("load_index", "error", load_started)
            raise
        _record_query_duration("load_index", "success", load_started)
        if invalid:
            raise ValueError("Derived index metadata is invalid; rebuild is required.")
        if not payload:
            raise FileNotFoundError("Derived index not built yet.")

        retrieve_started = _METRICS_CLOCK()
        normalized_prefix = _normalize_path(path_prefix)
        terms = _query_terms(query)
        phrase = _normalized_query_phrase(query)
        sources_by_path = {
            _normalize_path(source.get("path")): source
            for source in payload.get("sources") or []
            if isinstance(source, dict)
        }
        results: List[Dict[str, Any]] = []
        for chunk in payload.get("chunks") or []:
            source_path = _normalize_path(chunk.get("source_path"))
            if normalized_prefix and not (
                source_path == normalized_prefix
                or source_path.startswith(normalized_prefix + "/")
            ):
                continue
            text = str(chunk.get("text") or "")
            title = str(chunk.get("title") or "")
            tags = [str(tag or "") for tag in (chunk.get("tags") or [])]
            text_lower = text.lower()
            title_lower = title.lower()
            tags_lower = " ".join(tags).lower()
            path_lower = source_path.lower()
            links_lower = " ".join(
                _normalize_path(link)
                for link in (sources_by_path.get(source_path, {}).get("links") or [])
            ).lower()
            text_score = sum(1 for term in terms if term in text_lower)
            title_score = sum(8 for term in terms if term in title_lower)
            tag_score = sum(5 for term in terms if term in tags_lower)
            path_score = sum(3 for term in terms if term in path_lower)
            link_score = sum(2 for term in terms if term in links_lower)
            phrase_bonus = 0
            if phrase:
                if phrase in title_lower:
                    phrase_bonus += 5
                elif phrase in text_lower:
                    phrase_bonus += 3
                elif phrase in path_lower or phrase in links_lower:
                    phrase_bonus += 2
            score = text_score + title_score + tag_score + path_score + link_score + phrase_bonus
            if terms and score <= 0:
                continue
            matched_terms = sorted(
                {
                    term
                    for term in terms
                    if term in text_lower
                    or term in title_lower
                    or term in tags_lower
                    or term in path_lower
                    or term in links_lower
                }
            )
            results.append(
                {
                    "id": chunk.get("id"),
                    "source_path": source_path,
                    "title": title,
                    "score": score if terms else 1,
                    "text": text[:700],
                    "tags": tags,
                    "matched_terms": matched_terms,
                    "score_breakdown": {
                        "text": text_score,
                        "title": title_score,
                        "tags": tag_score,
                        "path": path_score,
                        "links": link_score,
                        "phrase": phrase_bonus,
                    },
                    "source_hash": chunk.get("source_hash") or "",
                }
            )
        _record_query_duration("retrieve", "success", retrieve_started)

        rank_started = _METRICS_CLOCK()
        results.sort(key=lambda item: (-int(item["score"]), str(item["source_path"]).lower(), str(item["id"]).lower()))
        _record_query_duration("rank", "success", rank_started)

        response_started = _METRICS_CLOCK()
        result = {
            "query": str(query or ""),
            "top_k": max(1, int(top_k or 5)),
            "path_prefix": normalized_prefix,
            "results": results[: max(1, int(top_k or 5))],
            "summary": {
                "total_results": len(results),
                "returned": len(results[: max(1, int(top_k or 5))]),
                "scoring": "lightweight_hybrid_v1",
                "query_terms": terms,
            },
        }
        _record_query_duration("build_response", "success", response_started)
        outcome = "success"
        return result
    except (FileNotFoundError, PermissionError, ValueError):
        outcome = "blocked"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        if _record_total:
            _record_query_duration("total", outcome, total_started)
            _record_query_outcome(outcome)

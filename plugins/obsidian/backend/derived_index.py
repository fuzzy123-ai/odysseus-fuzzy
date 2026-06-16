import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from . import vault_service
from .memory_ledger import mark_source_failed, mark_source_indexed, memory_ledger_status, sync_memory_ledger
from .readiness import readiness_gate_from_signals
from .vault_model import extract_tags


DERIVED_INDEX_PATH = ".obsidian/odysseus/memory/derived_index.json"
TEXT_DOCUMENT_EXTENSIONS = {".txt", ".json", ".csv", ".tsv", ".html", ".htm", ".rtf"}
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


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


def derived_index_status(vault_dir: str) -> Dict[str, Any]:
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
    return {
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


def retrieve_derived_chunks(vault_dir: str, query: str, *, top_k: int = 5) -> Dict[str, Any]:
    payload, invalid = _load_payload(vault_dir)
    if invalid:
        raise ValueError("Derived index metadata is invalid; rebuild is required.")
    if not payload:
        raise FileNotFoundError("Derived index not built yet.")
    terms = [
        term.lower()
        for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_/-]{1,}", str(query or ""))
    ]
    results: List[Dict[str, Any]] = []
    for chunk in payload.get("chunks") or []:
        text = str(chunk.get("text") or "")
        title = str(chunk.get("title") or "")
        tags = " ".join(chunk.get("tags") or [])
        score = 0
        for term in terms:
            if term in title.lower():
                score += 8
            if term in tags.lower():
                score += 5
            if term in text.lower():
                score += 1
        if terms and score <= 0:
            continue
        results.append(
            {
                "id": chunk.get("id"),
                "source_path": chunk.get("source_path"),
                "title": title,
                "score": score if terms else 1,
                "text": text[:700],
                "tags": chunk.get("tags") or [],
                "source_hash": chunk.get("source_hash") or "",
            }
        )
    results.sort(key=lambda item: (-int(item["score"]), str(item["source_path"]).lower(), str(item["id"]).lower()))
    return {
        "query": str(query or ""),
        "top_k": max(1, int(top_k or 5)),
        "results": results[: max(1, int(top_k or 5))],
        "summary": {
            "total_results": len(results),
            "returned": len(results[: max(1, int(top_k or 5))]),
        },
    }

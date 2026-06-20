import json
import os
import re
import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List

from . import vault_service
from .feature_flags import all_flags
from .knowledge_status import normalize_status
from .raptor_cache import clear_raptor_cache


RAPTOR_INDEX_PATH = ".obsidian/odysseus/raptor/index.json"
RAPTOR_SUMMARIES_PATH = ".obsidian/odysseus/raptor/summaries.json"
RAPTOR_REBUILD_REPORT_PATH = ".obsidian/odysseus/raptor/rebuild_report.json"
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
ISOLATED_STATUSES = {"stale", "superseded", "quarantined", "archived", "conflict", "needs_review", "review", "draft", "todo"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _source_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _normalize_path(path: Any) -> str:
    return str(path or "").replace("\\", "/").strip("/")


def _frontmatter_list(value: Any) -> List[str]:
    if isinstance(value, list):
        values = value
    elif value:
        values = [value]
    else:
        values = []
    return sorted({str(item).strip().lstrip("#") for item in values if str(item).strip()})


def _note_title(path: str, frontmatter: Dict[str, Any]) -> str:
    return str(frontmatter.get("title") or os.path.splitext(os.path.basename(path))[0]).strip()


def _source_type(path: str, frontmatter: Dict[str, Any]) -> str:
    return str(frontmatter.get("type") or "markdown").strip().lower() or "markdown"


def _resolve_link_target(raw: str, source_path: str, existing_paths: set[str], stems: Dict[str, str]) -> str:
    target = _normalize_path(raw)
    if not target.lower().endswith(".md"):
        target += ".md"
    if "/" not in target and "/" in source_path:
        local = _normalize_path(f"{source_path.rsplit('/', 1)[0]}/{target}")
        if local in existing_paths:
            return local
    if target in existing_paths:
        return target
    return stems.get(os.path.splitext(os.path.basename(target))[0].lower(), "")


def _wiki_links(path: str, body: str, existing_paths: set[str], stems: Dict[str, str], *, max_links: int) -> List[str]:
    links: List[str] = []
    seen = set()
    for match in WIKI_LINK_RE.findall(body or ""):
        target = _resolve_link_target(match, path, existing_paths, stems)
        if not target or target == path or target in seen:
            continue
        seen.add(target)
        links.append(target)
        if len(links) >= max_links:
            break
    return links


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, path)


def _artifact_path(vault_dir: str, relative_path: str) -> str:
    return vault_service.secure_path(vault_dir, relative_path)


def _write_gate(flags: Dict[str, bool]) -> Dict[str, Any]:
    gaps = []
    if not flags.get("obsidian_raptor_enabled", False):
        gaps.append("raptor_feature_flag_disabled")
    if not flags.get("obsidian_raptor_rebuild_enabled", False):
        gaps.append("raptor_rebuild_feature_flag_disabled")
    return {
        "feature_flag": "obsidian_raptor_enabled",
        "feature_enabled": bool(flags.get("obsidian_raptor_enabled", False)),
        "rebuild_feature_flag": "obsidian_raptor_rebuild_enabled",
        "rebuild_enabled": bool(flags.get("obsidian_raptor_rebuild_enabled", False)),
        "writes_supported": not gaps,
        "state": "ready" if not gaps else "blocked",
        "gaps": gaps,
    }


def _source_record(vault_dir: str, path: str, content: str, existing_paths: set[str], stems: Dict[str, str], max_links: int) -> Dict[str, Any]:
    frontmatter, body = vault_service.parse_frontmatter(content)
    status = normalize_status(frontmatter.get("status"))
    default_retrieval = status not in ISOLATED_STATUSES
    links = _wiki_links(path, body, existing_paths, stems, max_links=max_links)
    return {
        "path": path,
        "source_hash": _source_hash(content),
        "source_type": _source_type(path, frontmatter),
        "status": status,
        "default_retrieval": default_retrieval,
        "title": _note_title(path, frontmatter),
        "tags": _frontmatter_list(frontmatter.get("tags") or frontmatter.get("tag")),
        "superseded_by": str(frontmatter.get("superseded_by") or "").strip(),
        "folder": path.rsplit("/", 1)[0] if "/" in path else "",
        "link_count": len(links),
        "links": links,
    }


def rebuild_raptor_artifacts(
    vault_dir: str,
    *,
    max_sources: int = 2000,
    max_edges: int = 5000,
    max_links_per_source: int = 50,
    write_report: bool = True,
) -> Dict[str, Any]:
    flags = all_flags()
    write_gate = _write_gate(flags)
    if not write_gate["writes_supported"]:
        return {
            "success": False,
            "blocked": True,
            "write_gate": write_gate,
            "artifacts": [],
            "warnings": ["RAPTOR rebuild is disabled by feature flags."],
        }

    paths = vault_service.markdown_notes(vault_dir)
    existing_paths = set(paths)
    stems = {
        os.path.splitext(os.path.basename(path))[0].lower(): path
        for path in paths
    }
    built_at = _utcnow()
    warnings: List[str] = []
    source_records: List[Dict[str, Any]] = []
    source_hashes: Dict[str, str] = {}
    source_statuses: Counter[str] = Counter()

    for path in paths:
        try:
            content = vault_service.read_file(vault_dir, path)
            record = _source_record(vault_dir, path, content, existing_paths, stems, max_links_per_source)
        except Exception as exc:
            warnings.append(f"Skipped {path}: {exc}")
            continue
        source_records.append(record)
        source_hashes[path] = record["source_hash"]
        source_statuses[record["status"]] += 1

    source_records.sort(key=lambda item: item["path"].lower())
    graph_edges = [
        {"source": record["path"], "target": target, "type": "wiki_link"}
        for record in source_records
        for target in record.get("links", [])
    ]
    graph_edges.sort(key=lambda item: (item["source"].lower(), item["target"].lower(), item["type"]))
    stored_edges = graph_edges[: max(0, int(max_edges))]
    stored_sources = source_records[: max(0, int(max_sources))]
    active_count = sum(1 for record in source_records if record["default_retrieval"])
    isolated_count = len(source_records) - active_count
    folders = Counter(record["folder"] or "/" for record in source_records)
    statuses = Counter(record["status"] for record in source_records)
    tag_counts = Counter(tag for record in source_records for tag in record.get("tags", []))
    clusters = _clusters(folders, statuses, tag_counts, source_records, max_sources=max_sources)
    summary = {
        "source_count": len(source_records),
        "active_sources": active_count,
        "isolated_sources": isolated_count,
        "status_counts": dict(sorted(source_statuses.items())),
        "graph_edges": len(graph_edges),
        "stored_edges": len(stored_edges),
        "stored_sources": len(stored_sources),
        "source_clipped": len(stored_sources) < len(source_records),
        "graph_clipped": len(stored_edges) < len(graph_edges),
        "warnings": len(warnings),
    }
    index_payload = {
        "schema_version": "raptor-derived-v1",
        "artifact": "index",
        "built_at": built_at,
        "dirty": False,
        "tainted": isolated_count > 0,
        "source_hashes": source_hashes,
        "sources": [_compact_source(record) for record in stored_sources],
        "graph": {
            "node_count": len(source_records),
            "edge_count": len(graph_edges),
            "stored_edge_count": len(stored_edges),
            "max_edges": int(max_edges),
            "clipped": len(stored_edges) < len(graph_edges),
            "edges": stored_edges,
        },
        "summary": summary,
        "warnings": warnings[:50],
    }
    summaries_payload = {
        "schema_version": "raptor-derived-v1",
        "artifact": "summaries",
        "built_at": built_at,
        "dirty": False,
        "tainted": isolated_count > 0,
        "source_hashes": source_hashes,
        "clusters": clusters,
        "summary": summary,
        "warnings": warnings[:50],
    }
    report_payload = {
        "schema_version": "raptor-rebuild-report-v1",
        "built_at": built_at,
        "success": True,
        "write_gate": write_gate,
        "artifacts": [RAPTOR_INDEX_PATH, RAPTOR_SUMMARIES_PATH] + ([RAPTOR_REBUILD_REPORT_PATH] if write_report else []),
        "summary": summary,
        "security": {
            "derived_artifacts_only": True,
            "raw_note_content_stored": False,
            "absolute_host_paths_stored": False,
            "provider_output_stored": False,
        },
        "warnings": warnings[:50],
    }

    _atomic_write_json(_artifact_path(vault_dir, RAPTOR_INDEX_PATH), index_payload)
    _atomic_write_json(_artifact_path(vault_dir, RAPTOR_SUMMARIES_PATH), summaries_payload)
    if write_report:
        _atomic_write_json(_artifact_path(vault_dir, RAPTOR_REBUILD_REPORT_PATH), report_payload)
    clear_raptor_cache(vault_dir)
    return {
        "success": True,
        "blocked": False,
        "write_gate": write_gate,
        "artifacts": report_payload["artifacts"],
        "summary": summary,
        "warnings": warnings[:50],
    }


def _compact_source(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "path": record["path"],
        "source_hash": record["source_hash"],
        "source_type": record["source_type"],
        "status": record["status"],
        "default_retrieval": record["default_retrieval"],
        "title": record["title"],
        "tags": record["tags"][:25],
        "superseded_by": record["superseded_by"],
        "folder": record["folder"],
        "link_count": record["link_count"],
    }


def _clusters(
    folders: Counter[str],
    statuses: Counter[str],
    tags: Counter[str],
    sources: List[Dict[str, Any]],
    *,
    max_sources: int,
) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for kind, values in (("folder", folders), ("status", statuses), ("tag", tags)):
        for key, count in values.most_common(100):
            clusters.append({
                "id": f"{kind}:{key}",
                "type": kind,
                "key": key,
                "source_count": int(count),
            })
    cluster_sources = {
        cluster["id"]: [
            source["path"]
            for source in sources
            if _cluster_matches(cluster, source)
        ][: min(25, max(0, int(max_sources)))]
        for cluster in clusters
    }
    for cluster in clusters:
        cluster["sources"] = cluster_sources.get(cluster["id"], [])
    return clusters[:300]


def _cluster_matches(cluster: Dict[str, Any], source: Dict[str, Any]) -> bool:
    kind = cluster["type"]
    key = cluster["key"]
    if kind == "folder":
        return (source.get("folder") or "/") == key
    if kind == "status":
        return source.get("status") == key
    if kind == "tag":
        return key in (source.get("tags") or [])
    return False

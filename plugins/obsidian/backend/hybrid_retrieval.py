import json
import os
import hashlib
from typing import Any, Dict

from .feature_flags import all_flags, freshness_filtering_state, is_enabled
from .freshness import audit_knowledge
from . import vault_service


RAPTOR_INDEX_PATH = ".obsidian/odysseus/raptor/index.json"
RAPTOR_SUMMARIES_PATH = ".obsidian/odysseus/raptor/summaries.json"


def _source_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _normalize_source_path(path: Any) -> str:
    return str(path or "").replace("\\", "/").lstrip("/")


def _collect_lineage(payload: Any, lineage: Dict[str, str]) -> None:
    if isinstance(payload, dict):
        source_hashes = payload.get("source_hashes")
        if isinstance(source_hashes, dict):
            for path, source_hash in source_hashes.items():
                normalized = _normalize_source_path(path)
                if normalized:
                    lineage[normalized] = str(source_hash or "")
        source_paths = payload.get("source_paths")
        if isinstance(source_paths, list):
            for path in source_paths:
                normalized = _normalize_source_path(path)
                if normalized:
                    lineage.setdefault(normalized, "")
        source_path = payload.get("source_path")
        if source_path:
            lineage.setdefault(_normalize_source_path(source_path), "")
        sources = payload.get("sources")
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    normalized = _normalize_source_path(source.get("path") or source.get("source_path"))
                    if normalized:
                        lineage[normalized] = str(source.get("source_hash") or source.get("hash") or lineage.get(normalized, ""))
        for value in payload.values():
            _collect_lineage(value, lineage)
    elif isinstance(payload, list):
        for value in payload:
            _collect_lineage(value, lineage)


def _load_json(path: str) -> tuple[Dict[str, Any], bool]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}, True
    return payload if isinstance(payload, dict) else {"items": payload}, False


def _lineage_status(vault_dir: str, lineage: Dict[str, str], audit: Dict[str, Any]) -> Dict[str, Any]:
    dirty_sources = []
    missing_sources = []
    tainted_sources = []
    audit_records = {
        item["path"]: item
        for channel in ("needs_review", "conflicts", "quarantined")
        for item in audit.get("channels", {}).get(channel, [])
    }
    for path, expected_hash in sorted(lineage.items()):
        try:
            content = vault_service.read_file(vault_dir, path)
        except OSError:
            missing_sources.append(path)
            continue
        actual_hash = _source_hash(content)
        if expected_hash and actual_hash != expected_hash:
            dirty_sources.append({
                "path": path,
                "expected": expected_hash,
                "actual": actual_hash,
            })
        if path in audit_records:
            record = audit_records[path]
            tainted_sources.append({
                "path": path,
                "status": record.get("status", ""),
                "channel": record.get("channel", ""),
                "policy": record.get("policy", ""),
                "reason": record.get("reason", ""),
                "source_hash": record.get("source_hash", ""),
                "source_mtime": record.get("source_mtime", ""),
            })
    return {
        "source_count": len(lineage),
        "dirty_sources": dirty_sources,
        "missing_sources": missing_sources,
        "tainted_sources": tainted_sources,
        "summary": {
            "source_count": len(lineage),
            "dirty": len(dirty_sources),
            "missing": len(missing_sources),
            "tainted": len(tainted_sources),
        },
    }


def raptor_status(vault_dir: str) -> Dict[str, Any]:
    index_path = os.path.join(vault_dir, RAPTOR_INDEX_PATH)
    summaries_path = os.path.join(vault_dir, RAPTOR_SUMMARIES_PATH)
    index_present = os.path.exists(index_path)
    summaries_present = os.path.exists(summaries_path)
    last_built = ""
    dirty = False
    tainted = False
    invalid_index = False
    invalid_summaries = False
    lineage: Dict[str, str] = {}
    if index_present:
        payload, invalid_index = _load_json(index_path)
        last_built = str(payload.get("built_at") or payload.get("updated_at") or "")
        dirty = bool(payload.get("dirty"))
        tainted = bool(payload.get("tainted"))
        _collect_lineage(payload, lineage)
    if summaries_present:
        summaries_payload, invalid_summaries = _load_json(summaries_path)
        last_built = last_built or str(summaries_payload.get("built_at") or summaries_payload.get("updated_at") or "")
        dirty = dirty or bool(summaries_payload.get("dirty"))
        tainted = tainted or bool(summaries_payload.get("tainted"))
        _collect_lineage(summaries_payload, lineage)
    audit = audit_knowledge(vault_dir)
    lineage_status = _lineage_status(vault_dir, lineage, audit)
    if invalid_index or invalid_summaries:
        dirty = True
        tainted = True
    if lineage_status["dirty_sources"] or lineage_status["missing_sources"]:
        dirty = True
    if lineage_status["tainted_sources"]:
        tainted = True
    readiness = _raptor_readiness(
        configured=index_present or summaries_present,
        dirty=dirty,
        tainted=tainted,
        invalid_index=invalid_index,
        invalid_summaries=invalid_summaries,
        lineage_status=lineage_status,
    )
    return {
        "enabled": is_enabled("obsidian_raptor_enabled"),
        "configured": index_present or summaries_present,
        "index_present": index_present,
        "summaries_present": summaries_present,
        "invalid_index": invalid_index,
        "invalid_summaries": invalid_summaries,
        "index_path": RAPTOR_INDEX_PATH,
        "summaries_path": RAPTOR_SUMMARIES_PATH,
        "last_built": last_built,
        "dirty": dirty,
        "tainted": tainted,
        "readiness": readiness,
        "lineage": lineage_status,
        "summary": {
            "source_count": lineage_status["summary"]["source_count"],
            "dirty_sources": lineage_status["summary"]["dirty"],
            "missing_sources": lineage_status["summary"]["missing"],
            "tainted_sources": lineage_status["summary"]["tainted"],
            "invalid_sources": int(bool(invalid_index)) + int(bool(invalid_summaries)),
            "readiness_state": readiness["state"],
            "readiness_gaps": len(readiness["gaps"]),
            "writes_supported": False,
        },
        "writes_supported": False,
        "message": "RAPTOR rebuild/write is disabled in the MVP; status is read-only.",
    }


def _raptor_readiness(
    *,
    configured: bool,
    dirty: bool,
    tainted: bool,
    invalid_index: bool,
    invalid_summaries: bool,
    lineage_status: Dict[str, Any],
) -> Dict[str, Any]:
    gaps = []
    if not configured:
        gaps.append("raptor_index_missing")
    if invalid_index:
        gaps.append("raptor_index_invalid")
    if invalid_summaries:
        gaps.append("raptor_summaries_invalid")
    if lineage_status["dirty_sources"]:
        gaps.append("source_hash_changed")
    if lineage_status["missing_sources"]:
        gaps.append("source_missing")
    if lineage_status["tainted_sources"]:
        gaps.append("source_isolated_from_default_retrieval")
    if invalid_index or invalid_summaries:
        state = "invalid"
    elif not configured:
        state = "not_configured"
    elif dirty:
        state = "dirty"
    elif tainted:
        state = "tainted"
    else:
        state = "ready"
    return {
        "ready": state == "ready",
        "state": state,
        "gaps": gaps,
        "writes_supported": False,
    }


def enrich_context_payload(vault_dir: str, payload: Dict[str, Any], query: str) -> Dict[str, Any]:
    audit = audit_knowledge(vault_dir)
    audit_summary = dict(audit.get("summary") or {})
    flags = all_flags()
    filtering_state = freshness_filtering_state(flags)
    freshness_filtering = (
        filtering_state == "active"
    )
    relevant_paths = {source.get("path") for source in payload.get("sources", []) if source.get("path")}
    prefiltered = payload.pop("_freshness_excluded", []) or []
    excluded = [
        _exclusion_record(item)
        for item in prefiltered
        if item.get("path")
    ] if freshness_filtering else []
    seen_excluded = {item["path"] for item in excluded}
    if freshness_filtering:
        for channel in ("needs_review", "conflicts", "quarantined"):
            for item in audit["channels"].get(channel, []):
                if item["path"] not in seen_excluded and (item["path"] in relevant_paths or _query_mentions(query, item["path"])):
                    excluded.append(_exclusion_record({**item, "channel": channel}))
                    seen_excluded.add(item["path"])
    raptor = raptor_status(vault_dir)
    raptor_readiness = raptor.get("readiness") if isinstance(raptor.get("readiness"), dict) else {}
    audit_summary["excluded_relevant"] = len(excluded)
    audit_summary["filtering_state"] = filtering_state
    audit_summary["raptor_readiness_state"] = raptor_readiness.get("state", "unknown")
    audit_summary["raptor_readiness_gaps"] = len(raptor_readiness.get("gaps") or [])
    memory = {
        "summary": audit_summary,
        "current": [
            {"path": item["path"], "status": item["status"], "policy": item["policy"]}
            for item in audit["channels"]["current"]
            if item["path"] in relevant_paths
        ],
        "needs_review": audit["channels"]["needs_review"][:25],
        "conflicts": audit["channels"]["conflicts"][:25],
        "quarantined": audit["channels"]["quarantined"][:25],
        "excluded_relevant": excluded[:25],
        "retrieval_filtering": freshness_filtering,
        "filtering_state": filtering_state,
        "raptor": raptor,
        "flags": flags,
    }
    if excluded:
        warnings = payload.setdefault("warnings", [])
        warnings.append(f"Freshness Gate excluded {len(excluded)} relevant review/conflict/quarantined item(s).")
    payload["memory"] = memory
    return payload


def _exclusion_record(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "path": item.get("path", ""),
        "status": item.get("status", ""),
        "channel": item.get("channel", ""),
        "policy": item.get("policy", ""),
        "reason": item.get("reason", ""),
        "source_hash": item.get("source_hash", ""),
        "source_mtime": item.get("source_mtime", ""),
    }


def _query_mentions(query: str, path: str) -> bool:
    if not query:
        return False
    clean_query = query.lower()
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    return stem and stem in clean_query

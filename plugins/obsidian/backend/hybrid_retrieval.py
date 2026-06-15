import json
import os
import hashlib
from typing import Any, Dict

from .feature_flags import all_flags, freshness_filtering_state
from .freshness import audit_knowledge
from .readiness import readiness_gate_from_family, readiness_gate_from_signals
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
    flags = all_flags()
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
    lineage_flags = {
        "dirty": bool(lineage_status["dirty_sources"]),
        "missing": bool(lineage_status["missing_sources"]),
        "tainted": bool(lineage_status["tainted_sources"]),
        "invalid_index": bool(invalid_index),
        "invalid_summaries": bool(invalid_summaries),
    }
    readiness = _raptor_readiness(
        configured=index_present or summaries_present,
        dirty=dirty,
        tainted=tainted,
        invalid_index=invalid_index,
        invalid_summaries=invalid_summaries,
        lineage_status=lineage_status,
    )
    readiness_signal = _readiness_signal("raptor", readiness)
    readiness_gate = readiness_gate_from_signals([readiness_signal])
    write_gate = _raptor_write_gate(flags)
    return {
        "enabled": flags.get("obsidian_raptor_enabled", False),
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
        "readiness_signals": [readiness_signal],
        "readiness_gate": readiness_gate,
        "write_gate": write_gate,
        "lineage": lineage_status,
        "lineage_flags": lineage_flags,
        "summary": {
            "source_count": lineage_status["summary"]["source_count"],
            "dirty_sources": lineage_status["summary"]["dirty"],
            "missing_sources": lineage_status["summary"]["missing"],
            "tainted_sources": lineage_status["summary"]["tainted"],
            "invalid_sources": int(bool(invalid_index)) + int(bool(invalid_summaries)),
            "lineage_flags": lineage_flags,
            "readiness_state": readiness["state"],
            "readiness_gaps": len(readiness["gaps"]),
            "readiness_gap_names": readiness_signal["gaps"],
            "readiness_gate": readiness_gate,
            "write_gate": write_gate,
            "writes_supported": False,
        },
        "writes_supported": False,
        "message": "RAPTOR rebuild/write is disabled in the MVP; status is read-only.",
    }


def _raptor_write_gate(flags: Dict[str, bool]) -> Dict[str, Any]:
    gaps = [
        "source_hash_lineage_verification_required",
        "dirty_summary_behavior_required",
        "raptor_rebuild_write_disabled_in_mvp",
    ]
    if not flags.get("obsidian_raptor_enabled", False):
        gaps.insert(0, "raptor_feature_flag_disabled")
    return {
        "feature_flag": "obsidian_raptor_enabled",
        "feature_enabled": bool(flags.get("obsidian_raptor_enabled", False)),
        "writes_supported": False,
        "state": "blocked",
        "gaps": gaps,
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


def _readiness_signal(family: str, readiness: Dict[str, Any]) -> Dict[str, Any]:
    gaps = [str(gap) for gap in readiness.get("gaps") or []]
    state = str(readiness.get("state") or "unknown")
    return {
        "family": family,
        "source": "readiness",
        "state": state,
        "ready": bool(readiness.get("ready", state == "ready")),
        "gaps": gaps,
        "gap_count": len(gaps),
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
    freshness_isolation_flags = dict(audit.get("isolation_flags") or audit_summary.get("isolation_flags") or {})
    raptor_summary = raptor.get("summary") if isinstance(raptor.get("summary"), dict) else {}
    raptor_lineage_flags = dict(
        raptor.get("lineage_flags")
        or raptor_summary.get("lineage_flags")
        or {}
    )
    raptor_readiness = raptor.get("readiness") if isinstance(raptor.get("readiness"), dict) else {}
    freshness_readiness = audit.get("readiness") if isinstance(audit.get("readiness"), dict) else {}
    freshness_gaps = [str(gap) for gap in freshness_readiness.get("gaps") or []]
    raptor_gaps = [str(gap) for gap in raptor_readiness.get("gaps") or []]
    audit_summary["excluded_relevant"] = len(excluded)
    audit_summary["filtering_state"] = filtering_state
    audit_summary["freshness_readiness_state"] = freshness_readiness.get("state", "unknown")
    audit_summary["freshness_readiness_gaps"] = len(freshness_gaps)
    audit_summary["freshness_readiness_gap_names"] = freshness_gaps
    audit_summary["raptor_readiness_state"] = raptor_readiness.get("state", "unknown")
    audit_summary["raptor_readiness_gaps"] = len(raptor_gaps)
    audit_summary["raptor_readiness_gap_names"] = raptor_gaps
    audit_summary["freshness_isolation_flags"] = freshness_isolation_flags
    audit_summary["raptor_lineage_flags"] = raptor_lineage_flags
    readiness_signals = [
        _readiness_signal("freshness", freshness_readiness),
        _readiness_signal("raptor", raptor_readiness),
    ]
    audit_summary["readiness_state"] = "blocked" if any(not signal.get("ready", False) for signal in readiness_signals) else "ready"
    audit_summary["readiness_gaps"] = sum(int(signal.get("gap_count") or 0) for signal in readiness_signals)
    audit_summary["readiness_gap_names"] = _readiness_gap_names(readiness_signals)
    readiness_by_family = _readiness_by_family(readiness_signals)
    readiness_gate = readiness_gate_from_family(readiness_by_family, audit_summary["readiness_gap_names"])
    audit_summary["readiness_gate"] = readiness_gate
    retrieval_policy = {
        "filtering_state": filtering_state,
        "default_retrieval_is_filtered": freshness_filtering,
        "isolated_knowledge_retained_in_audit": True,
        "excluded_relevant_count": len(excluded),
    }
    audit_summary["retrieval_policy"] = retrieval_policy
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
        "retrieval_policy": retrieval_policy,
        "freshness_isolation_flags": freshness_isolation_flags,
        "raptor_lineage_flags": raptor_lineage_flags,
        "readiness_signals": readiness_signals,
        "readiness_by_family": readiness_by_family,
        "readiness_gate": readiness_gate,
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


def _readiness_by_family(signals: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for signal in signals:
        family = str(signal.get("family") or "generic")
        grouped[family] = signal
    return dict(sorted(grouped.items()))


def _readiness_gap_names(signals: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen = set()
    for signal in signals:
        for gap in signal.get("gaps") or []:
            name = str(gap or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
    return names[:25]


def _query_mentions(query: str, path: str) -> bool:
    if not query:
        return False
    clean_query = query.lower()
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    return stem and stem in clean_query

import json
import os
from typing import Any, Dict

from .feature_flags import all_flags, is_enabled
from .freshness import audit_knowledge


RAPTOR_INDEX_PATH = ".obsidian/odysseus/raptor/index.json"
RAPTOR_SUMMARIES_PATH = ".obsidian/odysseus/raptor/summaries.json"


def raptor_status(vault_dir: str) -> Dict[str, Any]:
    index_path = os.path.join(vault_dir, RAPTOR_INDEX_PATH)
    summaries_path = os.path.join(vault_dir, RAPTOR_SUMMARIES_PATH)
    index_present = os.path.exists(index_path)
    summaries_present = os.path.exists(summaries_path)
    last_built = ""
    dirty = False
    tainted = False
    if index_present:
        try:
            with open(index_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            last_built = str(payload.get("built_at") or payload.get("updated_at") or "")
            dirty = bool(payload.get("dirty"))
            tainted = bool(payload.get("tainted"))
        except (OSError, json.JSONDecodeError):
            dirty = True
            tainted = True
    return {
        "enabled": is_enabled("obsidian_raptor_enabled"),
        "configured": index_present or summaries_present,
        "index_present": index_present,
        "summaries_present": summaries_present,
        "index_path": RAPTOR_INDEX_PATH,
        "summaries_path": RAPTOR_SUMMARIES_PATH,
        "last_built": last_built,
        "dirty": dirty,
        "tainted": tainted,
        "writes_supported": False,
        "message": "RAPTOR rebuild/write is disabled in the MVP; status is read-only.",
    }


def enrich_context_payload(vault_dir: str, payload: Dict[str, Any], query: str) -> Dict[str, Any]:
    audit = audit_knowledge(vault_dir)
    relevant_paths = {source.get("path") for source in payload.get("sources", []) if source.get("path")}
    excluded = []
    for channel in ("needs_review", "conflicts", "quarantined"):
        for item in audit["channels"].get(channel, []):
            if item["path"] in relevant_paths or _query_mentions(query, item["path"]):
                excluded.append({
                    "path": item["path"],
                    "status": item["status"],
                    "channel": channel,
                    "reason": item["reason"],
                })
    memory = {
        "current": [
            {"path": item["path"], "status": item["status"], "policy": item["policy"]}
            for item in audit["channels"]["current"]
            if item["path"] in relevant_paths
        ],
        "needs_review": audit["channels"]["needs_review"][:25],
        "conflicts": audit["channels"]["conflicts"][:25],
        "quarantined": audit["channels"]["quarantined"][:25],
        "excluded_relevant": excluded[:25],
        "retrieval_filtering": is_enabled("obsidian_hybrid_retrieval_enabled"),
        "raptor": raptor_status(vault_dir),
        "flags": all_flags(),
    }
    if excluded:
        warnings = payload.setdefault("warnings", [])
        warnings.append(f"Freshness Gate excluded {len(excluded)} relevant stale/conflicting/quarantined item(s).")
    payload["memory"] = memory
    return payload


def _query_mentions(query: str, path: str) -> bool:
    if not query:
        return False
    clean_query = query.lower()
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    return stem and stem in clean_query

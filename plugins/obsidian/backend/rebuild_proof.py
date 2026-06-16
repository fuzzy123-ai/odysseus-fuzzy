import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .derived_index import build_derived_index, derived_index_status
from .memory_ledger import memory_ledger_status, sync_memory_ledger
from .query_layer import answer_query, query_layer_status


REBUILD_PROOF_PATH = ".obsidian/odysseus/memory/rebuild_proof.json"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _report_abspath(vault_dir: str) -> str:
    return os.path.join(vault_dir, REBUILD_PROOF_PATH.replace("/", os.sep))


def run_rebuild_proof(vault_dir: str, *, query: Optional[str] = None, top_k: int = 5) -> Dict[str, Any]:
    sync_memory_ledger(vault_dir)
    ledger_before = memory_ledger_status(vault_dir)
    build_derived_index(vault_dir)
    ledger_after = memory_ledger_status(vault_dir)
    derived = derived_index_status(vault_dir)
    query_status = query_layer_status(vault_dir)
    query_result = answer_query(vault_dir, query, top_k=top_k) if query else None
    payload = {
        "generated_at": _utc_iso(),
        "query": str(query or ""),
        "summary": {
            "ledger_ready": bool((ledger_after.get("readiness") or {}).get("ready", False)),
            "derived_index_ready": bool((derived.get("readiness") or {}).get("ready", False)),
            "query_layer_ready": bool((query_status.get("readiness") or {}).get("ready", False)),
            "source_count": int((ledger_after.get("summary") or {}).get("total_sources") or 0),
            "chunk_count": int((derived.get("summary") or {}).get("chunk_count") or 0),
            "query_citations": len((query_result or {}).get("citations") or []),
        },
        "ledger_before": ledger_before.get("summary", {}),
        "ledger_after": ledger_after.get("summary", {}),
        "derived_index": derived.get("summary", {}),
        "query_layer": query_status.get("summary", {}),
        "query_result": query_result or {},
    }
    out_path = _report_abspath(vault_dir)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return {
        "report_path": REBUILD_PROOF_PATH,
        "summary": payload["summary"],
        "query_result": query_result or {},
    }


def rebuild_proof_status(vault_dir: str) -> Dict[str, Any]:
    path = _report_abspath(vault_dir)
    if not os.path.exists(path):
        return {
            "configured": False,
            "report_path": REBUILD_PROOF_PATH,
            "warnings": ["No rebuild proof report has been generated yet."],
        }
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return {
            "configured": False,
            "report_path": REBUILD_PROOF_PATH,
            "warnings": ["Rebuild proof report exists but is unreadable."],
        }
    return {
        "configured": True,
        "report_path": REBUILD_PROOF_PATH,
        "generated_at": str(payload.get("generated_at") or ""),
        "summary": dict(payload.get("summary") or {}),
        "warnings": [],
    }

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import vault_service
from .derived_index import build_derived_index, derived_index_status
from .memory_ledger import memory_ledger_status, sync_memory_ledger
from .query_layer import query_layer_status
from .vault_security import VaultSecurityError


JOB_ID = "obsidian.memory_automation"
AUTOMATION_REPORT_PATH = ".obsidian/odysseus/memory/automation_status.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _cooldown_seconds() -> int:
    return max(0, int(os.getenv("ODYSSEUS_OBSIDIAN_MEMORY_AUTOMATION_COOLDOWN_SECONDS", "300") or 300))


def _max_sources_per_pass() -> int:
    return max(1, int(os.getenv("ODYSSEUS_OBSIDIAN_MEMORY_AUTOMATION_MAX_SOURCES", "500") or 500))


def _concurrency_limit() -> int:
    return max(1, int(os.getenv("ODYSSEUS_OBSIDIAN_MEMORY_AUTOMATION_CONCURRENCY", "1") or 1))


def _report_abspath(vault_dir: str) -> str:
    return vault_service.secure_path(vault_dir, AUTOMATION_REPORT_PATH)


def _read_report(vault_dir: str) -> Dict[str, Any]:
    path = _report_abspath(vault_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_report(vault_dir: str, payload: Dict[str, Any]) -> None:
    path = _report_abspath(vault_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def memory_automation_status(vault_dir: str) -> Dict[str, Any]:
    ledger = memory_ledger_status(vault_dir)
    derived = derived_index_status(vault_dir)
    query = query_layer_status(vault_dir)
    report = _read_report(vault_dir)
    now = _utcnow()
    cooldown = _cooldown_seconds()
    last_run_raw = str(report.get("last_run_at") or "").strip()
    next_eligible_at = ""
    cooling_down = False
    if last_run_raw:
        try:
            last_run = datetime.fromisoformat(last_run_raw.replace("Z", "+00:00"))
            next_eligible = last_run + timedelta(seconds=cooldown)
            next_eligible_at = _utc_iso(next_eligible.astimezone(timezone.utc))
            cooling_down = next_eligible > now
        except ValueError:
            next_eligible_at = ""
    pending_actions: List[str] = []
    if int((ledger.get("summary") or {}).get("pending_sources") or 0) > 0 or int((ledger.get("summary") or {}).get("stale_sources") or 0) > 0 or int((ledger.get("summary") or {}).get("total_sources") or 0) == 0:
        pending_actions.append("sync_memory_ledger")
    if not bool(derived.get("configured")) or not bool((derived.get("readiness") or {}).get("ready", False)):
        pending_actions.append("build_derived_index")
    if not bool((query.get("readiness") or {}).get("ready", False)):
        pending_actions.append("query_layer_waits_on_index")
    warnings: List[str] = []
    if cooling_down:
        warnings.append("Memory automation cooldown is active; the next periodic pass will wait until the cooldown expires.")
    return {
        "enabled": True,
        "job_id": JOB_ID,
        "report_path": AUTOMATION_REPORT_PATH,
        "pending_actions": pending_actions,
        "cost_controller": {
            "cooldown_seconds": cooldown,
            "max_sources_per_pass": _max_sources_per_pass(),
            "concurrency_limit": _concurrency_limit(),
            "cooling_down": cooling_down,
            "next_eligible_at": next_eligible_at,
        },
        "safety": {
            "source_note_writes": False,
            "derived_data_writes_only": True,
            "allowed_actions": ["sync_memory_ledger", "build_derived_index"],
        },
        "layers": {
            "ledger": ledger.get("summary", {}),
            "derived_index": derived.get("summary", {}),
            "query_layer": query.get("summary", {}),
        },
        "last_run": {
            "last_run_at": last_run_raw,
            "last_trigger": str(report.get("last_trigger") or ""),
            "last_actions": list(report.get("actions_executed") or []),
            "skipped": bool(report.get("skipped", False)),
            "reason": str(report.get("reason") or ""),
        },
        "warnings": warnings,
    }


def run_memory_automation(
    owner: Optional[str] = None,
    trigger: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    try:
        vault_dir = vault_service.unlocked_vault_path_for_owner(owner)
    except VaultSecurityError:
        return {"skipped": True, "reason": "vault_locked", "actions_executed": []}

    status_before = memory_automation_status(vault_dir)
    now = _utcnow()
    cost = dict(status_before.get("cost_controller") or {})
    if cost.get("cooling_down") and not force:
        payload = {
            "job_id": JOB_ID,
            "last_run_at": _utc_iso(now),
            "last_trigger": trigger or "",
            "actions_executed": [],
            "skipped": True,
            "reason": "cooldown_active",
            "summary": {
                "source_note_writes": False,
                "derived_data_writes_only": True,
            },
        }
        _write_report(vault_dir, payload)
        return {"skipped": True, "reason": "cooldown_active", "actions_executed": []}

    actions_executed: List[str] = []
    warnings: List[str] = []
    total_sources = int((status_before.get("layers") or {}).get("ledger", {}).get("total_sources") or 0)
    if total_sources <= _max_sources_per_pass():
        ledger = memory_ledger_status(vault_dir)
        if int((ledger.get("summary") or {}).get("pending_sources") or 0) > 0 or int((ledger.get("summary") or {}).get("stale_sources") or 0) > 0 or int((ledger.get("summary") or {}).get("total_sources") or 0) == 0:
            sync_memory_ledger(vault_dir)
            actions_executed.append("sync_memory_ledger")
        derived = derived_index_status(vault_dir)
        if not bool(derived.get("configured")) or not bool((derived.get("readiness") or {}).get("ready", False)):
            build_derived_index(vault_dir)
            actions_executed.append("build_derived_index")
    else:
        warnings.append(
            f"Memory automation skipped rebuild because the vault has {total_sources} sources and the per-pass cap is {_max_sources_per_pass()}."
        )

    after = memory_automation_status(vault_dir)
    payload = {
        "job_id": JOB_ID,
        "last_run_at": _utc_iso(now),
        "last_trigger": trigger or "",
        "actions_executed": actions_executed,
        "skipped": False,
        "reason": "",
        "summary": {
            "source_note_writes": False,
            "derived_data_writes_only": True,
            "pending_actions_after": after.get("pending_actions", []),
        },
        "warnings": warnings,
        "context": {
            "owner": owner or "default",
            "trigger": trigger or "",
            "event_keys": sorted((context or {}).keys())[:10],
        },
    }
    _write_report(vault_dir, payload)
    return {
        "skipped": False,
        "actions_executed": actions_executed,
        "status": after,
        "warnings": warnings,
        "safety": {
            "source_note_writes": False,
            "derived_data_writes_only": True,
        },
    }


def job_spec() -> Dict[str, Any]:
    return {
        "id": JOB_ID,
        "label": "Obsidian Memory Automation",
        "priority": 60,
        "capabilities": ["periodic", "chat_completed", "vault", "obsidian", "memory"],
        "run": run_memory_automation,
    }
